Run the starter factory simplicity review lane.

Review the implementation for maintainability, readable boundaries,
unnecessary abstractions, accidental broad changes, and obvious future
maintenance risk. Keep this lane beginner-friendly: flag only concrete issues
that a new factory user can understand and act on.

Before inspecting files, read `gc.build.code_review_context_path` from the
workflow root bead and use its `## Integrated Build` section as the authority
for code under review. Resolve `INTEGRATION_WORKTREE` from
`gc.build.integration_work_dir`, run `cd "$INTEGRATION_WORKTREE"`, and verify
`pwd -P`, the named branch, and exact `HEAD` match the recorded integration
evidence before running any command. Review the combined change on the
integrated branch; per-item worktrees are provenance only.

Write findings under the build artifact root. Required findings must be tied to
specific changed files or artifacts and must explain the smallest useful fix.

Close with `gc.outcome=pass`,
`code_review.simplicity_verdict=approve|iterate`, and
`code_review.output_path=<simplicity review report path>`.

Use explicit close metadata so the review loop can detect the lane result:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'code_review.simplicity_verdict=approve' \
  --set-metadata 'code_review.output_path=<simplicity review report path>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Build-basic simplicity review approved.'
```

If you find required fixes, set
`code_review.simplicity_verdict=iterate` instead of `approve` and explain the
smallest required fix in the report and close reason.

Do not set `code_review.verdict` or `code_review.report_path`; synthesis and
fix application own the final review verdict.

Do not invoke provider-native subagents. You are the starter factory simplicity
review lane.
