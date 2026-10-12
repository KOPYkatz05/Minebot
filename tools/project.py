"""Read-only readiness and source inventory checks; no external dependencies."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_config():
    with (ROOT / "project.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def project_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Configured path is outside the project: {relative}")
    return path


def doctor(config):
    errors = []
    print(f"Project: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    extent = config.get("training_extent_xyz")
    if not (isinstance(extent, list) and len(extent) == 3
            and all(type(size) is int and size > 0 for size in extent)):
        errors.append("training_extent_xyz must contain three positive integers")
    for name, relative in config["paths"].items():
        path = project_path(relative)
        if path.is_dir():
            print(f"OK: {name} directory")
        else:
            errors.append(f"Missing directory: {relative}")
    for key in ("minecraft_version", "litematica_version"):
        print(f"{key}: {config.get(key) or 'needs your version'}")
    print(f"Proposed learning extent (x, y, z): {extent}")
    for error in errors:
        print(f"ERROR: {error}")
    print("This checks setup only. See exports/*/verification.json for file checks; in-game checks are separate.")
    return 1 if errors else 0


def inventory(config):
    raw = project_path(config["paths"]["raw"])
    if not raw.is_dir():
        raise ValueError(f"Raw data directory does not exist: {raw}")
    files = sorted(path for path in raw.rglob("*")
                   if path.is_file() and path.suffix.lower() == ".litematic")
    groups = {}
    records = []
    for path in files:
        if not path.resolve().is_relative_to(raw):
            raise ValueError(f"Source link points outside the raw directory: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        sha256 = digest.hexdigest()
        relative = path.relative_to(ROOT).as_posix()
        groups.setdefault(sha256, []).append(relative)
        records.append({"source_file": relative,
                        "bytes": path.stat().st_size,
                        "source_sha256": sha256})
    print(json.dumps({"file_count": len(records), "files": records,
                      "exact_duplicate_groups": [paths for paths in groups.values()
                                                 if len(paths) > 1]}, indent=2))
    if not files:
        print("Next step: add one real .litematic building to data/raw_litematics/.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "inventory"))
    args = parser.parse_args()
    try:
        config = load_config()
        return {"doctor": doctor, "inventory": inventory}[args.command](config)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
