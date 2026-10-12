# Minebot

Automated GitHub discovery and verified original collection for a reproducible Minecraft building
dataset. **Pinned evidence capture and downloads are implemented; conversion validation and reviewer
integration are next.**
It never approves builds or grants training permission.

## Codex Cloud

Use [the environment setup guide](docs/CLOUD.md) with this repository. Install with:

```bash
bash scripts/setup-cloud.sh
```

The script creates `.venv`, installs dependencies and runs offline checks. Account-level environment
creation/publication must be completed in Codex; it is not automatically performed by the script.
CI independently tests Linux and Windows. This repo does not contain the existing local reviewer,
its datasets, downloaded buildings, Minecraft resources or training checkpoints.

## Commands

From the repository root (Windows: replace `.venv/bin/python` with `.venv/Scripts/python.exe`):

```bash
.venv/bin/python -m minebot init
.venv/bin/python -m minebot status
.venv/bin/python -m minebot configure config.example.json
.venv/bin/python -m minebot check-github
.venv/bin/python -m minebot --data-dir data/probe probe --request-budget 10
.venv/bin/python -m minebot run
.venv/bin/python -m minebot collect
.venv/bin/python -m minebot candidates --limit 100 --offset 0
.venv/bin/python -m minebot pause
.venv/bin/python -m minebot resume
.venv/bin/python -m minebot dismiss CANDIDATE_ID
.venv/bin/python -m minebot restore CANDIDATE_ID
.venv/bin/python -m minebot attempts CANDIDATE_ID
.venv/bin/python -m minebot retry CANDIDATE_ID
.venv/bin/python -m unittest discover -s tests -v
```

`run` discovers metadata; `collect` processes the persisted collection queue. Both need `GITHUB_TOKEN`
provided through environment credentials when making requests; never put a token in configuration.
`check-github` uses one authenticated `/user` request and reports only whether authentication succeeded;
it never outputs credentials or profile contents. `probe` authenticates, then runs metadata-only
discovery with at most 10 total requests (including authentication), 10 new candidates and 120 seconds.
Use an isolated data directory for the probe. Its temporary budgets never change persisted collection
configuration. A successful authentication followed by a budget deferral is a valid bounded probe,
not a failed authentication. Both commands respect pause and saved server deferrals.
All remaining administrative commands and tests work offline. Use `--data-dir PATH` **before** the command to isolate runs.
The collector uses a separate SQLite database under ignored `data/discovery/`. Back it up to preserve
configuration, candidates, repository identities, pinned traversal work and run history.

Public-only repository search and watched repositories are inspected at pinned commits. Candidate
versions retain stable repository ID, revision, path, Git blob SHA and source URL. Repeated discoveries
are idempotent; later commits retain separate source references. House filenames receive an explicitly
unconfirmed suggestion. Schematics in other formats, archives and submodules are marked unsupported.

Requests are serial and bounded; rate limits and temporary failures defer work instead of blocking
in a sleeping process. Interrupted traversal resumes from its saved position. Search caps and incomplete
results are visible in run warnings. **This is not an exhaustive index of GitHub.**

## Verified collection

`collect` rechecks public visibility and stable repository identity, then verifies the candidate path,
size and Git blob SHA against its pinned commit. It inspects nonrecursive trees along the source path
and captures matching README, LICENSE/LICENCE, COPYING, AUTHORS, ATTRIBUTION, NOTICE and COPYRIGHT
files from the repository root and ancestor directories. Evidence retains exact original bytes,
SHA-256, Git blob identity, revision, path and source URL. Evidence is limited to 32 files, 1 MiB per
file and 8 MiB per candidate. Truncated trees, oversized or unreadable evidence and unsupported
evidence entries hold the source. External links and LFS objects are not followed.

This is a bounded evidence screen, not a license adjudicator: text patterns indicating possible
collection, redistribution or training restrictions (including "all rights reserved") hold the source
before downloading its schematic. These patterns can produce false positives and do not detect
every restriction. Scoped or linked terms outside the inspected directories need human review.
Captured repository evidence never establishes asset authorship or asset-specific usage permission.
Missing evidence permits staging for inspection with usage still explicitly `unknown`.

Originals stream through temporary files and must match both pinned byte size and Git blob SHA
before entering SHA-256-addressed storage under ignored `data/discovery/staging/objects/`. Duplicate
references reuse verified originals without losing source identities. Git LFS pointers are retained
as `lfs_pointer`, not downloaded schematics. All originals stay `not_validated`, unapproved and
ineligible for automatic training admission; file parsing is the next milestone.

The 64 MiB compressed-file limit applies before transfer. Run limits bound processed candidates,
API requests, elapsed time and original/evidence payload bytes; the staging limit includes cached
objects and abandoned files. Database backups and bounded logs are separate from the staging budget.
Corrupt and oversized responses never become accepted objects. Transfers stop on pause, time or
byte limits; interrupted `.part` files are removed under the shared collector lock at the next collection.
Known duplicate bytes consume no additional transfer or storage budget. Lowering a budget below
existing storage never deletes preserved originals.

Candidate states include `discovered`, `collecting`, `deferred`, `downloaded`, `held`, `failed`,
`lfs_pointer`, `unsupported` and `dismissed`. Durable attempt history retains failures, interruptions,
byte counts and retry times. `retry` requeues failed/deferred candidates; it cannot override a saved
server deferral, a source hold or human dismissal. Held sources need human review; no automated or
CLI override is included. Discovery and collection share saved GitHub deferrals and a process lock.
Local logs rotate at 1 MiB with three backups and omit source bodies and credentials.

Database schema v2 automatically backs up existing v1 databases before migration. Preserve both
the discovery database and staging files when backing up or moving the collector.
No live source assets have been collected or trained on as part of development verification.

See [the accepted plan and remaining milestones](docs/PLAN.md).
