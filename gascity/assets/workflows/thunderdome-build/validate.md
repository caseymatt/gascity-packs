Validate the integrated Rust candidate in one serial owner after the drain and
integration barriers have completed.

Resolve the claimed step and `<workflow-root-id>`. Read the core-authored
`gc.drain_manifest.v1` and require the drain control plus every manifest row to
be closed pass. The only valid lifecycle ID is
`thunderdome-candidate-<workflow-root-id>`. Require that exact value in root
`gc.worktree.id`, then read `gc.worktree.path`, `work_dir`, `gc.work_dir`,
`gc.thunderdome.integration_worktree`, the exact 40-character
`gc.thunderdome.commit`, `gc.thunderdome.published_ref`,
`gc.thunderdome.published_sha`, `gc.cargo_target_dir`, and `gc.cargo_home`.
Never reconstruct membership or candidate state from branches, labels,
descriptions, or session logs.

From `${GC_RIG_ROOT:?}`, run
`gc worktree list "thunderdome-candidate-<workflow-root-id>" --json` and
require an array containing exactly one entry. Require its `id`, `owner`, `rig`,
`rig_root`, `path`, `attempt`, `base`, and `branch` to match the workflow root
and integration contract exactly. Require `published=true`, a nonempty
`published_ref`, and `head_sha`, `published_sha`,
`gc.thunderdome.published_sha`, and `gc.thunderdome.commit` all to equal the
same exact candidate commit. Require the root publication ref to equal registry
`published_ref`. Any absent, malformed, ambiguous, unpublished, or mismatched
entry fails closed before the gate.

Require the registered path to equal
`$GC_RIG_ROOT/worktrees/thunderdome-candidate-<workflow-root-id>`, to be an
absolute clean worktree for this repository, and to have `HEAD` equal the
recorded and published commit. Set `INTEGRATION_WORKTREE`,
`CARGO_TARGET_DIR`, and `CARGO_HOME` only from registry `path`,
`cargo_target_dir`, and `cargo_home`; require `gc.worktree.path`, `work_dir`,
`gc.work_dir`, `gc.thunderdome.integration_worktree`, and the root cache
metadata fields to match them exactly.

This step is the only owner of the repository-wide Rust validation/check gate.
Run `{{aggregate_rust_gate_command}}` exactly once, serially, from the
integration worktree. Do not delegate it to an item worker, fan it out,
background any part, or start a second aggregate Cargo process. Do not rerun
the union of per-item commands: the configured repository gate is the single
aggregate authority. A missing or blank rendered command is a configuration
failure.

Before the gate, require the registry-owned cache paths to be canonical,
absolute, writable, outside `/tmp`, and exactly:

```text
$GC_RIG_ROOT/worktrees/.cargo-targets/thunderdome-candidate-<workflow-root-id>/attempt-1
$GC_RIG_ROOT/.gc/cache/cargo-home
```

The candidate target must differ from every item, candidate, repair,
verification, and epoch lifecycle target. Ignore inherited
`CARGO_TARGET_DIR` and `CARGO_HOME`; do not create, guess, discover, or share a
mutable target directory.

The launcher supplies the shared sccache contract. Require nonempty inherited
`RUSTC_WRAPPER`, `SCCACHE_DIR`, and `SCCACHE_CACHE_SIZE`, preserve their exact
values, and launch the unchanged rendered aggregate command with
`CARGO_TARGET_DIR="$CARGO_TARGET_DIR"`, `CARGO_HOME="$CARGO_HOME"`,
`RUSTC_WRAPPER="$RUSTC_WRAPPER"`, `SCCACHE_DIR="$SCCACHE_DIR"`, and
`SCCACHE_CACHE_SIZE="$SCCACHE_CACHE_SIZE"` explicitly in its process
environment. Only Cargo home and sccache are shared; the target is isolated by
lifecycle ID and attempt. Do not invent cache environment variables.

Capture the rendered command, exit status, bounded sanitized output, duration,
canonical `CARGO_TARGET_DIR`, `CARGO_HOME`, and explicit sccache environment in
the aggregate verification evidence. After the command, require `HEAD` to
remain the pre-gate commit and run
`gc worktree list "thunderdome-candidate-<workflow-root-id>" --json` again.
Require the sole entry to retain that exact `head_sha`, `published_sha`, and
`published_ref`, with a nonempty unchanged `published_at`. This second
publication check is mandatory
even when the gate fails, so a losing candidate remains durably recoverable.

On a nonzero gate exit, mark this step failed only after recording the bounded
result and verified publication; do not write or approve the aggregate summary.
On success, additionally require the worktree to remain clean, then record and
read back `gc.thunderdome.validation_commit=<exact HEAD>` and
`gc.thunderdome.validation_target_dir=<registered cargo_target_dir>` on the
workflow root before closing pass. Do not edit source, create a commit, open a
PR, enqueue the candidate, or close source beads in this step.
