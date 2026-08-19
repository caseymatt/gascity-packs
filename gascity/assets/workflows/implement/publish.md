
If push {{push}} or open_pr {{open_pr}} are explicit opt-ins, publish only
after the direct implementation summary has completed successfully. Resolve the
publish report from summary_path {{summary_path}} when set; otherwise read the
`gc.implementation.summary_path` value recorded by the summarize step in
workflow root metadata. Fail closed if no absolute summary report path is
available.

Direct implement does not run gap-analysis or review loops. Treat this as an
explicit caller authorization to publish the direct implementation result, and
use the same protected-branch, lease-safe push, sanitized PR title/body, and
collision checks as the pack publish helper.

Before closing, record the canonical delivery result on both the
workflow root bead and the claimed publish bead:

- `gc.build.publish_status=published|noop|failed`
- `gc.build.publish_action=push|pr|push_pr|noop|failed`
- `gc.build.publish_reason=<short machine-readable reason>`
- `gc.build.publish_recorded_at=<UTC RFC3339 timestamp>`
- `gc.build.publish_artifact_path=<absolute publish result path>`

A verified idempotent result where the expected commit, branch, or PR is
already published is `gc.build.publish_status=published`, with the action that
the caller authorized and a reason such as `already_published`. If neither push
nor open_pr is an explicit opt-in, do not mutate remotes; record
`gc.build.publish_status=noop`, `gc.build.publish_action=noop`, and
`gc.build.publish_reason=push=false_open_pr=false`, then close with
`gc.outcome=pass`. A failed remote or PR verification records
`gc.build.publish_status=failed`, `gc.build.publish_action=failed`, and
`gc.outcome=fail`. Do not substitute ad hoc `gc.publish.*` keys for this
contract.
