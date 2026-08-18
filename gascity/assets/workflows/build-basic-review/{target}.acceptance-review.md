Run the starter factory acceptance review lane.

Review the implementation against the requirements, acceptance criteria,
implementation plan, decomposition, and task summaries. Focus on correctness:
did the factory build the requested behavior, and did it avoid out-of-scope
changes?

Read the review context first and evaluate the canonical integrated build
recorded there. Before inspecting files or running tests, read
`gc.build.code_review_context_path` from the workflow root bead and use its
`## Integrated Build` section as the authority for code under review. Resolve
`INTEGRATION_WORKTREE` from `gc.build.integration_work_dir`, run
`cd "$INTEGRATION_WORKTREE"`, and verify `pwd -P`, the named branch, and exact
`HEAD` match the recorded integration evidence before executing commands.

Review the integrated branch containing every successful per-item commit.
Isolated source-anchor worktrees are provenance, not the acceptance target. If
the context lacks a usable canonical integration worktree or the recorded
branch and commit are stale, write an iterate finding against review setup
instead of approving partial output.

Write findings under the build artifact root. Required findings must include
the relevant requirement or task reference plus the file, command, or artifact
that proves the issue.

Close with `gc.outcome=pass`,
`code_review.acceptance_verdict=approve|iterate`, and
`code_review.output_path=<acceptance review report path>`.

Use explicit close metadata so the review loop can detect the lane result:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'code_review.acceptance_verdict=approve' \
  --set-metadata 'code_review.output_path=<acceptance review report path>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Build-basic acceptance review approved.'
```

If you find required fixes, set
`code_review.acceptance_verdict=iterate` instead of `approve` and explain the
smallest required fix in the report and close reason.

Do not set `code_review.verdict` or `code_review.report_path`; synthesis and
fix application own the final review verdict.

Do not invoke provider-native subagents. You are the starter factory acceptance
review lane.
