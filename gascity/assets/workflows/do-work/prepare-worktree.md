
Resolve and publish the isolated worktree for this item. This is infrastructure
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
4. Create or reuse a deterministic git worktree at
   `$(pwd)/worktrees/<source-anchor-id>`, based on the up-to-date remote
   default branch — never the launcher's local `HEAD`, which may be behind
   `origin`. If the path is missing:
   - Resolve the remote default branch (do not hardcode `main`):
     `DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')`.
     If it is empty, fail closed — do not fall back to local `HEAD`.
   - Fetch it so the base is current:
     `git fetch --prune origin "$DEFAULT_BRANCH"`.
   - Create the worktree detached at the freshly fetched tip:
     `git worktree add "$WORKTREE" --detach "origin/$DEFAULT_BRANCH"`.
   If the path exists but is not the worktree for this repository, fail closed.
5. Before publishing the worktree, set `RIG_ROOT=$(pwd -P)` and copy the
   launcher rig's `.gc/scripts` and `.gc/schemas` trees into `$WORKTREE/.gc`:
   `mkdir -p "$WORKTREE/.gc/scripts" "$WORKTREE/.gc/schemas"`,
   `cp -a "$RIG_ROOT/.gc/scripts/." "$WORKTREE/.gc/scripts/"`, and
   `cp -a "$RIG_ROOT/.gc/schemas/." "$WORKTREE/.gc/schemas/"`.
   Hard-fail if either source tree is absent. Then verify
   `.gc/scripts/checks/build-artifact-valid.sh` and `.gc/schemas/build` exist
   under
   `$WORKTREE`; downstream controller checks execute from that isolated
   worktree and must not depend on files outside it.
6. Persist the absolute path on the source anchor with
   `gc bd update <source-anchor-id> --set-metadata work_dir=<absolute worktree path>`.
   For synthetic drain-unit convoys, never persist `work_dir` on the synthetic drain-unit convoy; the original drain member/source anchor is authoritative.
   Verify the source anchor now has `work_dir` before closing this step with
   `gc.outcome=pass`.
7. Stamp both `work_dir` and `gc.work_dir` on the do-work root and every open
   descendant carrying that root's `gc.root_bead_id`. Use the same absolute
   source-anchor worktree for every stamp, then read the downstream
   implementation step back and verify both keys equal that path. Complete this
   before closing this prepare step and before an implementation worker can
   claim the downstream step; stale launcher paths are a prepare failure.
