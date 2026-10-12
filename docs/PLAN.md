# Automated GitHub build discovery bot

## Accepted defaults

Public GitHub repository search plus named watched repositories. All `.litematic` building types,
with house prioritization. Automatic collection and validation; human approval of usage, visual
quality, confirmed labels and training inclusion. Unknown usage may be staged for inspection;
explicit collection prohibitions or conflicting evidence hold a source.

Daily local collection at 9 a.m. plus catch-up at Windows logon was the original production target.
Development now targets this repository in Codex Cloud. Cloud development does not change the
production hosting decision: a persistent cloud collector/sync service needs a separately agreed design.

Budgets: 100 new files and 1 GiB downloads per run, 10 GiB staging storage, 500 API requests,
two hours per run, one serial request at a time. Keep the existing reviewer's 64 MiB compressed,
256 MiB decompressed and 5-million-voxel limits; five-minute validation subprocess timeout.

## Milestones

1. **Implemented:** cross-platform Python package, validated persisted config, SQLite versioning,
   process lock, CLI, run records, offline tests, Codex setup script and Linux/Windows CI.
2. **Implemented metadata portion:** repository search with pagination, exclusions, watched repos,
   pinned commits and tree traversal, durable traversal positions, public-only enforcement,
   explicit incomplete-coverage reporting, unsupported-format records and filename suggestions.
   Discovery remains a separate metadata-only command.
3. **Implemented:** separate `collect` command with root/ancestor license, README and attribution
   evidence pinned to the source commit and hashed; conservative source holds; streamed originals
   through temporary paths; verified Git blob identity and exact byte size; SHA-256 artifact storage
   and deduplication across source references; retained LFS pointers without external fetches;
   durable attempts, retry times and shared GitHub deferrals; transfer/storage/time/file/request budgets;
   rotating local logs; automatic backed-up schema v2 migration. Evidence scanning is bounded and
   heuristic, not a determination of asset license applicability. Everything remains unvalidated,
   usage unknown and unapproved. Offline synthetic tests pass; live collection has not been piloted.
   Credential-safe `check-github` and a metadata-only `probe` command support readiness verification
   with a total budget of at most 10 requests including authentication. On 2026-10-12 the updated
   environment successfully authenticated. Three API requests checked authentication and remote
   branch/commit metadata; a separate seven-request probe authenticated again and recorded two public
   repositories, zero candidates and 103 pending work items. The probe stopped at its request budget
   and reported capped/incomplete search coverage. No schematics were downloaded; this verifies live
   authentication and bounded metadata access, not collection, conversion or reviewer integration.
4. **Implemented:** offline isolated validation through the published adapters in docs/ADAPTERS.md.
   Preserve block states, signed regions and metadata. Keep failed reports; generate measured redstone
   suggestions without changing human classifications. No GPU training dependencies in the collector.
   The worker uses bounded loading, archival verification, separately loaded comparisons and Litemapy
   checks of both originals and conversions. Five-minute timeout, bounded outputs, Linux memory guard,
   retained failed attempts, source/converter identities, duplicate reuse and stale-validation detection
   are implemented. Measured redstone IDs produce unconfirmed suggestions only. Schema v3 records
   validation separately; visual quality, provenance and training permission still require human review.
5. **Handoff implemented:** `handoff` transfers verified originals and hashed pinned evidence through
   the loopback reviewer API, retains staged files and retry state when unavailable, and verifies
   receipts. Duplicate delivery preserves human edits, decisions and dataset membership. The reviewer
   independently processes imported originals. Discovery endpoints and an initial received-sources
   tab show pinned source identities, downloadable evidence and links to review.
   **Controls implemented:** local collector pause/resume, paginated candidate search and state filters,
   collection/validation/delivery retries, dismiss/restore, stage counts, recent run checkpoints,
   failure/attempt history and bounded-frequency staging storage usage. Candidate mutations hold the
   collector process lock; pause remains available while a worker runs. The reviewer reads collector
   state without recovering worker records or migrating a database during an active run.
   Preserve human edits on duplicate imports. Approval, usage and dataset admission remain manual.
6. Windows scheduler setup/removal with hidden interactive-user execution, no stored password,
   no computer wake; bounded live pilot and browser verification. If runtime moves to a cloud host,
   first define deployment, storage, authentication and artifact transfer to the local reviewer.

## Required checks

Offline tests for pagination, truncated trees, changed revisions, duplicate references, corrupt and
oversized files, LFS pointers, failed/broken conversions, rate limits, authentication errors, interrupts,
storage budgets and repeated delivery. Browser checks for persistent decisions, independent dataset
membership, unchanged human labels on duplicates and blocked unknown-permission training exports.
Inspect original/converted pairs and publish actual live-pilot yield and limitations before scaling.

## Exclusions

Hugging Face, cloud production hosting, additional schematic parsers, broad scraping, archive extraction,
external download links, automatic training admission and automated training runs are not in v1.
The Minecraft geometry exporter remains separate. No downloaded asset license is inferred from a badge.
