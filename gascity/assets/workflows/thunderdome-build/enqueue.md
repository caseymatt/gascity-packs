Enqueue the reviewed integrated commit as one immutable landing candidate.

Resolve the workflow root and require exact typed metadata for delivery unit,
sorted source bead IDs, candidate commit, pinned base SHA, absolute summary
path, absolute review path, and target ref. Read the review step from the same
workflow; require `code_review.verdict=approved`, a closed-pass check, and a
schema-valid report. Verify the integration worktree is clean, `HEAD` still
equals the recorded commit, and every source bead remains open.

Run the pack-installed state adapter from the rig root:

```bash
{{pack_root}}/assets/scripts/thunderdome.py candidate enqueue \
  --rig "${GC_RIG_NAME:?}" \
  --delivery-unit "<delivery-unit-id>" \
  --commit "<exact-candidate-commit>" \
  --base-sha "<exact-pinned-base-sha>" \
  --summary-path "<absolute-summary-path>" \
  --review-path "<absolute-review-path>" \
  --source-bead "<source-id>" [--source-bead "<source-id>" ...] \
  --json
```

Use `{{pack_root}}/assets/scripts/thunderdome.py candidate enqueue`; do not create candidate
beads directly. Parse the typed JSON result, require a candidate ID, and record
it on the workflow root as `gc.thunderdome.candidate_id`. Re-run is safe only
when the adapter returns the same active candidate key; conflicting active
source membership is a hard failure.

Do not push, create a PR, mutate trunk, or close source beads. The candidate
enters `gc.thunderdome.state=queued`; epoch landing and verified promotion own
all later lifecycle changes.
