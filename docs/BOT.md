# Minebot

Automated GitHub discovery and verified original collection for a reproducible Minecraft building
dataset. **Pinned evidence capture, verified downloads and isolated conversion validation are
implemented, with local API handoff and Discovery controls for the local collector. Scheduling and the live collection pilot remain unfinished.**
It never approves builds or grants training permission.

## Codex Cloud

Use [the environment setup guide](CLOUD.md) with this repository. Install with:

```bash
bash scripts/setup-cloud.sh
```

The script creates `.venv`, installs dependencies and runs offline checks. Account-level environment
creation/publication must be completed in Codex; it is not automatically performed by the script.
CI independently tests Linux and Windows. The shared repository includes the original parser,
conversion adapters and reviewer source; see [ADAPTERS.md](ADAPTERS.md). Downloaded assets,
databases, Minecraft resources and training checkpoints remain local.

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
.venv/bin/python -m minebot validate
.venv/bin/python -m minebot handoff
.venv/bin/python -m minebot retry-handoff CANDIDATE_ID
.venv/bin/python -m minebot candidates --limit 100 --offset 0
.venv/bin/python -m minebot pause
.venv/bin/python -m minebot resume
.venv/bin/python -m minebot dismiss CANDIDATE_ID
.venv/bin/python -m minebot restore CANDIDATE_ID
.venv/bin/python -m minebot attempts CANDIDATE_ID
.venv/bin/python -m minebot retry CANDIDATE_ID
.venv/bin/python -m minebot retry-validation CANDIDATE_ID
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
ineligible for automatic training admission; the separate `validate` command performs file checks without granting approval.

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

Database schema v3 automatically backs up existing v1/v2 databases before migration. Preserve both
the discovery database and staging files when backing up or moving the collector.
No live source assets have been collected or trained on as part of development verification.

See [the accepted plan and remaining milestones](PLAN.md).

## Isolated conversion validation

`validate` works offline on `downloaded` candidates and keeps their collection status separate from
`validation_status`. It checks the stored original's size, Git blob identity and SHA-256 before launching
the trusted worker in a fresh Python subprocess. The worker uses `reviewer.comparison.load` for the
64 MiB compressed, 256 MiB decoded and five-million-total-voxel limits, then writes a new conversion,
loads it separately, and runs exact archival and full-state/geometry comparisons. Litemapy independently
checks both source and conversion. An unavailable independent reader never produces a passing result.

Originals remain unchanged; signed regions, complete block states, air, typed metadata, entity payloads
and pending ticks pass through the existing unchanged-roundtrip adapter. Regions and overlaps are
retained separately. The collector never creates a reviewer Service or writes its database.

The worker runs with Python isolation (`-I`) and a minimal environment that omits credentials, proxies
and Python startup hooks. This is process isolation, not an operating-system network/filesystem sandbox.
Linux adds a 2 GiB address-space guard; Windows relies on the same input/output/voxel/time bounds.
Each subprocess has a five-minute timeout, bounded by the remaining run time. Pause and interruption
kill and reap the active child. Partial conversions and failed reports are retained under ignored
`staging/validation/`, and interrupted candidates can resume in new attempt directories.

Converted output is limited to 64 MiB during writing and reports to 2 MiB. Before launching a worker,
the staging budget must have 66 MiB available to reserve those maxima. This conservative reservation
can defer a small build when storage is nearly full; preserved originals are never deleted to make room.
Run file and time budgets also apply. No schematic files or validation artifacts are committed or uploaded by CI.

Every validation records source SHA-256, converter identity, validator/dependency identity, report hash
and converted hash. Verified successful results can be reused across duplicate source references.
Code or parser-dependency changes queue fresh validation without removing older reports; cached reports
and conversions are verified before reuse. Failed validations require `retry-validation CANDIDATE_ID`.
Schema v3 records validation history separately from collection attempts and backs up earlier databases.

Measured counts of known vanilla redstone components produce an explicitly unconfirmed suggestion
and a recommendation to exclude matching builds from initial house datasets pending human review.
The screen does not simulate circuits or prove absence; it counts across retained regions without
flattening overlaps. Human classifications, source permissions, visual approvals and training admission
remain separate. Passing file checks is not visual approval or proof of design quality.

## Local reviewer handoff

Run the reviewer on the same machine as the collector, using the normal local launch instructions.
After `run`, `collect` and `validate`, execute:

```bash
.venv/bin/python -m minebot handoff --reviewer-url http://127.0.0.1:8765
```

Use the same `--data-dir` before each command if the collection is stored elsewhere. Handoff accepts
only loopback HTTP origins, bypasses environment proxies, sends no GitHub credential and follows no
redirects. The collector never starts a reviewer Service or opens its database. The reviewer must be
running for delivery; unavailable delivery records a one-minute retry deferral and preserves staging.
`retry-handoff CANDIDATE_ID` clears a local delivery failure/deferral after its cause is resolved.
Dismissed candidates, failed validation and stale validation are not delivered. File, request and time
budgets and collector pause apply to delivery. In-flight interruption is safe to retry.

