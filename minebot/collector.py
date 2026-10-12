"""Checkpointed repository discovery. File download/validation follow in milestone 3."""

import hashlib
from pathlib import PurePosixPath
import time
from urllib.parse import quote
import uuid

from .github import Deferred, GitHub, SetupError
from .config import Config
from .lock import collector_lock
from .store import now


def repository_document(repo):
    return {key: repo[key] for key in ('id', 'full_name', 'private', 'default_branch') } | {
        'owner_login': repo.get('owner', {}).get('login'),
        'visibility': repo.get('visibility', 'public'),
        'url': f"https://github.com/{repo['full_name']}",
    }


def scan_key(repo, revision, prefix, sha):
    suffix = hashlib.sha256(f'{revision}:{prefix}:{sha}'.encode()).hexdigest()
    return f"0tree:{repo['id']}:{suffix}"


def run(store, github_factory=GitHub, config_override=None):
    with collector_lock(store.directory / 'collector.lock'):
        config = Config.parse(store.config().document() | (config_override or {}))
        identifier = uuid.uuid4().hex
        report = {'started': now(), 'status': 'running', 'requests': 0, 'new_candidates': 0,
                  'coverage': 'bounded; repository search is not exhaustive', 'warnings': [], 'error': None}
        # Only the process-lock owner can recover earlier running records.
        for previous in store.status()['runs']:
            if previous['status'] == 'running':
                store.save_run(previous['id'], previous | {'status': 'interrupted', 'finished': now()})
        store.save_run(identifier, report)
        github = None
        try:
            if config.paused:
                report['status'] = 'paused'
                return report
            retry_at = store.retry_at()
            if retry_at > time.time():
                raise Deferred('Previous GitHub deferral has not elapsed', retry_at)
            github = github_factory(config)
            pending_keys = {key for key, _ in store.work()}
            for query in config.queries:
                key = '2query:' + hashlib.sha256(query.encode()).hexdigest()
                if key not in pending_keys:
                    store.put_work(key, {'kind': 'query', 'query': query, 'page': 1})
            for name in config.watched_repositories:
                key = '1repo:' + name.casefold()
                if key not in pending_keys:
                    store.put_work(key, {'kind': 'repository', 'name': name})
            while store.work():
                if store.config().paused:
                    report['status'] = 'paused'
                    break
                if report['new_candidates'] >= config.max_new_files:
                    raise Deferred('Candidate budget reached; remaining work saved')
                if time.monotonic() - github.started >= config.max_seconds:
                    raise Deferred('Run time budget reached; remaining work saved')
                key, task = store.work()[0]
                if task.get('retry_at', 0) > time.time():
                    raise Deferred('Saved work is waiting for retry', task['retry_at'])
                if task.get('name', '').casefold() in {r.casefold() for r in config.excluded_repositories}:
                    store.remove_work(key)
                    continue
                try:
                    if task['kind'] == 'query':
                        if task['query'] not in config.queries:
                            store.remove_work(key)
                            continue
                        result = github.search(task['query'], task['page'])
                        if result.get('incomplete_results') or result.get('total_count', 0) > 1000:
                            warning = f"Search coverage incomplete or capped: {task['query']}"
                            if warning not in report['warnings']:
                                report['warnings'].append(warning)
                        for repo in result['items']:
                            if repo.get('private') is False and repo.get('visibility', 'public') == 'public':
                                store.put_work('1repo:' + repo['full_name'].casefold(), {'kind': 'repository', 'name': repo['full_name']})
                        if len(result['items']) == 100 and task['page'] < 10:
                            store.put_work(key, task | {'page': task['page'] + 1})
                        else:
                            store.remove_work(key)
                    elif task['kind'] == 'repository':
                        repo = github.repository(task['name'])
                        store.repository(repository_document(repo))
                        commit = github.revision(repo)
                        work = {'kind': 'tree', 'name': repo['full_name'], 'repository': repository_document(repo),
                                'revision': commit['sha'], 'sha': commit['commit']['tree']['sha'],
                                'prefix': '', 'recursive': True}
                        store.put_work(scan_key(repo, work['revision'], '', work['sha']), work)
                        store.remove_work(key)
                    else:
                        if 'entries' not in task:
                            result = github.tree(task['name'], task['sha'], task['recursive'])
                            if result.get('truncated'):
                                if not task['recursive']:
                                    raise ValueError('Nonrecursive tree truncated; cannot claim complete traversal')
                                store.put_work(key, task | {'recursive': False})
                                continue
                            task = task | {'entries': result['tree'], 'index': 0}
                            store.put_work(key, task)
                        for index in range(task['index'], len(task['entries'])):
                            if report['new_candidates'] >= config.max_new_files:
                                raise Deferred('Candidate budget reached; traversal position saved')
                            entry = task['entries'][index]
                            path = task['prefix'] + entry['path']
                            if entry['type'] == 'tree' and not task['recursive']:
                                child = {k: v for k, v in task.items() if k not in ('entries', 'index')}
                                child.update(sha=entry['sha'], prefix=path + '/', recursive=False)
                                store.put_work(scan_key(task['repository'], task['revision'], child['prefix'], child['sha']), child)
                            elif entry['type'] in ('blob', 'commit'):
                                suffix = PurePosixPath(path).suffix.lower()
                                supported = suffix == '.litematic' and entry.get('mode', '100644') in ('100644', '100755')
                                if supported or suffix in ('.litematic', '.zip', '.schem', '.schematic', '.7z', '.rar') or entry['type'] == 'commit':
                                    document = {'repository_id': task['repository']['id'], 'repository': task['name'],
                                                'owner_login': task['repository']['owner_login'], 'asset_author': None,
                                                'revision': task['revision'], 'blob_sha': entry['sha'], 'path': path,
                                                'size': entry.get('size'), 'status': 'discovered' if supported else 'unsupported',
                                                'source_url': f"https://github.com/{task['name']}/blob/{task['revision']}/{quote(path, safe='/')}",
                                                'usage_permission': 'unknown', 'license_evidence': [],
                                                'evidence_status': 'not_collected_yet',
                                                'unsupported_reason': None if supported else 'Other formats, archives and submodules are not supported',
                                                'suggestions': ([{'field': 'building_type', 'value': 'house', 'origin': 'filename_house_v1', 'confidence': None}] if 'house' in path.casefold() else [])}
                                    _, created = store.candidate(document)
                                    report['new_candidates'] += int(created)
                            task['index'] = index + 1
                            store.put_work(key, task)
                        store.remove_work(key)
                except ValueError as error:
                    # Public-facing errors are generated locally, never include API bodies.
                    report['warnings'].append(str(error))
                    store.remove_work(key)
                except Deferred as error:
                    if error.retry_at:
                        attempts = task.get('attempts', 0) + 1
                        task.update(attempts=attempts, retry_at=error.retry_at)
                        store.put_work(key, task)
                    raise
            else:
                report['status'] = 'completed'
        except SetupError as error:
            report.update(status='setup_error', error=str(error))
        except Deferred as error:
            report.update(status='deferred', error=str(error), retry_at=error.retry_at or 0)
            if error.retry_at:
                store.defer_until(error.retry_at)
        except Exception:
            report.update(status='failed', error='Unexpected collector failure; queued work retained')
            raise
        finally:
            if github:
                report['requests'] = github.requests
                github.close()
            report['finished'] = now()
            store.save_run(identifier, report)
        return report
