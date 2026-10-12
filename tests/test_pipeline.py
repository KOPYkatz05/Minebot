"""Check packing boundaries, signed coordinates, and preservation of opaque NBT."""

import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

import nbtlib as nbt
from litemapy import BlockState, Region, Schematic
from litemapy.storage import LitematicaBitArray

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import litematic_pipeline as pipeline


class PipelineTests(unittest.TestCase):
    def test_bit_packing_against_independent_implementation(self):
        rng = random.Random(42)
        for palette_size in (1, 2, 5, 17, 72, 198, 561, 2049):
            with self.subTest(palette_size=palette_size):
                values = [rng.randrange(palette_size) for _ in range(257)]
                bits = max(2, (palette_size - 1).bit_length())
                oracle = LitematicaBitArray(len(values), bits)
                for i, value in enumerate(values):
                    oracle[i] = value
                packed = pipeline.pack_blocks(values, palette_size)
                self.assertEqual([int(word) for word in packed], oracle._to_long_list())
                self.assertEqual(pipeline.unpack_blocks(packed, len(values), palette_size), values)

    def test_invalid_indices_and_truncated_arrays_are_rejected(self):
        with self.assertRaises(ValueError):
            pipeline.pack_blocks([0, 5], 5)
        with self.assertRaises(ValueError):
            pipeline.unpack_blocks([], 1, 2)
        with self.assertRaises(ValueError):
            pipeline.unpack_blocks(nbt.LongArray([3]), 1, 2)

    def test_negative_dimensions_multiple_regions_and_unknown_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source, exported = (Path(directory) / name for name in ("source.litematic", "copy.litematic"))
            negative = Region(11, 9, -7, -5, -3, -7)
            negative[-4, -2, -6] = BlockState("minecraft:oak_stairs", facing="east", half="top",
                                            shape="straight", waterlogged="false")
            negative[0, 0, 0] = BlockState("minecraft:stone")
            positive = Region(-20, 0, 30, 3, 2, 4)
            positive[2, 1, 3] = BlockState("minecraft:glass")
            Schematic(name="Coordinate fixture", regions={"negative": negative,
                                                          "positive": positive}).save(str(source))
            original_nbt = nbt.load(source)
            original_nbt["FutureField"] = nbt.Compound({
                "UUID": nbt.IntArray([1, -2, 3, -4]),
                "empty": nbt.List[nbt.Compound]([]),
                "weights": nbt.List[nbt.Float]([nbt.Float(1.25), nbt.Float(-0.0)]),
                "opaque": nbt.ByteArray([-128, 127]),
            })
            original_nbt["Regions"]["negative"]["PendingBlockTicks"] = nbt.List[nbt.Compound]([
                nbt.Compound({"Block": nbt.String("minecraft:stone"), "Time": nbt.Long(123)})])
            original_nbt.save(source, gzipped=True)
            build = pipeline.read_build(source)
            self.assertEqual(pipeline.cross_check(source, build)["status"], "passed")
            self.assertEqual(build["regions"][0]["local_min_xyz"], [-4, -2, -6])
            on_disk = json.loads(json.dumps(build))
            pipeline.write_build(on_disk, exported)
            restored = pipeline.read_build(exported)
            self.assertTrue(pipeline.verify(build, restored)["passed"])
            self.assertEqual(pipeline.cross_check(exported, restored)["status"], "passed")
            self.assertEqual(pipeline.summary(build)["total_non_air_blocks_across_regions"], 3)
            self.assertEqual(pipeline.summary(build)["enclosing_dimensions_xyz"], [32, 10, 47])
            self.assertEqual(pipeline.encode_tag(nbt.load(source)["FutureField"]),
                             pipeline.encode_tag(nbt.load(exported)["FutureField"]))

    def test_export_refuses_to_replace_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.litematic"
            Region(0, 0, 0, 1, 1, 1).as_schematic().save(str(source))
            with self.assertRaises(FileExistsError):
                pipeline.write_build(pipeline.read_build(source), source)


if __name__ == "__main__":
    unittest.main()
