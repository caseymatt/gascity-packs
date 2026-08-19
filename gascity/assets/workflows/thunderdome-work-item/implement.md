
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. For a
synthetic drain-unit convoy, the source anchor is the original drain member in
`gc.drain_member_id`, not the synthetic convoy id. Read `work_dir` from the source anchor, never read `work_dir` from the synthetic drain-unit convoy,
validate that it is an absolute existing git worktree, set `WORKTREE` to that
path, then `cd "$WORKTREE"` before reading or editing source files. If
`work_dir` is missing, invalid, or points at the launcher checkout, fail this step before editing.

Do not infer the source anchor from dependency ids such as the
`prepare-worktree` step. Read the claimed step bead's `gc.root_bead_id`, read
that do-work root with `gc bd show <root-bead-id> --json`, then read the root
metadata `gc.input_convoy_id`. Read that input convoy with `gc bd show
<input-convoy-id> --json`; if the JSON output is a one-element list, unwrap the
first element before reading metadata. If the input convoy has
`gc.synthetic_kind=drain-unit-convoy`, use its `gc.drain_member_id` as the
source anchor. Otherwise use the input convoy id as the source anchor. Then
read the source anchor and use only its `work_dir` metadata as `WORKTREE`.

`gc.work_dir` is the launcher rig root, not the implementation worktree. Use
`gc.work_dir` only later to run `.gc/scripts/checks/build-artifact-valid.sh`.
After resolving `WORKTREE`, run `cd "$WORKTREE"` and verify `pwd -P` equals
`$WORKTREE` before any source read, source edit, test, file hash, `git add`, or
`git commit`. If a command uses the launcher checkout path for source edits,
verification, hashes, or commits, the step is invalid and must fail.

Do not edit files in the launcher checkout. Implement only the owned source
anchor boundary, run sandboxed verification from inside the worktree, and make a
focused commit in the worktree. Leave the source anchor open; only verified
stable promotion may close it. Close only this implementation step when done.

Rust verification in this parallel item lane is limited to focused behavioral tests only.
Select the smallest test target and exact test name or module that
proves the owned acceptance behavior. An item worker must not run repository-wide
or compilation-only Cargo work, including `cargo test --workspace`, `cargo test --all`,
`cargo test --all-targets`, `cargo check`, `cargo build`, or `cargo clippy`,
or any repository wrapper that expands to those aggregate commands. Do not run a
union of other items' commands. Formatting, workspace checks, broad tests, and
the repository aggregate Rust gate belong exclusively to the serialized
`thunderdome-build` validation step after the drain barrier.

Before the first focused Cargo test, give this source anchor its own durable
on-disk target directory beside the isolated worktrees:

```sh
WORKTREES_DIR=$(dirname "$WORKTREE")
CARGO_TARGET_DIR="$WORKTREES_DIR/.cargo-targets/<source-anchor-id>"
mkdir -p "$CARGO_TARGET_DIR"
CARGO_TARGET_DIR=$(cd "$CARGO_TARGET_DIR" && pwd -P)
case "$CARGO_TARGET_DIR" in /tmp|/tmp/*) exit 1 ;; esac
export CARGO_TARGET_DIR
```

Substitute the resolved source anchor ID rather than the literal placeholder.
Hard-fail if canonicalization escapes the source-owned
`.cargo-targets/<source-anchor-id>` directory, if it resolves under `/tmp`, if
it is not writable on disk, or if another item or aggregate gate uses the same
directory. Ignore any inherited `CARGO_TARGET_DIR`; never point two workers at a
shared live `target` tree or discover a target under a sibling worktree. A
shared sccache or immutable prewarmed base is permitted only when it is already
supplied through the installed pack or provider environment; consume that
contract unchanged. Do not invent cache environment variables, copy a mutable
target tree, or configure shared writable cache state in this workflow.

Record the canonical `CARGO_TARGET_DIR` and every focused test command with its
observed result in `## Verification`.

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

Artifact validation: this step is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the same validator locally from that rig root with `GC_BEAD_ID=<claimed-step-id> .gc/scripts/checks/build-artifact-valid.sh`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
