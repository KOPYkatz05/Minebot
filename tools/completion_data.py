"""Shared block states, masked examples, simple baselines, and edited exports."""

import argparse
from collections import deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import nbtlib as nbt
import numpy as np

import litematic_pipeline as pipeline

ROOT = pipeline.ROOT


def new_directory(path, parent):
    path = path.resolve()
    if not path.is_relative_to(parent.resolve()) or path == parent.resolve():
        raise ValueError(f"Output must be a subdirectory of {parent}")
    if path.exists():
        raise ValueError(f"Output already exists; choose a new name: {path}")
    path.mkdir(parents=True)
    return path


def canonical_state(tag):
    return {"name": str(tag["Name"]),
            "properties": {str(key): str(value) for key, value in
                           sorted(tag.get("Properties", {}).items())}}


def state_key(state):
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def state_tag(state):
    result = nbt.Compound({"Name": nbt.String(state["name"])})
    if state["properties"]:
        result["Properties"] = nbt.Compound({key: nbt.String(value)
                                             for key, value in state["properties"].items()})
    return result


def vocabulary(builds):
    states = {state_key({"name": "minecraft:air", "properties": {}}):
              {"name": "minecraft:air", "properties": {}}}
    for build in builds:
        for record in build["regions"]:
            palette = pipeline.decode_tag(record["region_nbt"])["BlockStatePalette"]
            for index in set(record["indices_x_fastest"]):
                state = canonical_state(palette[index])
                states[state_key(state)] = state
    air_key = state_key({"name": "minecraft:air", "properties": {}})
    ordered = [states[air_key]] + [states[key] for key in sorted(states) if key != air_key]
    return {"schema_version": 1, "states": ordered, "air_id": 0,
            "mask_input_id": len(ordered), "target_class_count": len(ordered),
            "array_axes": "y,z,x", "mask_is_target_class": False}


def array_for(record, vocab):
    lookup = {state_key(state): i for i, state in enumerate(vocab["states"])}
    palette = pipeline.decode_tag(record["region_nbt"])["BlockStatePalette"]
    # Unused palette entries are absent from the vocabulary by design.
    mapping = {index: lookup[state_key(canonical_state(palette[index]))]
               for index in set(record["indices_x_fastest"])}
    dx, dy, dz = record["dimensions_xyz"]
    return np.array([mapping[i] for i in record["indices_x_fastest"]], dtype=np.int32).reshape(dy, dz, dx)


def array_hash(array):
    return hashlib.sha256(np.asarray(array, dtype="<i4").tobytes()).hexdigest()


def air_ids(vocab):
    return np.array([i for i, state in enumerate(vocab["states"])
                     if state["name"] in pipeline.AIR], dtype=np.int32)


def masks_for(target, count, seed, vocab):
    rng = np.random.default_rng(seed)
    occupied = ~np.isin(target, air_ids(vocab))
    if not occupied.any():
        raise ValueError("Building contains only air")
    masks, seen = [], set()
    for attempt in range(10000):
        size = np.array([min(dim, int(rng.integers(3, 6))) for dim in target.shape])
        start = np.array([int(rng.integers(0, dim - width + 1))
                          for dim, width in zip(target.shape, size)])
        mask = np.zeros(target.shape, dtype=bool)
        mask[tuple(slice(int(s), int(s + width)) for s, width in zip(start, size))] = True
        key = tuple(start) + tuple(size)
        if key in seen or int(occupied[mask].sum()) < min(8, int(size.prod())):
            continue
        seen.add(key)
        masks.append(mask)
        if len(masks) == count:
            return np.stack(masks)
    raise ValueError("Unable to sample enough distinct masks containing building blocks")


def nearest_visible(target, mask):
    """Fill by Manhattan nearest observed voxel; never reads hidden answers."""
    predicted = target.copy()
    predicted[mask] = 0
    queue = deque(tuple(int(v) for v in coordinate) for coordinate in np.argwhere(~mask))
    visited = ~mask.copy()
    while queue:
        point = queue.popleft()
        for axis in range(3):
            for offset in (-1, 1):
                neighbor = list(point)
                neighbor[axis] += offset
                neighbor = tuple(neighbor)
                if (0 <= neighbor[axis] < target.shape[axis]) and not visited[neighbor]:
                    visited[neighbor] = True
                    predicted[neighbor] = predicted[point]
                    queue.append(neighbor)
    return predicted


