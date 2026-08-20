
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. For a
synthetic drain-unit convoy, the source anchor is the original drain member in
`gc.drain_member_id`, not the synthetic convoy id. Do not infer the source
anchor from dependency ids such as the `prepare-worktree` step. Read the
claimed step bead's `gc.root_bead_id`, read that do-work root with `gc bd show
<root-bead-id> --json`, then read root metadata `gc.input_convoy_id`. Read the
input convoy with `gc bd show <input-convoy-id> --json`; if the JSON output is a
one-element list, unwrap the first element before reading metadata. If it has
`gc.synthetic_kind=drain-unit-convoy`, use its `gc.drain_member_id`; otherwise
use the input convoy id.

Resolve the rig only from `GC_RIG_ROOT`: require an absolute canonical path and
set `RIG_ROOT=$(cd -- "$GC_RIG_ROOT" && pwd -P)`. Read
`gc.worktree.id`, `gc.worktree.path`, `work_dir`, `gc.work_dir`,
`gc.cargo_target_dir`, and `gc.cargo_home` from the source anchor. Set
`WORKTREE_ID` from `gc.worktree.id` and require it to equal
`<source-anchor-id>`. Never read these fields from a synthetic
drain-unit convoy and never derive the worktree or cache paths from the current
directory.

Run `gc worktree list "$WORKTREE_ID" --rig "$GC_RIG" --json` before any source
access. Hard-fail unless it returns an array containing exactly one entry for
this id. Require the entry's `id`, `owner`, `rig`, `rig_root`, and `attempt` to
equal `<source-anchor-id>`, `<source-anchor-id>`, `$GC_RIG`, `$RIG_ROOT`, and
`1`. Require registered `path` to equal each of `gc.worktree.path`, `work_dir`,
`gc.work_dir`, and `$RIG_ROOT/worktrees/$WORKTREE_ID`. Require registered
`cargo_target_dir` to equal `gc.cargo_target_dir` and
`$RIG_ROOT/worktrees/.cargo-targets/$WORKTREE_ID/attempt-1`. Require registered
`cargo_home` to equal `gc.cargo_home` and
`$RIG_ROOT/.gc/cache/cargo-home`. Missing, invalid, ambiguous, or mismatched
registry state must fail this step before editing.

Set `WORKTREE` to the registered `path`. Ignore inherited Cargo cache paths and
export the registered unique Cargo target and home before running any build or
test:

```sh
export CARGO_TARGET_DIR="<registered cargo_target_dir>"
export CARGO_HOME="<registered cargo_home>"
```

Require both to be canonical absolute paths and writable, then
`cd "$WORKTREE"` and verify `pwd -P` equals `$WORKTREE` before any source read,
source edit, test, file hash, `git add`, or `git commit`. If a command uses the
launcher checkout path for source work, or if either Cargo variable differs
from the registered entry, the step is invalid and must fail.

Do not edit files in the launcher checkout. Implement only the owned source
anchor boundary, run sandboxed verification from inside the registered
worktree with those exported cache paths, and make a focused commit there.
Leave the source anchor open for `close-source-anchor`; close only this
implementation step when done.

Write or update the task summary with these schema-required body sections,
using the exact `##` headings below in this order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

The `## Verification` section must include both the first verification command
and the final proof command, with the observed pass/fail result.

Write the summary as a `gc.build.implementation-summary.v1` artifact and record
its absolute path on the workflow root bead as `gc.implementation.summary_path`
before closing.
Include a Markdown coverage table. The validator only recognizes a table with
an `ID` column and a `Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.implementation-summary.v1`
- `workflow: {id: <workflow-root-id>, formula: <root-workflow-formula>}`
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: do-work, stage: implement, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For the source anchor bead, use `path: beads/<source-anchor-id>` and
  `hash: bead:<source-anchor-id>`. For changed files or upstream build
  artifacts, use repo-relative paths and scheme-qualified hashes such as
  `sha256:<digest>` or `git:<revision>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.

Artifact validation: this step is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, use the already validated `RIG_ROOT` derived only from `GC_RIG_ROOT`, then run the validator locally as `GC_BEAD_ID=<claimed-step-id> "$RIG_ROOT/.gc/scripts/checks/build-artifact-valid.sh"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
