Integrate the complete drained delivery unit into one immutable, durably
published candidate commit.

Resolve the claimed step and `<workflow-root-id>`. Read only root metadata and
the core-authored `gc.drain_manifest.v1`; do not reconstruct membership from
labels, descriptions, branches, or session logs. Require every manifest item
workflow to be closed pass with a schema-valid implementation artifact. Read
the exact 40-character `gc.thunderdome.base_sha` from this root and never
substitute a branch tip, remote ref, or local `HEAD`.

For each sorted source bead ID:

1. Require `gc.worktree.id=<source-bead-id>` and read
   `gc.worktree.path`, `work_dir`, `gc.work_dir`, `gc.cargo_target_dir`,
   `gc.cargo_home`, `gc.codestorage_ref`, and `gc.codestorage_sha` from that
   source bead.
2. Run `gc worktree list "<source-bead-id>" --json` from
   `${GC_RIG_ROOT:?}`. Require exactly one entry owned by that source bead at
   `$GC_RIG_ROOT/worktrees/<source-bead-id>`, based on the root's exact pinned
   SHA, with all bead path/cache fields equal to the registry.
3. Verify the path is an isolated worktree for this repository, is clean, and
   has a full 40-character `HEAD` descended from the pinned base. Require
   registry `head_sha` and `published_sha` and bead `gc.codestorage_sha` all to
   equal that exact `HEAD`; require `published=true`, a nonempty
   `published_ref`, and the same ref on the bead. Reject missing, unpublished,
   duplicate, mutable, ambiguous, or out-of-membership evidence.
4. Record that exact immutable item commit. Do not merge an item until its
   publication has been verified through both `gc worktree list` and the local
   checkout.

The candidate integration lifecycle ID is exactly `<workflow-root-id>` so the
Code Storage signer can authorize its rig bead prefix. Its owner is the same
workflow root ID. Resolve and canonicalize `${GC_RIG_ROOT:?}`, require
`${GC_RIG_NAME:?}`, and set its only permitted path and branch to:

```text
$GC_RIG_ROOT/worktrees/thunderdome-candidate-<workflow-root-id>
thunderdome/candidate-<workflow-root-id>
```

From `$GC_RIG_ROOT`, create or exactly reuse it at the pinned candidate base:

```sh
gc worktree create "<workflow-root-id>" \
  --owner "<workflow-root-id>" \
  --rig "${GC_RIG_NAME:?}" \
  --path "${GC_RIG_ROOT:?}/worktrees/thunderdome-candidate-<workflow-root-id>" \
  --base "<exact gc.thunderdome.base_sha>" \
  --branch "thunderdome/candidate-<workflow-root-id>" \
  --attempt 1 \
  --json
```

Creation failure, helper failure, malformed JSON, or any mismatch stops this
workflow before merging; never bypass `gc worktree create` with direct Git
lifecycle or Code Storage helper calls. Require the returned `id`, `owner`,
`rig`, `rig_root`, `path`, `attempt`, `base`, `branch`, and `head_sha` to match
exactly. Parse `path`, `cargo_target_dir`, and `cargo_home` from the returned
object, require them to be absolute, writable, outside `/tmp`, and require the
cache paths to equal:

```text
$GC_RIG_ROOT/worktrees/.cargo-targets/<workflow-root-id>/attempt-1
$GC_RIG_ROOT/.gc/cache/cargo-home
```

The target must not equal any item, candidate, repair, verification, or epoch
lifecycle target. Confirm this across
`gc worktree list --rig "${GC_RIG_NAME:?}" --json`: no different registry ID
may report the same `path` or `cargo_target_dir`. Copy
`$GC_RIG_ROOT/.gc/scripts` and `$GC_RIG_ROOT/.gc/schemas` into the returned
worktree for downstream artifact checks and hard-fail if either source tree or
required copied check is absent.

Merge every verified item commit in sorted source-bead order with explicit
merge commits. Resolve overlaps by preserving all accepted behavior; never
drop a candidate, bisect the delivery unit, or silently prefer one side. Do not
run the union of item verification commands, a repository-wide test suite, or
any broad Cargo validation in this step. In particular, this step must not run
`cargo test --workspace`, `cargo test --all`, `cargo check`, `cargo build`,
`cargo clippy`, or a repository wrapper that expands to an aggregate gate.
Resolve merge conflicts and commit integration-only fixes, but leave aggregate
Rust validation to the single serialized `validate` step after this step and
the completed drain barrier.

Require a clean worktree and resolve its full 40-character integration `HEAD`.
Before any candidate can hand off to validation, including a candidate that
later loses validation or review, publish it from `$GC_RIG_ROOT`:

```sh
gc worktree publish "<workflow-root-id>" --json
```

Publication is mandatory. A missing command/helper, nonzero exit, malformed
JSON, empty `published_ref`, or any mismatch fails integration. Require
`published=true`, `published_sha=<integration HEAD>`, matching `head_sha`,
`id`, `owner`, and `path`; then run
`gc worktree list "<workflow-root-id>" --json` and verify
its sole entry contains the same `published_ref` and `published_sha` and a
nonempty `published_at`.

Only after verified publication, record and read back on the workflow root:

- `gc.thunderdome.commit=<integration HEAD>`
- `gc.thunderdome.base_sha=<unchanged pinned SHA>`
- `gc.thunderdome.integration_worktree=<registered path>`
- `gc.thunderdome.published_ref=<published_ref>`
- `gc.thunderdome.published_sha=<integration HEAD>`
- `gc.worktree.id=<workflow-root-id>`
- `gc.worktree.owner=<workflow-root-id>`
- `gc.worktree.path=<registered path>`
- `work_dir=<registered path>`
- `gc.work_dir=<registered path>`
- `gc.cargo_target_dir=<registered cargo_target_dir>`
- `gc.cargo_home=<registered cargo_home>`

Do not open a PR, mutate trunk, enqueue before review, or close sources.
