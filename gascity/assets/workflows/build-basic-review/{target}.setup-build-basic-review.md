Prepare the build-basic starter factory review.

Gather the requirements artifact, implementation plan, decomposition artifact,
implementation summary, changed-file summaries, task evidence, and verification
commands into one review context file under the build artifact root. Record that
path on the workflow root as `gc.build.code_review_context_path`.

The implementation source of truth is the canonical integration worktree
recorded on the workflow root. Read `gc.build.integration_work_dir`,
`gc.build.integration_branch`, `gc.build.integration_commit`, and
`gc.build.integration_status`. Require status `integrated`, an absolute existing
Git worktree, the recorded named branch, and a current `HEAD` equal to the
recorded commit. The workflow root's `gc.work_dir` must resolve to the same
worktree. Missing or stale integration metadata is an iterate finding against
review setup; do not review isolated per-item worktrees as if they were a
completed build.

The review context must anchor every relative source path and proof command to
the canonical integration worktree. Include an `## Integrated Build` section
before artifact excerpts with:

- absolute integration worktree path
- integration branch and exact commit
- aggregate changed files and proof commands
- source anchor ids, per-item commits, and summaries as provenance

Per-item worktrees are evidence only. Review the integrated branch and commit
that contains all successful work. If the integration worktree is absent,
dirty, detached, on a different branch, or at a different commit, close this
setup bead with `gc.outcome=fail` and record the stale integration evidence.
When writing artifact excerpts, append the actual file contents with commands
such as `cat "$REQUIREMENTS_PATH"` outside any quoted heredoc. Do not write
literal command substitutions such as `$(cat ...)` or `$(date ...)` into the
review context. Before closing this setup bead, verify the generated context
does not contain literal shell substitutions, for example with
`rg -n '\$\((cat|date)' "$CONTEXT_PATH"`; any match is a setup failure to repair
before setting `gc.outcome=pass`.

This starter factory intentionally uses only three review lanes so new users can
see fanout/fanin without a large reviewer roster.

Do not invoke provider-native subagents. Gas City graph lanes are the
delegation mechanism.

Close this setup bead with `gc.outcome=pass` only after the review context path
is recorded.
