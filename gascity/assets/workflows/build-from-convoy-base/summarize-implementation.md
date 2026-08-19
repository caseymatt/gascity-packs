Write the canonical build implementation summary for this convoy continuation.

This continuation stage converts the completed implementation drain into one integrated source branch and the root implementation-summary artifact consumed by review.

The implementation drain may write one or more per-item summaries from source worktrees. This stage converts that implementation evidence into the root workflow artifact recorded as `gc.build.implementation_summary_path`, normally `implementation-summary.md` under the build artifact root.

Resolve the workflow root bead and artifact root from root metadata. If `gc.build.implementation_summary_path` is empty, derive an absolute path under `gc.var.artifact_root` or `gc.build.artifact_root` as `implementation-summary.md`, then record it on the workflow root:

`gc bd update "<workflow-root-id>" --set-metadata "gc.build.implementation_summary_path=<absolute path>"`

Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON object.

Collect the closed implementation source anchors and drain child workflows from the implementation convoy. Read their recorded implementation summary paths from `gc.implementation.summary_path`, `gc.build.implementation_summary_path`, or `gc.var.summary_path`. Include those summaries as upstream evidence. If an item summary is missing coverage IDs that appear in the requirements artifact, read the requirements, decomposition, review context, and verification evidence before writing the canonical root summary. The canonical summary must cover all accepted requirement IDs that the build finalized.

Integrate every successful per-item commit before writing the aggregate artifact. The canonical integration worktree is the absolute `gc.work_dir` on the workflow root. It must be an existing Git worktree on a named branch. Refuse to integrate into a detached HEAD or a worktree with unrelated tracked changes. Collect each source anchor's verified `gc.implementation.commit` in decomposition order. For each commit:

1. Verify the commit exists and its per-item summary passed.
2. Skip it only when it is already an ancestor of the canonical integration worktree's `HEAD`.
3. Otherwise, `cherry-pick` it into the canonical integration worktree.
4. Resolve a conflict only when the approved plan and source summary make the correct result unambiguous; then rerun the affected proof. Otherwise abort the cherry-pick and stop as blocked.

After all commits are integrated, run the aggregate proof commands from the canonical integration worktree. Record its absolute path, current branch, and exact `HEAD` on the workflow root as `gc.build.integration_work_dir`, `gc.build.integration_branch`, `gc.build.integration_commit`, and set `gc.build.integration_status=integrated`. The implementation summary's changed files and verification results must describe this integrated `HEAD`, not a collection of isolated worktrees.

If any commit is missing, conflicts cannot be resolved, or aggregate proof fails, set `gc.build.integration_status=blocked`, `gc.blocked_reason`, and restart metadata on the workflow root, set this step to `gc.outcome=fail`, and do not write an approved summary. Before failing, notify `{{notify}}` unless it is `none`; include the workflow root id, failing commit or proof, remaining blocker ids, and exact restart command. A scattered set of successful per-item worktrees is not a completed build.

Write the artifact as Markdown with YAML front matter, not JSON. Use mapping objects for front matter; do not use scalar shortcuts such as `workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.implementation-summary.v1`
- `workflow: {id: <workflow-root-id>, formula: <root-workflow-formula>}`
- `methodology: {pack: <pack-name>, name: <build-formula>}`
- `producer: {formula: <build-formula>, stage: summarize-implementation, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use `id`/`title`/`type` entries as the upstream shape.
- For upstream build artifacts or implementation summaries, use their recorded paths and scheme-qualified hashes such as `sha256:<digest>`. For convoy or bead inputs, use `path: beads/<bead-id>` and `hash: bead:<bead-id>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied requirements; do not use `approved` in `trace.coverage[].status` or the Markdown coverage table.

Include a Markdown coverage table whose ID/status pairs exactly match `trace.coverage`. The validator only recognizes a Markdown table with an `ID` column and a `Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

The body must include these schema-required sections:

- Summary
- Intended Behavior
- Changed Files
- Verification
- Remaining Risks

In those sections, include the implementation convoy id, source anchor ids, per-item summary paths, changed files, first verification commands, final proof commands, observed pass/fail results, and remaining risks. Keep the root summary concise, but do not omit accepted requirement IDs.

Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the same validator locally from that launcher rig root:

`GC_BEAD_ID=<claimed-step-id> .gc/scripts/checks/build-artifact-valid.sh`

Fix every reported validation error before setting `gc.outcome=pass`. Then set the claimed step outcome with `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, and close with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass `--metadata` or `--set-metadata` to `gc bd close`.

Artifact validation: this stage is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.implementation_summary_path` against schema `gc.build.implementation-summary.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
