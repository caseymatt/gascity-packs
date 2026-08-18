Observe and operate the Continuous Thunderdome state ledger for a rig.

Usage:

```text
gc gc thunderdome [--rig <rig>] status [--json] [--trunk-sha <sha>] [--fail-on-violation]
gc gc thunderdome [--rig <rig>] candidate enqueue ...
gc gc thunderdome [--rig <rig>] candidate transition ...
gc gc thunderdome [--rig <rig>] epoch open ...
gc gc thunderdome [--rig <rig>] epoch transition ...
```

`status` is read-only. It reports queue depth and age, stale candidates, active
epoch state, repair progress, the latest promoted release SHA, and invariant
violations. Pass the current trunk SHA to classify queued candidates whose base
has gone stale. `--fail-on-violation` exits nonzero when typed ledger invariants
are broken.

Mutation commands are infrastructure adapters used by the Thunderdome formulas.
They validate legal transitions, seal epoch membership, emit low-cardinality
transition events, and reject conflicting replay. Prefer the formulas for normal
operation; use direct mutation commands only for recovery with recorded evidence.
