Run the starter factory test evidence review lane.

Check that each accepted task recorded an intended behavior, first verification
command, proof command, changed files, and remaining risks. Verify that the
commands actually cover the acceptance criteria claimed by the requirements and
plan.

Before evaluating proof, read `gc.build.code_review_context_path` from the
workflow root bead and use its `## Integrated Build` section as the authority
for where commands must run. Resolve `INTEGRATION_WORKTREE` from
`gc.build.integration_work_dir`, run `cd "$INTEGRATION_WORKTREE"`, and verify
`pwd -P`, the named branch, and exact `HEAD` match the recorded integration
evidence before executing proof commands. If the context is missing a usable
canonical integration worktree, write an iterate finding against review setup.
Per-item worktrees are provenance only; proof must pass against the integrated
branch containing every successful commit.

Write concrete findings under the build artifact root. Distinguish missing
proof from real product defects so the fix lane can either run the missing
command or change code.

Close with `gc.outcome=pass`,
`code_review.test_evidence_verdict=approve|iterate`, and
`code_review.output_path=<test evidence report path>`.

Use explicit close metadata so the review loop can detect the lane result:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'code_review.test_evidence_verdict=approve' \
  --set-metadata 'code_review.output_path=<test evidence report path>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Build-basic test evidence review approved.'
```

If proof is missing or insufficient, set
`code_review.test_evidence_verdict=iterate` instead of `approve` and explain
whether the fix lane should run missing proof commands or change code.

Do not set `code_review.verdict` or `code_review.report_path`; synthesis and
fix application own the final review verdict.

Do not invoke provider-native subagents. You are the starter factory test
evidence review lane.
