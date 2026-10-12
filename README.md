# Minebot

Automated GitHub discovery for a reproducible Minecraft building dataset. **The first working
milestone discovers metadata; downloading, conversion validation and reviewer integration are next.**
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
.venv/bin/python -m minebot run
.venv/bin/python -m minebot candidates --limit 100 --offset 0
.venv/bin/python -m minebot pause
.venv/bin/python -m minebot resume
.venv/bin/python -m minebot dismiss CANDIDATE_ID
.venv/bin/python -m minebot restore CANDIDATE_ID
.venv/bin/python -m unittest discover -s tests -v
```

`run` needs `GITHUB_TOKEN` provided through environment credentials; never put a token in configuration.
All other commands and tests work offline. Use `--data-dir PATH` **before** the command to isolate runs.
The collector uses a separate SQLite database under ignored `data/discovery/`. Back it up to preserve
configuration, candidates, repository identities, pinned traversal work and run history.

Public-only repository search and watched repositories are inspected at pinned commits. Candidate
versions retain stable repository ID, revision, path, Git blob SHA and source URL. Repeated discoveries
are idempotent; later commits retain separate source references. House filenames receive an explicitly
unconfirmed suggestion. Schematics in other formats, archives and submodules are marked unsupported.

Requests are serial and bounded; rate limits and temporary failures defer work instead of blocking
in a sleeping process. Interrupted traversal resumes from its saved position. Search caps and incomplete
results are visible in run warnings. **This is not an exhaustive index of GitHub.**

The download/storage settings are reserved for the next milestone and do not imply downloads already
occur. License evidence is explicitly `not_collected_yet`; asset authors and permitted usage stay unknown.
No live source assets have been collected or trained on as part of repository setup.

See [the accepted plan and remaining milestones](docs/PLAN.md).
