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

[[ "$owner_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
  fail 64 "invalid owner id"
[[ "$promoted_sha" =~ ^[0-9a-fA-F]{40}$ ]] ||
  fail 64 "promoted SHA must contain exactly 40 hexadecimal characters"
[[ -d "$rig_arg" ]] || fail 65 "rig root does not exist: $rig_arg"

gc_bin="$(command -v gc)" ||
  fail 69 "gc is unavailable; registered worktree preserved: $worktree_arg"
python_bin="$(command -v python3)" ||
  fail 69 "python3 is unavailable; cannot validate registry ownership; preserved: $worktree_arg"

rig_root="$(cd "$rig_arg" && pwd -P)" ||
  fail 65 "cannot resolve rig root: $rig_arg"
worktree="$(
  "$python_bin" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' \
    "$worktree_arg"
)" || fail 66 "cannot resolve worktree path: $worktree_arg"

if registry_json="$(
  cd "$rig_root"
  "$gc_bin" worktree list "$worktree" --json
)"; then
  :
else
  status=$?
  if [[ -n "$registry_json" ]]; then
    printf '%s\n' "$registry_json" >&2
  fi
  fail "$status" "registry lookup failed; worktree preserved: $worktree"
fi

if registered_id="$(
  printf '%s\n' "$registry_json" |
    "$python_bin" -c '
import json
import sys

expected_owner, expected_path = sys.argv[1:]
try:
    entries = json.load(sys.stdin)
except (TypeError, ValueError) as exc:
    raise SystemExit(f"cleanup-worktree: invalid registry JSON; preserved: {expected_path}: {exc}")
if not isinstance(entries, list) or len(entries) != 1:
    count = len(entries) if isinstance(entries, list) else "non-array"
    raise SystemExit(
        f"cleanup-worktree: registry lookup returned {count} entries; preserved: {expected_path}"
    )
entry = entries[0]
if not isinstance(entry, dict):
    raise SystemExit(f"cleanup-worktree: registry entry is not an object; preserved: {expected_path}")
actual_owner = entry.get("owner")
if actual_owner != expected_owner:
    raise SystemExit(
        f"cleanup-worktree: registry owner mismatch for {expected_path}; "
        f"expected {expected_owner!r}, got {actual_owner!r}; preserved"
    )
actual_path = entry.get("path")
if actual_path != expected_path:
    raise SystemExit(
        f"cleanup-worktree: registry path mismatch; expected {expected_path!r}, "
        f"got {actual_path!r}; preserved"
    )
registered_id = entry.get("id")
if not isinstance(registered_id, str) or not registered_id:
    raise SystemExit(f"cleanup-worktree: registry id is missing; preserved: {expected_path}")
print(registered_id)
' "$owner_id" "$worktree"
)"; then
  :
else
  status=$?
  fail "$status" "registry ownership validation failed; worktree preserved: $worktree"
fi

reclaim=(
  "$gc_bin" worktree reclaim "$registered_id"
  --promoted-sha "$promoted_sha"
  --json
)
if [[ "${GC_WORKTREE_CLEANUP_DRY_RUN:-0}" == "1" ]]; then
  reclaim+=(--dry-run)
fi

cd "$rig_root"
exec "${reclaim[@]}"
