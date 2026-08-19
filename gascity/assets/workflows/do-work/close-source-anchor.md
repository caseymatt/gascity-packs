
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. Read `work_dir` from the source anchor and verify the implementation commit and
summary evidence are present in that worktree. Write per-item summary to
{{summary_path}} when set. If `summary_path` is not set, first use
`gc.implementation.summary_path` from the preceding implementation step when it
is present; otherwise use `{{artifact_root}}/task-<source-anchor-id>-summary.md`.

When reading beads with `gc bd show --json`, handle both an object and a
one-element list before reading metadata. `gc.work_dir` is the launcher rig
root, not the implementation worktree. If the source anchor `work_dir` is
missing, equals the launcher root, or points at a worktree without the
implementation commit, fail this step instead of closing the source anchor.

Before closing, publish the worktree so its commits survive the worktree. Run
`"$GC_CITY/tools/code-storage/gc-code-storage" publish "$GC_RIG" <source-anchor-id> 1 <work_dir>`
and record the returned `ref` and `headSha` on the source anchor as
`gc.codestorage_ref` and `gc.codestorage_sha`. These refs live in the ephemeral
namespace and never reach GitHub, so publishing does not add branches upstream.
If the source anchor carries `gc.codestorage=unavailable` from prepare, skip
this and continue.

Then reclaim the worktree, but only on published evidence. If
`gc.codestorage_sha` is present and equals the worktree's `HEAD`, the checkout
is disposable — the work is recoverable from Code Storage — so remove it with
`git worktree remove --force <work_dir>` followed by `git worktree prune` from
the launcher rig root, and stamp `gc.worktree_reclaimed=true`. If the sha is
missing or does not match, leave the worktree in place and stamp
`gc.worktree_reclaimed=false` with the reason; never delete a worktree whose
commits you have not confirmed elsewhere. A worktree here costs gigabytes —
mostly `target/` and `.hermit/` — so leaving one behind is expensive, but losing
the only copy of an agent's work is worse.

On success, close only `<source-anchor-id>` with `gc.outcome=pass`. Include the
verified commit and summary path in the source-anchor close reason. Read the
source anchor back with `gc bd show <source-anchor-id> --json` and verify
`status=closed` and `gc.outcome=pass`; if either check fails, fix the source
anchor before closing this step. Do not close this step with pass while the source anchor remains open. Then close this step. Do not close the drain-unit
convoy, parent convoy, or broader workflow root from this step.
