Assemble and land the complete frozen epoch as one protected integration PR.
Never bisect, omit, or reorder its immutable candidate membership.

Resolve the epoch ID only from the workflow root. Read the epoch and its sealed
candidate IDs through `{{pack_root}}/assets/scripts/thunderdome.py status --json`; require state
`assembling`, base SHA `{{base_sha}}`, target ref `{{target_ref}}`, and no
invariant violation. Fetch the remote target and fail closed if it no longer
points at the pinned base.

Create or safely reuse an epoch worktree and branch named
`thunderdome/epoch-<epoch-id>` at exactly the pinned base. For candidates in the
sealed order, verify each exact commit exists and descends from the base, then
merge it with an explicit merge commit. Resolve textual conflicts while
preserving all candidate behavior. Run formatting, compilation, schema/migration
preflight, and fast tests required to make the aggregate PR structurally valid.
Commit integration-only fixes on the epoch branch. Do not run binary search or
remove a member to obtain green.

Push only the epoch branch. Open exactly one aggregate PR to
`{{target_ref}}`, enable the repository's protected checks and merge queue or
auto-merge, and wait on GitHub's event/check surface rather than a sleep loop.
Never bypass branch protection or use a force push. If preflight or protected
checks fail before merge, write a sanitized artifact, transition the epoch to
`failed` with `failure_class` and `evidence_ref`, and fail this step.

After GitHub reports the PR merged, read the actual merge commit from GitHub and
fetch `origin/{{target_ref}}`; require both exact SHAs match. Transition through
the state adapter:

```bash
{{pack_root}}/assets/scripts/thunderdome.py epoch transition --rig "${GC_RIG_NAME:?}" \
  --epoch-id "<epoch-id>" --state landed --landed-sha "<actual-merge-sha>" \
  --pr-url "<aggregate-pr-url>" --json
```

Then transition every sealed candidate from `frozen` to `landed` using
`{{pack_root}}/assets/scripts/thunderdome.py candidate transition --candidate-id <id> --state landed`.
Record the PR URL and actual merged SHA on the workflow root. Do not close source
beads: merged is not yet verified.
