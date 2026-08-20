
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. Handle
both an object and a one-element list from `gc bd show --json`. Resolve the rig
only from `GC_RIG_ROOT`, require it to be absolute and canonical, and set
`RIG_ROOT=$(cd -- "$GC_RIG_ROOT" && pwd -P)`.

Read `gc.worktree.id`, `gc.worktree.path`, `work_dir`, `gc.work_dir`,
`gc.cargo_target_dir`, and `gc.cargo_home` from the source anchor. Set
`WORKTREE_ID` from `gc.worktree.id`, require it to equal `<source-anchor-id>`,
and require all three stored worktree paths to agree. Run
`gc worktree list "$WORKTREE_ID" --rig "$GC_RIG" --json` and require exactly
one matching entry whose `id`, `owner`, `rig`, `rig_root`, `path`, and `attempt`
equal `<source-anchor-id>`, `<source-anchor-id>`, `$GC_RIG`, `$RIG_ROOT`, the
three persisted paths, and `1`. Require its `cargo_target_dir` to equal both
`gc.cargo_target_dir` and
`$RIG_ROOT/worktrees/.cargo-targets/$WORKTREE_ID/attempt-1`, and require its
`cargo_home` to equal both `gc.cargo_home` and
`$RIG_ROOT/.gc/cache/cargo-home`. A
missing, malformed, ambiguous, or mismatched registry result is a hard failure;
do not reconstruct a path or close any bead.

Verify the implementation commit and summary evidence in the registered
`path`. Write the per-item summary to {{summary_path}} when set. If
`summary_path` is not set, first use `gc.implementation.summary_path` from the
preceding implementation step when present; otherwise use
`{{artifact_root}}/task-<source-anchor-id>-summary.md`. If the registered path
equals `RIG_ROOT`, is not the owned worktree, or does not contain the
implementation commit, fail this step instead of closing the source anchor.

Before any close or reclaim, publish through the registry:
`gc worktree publish "$WORKTREE_ID" --json`. Do not call a signer helper or
push directly. Publication must succeed and return one object whose `id`,
`owner`, `rig`, and `path` match the validated entry, whose `published` is
true, whose `published_ref` is nonempty, and whose `published_sha` equals both
its `head_sha` and the worktree's current `HEAD`. Record the returned values on
the source anchor as `gc.codestorage_ref`, `gc.codestorage_sha`,
`gc.worktree.published_ref`, and `gc.worktree.published_sha`, then read them
back. There is no unavailable-publication bypass: a helper, probe, registry,
parse, identity, or SHA failure leaves the worktree and source anchor open.

After verified publication, run
`gc worktree reclaim "$WORKTREE_ID" --json`, capturing both its exit status and
JSON because a fail-closed preservation decision may return nonzero. Accept a
reclaim result only when `id`, `owner`, `rig`, `path`, `published_ref`, and
`published_sha` match the validated and published entry. For any result with
`reclaimed=true` or `reclaimable=true`, also require `head_sha` to match the
validated entry. For a denied or preserved result, `head_sha` may be empty; if
nonempty it must exactly match the validated entry.
Only `reclaimed=true`, `reclaimable=true`, `dry_run=false`, and a zero exit
status prove deletion; stamp `gc.worktree_reclaimed=true` only for that exact
combination.

Every other outcome preserves the checkout. Before any close, stamp
`gc.worktree_reclaimed=false` and
`gc.worktree_preservation_reason=<nonempty exact reclaim reason>` on the source
anchor and read both values back. This includes a valid denied decision, a
`GC_WORKTREE_CLEANUP_DRY_RUN=1` or other dry run, a dirty or live checkout,
unpublished or mismatched evidence, and any ownership, repository, helper,
probe, registry, parse, ambiguity, or command error. For missing, malformed, or mismatched result JSON,
preserve the checkout, record a sanitized nonempty reason naming the JSON
defect, and fail this step with the source anchor still open; never infer
deletion. A non-reclaimed result with an empty reason is likewise unverified
and must not close.

After either verified deletion or a verified and recorded preservation
decision, close only `<source-anchor-id>` with `gc.outcome=pass`. Include the
verified commit and summary path in the source-anchor close reason. Read the
source anchor back with `gc bd show <source-anchor-id> --json` and verify
`status=closed`, `gc.outcome=pass`, and the reclaim metadata appropriate to the
decision. If any check fails, fix the source anchor before closing this step.
Do not close this step with pass while the source anchor remains open, or while
a preserved checkout lacks its recorded reason. Then close this step. Do not
close the drain-unit convoy, parent convoy, or broader workflow root here.
