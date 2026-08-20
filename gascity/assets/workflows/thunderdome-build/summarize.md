Write the aggregate candidate summary only after integration and serialized
aggregate Rust validation succeed.

Resolve the workflow root and its exact source membership, pinned base SHA,
integrated candidate commit, validated commit, item artifacts, and integration
worktree. Require `gc.thunderdome.validation_commit` to be a 40-character SHA
equal to both `gc.thunderdome.commit` and the clean integration worktree's
current `HEAD`; a missing or stale validation commit blocks the summary. Write to
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
the single aggregate gate command/result and its canonical non-`/tmp`
`CARGO_TARGET_DIR`, focused item test commands/results, and remaining risks
without private prompt or transcript content.

Derive every change claim from the pinned-base-to-candidate diff. Classify
production versus test changes by the changed lines' actual scope, not by file
path or deletion counts. Never describe a production reorganization unless the
diff contains it; when all hunks are inside test modules, say that explicitly.
Record the absolute path on the
workflow root as
both `gc.build.implementation_summary_path` and
`gc.implementation.summary_path`.

From the integration worktree run
`GC_BEAD_ID=<claimed-step-id> .gc/scripts/checks/build-artifact-valid.sh` and
repair every schema error before closing pass.
