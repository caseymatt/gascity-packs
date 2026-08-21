Promote only the exact verified SHA to the stable release ref, then close source
beads. Merged, landed, or currently green is insufficient.

Resolve the epoch through the workflow root and require:

- `gc.thunderdome.state=verified`
- no projection invariant violations
- `verified_sha` equals the latest `landed_sha`
- every sealed candidate has `gc.thunderdome.state=verified`
- `git fetch --no-tags origin "{{target_ref}}"` resolves `FETCH_HEAD` to that exact SHA

Require protection on `{{release_ref}}` when the provider/account exposes it.
When the provider cannot protect a private release ref, use the equivalent
fail-closed path: require the aggregate PR's complete configured check rollup
green, re-read `{{target_ref}}` at the exact verified SHA, and record the
unavailable protection capability in the promotion artifact before mutation.
Missing protection capability alone is not a failure when all fallback evidence
is present.

Fetch the validated full release ref with
`git fetch --no-tags origin "{{release_ref}}"`. If it exists, require its exact
SHA to be an ancestor of the verified SHA. Update it atomically to the exact
verified SHA with a normal fast-forward push; never force. If it does not exist,
create it at that SHA. A concurrent update, non-fast-forward result, failed
configured check, or ambiguous remote response is a hard failure. Read the
remote ref back and require exact equality.

Transition the epoch from `verified` to `promoting` through the installed
adapter. Write a sanitized promotion artifact containing epoch ID, candidate
IDs, source IDs, target ref, aggregate PR, verified SHA, release ref, and
timestamps. Then make the terminal transition:

```bash
{{pack_root}}/assets/scripts/thunderdome.py --rig "${GC_RIG_NAME:?}" \
  epoch transition "<epoch-id>" promoted \
  --release-sha "<verified-sha>" --release-ref "{{release_ref}}" \
  --evidence-ref "<absolute-promotion-artifact-path>" --json
```

The `promoted` adapter transition atomically closes each sealed source bead
with the verified SHA and epoch ID. Do not close sources directly or search by
label. A retry must treat the adapter's matching terminal state as success and
any conflicting closure as failure.

Finally read the epoch, candidates, source beads, and remote release ref back.
Require `gc.thunderdome.state=promoted`, release SHA/ref equality, every source
closed exactly once, and zero invariant violations. No source may close before
the promotion artifact is durable.
