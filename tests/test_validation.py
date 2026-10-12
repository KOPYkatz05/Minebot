"""Known-answer fixtures exercise real isolated adapters and queue failure recovery."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import nbtlib as nbt
from litemapy import BlockState, Region, Schematic
from reviewer import comparison as cmp
import completion_data

from minebot.collection import MAX_ASSET
from minebot.lock import AlreadyRunning, collector_lock
from minebot.store import Store
from minebot.validation import (MAX_REPORT_BYTES, child_environment, run_worker,
                                validate, validator_fingerprint)
from minebot.validation_worker import validate_file


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state')
        self.source = self.root / 'generated.litematic'
        completion_data.synthetic_house(self.source)
        self.original = self.source.read_bytes()
        self.identifier = self.stage(self.original)

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, content, path='house.litematic'):
        digest = hashlib.sha256(content).hexdigest()
        blob = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
        destination = self.store.directory / 'staging' / 'objects' / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        artifact = {'sha256': digest, 'blob_sha': blob, 'size': len(content),
                    'local_path': destination.relative_to(self.store.directory).as_posix(), 'is_lfs_pointer': False}
        self.store.artifact(artifact)
        return self.store.candidate({'repository_id': 42, 'repository': 'example/builds', 'revision': 'a' * 40,
            'blob_sha': blob, 'path': path, 'size': len(content), 'status': 'downloaded', 'artifact': artifact,
            'validation_status': 'not_validated', 'usage_permission': 'unknown', 'asset_author': None,
            'suggestions': [{'field': 'building_type', 'value': 'house', 'origin': 'filename_house_v1'}]})[0]

    def candidate(self, identifier=None):
        return next(c for c in self.store.candidates() if c['id'] == (identifier or self.identifier))

    def stored_source(self, identifier=None):
        return self.store.directory / self.candidate(identifier)['artifact']['local_path']

    def test_real_isolated_roundtrip_retains_original_and_manual_review_gates(self):
        report = validate(self.store)
        self.assertEqual(report['passed'], 1)
        self.store = Store(self.store.directory)
        candidate = self.candidate()
        self.assertEqual(candidate['status'], 'downloaded')
        self.assertEqual(candidate['validation_status'], 'passed')
        self.assertEqual(candidate['usage_permission'], 'unknown')
        self.assertEqual(candidate['visual_approval'], 'pending')
        self.assertEqual(candidate['training_eligibility'], 'human_review_required')
        self.assertEqual(self.stored_source().read_bytes(), self.original)
        record = candidate['validation']
        self.assertEqual(record['report']['independent_source_reader']['status'], 'passed')
        self.assertEqual(record['report']['independent_converted_reader']['status'], 'passed')
        self.assertTrue(record['report']['archive_comparison']['passed'])
        self.assertNotIn('original', record['report']['comparison'])
        self.assertNotIn('markers', record['report']['comparison'])
        self.assertEqual(validate(self.store)['processed'], 0)
        self.assertEqual(len(self.store.validation_history(candidate['artifact']['sha256'])), 1)
        self.assertFalse((self.store.directory / 'reviewer.sqlite3').exists())

    def test_duplicate_references_reuse_verified_conversion(self):
        other = self.stage(self.original, path='another-house.litematic')
        report = validate(self.store)
        self.assertEqual(report['passed'], 2)
        self.assertEqual(report['reused'], 1)
        self.assertEqual(self.candidate()['validation']['converted'], self.candidate(other)['validation']['converted'])
        self.assertNotEqual(self.candidate()['path'], self.candidate(other)['path'])

    def test_converter_change_invalidates_validation_and_retains_prior_report(self):
        validate(self.store)
        previous = self.candidate()['validation']['report_path']
        with patch('minebot.validation.validator_fingerprint', return_value='changed-validator'):
            self.assertEqual(validate(self.store)['passed'], 1)
        self.assertEqual(self.candidate()['validation']['validator_fingerprint'], 'changed-validator')
        self.assertTrue((self.store.directory / previous).exists())
        self.assertEqual(len(self.store.validation_history(self.candidate()['artifact']['sha256'])), 2)

    def test_real_malformed_schematic_keeps_failed_report_and_original(self):
        self.store.action(self.identifier, 'dismiss')
        broken = self.stage(b'not a schematic', path='broken.litematic')
        report = validate(self.store)
        self.assertEqual(report['failed'], 1)
        candidate = self.candidate(broken)
        self.assertEqual(candidate['status'], 'downloaded')
        self.assertEqual(candidate['validation_status'], 'failed')
        self.assertTrue((self.store.directory / candidate['validation']['report_path']).exists())
        self.assertEqual(self.stored_source(broken).read_bytes(), b'not a schematic')
        self.assertEqual(validate(self.store)['processed'], 0)
        self.store.action(broken, 'retry-validation')
        self.assertEqual(validate(self.store)['failed'], 1)
        self.assertEqual(len(self.store.validation_history(candidate['artifact']['sha256'])), 2)

    def test_broken_converter_is_exposed_by_exact_and_independent_comparisons(self):
        output = self.root / 'broken-conversion'
        output.mkdir()
        original_writer = cmp.pipeline.write_build
        def broken_writer(build, target):
            changed = deepcopy(build)
            changed['regions'][0]['indices_x_fastest'][0] = 1
            original_writer(changed, target)
        with patch.object(cmp.pipeline, 'write_build', side_effect=broken_writer):
            report = validate_file(self.source, output, hashlib.sha256(self.original).hexdigest())
        self.assertFalse(report['passed'])
        self.assertFalse(report['comparison']['passed'])
        self.assertFalse(report['archive_comparison']['passed'])

    def test_signed_regions_block_states_and_typed_auxiliary_metadata_survive(self):
        negative = Region(11, 9, -7, -3, -2, -4)
        negative[-2, -1, -3] = BlockState('minecraft:oak_stairs', facing='east', half='top', shape='straight', waterlogged='false')
        positive = Region(-20, 0, 30, 2, 2, 2)
        positive[1, 1, 1] = BlockState('minecraft:repeater', delay='4', facing='north', locked='false', powered='true')
        source = self.root / 'signed.litematic'
        Schematic(regions={'negative': negative, 'positive': positive}).save(str(source))
        tree = nbt.load(source)
        tree['Opaque'] = nbt.IntArray([1, -2, 3])
        tree['Regions']['negative']['PendingBlockTicks'] = nbt.List[nbt.Compound]([
            nbt.Compound({'Block': nbt.String('minecraft:stone'), 'Time': nbt.Long(123)})])
        tree.save(source, gzipped=True)
        self.store.action(self.identifier, 'dismiss')
        identifier = self.stage(source.read_bytes(), path='signed.litematic')
        self.assertEqual(validate(self.store)['passed'], 1)
        candidate = self.candidate(identifier)
        report = candidate['validation']['report']
        self.assertTrue(report['archive_comparison']['passed'])
        self.assertEqual(report['inspection']['regions'][0]['signed_size_xyz'], [-3, -2, -4])
        self.assertEqual(report['independent_source_reader']['voxel_positions_compared'], 32)
        self.assertEqual(report['redstone']['matched_block_count'], 1)
        self.assertEqual(candidate['suggestions'][-1]['origin'], 'measured_redstone_v1')
        self.assertFalse(candidate['suggestions'][-1]['confirmed'])
        self.assertNotIn('redstone', candidate)  # no confirmed human classification was created
        converted = self.store.directory / candidate['validation']['converted']['local_path']
        self.assertEqual(cmp.load(source)['root_nbt'], cmp.load(converted)['root_nbt'])

    def test_missing_independent_reader_never_passes_validation(self):
        output = self.root / 'unavailable-reader'
        output.mkdir()
        with patch.object(cmp.pipeline, 'cross_check', return_value={'status': 'unavailable'}):
            report = validate_file(self.source, output, hashlib.sha256(self.original).hexdigest())
        self.assertFalse(report['passed'])
        self.assertEqual(report['independent_source_reader']['status'], 'unavailable')

    def test_compressed_and_decoded_limit_failures_remain_explicit(self):
        for name, limit in (('MAX_FILE_BYTES', 1), ('MAX_NBT_BYTES', 1)):
            with self.subTest(name=name):
                output = self.root / name
                output.mkdir()
                with patch.object(cmp, name, limit):
                    report = validate_file(self.source, output, hashlib.sha256(self.original).hexdigest())
                self.assertFalse(report['passed'])
                self.assertEqual(report['stage'], 'bounded_load')

    def test_original_corruption_prevents_worker_launch(self):
        self.stored_source().write_bytes(b'x' * len(self.original))
        with patch('minebot.validation.run_worker') as worker:
            report = validate(self.store, worker)
        self.assertEqual(report['failed'], 1)
        worker.assert_not_called()

    def test_actual_subprocess_timeout_preserves_original_and_report(self):
        def timeout_worker(source, output, digest, **kwargs):
            return run_worker(source, output, digest, timeout=0.000001)
        self.assertEqual(validate(self.store, timeout_worker)['failed'], 1)
        candidate = self.candidate()
        self.assertEqual(candidate['validation']['error']['kind'], 'TimeoutError')
        self.assertEqual(self.stored_source().read_bytes(), self.original)
        self.assertTrue((self.store.directory / candidate['validation']['report_path']).exists())

    def test_interrupt_recovery_records_attempt_and_resumes_in_new_directory(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            validate(self.store, interrupted)
        self.assertEqual(self.candidate()['validation_status'], 'interrupted')
        sha = self.candidate()['artifact']['sha256']
        self.assertEqual(self.store.validation_history(sha)[0]['status'], 'interrupted')
        self.assertEqual(validate(Store(self.store.directory))['passed'], 1)
        self.assertEqual(len(self.store.validation_history(sha)), 2)

    def test_pause_before_and_during_worker_resumes_without_failure(self):
        self.store.configure({'paused': True})
        self.assertEqual(validate(self.store)['status'], 'paused')
        self.store.configure({'paused': False})
        def paused(source, output, digest, **kwargs):
            self.store.configure({'paused': True})
            return run_worker(source, output, digest, **kwargs)
        self.assertEqual(validate(self.store, paused)['status'], 'paused')
        self.assertEqual(self.candidate()['validation_status'], 'interrupted')
        self.store.configure({'paused': False})
        self.assertEqual(validate(self.store)['passed'], 1)

    def test_dismissed_candidates_stay_dismissed_if_decision_changes_during_validation(self):
        def dismiss(source, output, digest, **kwargs):
            self.store.action(self.identifier, 'dismiss')
            return run_worker(source, output, digest, **kwargs)
        self.assertEqual(validate(self.store, dismiss)['passed'], 1)
        self.assertEqual(self.candidate()['status'], 'dismissed')
        self.store.action(self.identifier, 'restore')
        self.assertEqual(self.candidate()['status'], 'downloaded')
        self.assertEqual(self.candidate()['validation_status'], 'passed')

    def test_storage_reservation_defers_without_writing_outputs(self):
        self.store.configure({'max_storage_bytes': len(self.original) + MAX_ASSET + MAX_REPORT_BYTES - 1})
        self.assertEqual(validate(self.store)['status'], 'deferred')
        self.assertEqual(self.candidate()['validation_status'], 'not_validated')
        self.assertFalse((self.store.directory / 'staging' / 'validation').exists())
        self.store.configure({'max_storage_bytes': 1024**3})
        self.assertEqual(validate(self.store)['passed'], 1)

    def test_file_budget_retains_remaining_work(self):
        self.stage(self.original, path='other-house.litematic')
        self.store.configure({'max_new_files': 1})
        report = validate(self.store)
        self.assertEqual(report['status'], 'deferred')
        self.assertEqual(report['passed'], 1)
        self.assertEqual(validate(self.store)['passed'], 1)

    def test_cached_report_corruption_is_detected(self):
        validate(self.store)
        record = self.candidate()['validation']
        (self.store.directory / record['report_path']).write_text('{}', encoding='utf-8')
        other = self.stage(self.original, path='other-house.litematic')
        self.assertEqual(validate(self.store)['failed'], 1)
        self.assertEqual(self.candidate(other)['validation_status'], 'failed')

    def test_cached_converted_file_corruption_is_detected(self):
        validate(self.store)
        converted = self.store.directory / self.candidate()['validation']['converted']['local_path']
        converted.write_bytes(b'x' * converted.stat().st_size)
        other = self.stage(self.original, path='other-house.litematic')
        self.assertEqual(validate(self.store)['failed'], 1)
        self.assertEqual(self.candidate(other)['validation_status'], 'failed')

    def test_total_voxel_limit_applies_across_separate_regions(self):
        source = self.root / 'multi-region.litematic'
        Schematic(regions={'first': Region(0, 0, 0, 3, 1, 1),
                           'second': Region(10, 0, 0, 3, 1, 1)}).save(str(source))
        output = self.root / 'volume-limit'
        output.mkdir()
        with patch.object(cmp.pipeline, 'MAX_VOLUME', 5):
            report = validate_file(source, output, cmp.digest(source))
        self.assertFalse(report['passed'])
        self.assertEqual(report['stage'], 'bounded_load')

    def test_child_never_inherits_credentials_or_python_hooks(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'TEST_SECRET', 'AWS_SECRET_ACCESS_KEY': 'TEST_SECRET',
                                     'HTTPS_PROXY': 'sensitive-proxy', 'PYTHONPATH': 'untrusted', 'PYTHONSTARTUP': 'untrusted'}):
            env = child_environment()
        for name in ('GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'HTTPS_PROXY', 'PYTHONPATH', 'PYTHONSTARTUP'):
            self.assertNotIn(name, env)
        self.assertNotIn('TEST_SECRET', json.dumps(env))

    def test_shared_lock_prevents_another_validator(self):
        with collector_lock(self.store.directory / 'collector.lock'):
            with self.assertRaises(AlreadyRunning):
                validate(self.store)

    def test_schema_v2_backup_preserves_downloaded_queue(self):
        with self.store.db() as db:
            db.executescript('DROP TABLE validations; PRAGMA user_version=2;')
        self.store = Store(self.store.directory)
        self.assertEqual(len(list((self.store.directory / 'backups').glob('before-v3-*'))), 1)
        self.assertEqual(validate(self.store)['passed'], 1)

    def test_unsupported_and_lfs_queue_entries_are_never_parsed(self):
        self.store.update_candidate(self.identifier, {}, 'lfs_pointer')
        with patch('minebot.validation.run_worker') as worker:
            self.assertEqual(validate(self.store, worker)['processed'], 0)
        worker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
