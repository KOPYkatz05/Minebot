#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
command -v node >/dev/null || { echo "Node.js 22 is required to build the reviewer"; exit 1; }
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("Node.js 22+ required")'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-reviewer.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
npm exec --yes --package=pnpm@10.6.5 -- pnpm --dir reviewer/frontend install --frozen-lockfile --ignore-scripts
npm exec --yes --package=pnpm@10.6.5 -- pnpm --dir reviewer/frontend run build
.venv/bin/python tools/project.py doctor
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m minebot --help
