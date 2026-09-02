Run the full-system gate against the actual landed trunk SHA and fix forward
every aggregate failure. Never bisect the epoch, revert a candidate merely to
obtain green, or test an unmerged branch as release evidence.

Resolve the epoch and current `landed_sha` from typed state. Run
`git fetch --no-tags origin "{{target_ref}}"`, require the exact `FETCH_HEAD` SHA
equals that value, and transition `landed` or `repairing` to `verifying` through
`{{pack_root}}/assets/scripts/thunderdome.py epoch transition`.

For verification round `<N>` starting at 1, create or exactly reuse lifecycle ID
`<epoch-id>-verify-r<N>` so the Code Storage signer can authorize its rig bead
prefix, owner `<epoch-id>`, attempt `1`, and explicit path
`$GC_RIG_ROOT/worktrees/verify-<epoch-id>-r<N>` at that exact landed commit:

```bash
verify_worktree_id="<epoch-id>-verify-r<N>"
verify_worktree="${GC_RIG_ROOT:?}/worktrees/verify-<epoch-id>-r<N>"
gc worktree create "$verify_worktree_id" \
  --owner "<epoch-id>" --rig "${GC_RIG_NAME:?}" --path "$verify_worktree" \
  --base "<current-landed-sha>" --attempt 1 --json
```

Require returned `id`, `owner`, `rig`, `rig_root`, `path`, `attempt`, `base`,
and `head_sha` to match exactly and require no branch. Persist and read back
`gc.worktree.id`, canonical `gc.worktree.path`, legacy `gc.work_dir`,
`gc.cargo_target_dir`, and `gc.cargo_home` for this round. Run the
repository-owned command exactly as
configured, `{{full_gate_command}}`, from the returned `path` with the returned
cache environment explicitly supplied unchanged. Require `cargo_target_dir` to
be `$GC_RIG_ROOT/worktrees/.cargo-targets/<epoch-id>-verify-r<N>/attempt-1` and
`cargo_home` to be `$GC_RIG_ROOT/.gc/cache/cargo-home`. The launcher supplies
the shared sccache contract: require nonempty inherited `RUSTC_WRAPPER`,
`SCCACHE_DIR`, and `SCCACHE_CACHE_SIZE`, do not rewrite them, and pass them plus
the registered Cargo paths explicitly to the full gate. Fail rather than
inherit a different target, invent settings, or share another live target tree.

Capture the command, environment identity, exit status, duration, canonical
cache paths, and bounded sanitized output in
`.gc/artifacts/<epoch-id>/verification/round-<n>.md`; never store prompts,
credentials, or raw private content.

If the gate passes, transition the epoch to `verified` with
`--verified-sha <current-landed-sha>` and `--verification-ref <artifact-path>`.
The epoch transition atomically advances every sealed candidate from `landed`
to `verified`; read the epoch and candidates back and close pass only after all
states match.

If the gate fails:

1. Transition `verifying` to `red` with a stable failure class and the sanitized
   evidence reference.
2. Diagnose the complete aggregate failure output. Cluster independent failures
   by subsystem and causal root. Create one repair bead per independent cluster,
   parented to the epoch and carrying epoch ID, failure class, exact failing SHA,
   evidence path, and acceptance command. Do not create one bead per log line.
3. Route every repair bead concurrently to `{{repair_target}}` with
   `thunderdome-work-item`. Each repair starts from the same failed trunk SHA in
   its own registered worktree and must return an exact published repair head.
   Record every repair bead ID on the epoch by transitioning `red` to
   `repairing` with repeated `--repair-bead` evidence.
4. Wait for workflow-root `bead.closed` events with `gc events --watch` and
   payload matching; do not poll. Require every repair implementation and
   artifact to close pass.
5. Create or exactly reuse the repair integration checkout with lifecycle ID
   `<epoch-id>-repair-int-r<N>`, explicit path
   `$GC_RIG_ROOT/worktrees/repair-int-<epoch-id>-r<N>`, owner `<epoch-id>`,
   attempt `1`, branch `thunderdome/repair-<epoch-id>-r<N>`, and base equal to
   the exact failed trunk SHA:

   ```bash
   repair_worktree_id="<epoch-id>-repair-int-r<N>"
   repair_worktree="${GC_RIG_ROOT:?}/worktrees/repair-int-<epoch-id>-r<N>"
   gc worktree create "$repair_worktree_id" \
     --owner "<epoch-id>" --rig "${GC_RIG_NAME:?}" --path "$repair_worktree" \
     --base "<failed-trunk-sha>" \
     --branch "thunderdome/repair-<epoch-id>-r<N>" --attempt 1 --json
   ```

   Require the returned registration fields and cache paths to match exactly;
   persist `gc.worktree.id`, canonical `gc.worktree.path`, legacy
   `gc.work_dir`, `gc.cargo_target_dir`, and `gc.cargo_home` for this repair
   round. If this build-bearing integration runs a command, launch it with the
   registered Cargo environment unchanged and require `cargo_target_dir` to
   equal
   `$GC_RIG_ROOT/worktrees/.cargo-targets/<epoch-id>-repair-int-r<N>/attempt-1`
   and `cargo_home` to equal `$GC_RIG_ROOT/.gc/cache/cargo-home`. Require the
   launcher's nonempty `RUSTC_WRAPPER`, `SCCACHE_DIR`, and
   `SCCACHE_CACHE_SIZE` unchanged and explicit on every Cargo command.
   Merge every successful published repair commit into this branch and resolve
   aggregate overlaps without omitting a repair. After the repair integration
   head is final and clean, publish it through the registered capability before
   opening its PR:

   ```bash
   gc worktree publish "<epoch-id>-repair-int-r<N>" --json
   ```

   Require `published_sha` to equal its exact current `HEAD`; persist and read
   back `gc.thunderdome.published_ref` and `gc.thunderdome.published_sha`. The
   Code Storage `published_ref` is durable recovery evidence and must never be
   passed to GitHub.

   Publish the exact durable SHA to the unique GitHub integration ref
   `refs/heads/thunderdome/repair-<epoch-id>-r<N>`. Read that full ref first
   with `git ls-remote --refs origin`. If absent, create it with an
   absence-guarded lease; if present, require it to equal `published_sha`:

   ```bash
   git push --force-with-lease="refs/heads/thunderdome/repair-<epoch-id>-r<N>:" \
     origin "<published-sha>:refs/heads/thunderdome/repair-<epoch-id>-r<N>"
   ```

   Read the remote ref back and require one exact `published_sha` match. Open
   one repair PR from the validated GitHub branch name, use protected checks
   and the merge queue, and read the actual merged SHA from GitHub.
   Publication, lease, remote-readback, or head ambiguity is failure and
   preserves the registered repair checkout. Close repair beads only after
   their fixes are present in that actual merged SHA.
6. Transition `repairing` to `verifying` with
   `--landed-sha <actual-repair-merge-sha>`, then repeat the full gate against a
   newly registered clean checkout of that exact merged trunk SHA.

Run at most `{{max_repair_rounds}}` repair rounds. This cap is pack policy, not
SDK judgment. On exhaustion or an unrecoverable infrastructure failure,
transition the epoch from `red` or `repairing` to `failed` with sanitized
failure evidence and fail this step. Never convert timeout, missing output,
empty repair membership, command failure, registry or publication failure, or
GitHub ambiguity into success.
