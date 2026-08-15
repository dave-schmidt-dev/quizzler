#!/usr/bin/env bash
# Wire repo-local git hooks (core.hooksPath is local to this clone, not global).
# Usage: ./scripts/hooks/install.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

git config core.hooksPath .githooks

echo "Installed git hooks for $(basename "$ROOT"):"
echo "  core.hooksPath = .githooks  (repo-local — does not affect other clones)"
echo "  pre-commit     → pack + SwiftLint/Periphery checks"
echo "  pre-push       → native aggregate + npm test"
