"""Deterministic tests; no credentials, source assets or live network required."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

from minebot.cli import main
from minebot.collector import run
from minebot.config import Config
from minebot.github import Deferred, GitHub, SetupError
from minebot.lock import collector_lock
from minebot.store import Store


REPO = {'id': 42, 'full_name': 'example/builds', 'private': False, 'visibility': 'public',
        'default_branch': 'main', 'owner': {'login': 'example'}}
COMMIT = {'sha': 'a' * 40, 'commit': {'tree': {'sha': 'b' * 40}}}


def blob(path, sha='c' * 40):
    return {'path': path, 'type': 'blob', 'mode': '100644', 'sha': sha, 'size': 128}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.store = Store(self.directory)
        self.store.configure({'queries': [], 'watched_repositories': ['example/builds']})
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def factory(self, handler=None):
        def default(request):
            self.calls.append(str(request.url))
            if request.url.path == '/repos/example/builds':
                return httpx.Response(200, json=REPO)
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT)
            return httpx.Response(200, json={'truncated': False, 'tree': [blob('Oak House.LITEMATIC')]})
        return lambda config: GitHub(config, token='TEST_SECRET_NEVER_PERSIST', transport=httpx.MockTransport(handler or default))

    def test_defaults_and_reject_invalid_or_secret_config(self):
        config = Config()
        self.assertEqual(config.max_storage_bytes, 10 * 1024**3)
        self.assertEqual(config.max_download_bytes, 1024**3)
        self.assertEqual(config.max_requests, 500)
        for changes in ({'GITHUB_TOKEN': 'secret'}, {'max_requests': True}, {'paused': 'yes'},
                        {'queries': ['']}, {'watched_repositories': ['https://evil.example']},
                        {'max_seconds': 0}, {'queries': 'string'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Config.parse(changes)

    def test_pinned_public_candidates_survive_restart_and_repeat(self):
        report = run(self.store, self.factory())
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['requests'], 3)
        restored = Store(self.directory)
        candidates = restored.candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['revision'], COMMIT['sha'])
        self.assertEqual(candidates[0]['blob_sha'], 'c' * 40)
        self.assertEqual(candidates[0]['usage_permission'], 'unknown')
        self.assertEqual(candidates[0]['evidence_status'], 'not_collected_yet')
        self.assertIsNone(candidates[0]['asset_author'])
        self.assertIn('Oak%20House.LITEMATIC', candidates[0]['source_url'])
        self.assertEqual(candidates[0]['suggestions'][0]['origin'], 'filename_house_v1')
        self.assertEqual(run(restored, self.factory())['new_candidates'], 0)
        self.assertNotIn('TEST_SECRET', json.dumps(restored.status()))

    def test_later_commit_retains_separate_version_of_same_blob(self):
        run(self.store, self.factory())
        def changed(request):
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT | {'sha': 'd' * 40})
            if '/git/trees/' in request.url.path:
                return httpx.Response(200, json={'tree': [blob('Oak House.LITEMATIC')]})
            return httpx.Response(200, json=REPO)
        run(self.store, self.factory(changed))
        self.assertEqual(len(self.store.candidates()), 2)
        self.assertEqual({c['blob_sha'] for c in self.store.candidates()}, {'c' * 40})

    def test_stable_repository_identity_after_rename(self):
        self.store.repository(REPO)
        self.store.repository(REPO | {'full_name': 'example/renamed'})
        self.assertEqual(self.store.status()['repository_count'], 1)

    def test_private_repository_never_inspected(self):
        def private(request):
            self.calls.append(str(request.url))
            return httpx.Response(200, json=REPO | {'private': True, 'visibility': 'private'})
        report = run(self.store, self.factory(private))
        self.assertEqual(report['new_candidates'], 0)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('Only public', report['warnings'][0])

    def test_exclusion_is_case_insensitive(self):
        self.store.configure({'excluded_repositories': ['EXAMPLE/BUILDS']})
        run(self.store, self.factory())
        self.assertEqual(self.calls, [])

    def test_truncated_recursive_tree_walks_children(self):
        def truncated(request):
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT)
            if '/git/trees/' in request.url.path:
                self.calls.append(str(request.url))
                if request.url.params.get('recursive'):
                    return httpx.Response(200, json={'truncated': True, 'tree': [blob('partial.litematic')]})
                if request.url.path.endswith('b' * 40):
                    return httpx.Response(200, json={'truncated': False, 'tree': [{'path': 'houses', 'sha': 'd' * 40, 'type': 'tree'}]})
                return httpx.Response(200, json={'truncated': False, 'tree': [blob('tiny.litematic')]})
            return httpx.Response(200, json=REPO)
        report = run(self.store, self.factory(truncated))
        self.assertEqual(report['status'], 'completed')
        self.assertEqual([c['path'] for c in self.store.candidates()], ['houses/tiny.litematic'])
        self.assertEqual(len(self.calls), 3)

    def test_tree_position_resumes_without_refetch_after_candidate_budget(self):
        self.store.configure({'max_new_files': 1})
        def several(request):
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT)
            if '/git/trees/' in request.url.path:
                self.calls.append('tree')
                return httpx.Response(200, json={'tree': [blob('one.litematic'), blob('two.litematic')]})
            return httpx.Response(200, json=REPO)
        self.assertEqual(run(self.store, self.factory(several))['status'], 'deferred')
        self.store.configure({'watched_repositories': []})
        run(Store(self.directory), self.factory(several))
        self.assertEqual(len(self.store.candidates()), 2)
        self.assertEqual(self.calls, ['tree'])

    def test_request_budget_keeps_remaining_work(self):
        self.store.configure({'max_requests': 1})
        report = run(self.store, self.factory())
        self.assertEqual(report['status'], 'deferred')
        self.assertEqual(report['requests'], 1)
        self.assertEqual(self.store.status()['pending_work'], 1)

    def test_query_pagination_and_incomplete_coverage_warning(self):
        self.store.configure({'queries': ['topic:litematica'], 'watched_repositories': []})
        def paginated(request):
            if request.url.path == '/search/repositories':
                page = request.url.params['page']
                self.calls.append(page)
                return httpx.Response(200, json={'total_count': 101, 'incomplete_results': True,
                                               'items': [REPO] * 100 if page == '1' else []})
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT)
            if '/git/trees/' in request.url.path:
                return httpx.Response(200, json={'tree': [blob('test.litematic')]})
            return httpx.Response(200, json=REPO)
        report = run(self.store, self.factory(paginated))
        self.assertEqual(self.calls, ['1', '2'])
        self.assertEqual(report['new_candidates'], 1)
        self.assertIn('coverage incomplete', report['warnings'][0])

    def test_unsupported_formats_and_submodule(self):
        def unsupported(request):
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT)
            if '/git/trees/' in request.url.path:
                return httpx.Response(200, json={'tree': [blob('build.zip'), blob('build.schem'),
                    {'path': 'external', 'sha': 'c' * 40, 'type': 'commit'}]})
            return httpx.Response(200, json=REPO)
        run(self.store, self.factory(unsupported))
        self.assertEqual(self.store.status()['candidate_counts'], {'unsupported': 3})

    def test_missing_and_rejected_credentials_are_setup_errors(self):
        with self.assertRaises(SetupError):
            GitHub(Config(), token='')
        report = run(self.store, self.factory(lambda r: httpx.Response(401, text='TEST_SECRET_NEVER_PERSIST')))
        self.assertEqual(report['status'], 'setup_error')
        self.assertNotIn('TEST_SECRET', json.dumps(self.store.status()))

    def test_rate_limit_defers_without_sleep_and_preserves_queue(self):
        before = time.monotonic()
        report = run(self.store, self.factory(lambda r: httpx.Response(429, headers={'Retry-After': '3600'})))
        self.assertEqual(report['status'], 'deferred')
        self.assertGreater(report['retry_at'], time.time() + 3500)
        self.assertLess(time.monotonic() - before, 5)
        self.assertEqual(self.store.work()[0][1]['attempts'], 1)
        self.assertEqual(run(self.store, self.factory())['requests'], 0)

    def test_temporary_server_failure_is_deferred(self):
        report = run(self.store, self.factory(lambda r: httpx.Response(503)))
        self.assertEqual(report['status'], 'deferred')
        self.assertGreater(report['retry_at'], time.time())

    def test_pause_dismiss_restore_and_history_survive_restart(self):
        run(self.store, self.factory())
        candidate = self.store.candidates()[0]
        self.store.action(candidate['id'], 'dismiss')
        restarted = Store(self.directory)
        self.assertEqual(restarted.candidates()[0]['status'], 'dismissed')
        restarted.action(candidate['id'], 'restore')
        self.assertEqual(restarted.candidates()[0]['status'], 'discovered')
        restarted.configure({'paused': True})
        self.assertEqual(run(restarted, self.factory())['status'], 'paused')
        self.assertEqual(len(restarted.status()['runs']), 2)

    def test_recover_interrupted_run_only_after_lock(self):
        self.store.save_run('previous', {'status': 'running', 'started': 'old'})
        run(self.store, self.factory())
        previous = next(r for r in self.store.status()['runs'] if r['id'] == 'previous')
        self.assertEqual(previous['status'], 'interrupted')

    def test_lock_blocks_a_second_process_and_releases(self):
        script = "from pathlib import Path; from minebot.lock import collector_lock, AlreadyRunning\ntry:\n with collector_lock(Path(__import__('sys').argv[1])): print('acquired')\nexcept AlreadyRunning: print('busy')"
        lock_path = self.directory / 'collector.lock'
        with collector_lock(lock_path):
            result = subprocess.run([sys.executable, '-c', script, str(lock_path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout.strip(), 'busy', result.stderr)
        result = subprocess.run([sys.executable, '-c', script, str(lock_path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.stdout.strip(), 'acquired', result.stderr)

    def test_newer_schema_rejected(self):
        with self.store.db() as db:
            db.execute('PRAGMA user_version=999')
        with self.assertRaisesRegex(ValueError, 'newer version'):
            Store(self.directory)

    def test_offline_cli_configuration_and_limit_validation(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['--data-dir', str(self.directory), 'status'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['implementation_stage'], 'isolated_validation')
        with self.assertRaises(ValueError):
            self.store.candidates(limit=1001)
        with self.assertRaises(ValueError):
            self.store.action('absent', 'dismiss')

    def test_client_rejects_nonrelative_paths(self):
        client = self.factory()(Config())
        try:
            for path in ('https://evil.example', '//evil.example'):
                with self.assertRaises(ValueError):
                    client.get(path)
        finally:
            client.close()

    def test_litematic_symlink_is_recorded_as_unsupported(self):
        def symlink(request):
            if '/commits/' in request.url.path:
                return httpx.Response(200, json=COMMIT)
            if '/git/trees/' in request.url.path:
                return httpx.Response(200, json={'tree': [blob('linked.litematic') | {'mode': '120000'}]})
            return httpx.Response(200, json=REPO)
        run(self.store, self.factory(symlink))
        self.assertEqual(self.store.candidates()[0]['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
