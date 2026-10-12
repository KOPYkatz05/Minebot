"""Independent file loading and palette-independent diagnostic comparisons."""

from copy import deepcopy
import gzip
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import litematic_pipeline as pipeline

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_NBT_BYTES = 256 * 1024 * 1024


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def fingerprint():
    value = hashlib.sha256(b"reviewer-conversion-v1")
    for path in (ROOT / "tools/litematic_pipeline.py", Path(__file__)):
        value.update(path.read_bytes())
    return value.hexdigest()


def load(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("File exceeds the 64 MiB import limit")
    with path.open("rb") as handle:
        compressed = handle.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    total = 0
    with opener(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(chunk)
            if total > MAX_NBT_BYTES:
                raise ValueError("Decompressed file exceeds the 256 MiB safety limit")
    build = pipeline.read_build(path)
    if sum(len(r["indices_x_fastest"]) for r in build["regions"]) > pipeline.MAX_VOLUME:
        raise ValueError("Total schematic volume exceeds 5 million voxels")
    return build


def scene(build):
    regions = []
    for record in build["regions"]:
        tag = pipeline.decode_tag(record["region_nbt"])
        palette = [{"name": str(state["Name"]),
                    "properties": {key: str(value) for key, value in sorted(state.get("Properties", {}).items())}}
                   for state in tag["BlockStatePalette"]]
        position = pipeline.vector(tag["Position"])
        regions.append({"name": record["name"], "size": record["dimensions_xyz"],
                        "origin": [p + offset for p, offset in zip(position, record["local_min_xyz"])],
                        "signed_size": pipeline.vector(tag["Size"]), "palette": palette,
                        "indices": record["indices_x_fastest"]})
    return {"kind": "diagnostic-voxels", "axes": "x,y,z; x-fastest then z then y", "regions": regions,
            "limitations": "Cube geometry only: stairs, slabs, fences, doors, entities, textures and lighting are not Minecraft-faithful."}


def key(state):
    return state["name"], tuple(sorted(state["properties"].items()))


def position(region, index):
    dx, dy, dz = region["size"]
    y, remainder = divmod(index, dx * dz)
    z, x = divmod(remainder, dx)
    return [region["origin"][0] + x, region["origin"][1] + y, region["origin"][2] + z]


def compare(original, converted):
    left, right = scene(original), scene(converted)
    a = {r["name"]: r for r in left["regions"]}
    b = {r["name"]: r for r in right["regions"]}
    differences, markers, geometry = [], {}, []
    counts = {"missing": 0, "added": 0, "changed": 0}
    names = sorted(a.keys() | b.keys())
    for name in names:
        lr, rr = a.get(name), b.get(name)
        markers[name] = []
        if lr is None or rr is None:
            geometry.append(f"Region {name}: missing counterpart")
            continue
        if any(lr[field] != rr[field] for field in ("size", "origin", "signed_size")):
            geometry.append(f"Region {name}: dimensions, signed size, or position differ")
            continue
        lp, rp = [key(state) for state in lr["palette"]], [key(state) for state in rr["palette"]]
        for i, (li, ri) in enumerate(zip(lr["indices"], rr["indices"])):
            ls, rs = lp[li], rp[ri]
            if ls == rs:
                continue
            kind = ("missing" if rs[0] in pipeline.AIR and ls[0] not in pipeline.AIR else
                    "added" if ls[0] in pipeline.AIR and rs[0] not in pipeline.AIR else "changed")
            counts[kind] += 1
            # Markers are bounded for a usable diagnostic preview; counts remain exact.
            if len(markers[name]) < 10000:
                markers[name].append({"index": i, "position": position(lr, i), "kind": kind})
            if len(differences) < 200:
                differences.append({"region": name, "index": i, "position": position(lr, i), "kind": kind,
                                    "original": lr["palette"][li], "converted": rr["palette"][ri]})
    metadata_changes = []
    if original["root_nbt"] != converted["root_nbt"] or original["root_name"] != converted["root_name"]:
        metadata_changes.append("Root metadata, format version, data version, or tag types differ")
    for name in a.keys() & b.keys():
        lo = deepcopy(next(r for r in original["regions"] if r["name"] == name)["region_nbt"])
        ro = deepcopy(next(r for r in converted["regions"] if r["name"] == name)["region_nbt"])
        for tag in (lo, ro):
            tag["value"].pop("BlockStatePalette", None)
        if lo != ro:
            metadata_changes.append(f"Region {name}: geometry or auxiliary metadata differ")
    return {"passed": not geometry and not metadata_changes and not any(counts.values()),
            "counts": counts, "geometry_errors": geometry, "metadata_changes": metadata_changes,
            "differences": differences, "markers": markers,
            "marker_limit_per_region": 10000, "difference_sample_limit": 200,
            "original": left, "converted": right}
