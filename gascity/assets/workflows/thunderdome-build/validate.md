Validate the integrated Rust candidate in one serial owner after the drain and
integration barriers have completed.

Resolve the claimed step and `<workflow-root-id>`. Read the core-authored
`gc.drain_manifest.v1` and require the drain control plus every manifest row to
be closed pass. Read `gc.thunderdome.integration_worktree` and the exact
40-character `gc.thunderdome.commit` from the workflow root. Require the path to
be an absolute Git worktree for this repository, require a clean worktree, and
require its `HEAD` to equal that recorded commit before running any command.
Never reconstruct membership or candidate state from branches, labels,
descriptions, or session logs.

This step is the only owner of the repository-wide Rust validation/check gate.
Run `{{aggregate_rust_gate_command}}` exactly once, serially, from the integration
worktree. Do not delegate it to an item worker, fan it out, background any part,
or start a second aggregate Cargo process. Do not rerun the union of per-item
commands: the configured repository gate is the single aggregate authority.
A missing or blank rendered command is a configuration failure.

Before the gate, create a workflow-owned target directory beside the integration
worktrees, never inside a source checkout and never under `/tmp`:

```sh
WORKTREES_DIR=$(dirname "$INTEGRATION_WORKTREE")
CARGO_TARGET_DIR="$WORKTREES_DIR/.cargo-targets/<workflow-root-id>/aggregate"
mkdir -p "$CARGO_TARGET_DIR"
CARGO_TARGET_DIR=$(cd "$CARGO_TARGET_DIR" && pwd -P)
case "$CARGO_TARGET_DIR" in /tmp|/tmp/*) exit 1 ;; esac
export CARGO_TARGET_DIR
```

Substitute the resolved workflow root ID rather than the literal placeholder.
Hard-fail if canonicalization escapes the workflow-owned
`.cargo-targets/<workflow-root-id>/aggregate` directory, if the target is shared
with an item worker or another workflow, or if it is not writable on disk. Never
reuse another live `target` tree. A shared sccache or immutable prewarmed base is
permitted only when supplied through the installed pack or provider environment;
consume that contract unchanged. Do not discover sibling
caches, invent cache environment variables, or configure shared writable cache
state in this workflow.

Capture the rendered command, exit status, bounded sanitized output, duration,
and canonical `CARGO_TARGET_DIR` in the aggregate verification evidence. On a
nonzero exit, mark this step failed and do not write or approve the aggregate
summary. On success, require the worktree to remain clean and require `HEAD` to
still equal the pre-gate `gc.thunderdome.commit`. Record and read back
`gc.thunderdome.validation_commit=<exact HEAD>` and
`gc.thunderdome.validation_target_dir=<canonical CARGO_TARGET_DIR>` on the
workflow root before closing pass. Do not edit source, create a commit, push,
open a PR, enqueue the candidate, or close source beads in this step.
