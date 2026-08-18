Apply build-basic starter review findings.

Use implementation target {{implementation_target}} for any code changes. Read
the starter review synthesis. If all three review lanes approve, write a no-op
review summary. If required fixes or missing evidence remain, make the smallest
focused changes, run the relevant proof commands, and write the review-fix
summary under the build artifact root.

Apply fixes only to the canonical integration worktree named in the review
context. Before editing or running proof commands, read
`gc.build.code_review_context_path` from the workflow root bead and use its
`## Integrated Build` section as the authority for writable code. Resolve
`INTEGRATION_WORKTREE` from `gc.build.integration_work_dir`, run
`cd "$INTEGRATION_WORKTREE"`, and verify `pwd -P`, the named branch, and exact
`HEAD` match the recorded integration evidence.

If a required fix changes code, patch only the canonical integration worktree,
run the affected and aggregate proof there, commit the fix, and update
`gc.build.integration_commit` to the new exact `HEAD`. Do not patch isolated
per-item worktrees after integration. If the integrated worktree is missing,
dirty for unrelated reasons, detached, or stale, write an iterate summary that
identifies the integration blocker; do not guess another checkout.

Set `code_review.verdict=done` only when acceptance, test evidence, and
simplicity all approve after this pass. Set `code_review.verdict=iterate` when
required fixes remain.

Always close with `gc.outcome=pass`,
`code_review.verdict=done|iterate`,
`code_review.report_path=<starter review summary path>`, and
`code_review.output_path=<starter review summary path>`.

Use the exact claimed bead id when updating metadata. Do not pass freeform notes
or additional positional arguments to `gc bd update`; unquoted words can resolve to
unrelated beads. Use this command shape:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'code_review.verdict=done' \
  --set-metadata 'code_review.report_path=<starter review summary path>' \
  --set-metadata 'code_review.output_path=<starter review summary path>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Build-basic starter review approved.'
```

Do not invoke provider-native subagents. This starter factory graph lane is the
fix delegation mechanism.