def metrics(target, prediction, mask, vocab):
    truth, predicted = target[mask], prediction[mask]
    true_solid = ~np.isin(truth, air_ids(vocab))
    pred_solid = ~np.isin(predicted, air_ids(vocab))
    tp = int((true_solid & pred_solid).sum())
    def ratio(numerator, denominator):
        return float(numerator / denominator) if denominator else None
    return {"masked_voxels": int(mask.sum()), "masked_non_air_voxels": int(true_solid.sum()),
            "block_state_accuracy": float((truth == predicted).mean()),
            "non_air_state_accuracy": ratio(int(((truth == predicted) & true_solid).sum()), int(true_solid.sum())),
            "air_state_accuracy": ratio(int(((truth == predicted) & ~true_solid).sum()), int((~true_solid).sum())),
            "occupancy_precision": ratio(tp, int(pred_solid.sum())),
            "occupancy_recall": ratio(tp, int(true_solid.sum()))}


def edited_build(build, array, vocab, name):
    """First edited exporter: one region, no populated entities or tick payloads."""
    if len(build["regions"]) != 1:
        raise ValueError("Edited exports currently support one region")
    changed = deepcopy(build)
    record = changed["regions"][0]
    region = pipeline.decode_tag(record["region_nbt"])
    for key in ("Entities", "TileEntities", "PendingBlockTicks", "PendingFluidTicks"):
        if len(region.get(key, [])):
            raise ValueError(f"Edited export would require handling populated {key}")
    dx, dy, dz = record["dimensions_xyz"]
    if tuple(array.shape) != (dy, dz, dx):
        raise ValueError("Prediction dimensions differ from source")
    if np.any(array < 0) or np.any(array >= len(vocab["states"])):
        raise ValueError("Predicted IDs include a mask token or unknown target")
    region["BlockStatePalette"] = nbt.List[nbt.Compound]([state_tag(state) for state in vocab["states"]])
    record["indices_x_fastest"] = [int(value) for value in array.ravel()]
    record["region_nbt"] = pipeline.encode_tag(region)
    root = pipeline.decode_tag(changed["root_nbt"])
    meta = root["Metadata"]
    meta["Name"] = nbt.String(name)
    meta["Description"] = nbt.String("Masked completion experiment; original source remains unchanged")
    meta["TimeModified"] = nbt.Long(int(time.time() * 1000))
    meta["TotalBlocks"] = nbt.Int(int((~np.isin(array, air_ids(vocab))).sum()))
    meta["TotalVolume"] = nbt.Int(int(array.size))
    meta["RegionCount"] = nbt.Int(1)
    meta["PreviewImageData"] = nbt.IntArray([])
    changed["root_nbt"] = pipeline.encode_tag(root)
    return changed


def export_checked(build, array, vocab, output, name):
    changed = edited_build(build, array, vocab, name)
    pipeline.write_build(changed, output)
    parsed = pipeline.read_build(output)
    if not np.array_equal(array_for(parsed["regions"][0], vocab), array):
        raise ValueError("Exported predictions do not match requested block array")
    independent = pipeline.cross_check(output, parsed)
    if independent["status"] != "passed":
        raise ValueError(f"Independent edited-export check unavailable: {independent}")
    if not pipeline.verify(changed, parsed)["passed"]:
        raise ValueError("Edited export changed metadata or geometry unexpectedly")
    return {"reimport_matches": True, "independent_reader": independent,
            "non_air_blocks": pipeline.summary(parsed)["total_non_air_blocks_across_regions"]}


def synthetic_house(path):
    """Project-authored fixture with a stone floor, timber walls, glass, and roof."""
    from litemapy import BlockState, Region
    region = Region(0, 0, 0, 13, 11, 13)
    for x in range(1, 12):
        for z in range(1, 12):
            region[x, 0, z] = BlockState("minecraft:stone_bricks")
            for y in range(1, 6):
                if x in (1, 11) or z in (1, 11):
                    block = "minecraft:oak_log" if x in (1, 11) and z in (1, 11) else "minecraft:oak_planks"
                    region[x, y, z] = (BlockState(block, axis="y") if block.endswith("log")
                                       else BlockState(block))
    for z in (1, 11):
        for x in (3, 4, 8, 9):
            for y in (2, 3):
                region[x, y, z] = BlockState("minecraft:glass")
    for y in (1, 2):
        region[6, y, 1] = BlockState("minecraft:air")
    for x in range(13):
        roof_y = 6 + min(x, 12 - x) // 2
        for z in range(13):
            region[x, roof_y, z] = BlockState("minecraft:dark_oak_planks")
    region.as_schematic(name="Project generated training fixture", author="Minecraft building AI project",
                        description="Generated by local project code for pipeline testing").save(str(path))


