#!/usr/bin/env bash
# Wire repo-local git hooks (core.hooksPath is local to this clone, not global).
# Usage: ./scripts/hooks/install.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

git config core.hooksPath scripts/hooks

echo "Installed git hooks for $(basename "$ROOT"):"
echo "  core.hooksPath = scripts/hooks  (repo-local — does not affect other clones)"
echo "  pre-commit     → lint staged packs + certification_fresh (no LLM)"
echo "  pre-push       → npm test"
