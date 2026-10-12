"""Retryable transfer to the running local reviewer; no reviewer database access."""

import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx

from .collection import MAX_EVIDENCE, MAX_EVIDENCE_TOTAL, fingerprints
from .lock import collector_lock
from .store import now
from .validation import checked_report, source_path, validator_fingerprint

MAX_METADATA = 1024 * 1024


def reviewer_url(value):
    parts = urlsplit(value)
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parts.username or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise ValueError('Reviewer URL must be a loopback HTTP origin without credentials or a path')
    return value.rstrip('/')


def public_metadata(candidate):
    """Whitelist source facts; never send local paths or human approval fields."""
    record = candidate['validation']
    return {'schema_version': 1, 'candidate_id': candidate['id'],
            **{key: candidate[key] for key in ('repository_id', 'repository', 'revision', 'path', 'blob_sha')},
            'private': False, 'source_sha256': candidate['artifact']['sha256'],
            'size': candidate['artifact']['size'], 'usage_permission': 'unknown',
            'asset_author': None, 'evidence_status': candidate.get('evidence_status', 'unknown'),
            'license_evidence': [{key: ref[key] for key in ('path', 'sha256', 'blob_sha', 'size')}
                                 for ref in candidate.get('license_evidence', [])],
            'validation': {key: record[key] for key in ('source_sha256', 'validator_fingerprint', 'report_sha256')},
            'validation_status': 'passed'}


def validate_metadata(document, content):
    """Receiver verifies identities independently before creating an import."""
    required = {'schema_version', 'candidate_id', 'repository_id', 'repository', 'revision', 'path',
                'blob_sha', 'private', 'source_sha256', 'size', 'usage_permission', 'asset_author',
                'evidence_status', 'license_evidence', 'validation', 'validation_status'}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError('Invalid discovery handoff fields')
    if (document['schema_version'] != 1 or document['private'] is not False
            or document['usage_permission'] != 'unknown' or document['asset_author'] is not None
            or document['validation_status'] != 'passed'):
        raise ValueError('Handoff must contain public, unapproved, validated source metadata')
    if type(document['repository_id']) is not int or document['repository_id'] <= 0:
        raise ValueError('Invalid repository identity')
    if not isinstance(document['repository'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', document['repository']):
        raise ValueError('Invalid repository name')
    for field, length in (('candidate_id', 64), ('source_sha256', 64), ('revision', 40), ('blob_sha', 40)):
        if not isinstance(document[field], str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', document[field]):
            raise ValueError('Invalid pinned source identity')
    path = document['path']
    if (not isinstance(path, str) or len(path) > 4096 or not path.lower().endswith('.litematic')
            or path.startswith('/') or '\\' in path or any(p in ('', '.', '..') for p in path.split('/'))):
        raise ValueError('Invalid source path')
    expected = hashlib.sha256(json.dumps([document['repository_id'], document['revision'], path]).encode()).hexdigest()
    sha = hashlib.sha256(content).hexdigest()
    blob = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
    if (document['candidate_id'] != expected or document['source_sha256'] != sha or document['blob_sha'] != blob
            or type(document['size']) is not int or document['size'] != len(content)):
        raise ValueError('Original does not match pinned handoff identities')
    validation = document['validation']
    if not isinstance(validation, dict) or set(validation) != {'source_sha256', 'validator_fingerprint', 'report_sha256'}:
        raise ValueError('Invalid validation identity')
    if validation['source_sha256'] != sha or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in validation.values()):
        raise ValueError('Invalid validation hashes')
    refs = document['license_evidence']
    if not isinstance(refs, list) or len(refs) > 100 or not isinstance(document['evidence_status'], str):
        raise ValueError('Invalid evidence metadata')
    total = 0
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'blob_sha', 'size'}
                or not isinstance(ref['path'], str) or len(ref['path']) > 4096
                or type(ref['size']) is not int or not 0 <= ref['size'] <= MAX_EVIDENCE
                or not isinstance(ref['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256'])
                or not isinstance(ref['blob_sha'], str) or not re.fullmatch('[0-9a-f]{40}', ref['blob_sha'])):
            raise ValueError('Invalid evidence reference')
        total += ref['size']
    if total > MAX_EVIDENCE_TOTAL:
        raise ValueError('Evidence exceeds handoff limit')
    return document


