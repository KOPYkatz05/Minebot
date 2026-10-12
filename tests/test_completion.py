"""Verify vocabulary, mask separation, honest baselines, and edited exports."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

import nbtlib as nbt
import numpy as np
from litemapy import BlockState, Region

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import completion_data as data
import litematic_pipeline as pipeline


class CompletionDataTests(unittest.TestCase):
    def fixture(self, directory):
        path = Path(directory) / "fixture.litematic"
        data.synthetic_house(path)
        build = pipeline.read_build(path)
        vocab = data.vocabulary([build])
        target = data.array_for(build["regions"][0], vocab)
        return path, build, vocab, target

    def test_global_vocabulary_ignores_per_file_palette_order(self):
        with tempfile.TemporaryDirectory() as directory:
            _, first, _, _ = self.fixture(directory)
            second = deepcopy(first)
            record = second["regions"][0]
            region = pipeline.decode_tag(record["region_nbt"])
            count = len(region["BlockStatePalette"])
            region["BlockStatePalette"] = nbt.List[nbt.Compound](list(reversed(region["BlockStatePalette"])))
            record["region_nbt"] = pipeline.encode_tag(region)
            record["indices_x_fastest"] = [count - 1 - index for index in record["indices_x_fastest"]]
            vocab = data.vocabulary([first, second])
            self.assertEqual(vocab, data.vocabulary([second, first]))
            self.assertTrue(np.array_equal(data.array_for(first["regions"][0], vocab),
                                           data.array_for(second["regions"][0], vocab)))

    def test_masks_are_repeatable_distinct_and_hide_non_air(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, vocab, target = self.fixture(directory)
            masks = data.masks_for(target, 28, 42, vocab)
            self.assertTrue(np.array_equal(masks, data.masks_for(target, 28, 42, vocab)))
            self.assertEqual(len({mask.tobytes() for mask in masks}), 28)
            self.assertTrue(all(np.sum(~np.isin(target[mask], data.air_ids(vocab))) >= 8 for mask in masks))
            self.assertGreater(vocab["mask_input_id"], int(target.max()))

    def test_neighbor_baseline_never_uses_hidden_targets(self):
        target = np.arange(125, dtype=np.int32).reshape(5, 5, 5)
        mask = np.zeros_like(target, dtype=bool)
        mask[1:4, 1:4, 1:4] = True
        changed = target.copy()
        changed[mask] = 999
        self.assertTrue(np.array_equal(data.nearest_visible(target, mask), data.nearest_visible(changed, mask)))

    def test_air_only_prediction_has_zero_occupancy_recall(self):
        vocab = {"states": [{"name": "minecraft:air"}, {"name": "minecraft:stone"}]}
        target = np.array([[[0, 1, 1, 1]]])
        scores = data.metrics(target, np.zeros_like(target), np.ones_like(target, dtype=bool), vocab)
        self.assertEqual(scores["block_state_accuracy"], 0.25)
        self.assertEqual(scores["occupancy_recall"], 0.0)
        self.assertIsNone(scores["occupancy_precision"])

    def test_missing_section_export_preserves_visible_blocks_and_updates_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            source, build, vocab, target = self.fixture(directory)
            source_hash = build["source_sha256"]
            mask = data.masks_for(target, 1, 42, vocab)[0]
            missing = target.copy()
            missing[mask] = vocab["air_id"]
            output = Path(directory) / "missing.litematic"
            report = data.export_checked(build, missing, vocab, output, "Missing section")
            parsed = pipeline.read_build(output)
            array = data.array_for(parsed["regions"][0], vocab)
            self.assertTrue(np.array_equal(array[~mask], target[~mask]))
            self.assertTrue(np.all(array[mask] == vocab["air_id"]))
            root = pipeline.decode_tag(parsed["root_nbt"])
            self.assertEqual(int(root["Metadata"]["TotalBlocks"]), report["non_air_blocks"])
            self.assertEqual(pipeline.read_build(source)["source_sha256"], source_hash)

    def test_edited_export_rejects_populated_block_entities_and_mask_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            _, build, vocab, target = self.fixture(directory)
            bad = target.copy()
            bad[0, 0, 0] = vocab["mask_input_id"]
            with self.assertRaises(ValueError):
                data.edited_build(build, bad, vocab, "bad")
            record = build["regions"][0]
            region = pipeline.decode_tag(record["region_nbt"])
            region["TileEntities"] = nbt.List[nbt.Compound]([nbt.Compound({"id": nbt.String("minecraft:chest")})])
            record["region_nbt"] = pipeline.encode_tag(region)
            with self.assertRaises(ValueError):
                data.edited_build(build, target, vocab, "bad")


if __name__ == "__main__":
    unittest.main()
