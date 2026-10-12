"""Offline, bounded subprocess validation of verified originals, separate from review."""

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .collection import MAX_ASSET, fingerprints
from .github import Deferred
from .lock import collector_lock
from .store import now
from .validation_worker import MAX_REPORT_BYTES

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name('validation_worker.py')
TIMEOUT_SECONDS = 300


def validator_fingerprint():
    digest = hashlib.sha256(b'minebot-isolated-validation-v1')
    for path in (WORKER, Path(__file__), ROOT / 'reviewer/comparison.py', ROOT / 'tools/litematic_pipeline.py'):
        digest.update(path.read_bytes())
    for name in ('nbtlib', 'litemapy', 'numpy'):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = 'unavailable'
        digest.update(f'{name}:{version}'.encode())
    return digest.hexdigest()


def child_environment():
    # Allow platform necessities, not inherited tokens, proxies or Python hooks.
    env = {name: value for name, value in os.environ.items()
           if name.upper() in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH')}
    return env | {'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}


def run_worker(source, output, sha256, *, timeout=TIMEOUT_SECONDS, paused=lambda: False):
    process = subprocess.Popen([sys.executable, '-I', str(WORKER), str(source), str(output), sha256],
                               cwd=output, env=child_environment(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    try:
        while True:
            if paused():
                raise InterruptedError('Validation paused; retry retained')
            if time.monotonic() - started >= timeout:
                raise TimeoutError('Validation exceeded its subprocess timeout')
            try:
                return process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def source_path(store, candidate):
    artifact = candidate.get('artifact', {})
    sha256 = artifact.get('sha256', '')
    if len(sha256) != 64 or any(c not in '0123456789abcdef' for c in sha256):
        raise ValueError('Candidate does not reference a verified original')
    path = store.directory / 'staging' / 'objects' / sha256[:2] / sha256
    if path.is_symlink() or not path.resolve().is_relative_to(store.directory / 'staging'):
        raise ValueError('Original must remain in collector staging')
    size = artifact.get('size')
    if type(size) is not int or not 0 <= size <= MAX_ASSET or not path.is_file() or path.stat().st_size != size:
        raise ValueError('Original is missing or its size changed')
    if fingerprints(path, size) != (candidate['blob_sha'], sha256):
        raise ValueError('Original hashes changed; validation cannot proceed')
    if artifact.get('is_lfs_pointer'):
        raise ValueError('LFS pointers are not schematic inputs')
    return path, sha256


def checked_report(path, source_sha256, exit_code):
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError('Validation worker did not produce a bounded report')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema_version') != 1 or report.get('source_sha256') != source_sha256 or type(report.get('passed')) is not bool:
        raise ValueError('Validation worker report identity or schema is invalid')
    if report['passed']:
        if (exit_code != 0 or report.get('source_unchanged') is not True
                or report.get('comparison', {}).get('passed') is not True
                or report.get('archive_comparison', {}).get('passed') is not True
                or report.get('independent_source_reader', {}).get('status') != 'passed'
                or report.get('independent_converted_reader', {}).get('status') != 'passed'):
            raise ValueError('Validation report lacks all required successful checks')
        converted = path.parent / 'converted.litematic'
        record = report.get('converted', {})
        size = record.get('size')
        if (type(size) is not int or not 0 <= size <= MAX_ASSET or not converted.is_file()
                or converted.stat().st_size != size or fingerprints(converted, size)[1] != record.get('sha256')):
            raise ValueError('Converted artifact does not match its validation report')
    return report


def validate(store, worker=run_worker):
    with collector_lock(store.directory / 'collector.lock'):
        fingerprint = validator_fingerprint()
        config = store.config()
        identifier = uuid.uuid4().hex
        report = {'kind': 'validation', 'started': now(), 'status': 'running', 'processed': 0,
                  'passed': 0, 'failed': 0, 'reused': 0, 'validator_fingerprint': fingerprint}
        for previous in store.status()['runs']:
            if previous['status'] == 'running':
                store.save_run(previous['id'], previous | {'status': 'interrupted', 'finished': now()})
        store.recover_validations()
        store.save_run(identifier, report)
        started = time.monotonic()
        active = None
        candidate = None
        directory = None
        try:
            if config.paused:
                report['status'] = 'paused'
                return report
            while candidate := store.next_validation(fingerprint):
                if store.config().paused:
                    report['status'] = 'paused'
                    break
                remaining = config.max_seconds - (time.monotonic() - started)
                if report['processed'] >= config.max_new_files or remaining <= 0:
                    raise Deferred('Validation run budget reached; candidates retained')
                uid = uuid.uuid4().hex
                sha256 = candidate.get('artifact', {}).get('sha256', '')
                active = {'id': uid, 'run_id': identifier, 'candidate_id': candidate['id'],
                          'source_sha256': sha256, 'validator_fingerprint': fingerprint,
                          'status': 'running', 'started': now()}
                directory = store.directory / 'staging' / 'validation' / candidate['id'] / uid
                try:
                    source, sha256 = source_path(store, candidate)
                    cached = store.cached_validation(sha256, fingerprint)
                    if cached:
                        path = store.directory / cached['report_path']
                        if hashlib.sha256(path.read_bytes()).hexdigest() != cached.get('report_sha256'):
                            raise ValueError('Cached validation report changed')
                        checked_report(path, sha256, 0)
                        record = cached
                        report['reused'] += 1
                    else:
                        staging = store.directory / 'staging'
                        used = sum(path.stat().st_size for path in staging.rglob('*') if path.is_file())
                        # Reserve the full bounded output and report before starting any writer.
                        if config.max_storage_bytes - used < MAX_ASSET + MAX_REPORT_BYTES:
                            active = None
                            raise Deferred('Storage budget cannot reserve validation output; candidate retained')
                        directory.mkdir(parents=True)
                        active['report_path'] = (directory / 'report.json').relative_to(store.directory).as_posix()
                        store.save_validation(uid, active)
                        store.update_candidate(candidate['id'], {'validation_status': 'validating'}, 'downloaded')
                        exit_code = worker(source, directory, sha256, timeout=min(TIMEOUT_SECONDS, remaining),
                                           paused=lambda: store.config().paused)
                        details = checked_report(directory / 'report.json', sha256, exit_code)
                        if source_path(store, candidate)[1] != sha256:
                            raise ValueError('Original changed during validation')
                        record = active | {'status': 'passed' if details['passed'] else 'failed',
                                           'finished': now(), 'report': details,
                                           'report_sha256': hashlib.sha256((directory / 'report.json').read_bytes()).hexdigest()}
                        if details['passed']:
                            record['converted'] = details['converted'] | {
                                'local_path': (directory / 'converted.litematic').relative_to(store.directory).as_posix()}
                        store.save_validation(uid, record)
                    status = record['status']
                    changes = {'validation': record, 'validation_status': status,
                               'visual_approval': 'pending', 'training_eligibility': 'human_review_required'}
                    measured = record.get('report', {}).get('redstone', {})
                    if measured.get('matched_block_count', 0):
                        suggestions = [s for s in candidate.get('suggestions', []) if s.get('origin') != 'measured_redstone_v1']
                        changes['suggestions'] = suggestions + [{'field': 'redstone', 'value': 'yes',
                            'origin': 'measured_redstone_v1', 'confidence': None, 'confirmed': False,
                            'matched_block_count': measured['matched_block_count']}]
                    store.update_candidate(candidate['id'], changes, 'downloaded')
                    report[status] += 1
                except InterruptedError:
                    raise
                except (ValueError, OSError, TimeoutError) as error:
                    directory.mkdir(parents=True, exist_ok=True)
                    failure_path = directory / 'failure.json'
                    record = active | {'status': 'failed', 'finished': now(),
                                       'error': {'kind': type(error).__name__, 'message': 'Validation failed; originals and prior reports retained'},
                                       'report_path': failure_path.relative_to(store.directory).as_posix()}
                    failure_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
                    store.save_validation(uid, record)
                    store.update_candidate(candidate['id'], {'validation_status': 'failed', 'validation': record}, 'downloaded')
                    report['failed'] += 1
                active = None
                report['processed'] += 1
                store.save_run(identifier, report)
            else:
                report['status'] = 'completed'
        except Deferred as error:
            report.update(status='deferred', error=str(error))
        except InterruptedError:
            report['status'] = 'paused'
        except KeyboardInterrupt:
            report['status'] = 'interrupted'
            raise
        except BaseException:
            report['status'] = 'failed'
            raise
        finally:
            if active:
                record = active | {'status': 'interrupted', 'finished': now()}
                store.save_validation(active['id'], record)
                store.update_candidate(candidate['id'], {'validation_status': 'interrupted', 'validation': record}, 'downloaded')
            report['finished'] = now()
            store.save_run(identifier, report)
        return report
