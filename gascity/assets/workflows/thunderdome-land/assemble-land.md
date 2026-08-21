Assemble and land the complete frozen epoch as one protected integration PR.
Never bisect, omit, or reorder its immutable candidate membership.

Resolve the epoch ID only from the workflow root. Read the epoch and its sealed
candidate IDs through `{{pack_root}}/assets/scripts/thunderdome.py status --json`; require state
`assembling`, base SHA `{{base_sha}}`, target ref `{{target_ref}}`, and no
invariant violation. Run `git fetch --no-tags origin "{{target_ref}}"`, resolve
`FETCH_HEAD`, and fail closed if that exact SHA no longer equals the pinned base.

Create or exactly reuse the registered epoch checkout with lifecycle ID
`thunderdome-epoch-<epoch-id>`, owner `<epoch-id>`, attempt `1`, branch
`thunderdome/epoch-<epoch-id>`, and path
`$GC_RIG_ROOT/worktrees/thunderdome-epoch-<epoch-id>`:

```bash
epoch_worktree_id="thunderdome-epoch-<epoch-id>"
epoch_worktree="${GC_RIG_ROOT:?}/worktrees/$epoch_worktree_id"
gc worktree create "$epoch_worktree_id" \
  --owner "<epoch-id>" --rig "${GC_RIG_NAME:?}" --path "$epoch_worktree" \
  --base "{{base_sha}}" --branch "thunderdome/epoch-<epoch-id>" \
  --attempt 1 --json
```

Require the returned `id`, `owner`, `rig`, `rig_root`, `path`, `attempt`,
`base`, `branch`, and `head_sha` to match exactly. A pre-existing registration
is reusable only when every field matches; otherwise fail closed. Persist
`gc.worktree.id`, canonical `gc.worktree.path`, legacy `gc.work_dir`,
`gc.cargo_target_dir`, and `gc.cargo_home` on the workflow root and read them back.
Require `cargo_target_dir` to equal
`$GC_RIG_ROOT/worktrees/.cargo-targets/thunderdome-epoch-<epoch-id>/attempt-1`
and `cargo_home` to equal `$GC_RIG_ROOT/.gc/cache/cargo-home`. The launcher
supplies the shared sccache contract: require nonempty inherited
`RUSTC_WRAPPER`, `SCCACHE_DIR`, and `SCCACHE_CACHE_SIZE`, do not rewrite them,
and pass all three plus the registered Cargo paths explicitly to every Cargo
command. Never inherit a different target, invent cache settings, or discover
a sibling cache.

For candidates in the sealed order, verify each exact commit exists and descends
from the base, then merge it with an explicit merge commit. Resolve textual
conflicts while preserving all candidate behavior. Commit integration-only
fixes on the epoch branch. Do not run binary search or remove a member to obtain
green.

After the aggregate head is final and clean, from the returned `path`, run the
sealed repository preflight exactly:

```bash
{{full_gate_command}}
```

Pass the returned cache environment explicitly and unchanged to that command.
Do not substitute, widen, split, or infer another gate.

After the aggregate head is final and clean, publish it through the registered
capability before opening the PR:

```bash
gc worktree publish "thunderdome-epoch-<epoch-id>" --json
```

Require returned `published_sha` to equal the checkout's current exact `HEAD`.
Persist and read back `gc.thunderdome.published_ref=<published_ref>` and
`gc.thunderdome.published_sha=<published_sha>` as teardown evidence. The
Code Storage ref is durable recovery evidence but deliberately never reaches
GitHub; never pass `published_ref` to `gh`.

Publish the exact durable SHA to the unique GitHub integration ref
`refs/heads/thunderdome/epoch-<epoch-id>`. First read that full ref with
`git ls-remote --refs origin`. If it is absent, create it with the
absence-guarded command below. If it already exists, require it to resolve to
exactly `published_sha`; any other value is a hard failure.

```bash
git push --force-with-lease="refs/heads/thunderdome/epoch-<epoch-id>:" \
  origin "<published-sha>:refs/heads/thunderdome/epoch-<epoch-id>"
```

Read the remote ref back and require one exact `<published_sha>` match. Derive
the GitHub PR head and base branch names by removing the validated
`refs/heads/` prefixes from the integration ref and `{{target_ref}}`; never pass
a full ref where the GitHub API requires a branch name. Open exactly one
aggregate PR from the GitHub integration branch and wait on GitHub's
check/event surface rather than a sleep loop. Use branch protection and the
merge queue when the repository exposes them.

When the provider or account cannot protect this private branch, use the
equivalent fail-closed path: require every configured PR check green, re-read
the exact base and candidate head SHAs immediately before merge, and merge with
`gh pr merge --match-head-commit <candidate-head-sha>`. Record the unavailable
protection capability and all check/merge evidence. Outside the exact
absence-guarded integration-ref command above, never push directly to the
target, force push, merge a changed head, or treat missing checks as success.
On any preflight, publication, check, or merge failure, write a sanitized
artifact, transition the epoch to `failed` with `failure_class` and
`evidence_ref`, and fail this step. Publication ambiguity is failure and must
leave the registered checkout intact.

After GitHub reports the PR merged, read the actual merge commit from GitHub,
run `git fetch --no-tags origin "{{target_ref}}"`, resolve `FETCH_HEAD`, and
require both exact SHAs match. Transition through the state adapter:

```bash
{{pack_root}}/assets/scripts/thunderdome.py --rig "${GC_RIG_NAME:?}" \
  epoch transition "<epoch-id>" landed --landed-sha "<actual-merge-sha>" \
  --pr-url "<aggregate-pr-url>" --json
```

The epoch transition atomically advances every sealed candidate from `frozen`
to `landed`. Read the epoch and candidates back and require that state before
recording the PR URL and actual merged SHA on the workflow root. Do not close
source beads: merged is not yet verified.
