Promote only the exact verified SHA to the stable release ref, then close source
beads. Merged, landed, or currently green is insufficient.

Resolve the epoch through the workflow root and require:

- `gc.thunderdome.state=verified`
- no projection invariant violations
- `verified_sha` equals the latest `landed_sha`
- every sealed candidate has `gc.thunderdome.state=verified`
- `origin/{{target_ref}}` still contains that exact SHA

Fetch the remote release ref `{{release_ref}}`. Update it atomically to the exact
verified SHA with a normal fast-forward push; never force. If it does not exist,
create it at that SHA. A concurrent update, non-fast-forward result, missing
protection, or ambiguous remote response is a hard failure. Read the remote ref
back and require exact equality.

Transition the epoch to `promoting`, then to `promoted` through the installed
adapter with `--release-sha <verified-sha>` and
`--release-ref refs/heads/{{release_ref}}`. Read status back and require
`gc.thunderdome.state=promoted`, release SHA/ref equality, and zero invariant
violations.

Only now close source beads from the sealed candidates. Close each source once
with a reason naming the verified SHA and epoch ID; a retry must treat an
already-closed source with the same evidence as success and any conflicting
closure as failure. Never close candidate, repair, or unrelated beads by label
search. Record the closed source IDs on the epoch and emit the normal bead close
events through `gc bd close`.

Finally verify the remote release ref again and write a sanitized promotion
artifact containing epoch ID, candidate IDs, source IDs, target ref, aggregate
PR, verified SHA, release ref, and timestamps. No source beads may close before
promotion evidence is durable.
