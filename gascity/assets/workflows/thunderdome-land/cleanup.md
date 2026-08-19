Clean formula-owned worktrees only after the landing scope settles. Cleanup must
never change the already-recorded workflow outcome.

Resolve the workflow root and epoch ID from typed metadata. Read the root,
epoch, candidates, source beads, and remote release ref. If the workflow did not
close pass, `gc.thunderdome.state=promoted` is absent, or the remote ref does not
equal the verified release SHA, preserve every worktree and record that cleanup
was skipped. Never turn a failed or incomplete epoch into a cleanup candidate.

For a promoted epoch, build an explicit ownership set from the epoch ID,
candidate IDs, source IDs, and repair bead IDs in typed Thunderdome state. For
each sealed candidate, also read its canonical summary and review paths. Require
both absolute paths to resolve inside the same registered Git worktree, require
that worktree to be a direct child of `$GC_RIG_ROOT/worktrees`, and require its
`HEAD` to equal the candidate's recorded commit. Add that exact path with its
worktree basename as the owning ID; an ambiguous or mismatched path is blocked
cleanup, never an inferred owner.

The only other eligible paths are direct children of
`$GC_RIG_ROOT/worktrees` whose names are one of:

- an exact candidate, source, or repair bead ID
- `thunderdome-epoch-<epoch-id>`
- `verify-<epoch-id>-r<N>`
- `repair-int-<epoch-id>-r<N>`

Do not search other directories, infer ownership from age, or remove a path not
in that set. For every existing eligible path, invoke:

```bash
{{pack_root}}/assets/scripts/cleanup-worktree.sh \
  "${GC_RIG_ROOT:?}" "<absolute-worktree-path>" "<owning-id>" \
  "<verified-release-sha>"
```

The script independently requires the path to belong to this repository, match
the owning ID, be clean, and have a `HEAD` reachable from the promoted SHA. It
uses normal `git worktree remove`. Never use `--force`, delete branches, or run
a global prune. Missing, dirty worktrees and worktrees with unreachable commits
must be preserved. Treat them as blocked cleanup, not permission to destroy
evidence.

Write `.gc/artifacts/<epoch-id>/cleanup.md` with the epoch ID, verified SHA,
exact removed paths, exact preserved paths, and each refusal reason. Do not
include prompts, credentials, or repository content. Record the artifact path
on the workflow root as `gc.cleanup.evidence_ref`; record removed paths as
`gc.cleanup.removed_paths` and dirty worktrees or other preserved paths as
`gc.cleanup.blocked_paths`. An unexpected state-read or command failure must
close this teardown fail so the relic remains visible. Otherwise close pass
after every eligible path is either removed or explicitly preserved and
recorded.
