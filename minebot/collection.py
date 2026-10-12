"""Pinned evidence and immutable originals. Source text is data, never instructions."""

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import time
from urllib.parse import quote
import uuid

from .github import Deferred, GitHub, SetupError
from .lock import collector_lock
from .store import now


MAX_ASSET = 64 * 1024**2
MAX_EVIDENCE = 1024**2
MAX_EVIDENCE_TOTAL = 8 * 1024**2
EVIDENCE_NAME = re.compile(r'^(readme|licen[cs]e|copying|authors?|attribution|notice|copyright)([._-].*)?$', re.I)
RESTRICTION = re.compile(
    r'\b(?:do\s+not|must\s+not|may\s+not|cannot|can\s+not|no)\s+(?:\w+\s+){0,4}'
    r'(?:download\w*|scrap\w*|collect\w*|redistribut\w*|train\w*)\b'
    r'|\b(?:download\w*|scrap\w*|collect\w*|redistribut\w*|train\w*)\s+(?:is\s+|are\s+)?'
    r'(?:prohibited|forbidden|not\s+allowed)\b|\ball\s+rights\s+reserved\b', re.I)
LFS_HEADER = b'version https://git-lfs.github.com/spec/v1'


class Held(ValueError):
    """Evidence requires a human decision before an original may be fetched."""


def fingerprints(path, size):
    git = hashlib.sha1(f'blob {size}\0'.encode())
    sha256 = hashlib.sha256()
    with path.open('rb') as source:
        while part := source.read(65536):
            git.update(part)
            sha256.update(part)
    return git.hexdigest(), sha256.hexdigest()


