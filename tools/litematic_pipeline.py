"""Preserve NBT payloads while decoding and rebuilding Litematica block arrays.

The archive representation is for unchanged round trips, not generated builds.
Indices have x fastest, then z, then y: i = (y * dz + z) * dx + x.
Storage coordinates start at the region's minimum local coordinate on each axis.
"""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

import nbtlib as nbt

ROOT = Path(__file__).resolve().parents[1]
AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
MASK64 = (1 << 64) - 1
MAX_VOLUME = 5_000_000


def encode_tag(tag):
    """JSON tagged tree retains numeric types, list subtypes and unknown fields."""
    type_id = tag.tag_id
    if type_id == 10:
        value = {key: encode_tag(item) for key, item in tag.items()}
    elif type_id == 9:
        return {"type": 9, "subtype": tag.subtype.tag_id,
                "value": [encode_tag(item) for item in tag]}
    elif type_id in (7, 11, 12):
        value = [int(item) for item in tag]
    elif type_id == 8:
        value = str(tag)
    elif type_id in (5, 6):
        value = float(tag).hex()
    else:
        value = int(tag)
    return {"type": type_id, "value": value}


def decode_tag(record):
    classes = {tag.tag_id: tag for tag in (
        nbt.End, nbt.Byte, nbt.Short, nbt.Int, nbt.Long, nbt.Float,
        nbt.Double, nbt.ByteArray, nbt.String, nbt.List, nbt.Compound,
        nbt.IntArray, nbt.LongArray)}
    type_id, value = record["type"], record["value"]
    if type_id == 10:
        return nbt.Compound({key: decode_tag(item) for key, item in value.items()})
    if type_id == 9:
        return nbt.List[classes[record["subtype"]]]([decode_tag(item) for item in value])
    if type_id in (5, 6):
        value = float.fromhex(value)
    return classes[type_id](value)


def unpack_blocks(longs, count, palette_size):
    if palette_size < 1:
        raise ValueError("Empty block palette")
    bits = max(2, (palette_size - 1).bit_length())
    expected = (count * bits + 63) // 64
    if len(longs) != expected:
        raise ValueError(f"BlockStates length {len(longs)} != expected {expected}")
    words = [int(value) & MASK64 for value in longs]
    mask = (1 << bits) - 1
    indices = []
    for index in range(count):
        word, shift = divmod(index * bits, 64)
        value = words[word] >> shift
        if shift + bits > 64:
            value |= words[word + 1] << (64 - shift)
        value &= mask
        if value >= palette_size:
            raise ValueError(f"Invalid palette index {value} at voxel {index}")
        indices.append(value)
    return indices


