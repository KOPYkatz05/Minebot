# Codex Cloud setup

This repository contains the install script and project instructions for a Codex Cloud environment.
Publishing repository files does **not** create or publish an account-level Codex environment.

In Codex on web or desktop:

1. Select **Work in > Cloud > Select environment > Create environment** (or Settings > Codex Cloud > Environments).
2. Select `KOPYkatz05/Minebot`, branch `main`, and start setup.
3. Request Python 3.12 and install with `bash scripts/setup-cloud.sh`. Run the offline test suite; no GitHub token is needed for setup or tests.
4. Keep the environment private. Package-manager access is sufficient for installation. For live discovery, also allow HTTPS GET access to `api.github.com`.
5. For live discovery only, configure `GITHUB_TOKEN` through the environment's credential settings for `api.github.com`. Do not paste it into a task prompt, repository file or script. Network-secret placeholders are acceptable: the client only sends this value in an Authorization header.
6. Review the setup report and select **Publish**. Confirm **Environment published**, then start a new task using this environment.

Verify the active task with `.venv/bin/python -m minebot check-github`. For a small metadata-only
readiness check, use `.venv/bin/python -m minebot --data-dir data/probe probe --request-budget 10`.
Authentication consumes one of those 10 requests. These commands do not download schematics and
never display the credential. If the active runtime still reports no secret binding or API host access
after configuration changes, apply the changes or start a task with the updated environment; saved
configuration alone does not prove the current process has received it.

Suggested first cloud task:

> Read AGENTS.md and docs/PLAN.md. Run the tests and inspect the implemented discovery/collection commands. Continue milestone 4 only when the original local parser/conversion adapters and their interface are available. Keep all candidates unapproved and usage permission unknown. Open a PR with the validation results.

This is a development environment, not an always-running collector host. Daily production scheduling,
durable deployment storage, and access to the original Windows local reviewer remain separate work.
Codex Cloud cannot reach the Windows reviewer's 127.0.0.1 directly. Do not expose that service publicly;
plan an explicit artifact-transfer mechanism before cloud-to-local handoff.

Official guide: https://learn.chatgpt.com/docs/environments/cloud-environments
