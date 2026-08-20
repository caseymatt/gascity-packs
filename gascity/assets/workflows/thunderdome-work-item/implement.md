
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. For a
synthetic drain-unit convoy, the source anchor is the original drain member in
`gc.drain_member_id`, not the synthetic convoy id.

Do not infer the source anchor from dependency ids such as the
`prepare-worktree` step. Read the claimed step bead's `gc.root_bead_id`, read
that do-work root with `gc bd show <root-bead-id> --json`, then read the root
metadata `gc.input_convoy_id`. Read that input convoy with `gc bd show
<input-convoy-id> --json`; if the JSON output is a one-element list, unwrap the
first element before reading metadata. If the input convoy has
`gc.synthetic_kind=drain-unit-convoy`, use its `gc.drain_member_id` as the
source anchor. Otherwise use the input convoy id as the source anchor.

Treat the central registry as lifecycle authority. Require
`gc.worktree.id=<source-anchor-id>` and `gc.worktree.path=<registered path>` on
the source anchor, then run
`gc worktree list "<source-anchor-id>" --json` from `${GC_RIG_ROOT:?}`. Require
an array containing exactly one entry whose `id`, `owner`, `rig`, `rig_root`,
`attempt`, `base`, `path`, `cargo_target_dir`, and `cargo_home` match the
prepare metadata and pinned drain base. Set `WORKTREE`, `CARGO_TARGET_DIR`, and
`CARGO_HOME` only from those registry fields. Require the source anchor and
claimed step values for `gc.worktree.path`, `work_dir`, `gc.work_dir`,
`gc.cargo_target_dir`, and `gc.cargo_home` to equal the registry exactly.
Missing, malformed, ambiguous, stale, or mismatched registry or bead data
fails this step before editing.

Require the registered worktree path to be absolute, to equal
`$GC_RIG_ROOT/worktrees/<source-anchor-id>`, to be an existing worktree for
this repository, and not to be the launcher checkout. Run `cd "$WORKTREE"` and
verify `pwd -P` equals the registered path before any source read, source edit,
test, file hash, `git add`, or `git commit`. If a command uses the launcher
checkout path for source work, verification, hashes, or commits, the step is
invalid and must fail. Use `${GC_RIG_ROOT:?}` directly for rig-owned scripts
and artifacts; do not reinterpret a launcher current directory as the rig root.

Do not edit files in the launcher checkout. Implement only the owned source
anchor boundary, run sandboxed verification from inside the worktree, and make a
focused commit in the worktree. Leave the source anchor open; only verified
stable promotion may close it. Close only this implementation step when done.

Rust verification in this parallel item lane is limited to focused behavioral tests only.
Select the smallest test target and exact test name or module that
proves the owned acceptance behavior. An item worker must not run repository-wide
or compilation-only Cargo work, including `cargo test --workspace`, `cargo test --all`,
`cargo test --all-targets`, `cargo check`, `cargo build`, or `cargo clippy`,
or any repository wrapper that expands to those aggregate commands. Do not run a
union of other items' commands. Formatting, workspace checks, broad tests, and
the repository aggregate Rust gate belong exclusively to the serialized
`thunderdome-build` validation step after the drain barrier.

Before the first focused Cargo test, require the registry-provided
`CARGO_TARGET_DIR` and `CARGO_HOME` to be canonical absolute writable
directories with these exact values:

```text
$GC_RIG_ROOT/worktrees/.cargo-targets/<source-anchor-id>/attempt-1
$GC_RIG_ROOT/.gc/cache/cargo-home
```

Hard-fail if either path differs from its registered value, resolves under
`/tmp`, or if the target equals another registered lifecycle's target. Ignore
any inherited `CARGO_TARGET_DIR` or `CARGO_HOME`; never construct a target,
discover one under a sibling worktree, or copy a mutable target tree.