def pack_blocks(indices, palette_size):
    if palette_size < 1:
        raise ValueError("Empty block palette")
    bits = max(2, (palette_size - 1).bit_length())
    words = [0] * ((len(indices) * bits + 63) // 64)
    for index, value in enumerate(indices):
        if type(value) is not int or not 0 <= value < palette_size:
            raise ValueError(f"Invalid palette index at voxel {index}: {value}")
        word, shift = divmod(index * bits, 64)
        words[word] |= (value << shift) & MASK64
        if shift + bits > 64:
            words[word + 1] |= value >> (64 - shift)
    return nbt.LongArray([value if value < (1 << 63) else value - (1 << 64)
                          for value in words])


def vector(tag):
    return [int(tag[axis]) for axis in "xyz"]


def read_build(path):
    source = nbt.load(path)
    version = int(source["Version"])
    if version not in (6, 7):
        raise ValueError(f"Schematic format {version} is not validated; expected 6 or 7")
    regions = []
    for name, region in source["Regions"].items():
        size = vector(region["Size"])
        count = math.prod(abs(value) for value in size)
        if not 0 < count <= MAX_VOLUME:
            raise ValueError(f"Region {name} volume {count} outside supported limits")
        indices = unpack_blocks(region["BlockStates"], count, len(region["BlockStatePalette"]))
        extra = deepcopy(region)
        del extra["BlockStates"]
        regions.append({"name": name, "dimensions_xyz": [abs(value) for value in size],
                        "local_min_xyz": [min(0, value + 1) for value in size],
                        "indices_x_fastest": indices, "region_nbt": encode_tag(extra)})
    if not regions:
        raise ValueError("Schematic has no regions")
    extra_root = nbt.Compound({key: deepcopy(value) for key, value in source.items()
                               if key != "Regions"})
    return {"schema_version": 1, "purpose": "unchanged_litematic_roundtrip",
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "root_name": source.root_name, "root_nbt": encode_tag(extra_root),
            "regions": regions}


def write_build(build, output):
    if build["schema_version"] != 1 or build["purpose"] != "unchanged_litematic_roundtrip":
        raise ValueError("Unsupported internal representation")
    root = decode_tag(build["root_nbt"])
    root["Regions"] = nbt.Compound()
    for record in build["regions"]:
        region = decode_tag(record["region_nbt"])
        size = vector(region["Size"])
        dimensions = [abs(value) for value in size]
        if dimensions != record["dimensions_xyz"]:
            raise ValueError("Region dimensions disagree with preserved NBT")
        if record["local_min_xyz"] != [min(0, value + 1) for value in size]:
            raise ValueError("Region coordinate origin disagrees with preserved NBT")
        if math.prod(dimensions) != len(record["indices_x_fastest"]):
            raise ValueError("Region volume disagrees with voxel array")
        if record["name"] in root["Regions"]:
            raise ValueError("Duplicate region name")
        region["BlockStates"] = pack_blocks(record["indices_x_fastest"],
                                             len(region["BlockStatePalette"]))
        root["Regions"][record["name"]] = region
    with output.open("xb") as handle:
        # gzip is a transport wrapper; preserve the NBT root name and all tag values.
        import gzip
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            nbt.File(root, root_name=build["root_name"]).write(compressed)


def state_name(state):
    name = str(state["Name"])
    properties = state.get("Properties", {})
    if properties:
        name += "[" + ",".join(f"{key}={properties[key]}" for key in sorted(properties)) + "]"
    return name


def summary(build):
    root = decode_tag(build["root_nbt"])
    regions = []
    minima, maxima = [], []
    notes = []
    for record in build["regions"]:
        region = decode_tag(record["region_nbt"])
        palette = region["BlockStatePalette"]
        counts = Counter(record["indices_x_fastest"])
        non_air = sum(count for index, count in counts.items()
                      if str(palette[index]["Name"]) not in AIR)
        position, size = vector(region["Position"]), vector(region["Size"])
        low = [p + min(0, s + 1) for p, s in zip(position, size)]
        high = [lo + dim - 1 for lo, dim in zip(low, record["dimensions_xyz"])]
        minima.append(low)
        maxima.append(high)
        opaque = [key for key in region if key not in
                  {"Position", "Size", "BlockStatePalette"}]
        regions.append({"name": record["name"], "position_xyz": position,
                        "signed_size_xyz": size, "dimensions_xyz": record["dimensions_xyz"],
                        "palette_state_count": len(palette), "used_palette_state_count": len(counts),
                        "voxel_count": len(record["indices_x_fastest"]),
                        "non_air_block_count": non_air,
                        "entity_count": len(region.get("Entities", [])),
                        "block_entity_count": len(region.get("TileEntities", [])),
                        "preserved_auxiliary_fields": opaque,
                        "block_state_counts": {state_name(palette[index]): count
                                               for index, count in sorted(counts.items())}})
        for field in ("Entities", "TileEntities", "PendingBlockTicks", "PendingFluidTicks"):
            if region.get(field):
                notes.append(f"{record['name']}: {field} preserved as typed NBT; not interpreted for learning")
    bounds = [max(hi[a] for hi in maxima) - min(lo[a] for lo in minima) + 1
              for a in range(3)]
    if len(regions) > 1:
        notes.append("Regions retained separately; overlaps are not flattened or resolved")
    return {"format_version": int(root["Version"]),
            "minecraft_data_version": int(root["MinecraftDataVersion"]),
            "enclosing_dimensions_xyz": bounds, "region_count": len(regions),
            "total_non_air_blocks_across_regions": sum(r["non_air_block_count"] for r in regions),
            "regions": regions, "notes": notes,
            "preservation_policy": "All NBT fields preserved; only block arrays decoded and repacked",
            "in_game_check": "pending_user_check"}


def verify(original, exported):
    checks = {"root_name_equal": original["root_name"] == exported["root_name"],
              "root_metadata_and_types_equal": original["root_nbt"] == exported["root_nbt"],
              "region_count_equal": len(original["regions"]) == len(exported["regions"])}
    left = {record["name"]: record for record in original["regions"]}
    right = {record["name"]: record for record in exported["regions"]}
    checks["region_names_equal"] = left.keys() == right.keys()
    checked = 0
    for name, region in left.items():
        other = right.get(name)
        checks[f"{name}:geometry_palette_auxiliary_nbt_equal"] = (
            other is not None and region["region_nbt"] == other["region_nbt"])
        checks[f"{name}:every_voxel_palette_index_equal"] = (
            other is not None and region["indices_x_fastest"] == other["indices_x_fastest"])
        checked += len(region["indices_x_fastest"])
    return {"passed": all(checks.values()), "voxel_positions_compared": checked, "checks": checks}


def cross_check(path, build):
    """Separate Litemapy reader confirms coordinates and complete block states."""
    from litemapy import Schematic
    try:
        schematic = Schematic.load(str(path))
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "unavailable", "reason": str(error)}
    compared = 0
    for record in build["regions"]:
        region = schematic.regions[record["name"]]
        preserved = decode_tag(record["region_nbt"])
        palette = preserved["BlockStatePalette"]
        dx, dy, dz = record["dimensions_xyz"]
        ox, oy, oz = record["local_min_xyz"]
        for i, palette_index in enumerate(record["indices_x_fastest"]):
            y, remainder = divmod(i, dx * dz)
            z, x = divmod(remainder, dx)
            other = region[x + ox, y + oy, z + oz].to_nbt()
            expected = palette[palette_index]
            # An absent Properties compound and an empty one mean the same state.
            if (str(other["Name"]) != str(expected["Name"])
                    or dict(other.get("Properties", {})) != dict(expected.get("Properties", {}))):
                raise ValueError(f"Independent reader mismatch in {record['name']} at {x,y,z}")
            compared += 1
    return {"status": "passed", "voxel_positions_compared": compared}


