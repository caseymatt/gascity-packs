This is the `build-from-review-base` finalize stage.

Synthesize the continuation result from the prerequisite artifacts,
implementation evidence, review reports, fix attempts, drift checks, and
publish intent.

The final report must state which continuation entrypoint started the run and
which upstream stages were skipped because their approved artifacts already
existed. Include the requirements path, plan path, plan-review path when
available, decomposition path, implementation convoy ID when available,
implementation evidence, review verdict, remaining risk, publish
authorization, and next action.

Do not close the workflow root with `gc.outcome=pass` when the review verdict
is `blocked` or `changes_required`, any implementation drain failed, required
implementation evidence is missing, or `gc.build.repair_status` is anything
other than `not_needed` or `approved`. In those cases, write a final report with
`status: blocked`, record `gc.outcome=fail`, `gc.build.status=blocked`,
`gc.failure_class` with the machine-readable reason, and preserve restart
metadata such as `gc.restart.entrypoint`, `gc.restart.reason`, and the relevant
artifact paths.

Only record a passing terminal outcome when all prerequisite artifacts exist,
implementation evidence is present, review is approved, and repair status is
`not_needed` or `approved`.

After the final report validates locally and before closing this step, send one
terminal notification unless `{{notify}}` is `none`. The message must include
the workflow root id, terminal outcome, final report path, every remaining
blocker id (or `none`), and an exact restart command (or `none`). Use:

`gc mail send "{{notify}}" --subject "Build <workflow-root-id>: <terminal-outcome>" --message "workflow root id: <id>; terminal outcome: <outcome>; final report: <path>; remaining blocker ids: <ids-or-none>; restart command: <command-or-none>" --notify`

Guard retries with `gc.build.terminal_mail_sent=true` on the workflow root: do
not send when it is already true, and set it only after the send succeeds. A
mail failure is a stage failure; do not silently close the workflow.

Record terminal outcome metadata on the workflow root before closing so the
publish step can safely no-op, push, open a PR, or block with an explicit
reason without changing the workflow outcome.

Artifact validation: this stage is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.final_report_path` against schema `gc.build.final-report.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
