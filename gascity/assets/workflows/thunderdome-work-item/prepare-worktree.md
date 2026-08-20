
Resolve and centrally register the isolated worktree for this item. This is
infrastructure setup only. Do not edit source files in the launcher checkout.

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
4. Resolve the exact candidate base through the drain lineage; never choose a
   branch tip or the launcher's local `HEAD`. Read `gc.drain_control_id` from
   the do-work root, read that drain control's `gc.root_bead_id`, then read the
   parent thunderdome-build root's `gc.thunderdome.base_sha`. Hard-fail unless
   every link exists and the base is a full 40-character commit available in
   this repository. Set `PINNED_BASE_SHA` to that exact value.
5. Resolve and canonicalize `${GC_RIG_ROOT:?}`, require `${GC_RIG_NAME:?}`, and
   verify the rig root is this repository. This item's lifecycle ID and owner
   are both the exact `<source-anchor-id>`; use attempt `1`. Its only permitted
   path is `$GC_RIG_ROOT/worktrees/<source-anchor-id>`.
6. From `$GC_RIG_ROOT`, create or exactly reuse the centrally registered
   lifecycle:

   ```sh
   gc worktree create "<source-anchor-id>" \
     --owner "<source-anchor-id>" \
     --rig "${GC_RIG_NAME:?}" \
     --path "${GC_RIG_ROOT:?}/worktrees/<source-anchor-id>" \
     --base "$PINNED_BASE_SHA" \
     --attempt 1 \
     --json
   ```

   Capture the JSON object. A missing command, helper failure, nonzero exit,
   malformed JSON, or mismatched registered entry is a hard prepare failure;
   do not stamp metadata or let implementation start. Require exact `id`,
   `owner`, `rig`, `rig_root`, `path`, `attempt`, `base`, and `head_sha`
   matches. Parse `path`, `cargo_target_dir`, and `cargo_home` from that object
   rather than constructing cache paths in the prompt. Require their canonical
   values to be, respectively:

   - `$GC_RIG_ROOT/worktrees/<source-anchor-id>`
   - `$GC_RIG_ROOT/worktrees/.cargo-targets/<source-anchor-id>/attempt-1`
   - `$GC_RIG_ROOT/.gc/cache/cargo-home`

   All three must be absolute, writable, outside `/tmp`, and the target must
   not equal any other registered lifecycle's target. Confirm isolation with
   `gc worktree list --rig "${GC_RIG_NAME:?}" --json`; no different registry ID
   may report the same `path` or `cargo_target_dir`. Creation is idempotent only
   when the complete registered identity matches exactly. Never bypass
   `gc worktree create` with direct Git lifecycle or Code Storage helper calls.
7. Copy the launcher rig's `.gc/scripts` and `.gc/schemas` trees into the
   returned worktree path: create `$WORKTREE/.gc/scripts` and
   `$WORKTREE/.gc/schemas`, copy from `$GC_RIG_ROOT/.gc/scripts` and
   `$GC_RIG_ROOT/.gc/schemas`, and hard-fail if either source tree is absent.
   Verify `.gc/scripts/checks/build-artifact-valid.sh` and `.gc/schemas/build`
   exist under `$WORKTREE`; downstream controller checks execute from the
   isolated worktree and must not depend on files outside it.
8. Persist and read back these exact fields on the source anchor:

   - `work_dir=<registered path>`
   - `gc.worktree.id=<source-anchor-id>`
   - `gc.worktree.path=<registered path>`
   - `gc.work_dir=<registered path>`
   - `gc.cargo_target_dir=<registered cargo_target_dir>`
   - `gc.cargo_home=<registered cargo_home>`

   For a synthetic drain-unit convoy, never persist lifecycle metadata on that
   synthetic convoy; the original drain member/source anchor is authoritative.
9. Stamp `work_dir`, `gc.worktree.id`, `gc.worktree.path`, `gc.work_dir`,
   `gc.cargo_target_dir`, and `gc.cargo_home` with the same registered values
   on the do-work root and every open descendant carrying that root's
   `gc.root_bead_id`. Read the downstream implementation step back and require
   every field to match the source anchor and registry before closing this
   prepare step with `gc.outcome=pass`. Complete this before an implementation
   worker can claim the downstream step; stale launcher paths or cache fields
   are a prepare failure.
