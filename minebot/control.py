"""Local collector administration, independent of reviewer decisions and jobs."""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time

from .collection import MAX_EVIDENCE
from .lock import AlreadyRunning, collector_lock
from .store import Store, now

STATUSES = {'discovered', 'collecting', 'downloaded', 'held', 'failed', 'deferred',
            'unsupported', 'lfs_pointer', 'dismissed'}


def message(value):
    if isinstance(value, dict):
        value = value.get('message')
    return value if isinstance(value, str) else None


def available_actions(candidate):
    if candidate['status'] == 'dismissed':
        return ['restore']
    actions = ['dismiss']
    if candidate['status'] in ('failed', 'deferred'):
        actions.append('retry')
    if candidate['status'] == 'downloaded':
        if candidate.get('validation_status') in ('failed', 'interrupted'):
            actions.append('retry-validation')
        if candidate.get('validation_status') == 'passed' and candidate.get('handoff', {}).get('status') in ('failed', 'deferred'):
            actions.append('retry-handoff')
    return actions


def candidate_summary(candidate):
    validation = candidate.get('validation', {})
    return {key: candidate.get(key) for key in ('id', 'repository', 'repository_id', 'revision',
            'path', 'blob_sha', 'status', 'evidence_status', 'validation_status')} | {
        'source_sha256': candidate.get('artifact', {}).get('sha256'),
        'size': candidate.get('size', candidate.get('artifact', {}).get('size')),
        'handoff_status': candidate.get('handoff', {}).get('status', 'not_sent'),
        'reviewer_build_id': candidate.get('handoff', {}).get('build_id'),
        'retry_at': max(candidate.get('collection', {}).get('retry_at', 0), candidate.get('handoff', {}).get('retry_at', 0)),
        'errors': {stage: error for stage, error in {
            'collection': message(candidate.get('collection', {}).get('error')),
            'validation': message(validation.get('error')) or message(validation.get('report', {}).get('error')),
            'handoff': message(candidate.get('handoff', {}).get('error'))}.items() if error},
        'evidence': [{key: ref.get(key) for key in ('path', 'sha256', 'size', 'scope')}
                     for ref in candidate.get('license_evidence', [])],
        'suggestions': [{key: suggestion.get(key) for key in ('field', 'value', 'origin', 'confidence', 'matched_block_count')}
                        for suggestion in candidate.get('suggestions', [])],
        'actions': available_actions(candidate)}


