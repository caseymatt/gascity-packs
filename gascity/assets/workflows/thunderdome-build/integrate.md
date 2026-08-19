Integrate the complete drained delivery unit into one immutable candidate commit.

Resolve the claimed step and workflow root. Read only root metadata and the
core-authored `gc.drain_manifest.v1`; do not reconstruct membership from labels,
descriptions, branches, or session logs. Require every manifest item workflow
to be closed pass with a schema-valid implementation artifact.

For each sorted source bead ID:

1. Read its absolute `work_dir` from that source bead.
2. Verify the path is an isolated worktree for this repository, has no uncommitted
   changes, and its `HEAD` is a 40-character commit descended from the root's
   pinned `gc.thunderdome.base_sha`.
3. Record that exact item commit. Reject missing, duplicate, mutable, or
   out-of-membership evidence.

Create or safely reuse one integration worktree and branch named from the
workflow root under the rig's `worktrees/` directory, starting exactly at the
pinned base SHA. Merge every item commit in sorted source-bead order with
explicit merge commits. Resolve overlaps by preserving all accepted behavior;
never drop a candidate, bisect the delivery unit, or silently prefer one side.
Do not run the union of item verification commands, a repository-wide test
suite, or any broad Cargo validation in this step. In particular, this step
must not run `cargo test --workspace`, `cargo test --all`, `cargo check`,
`cargo build`, `cargo clippy`, or a repository wrapper that expands to an
aggregate gate. Resolve merge conflicts and commit integration-only fixes, but
leave all aggregate Rust validation to the single serialized `validate` step
that follows this step and the completed drain barrier.

Require a clean worktree. Copy the rig `.gc/scripts` and `.gc/schemas` into this
worktree for downstream artifact checks. Record and read back on the workflow
root:

- `gc.thunderdome.commit=<integration HEAD>`
- `gc.thunderdome.base_sha=<unchanged pinned SHA>`
- `gc.thunderdome.integration_worktree=<absolute path>`
- `gc.work_dir=<same absolute integration worktree>`

Do not push, create a PR, mutate trunk, enqueue before review, or close sources.
