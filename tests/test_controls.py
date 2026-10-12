"""Collector UI controls operate on staging without modifying reviewer decisions."""
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest

import test_handoff as fixtures
from minebot.control import CollectorControls
from minebot.handoff import handoff
from minebot.lock import AlreadyRunning, collector_lock
from minebot.store import Store
import httpx

BASE = '/api/discovery/collector'


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HandoffTests('test_delivery_preserves_unknown_usage_and_evidence')
        self.fixture.setUp()
        self.store, self.client = self.fixture.store, self.fixture.client
        self.identifier = self.fixture.identifier

    def tearDown(self):
        self.fixture.tearDown()

    def action(self, action, identifier=None):
        return self.client.post(f'{BASE}/candidates/{identifier or self.identifier}/actions', json={'action': action})

    def add(self, path, status='discovered', **fields):
        document = {'repository_id': 123, 'repository': 'test/owned', 'revision': 'a' * 40,
                    'path': path, 'blob_sha': 'b' * 40, 'status': status} | fields
        return self.store.candidate(document)[0]

    def test_pause_during_active_run_and_candidate_mutation_conflict(self):
        with collector_lock(self.store.directory / 'collector.lock'):
            # Existing schema can be opened by a reviewer starting during an active CLI run.
            another = CollectorControls(self.store.directory)
            self.assertTrue(another.status()['active'])
            state = self.client.get(BASE).json()
            self.assertTrue(state['active'])
            self.assertEqual(self.client.post(BASE + '/pause', json={'paused': True}).status_code, 200)
            self.assertTrue(Store.open_existing(self.store.directory).config().paused)
            self.assertEqual(self.action('dismiss').status_code, 409)
        self.assertEqual(self.action('dismiss').json()['status'], 'dismissed')
        self.assertEqual(self.client.post(BASE + '/pause', json={'paused': False}).status_code, 200)
        self.assertFalse(Store.open_existing(self.store.directory).config().paused)

    def test_retry_collection_validation_and_delivery_preserve_history_and_server_limits(self):
        failed = self.add('failed.litematic', 'failed', collection={'error': 'Verification failed'})
        attempt = self.store.start_attempt(failed, 'fixture')
        self.store.finish_attempt(attempt, {'status': 'failed', 'error': 'Verification failed'})
        self.store.defer_until(12345678900)
        self.assertEqual(self.action('retry', failed).json()['status'], 'discovered')
        self.assertEqual(self.store.retry_at(), 12345678900)
        history = self.client.get(f'{BASE}/candidates/{failed}/history').json()
        self.assertEqual(history['collection'][0]['error'], 'Verification failed')
        self.store.update_candidate(self.identifier, {'validation_status': 'failed'}, 'downloaded')
        self.assertEqual(self.action('retry-validation').json()['validation_status'], 'not_validated')
        self.store.update_candidate(self.identifier, {'validation_status': 'passed', 'handoff': {'status': 'deferred', 'retry_at': 12345678900, 'attempts': 2}}, 'downloaded')
        result = self.action('retry-handoff').json()
        self.assertEqual(result['handoff_status'], 'pending')
        self.assertEqual(result['retry_at'], 0)
        self.assertEqual(self.client.get(f'{BASE}/candidates/{self.identifier}/history').json()['handoff']['attempts'], 2)

    def test_held_sources_and_successful_validation_cannot_be_force_retried(self):
        held = self.add('held.litematic', 'held')
        self.assertEqual(self.action('retry', held).status_code, 400)
        self.assertEqual(self.action('retry-validation').status_code, 400)
        self.assertEqual(self.action('retry-handoff').status_code, 400)
        self.assertEqual(self.action('restore').status_code, 400)
        self.assertEqual(self.action('approve').status_code, 400)

    def test_dismiss_restore_delivery_does_not_touch_approval_or_dataset_membership(self):
        self.assertEqual(handoff(self.store, transport=httpx.MockTransport(self.fixture.receiver))['delivered'], 1)
        service = self.fixture.app.state.service
        build = service.builds()[0]
        service.process(service.jobs()[0]['id'])
        service.update_build(build['id'], {'type': 'house', 'notes': 'Human classification'})
        service.review(build['id'], 'approved')
        dataset = service.update_dataset({'name': 'Independent'})
        service.membership(dataset['id'], [build['id']], True)
        self.assertEqual(self.action('dismiss').json()['status'], 'dismissed')
        self.assertEqual(self.action('restore').json()['status'], 'downloaded')
        self.assertEqual(service.get_build(build['id'])['review_status'], 'approved')
        self.assertEqual(service.get_build(build['id'])['notes'], 'Human classification')
        self.assertEqual(service.get_dataset(dataset['id'])['build_ids'], [build['id']])
        self.assertEqual(self.store.candidates()[0]['handoff']['status'], 'delivered')

    def test_search_filters_and_pagination_are_literal_and_bounded(self):
        for index in range(30):
            self.add(f'file{index:02}.litematic', 'failed' if index % 2 else 'discovered')
        self.add('percent_100%.litematic')
        page = self.client.get(BASE + '/candidates', params={'limit': 10, 'offset': 10}).json()
        self.assertEqual(len(page['items']), 10)
        self.assertEqual(page['total'], 32)
        self.assertEqual(len(self.client.get(BASE + '/candidates', params={'status': 'failed'}).json()['items']), 15)
        literal = self.client.get(BASE + '/candidates', params={'query': '%'}).json()
        self.assertEqual(literal['total'], 1)
        self.assertEqual(literal['items'][0]['path'], 'percent_100%.litematic')
        for params in ({'limit': 101}, {'offset': -1}, {'status': 'approved'}, {'query': 'x' * 201}):
            self.assertEqual(self.client.get(BASE + '/candidates', params=params).status_code, 400)
        self.assertEqual(self.client.get(f'{BASE}/candidates/' + 'f' * 64 + '/history').status_code, 404)

    def test_storage_counts_staging_but_never_follows_symlinks(self):
        controls = self.fixture.app.state.collector
        before = controls.storage()['used_bytes']
        outside = self.fixture.root / 'outside.txt'
        outside.write_bytes(b'x' * 100000)
        link = self.store.directory / 'staging' / 'external'
        try:
            link.symlink_to(outside)
            (self.store.directory / 'staging' / 'external-dir').symlink_to(self.fixture.root, target_is_directory=True)
        except OSError:
            self.skipTest('Symlinks require local OS permission')
        controls.storage_sample_time = 0
        state = self.client.get(BASE).json()
        self.assertEqual(state['storage']['used_bytes'], before)
        self.assertGreater(state['storage']['validation_bytes'], 0)
        self.assertEqual(state['candidate_counts']['downloaded'], 1)
        self.assertEqual(state['validation_counts']['passed'], 1)
        self.assertEqual(state['handoff_counts']['not_sent'], 1)

    def test_evidence_is_hash_checked_and_paths_are_not_exposed(self):
        sha = self.fixture.metadata['license_evidence'][0]['sha256']
        url = f'{BASE}/candidates/{self.identifier}/evidence/{sha}'
        self.assertEqual(self.client.get(url).content, self.fixture.evidence)
        self.assertEqual(self.client.get(f'{BASE}/candidates/{self.identifier}/evidence/' + 'f' * 64).status_code, 404)
        text = self.client.get(BASE + '/candidates').text + self.client.get(f'{BASE}/candidates/{self.identifier}/history').text
        self.assertNotIn(str(self.fixture.root), text)
        self.assertNotIn('local_path', text)
        path = self.store.directory / 'staging' / 'objects' / sha[:2] / sha
        path.write_bytes(b'changed')
        self.assertEqual(self.client.get(url).status_code, 400)

    def test_reading_abandoned_runs_does_not_recover_or_modify_worker_records(self):
        self.store.save_run('abandoned', {'status': 'running', 'started': '2026-10-12T00:00:00+00:00'})
        state = self.client.get(BASE).json()
        self.assertEqual(state['runs'][0]['observed_status'], 'interrupted')
        self.assertEqual(self.store.status()['runs'][0]['status'], 'running')

    def test_control_api_rejects_foreign_origins_and_unknown_fields(self):
        self.assertEqual(self.client.post(BASE + '/pause', json={'paused': True}, headers={'Origin': 'https://example.com'}).status_code, 403)
        self.assertEqual(self.client.post(BASE + '/pause', json={'paused': 'yes'}).status_code, 400)
        self.assertEqual(self.client.post(BASE + '/pause', json={'paused': False, 'data_dir': '/tmp/arbitrary'}).status_code, 400)
        self.assertEqual(self.client.post(f'{BASE}/candidates/{self.identifier}/actions', json={'action': 'dismiss', 'approval': True}).status_code, 400)

    def test_migration_requires_exclusive_collector_lock(self):
        with self.store.db() as db:
            db.execute('DROP TABLE validations')
            db.execute('PRAGMA user_version=2')
        with collector_lock(self.store.directory / 'collector.lock'):
            with self.assertRaises(AlreadyRunning):
                CollectorControls(self.store.directory)
        controls = CollectorControls(self.store.directory)
        self.assertTrue(list((self.store.directory / 'backups').glob('before-v3-*')))
        self.assertEqual(controls.candidate(self.identifier)['validation_status'], 'passed')

    def test_delivery_progress_is_visible_while_request_is_in_flight(self):
        def receiver(request):
            state = self.client.get(BASE).json()
            self.assertTrue(state['active'])
            self.assertEqual(state['runs'][0]['kind'], 'handoff')
            self.assertEqual(state['runs'][0]['requests'], 1)
            self.assertEqual(state['handoff_counts']['sending'], 1)
            return self.fixture.receiver(request)
        self.assertEqual(handoff(self.store, transport=httpx.MockTransport(receiver))['delivered'], 1)
        run = self.client.get(BASE).json()['runs'][0]
        self.assertEqual(run['observed_status'], 'completed')
        self.assertEqual(run['delivered'], 1)

    def test_discovery_progress_is_checkpointed_before_next_tree_request(self):
        from minebot.collector import run
        from minebot.github import GitHub
        self.store.configure({'queries': ['topic:litematica']})
        repository = {'id': 456, 'full_name': 'test/example', 'private': False, 'default_branch': 'main'}
        def receiver(request):
            path = request.url.path
            if path == '/search/repositories':
                data = {'items': [repository], 'total_count': 1}
            elif path.endswith('/commits/main'):
                data = {'sha': 'a' * 40, 'commit': {'tree': {'sha': 'b' * 40}}}
            elif '/git/trees/' in path:
                current = self.client.get(BASE).json()['runs'][0]
                self.assertEqual(current['requests'], 3)
                self.assertEqual(current['observed_status'], 'running')
                data = {'tree': [], 'truncated': False}
            else:
                data = repository
            return httpx.Response(200, json=data)
        result = run(self.store, lambda config: GitHub(config, token='fixture', transport=httpx.MockTransport(receiver)))
        self.assertEqual(result['requests'], 4)
