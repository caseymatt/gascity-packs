#!/usr/bin/env bash
set -euo pipefail

# Generic producer-stage build-artifact validation gate.
#
# The checked formula step names its artifact contract in step metadata:
#   gc.build.artifact_schema    - expected schema id (e.g. gc.build.requirements.v1)
#   gc.build.artifact_path_keys - comma-separated workflow-root metadata keys;
#                                 the first non-empty value is the artifact path
#
# The step bead (and the ralph control bead cloned from it) carries that
# metadata, so this script reads $GC_BEAD_ID, resolves the workflow root via
# gc.root_bead_id, resolves the artifact path, and validates the artifact with
# the shared base validator. Deterministic contract failures print
# machine-readable repair context on stderr and exit 1. Store reads instead
# preserve a bounded diagnostic and exit EX_TEMPFAIL so they can be retried
# without spending an artifact-repair attempt. This gate never prompts.

fail() {
  echo "build-artifact-check: $*" >&2
  exit 1
}

readonly EX_TEMPFAIL=75
readonly STORE_DIAGNOSTIC_LIMIT=2048

show_bead_json() {
  local bead_id="$1"
  local stderr_file output detail

  stderr_file="$(mktemp "${TMPDIR:-/tmp}/gc-build-artifact-show.XXXXXX")" ||
    fail "could not create temporary file for gc bd show diagnostics"
  if output="$(gc bd show "$bead_id" --json 2>"$stderr_file")"; then
    rm -f "$stderr_file"
    printf '%s' "$output"
    return 0
  fi

  # gc/bd may include terminal control bytes and an unbounded backend error
  # stream. Keep a printable, single-line prefix: enough to diagnose the real
  # store failure without letting it flood gc.attempt_log or control a terminal.
  detail="$(
    LC_ALL=C head -c "$STORE_DIAGNOSTIC_LIMIT" "$stderr_file" |
      LC_ALL=C tr '\r\n\t' '   ' |
      LC_ALL=C tr -cd '[:print:]'
  )"
  rm -f "$stderr_file"
  if [ -n "$detail" ]; then
    printf 'build-artifact-check: gc bd show %s failed: %s\n' "$bead_id" "$detail" >&2
  else
    printf 'build-artifact-check: gc bd show %s failed with no diagnostic\n' "$bead_id" >&2
  fi
  exit "$EX_TEMPFAIL"
}

BEAD_ID="${GC_BEAD_ID:-}"
[ -n "$BEAD_ID" ] || fail "GC_BEAD_ID is required"
command -v gc >/dev/null 2>&1 || fail "gc is required on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 is required on PATH"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

metadata_value() {
  # metadata_value <json> <key> -> prints metadata[key] or empty
  printf '%s' "$1" | python3 -c '
import json
import sys

key = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
if isinstance(data, list):
    data = data[0] if data else {}
if not isinstance(data, dict):
    print("")
    raise SystemExit(0)
metadata = data.get("metadata") or {}
value = metadata.get(key, "") if isinstance(metadata, dict) else ""
print(value if isinstance(value, str) else "")
' "$2"
}

SHOW_JSON="$(show_bead_json "$BEAD_ID")"

SCHEMA="$(metadata_value "$SHOW_JSON" "gc.build.artifact_schema")"
PATH_KEYS="$(metadata_value "$SHOW_JSON" "gc.build.artifact_path_keys")"
[ -n "$SCHEMA" ] || fail "step metadata gc.build.artifact_schema is missing on $BEAD_ID"
[ -n "$PATH_KEYS" ] || fail "step metadata gc.build.artifact_path_keys is missing on $BEAD_ID"

ROOT_ID="$(metadata_value "$SHOW_JSON" "gc.root_bead_id")"
ROOT_JSON="$SHOW_JSON"
if [ -n "$ROOT_ID" ] && [ "$ROOT_ID" != "$BEAD_ID" ]; then
  ROOT_JSON="$(show_bead_json "$ROOT_ID")"
fi

ARTIFACT_PATH=""
RESOLVED_KEY=""
IFS=',' read -r -a KEYS <<<"$PATH_KEYS"
for key in "${KEYS[@]}"; do
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
  [ -n "$key" ] || continue
  value="$(metadata_value "$ROOT_JSON" "$key")"
  if [ -n "$value" ]; then
    ARTIFACT_PATH="$value"
    RESOLVED_KEY="$key"
    break
  fi
done
[ -n "$ARTIFACT_PATH" ] || fail "no artifact path recorded on workflow root ${ROOT_ID:-$BEAD_ID}; tried metadata keys: $PATH_KEYS. The producing stage must record the resolved artifact path before closing."

case "$ARTIFACT_PATH" in
  /*) ;;
  *)
    # Formula artifact paths are rig-relative. A producer runs in a disposable
    # per-bead worktree, so GC_WORK_DIR points at the wrong place whenever the
    # runtime provides the durable rig root. Controller checks use
    # GC_BEADS_SCOPE_ROOT on some runtimes, while agent sessions use
    # GC_RIG_ROOT. Older controllers supply neither but execute the installed
    # check from <rig>/.gc/scripts/checks, which is another durable root
    # signal. Do not use that fallback for a source-tree script.
    ARTIFACT_ROOT="${GC_RIG_ROOT:-${GC_BEADS_SCOPE_ROOT:-${GC_DIR:-}}}"
    if [ -z "$ARTIFACT_ROOT" ]; then
      INSTALLED_RIG_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
      if [ -d "$INSTALLED_RIG_ROOT/.gc" ]; then
        ARTIFACT_ROOT="$INSTALLED_RIG_ROOT"
      fi
    fi
    if [ -n "$ARTIFACT_ROOT" ]; then
      ARTIFACT_PATH="$ARTIFACT_ROOT/$ARTIFACT_PATH"
    else
      [ -n "${GC_WORK_DIR:-}" ] || fail "artifact path $ARTIFACT_PATH from $RESOLVED_KEY is relative and no rig-root environment is set"
      ARTIFACT_PATH="$GC_WORK_DIR/$ARTIFACT_PATH"
    fi
    ;;
esac
[ -f "$ARTIFACT_PATH" ] || fail "artifact $ARTIFACT_PATH from $RESOLVED_KEY does not exist"

VALIDATOR=""
for candidate in \
  ${GC_WORK_DIR:+"$GC_WORK_DIR/gascity/assets/scripts/validate_build_artifact.py"} \
  "$SCRIPT_DIR/../validate_build_artifact.py"; do
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then
    VALIDATOR="$candidate"
    break
  fi
done
[ -n "$VALIDATOR" ] || fail "validate_build_artifact.py not found beside $SCRIPT_DIR or under GC_WORK_DIR"

VENDOR_DIR="$SCRIPT_DIR/../vendor"
[ -f "$VENDOR_DIR/yaml/__init__.py" ] ||
  fail "vendored PyYAML runtime not found under $VENDOR_DIR"

if OUTPUT="$(PYTHONPATH="$VENDOR_DIR" python3 -S "$VALIDATOR" --schema "$SCHEMA" --path "$ARTIFACT_PATH" 2>&1)"; then
  echo "build artifact valid: schema=$SCHEMA path=$ARTIFACT_PATH"
  exit 0
fi

echo "build-artifact-check: schema=$SCHEMA path=$ARTIFACT_PATH failed validation" >&2
printf '%s\n' "$OUTPUT" >&2
exit 1
