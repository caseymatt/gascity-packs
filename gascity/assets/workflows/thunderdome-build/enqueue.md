Enqueue the reviewed integrated commit as one immutable landing candidate.

Resolve the workflow root and require exact typed metadata for delivery unit,
sorted source bead IDs, candidate commit, serialized aggregate validation
commit `gc.thunderdome.validation_commit`, pinned base SHA, absolute summary
path, absolute review path, and target ref. Require the validation commit to
equal the candidate commit. Read the
review step from the same workflow; require `code_review.verdict=approved`, a
closed-pass check, and a schema-valid report. Verify the integration worktree is
clean, `HEAD` still equals both recorded commits, and every source bead remains
open.

Run the pack-installed state adapter from the rig root:

```bash
{{pack_root}}/assets/scripts/thunderdome.py --rig "${GC_RIG_NAME:?}" \
  candidate enqueue \
  --delivery-unit "<delivery-unit-id>" \
  --commit "<exact-candidate-commit>" \
  --base-sha "<exact-pinned-base-sha>" \
  --summary-path "<absolute-summary-path>" \
  --review-path "<absolute-review-path>" \
  --source-bead "<source-id>" [--source-bead "<source-id>" ...] \
  --json
```

Use `{{pack_root}}/assets/scripts/thunderdome.py candidate enqueue`; do not
create candidate beads directly. Before invoking it, read
`gc.worktree.id=thunderdome-candidate-<workflow-root-id>` and
`gc.worktree.owner=<workflow-root-id>` from the build workflow root. Hard-fail
if either field is absent or differs from the exact typed workflow identity.

Parse the adapter's typed JSON result and require a candidate ID. Immediately
copy the exact lifecycle identity from the build root onto that returned typed
candidate bead:

```sh
gc bd update "<candidate-id>" \
  --set-metadata "gc.worktree.id=thunderdome-candidate-<workflow-root-id>" \
  --set-metadata "gc.worktree.owner=<workflow-root-id>"
gc bd show "<candidate-id>" --json
```

Accept either a JSON object or a one-element list from the readback, but
hard-fail every other shape. Require the read-back bead to be the exact adapter
candidate ID with `gc.thunderdome.kind=candidate`,
`gc.thunderdome.state=queued`, and lifecycle ID and owner exactly equal to the
build root values. Do not acknowledge or hand off the queued state, record the
candidate ID, or close this step until that copy and readback verification
succeeds. A lifecycle metadata update/readback failure leaves the build failed;
never enqueue a typed candidate that landing cannot map back to its registered
resource.

After verified lifecycle stamping, record the returned candidate ID on the
workflow root as `gc.thunderdome.candidate_id` and read it back. Re-run is safe
only when the adapter returns the same active candidate key and the typed
candidate retains the exact lifecycle ID and owner; conflicting active source
membership or lifecycle identity is a hard failure.

Do not push, create a PR, mutate trunk, or close source beads. The candidate
enters `gc.thunderdome.state=queued`; epoch landing and verified promotion own
all later lifecycle changes.