The API checks candidate/repository/revision/path identity, exact byte size, Git blob SHA and SHA-256
before importing. Pinned evidence is also checked and retained under the reviewer's managed storage.
A successful receipt records the build ID in collector state. A lost receipt can be retried: the
reviewer deduplicates originals by hash and source references by manifest, preserving human labels,
review history, source-field edits and dataset memberships. It creates its own conversion job instead
of trusting a remote approval. Everything new starts pending, with unknown usage and unconfirmed labels.

The **Discovery** tab lists received source references, collector validation status, evidence downloads
and **Open for review**. Its local collector panel now includes pause/resume, stage counts, recent runs,
paginated candidate search and state filters, collection/validation/delivery retries, dismiss/restore,
failures, attempt history and staging storage usage. The ordinary review, library and dataset controls
continue to manage human decisions independently.

Codex Cloud cannot reach a Windows reviewer's `127.0.0.1`. This implementation supports local delivery;
cloud-to-local artifact transfer and any remote authenticated connection require a separate transport
implementation. GitHub source synchronization does not copy the ignored staging database or files.
Do not expose the current loopback reviewer to the public Internet. Scheduling and a live downloaded
build pilot remain unimplemented/unverified; generated fixtures cover this handoff milestone.

Handoff verification: the Python suite ran 136 tests with no failures and 12 optional resource/training skips. The TypeScript/Vite build passed. A generated-house fixture passed real loopback HTTP delivery and browser checks of Discovery, evidence links, opening review, recording approval and preserving it after reviewer restart. No downloaded source assets or real user reviewer state were used.


## Discovery collector controls

The reviewer connects to the separate collector database in `data/discovery/` by default. If the CLI
uses another `--data-dir`, set `MINEBOT_DATA_DIR` to the same directory **before starting the reviewer**.
The browser cannot change this path or run arbitrary commands. On Windows, for example:

```powershell
$env:MINEBOT_DATA_DIR = 'C:\MinebotData\discovery'
.\start-reviewer.ps1
.\.venv\Scripts\python.exe -m minebot --data-dir 'C:\MinebotData\discovery' run
```

Use distinct collector and reviewer directories. An existing current-schema collector can be observed
while a CLI worker is active; schema migration requires its exclusive process lock. Collector controls
never recover running jobs merely by opening the screen, never start a second reviewer Service and
never modify review decisions, classifications or dataset membership.

Pause is cooperative: the current request or worker checkpoint may finish before the lock is released.
The screen shows **Pause requested · worker stopping** while that happens. Resume enables subsequent
runs; it does not start a background run or install a schedule. Start bounded stages with the CLI until
scheduling is implemented. Retry actions queue work for the relevant next `collect`, `validate` or
`handoff` command. GitHub server deferrals remain in force after candidate retries.

Candidate changes use the collector's process lock and return HTTP 409 if a worker is active. Pause
remains available then. Dismissal preserves source/evidence files and prior delivery references;
restore returns the previous collection state. Held sources cannot be forced through collection by a
retry. Validation and delivery retries are offered only for relevant failed/deferred/interrupted states.
These operations preserve independently recorded reviewer approval and dataset membership.

The interface polls every four seconds. Counts and recent runs report persisted checkpoints rather
than an estimated completion percentage. Discovery saves counters at task boundaries, collection at
file boundaries, and validation/delivery at attempt boundaries. An abandoned running record is shown
as interrupted when no worker holds the lock, without modifying the original record. Stage history,
source hashes and unconfirmed suggestions remain separate from human review status.

Storage counts regular files under collector staging, including originals, evidence, validations and
temporary files; database/log storage and the reviewer's copies are outside this staging budget. It
samples at most every five seconds and excludes symlinks. Active writes can make the sample approximate.
No files are deleted automatically. Evidence downloads verify candidate membership, size and SHA-256
and use hash-derived paths. API responses expose source identities and stage summaries, not credential
values, local artifact paths or complete parser scene reports.

These controls manage the collector running on the same machine as the reviewer. They do not connect
Codex Cloud to an existing Windows installation, transfer ignored databases/assets or publish a service.

Collector-control verification: 148 Python tests completed with no failures and 12 optional resource/
training skips. The frontend build passed. A generated-fixture browser session checked pagination,
search, collection retry, pause during an active process lock, disabled candidate actions while active,
dismiss/restore, desktop/mobile layout and persistence of pause, dismissal and reviewer approval after
restart. The real loopback handoff passed and the browser reported no page errors. No GitHub discovery
requests or downloaded source assets were needed for these checks.
