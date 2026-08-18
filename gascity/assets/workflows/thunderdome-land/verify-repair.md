Run the full-system gate against the actual landed trunk SHA and fix forward
every aggregate failure. Never bisect the epoch, revert a candidate merely to obtain
green, or test an unmerged branch as release evidence.

Resolve the epoch and current `landed_sha` from typed state. Fetch
`origin/{{target_ref}}`, require its SHA equals that value, and create a clean
verification worktree at that exact commit. Transition `landed` or `repairing`
to `verifying` through `{{pack_root}}/assets/scripts/thunderdome.py epoch transition`. Run the
repository-owned command exactly as configured: `{{full_gate_command}}`. Capture
the command, environment identity, exit status, duration, and bounded sanitized
output in `.gc/artifacts/<epoch-id>/verification/round-<n>.md`; never store
prompts, credentials, or raw private content.

If the gate passes, transition the epoch to `verified` with
`--verified-sha <current-landed-sha>` and `--verification-ref <artifact-path>`.
Transition every sealed candidate from `landed` to `verified`. Read state back
and close pass.

If the gate fails:

1. Transition `verifying` to `red` with a stable failure class and the sanitized
   evidence reference.
2. Diagnose the complete aggregate failure output. Cluster independent failures
   by subsystem and causal root. Create one repair bead per independent cluster,
   parented to the epoch and carrying epoch ID, failure class, exact failing SHA,
   evidence path, and acceptance command. Do not create one bead per log line.
3. Route every repair bead concurrently to `{{repair_target}}` with
   `thunderdome-work-item`. Each repair starts from the same failed trunk SHA in
   its own worktree. Record every repair bead ID on the epoch by transitioning
   `red` to `repairing` with repeated `--repair-bead` evidence.
4. Wait for workflow-root `bead.closed` events with `gc events --watch` and
   payload matching; do not poll. Require every repair implementation and
   artifact to close pass.
5. Merge all successful repair commits into one repair integration branch based
   on the failed trunk SHA. Resolve overlaps in the aggregate. Push one repair
   PR, use protected checks/merge queue, and read the actual merged SHA from
   GitHub. Close repair beads only after their fixes are present in that SHA.
6. Transition `repairing` to `verifying` with
   `--landed-sha <actual-repair-merge-sha>`, then repeat the full gate against a
   clean checkout of that exact merged trunk SHA.

Run at most `{{max_repair_rounds}}` repair rounds. This cap is pack policy, not
SDK judgment. On exhaustion or an unrecoverable infrastructure failure,
transition the epoch from `red` or `repairing` to `failed` with sanitized
failure evidence and fail this step. Never convert timeout, missing output,
empty repair membership, command failure, or GitHub ambiguity into success.
