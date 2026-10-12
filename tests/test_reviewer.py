"""Review persistence, independent conversion checks, snapshots, and local API."""

from copy import deepcopy
import hashlib
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reviewer.service import Service
from reviewer import comparison as cmp
from reviewer.app import create_app
from fastapi.testclient import TestClient
import completion_data
import nbtlib as nbt
from litemapy import Region, Schematic, BlockState


class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'generated.litematic'
        completion_data.synthetic_house(self.source)
        self.content = self.source.read_bytes()
        self.service = Service(self.root / 'state', start_worker=False)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def imported(self):
        result = self.service.import_bytes('generated.litematic', self.content)
        if 'job_id' in result:
            self.service.process(result['job_id'])
        return self.service.get_build(result['build_id'])

    def approved(self):
        build = self.imported()
        self.service.add_label('styles', 'test')
        self.service.update_build(build['id'], {'type': 'house', 'styles': ['test', 'medieval'],
            'redstone': 'no', 'provenance': 'project_generated', 'evidence': 'Local test generator',
            'family_confirmed': True})
        return self.service.review(build['id'], 'approved')

    def test_duplicate_import_retains_references_and_one_identity(self):
        first = self.imported()
        duplicate = self.service.import_bytes('another-name.litematic', self.content, 'Additional source')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['id'], duplicate['build_id'])
        self.assertEqual(len(self.service.builds()), 1)
        self.assertEqual(len(self.service.get_build(first['id'])['source_references']), 2)

    def test_shared_labels_are_case_insensitive_unique_and_required_for_classification(self):
        self.assertEqual(self.service.add_label('styles', '  Art Deco  ')['label'], 'Art Deco')
        self.assertEqual(self.service.add_label('styles', 'art deco')['label'], 'Art Deco')
        build = self.imported()
        with self.assertRaisesRegex(ValueError, 'Add these styles labels'):
            self.service.update_build(build['id'], {'styles': ['art-deco']})
        updated = self.service.update_build(build['id'], {'styles': ['art deco']})
        self.assertEqual(updated['styles'], ['Art Deco'])
        self.service.close()
        self.service = Service(self.root / 'state', start_worker=False)
        self.assertIn('Art Deco', self.service.label_options()['styles'])

    def test_label_migration_seeds_legacy_values_and_collapses_case_variants(self):
        build = self.imported()
        with self.service.db() as db:
            document = json.loads(db.execute('SELECT document FROM builds WHERE id=?', (build['id'],)).fetchone()[0])
            document['styles'] = ['Legacy style', 'legacy STYLE']
            db.execute('UPDATE builds SET document=? WHERE id=?', (json.dumps(document), build['id']))
        self.service.close()
        with self.service.db() as db:
            db.execute('DROP TABLE labels')
            db.execute('PRAGMA user_version=1')
        self.service = Service(self.root / 'state', start_worker=False)
        self.assertIn('Legacy style', self.service.label_options()['styles'])
        self.assertEqual(self.service.get_build(build['id'])['styles'], ['Legacy style'])

    def test_palette_reordering_does_not_create_false_differences(self):
        original = cmp.load(self.source)
        changed = deepcopy(original)
        record = changed['regions'][0]
        region = cmp.pipeline.decode_tag(record['region_nbt'])
        size = len(region['BlockStatePalette'])
        region['BlockStatePalette'] = nbt.List[nbt.Compound](list(reversed(region['BlockStatePalette'])))
        record['region_nbt'] = cmp.pipeline.encode_tag(region)
        record['indices_x_fastest'] = [size - 1 - i for i in record['indices_x_fastest']]
        self.assertTrue(cmp.compare(original, changed)['passed'])

    def test_missing_blocks_properties_and_geometry_are_detected(self):
        original = cmp.load(self.source)
        missing = deepcopy(original)
        record = missing['regions'][0]
        palette = cmp.pipeline.decode_tag(record['region_nbt'])['BlockStatePalette']
        air = next(i for i, s in enumerate(palette) if str(s['Name']) == 'minecraft:air')
        index = next(i for i, s in enumerate(record['indices_x_fastest']) if s != air)
        record['indices_x_fastest'][index] = air
        self.assertEqual(cmp.compare(original, missing)['counts']['missing'], 1)
        property_change = deepcopy(original)
        region = cmp.pipeline.decode_tag(property_change['regions'][0]['region_nbt'])
        log = next(state for state in region['BlockStatePalette'] if str(state['Name']) == 'minecraft:oak_log')
        log['Properties']['axis'] = nbt.String('x')
        property_change['regions'][0]['region_nbt'] = cmp.pipeline.encode_tag(region)
        self.assertGreater(cmp.compare(original, property_change)['counts']['changed'], 0)
        geometry = deepcopy(original)
        region = cmp.pipeline.decode_tag(geometry['regions'][0]['region_nbt'])
        region['Position']['x'] = nbt.Int(50)
        geometry['regions'][0]['region_nbt'] = cmp.pipeline.encode_tag(region)
        self.assertTrue(cmp.compare(original, geometry)['geometry_errors'])

    def test_decisions_and_membership_survive_restart_and_remain_independent(self):
        build = self.approved()
        first = self.service.update_dataset({'name': 'Medieval'})
        second = self.service.update_dataset({'name': 'Test'})
        self.service.membership(first['id'], [build['id']], True)
        self.service.membership(second['id'], [build['id']], True)
        self.service.membership(second['id'], [build['id']], False)
        self.service.close()
        self.service = Service(self.root / 'state', start_worker=False)
        self.assertEqual(self.service.get_build(build['id'])['review_status'], 'approved')
        self.assertEqual(self.service.get_build(build['id'])['styles'], ['test', 'medieval'])
        self.assertEqual(self.service.get_dataset(first['id'])['build_ids'], [build['id']])
        self.assertEqual(self.service.get_dataset(second['id'])['build_ids'], [])

    def test_unknown_usage_stale_approval_and_redstone_block_exports(self):
        build = self.approved()
        dataset = self.service.update_dataset({'name': 'Training'})
        self.service.membership(dataset['id'], [build['id']], True)
        self.service.assign_split(dataset['id'], build['family'], 'train')
        self.assertTrue(self.service.preview(dataset['id'])['ready'])
        self.service.update_build(build['id'], {'provenance': 'unknown'})
        self.assertFalse(self.service.preview(dataset['id'])['ready'])
        self.service.update_build(build['id'], {'provenance': 'project_generated', 'redstone': 'yes'})
        self.assertFalse(self.service.preview(dataset['id'])['ready'])
        self.service.update_dataset({'exclude_redstone': False}, dataset['id'])
        self.assertTrue(self.service.preview(dataset['id'])['ready'])
        with mock.patch('reviewer.comparison.fingerprint', return_value='changed'):
            self.assertEqual(self.service.get_build(build['id'])['review_status'], 'pending')
            self.assertFalse(self.service.preview(dataset['id'])['ready'])
            with self.assertRaises(ValueError):
                self.service.review(build['id'], 'approved')

    def test_snapshot_is_fixed_after_classification_membership_and_archive_changes(self):
        build = self.approved()
        dataset = self.service.update_dataset({'name': 'Snapshot'})
        self.service.membership(dataset['id'], [build['id']], True)
        self.assertFalse(self.service.preview(dataset['id'])['ready'])
        self.service.assign_split(dataset['id'], build['family'], 'train')
        exported = self.service.export(dataset['id'])
        original = self.service.version(dataset['id'], exported['id'])
        self.service.update_build(build['id'], {'styles': ['modern'], 'archived': True})
        self.service.membership(dataset['id'], [build['id']], False)
        self.assertEqual(original, self.service.version(dataset['id'], exported['id']))
        self.assertEqual(original['builds'][0]['styles'], ['test', 'medieval'])
        folder = self.service.directory / 'snapshots' / exported['id']
        source_artifact = folder / original['builds'][0]['source_artifact']
        self.assertEqual(hashlib.sha256(source_artifact.read_bytes()).hexdigest(), build['source_hash'])
        self.service.update_build(build['id'], {'archived': False})
        self.assertEqual(len(self.service.get_build(build['id'])['reviews']), 1)

    def test_failed_import_does_not_stop_next_job(self):
        bad = self.service.import_bytes('corrupt.litematic', b'bad data')
        good = self.service.import_bytes('good.litematic', self.content)
        self.service.process(bad['job_id']); self.service.process(good['job_id'])
        statuses = {j['id']: j['status'] for j in self.service.jobs()}
        self.assertEqual(statuses[bad['job_id']], 'failed')
        self.assertEqual(statuses[good['job_id']], 'completed')
        self.service.retry(bad['job_id'])
        self.assertEqual(next(j for j in self.service.jobs() if j['id'] == bad['job_id'])['status'], 'queued')

    def test_changed_conversion_and_family_assignment_invalidate_eligibility(self):
        build = self.approved()
        output = self.service.directory / 'conversions' / f"{build['conversion']['id']}.litematic"
        output.write_bytes(b'changed')
        self.assertTrue(self.service.get_build(build['id'])['stale'])
        self.assertFalse(self.service.get_build(build['id'])['eligible'])

    def test_api_rejects_foreign_origins_and_serves_opaque_identifiers(self):
        with TestClient(create_app(self.root / 'api', worker=False, testing=True)) as client:
            response = client.post('/api/datasets', json={'name': 'Blocked'}, headers={'Origin': 'https://example.com'})
            self.assertEqual(response.status_code, 403)
            response = client.post('/api/imports', files={'files': ('generated.litematic', self.content)})
            self.assertEqual(response.status_code, 200)
            imported = response.json()[0]
            client.app.state.service.process(imported['job_id'])
            self.assertEqual(client.get(f"/api/builds/{imported['build_id']}/files/original").content, self.content)
            self.assertEqual(client.get('/api/builds/not-a-path/files/original').status_code, 404)
            self.assertEqual(client.get('/api/health', headers={'Host': 'evil.example'}).status_code, 403)

    def test_negative_regions_remain_separate_and_keep_world_coordinates(self):
        source = self.root / 'regions.litematic'
        first = Region(10, 20, 30, -3, -2, -4)
        first[-2, -1, -3] = BlockState('minecraft:stone')
        second = Region(50, 0, 0, 2, 2, 2)
        second[0, 0, 0] = BlockState('minecraft:glass')
        Schematic(name='Regions', regions={'negative': first, 'separate': second}).save(str(source))
        imported = self.service.import_bytes(source.name, source.read_bytes())
        self.service.process(imported['job_id'])
        data = self.service.scenes(imported['build_id'])
        self.assertTrue(data['passed'])
        self.assertEqual(len(data['original']['regions']), 2)
        region = data['original']['regions'][0]
        self.assertEqual(region['origin'], [8, 19, 27])
        self.assertEqual(cmp.position(region, 0), [8, 19, 27])

    def test_related_builds_cannot_receive_conflicting_splits(self):
        first = self.approved()
        altered = nbt.load(self.source)
        altered['Metadata']['Description'] = nbt.String('Variant')
        variant = self.root / 'variant.litematic'
        altered.save(variant, gzipped=True)
        result = self.service.import_bytes(variant.name, variant.read_bytes())
        self.service.process(result['job_id'])
        self.service.update_build(result['build_id'], {'family': first['family'], 'family_confirmed': True})
        dataset = self.service.update_dataset({'name': 'Families'})
        self.service.membership(dataset['id'], [first['id'], result['build_id']], True)
        self.service.assign_split(dataset['id'], first['family'], 'train')
        self.assertEqual({e['split'] for e in self.service.preview(dataset['id'])['entries']}, {'train'})
        self.service.assign_split(dataset['id'], first['family'], 'test')
        self.assertEqual({e['split'] for e in self.service.preview(dataset['id'])['entries']}, {'test'})

    def test_interrupted_processing_can_be_retried_after_restart(self):
        imported = self.service.import_bytes('interrupted.litematic', self.content)
        with self.service.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (imported['job_id'],))
        self.service.close()
        self.service = Service(self.root / 'state', start_worker=False)
        self.assertEqual(self.service.jobs()[0]['status'], 'interrupted')
        self.service.retry(imported['job_id'])
        self.service.process(imported['job_id'])
        self.assertEqual(self.service.jobs()[0]['status'], 'completed')

    def test_public_api_exports_verified_zip_without_rewriting_versions(self):
        with TestClient(create_app(self.root / 'zip-api', worker=False, testing=True)) as client:
            imported = client.post('/api/imports', files={'files': ('demo.litematic', self.content)}).json()[0]
            client.app.state.service.process(imported['job_id'])
            build_id = imported['build_id']
            client.patch(f'/api/builds/{build_id}', json={'type': 'house', 'redstone': 'no',
                'family_confirmed': True, 'provenance': 'project_generated', 'evidence': 'Test-owned generator'})
            client.post(f'/api/builds/{build_id}/review', json={'decision': 'approved'})
            dataset = client.post('/api/datasets', json={'name': 'API snapshot'}).json()
            dataset_id = dataset['id']
            client.post(f'/api/datasets/{dataset_id}/membership', json={'build_ids': [build_id], 'include': True})
            family = client.get(f'/api/builds/{build_id}').json()['family']
            client.post(f'/api/datasets/{dataset_id}/splits', json={'family': family, 'split': 'train'})
            self.assertTrue(client.get(f'/api/datasets/{dataset_id}/preview').json()['ready'])
            version = client.post(f'/api/datasets/{dataset_id}/versions').json()
            response = client.get(f"/api/datasets/{dataset_id}/versions/{version['id']}/download")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-type'], 'application/zip')
            with zipfile.ZipFile(io.BytesIO(response.content)) as package:
                manifest = json.loads(package.read('manifest.json'))
                self.assertEqual(package.read(manifest['builds'][0]['source_artifact']), self.content)
            client.patch(f'/api/builds/{build_id}', json={'notes': 'Changed after export'})
            self.assertEqual(client.get(f"/api/datasets/{dataset_id}/versions/{version['id']}").json(), manifest)


if __name__ == '__main__':
    unittest.main()