def _deliver(store, url, transport, report, identifier):
    config = store.config()
    started = time.monotonic()
    # Local delivery never sends the GitHub credential or uses the egress proxy.
    with httpx.Client(base_url=url, trust_env=False, follow_redirects=False, timeout=30, transport=transport) as client:
        while True:
            if store.config().paused:
                report['status'] = 'paused'
                break
            if report['requests'] >= config.max_requests or report['delivered'] + report['failed'] >= config.max_new_files or time.monotonic() - started >= config.max_seconds:
                report['status'] = 'deferred'
                break
            candidate = store.next_handoff(url)
            if not candidate:
                break
            receipt = {'status': 'sending', 'reviewer_url': url, 'attempts': candidate.get('handoff', {}).get('attempts', 0) + 1, 'started': now()}
            store.update_candidate(candidate['id'], {'handoff': receipt}, 'downloaded')
            try:
                source, sha = source_path(store, candidate)
                record = candidate['validation']
                if record['validator_fingerprint'] != validator_fingerprint():
                    raise ValueError('Validation is stale; validate again before handoff')
                path = (store.directory / record['report_path']).resolve()
                if not path.is_relative_to(store.directory / 'staging') or hashlib.sha256(path.read_bytes()).hexdigest() != record['report_sha256']:
                    raise ValueError('Validation report changed')
                checked_report(path, sha, 0)
                metadata = public_metadata(candidate)
                validate_metadata(metadata, source.read_bytes())
                from contextlib import ExitStack
                with ExitStack() as stack:
                    files = [('metadata', ('metadata.json', json.dumps(metadata).encode(), 'application/json')),
                             ('file', (Path(candidate['path']).name, stack.enter_context(source.open('rb')), 'application/octet-stream'))]
                    for ref in candidate.get('license_evidence', []):
                        evidence = store.directory / 'staging' / 'objects' / ref['sha256'][:2] / ref['sha256']
                        if evidence.is_symlink() or not evidence.resolve().is_relative_to(store.directory / 'staging') or fingerprints(evidence, ref['size']) != (ref['blob_sha'], ref['sha256']):
                            raise ValueError('Pinned evidence changed')
                        files.append(('evidence', (ref['sha256'], stack.enter_context(evidence.open('rb')), 'application/octet-stream')))
                    report['requests'] += 1
                    store.save_run(identifier, report | {'status': 'running'})
                    response = client.post('/api/discovery/imports', files=files)
                if response.status_code != 200:
                    raise httpx.RequestError('Reviewer did not accept the handoff')
                result = response.json()
                if (not isinstance(result, dict) or result.get('candidate_id') != candidate['id'] or result.get('source_sha256') != sha
                        or not isinstance(result.get('build_id'), str) or not re.fullmatch('[0-9a-f]{32}', result['build_id'])):
                    raise ValueError('Reviewer returned an invalid receipt')
                receipt.update(status='delivered', build_id=result['build_id'], source_sha256=sha, finished=now())
                report['delivered'] += 1
            except httpx.HTTPError:
                receipt.update(status='deferred', error='Local reviewer unavailable or did not accept handoff; staged files retained', retry_at=time.time() + 60)
                report['status'] = 'deferred'
            except (ValueError, OSError, KeyError):
                receipt.update(status='failed', error='Handoff identity or artifact verification failed; revalidate before retry')
                report['failed'] += 1
            store.update_candidate(candidate['id'], {'handoff': receipt}, 'downloaded')
            store.save_run(identifier, report | {'status': 'running'})
            if receipt['status'] == 'deferred':
                break
    if report['failed'] and report['status'] == 'completed':
        report['status'] = 'failed'
    return report


def handoff(store, url='http://127.0.0.1:8765', *, transport=None):
    url = reviewer_url(url)
    report = {'kind': 'handoff', 'status': 'completed', 'started': now(),
              'delivered': 0, 'failed': 0, 'requests': 0}
    identifier = uuid.uuid4().hex
    with collector_lock(store.directory / 'collector.lock'):
        for previous in store.status()['runs']:
            if previous['status'] == 'running':
                store.save_run(previous['id'], previous | {'status': 'interrupted', 'finished': now()})
        store.save_run(identifier, report | {'status': 'running'})
        try:
            return _deliver(store, url, transport, report, identifier)
        except KeyboardInterrupt:
            report['status'] = 'interrupted'
            raise
        except BaseException:
            report['status'] = 'failed'
            raise
        finally:
            report['finished'] = now()
            store.save_run(identifier, report)
