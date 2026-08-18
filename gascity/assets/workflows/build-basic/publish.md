Use the built-in Gas City publish flow.

Publish only the canonical integrated branch recorded on the workflow root bead.
Require `gc.build.integration_status=integrated`; verify
`gc.build.integration_branch` and the full `gc.build.integration_commit`
against the integration worktree immediately before pushing. Do not publish an isolated per-item
worktree or a set of scattered commits.

## Terminal delivery record

Create a publish result artifact under the workflow artifact root. Before
closing, record the same terminal result on both the workflow root bead and the
publish step:

- `gc.build.publish_status=published|noop|failed`
- `gc.build.publish_action=push|pr|push_pr|noop|failed`
- `gc.build.publish_recorded_at=<UTC RFC3339 timestamp>`
- `gc.build.publish_artifact_path=<absolute publish result path>`
- `gc.build.publish_reason=<short machine-readable reason>`
- `gc.build.publish_remote_status=<verified remote state>`
- `gc.build.published_commit=<full integration commit>`

For `push`, verify the remote branch resolves to
`gc.build.published_commit` after the push. For `pr` or `push_pr`, verify the
pull request exists for that commit and also record `pr_url` and `pr_number` on
the workflow root bead, the publish step, and the publish result artifact.
Publication is not complete merely because a local branch is green.

When both `push=false` and `open_pr=false`, record
`gc.build.publish_status=noop`, `gc.build.publish_action=noop`,
`gc.build.publish_reason=push=false_open_pr=false`,
`gc.publish_outcome=noop`, and `gc.publish_mode=disabled`. This explicit no-op
is successful delivery, preserving the approved build outcome and final artifact
paths. Record the observed remote state and integrated commit even when no
remote mutation was requested.

`gc.outcome` is the workflow step outcome, not the publish mode. Never set
`gc.outcome=noop`. A verified publish or explicit disabled no-op closes the
publish step with `gc.outcome=pass`. A failed remote or PR verification records
`gc.build.publish_status=failed` and must not be represented as successful
delivery.

For the disabled default, update each required bead before closing it. The
equivalent command shape is:

`gc bd update "<bead-id>" --set-metadata 'gc.outcome=pass' --set-metadata 'gc.publish_outcome=noop' --set-metadata 'gc.publish_mode=disabled' --set-metadata 'gc.build.publish_status=noop' --set-metadata 'gc.build.publish_action=noop' --set-metadata 'gc.build.publish_reason=push=false_open_pr=false'`

Close only after the terminal delivery record is persisted on both beads and
the publish result artifact is readable.
