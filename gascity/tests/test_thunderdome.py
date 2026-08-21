from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
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

class InjectedCrash(RuntimeError):
    pass


class DeterministicRunner:
    def __init__(self, module) -> None:
        self.module = module
        self.records: dict[str, dict] = {}
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.mutations = 0
        self.crash_at: int | None = None
        self.lose_next_sling = False
        self.workflows: dict[str, str] = {}
        self.sling_effects = 0
        self.close_effects: dict[str, int] = {}
        self.events: list[dict] = []
        self.formula_fingerprint = "a" * 64
        self.change_formula_after_show = False

    def add(
        self,
        bead_id: str,
        *,
        status: str = "in_progress",
        metadata: dict | None = None,
        envelope: dict | None = None,
    ) -> dict:
        if metadata is not None and envelope is not None:
            raise AssertionError("choose raw metadata or an authoritative envelope")
        stored = (
            self.module.stored_metadata(envelope)
            if envelope is not None
            else dict(metadata or {})
        )
        record = {"id": bead_id, "status": status, "metadata": stored}
        self.records[bead_id] = record
        return record

    @staticmethod
    def _copy(value):
        return json.loads(json.dumps(value))

    @staticmethod
    def _value(args: list[str], flag: str) -> str:
        return args[args.index(flag) + 1]

    def _effect(self) -> None:
        self.mutations += 1
        if self.crash_at == self.mutations:
            raise InjectedCrash(f"crash after mutation {self.mutations}")

    def _completed(self, args, output, returncode: int = 0):
        return subprocess.CompletedProcess(
            args,
            returncode,
            stdout=json.dumps(output),
            stderr="" if returncode == 0 else "deterministic fake failure",
        )

    def __call__(self, raw_args, env):
        args = list(raw_args)
        self.calls.append(args)
        self.envs.append(dict(env))
        command_index = next(
            index
            for index, value in enumerate(args)
            if value in {"bd", "sling", "event", "formula"}
        )
        command = args[command_index:]
        if command[:2] == ["bd", "show"]:
            ids = command[2 : command.index("--json")]
            missing = [bead_id for bead_id in ids if bead_id not in self.records]
            if missing:
                return self._completed(args, [], 1)
            return self._completed(
                args, [self._copy(self.records[bead_id]) for bead_id in ids]
            )
        if command[:2] == ["bd", "list"]:
            rows = [
                self._copy(record)
                for record in self.records.values()
                if self.module.KIND in record.get("metadata", {})
            ]
            return self._completed(args, rows)
        if command[:2] == ["bd", "create"]:
            bead_id = self._value(command, "--id")
            if bead_id in self.records:
                return self._completed(args, {}, 1)
            record = {
                "id": bead_id,
                "status": self._value(command, "--status"),
                "metadata": json.loads(self._value(command, "--metadata")),
            }
            self.records[bead_id] = record
            self._effect()
            return self._completed(args, self._copy(record))
        if command[:2] == ["bd", "metadata-cas"]:
            bead_id = command[2]
            record = self.records.get(bead_id)
            if record is None:
                return self._completed(args, {}, 1)
            key = self._value(command, "--key")
            expected = self._value(command, "--expected")
            value = self._value(command, "--value")
            current = record.setdefault("metadata", {}).get(key, "")
            current_wire = (
                current
                if isinstance(current, str)
                else self.module.canonical_json(current)
            )
            swapped = current_wire == expected
            if swapped:
                record["metadata"][key] = (
                    json.loads(value)
                    if key in {*self.module.STRUCTURED_ID_METADATA_KEYS, self.module.HISTORY}
                    else value
                )
                self._effect()
            return self._completed(args, {"swapped": swapped})
        if command[:2] == ["bd", "update"]:
            bead_id = command[2]
            record = self.records[bead_id]
            if "--status" in command:
                record["status"] = self._value(command, "--status")
            if "--metadata" in command:
                record["metadata"] = json.loads(self._value(command, "--metadata"))
            self._effect()
            return self._completed(args, self._copy(record))
        if command[:2] == ["bd", "close"]:
            bead_id = command[2]
            record = self.records[bead_id]
            if record["status"] != "closed":
                record["status"] = "closed"
                record["close_reason"] = self._value(command, "--reason")
                self.close_effects[bead_id] = self.close_effects.get(bead_id, 0) + 1
                self._effect()
            return self._completed(args, self._copy(record))
        if command[:2] == ["formula", "show"]:
            fingerprint = self.formula_fingerprint
            if self.change_formula_after_show:
                self.formula_fingerprint = "b" * 64
                self.change_formula_after_show = False
            return self._completed(
                args, {"ok": True, "compiled_fingerprint": fingerprint}
            )
        if command and command[0] == "sling":
            if (
                env.get("GC_EXPECTED_FORMULA_FINGERPRINT", "")
                != self.formula_fingerprint
            ):
                return self._completed(args, {}, 1)
            source_id = command[2]
            workflow_id = self.workflows.get(source_id)
            if not workflow_id:
                workflow_id = f"wf-{source_id}"
                self.workflows[source_id] = workflow_id
                self.sling_effects += 1
            if self.lose_next_sling:
                self.lose_next_sling = False
                return self._completed(args, {}, 75)
            return self._completed(args, {"root_id": workflow_id})
        if command[:2] == ["event", "emit"]:
            payload = json.loads(self._value(command, "--payload"))
            self.events.append(payload)
            return self._completed(args, {"ok": True, "submitted": True})
        raise AssertionError(f"unexpected fake command: {command}")


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
            "promotion_committing",
            now=LATER,
            evidence={"release_sha": LANDED_SHA, "release_ref": "refs/heads/release/stable"},
        )
        epoch = self.module.transition_metadata(
            epoch,
            "promoted",
            now=LATER,
            evidence={"release_sha": LANDED_SHA, "release_ref": "refs/heads/release/stable"},
        )

        self.assertEqual(epoch["gc.thunderdome.state"], "promoted")
        self.assertEqual(epoch["gc.thunderdome.release_sha"], LANDED_SHA)
        self.assertEqual([entry["seq"] for entry in epoch["gc.thunderdome.history"]], list(range(7)))

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

        epoch = self.module.transition_metadata(
            epoch,
            "promotion_committing",
            now=LATER,
            evidence={"release_sha": REPAIR_SHA, "release_ref": "refs/heads/release/stable"},
        )
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


    def test_epoch_history_is_bounded_and_exact_replay_does_not_append(self) -> None:
        epoch = self.module.transition_metadata(
            self.epoch(), "landed", now=LATER, evidence={"landed_sha": LANDED_SHA}
        )
        epoch = self.module.transition_metadata(epoch, "verifying", now=LATER)
        for index in range(80):
            epoch = self.module.transition_metadata(
                epoch,
                "red",
                now=LATER,
                evidence={
                    "failure_class": "test_failure",
                    "evidence_ref": f"artifact://failure-{index}",
                },
            )
            epoch = self.module.transition_metadata(
                epoch,
                "repairing",
                now=LATER,
                evidence={"repair_bead_ids": [f"sp-fix-{index}"]},
            )
            epoch = self.module.transition_metadata(
                epoch,
                "verifying",
                now=LATER,
                evidence={"landed_sha": REPAIR_SHA},
            )
        replayed = self.module.transition_metadata(
            epoch,
            "verifying",
            now="2026-08-18T14:00:00Z",
            evidence={"landed_sha": REPAIR_SHA},
        )

        self.assertEqual(replayed, epoch)
        self.assertEqual(len(epoch["gc.thunderdome.history"]), 64)
        sequences = [entry["seq"] for entry in epoch["gc.thunderdome.history"]]
        self.assertEqual(sequences, list(range(sequences[0], sequences[0] + 64)))


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
                ("promotion_committing", {"release_sha": LANDED_SHA, "release_ref": "refs/heads/release/stable"}),
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




