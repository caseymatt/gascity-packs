Assemble and land the complete frozen epoch as one protected integration PR.
Never bisect, omit, or reorder its immutable candidate membership.

Resolve the epoch ID only from the workflow root. Read the epoch and its sealed
candidate IDs through `{{pack_root}}/assets/scripts/thunderdome.py status --json`; require state
`assembling`, base SHA `{{base_sha}}`, target ref `{{target_ref}}`, and no
invariant violation. Run `git fetch --no-tags origin "{{target_ref}}"`, resolve
`FETCH_HEAD`, and fail closed if that exact SHA no longer equals the pinned base.

Create or safely reuse the epoch worktree at
`$GC_RIG_ROOT/worktrees/thunderdome-epoch-<epoch-id>` and branch
`thunderdome/epoch-<epoch-id>` at exactly the pinned base. For candidates in the
sealed order, verify each exact commit exists and descends from the base, then
merge it with an explicit merge commit. Resolve textual conflicts while
preserving all candidate behavior. Run formatting, compilation, schema/migration
preflight, and fast tests required to make the aggregate PR structurally valid.
Commit integration-only fixes on the epoch branch. Do not run binary search or
remove a member to obtain green.

Push only the epoch branch. Derive the GitHub PR base branch by removing the
validated `refs/heads/` prefix from `{{target_ref}}`; never pass a full ref where
the GitHub API requires a branch name. Open exactly one aggregate PR and wait
on GitHub's check/event surface rather than a sleep loop. Use branch protection
and the merge queue when the repository exposes them.

When the provider or account cannot protect this private branch, use the
equivalent fail-closed path: require every configured PR check green, re-read
the exact base and candidate head SHAs immediately before merge, and merge with
`gh pr merge --match-head-commit <candidate-head-sha>`. Record the unavailable
protection capability and all check/merge evidence. Never push directly to the
target, force push, merge a changed head, or treat missing checks as success.
On any preflight, check, or merge failure, write a sanitized artifact, transition
the epoch to `failed` with `failure_class` and `evidence_ref`, and fail this step.

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
