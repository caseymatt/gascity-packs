
Resolve and register the isolated worktree for this item. This is infrastructure
setup only. Do not edit source files in the launcher checkout.

1. Read current step bead metadata and get `gc.root_bead_id`; hard-fail if it is
   missing. Read that do-work root with `gc bd show <root-bead-id> --json`. If
   `gc bd show --json` returns a one-element list, unwrap the first element before
   reading metadata.
2. Resolve `<source-anchor-id>` from the do-work root:
   - read root metadata `gc.input_convoy_id`; hard-fail if it is missing
   - verify `gc.input_convoy_id` matches rendered runtime convoy `{{convoy_id}}`
   - read that input convoy with `gc bd show <input-convoy-id> --json`; unwrap a
     one-element list response before reading metadata
   - if input convoy metadata has `gc.synthetic_kind=drain-unit-convoy`, use
     input convoy metadata `gc.drain_member_id`
   - do not use the synthetic drain-unit convoy id as `<source-anchor-id>`;
     hard-fail if the selected source anchor id equals the synthetic input convoy id
   - otherwise use `<input-convoy-id>` as the source anchor
   - if root metadata also has `gc.drain_member_id`, it must match the selected
     drain member
3. Validate context path {{context_path}}, files ownership, and verification
   policy for the resolved source anchor.
4. Resolve the launcher rig only from `GC_RIG_ROOT`: hard-fail unless
   `GC_RIG_ROOT` names the absolute canonical root for `$GC_RIG`, then set
   `RIG_ROOT=$(cd -- "$GC_RIG_ROOT" && pwd -P)`. Set
   `WORKTREE_ID=<source-anchor-id>`, `OWNER_ID=<source-anchor-id>`, and
   `WORKTREE="$RIG_ROOT/worktrees/$WORKTREE_ID"`. Do not derive a path from
   `pwd`, the current step id, a synthetic drain-unit convoy id, or another
   checkout.
5. Resolve the remote default branch from the rig repository without hardcoding
   `main`, fetch it, and resolve its fetched tip to an exact commit:
   `DEFAULT_BRANCH=$(git -C "$RIG_ROOT" remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')`,
   `git -C "$RIG_ROOT" fetch --prune origin "$DEFAULT_BRANCH"`, and
   `BASE=$(git -C "$RIG_ROOT" rev-parse --verify "origin/$DEFAULT_BRANCH^{commit}")`.
   Hard-fail if any command fails or the branch or exact base SHA is empty.
6. Create or exactly reuse the registered checkout with
   `gc worktree create "$WORKTREE_ID" --owner "$OWNER_ID" --rig "$GC_RIG" --path "$WORKTREE" --base "$BASE" --attempt 1 --json`.
   This command, not this prompt, creates the checkout and attaches its scoped
   Code Storage publication capability. Do not call `git worktree` or a signer
   helper directly. Creation must succeed and return one JSON entry object;
   inability to attach publication capability is a creation failure, never a
   nonfatal mode. Do not register or continue from a partial checkout.
7. Validate the returned registry entry before touching the checkout. Require
   `id`, `owner`, `rig`, `rig_root`, `path`, `attempt`, and `base` to equal
   `WORKTREE_ID`, `OWNER_ID`, `$GC_RIG`, `RIG_ROOT`, `WORKTREE`, `1`, and
   `BASE`. Read `path`, `cargo_target_dir`, and `cargo_home` from that object;
   require canonical absolute paths, require `path` to equal
   `$RIG_ROOT/worktrees/$WORKTREE_ID`, require `cargo_target_dir` to equal
   `$RIG_ROOT/worktrees/.cargo-targets/$WORKTREE_ID/attempt-1`, and require
   `cargo_home` to equal `$RIG_ROOT/.gc/cache/cargo-home`. A malformed,
   ambiguous, or mismatched response is a hard failure.
8. Copy the launcher rig's `.gc/scripts` and `.gc/schemas` trees into the
   registered worktree:
   `mkdir -p "$WORKTREE/.gc/scripts" "$WORKTREE/.gc/schemas"`,
   `cp -a "$RIG_ROOT/.gc/scripts/." "$WORKTREE/.gc/scripts/"`, and
   `cp -a "$RIG_ROOT/.gc/schemas/." "$WORKTREE/.gc/schemas/"`.
   Hard-fail if either source tree is absent. Then verify
   `.gc/scripts/checks/build-artifact-valid.sh` and `.gc/schemas/build` exist
   under `$WORKTREE`; downstream controller checks execute from that isolated
   worktree and must not depend on files outside it.
9. Persist the validated registry identity, path, and caches on the source
   anchor: `gc.worktree.id=$WORKTREE_ID`, `gc.worktree.path=$WORKTREE`,
   `work_dir=$WORKTREE`, `gc.work_dir=$WORKTREE`,
   `gc.cargo_target_dir=<returned cargo_target_dir>`, and
   `gc.cargo_home=<returned cargo_home>`. Use `gc bd update
   <source-anchor-id> --set-metadata ...`, then read the source anchor back and
   require every value to match the validated entry. For synthetic drain-unit
   convoys, never persist these fields on the synthetic drain-unit convoy; the
   original drain member/source anchor is authoritative.
10. Stamp the same six fields on the do-work root and every open descendant
   carrying that root's `gc.root_bead_id`. Read the downstream implementation
   step back and verify all six values before closing this prepare step and
   before an implementation worker can claim it. Stale launcher paths, missing
   cache fields, or any value reconstructed instead of copied from the
   registered entry are a prepare failure.