def save_json(path, payload):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def run_roundtrip(source, destination):
    source = source.resolve()
    if not source.is_file() or source.suffix.lower() != ".litematic":
        raise ValueError("Provide an existing .litematic file")
    destination = destination.resolve()
    if not destination.is_relative_to(ROOT / "exports"):
        raise ValueError("Output directory must be inside this project's exports folder")
    if destination.exists():
        raise ValueError("Output directory already exists; choose a new name to preserve previous results")
    build = read_build(source)
    independent = cross_check(source, build)
    destination.mkdir(parents=True)
    archive = destination / "building.internal.json"
    save_json(archive, build)
    # Re-read the actual on-disk representation before exporting.
    restored = json.loads(archive.read_text(encoding="utf-8"))
    exported = destination / f"{source.stem}-roundtrip.litematic"
    write_build(restored, exported)
    checked = read_build(exported)
    verification = verify(build, checked)
    verification["independent_source_reader"] = independent
    verification["independent_export_reader"] = cross_check(exported, checked)
    verification["source_file_sha256"] = build["source_sha256"]
    verification["source_unchanged"] = (hashlib.sha256(source.read_bytes()).hexdigest()
                                         == build["source_sha256"])
    verification["passed"] = (verification["passed"] and verification["source_unchanged"])
    save_json(destination / "inspection.json", summary(build))
    save_json(destination / "verification.json", verification)
    print(f"Export: {exported}")
    print(f"Inspection: {destination / 'inspection.json'}")
    print(f"Verification: {'PASS' if verification['passed'] else 'FAIL'}; "
          f"{verification['voxel_positions_compared']:,} voxel positions compared")
    print(f"Independent reader: source {independent['status']}; "
          f"export {verification['independent_export_reader']['status']}")
    print("Next: load the exported copy in your Minecraft profile and compare it visually.")
    return 0 if verification["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("source", type=Path)
    roundtrip = subparsers.add_parser("roundtrip")
    roundtrip.add_argument("source", type=Path)
    roundtrip.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            print(json.dumps(summary(read_build(args.source)), indent=2))
            return 0
        return run_roundtrip(args.source, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