class Collection:
    def __init__(self, store, github, report):
        self.store, self.github, self.report = store, github, report
        self.config = store.config()
        self.root = store.directory / 'staging'
        self.root.mkdir(exist_ok=True)
        self.temporary = self.root / 'temporary'
        self.temporary.mkdir(exist_ok=True)
        # The shared process lock makes these abandoned transfers safe to remove.
        for path in self.temporary.glob('*.part'):
            path.unlink()
        self.storage = sum(path.stat().st_size for path in self.root.rglob('*') if path.is_file())
        self.trees = {}

    def check_active(self):
        if self.store.config().paused:
            raise Deferred('Collection paused')
        if time.monotonic() - self.github.started >= self.config.max_seconds:
            raise Deferred('Run time budget reached')

    def received(self, count):
        self.report['download_bytes'] += count
        self.check_active()
        if self.report['download_bytes'] > self.config.max_download_bytes:
            raise Deferred('Run download budget reached')

    def fetch(self, name, entry, kind):
        self.check_active()
        size, sha = entry.get('size'), entry.get('sha', '')
        if type(size) is not int or size < 0 or not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('Missing or invalid pinned blob identity or size')
        ceiling = MAX_EVIDENCE if kind == 'evidence' else MAX_ASSET
        if size > ceiling:
            raise ValueError('Pinned file exceeds compressed asset or evidence size limit')
        cached = self.store.artifact_for_blob(sha)
        if cached:
            # Paths come only from our content-addressed layout, never source filenames.
            path = self.root / 'objects' / cached['sha256'][:2] / cached['sha256']
            if (cached['size'] == size and path.is_file() and path.stat().st_size == size
                    and fingerprints(path, size) == (sha, cached['sha256'])):
                return cached
            raise ValueError('Stored original is missing or corrupt; restore staging before retry')
        if size > self.config.max_download_bytes - self.report['download_bytes']:
            raise Deferred('Run download budget reached; file left queued')
        if size > self.config.max_storage_bytes - self.storage:
            raise Deferred('Staging storage budget reached; file left queued')
        temporary = self.temporary / f'{uuid.uuid4().hex}.part'
        try:
            with temporary.open('xb') as target:
                self.github.blob(name, sha, target, size, self.received)
                target.flush()
                os.fsync(target.fileno())
            if temporary.stat().st_size != size:
                raise ValueError('Downloaded size does not match pinned tree size')
            actual_sha, digest = fingerprints(temporary, size)
            if actual_sha != sha:
                raise ValueError('Downloaded Git blob identity does not match pinned tree')
            self.check_active()
            with temporary.open('rb') as source:
                lfs = source.read(256).startswith(LFS_HEADER)
            destination = self.root / 'objects' / digest[:2] / digest
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.stat().st_size != size or fingerprints(destination, size) != (sha, digest):
                    raise ValueError('Content-addressed storage collision or corruption')
                temporary.unlink()
            else:
                os.replace(temporary, destination)
                self.storage += size
            artifact = {'sha256': digest, 'blob_sha': sha, 'size': size,
                        'local_path': destination.relative_to(self.store.directory).as_posix(),
                        'is_lfs_pointer': lfs}
            self.store.artifact(artifact)
            return artifact
        finally:
            temporary.unlink(missing_ok=True)

    def tree(self, name, sha):
        self.check_active()
        key = (name, sha)
        if key not in self.trees:
            result = self.github.tree(name, sha, recursive=False)
            if result.get('truncated'):
                raise Held('Evidence traversal incomplete: nonrecursive tree is truncated')
            self.trees[key] = result['tree']
        return self.trees[key]

    def evidence_entries(self, candidate):
        name, revision = candidate['repository'], candidate['revision']
        path = candidate['path']
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', name)
                or not re.fullmatch(r'[0-9a-f]{40}', revision)
                or path.startswith('/') or '\\' in path or any(p in ('', '.', '..') for p in path.split('/'))):
            raise Held('Invalid pinned source reference')
        if name.casefold() in {r.casefold() for r in self.config.excluded_repositories}:
            raise Held('Repository excluded by collection configuration')
        repository = self.github.repository(name)
        if repository['id'] != candidate['repository_id']:
            raise Held('Repository identity changed; source must be rediscovered')
        commit = self.github.commit(name, revision)
        if commit['sha'] != revision:
            raise Held('Pinned revision identity does not match')
        sha = commit['commit']['tree']['sha']
        parts = path.split('/')
        prefix = ''
        evidence = []
        for index, part in enumerate(parts):
            entries = self.tree(name, sha)
            evidence.extend(entry | {'path': prefix + entry['path']}
                            for entry in entries if EVIDENCE_NAME.fullmatch(entry['path']))
            match = next((entry for entry in entries if entry['path'] == part), None)
            if not match:
                raise Held('Pinned source path is missing from its revision')
            if index == len(parts) - 1:
                if (match['type'] != 'blob' or match.get('mode') not in ('100644', '100755')
                        or match['sha'] != candidate['blob_sha'] or match.get('size') != candidate['size']):
                    raise Held('Pinned source path identity or size changed')
            else:
                if match['type'] != 'tree':
                    raise Held('Source ancestor is not a directory')
                sha = match['sha']
                prefix += part + '/'
        return evidence

    def collect(self, candidate):
        refs = []
        try:
            entries = self.evidence_entries(candidate)
            if len(entries) > 32:
                raise Held('Evidence set exceeds 32-file review limit')
            if any(type(e.get('size')) is not int or e['size'] < 0 or e['size'] > MAX_EVIDENCE for e in entries):
                raise Held('Evidence file size is missing or exceeds review limit')
            if sum(e['size'] for e in entries) > MAX_EVIDENCE_TOTAL:
                raise Held('Evidence set exceeds 8 MiB review limit')
            for entry in entries:
                if entry['type'] != 'blob' or entry.get('mode') not in ('100644', '100755'):
                    raise Held('Evidence is a symlink or unsupported entry')
                artifact = self.fetch(candidate['repository'], entry, 'evidence')
                ref = artifact | {'repository_id': candidate['repository_id'], 'repository': candidate['repository'],
                                  'revision': candidate['revision'], 'path': entry['path'],
                                  'source_url': f"https://github.com/{candidate['repository']}/blob/{candidate['revision']}/{quote(entry['path'], safe='/')}",
                                  'scope': 'ancestor_directory_evidence; asset applicability unconfirmed'}
                refs.append(ref)
                self.store.update_candidate(candidate['id'], {'license_evidence': refs, 'evidence_status': 'collecting'}, 'collecting')
                if artifact['is_lfs_pointer']:
                    raise Held('Evidence is an LFS pointer; external content is not followed')
                try:
                    text = (self.store.directory / artifact['local_path']).read_text(encoding='utf-8-sig')
                except UnicodeError:
                    raise Held('Evidence is not UTF-8 text; manual inspection required') from None
                if '\0' in text:
                    raise Held('Evidence appears binary; manual inspection required')
                if RESTRICTION.search(text):
                    raise Held('Potential collection/usage restriction in pinned evidence; manual review required')
            self.store.update_candidate(candidate['id'], {'license_evidence': refs,
                'evidence_status': 'captured' if refs else 'no_matching_files',
                'evidence_scope': 'Repository root and source ancestor directories; external links are not followed',
                'usage_permission': 'unknown', 'asset_author': None}, 'collecting')
            artifact = self.fetch(candidate['repository'], {'sha': candidate['blob_sha'], 'size': candidate['size']}, 'asset')
            status = 'lfs_pointer' if artifact['is_lfs_pointer'] else 'downloaded'
            self.store.update_candidate(candidate['id'], {'artifact': artifact,
                'validation_status': 'not_validated', 'visual_approval': 'pending',
                'training_eligibility': 'human_review_required', 'collection': {'retry_at': 0}}, status)
            return status
        except Held:
            self.store.update_candidate(candidate['id'], {'license_evidence': refs, 'evidence_status': 'held'}, 'held')
            raise


