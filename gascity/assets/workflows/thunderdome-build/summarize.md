Write the aggregate candidate summary only after integration succeeds.

Resolve the workflow root and its exact source membership, pinned base SHA,
integrated candidate commit, item artifacts, and integration worktree. Write to
`{{summary_path}}` when non-empty; otherwise write under
`.gc/artifacts/<workflow-root-id>/implementation/implementation-summary.md` in
the integration worktree. The path must be absolute after resolution.

The artifact schema is `gc.build.implementation-summary.v1`. YAML front matter
must use mapping objects and identify workflow formula `thunderdome-build`,
methodology `{pack: gascity, name: thunderdome-build}`, producer stage
`summarize`, positive attempt, approved status, and trace upstream/coverage
entries for every source bead and item artifact. Source entries use
`path: beads/<id>` and `hash: bead:<id>`; file and artifact hashes are
scheme-qualified. Every source ID appears exactly once in trace coverage and in
the Markdown coverage table with status `covered`.

The body contains these exact `##` headings in order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

Include pinned base SHA, exact candidate commit, item commits, changed files,
observed verification commands/results, and remaining risks without private
prompt or transcript content. Record the absolute path on the workflow root as
both `gc.build.implementation_summary_path` and
`gc.implementation.summary_path`.

From the integration worktree run
`GC_BEAD_ID=<claimed-step-id> .gc/scripts/checks/build-artifact-valid.sh` and
repair every schema error before closing pass.