def prepare_pilot(destination):
    sources = sorted((ROOT / "data/raw_litematics").glob("*.litematic"))
    if not sources:
        raise ValueError("No source schematics found")
    builds = [pipeline.read_build(source) for source in sources]
    vocab = vocabulary(builds)
    destination = new_directory(destination, ROOT / "data/parsed_buildings")
    pipeline.save_json(destination / "vocabulary.json", vocab)
    entries = []
    for source, build in zip(sources, builds):
        region_entries = []
        for i, record in enumerate(build["regions"]):
            array = array_for(record, vocab)
            filename = f"{build['source_sha256'][:12]}-region-{i}.npz"
            with (destination / filename).open("xb") as handle:
                np.savez_compressed(handle, target=array)
            region_entries.append({"name": record["name"], "file": filename,
                                   "dimensions_xyz": record["dimensions_xyz"],
                                   "array_sha256": array_hash(array),
                                   "within_initial_extent": all(d <= 32 for d in record["dimensions_xyz"])})
        entries.append({"source_file": source.relative_to(ROOT).as_posix(),
                        "source_sha256": build["source_sha256"],
                        "family_group_id": build["source_sha256"], "family_review": "pending",
                        "split": "unassigned", "source_url": None,
                        "training_permission": "unknown", "regions": region_entries})
    pipeline.save_json(destination / "manifest.json", {"schema_version": 1,
                       "purpose": "inspection_pool_not_a_split_training_dataset",
                       "vocabulary_origin": "used_states_from_all_pilot_sources",
                       "entries": entries})
    print(f"Prepared {len(builds)} builds with {len(vocab['states'])} shared block states: {destination}")
    print("Family assignments and training permission remain unassigned. No model was trained.")


def prepare_exercise(destination, source=None, generated=False):
    if not generated and source is None:
        raise ValueError("Provide --source or --synthetic")
    if generated:
        destination = new_directory(destination, ROOT / "experiments")
        source = destination / "project-generated-house.litematic"
        synthetic_house(source)
    else:
        source = source.resolve()
    build = pipeline.read_build(source)
    if len(build["regions"]) != 1 or any(d > 32 for d in build["regions"][0]["dimensions_xyz"]):
        raise ValueError("First exercise requires one region with dimensions at most 32 each")
    vocab = vocabulary([build])
    target = array_for(build["regions"][0], vocab)
    masks = masks_for(target, 28, 42, vocab)
    # Validate edited-export support before creating source-derived outputs.
    edited_build(build, target, vocab, "Validation")
    if not generated:
        destination = new_directory(destination, ROOT / "experiments")
    train_masks, probe_masks = masks[:24], masks[24:]
    with (destination / "examples.npz").open("xb") as handle:
        np.savez_compressed(handle, target=target, train_masks=train_masks, probe_masks=probe_masks)
    pipeline.save_json(destination / "vocabulary.json", vocab)
    pipeline.save_json(destination / "source.internal.json", build)
    pipeline.save_json(destination / "exercise.json", {
        "schema_version": 1, "purpose": "single_build_completion_debug",
        "source_file": source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else str(source),
        "source_sha256": build["source_sha256"], "target_array_sha256": array_hash(target),
        "training_permission": "project_generated" if generated else "unknown",
        "permission_evidence": "tools/completion_data.py synthetic_house" if generated else None,
        "seed": 42, "train_mask_count": len(train_masks), "probe_mask_count": len(probe_masks),
        "evaluation_scope": "Different masks on the SAME building; no generalization claim",
        "array_axes": "y,z,x", "input_mask_token": vocab["mask_input_id"]})
    mask = probe_masks[0]
    empty = target.copy()
    empty[mask] = vocab["air_id"]
    neighbor = nearest_visible(target, mask)
    reports = {}
    for label, prediction in (("missing-section", empty), ("neighbor-baseline", neighbor), ("answer", target)):
        check = export_checked(build, prediction, vocab, destination / f"{label}.litematic", label)
        reports[label] = {"metrics": metrics(target, prediction, mask, vocab), "export_check": check}
    baseline_scores = {label: [metrics(target,
        np.where(m, vocab["air_id"], target) if label == "leave_empty" else nearest_visible(target, m),
        m, vocab) for m in probe_masks] for label in ("leave_empty", "nearest_visible")}
    pipeline.save_json(destination / "baseline-report.json", {
        "evaluation_scope": "same_build_probe_masks", "scores": baseline_scores,
        "first_probe_exports": reports,
        "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == build["source_sha256"]})
    print(f"Exercise: {destination}; {len(vocab['states'])} block states; 24 training and 4 probe masks")
    print("Exported missing section, neighbor baseline, and original answer; all re-import checks passed.")
    print("Downloaded examples are prepared for inspection; trainer requires recorded permission.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pilot = commands.add_parser("prepare-pilot")
    pilot.add_argument("--output-dir", type=Path, required=True)
    exercise = commands.add_parser("prepare-exercise")
    selection = exercise.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source", type=Path)
    selection.add_argument("--synthetic", action="store_true")
    exercise.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare-pilot":
            prepare_pilot(args.output_dir)
        else:
            prepare_exercise(args.output_dir, args.source, args.synthetic)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
