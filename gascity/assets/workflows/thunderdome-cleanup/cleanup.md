Reclaim deferred resources for terminal Thunderdome epoch `{{epoch_id}}` without force.

Resolve the claimed step and workflow root. Read `{{epoch_id}}` through the
pack-installed Thunderdome state adapter and directly through `gc bd show`.
Require an authoritative `gc.thunderdome.v1` epoch in `failed` or `cancelled`
state, a closed bead lifecycle, a sealed `gc.thunderdome.cleanup_intent` for
formula `thunderdome-cleanup`, and `gc.thunderdome.cleanup_workflow_id` equal to
this exact workflow root. Refuse cleanup for an active or promoted epoch.

Build one bounded lifecycle map from durable metadata only:

- every typed candidate's recorded `gc.worktree.id` and `gc.worktree.owner`;
- the epoch landing workflow root's recorded lifecycle ID and owner;
- lifecycle IDs and owners recorded by verification and repair descendants;
- source or repair bead lifecycle IDs only when the exact bead records them.

Never reconstruct an ID from a path, scan arbitrary directories, or infer
ownership from a title, branch, transcript, or naming convention. Reject
conflicting owners. For each exact map entry, run `gc worktree list
"<lifecycle-id>" --json` from `${GC_RIG_ROOT:?}` and require at most one exact
registry entry. A missing entry is already-clean evidence.

For every present exact entry, run `gc worktree reclaim "<lifecycle-id>"
--json` without `--force`. Accept only typed output whose ID, owner, rig, rig
root, and path match the registry read. Count `reclaimed=true` as cleaned. Count
a denied or preserved result only when it includes a nonempty safety reason;
leave that resource registered and untouched. Never call `git worktree remove`,
`rm -rf`, delete a branch, or bypass dirty, unpushed, unpublished, ownership, or
ancestry checks.

Write a bounded JSON report under
`${GC_RIG_ROOT}/.gc/artifacts/{{epoch_id}}/terminal-cleanup.json` with schema
`gc.thunderdome.cleanup.v1`, epoch ID and state, cleanup workflow ID, checked and
reclaimed lifecycle IDs, preserved `{id, reason}` rows, and completion time.
Record its absolute path on the epoch as
`gc.thunderdome.cleanup_report_path` and record
`gc.thunderdome.cleanup_state=complete` only when nothing is preserved;
otherwise record `gc.thunderdome.cleanup_state=partial`. Read both fields back.

Close pass after the report and metadata readback succeed. Preservation is a
safe successful janitor outcome when every retained resource has an explicit
registry reason; unexplained command, identity, or report failures remain hard
failures.
