#!/bin/bash
# Pinned consumer entrypoint for `bws-secret-exec` — runs the Layer-C pack gate
# with an API-key-bearing critic provider.
#
# WHY THIS FILE EXISTS
#   The broker pins ONE executable per consumer by sha256 and injects the secret
#   into that process's environment only. It cannot pin `python3` usefully (that
#   would authorize every Python program on the machine), so the pinned thing has
#   to be a small, fixed-purpose script. This is that script.
#
# WHAT IT GUARANTEES
#   * The key exists only in this process tree's environment. It is never
#     written to disk, never echoed, never passed as an argument (argv is world-
#     readable via `ps`).
#   * `exec` replaces this shell, so no wrapper process lingers holding the key.
#   * The only program this can launch is scripts/verify_pack.py, resolved
#     relative to THIS file. It is not a general command runner.
#
# USAGE (from the human's terminal or an agent tool call — same command):
#   bws-secret-exec quizzler-critic -- scripts/critic-with-key.sh \
#       question-packs/<course>/<pack>.json --panel deepseek,claude
#
# Never use `bws-run`, `bws-get`, or `bws secret get` to supply this key: they
# print secret values to stdout. See docs/CRITIC_PROVIDERS.md.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -eq 0 ]; then
  echo "usage: critic-with-key.sh <pack.json> [verify_pack.py options]" >&2
  echo "  e.g. critic-with-key.sh question-packs/c/p.json --panel deepseek,claude" >&2
  exit 1
fi

# Fail early and clearly if the broker did not inject a key, rather than letting
# the run get all the way to a provider call and report a 401.
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${QUIZZLER_OPENAI_API_KEY:-}" ]; then
  echo "error: no critic API key in the environment." >&2
  echo "Run this through the broker:  bws-secret-exec quizzler-critic -- $0 ..." >&2
  exit 1
fi

exec python3 "$DIR/verify_pack.py" "$@"
