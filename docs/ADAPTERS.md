# Existing parsers, conversions and reviewer interfaces

These are the original adapters from the local Minecraft building AI project, now committed at their
existing paths. Reuse them; no parser rewrite or downloaded build is needed to develop the bot.
Run commands from the repository root, using .venv/bin/python on Linux or .venv/Scripts/python.exe on Windows.

| Capability | Entry point | Contract |
| --- | --- | --- |
| Bounded Litematica loading | `reviewer.comparison.load(path)` | Checks 64 MiB compressed / 256 MiB decoded limits and 5 million total voxels; returns the internal build dictionary. |
| Parser | `tools/litematic_pipeline.py:read_build(path)` | Decodes Litematica versions 6/7, signed regions and typed NBT. Use the bounded loader for discovered untrusted files. |
| Lossless export | `reviewer.comparison.pipeline.write_build(build, output)` | Writes a new .litematic, refuses overwrites; preserves typed NBT and block-state properties. |
| File verification | `pipeline.verify(original, converted)` | Exact archival comparison of blocks, geometry, palettes and metadata. |
| Independent reader | `pipeline.cross_check(path, build)` | Litemapy coordinate/state check; report explicitly if unavailable. |
| Reviewer comparison | `reviewer.comparison.compare(original, converted)` | Independently loaded block-state comparison including missing/added/changed blocks, geometry and metadata errors. |
| Converter identity | `reviewer.comparison.fingerprint()` | Hash of converter/comparison code used to invalidate stale conversion approvals. |
| Measured summary | `pipeline.summary(build)` | Dimensions, palette usage, region and metadata notes. |
| Project-generated fixture | `completion_data.synthetic_house(path)` | Writes a project-owned schematic; import through tools path as below. |
| Dataset preparation | `tools/completion_data.py` | Full-state vocabulary, masks, baselines and checked edited exports; air remains distinct from mask input. |
| Reviewer backend | `reviewer/app.py`, `reviewer/service.py` | FastAPI, independent conversions, persistent review, provenance, dataset membership and snapshots. |
| Reviewer UI | `reviewer/frontend/` | Locally built React/Three.js; existing lockfile and replaceable ViewerAdapter. |
| Optional training | `tools/train_completion.py` | Masked completion debug experiment; PyTorch separate from collector/reviewer dependencies. |

Minimal adapter usage, without starting a reviewer service:

```python
from pathlib import Path
from reviewer import comparison as cmp

source = Path("data/raw_litematics/example.litematic")
output = Path("exports/example-roundtrip/new-copy.litematic")
original = cmp.load(source)
output.parent.mkdir(parents=True, exist_ok=True)
cmp.pipeline.write_build(original, output)
converted = cmp.load(output)
report = cmp.compare(original, converted)
archive_report = cmp.pipeline.verify(original, converted)
independent = cmp.pipeline.cross_check(output, converted)
# Save reports and source hash; passed conversion does not approve usage or visual quality.
```

Keep parser validation in the collector's isolated subprocess.
The implemented `minebot validate` worker now uses these entry points directly. See
[BOT.md](BOT.md) for limits, report storage, retries and manual approval boundaries.
Do not instantiate a second reviewer Service: its startup recovers jobs, which would interfere with a
running reviewer. Do not edit reviewer SQLite tables from the collector; `minebot handoff` uses its loopback API.

Tests generate temporary source files themselves: tests/test_pipeline.py, tests/test_completion.py,
tests/test_reviewer.py and tests/test_discovery.py. A clean checkout's full command is:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

PyTorch tests skip when it is not installed. Local-Minecraft-cache tests also skip when the cache is
absent. These are reported optional checks, not passing evidence of GPU or in-game behavior.

## Context and local artifacts

README.md preserves the local project's decisions, prior measurements, source policy, results and
Windows launch instructions. project.json preserves target versions and experiment configuration;
data/annotations/ preserves the source policy records and annotation template.
The paths for the original four downloads, parsed pilot, completed experiments and vanilla cache
refer to the user's local workspace and are not available in cloud checkouts. Do not silently substitute
downloaded fixtures, claim the historical experiments have been rerun, or grant permission based on a
public source URL. The tests' generated house provides permitted known-answer fixtures.

docs/BOT.md and docs/PLAN.md preserve the bot's current state and accepted roadmap.
GitHub source sync does not copy machine-local datasets, credentials, database state or running services.
