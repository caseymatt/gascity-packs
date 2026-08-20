from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tomllib
import unittest
from contextlib import redirect_stdout


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "assets" / "scripts" / "thunderdome.py"
NOW = "2026-08-18T13:00:00Z"
LATER = "2026-08-18T13:05:00Z"
BASE_SHA = "1" * 40
COMMIT_SHA = "2" * 40
LANDED_SHA = "3" * 40
REPAIR_SHA = "4" * 40


def load_module():
    spec = importlib.util.spec_from_file_location("gc_thunderdome", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load thunderdome.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StateMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def candidate(self):
        return self.module.new_candidate_metadata(
            source_beads=["sp-a", "sp-b"],
            delivery_unit="DU-REC-02",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/rig/.gc/artifacts/summary.json",
            review_path="/rig/.gc/artifacts/review.json",
            now=NOW,
        )

    def epoch(self):
        return self.module.new_epoch_metadata(
            candidate_ids=["sp-candidate-a", "sp-candidate-b"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )

    def test_candidate_is_canonical_and_order_independent(self) -> None:
        first = self.candidate()
        second = self.module.new_candidate_metadata(
            source_beads=["sp-b", "sp-a"],
            delivery_unit="DU-REC-02",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/rig/.gc/artifacts/summary.json",
            review_path="/rig/.gc/artifacts/review.json",
            now=NOW,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["gc.thunderdome.kind"], "candidate")
        self.assertEqual(first["gc.thunderdome.state"], "queued")
        self.assertEqual(first["gc.thunderdome.source_beads"], ["sp-a", "sp-b"])
        self.assertEqual(len(first["gc.thunderdome.candidate_key"]), 64)

    def test_candidate_requires_exact_commits_and_absolute_artifacts(self) -> None:
        cases = [
            {"commit": "abc"},
            {"base_sha": "main"},
            {"summary_path": "relative/summary.json"},
            {"review_path": "relative/review.json"},
            {"source_beads": []},
            {"delivery_unit": ""},
        ]
        defaults = {
            "source_beads": ["sp-a"],
            "delivery_unit": "DU-REC-02",
            "commit": COMMIT_SHA,
            "base_sha": BASE_SHA,
            "summary_path": "/summary.json",
            "review_path": "/review.json",
            "now": NOW,
        }

        for override in cases:
            with self.subTest(override=override), self.assertRaises(self.module.StateError):
                self.module.new_candidate_metadata(**(defaults | override))

    def test_epoch_membership_is_canonical_and_hashed(self) -> None:
        first = self.epoch()
        second = self.module.new_epoch_metadata(
            candidate_ids=["sp-candidate-b", "sp-candidate-a"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["gc.thunderdome.state"], "assembling")
        self.assertEqual(first["gc.thunderdome.candidate_ids"], ["sp-candidate-a", "sp-candidate-b"])
        self.assertEqual(len(first["gc.thunderdome.membership_hash"]), 64)

    def test_happy_epoch_path_records_bounded_history(self) -> None:
        epoch = self.epoch()
        epoch = self.module.transition_metadata(
            epoch,
            "landed",
            now=LATER,
            evidence={"landed_sha": LANDED_SHA, "pr_url": "https://github.com/o/r/pull/1"},
        )
        epoch = self.module.transition_metadata(epoch, "verifying", now=LATER)
        epoch = self.module.transition_metadata(
            epoch,
            "verified",
            now=LATER,
            evidence={"verified_sha": LANDED_SHA, "verification_ref": "artifact://gate-1"},
        )
        epoch = self.module.transition_metadata(epoch, "promoting", now=LATER)
        epoch = self.module.transition_metadata(
            epoch,
            "promoted",
            now=LATER,
            evidence={"release_sha": LANDED_SHA, "release_ref": "refs/heads/release/stable"},
        )

        self.assertEqual(epoch["gc.thunderdome.state"], "promoted")
        self.assertEqual(epoch["gc.thunderdome.release_sha"], LANDED_SHA)
        self.assertEqual([entry["seq"] for entry in epoch["gc.thunderdome.history"]], list(range(6)))

    def test_red_epoch_repairs_forward_without_bisection(self) -> None:
        epoch = self.epoch()
        epoch = self.module.transition_metadata(
            epoch,
            "landed",
            now=LATER,
            evidence={"landed_sha": LANDED_SHA},
        )
        epoch = self.module.transition_metadata(epoch, "verifying", now=LATER)
        epoch = self.module.transition_metadata(
            epoch,
            "red",
            now=LATER,
            evidence={"failure_class": "test_failure", "evidence_ref": "artifact://failure-1"},
        )
        epoch = self.module.transition_metadata(
            epoch,
            "repairing",
            now=LATER,
            evidence={"repair_bead_ids": ["sp-fix-a", "sp-fix-b"]},
        )
        epoch = self.module.transition_metadata(
            epoch,
            "verifying",
            now=LATER,
            evidence={"landed_sha": REPAIR_SHA},
        )
        epoch = self.module.transition_metadata(
            epoch,
            "verified",
            now=LATER,
            evidence={"verified_sha": REPAIR_SHA, "verification_ref": "artifact://gate-2"},
        )

        self.assertEqual(epoch["gc.thunderdome.landed_sha"], REPAIR_SHA)
        self.assertEqual(epoch["gc.thunderdome.verified_sha"], REPAIR_SHA)
        history = json.dumps(epoch["gc.thunderdome.history"])
        self.assertNotIn("bisect", history.lower())
        self.assertIn("sp-fix-a", history)

    def test_timeout_and_cancellation_are_distinguishable(self) -> None:
        timed_out = self.module.transition_metadata(
            self.epoch(),
            "cancelled",
            now=LATER,
            evidence={"failure_class": "timeout", "evidence_ref": "artifact://timeout-1"},
        )
        cancelled = self.module.transition_metadata(
            self.epoch(),
            "cancelled",
            now=LATER,
            evidence={"failure_class": "cancelled", "evidence_ref": "artifact://cancel-1"},
        )

        self.assertEqual(timed_out["gc.thunderdome.failure_class"], "timeout")
        self.assertEqual(cancelled["gc.thunderdome.failure_class"], "cancelled")

    def test_illegal_transition_fails_without_mutating_input(self) -> None:
        original = self.epoch()
        snapshot = json.loads(json.dumps(original))

        with self.assertRaisesRegex(self.module.StateError, "illegal epoch transition"):
            self.module.transition_metadata(
                original,
                "promoted",
                now=LATER,
                evidence={"release_sha": LANDED_SHA, "release_ref": "refs/heads/release/stable"},
            )

        self.assertEqual(original, snapshot)

    def test_replayed_transition_is_idempotent_but_conflicting_replay_fails(self) -> None:
        landed = self.module.transition_metadata(
            self.epoch(),
            "landed",
            now=LATER,
            evidence={"landed_sha": LANDED_SHA},
        )
        replayed = self.module.transition_metadata(
            landed,
            "landed",
            now="2026-08-18T13:06:00Z",
            evidence={"landed_sha": LANDED_SHA},
        )
        self.assertEqual(replayed, landed)

        with self.assertRaisesRegex(self.module.StateError, "conflicting replay"):
            self.module.transition_metadata(
                landed,
                "landed",
                now="2026-08-18T13:06:00Z",
                evidence={"landed_sha": REPAIR_SHA},
            )

    def test_evidence_contract_rejects_secrets_and_unbounded_content(self) -> None:
        for evidence in [
            {"landed_sha": LANDED_SHA, "token": "secret"},
            {"landed_sha": LANDED_SHA, "message": "raw agent transcript"},
            {"landed_sha": LANDED_SHA, "log": "x" * 10000},
        ]:
            with self.subTest(evidence=evidence), self.assertRaises(self.module.StateError):
                self.module.transition_metadata(self.epoch(), "landed", now=LATER, evidence=evidence)

    def test_verified_sha_must_match_latest_landed_sha(self) -> None:
        epoch = self.module.transition_metadata(
            self.epoch(), "landed", now=LATER, evidence={"landed_sha": LANDED_SHA}
        )
        epoch = self.module.transition_metadata(epoch, "verifying", now=LATER)

        with self.assertRaisesRegex(self.module.StateError, "verified_sha"):
            self.module.transition_metadata(
                epoch,
                "verified",
                now=LATER,
                evidence={"verified_sha": REPAIR_SHA, "verification_ref": "artifact://wrong"},
            )

    def test_release_sha_must_match_verified_sha(self) -> None:
        epoch = self.epoch()
        for state, evidence in [
            ("landed", {"landed_sha": LANDED_SHA}),
            ("verifying", {}),
            ("verified", {"verified_sha": LANDED_SHA, "verification_ref": "artifact://gate"}),
            ("promoting", {}),
        ]:
            epoch = self.module.transition_metadata(epoch, state, now=LATER, evidence=evidence)

        with self.assertRaisesRegex(self.module.StateError, "release_sha"):
            self.module.transition_metadata(
                epoch,
                "promoted",
                now=LATER,
                evidence={"release_sha": REPAIR_SHA, "release_ref": "refs/heads/release/stable"},
            )

    def test_epoch_refuses_stale_candidate_base(self) -> None:
        candidate = {"id": "sp-candidate", "metadata": self.candidate()}

        with self.assertRaisesRegex(self.module.StateError, "refresh it before freezing"):
            self.module.validate_epoch_candidates([candidate], base_sha=LANDED_SHA)

    def test_epoch_refuses_duplicate_source_membership(self) -> None:
        first = {"id": "sp-candidate-a", "metadata": self.candidate()}
        second = {"id": "sp-candidate-b", "metadata": self.candidate()}

        with self.assertRaisesRegex(self.module.StateError, "duplicate source beads"):
            self.module.validate_epoch_candidates([first, second], base_sha=BASE_SHA)


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def candidate_record(self, bead_id: str = "sp-candidate-a", state: str = "queued") -> dict:
        metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a"],
            delivery_unit="DU-ONE",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/summary.json",
            review_path="/review.json",
            now=NOW,
        )
        if state != "queued":
            evidence = {"epoch_id": "sp-epoch-a"} if state == "frozen" else {}
            metadata = self.module.transition_metadata(metadata, state, now=LATER, evidence=evidence)
        return {"id": bead_id, "status": "in_progress", "metadata": metadata}

    def epoch_record(self, state: str = "assembling") -> dict:
        metadata = self.module.new_epoch_metadata(
            candidate_ids=["sp-candidate-a"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        transitions = {
            "landed": [("landed", {"landed_sha": LANDED_SHA})],
            "verifying": [("landed", {"landed_sha": LANDED_SHA}), ("verifying", {})],
            "verified": [
                ("landed", {"landed_sha": LANDED_SHA}),
                ("verifying", {}),
                ("verified", {"verified_sha": LANDED_SHA, "verification_ref": "artifact://gate"}),
            ],
            "repairing": [
                ("landed", {"landed_sha": LANDED_SHA}),
                ("verifying", {}),
                ("red", {"failure_class": "test_failure", "evidence_ref": "artifact://red"}),
                ("repairing", {"repair_bead_ids": ["sp-repair-a", "sp-repair-b"]}),
            ],
            "promoted": [
                ("landed", {"landed_sha": LANDED_SHA}),
                ("verifying", {}),
                ("verified", {"verified_sha": LANDED_SHA, "verification_ref": "artifact://gate"}),
                ("promoting", {}),
                ("promoted", {"release_sha": LANDED_SHA, "release_ref": "refs/heads/release/stable"}),
            ],
        }
        for target, evidence in transitions.get(state, []):
            metadata = self.module.transition_metadata(metadata, target, now=LATER, evidence=evidence)
        return {"id": "sp-epoch-a", "status": "in_progress", "metadata": metadata}

    def test_projection_exposes_queue_epoch_release_and_history(self) -> None:
        candidate = self.candidate_record(state="frozen")
        epoch = self.epoch_record(state="assembling")
        result = self.module.project_state(
            [candidate, epoch],
            now="2026-08-18T13:10:00Z",
            source_states={"sp-source-a": "in_progress"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["queue"]["frozen"], 1)
        self.assertEqual(result["active_epochs"][0]["id"], "sp-epoch-a")
        self.assertEqual(result["active_epochs"][0]["transition_count"], 1)
        self.assertEqual(result["violations"], [])

    def test_projection_exposes_repair_progress_without_failure_content(self) -> None:
        candidate = self.candidate_record(state="frozen")
        candidate["metadata"] = self.module.transition_metadata(
            candidate["metadata"], "landed", now=LATER, evidence={}
        )
        result = self.module.project_state(
            [candidate, self.epoch_record(state="repairing")],
            now=LATER,
            source_states={"sp-source-a": "in_progress"},
        )

        epoch = result["active_epochs"][0]
        self.assertEqual(epoch["state"], "repairing")
        self.assertEqual(epoch["repair_bead_count"], 2)
        self.assertEqual(epoch["failure_class"], "test_failure")
        self.assertNotIn("artifact://red", self.module.format_status(result))

    def test_projection_surfaces_stale_queued_candidates_without_failing_health(self) -> None:
        result = self.module.project_state(
            [self.candidate_record()],
            now=LATER,
            source_states={"sp-source-a": "in_progress"},
            trunk_sha=LANDED_SHA,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["queue"]["stale_queued"], 1)
        self.assertEqual(result["queue"]["stale_candidate_ids"], ["sp-candidate-a"])

    def test_projection_reports_promoted_release_ref(self) -> None:
        candidate = self.candidate_record(state="frozen")
        candidate["metadata"] = self.module.transition_metadata(
            candidate["metadata"], "landed", now=LATER, evidence={}
        )
        candidate["metadata"] = self.module.transition_metadata(
            candidate["metadata"], "verified", now=LATER, evidence={}
        )
        result = self.module.project_state(
            [candidate, self.epoch_record(state="promoted")],
            now=LATER,
            source_states={"sp-source-a": "closed"},
        )

        self.assertEqual(result["release"]["sha"], LANDED_SHA)
        self.assertEqual(result["release"]["ref"], "refs/heads/release/stable")

    def test_projection_detects_membership_tampering(self) -> None:
        candidate = self.candidate_record(state="frozen")
        epoch = self.epoch_record()
        epoch["metadata"]["gc.thunderdome.candidate_ids"] = ["sp-candidate-a", "sp-injected"]

        result = self.module.project_state([candidate, epoch], now=LATER, source_states={})

        self.assertFalse(result["ok"])
        self.assertIn("epoch_membership_hash_mismatch", {v["code"] for v in result["violations"]})
        self.assertIn("epoch_candidate_missing", {v["code"] for v in result["violations"]})

    def test_projection_allows_candidate_recovery_from_cancelled_epoch(self) -> None:
        candidate = self.candidate_record(state="frozen")
        candidate["metadata"]["gc.thunderdome.epoch_id"] = "sp-epoch-b"
        cancelled = self.epoch_record()
        cancelled["metadata"] = self.module.transition_metadata(
            cancelled["metadata"],
            "cancelled",
            now=LATER,
            evidence={"failure_class": "cancelled", "evidence_ref": "artifact://cancelled"},
        )
        replacement = self.epoch_record()
        replacement["id"] = "sp-epoch-b"

        result = self.module.project_state(
            [candidate, cancelled, replacement],
            now=LATER,
            source_states={"sp-source-a": "in_progress"},
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("candidate_epoch_mismatch", {v["code"] for v in result["violations"]})

    def test_projection_detects_candidate_epoch_mismatch(self) -> None:
        candidate = self.candidate_record(state="frozen")
        candidate["metadata"]["gc.thunderdome.epoch_id"] = "sp-other-epoch"
        result = self.module.project_state(
            [candidate, self.epoch_record()], now=LATER, source_states={"sp-source-a": "in_progress"}
        )

        self.assertIn("candidate_epoch_mismatch", {v["code"] for v in result["violations"]})

    def test_projection_detects_premature_source_close(self) -> None:
        result = self.module.project_state(
            [self.candidate_record(), self.epoch_record()],
            now=LATER,
            source_states={"sp-source-a": "closed"},
        )

        self.assertIn("source_closed_before_verification", {v["code"] for v in result["violations"]})

    def test_projection_accepts_source_close_after_verified_candidate(self) -> None:
        candidate = self.candidate_record(state="frozen")
        candidate["metadata"] = self.module.transition_metadata(
            candidate["metadata"], "landed", now=LATER, evidence={}
        )
        candidate["metadata"] = self.module.transition_metadata(
            candidate["metadata"], "verified", now=LATER, evidence={}
        )
        epoch = self.epoch_record(state="verified")

        result = self.module.project_state(
            [candidate, epoch], now=LATER, source_states={"sp-source-a": "closed"}
        )

        self.assertNotIn("source_closed_before_verification", {v["code"] for v in result["violations"]})

    def test_projection_detects_duplicate_active_source_candidates(self) -> None:
        first = self.candidate_record("sp-candidate-a")
        second = self.candidate_record("sp-candidate-b")

        result = self.module.project_state([first, second], now=LATER, source_states={})

        self.assertIn("duplicate_active_source_candidate", {v["code"] for v in result["violations"]})

    def test_human_status_contains_actionable_state_not_private_content(self) -> None:
        result = self.module.project_state(
            [self.candidate_record()], now=LATER, source_states={"sp-source-a": "in_progress"}
        )
        output = self.module.format_status(result)

        self.assertIn("Queue: queued=1", output)
        self.assertIn("stale=0", output)
        self.assertIn("Invariant violations: 0", output)
        self.assertNotIn("summary.json", output)
        self.assertNotIn("review.json", output)




class FormulaContractTests(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parents[1]

    def formula(self, name: str) -> dict:
        path = self.ROOT / "formulas" / f"{name}.formula.toml"
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def prompt(self, relative: str) -> str:
        return (self.ROOT / "assets" / "workflows" / relative).read_text(encoding="utf-8")

    def test_build_serializes_one_aggregate_rust_gate_after_the_drain(self) -> None:
        data = self.formula("thunderdome-build")
        steps = data["steps"]
        ids = [step["id"] for step in steps]

        self.assertEqual(
            ids,
            [
                "prepare",
                "drain",
                "wait-for-drain",
                "integrate",
                "validate",
                "summarize",
                "review",
                "enqueue",
            ],
        )
        self.assertEqual(steps[1]["drain"]["formula"], "thunderdome-work-item")
        self.assertEqual(steps[3]["needs"], ["wait-for-drain"])
        self.assertEqual(steps[4]["needs"], ["integrate"])
        self.assertEqual(steps[4]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(steps[5]["needs"], ["validate"])
        self.assertTrue(data["vars"]["aggregate_rust_gate_command"]["required"])
        self.assertEqual(
            steps[5]["check"]["check"]["path"],
            ".gc/scripts/checks/build-artifact-valid.sh",
        )
        self.assertEqual(
            steps[6]["check"]["check"]["path"],
            ".gc/scripts/checks/implementation-review-approved.sh",
        )
        self.assertNotIn("publish", ids)
        self.assertNotIn("close-source-anchor", ids)
        self.assertEqual(data["requires"]["formula_compiler"], ">=2.0.0")

    def test_readme_launch_supplies_the_required_serialized_rust_gate(self) -> None:
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(
            "--var 'aggregate_rust_gate_command=cargo test --workspace'",
            readme,
        )
        self.assertIn("focused behavioral tests", readme)
        self.assertIn("after the drain barrier", " ".join(readme.lower().split()))

    def test_rust_validation_assets_isolate_worker_targets_and_one_gate_owner(self) -> None:
        item = self.prompt("thunderdome-work-item/implement.md")
        integrate = self.prompt("thunderdome-build/integrate.md")
        validate = self.prompt("thunderdome-build/validate.md")
        build_assets = [
            self.prompt(f"thunderdome-build/{name}.md")
            for name in (
                "prepare",
                "drain",
                "wait-for-drain",
                "integrate",
                "validate",
                "summarize",
                "review",
                "enqueue",
            )
        ]

        for broad_command in (
            "cargo test --workspace",
            "cargo test --all",
            "cargo check",
            "cargo build",
            "cargo clippy",
        ):
            with self.subTest(command=broad_command):
                self.assertIn(broad_command, item)
        self.assertIn("focused behavioral tests only", item)
        self.assertIn("must not run", item)
        self.assertIn("CARGO_TARGET_DIR", item)
        self.assertIn("CARGO_HOME", item)
        self.assertIn("<source-anchor-id>", item)
        self.assertIn("/tmp", item)
        self.assertIn("RUSTC_WRAPPER", item)
        self.assertIn("SCCACHE_DIR", item)
        self.assertIn("SCCACHE_CACHE_SIZE", item)
        self.assertIn(
            ".cargo-targets/<source-anchor-id>/attempt-1",
            item,
        )
        self.assertIn("must not run", integrate)
        self.assertEqual(
            sum("{{aggregate_rust_gate_command}}" in asset for asset in build_assets),
            1,
        )
        self.assertIn("exactly once", validate)
        self.assertIn("serial", validate)
        self.assertIn("CARGO_TARGET_DIR", validate)
        self.assertIn("CARGO_HOME", validate)
        self.assertIn("<workflow-root-id>", validate)
        self.assertIn("/tmp", validate)
        self.assertIn("RUSTC_WRAPPER", validate)
        self.assertIn("SCCACHE_DIR", validate)
        self.assertIn("SCCACHE_CACHE_SIZE", validate)
        self.assertIn(
            ".cargo-targets/thunderdome-candidate-<workflow-root-id>/attempt-1",
            validate,
        )
        self.assertIn("gc.thunderdome.validation_commit=<exact HEAD>", validate)
        summary = self.prompt("thunderdome-build/summarize.md")
        review = self.prompt("thunderdome-build/review.md")
        enqueue = self.prompt("thunderdome-build/enqueue.md")
        for downstream in (summary, review, enqueue):
            with self.subTest(downstream=downstream[:24]):
                self.assertIn("gc.thunderdome.validation_commit", downstream)

    def test_candidate_worktrees_use_central_lifecycle_topology_and_publication(self) -> None:
        item_prepare = self.prompt("thunderdome-work-item/prepare-worktree.md")
        item_implement = self.prompt("thunderdome-work-item/implement.md")
        candidate_integrate = self.prompt("thunderdome-build/integrate.md")
        candidate_validate = self.prompt("thunderdome-build/validate.md")
        candidate_enqueue = self.prompt("thunderdome-build/enqueue.md")
        lifecycle_assets = (
            item_prepare,
            item_implement,
            candidate_integrate,
            candidate_validate,
            candidate_enqueue,
        )

        self.assertIn('gc worktree create "<source-anchor-id>"', item_prepare)
        self.assertIn('--owner "<source-anchor-id>"', item_prepare)
        self.assertIn('--base "$PINNED_BASE_SHA"', item_prepare)
        self.assertIn(
            "$GC_RIG_ROOT/worktrees/<source-anchor-id>",
            item_prepare,
        )
        self.assertIn("gc.drain_control_id", item_prepare)
        self.assertIn("gc.thunderdome.base_sha", item_prepare)
        self.assertIn(
            ".cargo-targets/<source-anchor-id>/attempt-1",
            item_prepare,
        )
        self.assertIn("hard prepare failure", item_prepare)

        self.assertIn(
            'gc worktree list "<source-anchor-id>" --json',
            item_implement,
        )
        self.assertIn(
            'gc worktree publish "<source-anchor-id>" --json',
            item_implement,
        )
        self.assertIn("published_ref", item_implement)
        self.assertIn("published_sha", item_implement)

        candidate_id = "thunderdome-candidate-<workflow-root-id>"
        self.assertIn(f'gc worktree create "{candidate_id}"', candidate_integrate)
        self.assertIn('--owner "<workflow-root-id>"', candidate_integrate)
        self.assertIn('--base "<exact gc.thunderdome.base_sha>"', candidate_integrate)
        self.assertIn(
            f"$GC_RIG_ROOT/worktrees/{candidate_id}",
            candidate_integrate,
        )
        self.assertIn(
            f".cargo-targets/{candidate_id}/attempt-1",
            candidate_integrate,
        )
        self.assertIn(
            f'gc worktree publish "{candidate_id}" --json',
            candidate_integrate,
        )
        self.assertIn("including a candidate that", candidate_integrate)
        self.assertIn("later loses validation or review", candidate_integrate)
        self.assertIn("Creation failure", candidate_integrate)
        self.assertIn("gc.thunderdome.published_ref", candidate_integrate)
        self.assertIn("gc.thunderdome.published_sha", candidate_integrate)
        self.assertIn("gc.worktree.owner=<workflow-root-id>", candidate_integrate)
        for lifecycle_fragment in (
            "gc.worktree.id=thunderdome-candidate-<workflow-root-id>",
            "gc.worktree.owner=<workflow-root-id>",
            'gc bd update "<candidate-id>"',
            'gc bd show "<candidate-id>" --json',
            "JSON object or a one-element list",
            "gc.thunderdome.kind=candidate",
            "gc.thunderdome.state=queued",
            "Do not acknowledge or hand off the queued state",
            "gc.thunderdome.candidate_id",
        ):
            with self.subTest(lifecycle_fragment=lifecycle_fragment):
                self.assertIn(lifecycle_fragment, candidate_enqueue)

        for asset in (candidate_integrate, candidate_validate):
            with self.subTest(asset=asset[:36]):
                self.assertIn(
                    f'gc worktree list "{candidate_id}" --json',
                    asset,
                )
                self.assertIn("published_ref", asset)
                self.assertIn("published_sha", asset)
        self.assertIn("even when the gate fails", candidate_validate)
        self.assertIn(
            "losing candidate remains durably recoverable",
            candidate_validate,
        )

        for metadata_key in (
            "gc.worktree.id",
            "gc.worktree.path",
            "work_dir",
            "gc.work_dir",
            "gc.cargo_target_dir",
            "gc.cargo_home",
        ):
            with self.subTest(metadata_key=metadata_key):
                self.assertIn(metadata_key, item_prepare)
                self.assertIn(metadata_key, candidate_integrate)

        item_target = (
            "$GC_RIG_ROOT/worktrees/.cargo-targets/"
            "<source-anchor-id>/attempt-1"
        )
        candidate_target = (
            "$GC_RIG_ROOT/worktrees/.cargo-targets/"
            f"{candidate_id}/attempt-1"
        )
        self.assertNotEqual(item_target, candidate_target)
        self.assertIn(item_target, item_prepare)
        self.assertIn(candidate_target, candidate_integrate)
        self.assertNotIn(candidate_target, item_prepare)
        self.assertNotIn(item_target, candidate_integrate)
        for asset in lifecycle_assets:
            with self.subTest(direct_lifecycle=asset[:36]):
                self.assertNotIn("git worktree add", asset)
                self.assertNotIn("git worktree remove", asset)
                self.assertNotIn("git worktree prune", asset)
                self.assertNotIn("git push", asset)
                self.assertNotIn("gc-code-storage", asset)

    def test_work_item_never_closes_its_source_anchor(self) -> None:
        data = self.formula("thunderdome-work-item")
        ids = [step["id"] for step in data["steps"]]

        self.assertEqual(ids, ["prepare-worktree", "implement"])
        self.assertNotIn("close-source-anchor", ids)
        self.assertEqual(
            data["steps"][1]["check"]["check"]["path"],
            ".gc/scripts/checks/build-artifact-valid.sh",
        )

    def test_land_formula_is_a_single_ordered_epoch_control_loop(self) -> None:
        data = self.formula("thunderdome-land")
        steps = data["steps"]
        ids = [step["id"] for step in steps]

        self.assertEqual(
            ids,
            ["freeze", "assemble-land", "verify-repair", "promote", "body", "cleanup"],
        )
        self.assertEqual(steps[1]["needs"], ["freeze"])
        self.assertEqual(steps[2]["needs"], ["assemble-land"])
        self.assertEqual(steps[3]["needs"], ["verify-repair"])
        self.assertEqual(steps[4]["needs"], ["promote"])
        self.assertEqual(steps[5]["needs"], ["body"])
        self.assertEqual(steps[4]["metadata"]["gc.kind"], "scope")
        self.assertEqual(steps[5]["metadata"]["gc.kind"], "cleanup")
        self.assertEqual(data["requires"]["formula_compiler"], ">=2.0.0")
        self.assertEqual(data["vars"]["target_ref"]["default"], "refs/heads/main")
        self.assertEqual(
            data["vars"]["release_ref"]["default"], "refs/heads/release/stable"
        )

    def test_agent_contracts_use_the_state_tool_and_fix_forward(self) -> None:
        freeze = self.prompt("thunderdome-land/freeze.md")
        assemble = self.prompt("thunderdome-land/assemble-land.md")
        repair = self.prompt("thunderdome-land/verify-repair.md")
        promote = self.prompt("thunderdome-land/promote.md")
        enqueue = self.prompt("thunderdome-build/enqueue.md")

        self.assertIn("{{pack_root}}/assets/scripts/thunderdome.py", freeze)
        self.assertIn("epoch open", freeze)
        self.assertIn("{{pack_root}}/assets/scripts/thunderdome.py", assemble)
        self.assertIn("epoch transition", assemble)
        self.assertIn("fix forward", repair.lower())
        self.assertIn("never bisect", repair.lower())
        self.assertIn("gc.thunderdome.state=promoted", promote)
        self.assertIn("close source", promote.lower())
        self.assertIn("{{pack_root}}/assets/scripts/thunderdome.py", enqueue)
        self.assertIn("candidate enqueue", enqueue)
        self.assertIn("adapter transition atomically closes", promote.lower())
        self.assertNotIn("gc bd close", promote)

    def test_land_prompts_fetch_validated_full_refs_without_remote_path_composition(self) -> None:
        for relative in (
            "thunderdome-land/freeze.md",
            "thunderdome-land/assemble-land.md",
            "thunderdome-land/verify-repair.md",
            "thunderdome-land/promote.md",
        ):
            prompt = self.prompt(relative)
            self.assertNotIn("origin/{{target_ref}}", prompt)
            self.assertIn('git fetch --no-tags origin "{{target_ref}}"', prompt)
            self.assertNotIn("candidate transition", prompt)
        enqueue = self.prompt("thunderdome-build/enqueue.md")
        freeze = self.prompt("thunderdome-land/freeze.md")
        assemble = self.prompt("thunderdome-land/assemble-land.md")
        self.assertIn('thunderdome.py --rig "${GC_RIG_NAME:?}"', enqueue)
        self.assertIn('thunderdome.py --rig "${GC_RIG_NAME:?}"', freeze)
        self.assertNotIn("--epoch-id", assemble)
        self.assertNotIn("--state landed", assemble)
        self.assertIn("--match-head-commit", assemble)
        self.assertIn("equivalent fail-closed", assemble.lower())


class ReconcilePlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def candidate(self, bead_id: str, created_at: str, *, base_sha: str = BASE_SHA):
        return {
            "id": bead_id,
            "created_at": created_at,
            "metadata": self.module.new_candidate_metadata(
                source_beads=[f"source-{bead_id}"],
                delivery_unit=f"delivery-{bead_id}",
                commit=COMMIT_SHA,
                base_sha=base_sha,
                summary_path=f"/rig/.gc/artifacts/{bead_id}-summary.json",
                review_path=f"/rig/.gc/artifacts/{bead_id}-review.json",
                now=created_at,
            ),
        }

    def test_oldest_wait_enqueues_a_bounded_epoch(self) -> None:
        records = [
            self.candidate("sp-candidate-b", "2026-08-18T12:40:00Z"),
            self.candidate("sp-candidate-a", "2026-08-18T12:20:00Z"),
            self.candidate("sp-stale", "2026-08-18T12:10:00Z", base_sha=REPAIR_SHA),
        ]

        plan = self.module.plan_reconcile(
            records,
            now=NOW,
            trunk_sha=BASE_SHA,
            max_depth=8,
            max_age_seconds=1800,
        )

        self.assertTrue(plan["due"])
        self.assertEqual(plan["reason"], "oldest_age")
        self.assertEqual(plan["candidate_ids"], ["sp-candidate-a", "sp-candidate-b"])
        self.assertEqual(plan["stale_candidate_ids"], ["sp-stale"])

    def test_active_epoch_blocks_duplicate_dispatch(self) -> None:
        epoch = {
            "id": "sp-epoch-a",
            "metadata": self.module.new_epoch_metadata(
                candidate_ids=["sp-candidate-a"],
                base_sha=BASE_SHA,
                target_ref="refs/heads/main",
                now=NOW,
            ),
        }
        plan = self.module.plan_reconcile(
            [self.candidate("sp-candidate-a", NOW), epoch],
            now=LATER,
            trunk_sha=BASE_SHA,
            max_depth=1,
            max_age_seconds=1800,
        )

        self.assertFalse(plan["due"])
        self.assertEqual(plan["reason"], "active_epoch")
        self.assertEqual(plan["active_epoch_ids"], ["sp-epoch-a"])

    def test_reconcile_resumes_an_undispatched_assembling_epoch(self) -> None:
        candidate = self.candidate("sp-candidate-a", NOW)
        candidate["metadata"] = self.module.transition_metadata(
            candidate["metadata"], "frozen", now=NOW, evidence={"epoch_id": "sp-epoch-a"}
        )
        epoch = {
            "id": "sp-epoch-a",
            "metadata": self.module.new_epoch_metadata(
                candidate_ids=["sp-candidate-a"],
                base_sha=BASE_SHA,
                target_ref="refs/heads/main",
                now=NOW,
            ),
        }
        calls = []

        class Client:
            def list_thunderdome(_self):
                return [candidate, epoch]
            def show(_self, _bead_ids):
                return [{"id": "source-sp-candidate-a", "status": "in_progress"}]


            def run(_self, args):
                calls.append(list(args))
                return {"workflow_id": "sp-workflow-a"}

            def update_metadata(_self, bead_id, metadata):
                self.assertEqual(bead_id, "sp-epoch-a")
                self.assertEqual(
                    metadata["gc.thunderdome.workflow_id"], "sp-workflow-a"
                )
                return {"id": bead_id, "metadata": metadata}

        args = self.module.argparse.Namespace(
            rig="sprocket",
            now=LATER,
            trunk_sha=BASE_SHA,
            max_depth=8,
            max_age_seconds=1800,
            dry_run=False,
            full_gate_command="just ci",
            operator="gc.run-operator",
            target_ref="refs/heads/main",
        )
        result = self.module.reconcile(Client(), args)
        self.assertEqual(result["action"], "dispatched")
        self.assertEqual(result["workflow_id"], "sp-workflow-a")
        self.assertEqual(calls[0][:4], ["sling", "sprocket/gc.run-operator", "sp-epoch-a", "--on"])
        self.assertIn("full_gate_command=just ci", calls[0])


    def test_reconcile_fails_closed_on_projection_invariants(self) -> None:
        first = self.candidate("sp-candidate-a", NOW)
        second = self.candidate("sp-candidate-b", NOW)
        second["metadata"]["gc.thunderdome.source_beads"] = first["metadata"][
            "gc.thunderdome.source_beads"
        ]

        class Client:
            def list_thunderdome(_self):
                return [first, second]

            def show(_self, _bead_ids):
                return [{"id": "source-sp-candidate-a", "status": "in_progress"}]

        args = self.module.argparse.Namespace(
            rig="sprocket",
            now=LATER,
            trunk_sha=BASE_SHA,
            max_depth=8,
            max_age_seconds=1800,
            dry_run=True,
            full_gate_command="just ci",
            operator="gc.run-operator",
            target_ref="refs/heads/main",
        )
        with self.assertRaisesRegex(self.module.StateError, "invariant"):
            self.module.reconcile(Client(), args)


class AdapterTests(unittest.TestCase):
    @classmethod

    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_client_scopes_commands_and_redacts_subprocess_output(self) -> None:
        calls: list[list[str]] = []

        def runner(args, _env):
            calls.append(list(args))
            return subprocess.CompletedProcess(
                args,
                17,
                stdout="",
                stderr="Authorization: Bearer private-token",
            )

        client = self.module.BeadClient(
            gc_bin="/bin/gc",
            city="/city",
            rig="sprocket",
            runner=runner,
        )
        with self.assertRaises(self.module.CommandError) as raised:
            client.list_thunderdome()

        self.assertEqual(calls[0][:5], ["/bin/gc", "--city", "/city", "--rig", "sprocket"])
        self.assertNotIn("private-token", str(raised.exception))
        self.assertIn("exit 17", str(raised.exception))

    def test_client_decodes_structured_metadata_strings_and_rejects_malformed_values(self) -> None:
        candidate = self.module.new_candidate_metadata(
            source_beads=["sp-source"],
            delivery_unit="DU-ONE",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/summary.json",
            review_path="/review.json",
            now=NOW,
        )
        encoded = dict(candidate)
        for key in (
            "gc.thunderdome.source_beads",
            "gc.thunderdome.history",
        ):
            encoded[key] = json.dumps(encoded[key])

        def valid_runner(args, _env):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps([{"id": "sp-candidate", "metadata": encoded}]),
                stderr="",
            )

        client = self.module.BeadClient(runner=valid_runner)
        metadata = client.show(["sp-candidate"])[0]["metadata"]
        self.assertEqual(metadata["gc.thunderdome.source_beads"], ["sp-source"])
        transitioned = self.module.transition_metadata(
            metadata,
            "frozen",
            now=LATER,
            evidence={"epoch_id": "sp-epoch"},
        )
        self.assertEqual(transitioned["gc.thunderdome.history"][-1]["to"], "frozen")

        malformed = dict(encoded)
        malformed["gc.thunderdome.history"] = "["

        def malformed_runner(args, _env):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps([{"id": "sp-candidate", "metadata": malformed}]),
                stderr="",
            )

        with self.assertRaisesRegex(self.module.CommandError, "gc.thunderdome.history"):
            self.module.BeadClient(runner=malformed_runner).show(["sp-candidate"])

    def test_transition_event_is_low_cardinality_and_content_free(self) -> None:
        calls: list[list[str]] = []

        def runner(args, _env):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout='{"ok":true}', stderr="")

        client = self.module.BeadClient(runner=runner)
        client.emit_transition("sp-epoch", "epoch", "red", 3)

        payload = json.loads(calls[0][calls[0].index("--payload") + 1])
        self.assertEqual(
            payload,
            {"schema_version": "1", "kind": "epoch", "state": "red", "transition_seq": 3},
        )
        self.assertNotIn("message", payload)
        self.assertNotIn("log", payload)

    def test_status_cli_returns_machine_readable_invariant_state(self) -> None:
        metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source"],
            delivery_unit="DU-ONE",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/summary.json",
            review_path="/review.json",
            now=NOW,
        )

        def runner(args, _env):
            if "list" in args:
                output = [{"id": "sp-candidate", "status": "in_progress", "metadata": metadata}]
            elif "show" in args:
                output = [{"id": "sp-source", "status": "in_progress", "metadata": {}}]
            else:
                raise AssertionError(f"unexpected command: {args}")
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(output), stderr="")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = self.module.main(["--rig", "sprocket", "status", "--json"], runner=runner)

        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["queue"]["queued"], 1)


class CommandExtensionTests(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parents[1]

    def test_pack_command_exposes_the_state_cli(self) -> None:
        command_dir = self.ROOT / "commands" / "thunderdome"
        manifest = tomllib.loads((command_dir / "command.toml").read_text(encoding="utf-8"))
        result = subprocess.run(
            [str(command_dir / "run.sh"), "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "GC_PACK_DIR": str(self.ROOT)},
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Manage and observe Continuous Thunderdome state", result.stdout)
        self.assertIn("candidate", result.stdout)
        self.assertIn("epoch", result.stdout)
        self.assertIn("reconcile", result.stdout)
        result_schema = json.loads(
            (command_dir / "schemas" / "result.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result_schema["properties"]["schema_version"]["const"], "1")
        self.assertEqual(
            manifest["description"],
            "Observe and operate Continuous Thunderdome state",
        )


if __name__ == "__main__":
    unittest.main()
