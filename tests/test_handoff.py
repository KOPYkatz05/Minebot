"""Local API delivery, restart recovery and preservation of human review state."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import completion_data
from minebot.handoff import handoff, public_metadata, reviewer_url
from minebot.store import Store
from minebot.validation import validate
from reviewer.app import create_app


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'collector')
        source = self.root / 'owned.litematic'
        completion_data.synthetic_house(source)
        self.content = source.read_bytes()
        sha = hashlib.sha256(self.content).hexdigest()
        blob = hashlib.sha1(f'blob {len(self.content)}\0'.encode() + self.content).hexdigest()
        original = self.store.directory / 'staging' / 'objects' / sha[:2] / sha
        original.parent.mkdir(parents=True)
        original.write_bytes(self.content)
        evidence = b'Test evidence: usage permission remains unknown.'
        evidence_sha = hashlib.sha256(evidence).hexdigest()
        evidence_blob = hashlib.sha1(f'blob {len(evidence)}\0'.encode() + evidence).hexdigest()
        evidence_path = self.store.directory / 'staging' / 'objects' / evidence_sha[:2] / evidence_sha
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(evidence)
        candidate = {'repository_id': 123, 'repository': 'test/owned', 'revision': 'a' * 40,
                     'path': 'house.litematic', 'blob_sha': blob, 'status': 'downloaded',
                     'artifact': {'sha256': sha, 'size': len(self.content)}, 'validation_status': 'not_validated',
                     'evidence_status': 'captured', 'license_evidence': [
                         {'path': 'LICENSE', 'size': len(evidence), 'sha256': evidence_sha, 'blob_sha': evidence_blob}]}
        self.identifier, _ = self.store.candidate(candidate)
        report = validate(self.store)
        self.assertEqual(report['passed'], 1)
        self.candidate = self.store.candidates()[0]
        self.metadata = public_metadata(self.candidate)
        self.evidence = evidence
        self.app = create_app(self.root / 'reviewer', worker=False, testing=True)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.calls = []

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def receiver(self, request):
        self.calls.append(request)
        self.assertNotIn('authorization', request.headers)
        response = self.client.post(request.url.path, content=request.read(), headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content)

    def post(self, metadata=None, evidence=None):
        return self.client.post('/api/discovery/imports', files=[
            ('metadata', ('metadata.json', json.dumps(metadata or self.metadata).encode(), 'application/json')),
            ('file', ('house.litematic', self.content)),
            ('evidence', ('LICENSE', self.evidence if evidence is None else evidence))])

    def test_delivery_preserves_unknown_usage_and_evidence(self):
        report = handoff(self.store, transport=httpx.MockTransport(self.receiver))
        self.assertEqual(report['delivered'], 1)
        receipt = self.store.candidates()[0]['handoff']
        self.assertEqual(receipt['status'], 'delivered')
        build = self.app.state.service.get_build(receipt['build_id'])
        self.assertEqual(build['review_status'], 'pending')
        self.assertEqual(build['provenance'], 'unknown')
        self.assertEqual(build['type'], 'unclassified')
        self.assertFalse(build['eligible'])
        self.assertEqual(self.client.get('/api/discovery').json()[0]['source']['candidate_id'], self.identifier)
        sha = self.metadata['license_evidence'][0]['sha256']
        self.assertEqual(self.client.get(f"/api/discovery/{build['id']}/evidence/{sha}").content, self.evidence)
        restarted = Store(self.store.directory)
        self.assertEqual(handoff(restarted, transport=httpx.MockTransport(self.receiver))['requests'], 0)

    def test_lost_receipt_retry_preserves_human_edits_and_membership(self):
        def lost(request):
            self.receiver(request)
            raise httpx.ReadError('simulated lost response')
        report = handoff(self.store, transport=httpx.MockTransport(lost))
        self.assertEqual(report['status'], 'deferred')
        service = self.app.state.service
        build = service.builds()[0]
        service.process(service.jobs()[0]['id'])
        service.update_build(build['id'], {'type': 'house', 'notes': 'Human note', 'source_url': 'Human source'})
        service.review(build['id'], 'approved')
        dataset = service.update_dataset({'name': 'Keep membership'})
        service.membership(dataset['id'], [build['id']], True)
        self.store.action(self.identifier, 'retry-handoff')
        self.assertEqual(handoff(Store(self.store.directory), transport=httpx.MockTransport(self.receiver))['delivered'], 1)
        build = service.get_build(build['id'])
        self.assertEqual(build['review_status'], 'approved')
        self.assertEqual(build['notes'], 'Human note')
        self.assertEqual(build['source_url'], 'Human source')
        self.assertEqual(len(build['source_references']), 1)
        self.assertEqual(service.get_dataset(dataset['id'])['build_ids'], [build['id']])
        self.assertFalse(service.preview(dataset['id'])['ready'])

    def test_invalid_manifest_or_evidence_never_imports(self):
        for changes in ({'private': True}, {'usage_permission': 'allowed'}, {'source_sha256': 'b' * 64},
                        {'candidate_id': 'c' * 64}, {'path': '../escape.litematic'}, {'provenance': 'allowed'}):
            self.assertEqual(self.post(self.metadata | changes).status_code, 400)
        self.assertEqual(self.post(evidence=b'changed').status_code, 400)
        self.assertEqual(self.app.state.service.builds(), [])
        self.assertEqual(self.client.get('/api/discovery/missing/evidence/' + 'f' * 64).status_code, 404)

    def test_pause_dismiss_and_changed_artifact_prevent_delivery(self):
        self.store.configure({'paused': True})
        self.assertEqual(handoff(self.store, transport=httpx.MockTransport(self.receiver))['status'], 'paused')
        self.store.configure({'paused': False})
        self.store.action(self.identifier, 'dismiss')
        self.assertEqual(handoff(self.store, transport=httpx.MockTransport(self.receiver))['requests'], 0)
        self.store.action(self.identifier, 'restore')
        sha = self.candidate['artifact']['sha256']
        (self.store.directory / 'staging' / 'objects' / sha[:2] / sha).write_bytes(b'changed')
        self.assertEqual(handoff(self.store, transport=httpx.MockTransport(self.receiver))['failed'], 1)
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(handoff(self.store, transport=httpx.MockTransport(self.receiver))['requests'], 0)

    def test_loopback_only_and_origin_restrictions(self):
        for value in ('https://example.com', 'http://user:secret@localhost', 'http://localhost/path', 'http://127.0.0.1?x=1'):
            with self.assertRaises(ValueError):
                reviewer_url(value)
        response = self.client.post('/api/discovery/imports', headers={'Origin': 'https://example.com'})
        self.assertEqual(response.status_code, 403)
