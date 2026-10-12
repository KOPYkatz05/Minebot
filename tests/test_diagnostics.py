"""Bounded metadata probes authenticate without exposing credential or profile content."""

import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx

from minebot.diagnostics import check_github
from minebot.github import GitHub
from minebot.store import Store


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.store.configure({'queries': ['topic:litematica'], 'watched_repositories': []})
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def handler(self, request):
        self.calls.append(request)
        path = request.url.path
        repo = {'id': 42, 'full_name': 'example/builds', 'private': False,
                'default_branch': 'main', 'owner': {'login': 'example'}}
        if path == '/user':
            return httpx.Response(200, json={'id': 123, 'login': 'DO_NOT_PERSIST_PROFILE'})
        if path == '/search/repositories':
            return httpx.Response(200, json={'items': [repo]})
        if path == '/repos/example/builds':
            return httpx.Response(200, json=repo)
        if '/commits/' in path:
            return httpx.Response(200, json={'sha': 'a' * 40, 'commit': {'tree': {'sha': 'b' * 40}}})
        if '/git/trees/' in path:
            return httpx.Response(200, json={'tree': [{'type': 'blob', 'mode': '100644', 'path': 'house.litematic',
                                                     'size': 12, 'sha': 'c' * 40}]})
        self.fail('Probe attempted an unexpected endpoint or asset download')

    def factory(self, handler=None, token='TEST_SECRET_NEVER_PERSIST'):
        return lambda config: GitHub(config, token=token, transport=httpx.MockTransport(handler or self.handler))

    def test_authentication_uses_one_request_and_never_persists_profile(self):
        result = check_github(self.store, github_factory=self.factory())
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['authentication_verified'])
        self.assertEqual(result['requests'], 1)
        self.assertEqual(result['schematic_downloads'], 0)
        self.assertEqual(len(self.store.status()['runs']), 0)
        for forbidden in ('TEST_SECRET', 'DO_NOT_PERSIST_PROFILE'):
            self.assertNotIn(forbidden, json.dumps(result))
            self.assertNotIn(forbidden.encode(), self.store.database.read_bytes())

    def test_metadata_probe_keeps_persisted_configuration_and_uses_no_blob_endpoints(self):
        original = self.store.config().document()
        result = check_github(self.store, discover=True, github_factory=self.factory())
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['authentication_verified'])
        self.assertEqual(result['requests'], 5)
        self.assertEqual(result['discovery']['new_candidates'], 1)
        self.assertEqual(self.store.config().document(), original)
        self.assertEqual(self.store.candidates()[0]['status'], 'discovered')
        self.assertFalse((self.store.directory / 'staging').exists())

    def test_auth_request_counts_toward_total_budget(self):
        for budget in (1, 2, 3, 4):
            with self.subTest(budget=budget):
                fresh = Store(self.store.directory / str(budget))
                fresh.configure({'queries': ['topic:litematica'], 'watched_repositories': []})
                self.calls.clear()
                result = check_github(fresh, discover=True, budget=budget, github_factory=self.factory())
                self.assertTrue(result['authentication_verified'])
                self.assertEqual(result['status'], 'deferred')
                self.assertEqual(result['requests'], budget)
                self.assertEqual(len(self.calls), budget)
                self.assertEqual(result['schematic_downloads'], 0)

    def test_missing_and_rejected_credentials_do_not_start_discovery(self):
        result = check_github(self.store, discover=True, github_factory=self.factory(token=''))
        self.assertEqual(result['status'], 'setup_error')
        self.assertEqual(result['requests'], 0)
        result = check_github(self.store, discover=True, github_factory=self.factory(lambda r: httpx.Response(401)))
        self.assertEqual(result['status'], 'setup_error')
        self.assertEqual(result['requests'], 1)
        self.assertEqual(self.store.status()['pending_work'], 0)

    def test_invalid_identity_cannot_be_reported_as_verified(self):
        result = check_github(self.store, github_factory=self.factory(lambda r: httpx.Response(200, json={'id': True})))
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['authentication_verified'])

    def test_rate_limit_is_retained_without_sleep_or_discovery(self):
        result = check_github(self.store, discover=True,
                              github_factory=self.factory(lambda r: httpx.Response(429, headers={'Retry-After': '600'})))
        self.assertEqual(result['status'], 'deferred')
        self.assertGreater(self.store.retry_at(), time.time())
        self.assertEqual(check_github(self.store, github_factory=self.factory())['requests'], 0)

    def test_paused_probe_makes_no_requests(self):
        self.store.configure({'paused': True})
        result = check_github(self.store, discover=True, github_factory=self.factory())
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(result['requests'], 0)

    def test_probe_rejects_large_or_invalid_budgets(self):
        for budget in (0, -1, 11, True):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                check_github(self.store, discover=True, budget=budget, github_factory=self.factory())


if __name__ == '__main__':
    unittest.main()
