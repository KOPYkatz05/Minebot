"""Read-only, serial GitHub REST access with durable deferral signals."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import time
from urllib.parse import quote

import httpx


class Deferred(Exception):
    def __init__(self, message, retry_at=None):
        super().__init__(message)
        self.retry_at = retry_at


class SetupError(Exception):
    pass


class GitHub:
    def __init__(self, config, token=None, transport=None):
        token = token if token is not None else os.environ.get('GITHUB_TOKEN', '')
        if not token.strip():
            raise SetupError('GITHUB_TOKEN is missing; configure read-only public GitHub access')
        self.config = config
        self.requests = 0
        self.started = time.monotonic()
        self.next_request = 0.0
        self.client = httpx.Client(base_url='https://api.github.com', timeout=30, follow_redirects=False,
                                   transport=transport, headers={'Authorization': f'Bearer {token}',
                                   'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                                   'User-Agent': 'Minebot-discovery/0.1'})

    def close(self):
        self.client.close()

    def get(self, path, params=None):
        if not path.startswith('/') or path.startswith('//') or '://' in path:
            raise ValueError('GitHub requests require relative API paths')
        if self.requests >= self.config.max_requests or time.monotonic() - self.started >= self.config.max_seconds:
            raise Deferred('Run request or time budget reached')
        if time.time() < self.next_request:
            raise Deferred('GitHub rate limit reached', self.next_request)
        self.requests += 1
        try:
            with self.client.stream('GET', path, params=params) as response:
                status = response.status_code
                if status == 401:
                    raise SetupError('GitHub rejected GITHUB_TOKEN; check or replace the credential')
                if status in (403, 429):
                    retry = response.headers.get('retry-after')
                    try:
                        delay = float(retry) if retry else 60.0
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (TypeError, ValueError, OverflowError):
                            delay = 60.0
                    try:
                        reset = float(response.headers.get('x-ratelimit-reset', 0))
                    except ValueError:
                        reset = 0
                    raise Deferred(f'GitHub access deferred (HTTP {status}); inspect credential permissions if repeated',
                                   max(time.time() + max(1, delay), reset))
                if status >= 500:
                    raise Deferred('GitHub temporarily unavailable', time.time() + 60)
                if status == 404:
                    raise ValueError('GitHub resource not found or no longer accessible')
                if status != 200:
                    raise ValueError(f'GitHub request failed (HTTP {status})')
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > 16 * 1024**2:
                        raise ValueError('GitHub metadata response exceeds 16 MiB')
                if response.headers.get('x-ratelimit-remaining') == '0':
                    try:
                        self.next_request = float(response.headers.get('x-ratelimit-reset', time.time() + 60))
                    except ValueError:
                        self.next_request = time.time() + 60
                import json
                return json.loads(body)
        except httpx.HTTPError:
            # Never persist exception URLs, headers, response text, or credentials.
            raise Deferred('GitHub network request failed', time.time() + 60) from None

    def repository(self, name):
        repo = self.get('/repos/' + quote(name, safe='/'))
        if repo.get('private') is not False or repo.get('visibility', 'public') != 'public':
            raise ValueError('Only public repositories are collected')
        return repo

    def revision(self, repo):
        return self.get(f"/repos/{repo['full_name']}/commits/{quote(repo['default_branch'], safe='')}")

    def tree(self, name, sha, recursive=True):
        return self.get(f'/repos/{name}/git/trees/{quote(sha, safe="")}', {'recursive': '1'} if recursive else None)

    def search(self, query, page):
        return self.get('/search/repositories', {'q': query, 'page': page, 'per_page': 100,
                                                'sort': 'updated', 'order': 'desc'})
