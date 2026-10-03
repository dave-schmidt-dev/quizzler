#!/usr/bin/env bash
# Shared helper: keep only the installable packs from a list of repo-relative paths.
#
# The rule lives once, in scripts/pack_discovery.py. It is read from the same
# object set the paths come from (the index, or a pushed commit), never from the
# working tree, so an edited local copy cannot change which files get gated.
#
#   filter_installable_packs <rev>   paths on stdin; ":" means the index.
#
# Fail closed: an absent predicate must stop the hook, not turn into "no packs
# staged". Call it in an assignment (`x=$(... | filter_installable_packs :)`) so
# `set -e` sees the failure.
filter_installable_packs() {
  local rev=$1 spec module_source where
  if [[ "$rev" == ":" ]]; then
    spec=":scripts/pack_discovery.py"; where="staged"
  else
    spec="$rev:scripts/pack_discovery.py"; where="pushed ($rev)"
  fi
  module_source=$(git show "$spec") && [[ -n "$module_source" ]] || {
    echo "hooks: scripts/pack_discovery.py is not in the $where object set" >&2
    return 1
  }
  python3 -c "$module_source"
}
