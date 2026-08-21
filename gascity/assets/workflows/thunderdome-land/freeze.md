Freeze exactly one immutable Continuous Thunderdome epoch.

Parse `{{candidate_ids}}` as comma-separated bead IDs, trim whitespace, reject
empty or duplicate IDs, and read every candidate from the task store. Require
`gc.thunderdome.schema=gc.thunderdome.v1`, kind `candidate`, an approved
summary/review, exact commits, identical base SHA `{{base_sha}}`, and no
overlapping source beads. A member may already be `frozen` only when it points
to the same deterministic epoch supplied as this workflow's source; otherwise
it must be `queued`.

Run `git fetch --no-tags origin "{{target_ref}}"`, resolve `FETCH_HEAD`, and
require its exact 40-character SHA still equals `{{base_sha}}`. If trunk moved,
fail closed without rewriting queued candidates, frozen followers, reservations,
or active intent; never rebase or silently substitute a base.

Open the epoch only through the installed state adapter:

```bash
{{pack_root}}/assets/scripts/thunderdome.py --rig "${GC_RIG_NAME:?}" \
  epoch open \
  --base-sha "{{base_sha}}" \
  --target-ref "{{target_ref}}" \
  --candidate "<candidate-id>" [--candidate "<candidate-id>" ...] \
  --json
```

Use `{{pack_root}}/assets/scripts/thunderdome.py epoch open`; do not write state
metadata, source reservations, control intent, or epoch beads directly. The
adapter may return and resume the already-sealed epoch that owns this workflow.
Parse and require that epoch ID, and require it to match any
`gc.thunderdome.epoch_id` already recorded on this workflow root. Record the ID
when absent, then read the epoch and all candidates back. Membership must be
sorted and hash-sealed, and every candidate must now have
`gc.thunderdome.state=frozen` with the same epoch ID.

Do not create a branch, PR, merge, close a source, or mutate the frozen member
list. A failed freeze can leave durable control intent, an epoch, reservations,
or partially frozen followers. Do not delete or rewrite that evidence: fail
closed and let the state adapter resume and repair the same epoch.