class DeployedOrderContractTests(unittest.TestCase):
    ORDER_PATH = (
        pathlib.Path(__file__).resolve().parents[3]
        / "gc-city"
        / "orders"
        / "thunderdome-sprocket.toml"
    )

    def test_sprocket_reconcile_uses_city_cooldown_contract(self) -> None:
        if not self.ORDER_PATH.is_file():
            self.skipTest("deployed dogfood city order is not present")
        data = tomllib.loads(self.ORDER_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            data,
            {
                "order": {
                    "description": "Reconcile the Sprocket Continuous Thunderdome queue",
                    "trigger": "cooldown",
                    "interval": "1m",
                    "scope": "city",
                    "timeout": "60s",
                    "idempotent": False,
                    "no_work_gate": False,
                    "exec": (
                        "git -C /home/exedev/workspace/sprocket fetch --no-tags origin "
                        "refs/heads/main && trunk_sha=$(git -C /home/exedev/workspace/sprocket "
                        "rev-parse FETCH_HEAD) && gc gc thunderdome --rig sprocket reconcile "
                        "--trunk-sha $trunk_sha --target-ref refs/heads/main --max-depth 8 "
                        "--max-age-seconds 1800 --full-gate-command 'just ci' --json"
                    ),
                }
            },
        )


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
        self.assertIn("already-sealed epoch", freeze)
        self.assertIn("durable control intent", freeze)
        self.assertNotIn("leaves no partial epoch", freeze)
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
        runner = DeterministicRunner(self.module)
        candidate_metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a"],
            delivery_unit="delivery-a",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/summary-a.json",
            review_path="/review-a.json",
            now=NOW,
        )
        candidate_id = self.module.candidate_record_id(candidate_metadata)
        epoch_metadata = self.module.new_epoch_metadata(
            candidate_ids=[candidate_id],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        epoch_id = self.module.epoch_record_id(epoch_metadata)
        candidate_metadata = self.module.transition_metadata(
            candidate_metadata,
            "frozen",
            now=NOW,
            evidence={"epoch_id": epoch_id},
        )
        runner.add(
            "sp-source-a",
            metadata={self.module.CANDIDATE_ID: candidate_id},
        )
        runner.add(candidate_id, envelope=candidate_metadata)
        runner.add(epoch_id, envelope=epoch_metadata)
        runner.add(
            self.module.control_record_id([candidate_id]),
            metadata={
                self.module.PREFIX + "schema": self.module.SCHEMA,
                self.module.KIND: "control",
                self.module.ACTIVE_EPOCH: self.module.canonical_json(
                    self.module.epoch_intent(epoch_id, epoch_metadata)
                ),
            },
        )
        client = self.module.BeadClient(rig="sprocket", runner=runner)
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

        result = self.module.reconcile(client, args)

        self.assertEqual(result["action"], "dispatched")
        self.assertEqual(result["workflow_id"], f"wf-{epoch_id}")
        sling = next(call for call in runner.calls if "sling" in call)
        command_index = sling.index("sling")
        self.assertEqual(
            sling[command_index : command_index + 4],
            ["sling", "sprocket/gc.run-operator", epoch_id, "--on"],
        )
        self.assertIn("full_gate_command=just ci", sling)


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
        result = self.module.reconcile(Client(), args)
        self.assertEqual(result["action"], "would_repair")
        self.assertTrue(result["repair_reasons"])


class RecoveryProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def setUp(self) -> None:
        self.runner = DeterministicRunner(self.module)
        self.client = self.module.BeadClient(rig="sprocket", runner=self.runner)

    def add_source(self, source_id: str) -> None:
        self.runner.add(source_id, metadata={})

    def enqueue(
        self,
        source_ids: list[str],
        *,
        delivery: str = "delivery-a",
        commit: str = COMMIT_SHA,
    ) -> dict:
        return self.module.enqueue_candidate(
            self.client,
            self.module.argparse.Namespace(
                source_bead=source_ids,
                delivery_unit=delivery,
                commit=commit,
                base_sha=BASE_SHA,
                summary_path=f"/{delivery}-summary.json",
                review_path=f"/{delivery}-review.json",
                now=NOW,
            ),
        )

    def open_epoch(self, candidate_ids: list[str]) -> dict:
        return self.module.open_epoch(
            self.client,
            self.module.argparse.Namespace(
                candidate=candidate_ids,
                base_sha=BASE_SHA,
                target_ref="refs/heads/main",
                now=NOW,
            ),
        )

    def transition(self, epoch_id: str, state: str, **evidence) -> dict:
        values = {
            "epoch_id": epoch_id,
            "state": state,
            "landed_sha": None,
            "verified_sha": None,
            "release_sha": None,
            "release_ref": None,
            "pr_url": None,
            "failure_class": None,
            "evidence_ref": None,
            "verification_ref": None,
            "repair_bead": None,
            "now": LATER,
        }
        values.update(evidence)
        return self.module.transition_epoch(
            self.client, self.module.argparse.Namespace(**values)
        )

    def reconcile_args(self, *, dry_run: bool = False):
        return self.module.argparse.Namespace(
            rig="sprocket",
            now=LATER,
            trunk_sha=BASE_SHA,
            max_depth=8,
            max_age_seconds=0,
            dry_run=dry_run,
            full_gate_command="just ci",
            operator="gc.run-operator",
            target_ref="refs/heads/main",
        )

    def allow_legacy_migration(self) -> None:
        key = "GC_THUNDERDOME_ALLOW_LEGACY_MIGRATION"
        previous = self.module.os.environ.get(key)
        self.module.os.environ[key] = "1"

        def restore() -> None:
            if previous is None:
                self.module.os.environ.pop(key, None)
            else:
                self.module.os.environ[key] = previous

        self.addCleanup(restore)

    def test_concurrent_same_key_enqueue_converges_to_one_candidate(self) -> None:
        self.add_source("sp-source-a")

        first = self.enqueue(["sp-source-a"])
        replay = self.enqueue(["sp-source-a"])

        self.assertEqual(first["id"], replay["id"])
        candidates = [
            record
            for record in self.runner.records.values()
            if record.get("metadata", {}).get(self.module.KIND) == "candidate"
        ]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"][self.module.CANDIDATE_ID],
            first["id"],
        )
        metadata = self.module.record_metadata(
            self.client.authoritative_reread(str(first["id"]))
        )
        self.assertEqual(
            first["id"],
            f"sp-tdc-{metadata[self.module.PREFIX + 'candidate_key'][:12]}",
        )
        self.assertEqual(len(metadata[self.module.HISTORY]), 1)

    def test_overlapping_different_keys_cannot_both_be_active_and_loser_rolls_back(self) -> None:
        self.add_source("sp-source-a")
        self.add_source("sp-source-b")
        winner = self.enqueue(["sp-source-b"], delivery="winner")
        loser_metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a", "sp-source-b"],
            delivery_unit="loser",
            commit=LANDED_SHA,
            base_sha=BASE_SHA,
            summary_path="/loser-summary.json",
            review_path="/loser-review.json",
            now=NOW,
        )
        loser_id = self.module.candidate_record_id(loser_metadata)

        with self.assertRaisesRegex(self.module.StateError, "reserved by candidate"):
            self.enqueue(
                ["sp-source-a", "sp-source-b"],
                delivery="loser",
                commit=LANDED_SHA,
            )

        loser = self.client.authoritative_reread(loser_id)
        self.assertEqual(
            self.module.record_metadata(loser)[self.module.STATE], "rejected"
        )
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"].get(
                self.module.CANDIDATE_ID, ""
            ),
            "",
        )
        self.assertEqual(
            self.runner.records["sp-source-b"]["metadata"][
                self.module.CANDIDATE_ID
            ],
            winner["id"],
        )

    def test_rejected_retry_releases_reservations_from_prior_attempt(self) -> None:
        self.add_source("sp-source-a")
        self.add_source("sp-source-b")
        winner = self.enqueue(["sp-source-b"], delivery="winner")
        loser_metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a", "sp-source-b"],
            delivery_unit="loser",
            commit=LANDED_SHA,
            base_sha=BASE_SHA,
            summary_path="/loser-summary.json",
            review_path="/loser-review.json",
            now=NOW,
        )
        loser_id = self.module.candidate_record_id(loser_metadata)
        self.client.create_or_validate(
            loser_id,
            "partial loser",
            "thunderdome-candidate",
            loser_metadata,
        )
        self.assertTrue(
            self.client.metadata_cas(
                "sp-source-a", self.module.CANDIDATE_ID, "", loser_id
            )
        )

        with self.assertRaisesRegex(self.module.StateError, "reserved by candidate"):
            self.enqueue(
                ["sp-source-a", "sp-source-b"],
                delivery="loser",
                commit=LANDED_SHA,
            )

        loser = self.client.authoritative_reread(loser_id)
        self.assertEqual(
            self.module.record_metadata(loser)[self.module.STATE], "rejected"
        )
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"].get(
                self.module.CANDIDATE_ID, ""
            ),
            "",
        )
        self.assertEqual(
            self.runner.records["sp-source-b"]["metadata"][
                self.module.CANDIDATE_ID
            ],
            winner["id"],
        )

    def test_disjoint_epoch_opens_elect_exactly_one_active_epoch(self) -> None:
        self.add_source("sp-source-a")
        self.add_source("sp-source-b")
        first_candidate = self.enqueue(["sp-source-a"], delivery="first")
        second_candidate = self.enqueue(
            ["sp-source-b"], delivery="second", commit=LANDED_SHA
        )
        first_epoch = self.open_epoch([str(first_candidate["id"])])

        with self.assertRaisesRegex(self.module.StateError, "already owns"):
            self.open_epoch([str(second_candidate["id"])])

        epochs = [
            record
            for record in self.runner.records.values()
            if record.get("metadata", {}).get(self.module.KIND) == "epoch"
        ]
        self.assertEqual(len(epochs), 1)
        control_id = self.module.control_record_id([str(first_candidate["id"])])
        active = self.runner.records[control_id]["metadata"][
            self.module.ACTIVE_EPOCH
        ]
        self.assertEqual(
            self.module.parse_epoch_intent(active)["epoch_id"], first_epoch["id"]
        )

    def test_enqueue_replays_after_create_and_reservation_boundaries(self) -> None:
        for boundary in range(1, 3):
            with self.subTest(boundary=boundary):
                runner = DeterministicRunner(self.module)
                runner.add("sp-source-a", metadata={})
                client = self.module.BeadClient(rig="sprocket", runner=runner)
                args = self.module.argparse.Namespace(
                    source_bead=["sp-source-a"],
                    delivery_unit="delivery-a",
                    commit=COMMIT_SHA,
                    base_sha=BASE_SHA,
                    summary_path="/summary.json",
                    review_path="/review.json",
                    now=NOW,
                )
                runner.crash_at = boundary
                with self.assertRaises(InjectedCrash):
                    self.module.enqueue_candidate(client, args)
                runner.crash_at = None

                candidate = self.module.enqueue_candidate(client, args)

                self.assertEqual(
                    runner.records["sp-source-a"]["metadata"][
                        self.module.CANDIDATE_ID
                    ],
                    candidate["id"],
                )
                self.assertEqual(
                    len(
                        [
                            record
                            for record in runner.records.values()
                            if record.get("metadata", {}).get(self.module.KIND)
                            == "candidate"
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    len(
                        self.module.record_metadata(
                            client.authoritative_reread(str(candidate["id"]))
                        )[self.module.HISTORY]
                    ),
                    1,
                )


    def test_open_epoch_replays_after_every_durable_mutation_boundary(self) -> None:
        for boundary in range(1, 5):
            with self.subTest(boundary=boundary):
                runner = DeterministicRunner(self.module)
                runner.add("sp-source-a", metadata={})
                client = self.module.BeadClient(rig="sprocket", runner=runner)
                candidate = self.module.enqueue_candidate(
                    client,
                    self.module.argparse.Namespace(
                        source_bead=["sp-source-a"],
                        delivery_unit="delivery-a",
                        commit=COMMIT_SHA,
                        base_sha=BASE_SHA,
                        summary_path="/summary.json",
                        review_path="/review.json",
                        now=NOW,
                    ),
                )
                args = self.module.argparse.Namespace(
                    candidate=[str(candidate["id"])],
                    base_sha=BASE_SHA,
                    target_ref="refs/heads/main",
                    now=NOW,
                )
                runner.crash_at = runner.mutations + boundary
                with self.assertRaises(InjectedCrash):
                    self.module.open_epoch(client, args)
                runner.crash_at = None
                args.now = LATER

                epoch = self.module.open_epoch(client, args)

                self.assertEqual(
                    self.module.record_metadata(
                        client.authoritative_reread(str(candidate["id"]))
                    )[self.module.STATE],
                    "frozen",
                )
                self.assertEqual(
                    len(
                        [
                            record
                            for record in runner.records.values()
                            if record.get("metadata", {}).get(self.module.KIND)
                            == "epoch"
                        ]
                    ),
                    1,
                )
                active = runner.records[
                    self.module.control_record_id([str(candidate["id"])])
                ]["metadata"][self.module.ACTIVE_EPOCH]
                self.assertEqual(
                    self.module.parse_epoch_intent(active)["epoch_id"], epoch["id"]
                )

    def test_dispatch_lost_response_replays_same_graph_root(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        args = self.reconcile_args()
        self.runner.lose_next_sling = True

        with self.assertRaises(self.module.CommandError):
            self.module._dispatch_epoch(self.client, args, epoch)
        result = self.module._dispatch_epoch(self.client, args, epoch)

        self.assertEqual(self.runner.sling_effects, 1)
        self.assertEqual(result["workflow_id"], f"wf-{epoch['id']}")
        persisted = self.module.record_metadata(
            self.client.authoritative_reread(str(epoch["id"]))
        )
        self.assertEqual(
            persisted[self.module.PREFIX + "workflow_id"], result["workflow_id"]
        )
        sling_envs = [
            env
            for call, env in zip(self.runner.calls, self.runner.envs)
            if "sling" in call
        ]
        self.assertTrue(sling_envs)
        self.assertEqual(
            sling_envs[0]["GC_EXPECTED_FORMULA_FINGERPRINT"],
            persisted[self.module.DISPATCH_INTENT]["formula_digest"],
        )

    def test_dispatch_refuses_changed_intent_after_lost_response(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        args = self.reconcile_args()
        self.runner.lose_next_sling = True

        with self.assertRaises(self.module.CommandError):
            self.module._dispatch_epoch(self.client, args, epoch)
        args.full_gate_command = "just changed-gate"
        with self.assertRaisesRegex(self.module.StateError, "sealed intent"):
            self.module._dispatch_epoch(self.client, args, epoch)
        self.assertEqual(self.runner.sling_effects, 1)
        persisted = self.module.record_metadata(
            self.client.authoritative_reread(str(epoch["id"]))
        )
        self.assertEqual(
            persisted[self.module.DISPATCH_INTENT]["full_gate_command"],
            "just ci",
        )

    def test_dispatch_refuses_formula_change_between_seal_and_launch(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        self.runner.change_formula_after_show = True

        with self.assertRaises(self.module.CommandError):
            self.module._dispatch_epoch(self.client, self.reconcile_args(), epoch)

        self.assertEqual(self.runner.sling_effects, 0)
        persisted = self.module.record_metadata(
            self.client.authoritative_reread(str(epoch["id"]))
        )
        self.assertNotIn(self.module.PREFIX + "workflow_id", persisted)



    def test_dispatch_refuses_changed_formula_digest(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        args = self.reconcile_args()
        original_digest = self.module._dispatch_formula_digest
        try:
            self.module._dispatch_formula_digest = (
                lambda _client, _args, _metadata: "a" * 64
            )
            self.module._seal_dispatch_intent(self.client, str(epoch["id"]), args)
            self.module._dispatch_formula_digest = (
                lambda _client, _args, _metadata: "b" * 64
            )
            with self.assertRaisesRegex(self.module.StateError, "sealed intent"):
                self.module._seal_dispatch_intent(
                    self.client, str(epoch["id"]), args
                )
        finally:
            self.module._dispatch_formula_digest = original_digest

    def test_abandoned_epoch_releases_reservations_with_owner_cas(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        epoch_id = str(epoch["id"])

        self.transition(
            epoch_id,
            "cancelled",
            failure_class="cancelled",
            evidence_ref="artifact://cancelled",
        )

        candidate_record = self.client.authoritative_reread(str(candidate["id"]))
        self.assertEqual(
            self.module.record_metadata(candidate_record)[self.module.STATE],
            "rejected",
        )
        self.assertEqual(candidate_record["status"], "closed")
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"][
                self.module.CANDIDATE_ID
            ],
            "",
        )
        control_id = self.module.control_record_id([str(candidate["id"])])
        self.assertEqual(
            self.runner.records[control_id]["metadata"][self.module.ACTIVE_EPOCH],
            "",
        )

    def test_abandoned_verified_candidate_does_not_reclaim_retry_reservation(
        self,
    ) -> None:
        self.add_source("sp-source-a")
        first = self.enqueue(["sp-source-a"], delivery="first")
        epoch = self.open_epoch([str(first["id"])])
        epoch_id = str(epoch["id"])
        self.transition(epoch_id, "landed", landed_sha=LANDED_SHA)
        self.transition(epoch_id, "verifying")
        self.transition(
            epoch_id,
            "verified",
            verified_sha=LANDED_SHA,
            verification_ref="artifact://gate",
        )
        self.transition(
            epoch_id,
            "failed",
            failure_class="policy",
            evidence_ref="artifact://promotion-failure",
        )
        retry = self.enqueue(["sp-source-a"], delivery="retry")

        result = self.module.reconcile(self.client, self.reconcile_args(dry_run=True))

        self.assertEqual(result["action"], "would_dispatch")
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"][
                self.module.CANDIDATE_ID
            ],
            retry["id"],
        )


    def test_transition_replay_marks_event_once_and_closes_sources_once(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        epoch_id = str(epoch["id"])
        self.transition(epoch_id, "landed", landed_sha=LANDED_SHA)
        self.transition(epoch_id, "verifying")
        self.transition(
            epoch_id,
            "verified",
            verified_sha=LANDED_SHA,
            verification_ref="artifact://gate",
        )
        self.transition(epoch_id, "promoting")
        self.transition(
            epoch_id,
            "promoted",
            release_sha=LANDED_SHA,
            release_ref="refs/heads/release/stable",
        )
        event_count = len(self.runner.events)
        history = list(
            self.module.record_metadata(
                self.client.authoritative_reread(epoch_id)
            )[self.module.HISTORY]
        )

        self.transition(
            epoch_id,
            "promoted",
            release_sha=LANDED_SHA,
            release_ref="refs/heads/release/stable",
        )

        source = self.runner.records["sp-source-a"]
        self.assertEqual(source["status"], "closed")
        self.assertEqual(
            source["metadata"][self.module.PROMOTED_BY], epoch_id
        )
        self.assertEqual(source["metadata"][self.module.CANDIDATE_ID], "")
        self.assertEqual(self.runner.close_effects["sp-source-a"], 1)
        self.assertEqual(len(self.runner.events), event_count)
        self.assertEqual(
            self.module.record_metadata(
                self.client.authoritative_reread(epoch_id)
            )[self.module.HISTORY],
            history,
        )

    def test_promotion_rejects_unsealed_evidence_fields(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        epoch_id = str(epoch["id"])
        self.transition(epoch_id, "landed", landed_sha=LANDED_SHA)
        self.transition(epoch_id, "verifying")
        self.transition(
            epoch_id,
            "verified",
            verified_sha=LANDED_SHA,
            verification_ref="artifact://gate",
        )
        self.transition(epoch_id, "promoting")

        with self.assertRaisesRegex(
            self.module.StateError, "unsupported evidence fields: pr_url"
        ):
            self.transition(
                epoch_id,
                "promoted",
                release_sha=LANDED_SHA,
                release_ref="refs/heads/release/stable",
                pr_url="https://example.invalid/pull/1",
            )

        persisted = self.module.record_metadata(
            self.client.authoritative_reread(epoch_id)
        )
        self.assertEqual(persisted[self.module.STATE], "promoting")

    def test_event_marker_emits_each_claimed_history_state(self) -> None:
        metadata = self.module.new_epoch_metadata(
            candidate_ids=["sp-candidate-a"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        metadata = self.module.transition_metadata(
            metadata,
            "landed",
            now=LATER,
            evidence={"landed_sha": LANDED_SHA},
        )
        metadata = self.module.transition_metadata(
            metadata,
            "verifying",
            now=LATER,
        )
        metadata = self.module.transition_metadata(
            metadata,
            "verified",
            now=LATER,
            evidence={
                "verified_sha": LANDED_SHA,
                "verification_ref": "artifact://gate",
            },
        )
        metadata[self.module.EMITTED_SEQ] = 0
        runner = DeterministicRunner(self.module)
        runner.add("sp-epoch", envelope=metadata)
        client = self.module.BeadClient(runner=runner)

        self.module._mark_and_emit_transition(client, {"id": "sp-epoch"})

        self.assertEqual(
            [
                (event["state"], event["transition_seq"])
                for event in runner.events
            ],
            [("landed", 1), ("verifying", 2), ("verified", 3)],
        )

    def test_event_marker_drains_full_history_after_cas_conflict(self) -> None:
        metadata = self.module.new_epoch_metadata(
            candidate_ids=["sp-candidate-a"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        metadata[self.module.HISTORY] = [
            {"seq": seq, "to": "landed"} for seq in range(self.module.MAX_HISTORY)
        ]
        metadata[self.module.EMITTED_SEQ] = -1
        runner = DeterministicRunner(self.module)
        runner.add("sp-epoch", envelope=metadata)
        client = self.module.BeadClient(runner=runner)
        original_cas = client.cas_envelope
        conflicts = 0

        def conflict_once(record, updated):
            nonlocal conflicts
            if conflicts == 0:
                conflicts += 1
                return None
            return original_cas(record, updated)

        client.cas_envelope = conflict_once
        marked = self.module._mark_and_emit_transition(client, {"id": "sp-epoch"})

        self.assertEqual(
            self.module.record_metadata(marked)[self.module.EMITTED_SEQ],
            self.module.MAX_HISTORY - 1,
        )
        self.assertEqual(len(runner.events), self.module.MAX_HISTORY + 1)

    def test_promotion_replays_after_every_terminal_mutation_boundary(self) -> None:
        for boundary in range(1, 9):
            with self.subTest(boundary=boundary):
                self.runner = DeterministicRunner(self.module)
                self.client = self.module.BeadClient(
                    rig="sprocket", runner=self.runner
                )
                self.add_source("sp-source-a")
                candidate = self.enqueue(["sp-source-a"])
                epoch = self.open_epoch([str(candidate["id"])])
                epoch_id = str(epoch["id"])
                self.transition(epoch_id, "landed", landed_sha=LANDED_SHA)
                self.transition(epoch_id, "verifying")
                self.transition(
                    epoch_id,
                    "verified",
                    verified_sha=LANDED_SHA,
                    verification_ref="artifact://gate",
                )
                self.transition(epoch_id, "promoting")
                self.runner.crash_at = self.runner.mutations + boundary

                with self.assertRaises(InjectedCrash):
                    self.transition(
                        epoch_id,
                        "promoted",
                        release_sha=LANDED_SHA,
                        release_ref="refs/heads/release/stable",
                    )
                self.runner.crash_at = None
                self.transition(
                    epoch_id,
                    "promoted",
                    release_sha=LANDED_SHA,
                    release_ref="refs/heads/release/stable",
                )

                source = self.runner.records["sp-source-a"]
                self.assertEqual(source["status"], "closed")
                self.assertEqual(
                    source["metadata"][self.module.PROMOTED_BY], epoch_id
                )
                self.assertEqual(
                    source["metadata"][self.module.CANDIDATE_ID], ""
                )
                self.assertEqual(self.runner.close_effects["sp-source-a"], 1)
                history = self.module.record_metadata(
                    self.client.authoritative_reread(epoch_id)
                )[self.module.HISTORY]
                self.assertEqual(
                    [entry["to"] for entry in history].count("promoted"), 1
                )


    def test_closed_source_without_matching_provenance_fails_closed(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch = self.open_epoch([str(candidate["id"])])
        epoch_id = str(epoch["id"])
        self.transition(epoch_id, "landed", landed_sha=LANDED_SHA)
        self.transition(epoch_id, "verifying")
        self.transition(
            epoch_id,
            "verified",
            verified_sha=LANDED_SHA,
            verification_ref="artifact://gate",
        )
        self.transition(epoch_id, "promoting")
        self.runner.records["sp-source-a"]["status"] = "closed"

        with self.assertRaisesRegex(self.module.StateError, "provenance"):
            self.transition(
                epoch_id,
                "promoted",
                release_sha=LANDED_SHA,
                release_ref="refs/heads/release/stable",
            )

        self.assertNotIn(
            self.module.PROMOTED_BY,
            self.runner.records["sp-source-a"]["metadata"],
        )

    def test_reconcile_materializes_epoch_from_durable_active_intent(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        epoch_metadata = self.module.new_epoch_metadata(
            candidate_ids=[str(candidate["id"])],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        epoch_id = self.module.epoch_record_id(epoch_metadata)
        control_id = self.module.control_record_id([str(candidate["id"])])
        self.runner.add(
            control_id,
            metadata={
                self.module.PREFIX + "schema": self.module.SCHEMA,
                self.module.KIND: "control",
                self.module.ACTIVE_EPOCH: self.module.canonical_json(
                    self.module.epoch_intent(epoch_id, epoch_metadata)
                ),
            },
        )

        result = self.module.reconcile(self.client, self.reconcile_args())

        self.assertEqual(result["action"], "dispatched")
        self.assertIn(epoch_id, self.runner.records)
        self.assertEqual(
            self.module.record_metadata(
                self.client.authoritative_reread(str(candidate["id"]))
            )[self.module.PREFIX + "epoch_id"],
            epoch_id,
        )


    def test_reconcile_repairs_legacy_partial_freeze_before_projection(self) -> None:
        self.add_source("sp-source-a")
        self.allow_legacy_migration()
        candidate_metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a"],
            delivery_unit="legacy",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/legacy-summary.json",
            review_path="/legacy-review.json",
            now=NOW,
        )
        candidate_id = self.module.candidate_record_id(candidate_metadata)
        epoch_metadata = self.module.new_epoch_metadata(
            candidate_ids=[candidate_id],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        epoch_id = "sp-legacy-random"
        self.runner.add(candidate_id, metadata=candidate_metadata)
        self.runner.add(epoch_id, metadata=epoch_metadata)
        self.runner.records["sp-source-a"]["metadata"][
            self.module.CANDIDATE_ID
        ] = candidate_id

        result = self.module.reconcile(self.client, self.reconcile_args())

        self.assertEqual(result["action"], "dispatched")
        self.assertIn(
            self.module.RECORD, self.runner.records[candidate_id]["metadata"]
        )
        self.assertIn(self.module.RECORD, self.runner.records[epoch_id]["metadata"])
        self.assertNotIn(
            self.module.epoch_record_id(epoch_metadata), self.runner.records
        )
        candidate = self.client.authoritative_reread(candidate_id)
        self.assertEqual(
            self.module.record_metadata(candidate)[self.module.STATE], "frozen"
        )
        projection = self.module.read_projection(self.client, LATER, BASE_SHA)
        self.assertTrue(projection["ok"], projection["violations"])


    def test_epoch_open_refuses_a_legacy_active_epoch_without_control(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        legacy = self.module.new_epoch_metadata(
            candidate_ids=["sp-existing-candidate"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        self.runner.add("sp-legacy-active", metadata=legacy)

        with self.assertRaisesRegex(
            self.module.StateError, "active epoch records already exist"
        ):
            self.open_epoch([str(candidate["id"])])

    def test_promotion_refuses_an_unrelated_concurrent_source_close(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        candidate_id = str(candidate["id"])
        original_cas = self.client.metadata_cas

        def closing_cas(bead_id, key, expected, value):
            swapped = original_cas(bead_id, key, expected, value)
            if swapped and key == self.module.PROMOTED_BY:
                source = self.runner.records[bead_id]
                source["status"] = "closed"
                source["close_reason"] = "closed by another operator"
            return swapped

        self.client.metadata_cas = closing_cas
        with self.assertRaisesRegex(
            self.module.StateError, "closed outside epoch"
        ):
            self.module._converge_promoted_source(
                self.client,
                "sp-source-a",
                candidate_id,
                "sp-epoch",
                LANDED_SHA,
            )
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"][
                self.module.PROMOTED_BY
            ],
            "",
        )

    def test_event_marker_remains_retryable_when_emission_fails(self) -> None:
        metadata = self.module.new_epoch_metadata(
            candidate_ids=["sp-candidate-a"],
            base_sha=BASE_SHA,
            target_ref="refs/heads/main",
            now=NOW,
        )
        metadata = self.module.transition_metadata(
            metadata,
            "landed",
            now=LATER,
            evidence={"landed_sha": LANDED_SHA},
        )
        metadata[self.module.EMITTED_SEQ] = 0
        self.runner.add("sp-epoch", envelope=metadata)
        original_emit = self.client.emit_transition

        def fail_emit(*_args):
            raise self.module.CommandError("event provider rejected submission")

        self.client.emit_transition = fail_emit
        with self.assertRaisesRegex(self.module.CommandError, "rejected"):
            self.module._mark_and_emit_transition(self.client, {"id": "sp-epoch"})
        persisted = self.module.record_metadata(
            self.client.authoritative_reread("sp-epoch")
        )

        self.assertEqual(persisted[self.module.EMITTED_SEQ], 0)

        self.client.emit_transition = original_emit
        self.module._mark_and_emit_transition(self.client, {"id": "sp-epoch"})
        self.assertEqual(
            [(event["state"], event["transition_seq"]) for event in self.runner.events],
            [("landed", 1)],
        )

    def test_legacy_envelope_migration_requires_an_explicit_quiescent_gate(self) -> None:
        metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a"],
            delivery_unit="legacy",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/legacy-summary.json",
            review_path="/legacy-review.json",
            now=NOW,
        )
        candidate_id = self.module.candidate_record_id(metadata)
        self.runner.add(candidate_id, metadata=metadata)

        with self.assertRaisesRegex(
            self.module.StateError, "requires a quiescent one-time migration"
        ):
            self.client.authoritative_reread(candidate_id)
        self.assertNotIn(
            self.module.RECORD, self.runner.records[candidate_id]["metadata"]
        )

    def test_legacy_migration_restores_provenance_for_promoted_sources(self) -> None:
        self.add_source("sp-source-a")
        candidate = self.enqueue(["sp-source-a"])
        candidate_id = str(candidate["id"])
        epoch = self.open_epoch([candidate_id])
        epoch_id = str(epoch["id"])
        self.transition(epoch_id, "landed", landed_sha=LANDED_SHA)
        self.transition(epoch_id, "verifying")
        self.transition(
            epoch_id,
            "verified",
            verified_sha=LANDED_SHA,
            verification_ref="artifact://gate",
        )
        self.transition(epoch_id, "promoting")
        self.transition(
            epoch_id,
            "promoted",
            release_sha=LANDED_SHA,
            release_ref="refs/heads/release/stable",
        )
        candidate_metadata = self.module.record_metadata(
            self.client.authoritative_reread(candidate_id)
        )
        epoch_metadata = self.module.record_metadata(
            self.client.authoritative_reread(epoch_id)
        )
        self.runner.records[candidate_id]["metadata"] = candidate_metadata
        self.runner.records[epoch_id]["metadata"] = epoch_metadata
        source = self.runner.records["sp-source-a"]
        del source["metadata"][self.module.PROMOTED_BY]
        self.allow_legacy_migration()

        result = self.module.migrate_legacy_records(self.client)

        self.assertEqual(
            result["migrated_ids"],
            sorted([candidate_id, epoch_id, "sp-source-a"]),
        )
        self.assertEqual(
            self.runner.records["sp-source-a"]["metadata"][
                self.module.PROMOTED_BY
            ],
            epoch_id,
        )

    def test_reconcile_dry_run_reports_repair_without_mutation(self) -> None:
        self.add_source("sp-source-a")
        metadata = self.module.new_candidate_metadata(
            source_beads=["sp-source-a"],
            delivery_unit="legacy",
            commit=COMMIT_SHA,
            base_sha=BASE_SHA,
            summary_path="/legacy-summary.json",
            review_path="/legacy-review.json",
            now=NOW,
        )
        candidate_id = self.module.candidate_record_id(metadata)
        self.runner.add(candidate_id, metadata=metadata)
        before = DeterministicRunner._copy(self.runner.records)
        mutations = self.runner.mutations

        result = self.module.reconcile(
            self.client, self.reconcile_args(dry_run=True)
        )

        self.assertEqual(result["action"], "would_repair")
        self.assertEqual(self.runner.mutations, mutations)
        self.assertEqual(self.runner.records, before)


class GitWorkflowIntegrationTests(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo = pathlib.Path(self.tempdir.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Thunderdome Tests")

    def git(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr}"
            )
        return result

    def commit_all(self, message: str) -> str:
        self.git("add", ".")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def test_real_candidate_conflict_is_resolved_without_omission(self) -> None:
        artifact = self.repo / "artifact.txt"
        artifact.write_text("base\n", encoding="utf-8")
        base = self.commit_all("base")

        self.git("switch", "-q", "-c", "candidate-a")
        artifact.write_text("candidate-a\n", encoding="utf-8")
        candidate_a = self.commit_all("candidate a")
        self.git("switch", "-q", "-c", "candidate-b", base)
        artifact.write_text("candidate-b\n", encoding="utf-8")
        candidate_b = self.commit_all("candidate b")

        self.git("switch", "-q", "-c", "integration", base)
        self.git("cherry-pick", candidate_a)
        conflict = self.git("cherry-pick", candidate_b, check=False)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(
            self.git("diff", "--name-only", "--diff-filter=U").stdout.strip(),
            "artifact.txt",
        )
        artifact.write_text("candidate-a\ncandidate-b\n", encoding="utf-8")
        self.git("add", "artifact.txt")
        self.git("-c", "core.editor=true", "cherry-pick", "--continue")

        self.assertEqual(
            artifact.read_text(encoding="utf-8"),
            "candidate-a\ncandidate-b\n",
        )
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        assemble = (
            self.ROOT / "assets" / "workflows" / "thunderdome-land" / "assemble-land.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Never bisect, omit, or reorder", assemble)
        self.assertIn("conflicts while preserving all candidate behavior", assemble)

    def test_semantic_repair_reintegration_runs_the_aggregate_gate(self) -> None:
        min_setting = self.repo / "min_setting.py"
        max_setting = self.repo / "max_setting.py"
        gate = self.repo / "gate.py"
        min_setting.write_text("MIN = 1\n", encoding="utf-8")
        max_setting.write_text("MAX = 3\n", encoding="utf-8")
        gate.write_text(
            "from min_setting import MIN\nfrom max_setting import MAX\nassert MIN < MAX\n",
            encoding="utf-8",
        )
        base = self.commit_all("base")

        self.git("switch", "-q", "-c", "repair-min")
        min_setting.write_text("MIN = 2\n", encoding="utf-8")
        repair_min = self.commit_all("raise minimum")
        self.git("switch", "-q", "-c", "repair-max", base)
        max_setting.write_text("MAX = 2\n", encoding="utf-8")
        repair_max = self.commit_all("lower maximum")

        self.git("switch", "-q", "-c", "repair-integration", base)
        self.git("cherry-pick", repair_min)
        self.git("cherry-pick", repair_max)
        failed_gate = subprocess.run(
            [sys.executable, "gate.py"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(failed_gate.returncode, 0)

        gate.write_text(
            "from min_setting import MIN\nfrom max_setting import MAX\nassert MIN <= MAX\n",
            encoding="utf-8",
        )
        self.commit_all("resolve aggregate semantic conflict")
        passed_gate = subprocess.run(
            [sys.executable, "gate.py"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(passed_gate.returncode, 0, passed_gate.stderr)
        self.assertEqual(min_setting.read_text(encoding="utf-8"), "MIN = 2\n")
        self.assertEqual(max_setting.read_text(encoding="utf-8"), "MAX = 2\n")
        repair = (
            self.ROOT / "assets" / "workflows" / "thunderdome-land" / "verify-repair.md"
        ).read_text(encoding="utf-8")
        self.assertIn("without omitting a repair", repair)
        self.assertIn("repeat the full gate", repair)


class AdapterTests(unittest.TestCase):
    @classmethod

    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_client_scopes_commands_and_redacts_subprocess_output(self) -> None:
        calls: list[list[str]] = []
        envs: list[dict[str, str]] = []

        def runner(args, env):
            calls.append(list(args))
            envs.append(dict(env))
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
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(self.module.CommandError) as raised:
                client.list_thunderdome()
        with mock.patch.dict(
            os.environ, {"BD_IGNORE_SCHEMA_SKEW": "caller-owned"}, clear=True
        ):
            with self.assertRaises(self.module.CommandError):
                client.list_thunderdome()

        self.assertEqual(calls[0][:5], ["/bin/gc", "--city", "/city", "--rig", "sprocket"])
        self.assertNotIn("private-token", str(raised.exception))
        self.assertIn("exit 17", str(raised.exception))
        self.assertNotIn("BD_IGNORE_SCHEMA_SKEW", envs[0])
        self.assertEqual(envs[1]["BD_IGNORE_SCHEMA_SKEW"], "caller-owned")

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
            return subprocess.CompletedProcess(args, 0, stdout='{"ok":true,"submitted":true}', stderr="")

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
