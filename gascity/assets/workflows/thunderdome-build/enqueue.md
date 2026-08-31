Stage the reviewed integrated commit for automatic candidate ingress.

Resolve the workflow root and require exact typed metadata for delivery unit,
sorted source bead IDs, candidate commit, serialized aggregate validation
commit `gc.thunderdome.validation_commit`, pinned base SHA, absolute summary
path, absolute review path, and target ref. Require the validation commit to
equal the candidate commit. Read the review step from the same workflow;
require `code_review.verdict=approved`, a closed-pass check, and a schema-valid
report. Verify the integration worktree is clean, `HEAD` still equals both
recorded commits, and every source bead remains open.

Read `gc.worktree.id=thunderdome-candidate-<workflow-root-id>` and
`gc.worktree.owner=<workflow-root-id>` from the build workflow root. Hard-fail
if either field is absent or differs from the exact typed workflow identity.

Copy the canonical absolute summary path to `gc.thunderdome.summary_path` and
the canonical absolute review path to `gc.thunderdome.review_path` on the
workflow root. Then atomically admit the reviewed delivery unit:

```sh
gc bd metadata-cas "<workflow-root-id>" \
  --key gc.thunderdome.ingress_state \
  --expected "" \
  --value reviewed \
  --json
gc bd show "<workflow-root-id>" --json
```

Accept `swapped=true`, or an idempotent readback that already has
`gc.thunderdome.ingress_state=reviewed` or `queued` with the exact same
immutable delivery metadata. Refuse every conflicting state. On readback,
revalidate the commit pair, source membership, artifact paths, base SHA, and
worktree lifecycle identity before closing this step.

Do not invoke `candidate enqueue`, create a candidate bead, push, create a PR,
mutate trunk, or close source beads. The city-scoped Thunderdome reconcile order
discovers the reviewed ingress marker, atomically materializes and reserves the
immutable candidate, copies the worktree lifecycle identity, records
`gc.thunderdome.candidate_id`, and advances the ingress state to `queued`.
Epoch landing and verified promotion own all later lifecycle changes.