The launcher supplies the shared sccache contract. Require nonempty inherited
`RUSTC_WRAPPER`, `SCCACHE_DIR`, and `SCCACHE_CACHE_SIZE`, do not rewrite their
values, and launch every focused Cargo command with
`CARGO_TARGET_DIR="$CARGO_TARGET_DIR"`, `CARGO_HOME="$CARGO_HOME"`,
`RUSTC_WRAPPER="$RUSTC_WRAPPER"`, `SCCACHE_DIR="$SCCACHE_DIR"`, and
`SCCACHE_CACHE_SIZE="$SCCACHE_CACHE_SIZE"` explicitly in that command's
environment. The target is lifecycle-isolated; only Cargo home and sccache are
shared. Do not invent any cache environment variable.

Record the canonical `CARGO_TARGET_DIR`, `CARGO_HOME`, and every focused test
command with its explicit cache environment and observed result in
`## Verification`.

Write or update the task summary with these schema-required body sections,
using the exact `##` headings below in this order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

The `## Verification` section must include both the first verification command
and the final proof command, with the observed pass/fail result.

Write the summary as a `gc.build.implementation-summary.v1` artifact and record
its absolute path on the workflow root bead as `gc.implementation.summary_path`
before closing.
Include a Markdown coverage table. The validator only recognizes a table with
an `ID` column and a `Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.implementation-summary.v1`
- `workflow: {id: <workflow-root-id>, formula: <root-workflow-formula>}`
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: do-work, stage: implement, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For the source anchor bead, use `path: beads/<source-anchor-id>` and
  `hash: bead:<source-anchor-id>`. For changed files or upstream build
  artifacts, use repo-relative paths and scheme-qualified hashes such as
  `sha256:<digest>` or `git:<revision>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.

After making each focused implementation or repair commit, require the
worktree to be clean and resolve its full 40-character `HEAD`. Before that
attempt can settle or hand off, publish the registered lifecycle from
`${GC_RIG_ROOT:?}`:

```sh
gc worktree publish "<source-anchor-id>" --json
```

A missing command or helper, nonzero exit, malformed JSON, or publication
mismatch fails the attempt; never continue with an unpublished commit. Require
the returned `id`, `owner`, `path`, `head_sha`, `published`, `published_ref`,
and `published_sha` to match the source anchor, registered worktree, and exact
current `HEAD`, with nonempty `published_ref` and `published=true`. Run
`gc worktree list "<source-anchor-id>" --json` and require its sole entry to
contain the same `published_ref` and `published_sha` and a nonempty
`published_at`. Persist
and read back `gc.codestorage_ref=<published_ref>` and
`gc.codestorage_sha=<published_sha>` on the source anchor and workflow root.
Every later repair commit must be republished and replace that evidence with
its new exact `HEAD`.

Artifact validation: this step is gated by
`.gc/scripts/checks/build-artifact-valid.sh`, which validates the summary
recorded at `gc.implementation.summary_path` (fallbacks
`gc.build.implementation_summary_path`, then `gc.var.summary_path`) against
schema `gc.build.implementation-summary.v1`. Before closing this step, use the
canonical `${GC_RIG_ROOT:?}` rather than bead `gc.work_dir`, then run the same
validator locally from that rig root with
`GC_BEAD_ID=<claimed-step-id> .gc/scripts/checks/build-artifact-valid.sh`; fix
every reported validation error before setting `gc.outcome=pass`. On repair
attempts with an explicit positive `gc.attempt` greater than 1, read the
validator errors from `gc.attempt_log` on the validation loop control bead (the
dependent of this step bead) and repair the summary in place instead of
rewriting it. Two bounded repair attempts follow the first failure; exhausting
them closes this stage with `gc.outcome=fail` and machine-readable validation
errors that block downstream stages. Never ask questions in headless mode;
record unresolved ambiguity inside the summary.

Immediately before either pass or fail settlement, require `HEAD` to remain the
registry's verified `published_sha`; if validation or repair changed `HEAD`,
publish and verify it again first. Never close this step or hand off an item
whose exact final commit is not durably published.
