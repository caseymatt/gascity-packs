#!/usr/bin/env bash
set -euo pipefail

fail() {
  local code="$1"
  shift
  printf 'cleanup-worktree: %s\n' "$*" >&2
  exit "$code"
}

if [[ "$#" -ne 4 ]]; then
  fail 64 "usage: cleanup-worktree.sh <rig-root> <worktree> <owner-id> <promoted-sha>"
fi

rig_arg="$1"
worktree_arg="$2"
owner_id="$3"
promoted_sha="$4"

[[ "$owner_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail 64 "invalid owner id"
[[ "$promoted_sha" =~ ^[0-9a-fA-F]{40}$ ]] || fail 64 "promoted SHA must contain exactly 40 hexadecimal characters"

rig_root="$(git -C "$rig_arg" rev-parse --show-toplevel 2>/dev/null)" || fail 65 "rig root is not a Git worktree: $rig_arg"
rig_root="$(cd "$rig_root" && pwd -P)"
[[ -d "$worktree_arg" ]] || fail 66 "worktree does not exist: $worktree_arg"
worktree="$(cd "$worktree_arg" && pwd -P)"
owned_parent="$rig_root/worktrees"
[[ "$(dirname "$worktree")" == "$owned_parent" ]] || fail 67 "worktree is outside the formula-owned directory: $worktree"

worktree_name="$(basename "$worktree")"
case "$worktree_name" in
  "$owner_id"|"thunderdome-epoch-$owner_id"|"verify-$owner_id-r"[0-9]*|"repair-int-$owner_id-r"[0-9]*)
    ;;
  *)
    fail 68 "worktree name does not match owner $owner_id: $worktree_name"
    ;;
esac

common_dir() {
  local repo="$1"
  local path
  path="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null)" || return 1
  if [[ "$path" != /* ]]; then
    path="$repo/$path"
  fi
  (cd "$path" && pwd -P)
}

rig_common="$(common_dir "$rig_root")" || fail 69 "cannot resolve rig Git common directory"
worktree_common="$(common_dir "$worktree")" || fail 69 "path is not a registered Git worktree: $worktree"
[[ "$rig_common" == "$worktree_common" ]] || fail 69 "worktree belongs to another repository: $worktree"

if [[ -n "$(git -C "$worktree" status --porcelain=v1 --untracked-files=all)" ]]; then
  fail 70 "dirty worktree preserved: $worktree"
fi

git -C "$rig_root" cat-file -e "$promoted_sha^{commit}" 2>/dev/null || fail 71 "promoted commit is unavailable: $promoted_sha"
worktree_head="$(git -C "$worktree" rev-parse HEAD)"
if ! git -C "$rig_root" merge-base --is-ancestor "$worktree_head" "$promoted_sha"; then
  fail 72 "worktree HEAD $worktree_head is not reachable from promoted SHA $promoted_sha; preserved: $worktree"
fi

git -C "$rig_root" worktree remove "$worktree"
printf 'removed formula-owned worktree: %s (owner=%s head=%s)\n' "$worktree" "$owner_id" "$worktree_head"
