#!/usr/bin/env bash
# Shared helper: materialize a git object set into a disposable directory so
# validation reads committed content instead of the working tree.
#
# The working tree can differ from what git will actually record: a file may be
# staged clean and then edited, or an untracked file may sit beside a tracked
# one. Validating the worktree therefore certifies content that no commit will
# ever contain. Every content check that gates an object set must read the
# object set.
#
# Ambient exception: paths git deliberately ignores (see `question-packs/*/` in
# .gitignore) can never appear in any object set, yet the pack linter resolves a
# sibling `_course.json` for course metadata. Those ignored paths are overlaid
# from the working tree and are explicitly NOT part of the validated set.

# Materialize the staged tree (the exact content `git commit` would record).
snapshot_index() {
  local dest=$1
  [[ -d "$dest" ]] || return 1
  git checkout-index -a --prefix="$dest/"
  overlay_ignored_context "$dest"
}

# Materialize a pushed commit's tree (the exact content the remote would gain).
snapshot_commit() {
  local rev=$1 dest=$2
  [[ -d "$dest" ]] || return 1
  git archive --format=tar "$rev" | tar -x -C "$dest"
  overlay_ignored_context "$dest"
}

# Copy gitignored course content into the snapshot as ambient authoring
# context. These files are never committed and are never validated; they exist
# only so the linter can resolve course metadata for a staged pack.
overlay_ignored_context() {
  local dest=$1 path
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    mkdir -p "$dest/${path%/*}"
    # Never let an ignored file mask a path that the object set already
    # provides; the object set always wins.
    [[ -e "$dest/$path" ]] || cp "$path" "$dest/$path"
  done < <(git ls-files --others --ignored --exclude-standard -- 'question-packs/' || true)
}
