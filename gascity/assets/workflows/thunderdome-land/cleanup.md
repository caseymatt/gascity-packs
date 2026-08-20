Clean only registered formula-owned worktrees after the landing scope settles.
Cleanup must never change the already-recorded workflow outcome.

Resolve the workflow root and epoch ID from typed metadata. Read the root,
epoch, candidates, source beads, repair beads, and remote release ref. If the
workflow did not close pass, `gc.thunderdome.state=promoted` is absent, or the
remote ref does not equal the verified release SHA, do not reclaim anything.
Record every known lifecycle ID as preserved and record why cleanup was skipped.
Never turn a failed or incomplete epoch into a cleanup candidate.

For a promoted epoch, build an explicit lifecycle-ID-to-owner map only from
typed Thunderdome state and lifecycle fields persisted by the child workflows.
Every typed candidate record must carry the exact `gc.worktree.id` and
`gc.worktree.owner` copied from its candidate workflow root at enqueue. Add that
recorded pair directly to the bounded map; never reconstruct a candidate
lifecycle ID, infer its workflow root, or derive ownership from a path. The map
may otherwise contain:

- each exact source or repair bead ID, owned by that source or repair bead
- each candidate's exact recorded `gc.worktree.id` and `gc.worktree.owner`
- `thunderdome-epoch-<epoch-id>`, owned by the epoch
- each recorded `verify-<epoch-id>-r<N>`, owned by the epoch
- each recorded `repair-int-<epoch-id>-r<N>`, owned by the epoch

Reject missing candidate lifecycle fields, duplicate lifecycle IDs, conflicting
owners, or values that mismatch the immutable enqueue evidence. Require every
round number to be a recorded positive round. Do not scan directories, infer
IDs from age or workflow-root names, enumerate unrecorded rounds, or include a
registry entry merely because its path looks familiar.

For each allowed ID, query the central registry:

```bash
gc worktree list "<lifecycle-id>" --rig "${GC_RIG_NAME:?}" --json
```

Require exactly one returned array entry whose `id`, `owner`, `rig`, `rig_root`,
and canonical `path` match the typed ownership map and this rig. Require `path`
to be exactly `$GC_RIG_ROOT/worktrees/<lifecycle-id>`. Zero, multiple,
malformed, mismatched, or ambiguous results preserve the path and are cleanup
failures; never infer a replacement path.

For each exact matching entry, invoke central reclaim by lifecycle ID:

```bash
reclaim=(gc worktree reclaim "<lifecycle-id>" \
  --promoted-sha "<verified-release-sha>" --json)
if [[ "${GC_WORKTREE_CLEANUP_DRY_RUN:-0}" == "1" ]]; then
  reclaim+=(--dry-run)
fi
"${reclaim[@]}"
```

Reclaim independently requires a clean checkout in the same repository, exact
registry ownership, and no live owner. It may remove the worktree only when
either its verified remote `published_sha` equals its current `head_sha`, or
its current `head_sha` is an ancestor of the supplied promoted SHA. Thus a
published losing source or repair head remains recoverable and may be reclaimed,
while an unpublished non-ancestor is preserved. A missing or unverifiable
publication is not publication evidence; the promoted-SHA ancestry path remains
an independent alternative.

Parse the flat reclaim result fields `id`, `owner`, `rig`, `path`, `reclaimed`,
`dry_run`, `reclaimable`, `reason`, `head_sha`, `published_ref`, and
`published_sha`. A blocked reclaim may return nonzero with a valid result:
preserve its path and record its exact reason, then continue with the bounded
set. A successful dry run has `reclaimed=false`, `dry_run=true`, and
`reclaimable=true`; report it as would reclaim and never delete anything.
Dirty, live, unpublished divergent, missing, or probe-denied worktrees are
preserved. In particular, a dirty worktree must be preserved.
The exact reclaim reason must identify the dirty state.
Registry ambiguity, malformed output, helper failure, or any command failure
without a valid refusal result also preserves the path and makes this teardown
fail.
Never use force removal, delete a branch, run a global prune, or call Git
directly for worktree cleanup.

Write `.gc/artifacts/<epoch-id>/cleanup.md` with the epoch ID, verified SHA,
dry-run state, exact reclaimed paths, exact would-reclaim paths, exact preserved
paths, publication refs where present, and every refusal or failure reason. Do
not include prompts, credentials, or repository content. Record the artifact
path on the workflow root as `gc.cleanup.evidence_ref`; record reclaimed paths
as `gc.cleanup.removed_paths` and every preserved path as
`gc.cleanup.blocked_paths`. Close pass only after every eligible entry has a
well-formed reclaimed, would-reclaim, or explicitly preserved result and no
unexpected failure occurred.
