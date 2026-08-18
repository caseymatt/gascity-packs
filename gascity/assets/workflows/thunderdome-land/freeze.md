Freeze exactly one immutable Continuous Thunderdome epoch.

Parse `{{candidate_ids}}` as comma-separated bead IDs, trim whitespace, reject
empty or duplicate IDs, and read every candidate from the task store. Require
`gc.thunderdome.schema=gc.thunderdome.v1`, kind `candidate`, state `queued`, an
approved summary/review, exact commits, identical base SHA `{{base_sha}}`, and
no overlapping source beads.

Fetch `origin/{{target_ref}}` and require its exact 40-character SHA still equals
`{{base_sha}}`. If trunk moved, fail closed and leave candidates queued for
explicit refresh/rebuild; never rebase or silently substitute a base.

Open the epoch only through the installed state adapter:

```bash
{{pack_root}}/assets/scripts/thunderdome.py epoch open \
  --rig "${GC_RIG_NAME:?}" \
  --base-sha "{{base_sha}}" \
  --target-ref "{{target_ref}}" \
  --candidate "<candidate-id>" [--candidate "<candidate-id>" ...] \
  --json
```

Use `{{pack_root}}/assets/scripts/thunderdome.py epoch open`; do not write state metadata or epoch
beads directly. Parse and require the returned epoch ID. Record it on this
workflow root as `gc.thunderdome.epoch_id`, then read the epoch and all candidates
back. Membership must be sorted, hash-sealed, and every candidate must now have
`gc.thunderdome.state=frozen` with the same epoch ID.

Do not create a branch, PR, merge, close a source, or mutate the frozen member
list. A failed freeze leaves no partial epoch.
