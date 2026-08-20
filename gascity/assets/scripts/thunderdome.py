#!/usr/bin/env python3
"""Durable Continuous Thunderdome state over Gas City beads.

The ledger is the source of truth. This helper owns deterministic transition
validation and a read-only projection; agents own judgment and Git operations.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any


SCHEMA = "gc.thunderdome.v1"
PREFIX = "gc.thunderdome."
KIND = PREFIX + "kind"
STATE = PREFIX + "state"
HISTORY = PREFIX + "history"
CANDIDATE_STATES = {"queued", "frozen", "landed", "verified", "superseded", "rejected"}
EPOCH_STATES = {
    "assembling",
    "landed",
    "verifying",
    "red",
    "repairing",
    "verified",
    "promoting",
    "promotion_failed",
    "promoted",
    "failed",
    "cancelled",
}
TERMINAL_EPOCH_STATES = {"promoted", "failed", "cancelled"}
ABANDONED_EPOCH_STATES = {"failed", "cancelled"}
ACTIVE_CANDIDATE_STATES = {"queued", "frozen", "landed"}
SAFE_FAILURE_CLASSES = {
    "cancelled",
    "conflict",
    "infrastructure",
    "policy",
    "test_failure",
    "timeout",
}
SAFE_EVIDENCE_KEYS = {
    "epoch_id",
    "evidence_ref",
    "failure_class",
    "landed_sha",
    "pr_url",
    "release_ref",
    "release_sha",
    "repair_bead_ids",
    "verification_ref",
    "verified_sha",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^refs/(heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]*$")

CANDIDATE_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"frozen", "superseded", "rejected"},
    "frozen": {"landed", "superseded", "rejected"},
    "landed": {"verified", "rejected"},
    "verified": set(),
    "superseded": set(),
    "rejected": set(),
}
EPOCH_TRANSITIONS: dict[str, set[str]] = {
    "assembling": {"landed", "failed", "cancelled"},
    "landed": {"verifying", "failed", "cancelled"},
    "verifying": {"verified", "red", "failed", "cancelled"},
    "red": {"repairing", "failed", "cancelled"},
    "repairing": {"verifying", "red", "failed", "cancelled"},
    "verified": {"promoting", "failed"},
    "promoting": {"promoted", "promotion_failed", "failed"},
    "promotion_failed": {"promoting", "failed", "cancelled"},
    "promoted": set(),
    "failed": set(),
    "cancelled": set(),
}


class StateError(ValueError):
    """The requested state or transition violates the ledger contract."""


class CommandError(RuntimeError):
    """A gc or git adapter command failed."""


STRUCTURED_ID_METADATA_KEYS = {
    PREFIX + "candidate_ids",
    PREFIX + "repair_bead_ids",
    PREFIX + "source_beads",
}


def decode_thunderdome_record(record: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    metadata = result.get("metadata")
    if not isinstance(metadata, Mapping):
        return result
    decoded = dict(metadata)
    for key in [*sorted(STRUCTURED_ID_METADATA_KEYS), HISTORY]:
        if key not in decoded:
            continue
        value = decoded[key]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise CommandError(f"metadata field {key} contains malformed JSON") from exc
        if not isinstance(value, list):
            raise CommandError(f"metadata field {key} must decode to an array")
        if key == HISTORY:
            if any(not isinstance(item, Mapping) for item in value):
                raise CommandError(f"metadata field {key} must contain objects")
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            raise CommandError(f"metadata field {key} must contain non-empty strings")
        decoded[key] = value
    result["metadata"] = decoded
    return result

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_sha(name: str, value: str) -> str:
    normalized = value.strip().lower()
    if not SHA_RE.fullmatch(normalized):
        raise StateError(f"{name} must be an exact 40-character lowercase Git SHA")
    return normalized


def require_absolute_path(name: str, value: str) -> str:
    path = pathlib.Path(value)
    if not value or not path.is_absolute():
        raise StateError(f"{name} must be an absolute path")
    return str(path)


def require_ref(name: str, value: str) -> str:
    if not REF_RE.fullmatch(value):
        raise StateError(f"{name} must be a full refs/heads/* or refs/tags/* ref")
    return value


def require_nonempty_ids(name: str, values: Iterable[str]) -> list[str]:
    canonical = sorted(set(value.strip() for value in values if value.strip()))
    if not canonical:
        raise StateError(f"{name} must contain at least one identifier")
    return canonical


def initial_history(state: str, now: str) -> list[dict[str, Any]]:
    return [{"seq": 0, "from": "", "to": state, "at": now, "evidence": {}}]


def new_candidate_metadata(
    *,
    source_beads: Iterable[str],
    delivery_unit: str,
    commit: str,
    base_sha: str,
    summary_path: str,
    review_path: str,
    now: str,
) -> dict[str, Any]:
    sources = require_nonempty_ids("source_beads", source_beads)
    unit = delivery_unit.strip()
    if not unit:
        raise StateError("delivery_unit must not be empty")
    exact_commit = require_sha("commit", commit)
    exact_base = require_sha("base_sha", base_sha)
    summary = require_absolute_path("summary_path", summary_path)
    review = require_absolute_path("review_path", review_path)
    key_material = {
        "source_beads": sources,
        "delivery_unit": unit,
        "commit": exact_commit,
        "base_sha": exact_base,
    }
    return {
        PREFIX + "schema": SCHEMA,
        KIND: "candidate",
        STATE: "queued",
        PREFIX + "candidate_key": digest(key_material),
        PREFIX + "source_beads": sources,
        PREFIX + "delivery_unit": unit,
        PREFIX + "commit": exact_commit,
        PREFIX + "base_sha": exact_base,
        PREFIX + "summary_path": summary,
        PREFIX + "review_path": review,
        PREFIX + "epoch_id": "",
        PREFIX + "created_at": now,
        PREFIX + "updated_at": now,
        HISTORY: initial_history("queued", now),
    }


def new_epoch_metadata(
    *,
    candidate_ids: Iterable[str],
    base_sha: str,
    target_ref: str,
    now: str,
) -> dict[str, Any]:
    candidates = require_nonempty_ids("candidate_ids", candidate_ids)
    exact_base = require_sha("base_sha", base_sha)
    target = require_ref("target_ref", target_ref)
    return {
        PREFIX + "schema": SCHEMA,
        KIND: "epoch",
        STATE: "assembling",
        PREFIX + "candidate_ids": candidates,
        PREFIX + "membership_hash": digest(candidates),
        PREFIX + "base_sha": exact_base,
        PREFIX + "target_ref": target,
        PREFIX + "landed_sha": "",
        PREFIX + "verified_sha": "",
        PREFIX + "release_sha": "",
        PREFIX + "created_at": now,
        PREFIX + "updated_at": now,
        HISTORY: initial_history("assembling", now),
    }

def validate_epoch_candidates(
    records: Sequence[Mapping[str, Any]], *, base_sha: str
) -> None:
    exact_base = require_sha("base_sha", base_sha)
    if not records:
        raise StateError("epoch requires at least one candidate")
    seen_sources: set[str] = set()
    for record in records:
        candidate_id = str(record.get("id", ""))
        metadata = record.get("metadata") or {}
        if metadata.get(KIND) != "candidate" or metadata.get(STATE) not in {"queued", "frozen"}:
            raise StateError(f"candidate {candidate_id} is not queueable")
        if metadata.get(PREFIX + "base_sha") != exact_base:
            raise StateError(
                f"candidate {candidate_id} base_sha does not match the epoch base; refresh it before freezing"
            )
        sources = set(metadata.get(PREFIX + "source_beads", []))
        overlap = sorted(seen_sources.intersection(sources))
        if overlap:
            raise StateError(
                f"epoch candidates duplicate source beads: {', '.join(overlap)}"
            )
        seen_sources.update(sources)



def _normalized_evidence(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(dict(evidence or {}))
    unknown = sorted(set(result) - SAFE_EVIDENCE_KEYS)
    if unknown:
        raise StateError(f"unsafe or unknown evidence fields: {', '.join(unknown)}")
    for key, value in result.items():
        if isinstance(value, str) and len(value) > 2048:
            raise StateError(f"evidence field {key} exceeds 2048 characters")
        if isinstance(value, list):
            if len(value) > 256 or any(not isinstance(item, str) or len(item) > 256 for item in value):
                raise StateError(f"evidence field {key} exceeds its bounded string-list contract")
    if "failure_class" in result and result["failure_class"] not in SAFE_FAILURE_CLASSES:
        raise StateError("failure_class is not a supported low-cardinality class")
    for key in ("landed_sha", "verified_sha", "release_sha"):
        if key in result:
            result[key] = require_sha(key, str(result[key]))
    for key in ("release_ref",):
        if key in result:
            result[key] = require_ref(key, str(result[key]))
    if "pr_url" in result:
        url = str(result["pr_url"])
        if not url.startswith("https://") or len(url) > 2048:
            raise StateError("pr_url must be a bounded HTTPS URL")
        result["pr_url"] = url
    if "repair_bead_ids" in result:
        result["repair_bead_ids"] = require_nonempty_ids("repair_bead_ids", result["repair_bead_ids"])
    for key in ("epoch_id", "evidence_ref", "verification_ref"):
        if key in result:
            value = str(result[key]).strip()
            if not value:
                raise StateError(f"{key} must not be empty")
            result[key] = value
    return result


def _validate_transition_evidence(
    metadata: Mapping[str, Any], kind: str, current: str, target: str, evidence: Mapping[str, Any]
) -> None:
    if kind == "candidate":
        if target == "frozen" and "epoch_id" not in evidence:
            raise StateError("candidate frozen transition requires epoch_id")
        return

    if target == "landed" and "landed_sha" not in evidence:
        raise StateError("epoch landed transition requires landed_sha")
    if target == "red":
        if "failure_class" not in evidence or "evidence_ref" not in evidence:
            raise StateError("epoch red transition requires failure_class and evidence_ref")
    if target == "repairing" and "repair_bead_ids" not in evidence:
        raise StateError("epoch repairing transition requires repair_bead_ids")
    if current == "repairing" and target == "verifying" and "landed_sha" not in evidence:
        raise StateError("repair verification requires the new fix-forward landed_sha")
    if target == "verified":
        if "verified_sha" not in evidence or "verification_ref" not in evidence:
            raise StateError("epoch verified transition requires verified_sha and verification_ref")
        if evidence["verified_sha"] != metadata.get(PREFIX + "landed_sha"):
            raise StateError("verified_sha must match the latest landed_sha")
    if target == "promoted":
        if "release_sha" not in evidence or "release_ref" not in evidence:
            raise StateError("epoch promoted transition requires release_sha and release_ref")
        if evidence["release_sha"] != metadata.get(PREFIX + "verified_sha"):
            raise StateError("release_sha must match verified_sha")
    if target in {"failed", "cancelled", "promotion_failed"}:
        if "failure_class" not in evidence or "evidence_ref" not in evidence:
            raise StateError(f"epoch {target} transition requires failure_class and evidence_ref")


def transition_metadata(
    metadata: Mapping[str, Any],
    target_state: str,
    *,
    now: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(metadata))
    if result.get(PREFIX + "schema") != SCHEMA:
        raise StateError("unsupported or missing Thunderdome schema")
    kind = result.get(KIND)
    current = result.get(STATE)
    transitions = CANDIDATE_TRANSITIONS if kind == "candidate" else EPOCH_TRANSITIONS if kind == "epoch" else None
    states = CANDIDATE_STATES if kind == "candidate" else EPOCH_STATES if kind == "epoch" else set()
    if transitions is None or current not in states:
        raise StateError("unsupported Thunderdome kind or current state")
    if target_state not in states:
        raise StateError(f"unknown {kind} state {target_state!r}")
    safe_evidence = _normalized_evidence(evidence)
    if target_state == current:
        history = result.get(HISTORY, [])
        prior = history[-1].get("evidence", {}) if history else {}
        if prior == safe_evidence:
            return result
        raise StateError(f"conflicting replay of {kind} transition to {target_state}")
    if target_state not in transitions[current]:
        raise StateError(f"illegal {kind} transition {current} -> {target_state}")
    _validate_transition_evidence(result, kind, current, target_state, safe_evidence)

    history = copy.deepcopy(result.get(HISTORY, []))
    history.append(
        {
            "seq": len(history),
            "from": current,
            "to": target_state,
            "at": now,
            "evidence": safe_evidence,
        }
    )
    result[STATE] = target_state
    result[PREFIX + "updated_at"] = now
    result[HISTORY] = history
    if kind == "candidate" and "epoch_id" in safe_evidence:
        result[PREFIX + "epoch_id"] = safe_evidence["epoch_id"]
    for key, value in safe_evidence.items():
        result[PREFIX + key] = value
    return result


def _parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(created_at: Any, now: str) -> int | None:
    created = _parse_time(created_at)
    current = _parse_time(now)
    if created is None or current is None:
        return None
    return max(0, int((current - created).total_seconds()))


def _violation(code: str, entity: str, message: str) -> dict[str, str]:
    return {"code": code, "entity": entity, "message": message}


def project_state(
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
    source_states: Mapping[str, str],
    trunk_sha: str = "",
) -> dict[str, Any]:
    candidates: dict[str, Mapping[str, Any]] = {}
    epochs: dict[str, Mapping[str, Any]] = {}
    violations: list[dict[str, str]] = []
    for record in records:
        metadata = record.get("metadata") or {}
        if metadata.get(PREFIX + "schema") != SCHEMA:
            continue
        bead_id = str(record.get("id", ""))
        if metadata.get(KIND) == "candidate":
            candidates[bead_id] = record
        elif metadata.get(KIND) == "epoch":
            epochs[bead_id] = record

    source_to_candidates: dict[str, list[str]] = defaultdict(list)
    for candidate_id, record in candidates.items():
        metadata = record.get("metadata") or {}
        state = metadata.get(STATE)
        for source_id in metadata.get(PREFIX + "source_beads", []):
            if state in ACTIVE_CANDIDATE_STATES:
                source_to_candidates[source_id].append(candidate_id)
            if source_states.get(source_id) == "closed" and state not in {"verified", "superseded", "rejected"}:
                violations.append(
                    _violation(
                        "source_closed_before_verification",
                        source_id,
                        f"source bead closed while candidate {candidate_id} is {state}",
                    )
                )
        epoch_id = metadata.get(PREFIX + "epoch_id", "")
        if state in {"frozen", "landed", "verified"} and epoch_id not in epochs:
            violations.append(
                _violation("candidate_epoch_missing", candidate_id, f"candidate references missing epoch {epoch_id!r}")
            )

    for source_id, candidate_ids in source_to_candidates.items():
        if len(candidate_ids) > 1:
            violations.append(
                _violation(
                    "duplicate_active_source_candidate",
                    source_id,
                    f"source bead has active candidates {','.join(sorted(candidate_ids))}",
                )
            )

    expected_candidate_state = {
        "assembling": "frozen",
        "landed": "landed",
        "verifying": "landed",
        "red": "landed",
        "repairing": "landed",
        "verified": "verified",
        "promoting": "verified",
        "promotion_failed": "verified",
        "promoted": "verified",
    }
    epoch_views: list[dict[str, Any]] = []
    for epoch_id, record in epochs.items():
        metadata = record.get("metadata") or {}
        state = str(metadata.get(STATE, ""))
        candidate_ids = metadata.get(PREFIX + "candidate_ids", [])
        if not isinstance(candidate_ids, list) or metadata.get(PREFIX + "membership_hash") != digest(sorted(candidate_ids)):
            violations.append(
                _violation("epoch_membership_hash_mismatch", epoch_id, "epoch candidate membership does not match hash")
            )
        for candidate_id in candidate_ids if isinstance(candidate_ids, list) else []:
            candidate = candidates.get(candidate_id)
            if candidate is None:
                violations.append(
                    _violation("epoch_candidate_missing", epoch_id, f"epoch references missing candidate {candidate_id}")
                )
                continue
            candidate_metadata = candidate.get("metadata") or {}
            if state not in ABANDONED_EPOCH_STATES and candidate_metadata.get(PREFIX + "epoch_id") != epoch_id:
                violations.append(
                    _violation(
                        "candidate_epoch_mismatch",
                        candidate_id,
                        f"candidate does not point back to epoch {epoch_id}",
                    )
                )
            expected = expected_candidate_state.get(state)
            if expected and candidate_metadata.get(STATE) != expected:
                violations.append(
                    _violation(
                        "candidate_state_mismatch",
                        candidate_id,
                        f"candidate is {candidate_metadata.get(STATE)}, epoch {state} requires {expected}",
                    )
                )
        landed_sha = metadata.get(PREFIX + "landed_sha", "")
        verified_sha = metadata.get(PREFIX + "verified_sha", "")
        release_sha = metadata.get(PREFIX + "release_sha", "")
        if state in {"verified", "promoting", "promotion_failed", "promoted"} and verified_sha != landed_sha:
            violations.append(
                _violation("epoch_verified_sha_mismatch", epoch_id, "verified SHA does not match latest landed SHA")
            )
        if state == "promoted" and release_sha != verified_sha:
            violations.append(
                _violation("epoch_release_sha_mismatch", epoch_id, "release SHA does not match verified SHA")
            )
        repair_beads = metadata.get(PREFIX + "repair_bead_ids", [])
        history = metadata.get(HISTORY, [])
        epoch_views.append(
            {
                "id": epoch_id,
                "state": state,
                "base_sha": metadata.get(PREFIX + "base_sha", ""),
                "landed_sha": landed_sha,
                "verified_sha": verified_sha,
                "release_sha": release_sha,
                "release_ref": metadata.get(PREFIX + "release_ref", ""),
                "target_ref": metadata.get(PREFIX + "target_ref", ""),
                "candidate_count": len(candidate_ids) if isinstance(candidate_ids, list) else 0,
                "transition_count": len(history) if isinstance(history, list) else 0,
                "failure_class": metadata.get(PREFIX + "failure_class", ""),
                "repair_bead_count": len(repair_beads) if isinstance(repair_beads, list) else 0,
                "age_seconds": _age_seconds(metadata.get(PREFIX + "created_at"), now),
            }
        )

    queue_counts = Counter(
        str((record.get("metadata") or {}).get(STATE, "unknown")) for record in candidates.values()
    )
    queued_ages = [
        age
        for record in candidates.values()
        if (record.get("metadata") or {}).get(STATE) == "queued"
        for age in [_age_seconds((record.get("metadata") or {}).get(PREFIX + "created_at"), now)]
        if age is not None
    ]
    stale_queued_ids = sorted(
        candidate_id
        for candidate_id, record in candidates.items()
        if (record.get("metadata") or {}).get(STATE) == "queued"
        and trunk_sha
        and (record.get("metadata") or {}).get(PREFIX + "base_sha") != trunk_sha
    )
    promoted = sorted(
        (view for view in epoch_views if view["state"] == "promoted"),
        key=lambda view: (view["age_seconds"] is None, -(view["age_seconds"] or 0)),
    )
    release = {
        "epoch_id": promoted[-1]["id"] if promoted else "",
        "sha": promoted[-1]["release_sha"] if promoted else "",
        "ref": promoted[-1]["release_ref"] if promoted else "",
    }
    violations.sort(key=lambda item: (item["code"], item["entity"], item["message"]))
    active_epochs = sorted(
        (view for view in epoch_views if view["state"] not in TERMINAL_EPOCH_STATES), key=lambda view: view["id"]
    )
    recent_epochs = sorted(epoch_views, key=lambda view: view["id"])
    return {
        "schema_version": "1",
        "ok": not violations,
        "observed_at": now,
        "queue": {
            **{state: queue_counts.get(state, 0) for state in sorted(CANDIDATE_STATES)},
            "total": sum(queue_counts.values()),
            "oldest_queued_seconds": max(queued_ages) if queued_ages else None,
            "stale_queued": len(stale_queued_ids),
            "stale_candidate_ids": stale_queued_ids,
        },
        "active_epochs": active_epochs,
        "epochs": recent_epochs,
        "release": release,
        "violations": violations,
    }



def plan_reconcile(
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
    trunk_sha: str,
    max_depth: int,
    max_age_seconds: int,
) -> dict[str, Any]:
    exact_trunk = require_sha("trunk_sha", trunk_sha)
    if max_depth < 1:
        raise StateError("max_depth must be at least 1")
    if max_age_seconds < 0:
        raise StateError("max_age_seconds must not be negative")
    _parse_time(now)

    active_epoch_ids = sorted(
        str(record.get("id", ""))
        for record in records
        if (record.get("metadata") or {}).get(PREFIX + "schema") == SCHEMA
        and (record.get("metadata") or {}).get(KIND) == "epoch"
        and (record.get("metadata") or {}).get(STATE) not in TERMINAL_EPOCH_STATES
    )
    queued: list[tuple[int, str]] = []
    stale_candidate_ids: list[str] = []
    for record in records:
        metadata = record.get("metadata") or {}
        if (
            metadata.get(PREFIX + "schema") != SCHEMA
            or metadata.get(KIND) != "candidate"
            or metadata.get(STATE) != "queued"
        ):
            continue
        candidate_id = str(record.get("id", ""))
        if metadata.get(PREFIX + "base_sha") != exact_trunk:
            stale_candidate_ids.append(candidate_id)
            continue
        age_seconds = _age_seconds(metadata.get(PREFIX + "created_at"), now)
        queued.append((age_seconds if age_seconds is not None else -1, candidate_id))

    queued.sort(key=lambda item: (-item[0], item[1]))
    candidate_ids = [candidate_id for _, candidate_id in queued[:max_depth]]
    oldest_age_seconds = queued[0][0] if queued else None
    if active_epoch_ids:
        due = False
        reason = "active_epoch"
        candidate_ids = []
    elif len(queued) >= max_depth:
        due = True
        reason = "queue_depth"
    elif queued and oldest_age_seconds is not None and oldest_age_seconds >= max_age_seconds:
        due = True
        reason = "oldest_age"
    elif queued:
        due = False
        reason = "waiting"
        candidate_ids = []
    else:
        due = False
        reason = "no_ready_candidates"

    return {
        "schema_version": "1",
        "due": due,
        "reason": reason,
        "candidate_ids": candidate_ids,
        "active_epoch_ids": active_epoch_ids,
        "stale_candidate_ids": sorted(stale_candidate_ids),
        "ready_count": len(queued),
        "oldest_ready_seconds": oldest_age_seconds,
        "max_depth": max_depth,
        "max_age_seconds": max_age_seconds,
        "trunk_sha": exact_trunk,
    }


def format_status(projection: Mapping[str, Any]) -> str:
    queue = projection["queue"]
    lines = [
        "Thunderdome: " + ("healthy" if projection["ok"] else "invariant violation"),
        (
            "Queue: "
            f"queued={queue['queued']} stale={queue['stale_queued']} "
            f"frozen={queue['frozen']} landed={queue['landed']} "
            f"verified={queue['verified']} total={queue['total']}"
        ),
        f"Active epochs: {len(projection['active_epochs'])}",
    ]
    for epoch in projection["active_epochs"]:
        lines.append(
            f"- {epoch['id']}: state={epoch['state']} candidates={epoch['candidate_count']} "
            f"repairs={epoch['repair_bead_count']} failure={epoch['failure_class'] or 'none'} "
            f"transitions={epoch['transition_count']} age={epoch['age_seconds']}s"
        )
    release = projection["release"]
    lines.append(f"Stable release: {release['sha'] or 'unreported'}")
    lines.append(f"Invariant violations: {len(projection['violations'])}")
    for violation in projection["violations"]:
        lines.append(f"- {violation['code']} {violation['entity']}: {violation['message']}")
    return "\n".join(lines)


Runner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


def subprocess_runner(args: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, env=dict(env), check=False)


class BeadClient:
    def __init__(
        self,
        *,
        gc_bin: str = "gc",
        city: str = "",
        rig: str = "",
        runner: Runner = subprocess_runner,
    ) -> None:
        self._gc_bin = gc_bin
        self._city = city
        self._rig = rig
        self._runner = runner

    def _prefix(self) -> list[str]:
        args = [self._gc_bin]
        if self._city:
            args.extend(["--city", self._city])
        if self._rig:
            args.extend(["--rig", self._rig])
        return args

    def run(self, args: Sequence[str], *, expect_json: bool = True) -> Any:
        env = os.environ.copy()
        env.setdefault("BD_IGNORE_SCHEMA_SKEW", "1")
        completed = self._runner([*self._prefix(), *args], env)
        if completed.returncode != 0:
            operation = " ".join(args[:2])
            raise CommandError(f"gc {operation} failed with exit {completed.returncode}; inspect command diagnostics locally")
        if not expect_json:
            return completed.stdout
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CommandError(f"gc {' '.join(args[:2])} returned malformed JSON") from exc

    def list_thunderdome(self) -> list[dict[str, Any]]:
        result = self.run(
            [
                "bd",
                "list",
                "--status",
                "open,in_progress,blocked,deferred,closed",
                "--has-metadata-key",
                KIND,
                "--limit",
                "0",
                "--json",
            ]
        )
        return [decode_thunderdome_record(record) for record in result]

    def show(self, bead_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not bead_ids:
            return []
        return [
            decode_thunderdome_record(record)
            for record in self.run(["bd", "show", *bead_ids, "--json"])
        ]

    def create_record(self, title: str, label: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        created = self.run(
            [
                "bd",
                "create",
                title,
                "--type",
                "task",
                "--priority",
                "1",
                "--labels",
                label,
                "--metadata",
                canonical_json(metadata),
                "--json",
            ]
        )
        record = created[0] if isinstance(created, list) else created
        updated = self.run(
            [
                "bd",
                "update",
                str(record["id"]),
                "--status",
                "in_progress",
                "--json",
            ]
        )
        record = updated[0] if isinstance(updated, list) else updated
        return decode_thunderdome_record(record)

    def update_metadata(self, bead_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        updated = self.run(
            ["bd", "update", bead_id, "--metadata", canonical_json(metadata), "--json"]
        )
        record = updated[0] if isinstance(updated, list) else updated
        return decode_thunderdome_record(record)

    def close(self, bead_id: str, reason: str) -> None:
        self.run(["bd", "close", bead_id, "--reason", reason, "--json"])

    def emit_transition(self, bead_id: str, kind: str, state: str, seq: int) -> None:
        payload = canonical_json(
            {"schema_version": "1", "kind": kind, "state": state, "transition_seq": seq}
        )
        self.run(
            [
                "event",
                "emit",
                "thunderdome.transition",
                "--subject",
                bead_id,
                "--payload",
                payload,
                "--json",
            ]
        )


def enqueue_candidate(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    metadata = new_candidate_metadata(
        source_beads=args.source_bead,
        delivery_unit=args.delivery_unit,
        commit=args.commit,
        base_sha=args.base_sha,
        summary_path=args.summary_path,
        review_path=args.review_path,
        now=args.now or utc_now(),
    )
    records = client.list_thunderdome()
    existing = [
        record
        for record in records
        if (record.get("metadata") or {}).get(KIND) == "candidate"
        and (record.get("metadata") or {}).get(PREFIX + "candidate_key")
        == metadata[PREFIX + "candidate_key"]
    ]
    if existing:
        return existing[0]
    active_sources = set(metadata[PREFIX + "source_beads"])
    for record in records:
        current = record.get("metadata") or {}
        if current.get(KIND) != "candidate" or current.get(STATE) not in ACTIVE_CANDIDATE_STATES:
            continue
        if active_sources.intersection(current.get(PREFIX + "source_beads", [])):
            raise StateError(
                f"source bead already has active candidate {record.get('id')}; supersede it explicitly"
            )
    short = metadata[PREFIX + "commit"][:12]
    return client.create_record(
        f"Land candidate {metadata[PREFIX + 'delivery_unit']} {short}",
        "thunderdome-candidate",
        metadata,
    )


def open_epoch(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    candidate_ids = require_nonempty_ids("candidate_ids", args.candidate)
    shown = {record["id"]: record for record in client.show(candidate_ids)}
    missing = sorted(set(candidate_ids) - set(shown))
    if missing:
        raise StateError(f"missing candidate beads: {', '.join(missing)}")
    validate_epoch_candidates(list(shown.values()), base_sha=args.base_sha)
    metadata = new_epoch_metadata(
        candidate_ids=candidate_ids,
        base_sha=args.base_sha,
        target_ref=args.target_ref,
        now=args.now or utc_now(),
    )
    existing = [
        record
        for record in client.list_thunderdome()
        if (record.get("metadata") or {}).get(KIND) == "epoch"
        and (record.get("metadata") or {}).get(PREFIX + "membership_hash")
        == metadata[PREFIX + "membership_hash"]
        and (record.get("metadata") or {}).get(PREFIX + "base_sha") == metadata[PREFIX + "base_sha"]
        and (record.get("metadata") or {}).get(PREFIX + "target_ref") == metadata[PREFIX + "target_ref"]
        and (record.get("metadata") or {}).get(STATE) == "assembling"
    ]
    epoch = existing[0] if existing else client.create_record(
        f"Thunderdome epoch {metadata[PREFIX + 'membership_hash'][:12]}",
        "thunderdome-epoch",
        metadata,
    )
    epoch_id = str(epoch["id"])
    for candidate_id in candidate_ids:
        candidate_metadata = shown[candidate_id].get("metadata") or {}
        if candidate_metadata.get(STATE) == "frozen":
            if candidate_metadata.get(PREFIX + "epoch_id") != epoch_id:
                raise StateError(f"candidate {candidate_id} is frozen into another epoch")
            continue
        frozen = transition_metadata(
            candidate_metadata, "frozen", now=args.now or utc_now(), evidence={"epoch_id": epoch_id}
        )
        client.update_metadata(candidate_id, frozen)
    return epoch


def _transition_evidence(args: argparse.Namespace) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for name in (
        "epoch_id",
        "evidence_ref",
        "failure_class",
        "landed_sha",
        "pr_url",
        "release_ref",
        "release_sha",
        "verification_ref",
        "verified_sha",
    ):
        value = getattr(args, name, None)
        if value:
            evidence[name] = value
    repair_ids = getattr(args, "repair_bead", None)
    if repair_ids:
        evidence["repair_bead_ids"] = repair_ids
    return evidence


def transition_epoch(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    records = client.show([args.epoch_id])
    if not records:
        raise StateError(f"epoch {args.epoch_id} not found")
    record = records[0]
    metadata = record.get("metadata") or {}
    if metadata.get(KIND) != "epoch":
        raise StateError(f"bead {args.epoch_id} is not an epoch")
    updated_metadata = transition_metadata(
        metadata,
        args.state,
        now=args.now or utc_now(),
        evidence=_transition_evidence(args),
    )
    updated = client.update_metadata(args.epoch_id, updated_metadata)
    candidate_ids = updated_metadata.get(PREFIX + "candidate_ids", [])
    candidate_target = "landed" if args.state == "landed" else "verified" if args.state == "verified" else ""
    if candidate_target:
        for candidate in client.show(candidate_ids):
            candidate_metadata = candidate.get("metadata") or {}
            if candidate_metadata.get(STATE) == candidate_target:
                continue
            candidate_updated = transition_metadata(
                candidate_metadata, candidate_target, now=args.now or utc_now(), evidence={}
            )
            client.update_metadata(str(candidate["id"]), candidate_updated)
    if args.state == "promoted":
        for candidate in client.show(candidate_ids):
            for source_id in (candidate.get("metadata") or {}).get(PREFIX + "source_beads", []):
                client.close(
                    source_id,
                    f"Verified in Thunderdome epoch {args.epoch_id} at {updated_metadata[PREFIX + 'release_sha']}",
                )
    client.emit_transition(
        args.epoch_id,
        "epoch",
        args.state,
        len(updated_metadata.get(HISTORY, [])) - 1,
    )
    return updated


def read_projection(client: BeadClient, now: str, trunk_sha: str = "") -> dict[str, Any]:
    records = client.list_thunderdome()
    source_ids = sorted(
        {
            source_id
            for record in records
            if (record.get("metadata") or {}).get(KIND) == "candidate"
            for source_id in (record.get("metadata") or {}).get(PREFIX + "source_beads", [])
        }
    )
    source_states = {
        str(record["id"]): str(record.get("status", "")) for record in client.show(source_ids)
    }
    exact_trunk = require_sha("trunk_sha", trunk_sha) if trunk_sha else ""
    return project_state(
        records,
        now=now,
        source_states=source_states,
        trunk_sha=exact_trunk,
    )



def _dispatch_epoch(client: BeadClient, args: argparse.Namespace, epoch: Mapping[str, Any]) -> dict[str, Any]:
    if not args.rig:
        raise StateError("--rig is required to dispatch an epoch")
    if not args.full_gate_command:
        raise StateError("--full-gate-command is required to dispatch an epoch")
    metadata = epoch.get("metadata") or {}
    candidate_ids = require_nonempty_ids(
        "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
    )
    operator = args.operator if "/" in args.operator else f"{args.rig}/{args.operator}"
    response = client.run(
        [
            "sling",
            operator,
            str(epoch["id"]),
            "--on",
            "thunderdome-land",
            "--var",
            f"candidate_ids={','.join(candidate_ids)}",
            "--var",
            f"base_sha={metadata.get(PREFIX + 'base_sha', '')}",
            "--var",
            f"target_ref={args.target_ref}",
            "--var",
            f"full_gate_command={args.full_gate_command}",
            "--json",
        ]
    )
    workflow_id = str(response.get("workflow_id") or response.get("bead_id") or "")
    if not workflow_id:
        raise CommandError("gc sling returned no workflow identifier")
    updated_metadata = dict(metadata)
    updated_metadata[PREFIX + "workflow_id"] = workflow_id
    client.update_metadata(str(epoch["id"]), updated_metadata)
    return {
        "schema_version": "1",
        "ok": True,
        "action": "dispatched",
        "epoch_id": str(epoch["id"]),
        "workflow_id": workflow_id,
        "candidate_ids": candidate_ids,
    }


def reconcile(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    records = client.list_thunderdome()
    now = args.now or utc_now()
    source_ids = sorted(
        {
            source_id
            for record in records
            if (record.get("metadata") or {}).get(KIND) == "candidate"
            for source_id in (record.get("metadata") or {}).get(PREFIX + "source_beads", [])
        }
    )
    source_states = {
        str(record["id"]): str(record.get("status", ""))
        for record in client.show(source_ids)
    }
    projection = project_state(
        records,
        now=now,
        source_states=source_states,
        trunk_sha=args.trunk_sha,
    )
    if not projection["ok"]:
        raise StateError(
            f"refusing reconcile with {len(projection['violations'])} invariant violation(s)"
        )
    plan = plan_reconcile(
        records,
        now=now,
        trunk_sha=args.trunk_sha,
        max_depth=args.max_depth,
        max_age_seconds=args.max_age_seconds,
    )
    if plan["active_epoch_ids"]:
        active = {
            str(record.get("id", "")): record
            for record in records
            if str(record.get("id", "")) in plan["active_epoch_ids"]
        }
        if len(active) == 1:
            epoch = active[plan["active_epoch_ids"][0]]
            metadata = epoch.get("metadata") or {}
            if metadata.get(STATE) == "assembling" and not metadata.get(PREFIX + "workflow_id"):
                if args.dry_run:
                    return {**plan, "ok": True, "action": "would_resume"}
                return _dispatch_epoch(client, args, epoch)
        return {**plan, "ok": True, "action": "none"}
    if not plan["due"]:
        return {**plan, "ok": True, "action": "none"}
    if args.dry_run:
        return {**plan, "ok": True, "action": "would_dispatch"}

    epoch_args = argparse.Namespace(
        candidate=plan["candidate_ids"],
        base_sha=args.trunk_sha,
        target_ref=args.target_ref,
        now=args.now,
    )
    epoch = open_epoch(client, epoch_args)
    return _dispatch_epoch(client, args, epoch)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage and observe Continuous Thunderdome state")
    parser.add_argument("--gc-bin", default="gc")
    parser.add_argument("--city", default=os.environ.get("GC_CITY_DIR", ""))
    parser.add_argument("--rig", default=os.environ.get("GC_RIG_NAME", os.environ.get("GC_RIG", "")))
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidate = subparsers.add_parser("candidate", help="Manage land candidates")
    candidate_sub = candidate.add_subparsers(dest="candidate_command", required=True)
    enqueue = candidate_sub.add_parser("enqueue", help="Create an immutable reviewed land candidate")
    enqueue.add_argument("--source-bead", action="append", required=True)
    enqueue.add_argument("--delivery-unit", required=True)
    enqueue.add_argument("--commit", required=True)
    enqueue.add_argument("--base-sha", required=True)
    enqueue.add_argument("--summary-path", required=True)
    enqueue.add_argument("--review-path", required=True)
    enqueue.add_argument("--now", default="")
    enqueue.add_argument("--json", action="store_true")

    epoch = subparsers.add_parser("epoch", help="Manage immutable land epochs")
    epoch_sub = epoch.add_subparsers(dest="epoch_command", required=True)
    open_command = epoch_sub.add_parser("open", help="Freeze queued candidates into an epoch")
    open_command.add_argument("--candidate", action="append", required=True)
    open_command.add_argument("--base-sha", required=True)
    open_command.add_argument("--target-ref", default="refs/heads/main")
    open_command.add_argument("--now", default="")
    open_command.add_argument("--json", action="store_true")

    transition = epoch_sub.add_parser("transition", help="Apply one validated epoch transition")
    transition.add_argument("epoch_id")
    transition.add_argument("state", choices=sorted(EPOCH_STATES))
    transition.add_argument("--landed-sha")
    transition.add_argument("--verified-sha")
    transition.add_argument("--release-sha")
    transition.add_argument("--release-ref")
    transition.add_argument("--pr-url")
    transition.add_argument("--failure-class", choices=sorted(SAFE_FAILURE_CLASSES))
    transition.add_argument("--evidence-ref")
    transition.add_argument("--verification-ref")
    transition.add_argument("--repair-bead", action="append")
    transition.add_argument("--now", default="")
    transition.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="Project queue, epoch, release, and invariant state")
    status.add_argument("--now", default="")
    status.add_argument("--json", action="store_true")
    status.add_argument("--trunk-sha", default="")
    status.add_argument("--fail-on-violation", action="store_true")

    reconcile_command = subparsers.add_parser(
        "reconcile", help="Dispatch one bounded epoch when queue depth or age is due"
    )
    reconcile_command.add_argument("--trunk-sha", required=True)
    reconcile_command.add_argument("--max-depth", type=int, default=8)
    reconcile_command.add_argument("--max-age-seconds", type=int, default=1800)
    reconcile_command.add_argument("--target-ref", default="refs/heads/main")
    reconcile_command.add_argument("--operator", default="gc.run-operator")
    reconcile_command.add_argument("--full-gate-command", default="")
    reconcile_command.add_argument("--now", default="")
    reconcile_command.add_argument("--dry-run", action="store_true")
    reconcile_command.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, runner: Runner = subprocess_runner) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    client = BeadClient(gc_bin=args.gc_bin, city=args.city, rig=args.rig, runner=runner)
    try:
        if args.command == "candidate" and args.candidate_command == "enqueue":
            result = enqueue_candidate(client, args)
        elif args.command == "epoch" and args.epoch_command == "open":
            result = open_epoch(client, args)
        elif args.command == "epoch" and args.epoch_command == "transition":
            result = transition_epoch(client, args)
        elif args.command == "reconcile":
            result = reconcile(client, args)
        elif args.command == "status":
            projection = read_projection(client, args.now or utc_now(), args.trunk_sha)
            if args.json:
                print(json.dumps(projection, sort_keys=True))
            else:
                print(format_status(projection))
            return 1 if args.fail_on_violation and not projection["ok"] else 0
        else:
            parser.error("unsupported command")
            return 2
        if getattr(args, "json", False):
            print(json.dumps(result, sort_keys=True))
        else:
            print(result.get("id", ""))
        return 0
    except (CommandError, StateError) as exc:
        error = {"schema_version": "1", "ok": False, "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(error, sort_keys=True))
        else:
            print(f"thunderdome: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