class CollectorControls:
    def __init__(self, directory):
        directory = Path(directory).resolve()
        try:
            self.store = Store.open_existing(directory)
        except (sqlite3.OperationalError, ValueError):
            # Schema changes only occur while no CLI collector owns the process lock.
            with collector_lock(directory / 'collector.lock'):
                self.store = Store(directory)
        self.storage_lock = threading.Lock()
        self.storage_sample = None
        self.storage_sample_time = 0

    def active(self):
        try:
            with collector_lock(self.store.directory / 'collector.lock'):
                return False
        except AlreadyRunning:
            return True

    def storage(self):
        with self.storage_lock:
            if self.storage_sample is not None and time.monotonic() - self.storage_sample_time < 5:
                return self.storage_sample.copy()
            result = {'used_bytes': 0, 'objects_bytes': 0, 'validation_bytes': 0, 'other_bytes': 0,
                      'incomplete': False, 'sampled_at': now()}
            staging = self.store.directory / 'staging'
            if staging.is_symlink():
                result['incomplete'] = True
                return result
            def failed(error):
                result['incomplete'] = True
            for root, directories, files in os.walk(staging, followlinks=False, onerror=failed):
                directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
                for name in files:
                    path = Path(root) / name
                    try:
                        info = path.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        group = path.relative_to(staging).parts[0]
                        field = 'objects_bytes' if group == 'objects' else 'validation_bytes' if group == 'validation' else 'other_bytes'
                        result[field] += info.st_size
                        result['used_bytes'] += info.st_size
                    except OSError:
                        result['incomplete'] = True
            self.storage_sample = result
            self.storage_sample_time = time.monotonic()
            return result.copy()

    def status(self):
        state = self.store.status()
        active = self.active()
        with self.store.db() as db:
            validation = {row[0]: row[1] for row in db.execute("SELECT COALESCE(json_extract(document,'$.validation_status'),'not_validated'),count(*) FROM candidates GROUP BY 1")}
            handoff = {row[0]: row[1] for row in db.execute("SELECT COALESCE(json_extract(document,'$.handoff.status'),'not_sent'),count(*) FROM candidates GROUP BY 1")}
        runs = [{key: value for key, value in run.items() if key in
                 {'id', 'kind', 'status', 'started', 'finished', 'requests', 'new_candidates', 'processed',
                  'downloaded', 'download_bytes', 'passed', 'failed', 'held', 'delivered', 'error', 'warnings'}}
                for run in state['runs']]
        for run in runs:
            run['kind'] = run.get('kind', 'discovery')
            run['observed_status'] = 'interrupted' if run['status'] == 'running' and not active else run['status']
        return {'paused': state['config']['paused'], 'active': active, 'sampled_at': now(),
                'repository_count': state['repository_count'], 'pending_work': state['pending_work'],
                'candidate_counts': state['candidate_counts'], 'validation_counts': validation,
                'handoff_counts': handoff, 'github_retry_at': self.store.retry_at(), 'runs': runs,
                'storage': self.storage() | {'limit_bytes': state['config']['max_storage_bytes']}}

    def candidate(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{64}', identifier):
            raise KeyError('Candidate not found')
        with self.store.db() as db:
            row = db.execute('SELECT * FROM candidates WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise KeyError('Candidate not found')
            return json.loads(row['document']) | {'id': row['id'], 'status': row['status']}

    def candidates(self, query='', status='', limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('Candidate page requires limit 1–100 and a nonnegative offset')
        if status and status not in STATUSES:
            raise ValueError('Unknown candidate status filter')
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('Search must be text of at most 200 characters')
        where, parameters = [], []
        if status:
            where.append('status=?')
            parameters.append(status)
        if query:
            where.append("(json_extract(document,'$.repository') LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\')")
            pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            parameters.extend([pattern, pattern])
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.store.db() as db:
            count = db.execute('SELECT count(*) FROM candidates' + clause, parameters).fetchone()[0]
            rows = db.execute('SELECT * FROM candidates' + clause + ' ORDER BY created,id LIMIT ? OFFSET ?', parameters + [limit, offset])
            items = [candidate_summary(json.loads(row['document']) | {'id': row['id'], 'status': row['status']}) for row in rows]
        return {'items': items, 'total': count, 'limit': limit, 'offset': offset}

    def pause(self, paused):
        if type(paused) is not bool:
            raise ValueError('paused must be boolean')
        # A running worker polls this setting; do not take its long-lived process lock.
        self.store.configure({'paused': paused})
        return {'paused': paused, 'active': self.active()}

    def action(self, identifier, action):
        with collector_lock(self.store.directory / 'collector.lock'):
            candidate = self.candidate(identifier)
            if action not in available_actions(candidate):
                raise ValueError('Action is unavailable for this candidate state')
            self.store.action(identifier, action)
            return candidate_summary(self.candidate(identifier))

    def history(self, identifier):
        candidate = self.candidate(identifier)
        attempts = [{key: attempt[key] for key in ('id', 'started', 'finished', 'status', 'error', 'retry_at', 'download_bytes') if key in attempt}
                    for attempt in self.store.attempts(identifier)]
        # A cached validation may belong to another candidate with identical bytes. Include only this source history.
        validations = [{key: record[key] for key in ('id', 'status', 'started', 'finished', 'source_sha256', 'validator_fingerprint', 'error') if key in record}
                       for record in self.store.validation_history(candidate.get('artifact', {}).get('sha256', ''))]
        delivery = {key: candidate['handoff'][key] for key in ('status', 'attempts', 'started', 'finished', 'retry_at', 'error', 'build_id') if key in candidate.get('handoff', {})}
        return {'collection': attempts, 'validation': validations, 'handoff': delivery}

    def evidence(self, identifier, sha256):
        candidate = self.candidate(identifier)
        ref = next((ref for ref in candidate.get('license_evidence', []) if ref.get('sha256') == sha256), None)
        if not ref or not re.fullmatch('[0-9a-f]{64}', sha256):
            raise KeyError('Evidence not found')
        path = self.store.directory / 'staging' / 'objects' / sha256[:2] / sha256
        if (path.is_symlink() or not path.resolve().is_relative_to(self.store.directory / 'staging')
                or not path.is_file() or not 0 <= path.stat().st_size <= MAX_EVIDENCE
                or path.stat().st_size != ref.get('size') or hashlib.sha256(path.read_bytes()).hexdigest() != sha256):
            raise ValueError('Pinned evidence is missing or changed')
        return path
