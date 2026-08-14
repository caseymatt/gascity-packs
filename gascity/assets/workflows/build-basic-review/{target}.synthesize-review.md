Synthesize the build-basic starter factory review.

Read the acceptance, test evidence, and simplicity review reports. Deduplicate
findings, preserve the source review lane for each finding, and classify each
item as required fix, missing evidence, or residual risk.

Also read `gc.build.code_review_context_path` from the workflow root bead. When
you carry a finding forward, include the canonical path from the context's
`## Integrated Build` section plus `gc.build.integration_work_dir`. Tie every
finding to the integrated branch and commit. If a finding cites only a relative
filename, resolve it against the canonical integration worktree. Required fixes
must be specific enough for the fix lane to act without guessing, and the
synthesis must state the integrated branch and commit it reviewed.

Write one starter review synthesis under the build artifact root. The synthesis
must be short enough for a first-time factory user to scan, but concrete enough
for the fix lane to act without another planning pass.

Close with `gc.outcome=pass`,
`code_review.synthesis_path=<starter review synthesis path>`, and
`code_review.output_path=<starter review synthesis path>`.

Do not invoke provider-native subagents. Synthesis happens in this Gas City
fan-in lane.
