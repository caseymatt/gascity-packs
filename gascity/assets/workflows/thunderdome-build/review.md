Review the integrated Continuous Thunderdome candidate as one delivery unit.
This is report-only: do not edit code, amend commits, push, publish, or close
source beads.

Resolve the workflow root, pinned base SHA, exact candidate commit, source
membership, item artifacts, aggregate summary, and integration worktree from
typed metadata. Verify the candidate worktree is clean and its `HEAD` equals
`gc.thunderdome.commit`. Review the complete diff from pinned base to candidate,
not each item in isolation. Check acceptance behavior, regression evidence,
security/correctness boundaries, integration conflicts, simplicity, and whether
every source bead is represented exactly once.

Write a `gc.build.review.v1` report to `{{review_path}}` when non-empty;
otherwise use `.gc/artifacts/<workflow-root-id>/review/implementation-review.md`
under the integration worktree. Record the absolute path on the workflow root as
`gc.build.review_report_path` and on this review step as
`code_review.report_path`.

An approval requires no blocking findings, a clean exact candidate commit, and
complete observed evidence. Set this step's `code_review.verdict=approved` only
then. Otherwise set `code_review.verdict=iterate`, record concrete file/command
findings in the report, and leave the check gate to request a bounded re-review.
Never report approval to bypass the gate.

Run the build artifact validator from the integration worktree with the claimed
step ID and repair schema errors before closing. The formula's
`implementation-review-approved.sh` check is authoritative for the verdict.
