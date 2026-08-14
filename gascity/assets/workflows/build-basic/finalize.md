Finalize the `build-basic` workflow.

Summarize requirements, implementation-plan, design-review, create-beads,
implementation, and review artifacts. Record the final outcome, artifact paths,
and remaining follow-up beads on the workflow root bead.

The build-basic result must be integrated into the canonical integration
worktree before finalization. Require `gc.build.integration_status=integrated`,
an absolute `gc.build.integration_work_dir`, and exact
`gc.build.integration_branch` and `gc.build.integration_commit` metadata.
Verify that the recorded commit is the integration worktree's current `HEAD`
and that the implementation summary and review artifacts cover that commit.
Missing, stale, or scattered per-item-only integration evidence is a blocked
build, never an approved partial result.

Write the final report, normally `factory-run.md`, at the path recorded on the
workflow root bead as `gc.build.final_report_path`. The artifact must be Markdown with YAML front
matter, not JSON. Its front matter must declare
`schema: gc.build.final-report.v1`, the workflow id/formula, the methodology
pack/name, the producer formula/stage/attempt, `status`, and `trace` with
upstream and coverage entries. Include a Markdown coverage table whose
ID/status pairs exactly match `trace.coverage`.
The validator only recognizes a Markdown table with an `ID` column and a
`Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

Before writing `factory-run.md`, ensure the canonical implementation summary
exists at the path recorded on the workflow root bead as
`gc.build.implementation_summary_path`, normally `implementation-summary.md`.
If that path is missing, absent on disk, or not a valid
`gc.build.implementation-summary.v1` artifact, synthesize the canonical
`implementation-summary.md` from closed implementation source anchors and their
recorded per-item `gc.implementation.summary_path` values. The synthesized
artifact must be Markdown with YAML front matter, schema
`gc.build.implementation-summary.v1`, the same trace shape and `ID`/`Status`
coverage matrix described here, and these sections:

- Summary
- Intended Behavior
- Changed Files
- Verification
- Remaining Risks

Record the canonical path on the workflow root bead before validating or
writing the final report. Use
`gc bd update "<workflow-root-id>" --set-metadata "gc.build.implementation_summary_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.final-report.v1`
- `workflow: {id: <workflow-root-id>, formula: build-basic}`
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: build-basic, stage: finalize, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For upstream build artifacts, use their recorded paths and scheme-qualified
  hashes such as `sha256:<digest>` or `git:<revision>`. For convoy or bead
  inputs, use `path: beads/<bead-id>` and `hash: bead:<bead-id>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.
- Do not create any additional Markdown table with both an `ID` column and a
  `Status` column unless it repeats the exact same coverage ID/status pairs.
  For requirement or artifact summaries, use different column names such as
  `Requirement` and `Result`, or use `covered` as the status for every covered
  requirement.

Keep it short and useful for a first-time factory user. Include the required
schema sections:

- Summary
- Outcome
- Artifacts
- Remaining Risks

In those sections, include:

- methodology: build-basic starter factory
- requirements, plan, decomposition, implementation, and review artifact paths
- implementation convoy id
- review lanes that ran
- proof commands or test summaries that were recorded
- publish outcome
- next human action

Record the final report path on the workflow root bead as both
`gc.build.final_report_path=<path>` and `gc.build.factory_run_path=<path>`.
Use `gc bd update "<workflow-root-id>" --set-metadata "gc.build.final_report_path=<absolute path>" --set-metadata "gc.build.factory_run_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.
After the final report validates locally and before closing this step, send one
terminal notification unless `{{notify}}` is `none`. The message must include
the workflow root id, terminal outcome, final report path, integrated branch and
commit, every remaining blocker id (or `none`), and an exact restart command
(or `none`). Use:

`gc mail send "{{notify}}" --subject "Build <workflow-root-id>: <terminal-outcome>" --message "workflow root id: <id>; terminal outcome: <outcome>; final report: <path>; integrated branch: <branch>; integrated commit: <commit>; remaining blocker ids: <ids-or-none>; restart command: <command-or-none>" --notify`

Guard retries with `gc.build.terminal_mail_sent=true` on the workflow root: do
not send when it is already true, and set it only after the send succeeds. A
mail failure is a stage failure; do not silently close the workflow.

Before closing this step, set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.

Do not publish from this step.

Artifact validation: this stage is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.final_report_path` against schema `gc.build.final-report.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
