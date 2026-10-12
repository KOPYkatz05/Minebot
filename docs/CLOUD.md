# Codex Cloud setup

This repository contains the install script and project instructions for a Codex Cloud environment.
Publishing repository files does **not** create or publish an account-level Codex environment.

In Codex on web or desktop:

1. Select **Work in > Cloud > Select environment > Create environment** (or Settings > Codex Cloud > Environments).
2. Select `KOPYkatz05/Minebot`, branch `main`, and start setup.
3. Request Python 3.12 and Node.js 22 and install with `bash scripts/setup-cloud.sh`. This installs core/reviewer packages, builds the React/Three.js frontend from the lockfile, and runs the offline test suite. No GitHub token is needed for setup or tests; PyTorch and game-resource checks are optional.
4. Keep the environment private. Package-manager access is sufficient for installation. For live discovery, also allow HTTPS GET access to `api.github.com`.
5. For live discovery only, configure `GITHUB_TOKEN` through the environment's credential settings for `api.github.com`. Do not paste it into a task prompt, repository file or script. Network-secret placeholders are acceptable: the client only sends this value in an Authorization header.
6. Review the setup report and select **Publish**. Confirm **Environment published**, then start a new task using this environment.

Suggested first cloud task:

> Read AGENTS.md, docs/PLAN.md and docs/ADAPTERS.md. Run the portable tests and inspect the existing collector and isolated validation commands. Continue milestone 5 with an idempotent reviewer API handoff that preserves human decisions, stages safely when offline and never writes reviewer tables from the collector. Keep all candidates unapproved and usage permission unknown. Open a PR with the validation results.

For a new task with the GitHub credential applied, verify it with `minebot check-github`, then run
`minebot --data-dir data/probe probe --request-budget 10`. Authentication counts toward the total
10-request budget. Both commands omit the credential and profile contents; the probe is metadata-only.
Saved environment changes do not update the credential bindings of a task that is already running.

The source checkout now includes tools/, reviewer/, project.json, annotations and all tests. The original Windows datasets and local game cache remain excluded. See docs/ADAPTERS.md for exact parser and conversion entry points.

This is a development environment, not an always-running collector host. Daily production scheduling,
durable deployment storage, and access to the original Windows local reviewer remain separate work.
Codex Cloud cannot reach the Windows reviewer's 127.0.0.1 directly. Do not expose that service publicly;
plan an explicit artifact-transfer mechanism before cloud-to-local handoff.

Official guide: https://learn.chatgpt.com/docs/environments/cloud-environments