def collect(store, github_factory=GitHub):
    with collector_lock(store.directory / 'collector.lock'):
        identifier = uuid.uuid4().hex
        report = {'kind': 'collection', 'started': now(), 'status': 'running', 'requests': 0,
                  'download_bytes': 0, 'processed': 0, 'downloaded': 0, 'held': 0,
                  'failed': 0, 'lfs_pointers': 0, 'error': None}
        for previous in store.status()['runs']:
            if previous['status'] == 'running':
                store.save_run(previous['id'], previous | {'status': 'interrupted', 'finished': now()})
        store.recover_attempts()
        store.save_run(identifier, report)
        logger = logging.getLogger(f'minebot.collection.{identifier}')
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logs = store.directory / 'logs'
        logs.mkdir(exist_ok=True)
        handler = RotatingFileHandler(logs / 'collection.log', maxBytes=1024**2, backupCount=3, encoding='utf-8')
        logger.addHandler(handler)
        github = None
        attempt = None
        candidate = None
        before = 0
        try:
            if store.config().paused:
                report['status'] = 'paused'
                return report
            if store.retry_at() > time.time():
                raise Deferred('Previous GitHub deferral has not elapsed', store.retry_at())
            if not store.next_collection():
                report['status'] = 'completed'
                return report
            github = github_factory(store.config())
            worker = Collection(store, github, report)
            while candidate := store.next_collection():
                worker.check_active()
                if report['processed'] >= worker.config.max_new_files:
                    raise Deferred('Run file budget reached; remaining candidates retained')
                attempt = store.start_attempt(candidate['id'], identifier)
                before = report['download_bytes']
                store.update_candidate(candidate['id'], {}, 'collecting')
                try:
                    outcome = worker.collect(candidate)
                    report['lfs_pointers' if outcome == 'lfs_pointer' else 'downloaded'] += 1
                    detail = {'status': outcome}
                except ValueError as error:
                    outcome = 'held' if isinstance(error, Held) or 'Only public' in str(error) else 'failed'
                    detail = {'status': outcome, 'error': str(error)}
                    store.update_candidate(candidate['id'], {'collection': detail | {'retry_at': 0}}, outcome)
                    report[outcome] += 1
                store.finish_attempt(attempt, detail | {'download_bytes': report['download_bytes'] - before})
                logger.info(json.dumps({'run_id': identifier, 'candidate_id': candidate['id']} | detail))
                attempt = None
                report['processed'] += 1
                store.save_run(identifier, report)
            report['status'] = 'completed'
        except Deferred as error:
            paused = store.config().paused
            report.update(status='paused' if paused else 'deferred', error=str(error), retry_at=error.retry_at or 0)
            if error.retry_at:
                store.defer_until(error.retry_at)
            if attempt is not None:
                detail = {'status': 'deferred', 'error': str(error), 'retry_at': error.retry_at or 0}
                store.update_candidate(candidate['id'], {'collection': detail}, 'deferred')
                store.finish_attempt(attempt, detail | {'download_bytes': report['download_bytes'] - before})
                attempt = None
        except SetupError as error:
            report.update(status='setup_error', error=str(error))
        except KeyboardInterrupt:
            report.update(status='interrupted', error='Collection interrupted; source references retained')
            raise
        except BaseException:
            report.update(status='failed',
                          error='Collection interrupted; source references retained')
            raise
        finally:
            if attempt is not None:
                store.finish_attempt(attempt, {'status': report['status'], 'error': report['error'],
                                               'download_bytes': report['download_bytes'] - before})
            if github:
                report['requests'] = github.requests
                github.close()
            report['finished'] = now()
            store.save_run(identifier, report)
            logger.info(json.dumps({'run_id': identifier} | report))
            handler.close()
            logger.removeHandler(handler)
        return report
