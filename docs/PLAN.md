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
   This release deliberately stops before downloading source assets.
3. **Next:** fetch license/README/attribution evidence at the pinned revision, record evidence hashes,
   identify held sources, stream immutable files through temporary paths, verify Git blob identity
   and byte size, compute SHA-256, detect LFS pointers, deduplicate artifacts across references,
   retain retry times and failed attempts, enforce download/storage budgets, rotate local logs.
4. Isolated parser and independent conversion comparisons using the existing project adapters now included in this repository (see docs/ADAPTERS.md).
   Preserve block states, signed regions and metadata. Keep failed reports; generate measured redstone
   suggestions without changing human classifications. No GPU training dependencies in the collector.
5. Idempotent API handoff to the local reviewer; stage when offline. Add Discovery endpoints and tab,
   source evidence display, links to review, pause/retry/dismiss/restore, progress, failures and storage.
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
