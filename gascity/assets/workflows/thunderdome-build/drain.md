Drain every delivery-unit source bead through `thunderdome-work-item` in a
separate isolated worktree and session. Core owns the drain-unit convoy,
manifest, and fan-out lifecycle. Each item gets exclusive source access.

Do not enumerate members manually, create substitute work beads, publish item
branches as PRs, or close source beads. A failed item must fail this aggregate
candidate build; a candidate is only valid when every member has a tested commit
and a schema-valid implementation summary.
