"""Credential-safe authentication and a small metadata-only discovery probe."""

import time

from .collector import run
from .config import Config
from .github import Deferred, GitHub, SetupError


def check_github(store, *, discover=False, budget=10, github_factory=GitHub):
    if type(budget) is not int or not 1 <= budget <= 10:
        raise ValueError('Diagnostic request budget must be 1–10')
    result = {'status': 'running', 'authentication_verified': False, 'requests': 0,
              'request_budget': budget if discover else 1, 'schematic_downloads': 0}
    if store.config().paused:
        return result | {'status': 'paused'}
    if store.retry_at() > time.time():
        return result | {'status': 'deferred', 'retry_at': store.retry_at(),
                         'error': 'Previous GitHub deferral has not elapsed'}
    overrides = {'max_requests': result['request_budget'], 'max_seconds': 120, 'max_new_files': 10}
    config = Config.parse(store.config().document() | overrides)
    github = None
    try:
        github = github_factory(config)
        profile = github.get('/user')
        if type(profile.get('id')) is not int or profile['id'] < 1:
            raise ValueError('GitHub authentication response does not contain a valid user identity')
        result['authentication_verified'] = True
        if discover:
            report = run(store, lambda config: github, config_override=overrides)
            result.update(status=report['status'], discovery=report,
                          candidate_counts=store.status()['candidate_counts'],
                          repository_count=store.status()['repository_count'],
                          pending_work=store.status()['pending_work'])
        else:
            result['status'] = 'completed'
    except SetupError as error:
        result.update(status='setup_error', error=str(error))
    except Deferred as error:
        result.update(status='deferred', error=str(error), retry_at=error.retry_at or 0)
        if error.retry_at:
            store.defer_until(error.retry_at)
    except ValueError as error:
        result.update(status='failed', error=str(error))
    finally:
        if github:
            result['requests'] = github.requests
            github.close()
    return result
