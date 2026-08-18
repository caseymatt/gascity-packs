Validate and pin one Continuous Thunderdome delivery unit before any source side effect.

1. Resolve the claimed step, its `gc.root_bead_id`, and the workflow root. Read
   `gc.input_convoy_id` only from that root; verify it equals `{{convoy_id}}` and
   resolves to a real or normalized singleton convoy.
2. Read every convoy member from the task store. Require at least one member,
   require every member to be open work, and reject any member already present
   in an active `gc.thunderdome.v1` candidate.
3. Validate `{{context_path}}` when present. Fetch `origin/{{target_ref}}` and
   resolve its exact 40-character SHA. Fail closed if the ref, remote, or SHA is
   unavailable. This is the candidate base; never use a stale local branch.
4. Resolve the durable rig root from `$GC_RIG_ROOT`, verify it is this Git
   repository, and record these typed root metadata fields:
   - `gc.thunderdome.delivery_unit=<input-convoy-id>`
   - `gc.thunderdome.base_sha=<exact fetched SHA>`
   - `gc.thunderdome.target_ref={{target_ref}}`
   - `gc.work_dir=<absolute rig root>`
5. Record the sorted source bead IDs on the root as JSON metadata
   `gc.thunderdome.source_beads`. Do not infer members from descriptions or
   session logs.

Do not create a worktree, branch, commit, candidate bead, or PR in this step.
Close pass only after reading the metadata back and confirming exact values.
