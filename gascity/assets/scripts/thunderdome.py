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
RECORD = PREFIX + "record"
CANDIDATE_ID = PREFIX + "candidate_id"
ACTIVE_EPOCH = PREFIX + "active_epoch"
PROMOTED_BY = PREFIX + "promoted_by"
EMITTED_SEQ = PREFIX + "emitted_seq"
DISPATCH_INTENT = PREFIX + "dispatch_intent"
INGRESS_STATE = PREFIX + "ingress_state"
INGRESS_REVIEWED = "reviewed"
INGRESS_QUEUED = "queued"
INGRESS_CANDIDATE_ID = PREFIX + "candidate_id"
REFRESH_INTENT = PREFIX + "refresh_intent"
REFRESH_WORKFLOW_ID = PREFIX + "refresh_workflow_id"
CLEANUP_INTENT = PREFIX + "cleanup_intent"
CLEANUP_WORKFLOW_ID = PREFIX + "cleanup_workflow_id"
MAX_HISTORY = 64
COMMAND_DIAGNOSTIC_LIMIT = 512
COMMAND_DIAGNOSTIC_SCAN_LIMIT = COMMAND_DIAGNOSTIC_LIMIT * 8
SENSITIVE_DIAGNOSTIC_PATTERN = re.compile(
    r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+|"
    r"(?:token|password|passwd|secret|api[_-]?key)\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|\S+)"
)
CANDIDATE_STATES = {"queued", "frozen", "landed", "verified", "superseded", "rejected"}
EPOCH_STATES = {
    "assembling",
    "landed",
    "verifying",
    "red",
    "repairing",
    "verified",
    "promoting",
    "promotion_committing",
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
    "promoting": {"promotion_committing", "promotion_failed", "failed"},
    "promotion_committing": {"promoted"},
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


def _decode_structured_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    decoded = copy.deepcopy(dict(metadata))
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
    return decoded


def decode_thunderdome_record(record: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    metadata = result.get("metadata")
    if not isinstance(metadata, Mapping):
        return result
    raw = _decode_structured_metadata(metadata)
    result["_thunderdome_raw_metadata"] = copy.deepcopy(raw)
    payload = raw.get(RECORD)
    if payload in (None, ""):
        result["metadata"] = raw
        return result
    if not isinstance(payload, str):
        raise CommandError(f"metadata field {RECORD} must be canonical JSON text")
    try:
        envelope_value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CommandError(f"metadata field {RECORD} contains malformed JSON") from exc
    if not isinstance(envelope_value, Mapping):
        raise CommandError(f"metadata field {RECORD} must decode to an object")
    envelope = _decode_structured_metadata(envelope_value)
    if canonical_json(envelope) != payload:
        raise CommandError(f"metadata field {RECORD} is not canonical JSON")
    merged = dict(raw)
    merged.update(envelope)
    merged[RECORD] = payload
    result["metadata"] = merged
    return result


def record_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    payload = metadata.get(RECORD)
    if payload in (None, ""):
        return _decode_structured_metadata(metadata)
    if not isinstance(payload, str):
        raise CommandError(f"metadata field {RECORD} must be canonical JSON text")
    try:
        envelope_value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CommandError(f"metadata field {RECORD} contains malformed JSON") from exc
    if not isinstance(envelope_value, Mapping):
        raise CommandError(f"metadata field {RECORD} must decode to an object")
    envelope = _decode_structured_metadata(envelope_value)
    if canonical_json(envelope) != payload:
        raise CommandError(f"metadata field {RECORD} is not canonical JSON")
    return envelope


def authoritative_value(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    value = metadata.get(RECORD, "")
    return value if isinstance(value, str) else ""


def raw_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("_thunderdome_raw_metadata")
    if isinstance(raw, Mapping):
        return dict(raw)
    metadata = record.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def bead_prefix(bead_ids: Iterable[str]) -> str:
    prefixes: set[str] = set()
    for bead_id in require_nonempty_ids("bead_ids", bead_ids):
        for marker in ("-tdc-", "-tde-", "-thunderdome-control"):
            if marker in bead_id:
                prefix = bead_id.split(marker, 1)[0]
                break
        else:
            prefix, separator, suffix = bead_id.partition("-")
            if not separator or not prefix or not suffix:
                raise StateError(f"cannot derive rig bead prefix from {bead_id!r}")
        prefixes.add(prefix)
    if len(prefixes) != 1:
        raise StateError("Thunderdome records must belong to one rig bead prefix")
    return next(iter(prefixes))


def candidate_record_id(metadata: Mapping[str, Any]) -> str:
    prefix = bead_prefix(metadata.get(PREFIX + "source_beads", []))
    return f"{prefix}-tdc-{str(metadata.get(PREFIX + 'candidate_key', ''))[:12]}"


def epoch_record_id(metadata: Mapping[str, Any]) -> str:
    prefix = bead_prefix(metadata.get(PREFIX + "candidate_ids", []))
    return f"{prefix}-tde-{str(metadata.get(PREFIX + 'membership_hash', ''))[:12]}"


def control_record_id(bead_ids: Iterable[str]) -> str:
    return f"{bead_prefix(bead_ids)}-thunderdome-control"


def discovery_mirrors(envelope: Mapping[str, Any]) -> dict[str, str]:
    mirrors = {
        PREFIX + "schema": str(envelope.get(PREFIX + "schema", "")),
        KIND: str(envelope.get(KIND, "")),
    }
    kind = envelope.get(KIND)
    if kind == "candidate":
        mirrors[PREFIX + "candidate_key"] = str(envelope.get(PREFIX + "candidate_key", ""))
    elif kind == "epoch":
        mirrors[PREFIX + "membership_hash"] = str(envelope.get(PREFIX + "membership_hash", ""))
    return mirrors


def stored_metadata(envelope: Mapping[str, Any]) -> dict[str, str]:
    return {RECORD: canonical_json(envelope), **discovery_mirrors(envelope)}


def epoch_intent(epoch_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "epoch_id": epoch_id,
        "candidate_ids": require_nonempty_ids(
            "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
        ),
        "membership_hash": str(metadata.get(PREFIX + "membership_hash", "")),
        "base_sha": require_sha("base_sha", str(metadata.get(PREFIX + "base_sha", ""))),
        "target_ref": require_ref("target_ref", str(metadata.get(PREFIX + "target_ref", ""))),
        "created_at": str(metadata.get(PREFIX + "created_at", "")),
    }


def parse_epoch_intent(payload: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StateError("active epoch intent is malformed") from exc
    if not isinstance(value, Mapping) or canonical_json(value) != payload:
        raise StateError("active epoch intent is not canonical JSON")
    required = {
        "schema_version",
        "epoch_id",
        "candidate_ids",
        "membership_hash",
        "base_sha",
        "target_ref",
        "created_at",
    }
    if set(value) != required or value.get("schema_version") != "1":
        raise StateError("active epoch intent has an unsupported shape")
    candidates = require_nonempty_ids("candidate_ids", value.get("candidate_ids", []))
    if candidates != value.get("candidate_ids") or digest(candidates) != value.get("membership_hash"):
        raise StateError("active epoch intent membership is not canonical")
    epoch_id = str(value.get("epoch_id", "")).strip()
    if not epoch_id or any(character.isspace() for character in epoch_id):
        raise StateError("active epoch intent has an invalid epoch ID")
    if bead_prefix([epoch_id]) != bead_prefix(candidates):
        raise StateError("active epoch intent epoch ID belongs to another rig")
    new_epoch_metadata(
        candidate_ids=candidates,
        base_sha=str(value.get("base_sha", "")),
        target_ref=str(value.get("target_ref", "")),
        now=str(value.get("created_at", "")),
    )
    return dict(value)


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
        metadata = record_metadata(record)
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
    next_seq = int(history[-1].get("seq", -1)) + 1 if history else 0
    history.append(
        {
            "seq": next_seq,
            "from": current,
            "to": target_state,
            "at": now,
            "evidence": safe_evidence,
        }
    )
    history = history[-MAX_HISTORY:]
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
        metadata = record_metadata(record)
        if metadata.get(PREFIX + "schema") != SCHEMA:
            continue
        bead_id = str(record.get("id", ""))
        if metadata.get(KIND) == "candidate":
            candidates[bead_id] = record
        elif metadata.get(KIND) == "epoch":
            epochs[bead_id] = record

    source_to_candidates: dict[str, list[str]] = defaultdict(list)
    for candidate_id, record in candidates.items():
        metadata = record_metadata(record)
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
        "promotion_committing": "verified",
        "promotion_failed": "verified",
        "promoted": "verified",
    }
    epoch_views: list[dict[str, Any]] = []
    for epoch_id, record in epochs.items():
        metadata = record_metadata(record)
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
            candidate_metadata = record_metadata(candidate)
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
        if state in {"verified", "promoting", "promotion_committing", "promotion_failed", "promoted"} and verified_sha != landed_sha:
            violations.append(
                _violation("epoch_verified_sha_mismatch", epoch_id, "verified SHA does not match latest landed SHA")
            )
        if state in {"promotion_committing", "promoted"} and release_sha != verified_sha:
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

    candidate_metadata = {
        candidate_id: record_metadata(record) for candidate_id, record in candidates.items()
    }
    queue_counts = Counter(
        str(metadata.get(STATE, "unknown")) for metadata in candidate_metadata.values()
    )
    queued_ages = [
        age
        for metadata in candidate_metadata.values()
        if metadata.get(STATE) == "queued"
        for age in [_age_seconds(metadata.get(PREFIX + "created_at"), now)]
        if age is not None
    ]
    stale_queued_ids = sorted(
        candidate_id
        for candidate_id, metadata in candidate_metadata.items()
        if metadata.get(STATE) == "queued"
        and trunk_sha
        and metadata.get(PREFIX + "base_sha") != trunk_sha
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
    active_epochs = sorted(
        (view for view in epoch_views if view["state"] not in TERMINAL_EPOCH_STATES),
        key=lambda view: view["id"],
    )
    if len(active_epochs) > 1:
        violations.append(
            _violation(
                "multiple_active_epochs",
                ",".join(view["id"] for view in active_epochs),
                "more than one epoch is active",
            )
        )
    violations.sort(key=lambda item: (item["code"], item["entity"], item["message"]))
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
        if record_metadata(record).get(PREFIX + "schema") == SCHEMA
        and record_metadata(record).get(KIND) == "epoch"
        and record_metadata(record).get(STATE) not in TERMINAL_EPOCH_STATES
    )
    queued: list[tuple[int, str]] = []
    stale_candidate_ids: list[str] = []
    for record in records:
        metadata = record_metadata(record)
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


def _command_diagnostic(
    stream: str | None,
    *,
    env: Mapping[str, str],
) -> str:
    raw = (stream or "")[:COMMAND_DIAGNOSTIC_SCAN_LIMIT]
    environment_values = sorted(
        {value for value in env.values() if value},
        key=len,
        reverse=True,
    )
    long_values = [re.escape(value) for value in environment_values if len(value) >= 4]
    short_values = [re.escape(value) for value in environment_values if len(value) < 4]
    alternatives = long_values
    if short_values:
        alternatives.append(r"(?<!\w)(?:" + "|".join(short_values) + r")(?!\w)")
    sanitized = (
        re.sub("|".join(alternatives), "[redacted]", raw) if alternatives else raw
    )
    sanitized = SENSITIVE_DIAGNOSTIC_PATTERN.sub(r"\1[redacted]", sanitized)
    concise = " ".join(sanitized.split())
    if len(concise) <= COMMAND_DIAGNOSTIC_LIMIT:
        return concise
    suffix = "…[truncated]"
    return concise[: COMMAND_DIAGNOSTIC_LIMIT - len(suffix)] + suffix


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

    def run(
        self,
        args: Sequence[str],
        *,
        expect_json: bool = True,
        extra_env: Mapping[str, str] | None = None,
    ) -> Any:
        env = os.environ.copy()
        env.update(extra_env or {})
        completed = self._runner([*self._prefix(), *args], env)
        if completed.returncode != 0:
            operation = " ".join(args[:2])
            stream_name, diagnostic = (
                ("stderr", completed.stderr)
                if (completed.stderr or "").strip()
                else ("stdout", completed.stdout)
            )
            detail = _command_diagnostic(diagnostic, env=env) or "<no output>"
            raise CommandError(
                f"gc {operation} failed with exit {completed.returncode}; "
                f"{stream_name}: {detail}"
            )
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
        if not isinstance(result, list):
            raise CommandError("gc bd list returned a non-array result")
        return [decode_thunderdome_record(record) for record in result]

    def list_metadata(self, key: str, value: str) -> list[dict[str, Any]]:
        result = self.run(
            [
                "bd",
                "list",
                "--status",
                "open,in_progress,blocked,deferred,closed",
                "--metadata-field",
                f"{key}={value}",
                "--limit",
                "0",
                "--json",
            ]
        )
        if not isinstance(result, list):
            raise CommandError("gc bd list returned a non-array result")
        records = [decode_thunderdome_record(record) for record in result]
        return [
            record
            for record in records
            if raw_metadata(record).get(key) == value
        ]

    def show(self, bead_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not bead_ids:
            return []
        result = self.run(["bd", "show", *bead_ids, "--json"])
        rows = result if isinstance(result, list) else [result]
        return [decode_thunderdome_record(record) for record in rows]

    def metadata_cas(self, bead_id: str, key: str, expected: str, value: str) -> bool:
        result = self.run(
            [
                "bd",
                "metadata-cas",
                bead_id,
                "--key",
                key,
                "--expected",
                expected,
                "--value",
                value,
                "--json",
            ]
        )
        if not isinstance(result, Mapping) or not isinstance(result.get("swapped"), bool):
            raise CommandError("gc bd metadata-cas returned an invalid result")
        return bool(result["swapped"])

    def converge_status(self, bead_id: str, status: str, *, reason: str = "") -> dict[str, Any]:
        records = self.show([bead_id])
        if not records:
            raise StateError(f"bead {bead_id} not found")
        record = records[0]
        current = str(record.get("status", ""))
        if current == status:
            return record
        if current == "closed":
            raise StateError(f"bead {bead_id} is already closed with conflicting lifecycle evidence")
        if status == "closed":
            if not reason:
                raise StateError("closing a bead requires a reason")
            updated = self.run(["bd", "close", bead_id, "--reason", reason, "--json"])
        else:
            updated = self.run(["bd", "update", bead_id, "--status", status, "--json"])
        row = updated[0] if isinstance(updated, list) else updated
        return decode_thunderdome_record(row)

    @staticmethod
    def _immutable_identity(envelope: Mapping[str, Any]) -> dict[str, Any]:
        kind = envelope.get(KIND)
        common = {
            PREFIX + "schema": envelope.get(PREFIX + "schema"),
            KIND: kind,
        }
        if kind == "candidate":
            keys = (
                PREFIX + "candidate_key",
                PREFIX + "source_beads",
                PREFIX + "delivery_unit",
                PREFIX + "commit",
                PREFIX + "base_sha",
                PREFIX + "summary_path",
                PREFIX + "review_path",
            )
        elif kind == "epoch":
            keys = (
                PREFIX + "candidate_ids",
                PREFIX + "membership_hash",
                PREFIX + "base_sha",
                PREFIX + "target_ref",
            )
        elif kind == "control":
            keys = ()
        else:
            raise StateError("unsupported deterministic Thunderdome record kind")
        return {**common, **{key: envelope.get(key) for key in keys}}

    @staticmethod
    def _legacy_envelope(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in raw_metadata(record).items()
            if isinstance(key, str) and key.startswith(PREFIX) and key != RECORD
        }



    def repair_mirrors(
        self, record: Mapping[str, Any], envelope: Mapping[str, Any]
    ) -> dict[str, Any]:
        bead_id = str(record.get("id", ""))
        raw = raw_metadata(record)
        for key, desired in discovery_mirrors(envelope).items():
            current = raw.get(key, "")
            if current == desired:
                continue
            if not isinstance(current, str):
                raise StateError(f"record {bead_id} has non-string discovery mirror {key}")
            if not self.metadata_cas(bead_id, key, current, desired):
                refreshed = self.show([bead_id])
                if not refreshed or raw_metadata(refreshed[0]).get(key, "") != desired:
                    raise StateError(f"record {bead_id} has conflicting discovery mirror {key}")
                record = refreshed[0]
            raw[key] = desired
        refreshed = self.show([bead_id])
        return refreshed[0] if refreshed else dict(record)

    def authoritative_reread(
        self, bead_id: str, *, migrate: bool = True
    ) -> dict[str, Any]:
        records = self.show([bead_id])
        if not records:
            raise StateError(f"bead {bead_id} not found")
        record = records[0]
        metadata = record_metadata(record)
        kind = metadata.get(KIND)
        payload = authoritative_value(record)
        if not payload and migrate and kind in {"candidate", "epoch"}:
            if os.environ.get("GC_THUNDERDOME_ALLOW_LEGACY_MIGRATION") != "1":
                raise StateError(
                    f"legacy record {bead_id} requires a quiescent one-time migration"
                )
            envelope = self._legacy_envelope(record)
            canonical = canonical_json(envelope)
            if not self.metadata_cas(bead_id, RECORD, "", canonical):
                return self.authoritative_reread(bead_id, migrate=False)
            record = self.show([bead_id])[0]
            metadata = record_metadata(record)
            payload = authoritative_value(record)
        if kind in {"candidate", "epoch"} and not payload:
            raise StateError(f"record {bead_id} has no authoritative envelope")
        if kind in {"candidate", "epoch"}:
            return self.repair_mirrors(record, metadata)
        return record

    def cas_envelope(
        self,
        record: Mapping[str, Any],
        updated_envelope: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        bead_id = str(record.get("id", ""))
        expected = authoritative_value(record)
        if not expected:
            record = self.authoritative_reread(bead_id)
            expected = authoritative_value(record)
        value = canonical_json(updated_envelope)
        if expected == value:
            return dict(record)
        if not self.metadata_cas(bead_id, RECORD, expected, value):
            return None
        return self.authoritative_reread(bead_id, migrate=False)

    def create_or_validate(
        self,
        bead_id: str,
        title: str,
        label: str,
        envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        existing: list[dict[str, Any]] = []
        try:
            existing = self.show([bead_id])
        except CommandError:
            existing = []
        if not existing:
            try:
                created = self.run(
                    [
                        "bd",
                        "create",
                        title,
                        "--id",
                        bead_id,
                        "--type",
                        "task",
                        "--priority",
                        "1",
                        "--status",
                        "in_progress",
                        "--labels",
                        label,
                        "--metadata",
                        canonical_json(
                            stored_metadata(envelope)
                            if envelope.get(KIND) != "control"
                            else dict(envelope)
                        ),
                        "--json",
                    ]
                )
                row = created[0] if isinstance(created, list) else created
                existing = [decode_thunderdome_record(row)]
            except CommandError as create_error:
                try:
                    existing = self.show([bead_id])
                except CommandError:
                    raise create_error
        if not existing:
            raise StateError(f"deterministic record {bead_id} was not materialized")
        record = existing[0]
        current = record_metadata(record)
        if self._immutable_identity(current) != self._immutable_identity(envelope):
            raise StateError(f"deterministic record {bead_id} has conflicting immutable payload")
        if current.get(KIND) in {"candidate", "epoch"}:
            record = self.authoritative_reread(bead_id)
        if str(record.get("status", "")) == "closed" and current.get(STATE) not in (
            TERMINAL_EPOCH_STATES | {"verified", "superseded", "rejected"}
        ):
            raise StateError(f"deterministic record {bead_id} is closed before terminal convergence")
        return record

    def close(self, bead_id: str, reason: str) -> None:
        self.converge_status(bead_id, "closed", reason=reason)

    def emit_transition(self, bead_id: str, kind: str, state: str, seq: int) -> None:
        payload = canonical_json(
            {"schema_version": "1", "kind": kind, "state": state, "transition_seq": seq}
        )
        result = self.run(
            [
                "event",
                "emit",
                "thunderdome.transition",
                "--subject",
                bead_id,
                "--payload",
                payload,
                "--json",
                "--require-ack",
            ]
        )
        if not isinstance(result, Mapping) or result.get("submitted") is not True:
            raise CommandError(
                f"transition event {bead_id}/{seq} was not durably accepted"
            )


def _cas_transition(
    client: BeadClient,
    bead_id: str,
    target: str,
    *,
    now: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    for _ in range(16):
        record = client.authoritative_reread(bead_id)
        metadata = record_metadata(record)
        updated = transition_metadata(metadata, target, now=now, evidence=evidence)
        if updated == metadata:
            return record
        swapped = client.cas_envelope(record, updated)
        if swapped is not None:
            return swapped
    raise StateError(f"record {bead_id} did not converge after concurrent mutations")


def _source_reservation(client: BeadClient, source_id: str) -> tuple[dict[str, Any], str]:
    records = client.show([source_id])
    if not records:
        raise StateError(f"source bead {source_id} not found")
    record = records[0]
    value = raw_metadata(record).get(CANDIDATE_ID, "")
    if not isinstance(value, str):
        raise StateError(f"source bead {source_id} has invalid candidate reservation")
    return record, value


def _release_owned_reservations(
    client: BeadClient, candidate_id: str, source_ids: Iterable[str]
) -> None:
    for source_id in sorted(set(source_ids)):
        for _ in range(16):
            _, owner = _source_reservation(client, source_id)
            if owner == "" or owner != candidate_id:
                break
            if client.metadata_cas(source_id, CANDIDATE_ID, candidate_id, ""):
                break
        else:
            raise StateError(
                f"source bead {source_id} reservation did not converge while releasing {candidate_id}"
            )


def _reserve_candidate_sources(
    client: BeadClient, candidate_id: str, *, now: str
) -> dict[str, Any]:
    candidate = client.authoritative_reread(candidate_id)
    metadata = record_metadata(candidate)
    state = metadata.get(STATE)
    if state not in ACTIVE_CANDIDATE_STATES:
        return candidate
    acquired: list[str] = []
    source_ids = require_nonempty_ids(
        "source_beads", metadata.get(PREFIX + "source_beads", [])
    )
    for source_id in source_ids:
        source, owner = _source_reservation(client, source_id)
        if str(source.get("status", "")) == "closed":
            if metadata.get(STATE) == "queued":
                try:
                    _cas_transition(client, candidate_id, "rejected", now=now)
                finally:
                    _release_owned_reservations(client, candidate_id, source_ids)
            else:
                _release_owned_reservations(client, candidate_id, acquired)
            raise StateError(
                f"source bead {source_id} is already closed without matching promotion evidence"
            )
        if owner == candidate_id:
            continue
        if owner == "" and client.metadata_cas(
            source_id, CANDIDATE_ID, "", candidate_id
        ):
            acquired.append(source_id)
            continue
        _, owner = _source_reservation(client, source_id)
        if owner == candidate_id:
            continue
        if metadata.get(STATE) == "queued":
            try:
                _cas_transition(client, candidate_id, "rejected", now=now)
            finally:
                _release_owned_reservations(client, candidate_id, source_ids)
        else:
            _release_owned_reservations(client, candidate_id, acquired)
        raise StateError(
            f"source bead {source_id} is reserved by candidate {owner!r}; refusing conflicting ownership"
        )
    return client.authoritative_reread(candidate_id)


def enqueue_candidate(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    now = args.now or utc_now()
    metadata = new_candidate_metadata(
        source_beads=args.source_bead,
        delivery_unit=args.delivery_unit,
        commit=args.commit,
        base_sha=args.base_sha,
        summary_path=args.summary_path,
        review_path=args.review_path,
        now=now,
    )
    bead_id = candidate_record_id(metadata)
    legacy_matches = [
        record
        for record in client.list_thunderdome()
        if record_metadata(record).get(KIND) == "candidate"
        and record_metadata(record).get(PREFIX + "candidate_key")
        == metadata[PREFIX + "candidate_key"]
    ]
    if len(legacy_matches) > 1:
        raise StateError("candidate key has multiple conflicting legacy records")
    if legacy_matches and str(legacy_matches[0].get("id", "")) != bead_id:
        candidate = client.authoritative_reread(str(legacy_matches[0]["id"]))
        state = record_metadata(candidate).get(STATE)
        if state in ACTIVE_CANDIDATE_STATES:
            candidate = _reserve_candidate_sources(
                client, str(candidate["id"]), now=now
            )
        elif state in {"rejected", "superseded"}:
            _release_owned_reservations(
                client,
                str(candidate["id"]),
                record_metadata(candidate).get(PREFIX + "source_beads", []),
            )
            client.converge_status(
                str(candidate["id"]),
                "closed",
                reason=f"Thunderdome candidate converged in terminal state {state}",
            )
        return candidate
    short = metadata[PREFIX + "commit"][:12]
    candidate = client.create_or_validate(
        bead_id,
        f"Land candidate {metadata[PREFIX + 'delivery_unit']} {short}",
        "thunderdome-candidate",
        metadata,
    )
    state = record_metadata(candidate).get(STATE)
    if state in ACTIVE_CANDIDATE_STATES:
        candidate = _reserve_candidate_sources(client, bead_id, now=now)
    elif state in {"rejected", "superseded"}:
        _release_owned_reservations(
            client,
            bead_id,
            record_metadata(candidate).get(PREFIX + "source_beads", []),
        )
        client.converge_status(
            bead_id,
            "closed",
            reason=f"Thunderdome candidate converged in terminal state {state}",
        )
    return candidate


def _metadata_text(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _converge_metadata_value(
    client: BeadClient, bead_id: str, key: str, expected: str, value: str
) -> None:
    if expected == value:
        return
    if client.metadata_cas(bead_id, key, expected, value):
        return
    records = client.show([bead_id])
    current = raw_metadata(records[0]).get(key, "") if records else ""
    if current != value:
        raise StateError(f"metadata {key} on {bead_id} did not converge")


def ingest_reviewed_candidate(
    client: BeadClient, workflow: Mapping[str, Any], *, now: str
) -> dict[str, Any]:
    workflow_id = str(workflow.get("id", ""))
    metadata = record_metadata(workflow)
    if metadata.get(INGRESS_STATE) != INGRESS_REVIEWED:
        raise StateError(f"workflow {workflow_id} is not a reviewed Thunderdome ingress")
    commit = _metadata_text(metadata, PREFIX + "commit")
    validation_commit = _metadata_text(metadata, PREFIX + "validation_commit")
    if commit != validation_commit:
        raise StateError(
            f"workflow {workflow_id} candidate and validation commits do not match"
        )
    lifecycle_id = _metadata_text(metadata, "gc.worktree.id")
    lifecycle_owner = _metadata_text(metadata, "gc.worktree.owner")
    if not lifecycle_id or lifecycle_owner != workflow_id:
        raise StateError(f"workflow {workflow_id} has invalid worktree lifecycle identity")
    candidate = enqueue_candidate(
        client,
        argparse.Namespace(
            source_bead=metadata.get(PREFIX + "source_beads", []),
            delivery_unit=_metadata_text(metadata, PREFIX + "delivery_unit"),
            commit=commit,
            base_sha=_metadata_text(metadata, PREFIX + "base_sha"),
            summary_path=_metadata_text(
                metadata,
                PREFIX + "summary_path",
                "gc.implementation.summary_path",
                "gc.build.implementation_summary_path",
            ),
            review_path=_metadata_text(
                metadata,
                PREFIX + "review_path",
                "gc.build.review_report_path",
            ),
            now=now,
        ),
    )
    candidate_id = str(candidate["id"])
    candidate_raw = raw_metadata(candidate)
    _converge_metadata_value(
        client,
        candidate_id,
        "gc.worktree.id",
        str(candidate_raw.get("gc.worktree.id", "")),
        lifecycle_id,
    )
    candidate = client.authoritative_reread(candidate_id)
    candidate_raw = raw_metadata(candidate)
    _converge_metadata_value(
        client,
        candidate_id,
        "gc.worktree.owner",
        str(candidate_raw.get("gc.worktree.owner", "")),
        lifecycle_owner,
    )
    workflow_raw = raw_metadata(workflow)
    _converge_metadata_value(
        client,
        workflow_id,
        INGRESS_CANDIDATE_ID,
        str(workflow_raw.get(INGRESS_CANDIDATE_ID, "")),
        candidate_id,
    )
    _converge_metadata_value(
        client,
        workflow_id,
        INGRESS_STATE,
        INGRESS_REVIEWED,
        INGRESS_QUEUED,
    )
    return client.authoritative_reread(candidate_id)


def ingest_reviewed_candidates(
    client: BeadClient, *, now: str
) -> list[dict[str, Any]]:
    return [
        ingest_reviewed_candidate(client, workflow, now=now)
        for workflow in client.list_metadata(INGRESS_STATE, INGRESS_REVIEWED)
    ]


def _control_for(
    client: BeadClient, bead_ids: Iterable[str]
) -> dict[str, Any]:
    control_id = control_record_id(bead_ids)
    return client.create_or_validate(
        control_id,
        "Continuous Thunderdome epoch control",
        "thunderdome-control",
        {
            PREFIX + "schema": SCHEMA,
            KIND: "control",
            ACTIVE_EPOCH: "",
        },
    )


def _active_epoch_payload(control: Mapping[str, Any]) -> str:
    value = raw_metadata(control).get(ACTIVE_EPOCH, "")
    if not isinstance(value, str):
        raise StateError("Thunderdome control has invalid active epoch intent")
    return value


def _other_active_epochs(client: BeadClient, epoch_id: str) -> list[str]:
    active: list[str] = []
    for record in client.list_thunderdome():
        metadata = record_metadata(record)
        if (
            metadata.get(KIND) == "epoch"
            and metadata.get(STATE) in EPOCH_STATES - TERMINAL_EPOCH_STATES
            and str(record.get("id", "")) != epoch_id
        ):
            active.append(str(record.get("id", "")))
    return sorted(require_nonempty_ids("active_epoch_ids", active)) if active else []


def open_epoch(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    now = args.now or utc_now()
    candidate_ids = require_nonempty_ids("candidate_ids", args.candidate)
    metadata = new_epoch_metadata(
        candidate_ids=candidate_ids,
        base_sha=args.base_sha,
        target_ref=args.target_ref,
        now=now,
    )
    epoch_id = epoch_record_id(metadata)
    desired_identity = client._immutable_identity(metadata)
    exact: list[dict[str, Any]] = []
    try:
        exact = client.show([epoch_id])
    except CommandError:
        exact = []
    if exact and client._immutable_identity(record_metadata(exact[0])) != desired_identity:
        raise StateError(f"deterministic epoch {epoch_id} has conflicting immutable payload")
    matches = {
        str(record["id"]): record
        for record in client.list_thunderdome()
        if record_metadata(record).get(KIND) == "epoch"
        and client._immutable_identity(record_metadata(record)) == desired_identity
    }
    for record in exact:
        matches[str(record["id"])] = record
    if len(matches) > 1:
        raise StateError(
            "epoch membership has multiple durable records: "
            + ", ".join(sorted(matches))
        )
    if matches:
        epoch_id = next(iter(matches))
        existing_epoch = client.authoritative_reread(epoch_id)
        if record_metadata(existing_epoch).get(STATE) != "assembling":
            return converge_epoch(client, existing_epoch, now=now)
        metadata = record_metadata(existing_epoch)

    shown = {str(record["id"]): record for record in client.show(candidate_ids)}
    missing = sorted(set(candidate_ids) - set(shown))
    if missing:
        raise StateError(f"missing candidate beads: {', '.join(missing)}")
    for candidate_id in candidate_ids:
        shown[candidate_id] = _reserve_candidate_sources(
            client, candidate_id, now=now
        )
    validate_epoch_candidates(list(shown.values()), base_sha=args.base_sha)
    for candidate_id, candidate in shown.items():
        candidate_metadata = record_metadata(candidate)
        if (
            candidate_metadata.get(STATE) == "frozen"
            and candidate_metadata.get(PREFIX + "epoch_id") != epoch_id
        ):
            raise StateError(f"candidate {candidate_id} is frozen into another epoch")

    control = _control_for(client, candidate_ids)
    manifest = canonical_json(epoch_intent(epoch_id, metadata))
    for _ in range(2):
        active = _active_epoch_payload(control)
        if active == manifest:
            break
        if active == "":
            conflicting_epochs = _other_active_epochs(client, epoch_id)
            if conflicting_epochs:
                raise StateError(
                    "active epoch records already exist outside the control ledger: "
                    + ", ".join(conflicting_epochs)
                )
            if client.metadata_cas(str(control["id"]), ACTIVE_EPOCH, "", manifest):
                break
        control = client.show([str(control["id"])])[0]
        active = _active_epoch_payload(control)
        if active == manifest:
            break
        active_intent = parse_epoch_intent(active)
        if active_intent["epoch_id"] == epoch_id:
            active_metadata = new_epoch_metadata(
                candidate_ids=active_intent["candidate_ids"],
                base_sha=str(active_intent["base_sha"]),
                target_ref=str(active_intent["target_ref"]),
                now=str(active_intent["created_at"]),
            )
            if (
                client._immutable_identity(active_metadata)
                != client._immutable_identity(metadata)
            ):
                raise StateError(
                    f"active intent for deterministic epoch {epoch_id} has conflicting payload"
                )
            metadata = active_metadata
            manifest = active
            break
        active_records = client.show([str(active_intent["epoch_id"])])
        if active_records and record_metadata(active_records[0]).get(STATE) in TERMINAL_EPOCH_STATES:
            converge_epoch(client, active_records[0], now=now)
            control = client.show([str(control["id"])])[0]
            continue
        raise StateError(
            f"active epoch {active_intent['epoch_id']} already owns the Thunderdome control"
        )
    else:
        raise StateError("could not elect the active Thunderdome epoch")

    epoch = client.create_or_validate(
        epoch_id,
        f"Thunderdome epoch {metadata[PREFIX + 'membership_hash'][:12]}",
        "thunderdome-epoch",
        metadata,
    )
    return converge_epoch(client, epoch, now=now)


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


def _ensure_epoch_source_reservations(
    client: BeadClient,
    candidate_id: str,
    metadata: Mapping[str, Any],
    epoch_id: str,
) -> None:
    for source_id in require_nonempty_ids(
        "source_beads", metadata.get(PREFIX + "source_beads", [])
    ):
        source, owner = _source_reservation(client, source_id)
        if str(source.get("status", "")) == "closed":
            if raw_metadata(source).get(PROMOTED_BY, "") == epoch_id:
                continue
            raise StateError(
                f"source bead {source_id} closed before epoch candidate convergence without matching promotion provenance"
            )
        if owner == candidate_id:
            continue
        if owner == "" and client.metadata_cas(
            source_id, CANDIDATE_ID, "", candidate_id
        ):
            continue
        _, owner = _source_reservation(client, source_id)
        if owner != candidate_id:
            raise StateError(
                f"source bead {source_id} has conflicting reservation owner {owner!r}"
            )


def _converge_candidate_follower(
    client: BeadClient,
    candidate_id: str,
    epoch_id: str,
    epoch_state: str,
    *,
    now: str,
) -> dict[str, Any]:
    record = client.authoritative_reread(candidate_id)
    metadata = record_metadata(record)
    if metadata.get(KIND) != "candidate":
        raise StateError(f"epoch member {candidate_id} is not a candidate")
    if (
        str(record.get("status", "")) == "closed"
        and metadata.get(STATE) in ACTIVE_CANDIDATE_STATES
        and epoch_state not in TERMINAL_EPOCH_STATES
    ):
        raise StateError(f"candidate {candidate_id} is closed before terminal convergence")
    if epoch_state in ABANDONED_EPOCH_STATES:
        state = metadata.get(STATE)
        if state in {"frozen", "landed"} and metadata.get(PREFIX + "epoch_id") != epoch_id:
            raise StateError(f"candidate {candidate_id} belongs to another epoch")
        if state in {"queued", "frozen", "landed"}:
            return _cas_transition(client, candidate_id, "rejected", now=now)
        if state in {"verified", "rejected", "superseded"}:
            return record
        raise StateError(
            f"candidate {candidate_id} has unsupported terminal follower state {state!r}"
        )

    desired = {
        "assembling": "frozen",
        "landed": "landed",
        "verifying": "landed",
        "red": "landed",
        "repairing": "landed",
        "verified": "verified",
        "promoting": "verified",
        "promotion_committing": "verified",
        "promotion_failed": "verified",
        "promoted": "verified",
    }.get(epoch_state)
    if not desired:
        return record
    if epoch_state != "promoted":
        _ensure_epoch_source_reservations(client, candidate_id, metadata, epoch_id)
    for target in ("frozen", "landed", "verified"):
        record = client.authoritative_reread(candidate_id)
        metadata = record_metadata(record)
        state = str(metadata.get(STATE, ""))
        if state == target:
            if target == "frozen" and metadata.get(PREFIX + "epoch_id") != epoch_id:
                raise StateError(f"candidate {candidate_id} is frozen into another epoch")
        elif target == "frozen" and state == "queued":
            record = _cas_transition(
                client,
                candidate_id,
                "frozen",
                now=now,
                evidence={"epoch_id": epoch_id},
            )
        elif target == "landed" and state == "frozen":
            if metadata.get(PREFIX + "epoch_id") != epoch_id:
                raise StateError(f"candidate {candidate_id} is frozen into another epoch")
            record = _cas_transition(client, candidate_id, "landed", now=now)
        elif target == "verified" and state == "landed":
            record = _cas_transition(client, candidate_id, "verified", now=now)
        elif state in {"rejected", "superseded"}:
            raise StateError(
                f"candidate {candidate_id} is {state} while epoch {epoch_id} owns it"
            )
        if target == desired:
            return record
    return record


def _release_active_epoch(
    client: BeadClient, epoch_id: str, candidate_ids: Iterable[str]
) -> None:
    control_id = control_record_id(candidate_ids)
    try:
        controls = client.show([control_id])
    except CommandError:
        return
    if not controls:
        return
    active = _active_epoch_payload(controls[0])
    if active == "":
        return
    intent = parse_epoch_intent(active)
    if intent["epoch_id"] != epoch_id:
        return
    if not client.metadata_cas(control_id, ACTIVE_EPOCH, active, ""):
        current = client.show([control_id])[0]
        current_active = _active_epoch_payload(current)
        if current_active:
            current_intent = parse_epoch_intent(current_active)
            if current_intent["epoch_id"] == epoch_id:
                raise StateError(f"active epoch control for {epoch_id} did not converge")


def _converge_promoted_source(
    client: BeadClient,
    source_id: str,
    candidate_id: str,
    epoch_id: str,
    release_sha: str,
) -> None:
    source, reservation = _source_reservation(client, source_id)
    provenance = raw_metadata(source).get(PROMOTED_BY, "")
    if not isinstance(provenance, str):
        raise StateError(f"source bead {source_id} has invalid promotion provenance")
    expected_close_reason = f"Verified in Thunderdome epoch {epoch_id} at {release_sha}"
    if str(source.get("status", "")) == "closed":
        if provenance != epoch_id or str(source.get("close_reason", "")) != expected_close_reason:
            if provenance == epoch_id:
                client.metadata_cas(source_id, PROMOTED_BY, epoch_id, "")
            raise StateError(
                f"closed source bead {source_id} lacks matching promotion provenance"
            )
    else:
        provenance_installed = False
        if provenance == "":
            if not client.metadata_cas(source_id, PROMOTED_BY, "", epoch_id):
                source, reservation = _source_reservation(client, source_id)
                provenance = raw_metadata(source).get(PROMOTED_BY, "")
            else:
                source, reservation = _source_reservation(client, source_id)
                provenance = epoch_id
                provenance_installed = True
        if provenance != epoch_id:
            raise StateError(
                f"source bead {source_id} was promoted by conflicting epoch {provenance!r}"
            )
        if reservation != candidate_id:
            raise StateError(
                f"source bead {source_id} is not reserved by promoted candidate {candidate_id}"
            )
        source = client.converge_status(
            source_id,
            "closed",
            reason=expected_close_reason,
        )
        if str(source.get("close_reason", "")) != expected_close_reason:
            if provenance_installed:
                client.metadata_cas(source_id, PROMOTED_BY, epoch_id, "")
            raise StateError(
                f"source bead {source_id} was closed outside epoch {epoch_id}"
            )
    for _ in range(16):
        _, reservation = _source_reservation(client, source_id)
        if reservation == "":
            break
        if reservation != candidate_id:
            raise StateError(
                f"source bead {source_id} reservation changed during promotion convergence"
            )
        if client.metadata_cas(source_id, CANDIDATE_ID, candidate_id, ""):
            break
    else:
        raise StateError(
            f"source bead {source_id} reservation release did not converge"
        )


def _converge_promoted_candidates(
    client: BeadClient,
    candidates: Sequence[Mapping[str, Any]],
    epoch_id: str,
    release_sha: str,
    *,
    close_candidates: bool,
) -> None:
    for candidate in candidates:
        candidate_id = str(candidate["id"])
        candidate_metadata = record_metadata(candidate)
        for source_id in candidate_metadata.get(PREFIX + "source_beads", []):
            _converge_promoted_source(
                client, source_id, candidate_id, epoch_id, release_sha
            )
        if close_candidates:
            client.converge_status(
                candidate_id,
                "closed",
                reason=f"Verified in promoted Thunderdome epoch {epoch_id}",
            )


def _sealed_promotion_evidence(
    metadata: Mapping[str, Any], epoch_id: str
) -> dict[str, Any]:
    evidence = {
        "epoch_id": epoch_id,
        "release_sha": str(metadata.get(PREFIX + "release_sha", "")),
        "release_ref": str(metadata.get(PREFIX + "release_ref", "")),
    }
    evidence_ref = str(metadata.get(PREFIX + "evidence_ref", ""))
    if evidence_ref:
        evidence["evidence_ref"] = evidence_ref
    return evidence


def converge_epoch(
    client: BeadClient, epoch: Mapping[str, Any], *, now: str
) -> dict[str, Any]:
    epoch_id = str(epoch.get("id", ""))
    current = client.authoritative_reread(epoch_id)
    metadata = record_metadata(current)
    if metadata.get(KIND) != "epoch":
        raise StateError(f"bead {epoch_id} is not an epoch")
    state = str(metadata.get(STATE, ""))
    if (
        str(current.get("status", "")) == "closed"
        and state not in TERMINAL_EPOCH_STATES
    ):
        raise StateError(f"epoch {epoch_id} is closed before terminal convergence")
    candidate_ids = require_nonempty_ids(
        "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
    )
    candidates = [
        _converge_candidate_follower(
            client, candidate_id, epoch_id, state, now=now
        )
        for candidate_id in candidate_ids
    ]
    if state == "promotion_committing":
        sealed = _sealed_promotion_evidence(metadata, epoch_id)
        _converge_promoted_candidates(
            client,
            candidates,
            epoch_id,
            sealed["release_sha"],
            close_candidates=False,
        )
        promoted = _cas_transition(
            client,
            epoch_id,
            "promoted",
            now=now,
            evidence=sealed,
        )
        return converge_epoch(client, promoted, now=now)
    if state == "promoted":
        _converge_promoted_candidates(
            client,
            candidates,
            epoch_id,
            str(metadata.get(PREFIX + "release_sha", "")),
            close_candidates=True,
        )
    if state in ABANDONED_EPOCH_STATES:
        for candidate in candidates:
            candidate_id = str(candidate["id"])
            candidate_metadata = record_metadata(candidate)
            _release_owned_reservations(
                client,
                candidate_id,
                candidate_metadata.get(PREFIX + "source_beads", []),
            )
            client.converge_status(
                candidate_id,
                "closed",
                reason=f"Thunderdome epoch {epoch_id} ended in {state}",
            )
    if state in TERMINAL_EPOCH_STATES:
        client.converge_status(
            epoch_id,
            "closed",
            reason=f"Thunderdome epoch converged in terminal state {state}",
        )
        _release_active_epoch(client, epoch_id, candidate_ids)
    return client.authoritative_reread(epoch_id)


def _mark_and_emit_transition(
    client: BeadClient, epoch: Mapping[str, Any]
) -> dict[str, Any]:
    epoch_id = str(epoch["id"])
    marked_count = 0
    cas_conflicts = 0
    while marked_count <= MAX_HISTORY:
        record = client.authoritative_reread(epoch_id)
        metadata = record_metadata(record)
        history = metadata.get(HISTORY, [])
        if not history:
            return record
        emitted = int(metadata.get(EMITTED_SEQ, -1))
        pending = [
            entry
            for entry in history
            if int(entry.get("seq", -1)) > emitted
        ]
        if not pending:
            return record
        if marked_count == MAX_HISTORY:
            raise StateError(f"event marker for epoch {epoch_id} exceeded bounded history")
        entry = pending[0]
        seq = int(entry.get("seq", -1))
        client.emit_transition(
            epoch_id,
            "epoch",
            str(entry.get("to", "")),
            seq,
        )
        marked = dict(metadata)
        marked[EMITTED_SEQ] = seq
        if client.cas_envelope(record, marked) is not None:
            marked_count += 1
            cas_conflicts = 0
            continue
        cas_conflicts += 1
        if cas_conflicts >= 16:
            raise StateError(
                f"event marker for epoch {epoch_id} did not converge after concurrent mutations"
            )
    raise StateError(f"event marker for epoch {epoch_id} did not converge")


def transition_epoch(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    now = args.now or utc_now()
    record = client.authoritative_reread(args.epoch_id)
    metadata = record_metadata(record)
    if metadata.get(KIND) != "epoch":
        raise StateError(f"bead {args.epoch_id} is not an epoch")
    record = converge_epoch(client, record, now=now)
    metadata = record_metadata(record)
    evidence = _transition_evidence(args)
    if args.state == "promoted" and metadata.get(STATE) != "promoted":
        unsupported = set(evidence) - {
            "epoch_id",
            "release_sha",
            "release_ref",
            "evidence_ref",
        }
        if unsupported:
            raise StateError(
                "promoted transition has unsupported evidence fields: "
                + ", ".join(sorted(unsupported))
            )
        if metadata.get(STATE) == "promoting":
            committing_preview = transition_metadata(
                metadata,
                "promotion_committing",
                now=now,
                evidence=evidence,
            )
            transition_metadata(
                committing_preview,
                "promoted",
                now=now,
                evidence=evidence,
            )
            record = _cas_transition(
                client,
                args.epoch_id,
                "promotion_committing",
                now=now,
                evidence=evidence,
            )
            metadata = record_metadata(record)
        if metadata.get(STATE) != "promotion_committing":
            raise StateError(
                f"epoch {args.epoch_id} cannot commit promotion from {metadata.get(STATE)!r}"
            )
        sealed = _sealed_promotion_evidence(metadata, args.epoch_id)
        requested = {
            key: value
            for key, value in evidence.items()
            if key in {"epoch_id", "release_sha", "release_ref", "evidence_ref"}
        }
        if requested != sealed:
            raise StateError(
                f"epoch {args.epoch_id} promotion evidence conflicts with its sealed commit"
            )
        candidate_ids = require_nonempty_ids(
            "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
        )
        candidates = [
            _converge_candidate_follower(
                client, candidate_id, args.epoch_id, "promotion_committing", now=now
            )
            for candidate_id in candidate_ids
        ]
        _converge_promoted_candidates(
            client,
            candidates,
            args.epoch_id,
            sealed["release_sha"],
            close_candidates=False,
        )
    updated = _cas_transition(
        client,
        args.epoch_id,
        args.state,
        now=now,
        evidence=evidence,
    )
    updated = converge_epoch(client, updated, now=now)
    return _mark_and_emit_transition(client, updated)


def read_projection(client: BeadClient, now: str, trunk_sha: str = "") -> dict[str, Any]:
    records = client.list_thunderdome()
    source_ids = sorted(
        {
            source_id
            for record in records
            if record_metadata(record).get(KIND) == "candidate"
            for source_id in record_metadata(record).get(PREFIX + "source_beads", [])
        }
    )
    source_states = {
        str(record["id"]): str(record.get("status", ""))
        for record in client.show(source_ids)
    }
    exact_trunk = require_sha("trunk_sha", trunk_sha) if trunk_sha else ""
    return project_state(
        records,
        now=now,
        source_states=source_states,
        trunk_sha=exact_trunk,
    )


def _dispatch_formula_digest(
    client: BeadClient, args: argparse.Namespace, metadata: Mapping[str, Any]
) -> str:
    candidate_ids = require_nonempty_ids(
        "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
    )
    result = client.run(
        [
            "formula",
            "show",
            "thunderdome-land",
            "--var",
            f"candidate_ids={','.join(candidate_ids)}",
            "--var",
            f"base_sha={metadata.get(PREFIX + 'base_sha', '')}",
            "--var",
            f"target_ref={metadata.get(PREFIX + 'target_ref', '')}",
            "--var",
            f"full_gate_command={args.full_gate_command}",
            "--json",
        ]
    )
    if not isinstance(result, Mapping):
        raise CommandError("gc formula show returned an invalid response")
    fingerprint = str(result.get("compiled_fingerprint", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise CommandError("gc formula show returned no compiled fingerprint")
    return fingerprint


def _dispatch_intent(
    client: BeadClient, args: argparse.Namespace, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    operator = args.operator if "/" in args.operator else f"{args.rig}/{args.operator}"
    return {
        "schema_version": "1",
        "formula": "thunderdome-land",
        "formula_digest": _dispatch_formula_digest(client, args, metadata),
        "operator": operator,
        "rig": str(args.rig),
        "full_gate_command": str(args.full_gate_command),
        "candidate_ids": require_nonempty_ids(
            "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
        ),
        "base_sha": require_sha(
            "base_sha", str(metadata.get(PREFIX + "base_sha", ""))
        ),
        "target_ref": require_ref(
            "target_ref", str(metadata.get(PREFIX + "target_ref", ""))
        ),
    }


def _seal_dispatch_intent(
    client: BeadClient, epoch_id: str, args: argparse.Namespace
) -> dict[str, Any]:
    for _ in range(16):
        epoch = client.authoritative_reread(epoch_id)
        metadata = record_metadata(epoch)
        intent = _dispatch_intent(client, args, metadata)
        existing = metadata.get(DISPATCH_INTENT)
        if existing is not None:
            if not isinstance(existing, Mapping) or canonical_json(existing) != canonical_json(intent):
                raise StateError(
                    f"epoch {epoch_id} dispatch identity differs from its sealed intent"
                )
            return epoch
        updated = dict(metadata)
        updated[DISPATCH_INTENT] = intent
        swapped = client.cas_envelope(epoch, updated)
        if swapped is not None:
            return swapped
    raise StateError(f"dispatch intent for epoch {epoch_id} did not converge")


def _dispatch_epoch(client: BeadClient, args: argparse.Namespace, epoch: Mapping[str, Any]) -> dict[str, Any]:
    if not args.rig:
        raise StateError("--rig is required to dispatch an epoch")
    if not args.full_gate_command:
        raise StateError("--full-gate-command is required to dispatch an epoch")
    epoch_id = str(epoch["id"])
    epoch = _seal_dispatch_intent(client, epoch_id, args)
    metadata = record_metadata(epoch)
    candidate_ids = require_nonempty_ids(
        "candidate_ids", metadata.get(PREFIX + "candidate_ids", [])
    )
    existing_workflow = str(metadata.get(PREFIX + "workflow_id", ""))
    if existing_workflow:
        workflow_id = existing_workflow
    else:
        operator = args.operator if "/" in args.operator else f"{args.rig}/{args.operator}"
        response = client.run(
            [
                "sling",
                operator,
                epoch_id,
                "--on",
                "thunderdome-land",
                "--var",
                f"candidate_ids={','.join(candidate_ids)}",
                "--var",
                f"base_sha={metadata.get(PREFIX + 'base_sha', '')}",
                "--var",
                f"target_ref={metadata.get(PREFIX + 'target_ref', '')}",
                "--var",
                f"full_gate_command={args.full_gate_command}",
                "--json",
            ],
            extra_env={
                "GC_EXPECTED_FORMULA_FINGERPRINT": str(
                    metadata[DISPATCH_INTENT]["formula_digest"]
                )
            },
        )
        if not isinstance(response, Mapping):
            raise CommandError("gc sling returned an invalid response")
        root = response.get("root")
        root_id = root.get("id") if isinstance(root, Mapping) else ""
        workflow_id = str(
            response.get("workflow_id")
            or response.get("root_id")
            or response.get("bead_id")
            or root_id
            or ""
        )
        if not workflow_id:
            raise CommandError("gc sling returned no workflow identifier")
        for _ in range(16):
            epoch = client.authoritative_reread(epoch_id)
            metadata = record_metadata(epoch)
            current_workflow = str(metadata.get(PREFIX + "workflow_id", ""))
            if current_workflow:
                if current_workflow != workflow_id:
                    raise StateError(
                        f"epoch {epoch_id} has conflicting workflow {current_workflow}"
                    )
                workflow_id = current_workflow
                break
            updated = dict(metadata)
            updated[PREFIX + "workflow_id"] = workflow_id
            if client.cas_envelope(epoch, updated) is not None:
                break
        else:
            raise StateError(f"workflow linkage for epoch {epoch_id} did not converge")
    return {
        "schema_version": "1",
        "ok": True,
        "action": "dispatched",
        "epoch_id": epoch_id,
        "workflow_id": workflow_id,
        "candidate_ids": candidate_ids,
    }


def _target_branch(target_ref: str) -> str:
    exact_ref = require_ref("target_ref", target_ref)
    prefix = "refs/heads/"
    if not exact_ref.startswith(prefix):
        raise StateError("candidate refresh requires a refs/heads/* target")
    return exact_ref[len(prefix) :]


def _refresh_formula_digest(
    client: BeadClient, args: argparse.Namespace, metadata: Mapping[str, Any]
) -> str:
    result = client.run(
        [
            "formula",
            "show",
            "thunderdome-build",
            "--var",
            f"target_ref={_target_branch(args.target_ref)}",
            "--var",
            f"aggregate_rust_gate_command={args.full_gate_command}",
            "--json",
        ]
    )
    if not isinstance(result, Mapping):
        raise CommandError("gc formula show returned an invalid response")
    fingerprint = str(result.get("compiled_fingerprint", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise CommandError("gc formula show returned no compiled fingerprint")
    return fingerprint


def _seal_refresh_intent(
    client: BeadClient,
    candidate_id: str,
    args: argparse.Namespace,
    *,
    trunk_sha: str,
) -> dict[str, Any]:
    for _ in range(16):
        candidate = client.authoritative_reread(candidate_id)
        metadata = record_metadata(candidate)
        existing = metadata.get(REFRESH_INTENT)
        if existing is not None:
            if not isinstance(existing, Mapping):
                raise StateError(
                    f"candidate {candidate_id} has an invalid refresh intent"
                )
            return candidate
        operator = args.operator if "/" in args.operator else f"{args.rig}/{args.operator}"
        intent = {
            "schema_version": "1",
            "formula": "thunderdome-build",
            "formula_digest": _refresh_formula_digest(client, args, metadata),
            "operator": operator,
            "rig": str(args.rig),
            "delivery_unit": _metadata_text(metadata, PREFIX + "delivery_unit"),
            "stale_candidate_id": candidate_id,
            "stale_base_sha": require_sha(
                "base_sha", _metadata_text(metadata, PREFIX + "base_sha")
            ),
            "refresh_base_sha": require_sha("trunk_sha", trunk_sha),
            "target_ref": require_ref("target_ref", args.target_ref),
            "full_gate_command": str(args.full_gate_command),
        }
        updated = dict(metadata)
        updated[REFRESH_INTENT] = intent
        swapped = client.cas_envelope(candidate, updated)
        if swapped is not None:
            return swapped
    raise StateError(f"refresh intent for candidate {candidate_id} did not converge")


def _dispatch_candidate_refresh(
    client: BeadClient,
    args: argparse.Namespace,
    candidate: Mapping[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    if not args.rig:
        raise StateError("--rig is required to refresh a stale candidate")
    if not args.full_gate_command:
        raise StateError("--full-gate-command is required to refresh a stale candidate")
    candidate_id = str(candidate["id"])
    candidate = _seal_refresh_intent(
        client, candidate_id, args, trunk_sha=args.trunk_sha
    )
    metadata = record_metadata(candidate)
    state = str(metadata.get(STATE, ""))
    if state == "queued":
        candidate = _cas_transition(
            client,
            candidate_id,
            "superseded",
            now=now,
            evidence={
                "evidence_ref": (
                    "stale-base:"
                    f"{metadata.get(PREFIX + 'base_sha', '')[:12]}"
                    f"->{args.trunk_sha[:12]}"
                )
            },
        )
        metadata = record_metadata(candidate)
    elif state not in {"superseded", "rejected", "verified"}:
        raise StateError(
            f"candidate {candidate_id} cannot refresh from state {state!r}"
        )
    _release_owned_reservations(
        client, candidate_id, metadata.get(PREFIX + "source_beads", [])
    )
    client.converge_status(
        candidate_id,
        "closed",
        reason=(
            "Thunderdome candidate superseded after trunk advanced"
            if state == "superseded"
            else "Thunderdome candidate rebuild follows failed epoch"
        ),
    )
    existing_workflow = _metadata_text(metadata, REFRESH_WORKFLOW_ID)
    if existing_workflow:
        workflow_id = existing_workflow
    else:
        intent = metadata.get(REFRESH_INTENT)
        if not isinstance(intent, Mapping):
            raise StateError(f"candidate {candidate_id} has no sealed refresh intent")
        response = client.run(
            [
                "sling",
                str(intent["operator"]),
                str(intent["delivery_unit"]),
                "--on",
                "thunderdome-build",
                "--var",
                f"target_ref={_target_branch(str(intent['target_ref']))}",
                "--var",
                f"aggregate_rust_gate_command={intent['full_gate_command']}",
                "--json",
            ],
            extra_env={
                "GC_EXPECTED_FORMULA_FINGERPRINT": str(intent["formula_digest"])
            },
        )
        if not isinstance(response, Mapping):
            raise CommandError("gc sling returned an invalid response")
        root = response.get("root")
        root_id = root.get("id") if isinstance(root, Mapping) else ""
        workflow_id = str(
            response.get("workflow_id")
            or response.get("root_id")
            or response.get("bead_id")
            or root_id
            or ""
        )
        if not workflow_id:
            raise CommandError("gc sling returned no workflow identifier")
        for _ in range(16):
            candidate = client.authoritative_reread(candidate_id)
            metadata = record_metadata(candidate)
            current_workflow = _metadata_text(metadata, REFRESH_WORKFLOW_ID)
            if current_workflow:
                if current_workflow != workflow_id:
                    raise StateError(
                        f"candidate {candidate_id} has conflicting refresh workflow "
                        f"{current_workflow}"
                    )
                workflow_id = current_workflow
                break
            updated = dict(metadata)
            updated[REFRESH_WORKFLOW_ID] = workflow_id
            if client.cas_envelope(candidate, updated) is not None:
                break
        else:
            raise StateError(
                f"refresh workflow linkage for candidate {candidate_id} did not converge"
            )
    return {
        "candidate_id": candidate_id,
        "delivery_unit": str(metadata.get(PREFIX + "delivery_unit", "")),
        "workflow_id": workflow_id,
    }


def _workflow_failure(
    client: BeadClient,
    workflow_id: str,
    *,
    now: str,
    timeout_seconds: int,
) -> dict[str, str] | None:
    if timeout_seconds < 1:
        raise StateError("workflow_timeout_seconds must be at least 1")
    try:
        roots = client.show([workflow_id])
    except CommandError:
        roots = []
    if not roots:
        return {"failure_class": "infrastructure", "reason": "missing"}
    root = roots[0]
    if str(root.get("status", "")) == "closed":
        return {"failure_class": "infrastructure", "reason": "closed-active"}
    descendants = (
        client.list_metadata("gc.root_bead_id", workflow_id)
        if hasattr(client, "list_metadata")
        else []
    )
    timestamps = [
        parsed
        for record in [root, *descendants]
        for parsed in (
            _parse_time(record.get("updated_at")),
            _parse_time(record.get("created_at")),
        )
        if parsed is not None
    ]
    current = _parse_time(now)
    if current is None:
        raise StateError("now must be an RFC3339 timestamp")
    if timestamps:
        age_seconds = max(
            0, int((current - max(timestamps)).total_seconds())
        )
        if age_seconds >= timeout_seconds:
            return {
                "failure_class": "timeout",
                "reason": f"stalled-{age_seconds}s",
            }
    return None


def unhealthy_active_epochs(
    client: BeadClient,
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
    timeout_seconds: int,
) -> list[dict[str, str]]:
    unhealthy: list[dict[str, str]] = []
    for record in records:
        metadata = record_metadata(record)
        if (
            metadata.get(KIND) != "epoch"
            or metadata.get(STATE) in TERMINAL_EPOCH_STATES
        ):
            continue
        workflow_id = _metadata_text(metadata, PREFIX + "workflow_id")
        if not workflow_id:
            continue
        failure = _workflow_failure(
            client,
            workflow_id,
            now=now,
            timeout_seconds=timeout_seconds,
        )
        if failure is not None:
            unhealthy.append(
                {
                    "epoch_id": str(record.get("id", "")),
                    "workflow_id": workflow_id,
                    **failure,
                }
            )
    return unhealthy


def recover_unhealthy_epochs(
    client: BeadClient,
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
    timeout_seconds: int,
) -> list[dict[str, str]]:
    unhealthy = unhealthy_active_epochs(
        client,
        records,
        now=now,
        timeout_seconds=timeout_seconds,
    )
    for item in unhealthy:
        epoch = _cas_transition(
            client,
            item["epoch_id"],
            "failed",
            now=now,
            evidence={
                "failure_class": item["failure_class"],
                "evidence_ref": (
                    f"workflow:{item['workflow_id']}:{item['reason']}"
                ),
            },
        )
        epoch = converge_epoch(client, epoch, now=now)
        _mark_and_emit_transition(client, epoch)
    return unhealthy


def recover_stale_candidates(
    client: BeadClient,
    args: argparse.Namespace,
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
) -> list[dict[str, Any]]:
    failed_epoch_ids = {
        str(record.get("id", ""))
        for record in records
        if record_metadata(record).get(KIND) == "epoch"
        and record_metadata(record).get(STATE) == "failed"
    }
    recoverable: list[Mapping[str, Any]] = []
    for record in records:
        metadata = record_metadata(record)
        if metadata.get(KIND) != "candidate":
            continue
        state = metadata.get(STATE)
        stale_queued = (
            state == "queued"
            and metadata.get(PREFIX + "base_sha") != args.trunk_sha
        )
        interrupted_refresh = (
            state in {"superseded", "rejected", "verified"}
            and isinstance(metadata.get(REFRESH_INTENT), Mapping)
            and not metadata.get(REFRESH_WORKFLOW_ID)
        )
        failed_epoch_retry = (
            state in {"rejected", "verified"}
            and metadata.get(PREFIX + "epoch_id") in failed_epoch_ids
            and not metadata.get(REFRESH_WORKFLOW_ID)
        )
        if stale_queued or interrupted_refresh or failed_epoch_retry:
            recoverable.append(record)
    return [
        _dispatch_candidate_refresh(client, args, candidate, now=now)
        for candidate in sorted(recoverable, key=lambda item: str(item.get("id", "")))
    ]


def terminal_cleanup_due(
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
    grace_seconds: int,
) -> list[Mapping[str, Any]]:
    if grace_seconds < 0:
        raise StateError("cleanup_grace_seconds must not be negative")
    due: list[Mapping[str, Any]] = []
    for record in records:
        metadata = record_metadata(record)
        if (
            metadata.get(KIND) != "epoch"
            or metadata.get(STATE) not in ABANDONED_EPOCH_STATES
            or metadata.get(CLEANUP_WORKFLOW_ID)
        ):
            continue
        age = _age_seconds(metadata.get(PREFIX + "updated_at"), now)
        if age is not None and age >= grace_seconds:
            due.append(record)
    return sorted(due, key=lambda item: str(item.get("id", "")))


def _cleanup_formula_digest(
    client: BeadClient, epoch_id: str
) -> str:
    result = client.run(
        [
            "formula",
            "show",
            "thunderdome-cleanup",
            "--var",
            f"epoch_id={epoch_id}",
            "--json",
        ]
    )
    if not isinstance(result, Mapping):
        raise CommandError("gc formula show returned an invalid response")
    fingerprint = str(result.get("compiled_fingerprint", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise CommandError("gc formula show returned no compiled fingerprint")
    return fingerprint


def _dispatch_terminal_cleanup(
    client: BeadClient,
    args: argparse.Namespace,
    epoch: Mapping[str, Any],
) -> dict[str, str]:
    if not args.rig:
        raise StateError("--rig is required to dispatch terminal cleanup")
    epoch_id = str(epoch["id"])
    for _ in range(16):
        epoch = client.authoritative_reread(epoch_id)
        metadata = record_metadata(epoch)
        existing = metadata.get(CLEANUP_INTENT)
        if existing is not None:
            if not isinstance(existing, Mapping):
                raise StateError(f"epoch {epoch_id} has an invalid cleanup intent")
            break
        operator = args.operator if "/" in args.operator else f"{args.rig}/{args.operator}"
        intent = {
            "schema_version": "1",
            "formula": "thunderdome-cleanup",
            "formula_digest": _cleanup_formula_digest(client, epoch_id),
            "operator": operator,
            "rig": str(args.rig),
            "epoch_id": epoch_id,
        }
        updated = dict(metadata)
        updated[CLEANUP_INTENT] = intent
        swapped = client.cas_envelope(epoch, updated)
        if swapped is not None:
            epoch = swapped
            metadata = record_metadata(epoch)
            break
    else:
        raise StateError(f"cleanup intent for epoch {epoch_id} did not converge")
    workflow_id = _metadata_text(metadata, CLEANUP_WORKFLOW_ID)
    if not workflow_id:
        intent = metadata.get(CLEANUP_INTENT)
        if not isinstance(intent, Mapping):
            raise StateError(f"epoch {epoch_id} has no sealed cleanup intent")
        response = client.run(
            [
                "sling",
                str(intent["operator"]),
                epoch_id,
                "--on",
                "thunderdome-cleanup",
                "--var",
                f"epoch_id={epoch_id}",
                "--json",
            ],
            extra_env={
                "GC_EXPECTED_FORMULA_FINGERPRINT": str(intent["formula_digest"])
            },
        )
        if not isinstance(response, Mapping):
            raise CommandError("gc sling returned an invalid response")
        root = response.get("root")
        root_id = root.get("id") if isinstance(root, Mapping) else ""
        workflow_id = str(
            response.get("workflow_id")
            or response.get("root_id")
            or response.get("bead_id")
            or root_id
            or ""
        )
        if not workflow_id:
            raise CommandError("gc sling returned no workflow identifier")
        for _ in range(16):
            epoch = client.authoritative_reread(epoch_id)
            metadata = record_metadata(epoch)
            current = _metadata_text(metadata, CLEANUP_WORKFLOW_ID)
            if current:
                if current != workflow_id:
                    raise StateError(
                        f"epoch {epoch_id} has conflicting cleanup workflow {current}"
                    )
                workflow_id = current
                break
            updated = dict(metadata)
            updated[CLEANUP_WORKFLOW_ID] = workflow_id
            if client.cas_envelope(epoch, updated) is not None:
                break
        else:
            raise StateError(
                f"cleanup workflow linkage for epoch {epoch_id} did not converge"
            )
    return {"epoch_id": epoch_id, "workflow_id": workflow_id}


def dispatch_deferred_cleanups(
    client: BeadClient,
    args: argparse.Namespace,
    records: Sequence[Mapping[str, Any]],
    *,
    now: str,
    grace_seconds: int,
) -> list[dict[str, str]]:
    return [
        _dispatch_terminal_cleanup(client, args, epoch)
        for epoch in terminal_cleanup_due(
            records, now=now, grace_seconds=grace_seconds
        )
    ]


def _repair_reasons(
    records: Sequence[Mapping[str, Any]],
    source_records: Mapping[str, Mapping[str, Any]],
    projection: Mapping[str, Any],
) -> list[str]:
    reasons = {
        f"projection:{item['code']}:{item['entity']}"
        for item in projection.get("violations", [])
    }
    by_id = {str(record.get("id", "")): record for record in records}
    controls = {
        str(record.get("id", "")): record
        for record in records
        if record_metadata(record).get(KIND) == "control"
    }
    for bead_id, record in by_id.items():
        metadata = record_metadata(record)
        kind = metadata.get(KIND)
        if kind in {"candidate", "epoch"}:
            if not authoritative_value(record):
                reasons.add(f"legacy-envelope:{bead_id}")
            raw = raw_metadata(record)
            for key, value in discovery_mirrors(metadata).items():
                if raw.get(key, "") != value:
                    reasons.add(f"mirror:{bead_id}:{key}")
        if kind == "candidate":
            state = metadata.get(STATE)
            if (
                state in ACTIVE_CANDIDATE_STATES
                and str(record.get("status", "")) == "closed"
            ):
                reasons.add(f"premature-close:{bead_id}")
            if (
                state in {"rejected", "superseded"}
                and str(record.get("status", "")) != "closed"
            ):
                reasons.add(f"terminal-status:{bead_id}")
            candidate_epoch = by_id.get(str(metadata.get(PREFIX + "epoch_id", "")))
            promoted = (
                candidate_epoch is not None
                and record_metadata(candidate_epoch).get(STATE) == "promoted"
            )
            abandoned = (
                candidate_epoch is not None
                and record_metadata(candidate_epoch).get(STATE)
                in ABANDONED_EPOCH_STATES
            )
            if abandoned and (
                state not in {"verified", "rejected", "superseded"}
                or str(record.get("status", "")) != "closed"
            ):
                reasons.add(f"terminal-follower:{bead_id}")
            if promoted and str(record.get("status", "")) != "closed":
                reasons.add(f"terminal-status:{bead_id}")
            for source_id in metadata.get(PREFIX + "source_beads", []):
                source = source_records.get(source_id)
                if source is None:
                    reasons.add(f"missing-source:{source_id}")
                    continue
                owner = raw_metadata(source).get(CANDIDATE_ID, "")
                if state in ACTIVE_CANDIDATE_STATES and owner != bead_id:
                    reasons.add(f"reservation:{source_id}:{bead_id}")
                if state in {"rejected", "superseded"} and owner == bead_id:
                    reasons.add(f"stale-reservation:{source_id}:{bead_id}")
                if promoted:
                    if (
                        str(source.get("status", "")) != "closed"
                        or raw_metadata(source).get(PROMOTED_BY, "") != str(
                            metadata.get(PREFIX + "epoch_id", "")
                        )
                        or owner == bead_id
                    ):
                        reasons.add(f"promotion-source:{source_id}")
        if kind == "epoch":
            state = str(metadata.get(STATE, ""))
            candidate_ids = metadata.get(PREFIX + "candidate_ids", [])
            if (
                state not in TERMINAL_EPOCH_STATES
                and str(record.get("status", "")) == "closed"
            ):
                reasons.add(f"premature-close:{bead_id}")
            if state in TERMINAL_EPOCH_STATES and str(record.get("status", "")) != "closed":
                reasons.add(f"terminal-status:{bead_id}")
            if state not in TERMINAL_EPOCH_STATES:
                control_id = control_record_id(candidate_ids)
                control = controls.get(control_id)
                if control is None:
                    reasons.add(f"missing-control:{control_id}")
                else:
                    active = _active_epoch_payload(control)
                    if not active:
                        reasons.add(f"missing-intent:{control_id}")
                    else:
                        try:
                            intent = parse_epoch_intent(active)
                        except StateError:
                            reasons.add(f"invalid-intent:{control_id}")
                        else:
                            if intent["epoch_id"] != bead_id:
                                reasons.add(f"intent-owner:{control_id}")
    for control_id, control in controls.items():
        active = _active_epoch_payload(control)
        if not active:
            continue
        try:
            intent = parse_epoch_intent(active)
        except StateError:
            reasons.add(f"invalid-intent:{control_id}")
            continue
        epoch = by_id.get(str(intent["epoch_id"]))
        if epoch is None:
            reasons.add(f"intent-epoch-missing:{intent['epoch_id']}")
        elif record_metadata(epoch).get(STATE) in TERMINAL_EPOCH_STATES:
            reasons.add(f"stale-intent:{intent['epoch_id']}")
    return sorted(reasons)


def repair_ledger(
    client: BeadClient, records: Sequence[Mapping[str, Any]], *, now: str
) -> list[dict[str, Any]]:
    for record in records:
        metadata = record_metadata(record)
        if metadata.get(KIND) in {"candidate", "epoch"}:
            client.authoritative_reread(str(record["id"]))
    records = client.list_thunderdome()
    by_id = {str(record.get("id", "")): record for record in records}
    for candidate_id, record in sorted(by_id.items()):
        metadata = record_metadata(record)
        state = metadata.get(STATE)
        if metadata.get(KIND) != "candidate" or state not in {
            "rejected",
            "superseded",
        }:
            continue
        _release_owned_reservations(
            client, candidate_id, metadata.get(PREFIX + "source_beads", [])
        )
        client.converge_status(
            candidate_id,
            "closed",
            reason=f"Thunderdome candidate converged in terminal state {state}",
        )
    records = client.list_thunderdome()
    by_id = {str(record.get("id", "")): record for record in records}

    for candidate_id, record in sorted(by_id.items()):
        metadata = record_metadata(record)
        if metadata.get(KIND) != "candidate":
            continue
        state = metadata.get(STATE)
        if (
            state in ACTIVE_CANDIDATE_STATES
            and str(record.get("status", "")) == "closed"
        ):
            raise StateError(
                f"candidate {candidate_id} is closed before terminal convergence"
            )
        sources = metadata.get(PREFIX + "source_beads", [])
        if state in {"rejected", "superseded"}:
            _release_owned_reservations(client, candidate_id, sources)
            client.converge_status(
                candidate_id,
                "closed",
                reason=f"Thunderdome candidate converged in terminal state {state}",
            )
            continue
        if state == "queued":
            try:
                _reserve_candidate_sources(client, candidate_id, now=now)
            except StateError:
                if record_metadata(client.authoritative_reread(candidate_id)).get(STATE) != "rejected":
                    raise
        elif state in {"frozen", "landed", "verified"}:
            epoch = by_id.get(str(metadata.get(PREFIX + "epoch_id", "")))
            if state == "verified" and epoch is not None:
                epoch_state = record_metadata(epoch).get(STATE)
                if epoch_state in TERMINAL_EPOCH_STATES:
                    if epoch_state in ABANDONED_EPOCH_STATES:
                        _release_owned_reservations(client, candidate_id, sources)
                    continue
            _ensure_epoch_source_reservations(
                client,
                candidate_id,
                metadata,
                str(metadata.get(PREFIX + "epoch_id", "")),
            )

    records = client.list_thunderdome()
    epochs = {
        str(record["id"]): record
        for record in records
        if record_metadata(record).get(KIND) == "epoch"
    }
    active_epochs = {
        epoch_id: record
        for epoch_id, record in epochs.items()
        if record_metadata(record).get(STATE) not in TERMINAL_EPOCH_STATES
    }
    prematurely_closed_epochs = sorted(
        epoch_id
        for epoch_id, record in active_epochs.items()
        if str(record.get("status", "")) == "closed"
    )
    if prematurely_closed_epochs:
        raise StateError(
            "active epochs are closed before terminal convergence: "
            + ", ".join(prematurely_closed_epochs)
        )
    if len(active_epochs) > 1:
        raise StateError(
            "multiple active epochs have conflicting ownership evidence: "
            + ", ".join(sorted(active_epochs))
        )

    control_records = [
        record for record in records if record_metadata(record).get(KIND) == "control"
    ]
    for control in control_records:
        active = _active_epoch_payload(control)
        if not active:
            continue
        intent = parse_epoch_intent(active)
        epoch_id = str(intent["epoch_id"])
        epoch = epochs.get(epoch_id)
        if epoch is None and active_epochs and epoch_id not in active_epochs:
            raise StateError(
                f"active intent for {epoch_id} conflicts with another active epoch"
            )
        if epoch is None:
            metadata = new_epoch_metadata(
                candidate_ids=intent["candidate_ids"],
                base_sha=str(intent["base_sha"]),
                target_ref=str(intent["target_ref"]),
                now=str(intent["created_at"]),
            )
            epoch = client.create_or_validate(
                epoch_id,
                f"Thunderdome epoch {str(intent['membership_hash'])[:12]}",
                "thunderdome-epoch",
                metadata,
            )
            epochs[epoch_id] = epoch
        elif (
            client._immutable_identity(record_metadata(epoch))
            != client._immutable_identity(
                new_epoch_metadata(
                    candidate_ids=intent["candidate_ids"],
                    base_sha=str(intent["base_sha"]),
                    target_ref=str(intent["target_ref"]),
                    now=str(intent["created_at"]),
                )
            )
        ):
            raise StateError(f"active intent for epoch {epoch_id} conflicts with epoch payload")
        if active_epochs and epoch_id not in active_epochs and record_metadata(epoch).get(STATE) not in TERMINAL_EPOCH_STATES:
            raise StateError(f"active intent for {epoch_id} conflicts with another active epoch")
        converge_epoch(client, epoch, now=now)

    records = client.list_thunderdome()
    active_epochs = {
        str(record["id"]): record
        for record in records
        if record_metadata(record).get(KIND) == "epoch"
        and record_metadata(record).get(STATE) not in TERMINAL_EPOCH_STATES
    }
    if len(active_epochs) == 1:
        epoch_id, epoch = next(iter(active_epochs.items()))
        metadata = record_metadata(epoch)
        control = _control_for(client, metadata.get(PREFIX + "candidate_ids", []))
        manifest = canonical_json(epoch_intent(epoch_id, metadata))
        active = _active_epoch_payload(control)
        if active == "":
            if not client.metadata_cas(str(control["id"]), ACTIVE_EPOCH, "", manifest):
                control = client.show([str(control["id"])])[0]
                active = _active_epoch_payload(control)
            else:
                active = manifest
        if active != manifest:
            intent = parse_epoch_intent(active)
            raise StateError(
                f"active epoch {intent['epoch_id']} conflicts with recoverable epoch {epoch_id}"
            )
        converge_epoch(client, epoch, now=now)

    records = client.list_thunderdome()
    for record in records:
        metadata = record_metadata(record)
        if metadata.get(KIND) != "epoch":
            continue
        if metadata.get(STATE) in TERMINAL_EPOCH_STATES:
            record = converge_epoch(client, record, now=now)
        _mark_and_emit_transition(client, record)
    return client.list_thunderdome()


def reconcile(client: BeadClient, args: argparse.Namespace) -> dict[str, Any]:
    now = args.now or utc_now()
    pending_ingress = (
        client.list_metadata(INGRESS_STATE, INGRESS_REVIEWED)
        if hasattr(client, "list_metadata")
        else []
    )
    ingressed_candidate_ids: list[str] = []
    if not args.dry_run:
        ingressed_candidate_ids = [
            str(candidate["id"])
            for candidate in (
                ingest_reviewed_candidate(client, workflow, now=now)
                for workflow in pending_ingress
            )
        ]
    cleanup_grace_seconds = int(
        getattr(args, "cleanup_grace_seconds", 3600)
    )
    records = client.list_thunderdome()
    workflow_timeout_seconds = int(
        getattr(args, "workflow_timeout_seconds", 7200)
    )
    unhealthy_epochs: list[dict[str, str]] = []
    refreshed_candidates: list[dict[str, Any]] = []
    if not args.dry_run:
        unhealthy_epochs = recover_unhealthy_epochs(
            client,
            records,
            now=now,
            timeout_seconds=workflow_timeout_seconds,
        )
        records = client.list_thunderdome()
        refreshed_candidates = recover_stale_candidates(
            client, args, records, now=now
        )
        records = client.list_thunderdome()
    source_ids = sorted(
        {
            source_id
            for record in records
            if record_metadata(record).get(KIND) == "candidate"
            for source_id in record_metadata(record).get(PREFIX + "source_beads", [])
        }
    )
    source_records = {
        str(record["id"]): record for record in client.show(source_ids)
    }
    source_states = {
        source_id: str(record.get("status", ""))
        for source_id, record in source_records.items()
    }
    if args.dry_run:
        projection = project_state(
            records,
            now=now,
            source_states=source_states,
            trunk_sha=args.trunk_sha,
        )
        repair_reasons = _repair_reasons(records, source_records, projection)
        plan = plan_reconcile(
            records,
            now=now,
            trunk_sha=args.trunk_sha,
            max_depth=args.max_depth,
            max_age_seconds=args.max_age_seconds,
        )
        ingress_workflow_ids = sorted(
            str(workflow.get("id", "")) for workflow in pending_ingress
        )
        unhealthy_epochs = unhealthy_active_epochs(
            client,
            records,
            now=now,
            timeout_seconds=workflow_timeout_seconds,
        )
        cleanup_epoch_ids = [
            str(epoch.get("id", ""))
            for epoch in terminal_cleanup_due(
                records,
                now=now,
                grace_seconds=cleanup_grace_seconds,
            )
        ]
        if repair_reasons:
            return {
                **plan,
                "ok": True,
                "action": "would_repair",
                "repair_reasons": repair_reasons,
            }
        if ingress_workflow_ids:
            return {
                **plan,
                "ok": True,
                "action": "would_ingest",
                "ingress_workflow_ids": ingress_workflow_ids,
            }
        if unhealthy_epochs:
            return {
                **plan,
                "ok": True,
                "action": "would_fail_workflow",
                "unhealthy_epochs": unhealthy_epochs,
            }
        if plan["stale_candidate_ids"]:
            return {
                **plan,
                "ok": True,
                "action": "would_refresh",
            }
        if cleanup_epoch_ids:
            return {
                **plan,
                "ok": True,
                "action": "would_cleanup",
                "cleanup_epoch_ids": cleanup_epoch_ids,
            }
        if plan["active_epoch_ids"]:
            active = {
                str(record["id"]): record
                for record in records
                if str(record.get("id", "")) in plan["active_epoch_ids"]
            }
            if len(active) == 1:
                metadata = record_metadata(next(iter(active.values())))
                if metadata.get(STATE) == "assembling" and not metadata.get(
                    PREFIX + "workflow_id"
                ):
                    return {**plan, "ok": True, "action": "would_resume"}
            return {**plan, "ok": True, "action": "none"}
        return {
            **plan,
            "ok": True,
            "action": "would_dispatch" if plan["due"] else "none",
        }

    records = repair_ledger(client, records, now=now)
    cleanup_dispatches = dispatch_deferred_cleanups(
        client,
        args,
        records,
        now=now,
        grace_seconds=cleanup_grace_seconds,
    )
    records = client.list_thunderdome()
    refresh_workflow_ids = sorted(
        str(item["workflow_id"]) for item in refreshed_candidates
    )
    recovered_epoch_ids = sorted(
        item["epoch_id"] for item in unhealthy_epochs
    )
    cleanup_workflow_ids = sorted(
        item["workflow_id"] for item in cleanup_dispatches
    )
    source_ids = sorted(
        {
            source_id
            for record in records
            if record_metadata(record).get(KIND) == "candidate"
            for source_id in record_metadata(record).get(PREFIX + "source_beads", [])
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
            metadata = record_metadata(epoch)
            if metadata.get(STATE) == "assembling" and not metadata.get(
                PREFIX + "workflow_id"
            ):
                return {
                    **_dispatch_epoch(client, args, epoch),
                    "ingressed_candidate_ids": ingressed_candidate_ids,
                    "refresh_workflow_ids": refresh_workflow_ids,
                    "cleanup_workflow_ids": cleanup_workflow_ids,
                }
        return {
            **plan,
            "ok": True,
            "action": (
                "recovered_workflow"
                if recovered_epoch_ids
                else "refreshed_stale"
                if refresh_workflow_ids
                else "ingested"
                if ingressed_candidate_ids
                else "cleanup_dispatched"
                if cleanup_workflow_ids
                else "none"
            ),
            "ingressed_candidate_ids": ingressed_candidate_ids,
            "refresh_workflow_ids": refresh_workflow_ids,
            "recovered_epoch_ids": recovered_epoch_ids,
            "cleanup_workflow_ids": cleanup_workflow_ids,
        }
    if not plan["due"]:
        return {
            **plan,
            "ok": True,
            "action": (
                "recovered_workflow"
                if recovered_epoch_ids
                else "refreshed_stale"
                if refresh_workflow_ids
                else "ingested"
                if ingressed_candidate_ids
                else "cleanup_dispatched"
                if cleanup_workflow_ids
                else "none"
            ),
            "ingressed_candidate_ids": ingressed_candidate_ids,
            "refresh_workflow_ids": refresh_workflow_ids,
            "recovered_epoch_ids": recovered_epoch_ids,
            "cleanup_workflow_ids": cleanup_workflow_ids,
        }
    epoch_args = argparse.Namespace(
        candidate=plan["candidate_ids"],
        base_sha=args.trunk_sha,
        target_ref=args.target_ref,
        now=args.now,
    )
    epoch = open_epoch(client, epoch_args)
    return {
        **_dispatch_epoch(client, args, epoch),
        "ingressed_candidate_ids": ingressed_candidate_ids,
        "refresh_workflow_ids": refresh_workflow_ids,
        "cleanup_workflow_ids": cleanup_workflow_ids,
    }


def migrate_legacy_records(client: BeadClient) -> dict[str, Any]:
    migrated: set[str] = set()
    for record in client.list_thunderdome():
        metadata = record_metadata(record)
        if metadata.get(KIND) not in {"candidate", "epoch"}:
            continue
        if authoritative_value(record):
            continue
        bead_id = str(record.get("id", ""))
        client.authoritative_reread(bead_id)
        migrated.add(bead_id)

    records = client.list_thunderdome()
    by_id = {str(record.get("id", "")): record for record in records}
    for epoch in records:
        epoch_metadata = record_metadata(epoch)
        if epoch_metadata.get(KIND) != "epoch" or epoch_metadata.get(STATE) != "promoted":
            continue
        epoch_id = str(epoch.get("id", ""))
        release_sha = str(epoch_metadata.get(PREFIX + "release_sha", ""))
        expected_close_reason = (
            f"Verified in Thunderdome epoch {epoch_id} at {release_sha}"
        )
        for candidate_id in epoch_metadata.get(PREFIX + "candidate_ids", []):
            candidate = by_id.get(str(candidate_id))
            if candidate is None:
                raise StateError(
                    f"promoted epoch {epoch_id} references missing candidate {candidate_id}"
                )
            candidate_metadata = record_metadata(candidate)
            if (
                candidate_metadata.get(STATE) != "verified"
                or candidate_metadata.get(PREFIX + "epoch_id") != epoch_id
            ):
                raise StateError(
                    f"promoted epoch {epoch_id} has inconsistent candidate {candidate_id}"
                )
            for source_id in candidate_metadata.get(PREFIX + "source_beads", []):
                sources = client.show([str(source_id)])
                if not sources:
                    raise StateError(f"promoted source bead {source_id} is missing")
                source = sources[0]
                provenance = raw_metadata(source).get(PROMOTED_BY, "")
                if provenance == epoch_id:
                    continue
                if (
                    provenance != ""
                    or str(source.get("status", "")) != "closed"
                    or str(source.get("close_reason", "")) != expected_close_reason
                ):
                    raise StateError(
                        f"source bead {source_id} cannot prove promotion by epoch {epoch_id}"
                    )
                if not client.metadata_cas(
                    str(source_id), PROMOTED_BY, "", epoch_id
                ):
                    sources = client.show([str(source_id)])
                    if not sources:
                        raise StateError(f"promoted source bead {source_id} is missing")
                    source = sources[0]
                    if raw_metadata(source).get(PROMOTED_BY, "") != epoch_id:
                        raise StateError(
                            f"source bead {source_id} promotion provenance changed concurrently"
                        )
                migrated.add(str(source_id))
    migrated_ids = sorted(migrated)
    return {
        "schema_version": "1",
        "ok": True,
        "action": "migrated",
        "migrated_ids": migrated_ids,
        "migrated_count": len(migrated_ids),
    }


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
    transition.add_argument(
        "state", choices=sorted(EPOCH_STATES - {"promotion_committing"})
    )
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

    migrate = subparsers.add_parser(
        "migrate", help="Migrate legacy records during an explicitly quiescent cutover"
    )
    migrate.add_argument("--json", action="store_true")

    reconcile_command = subparsers.add_parser(
        "reconcile", help="Dispatch one bounded epoch when queue depth or age is due"
    )
    reconcile_command.add_argument("--trunk-sha", required=True)
    reconcile_command.add_argument("--max-depth", type=int, default=8)
    reconcile_command.add_argument("--max-age-seconds", type=int, default=1800)
    reconcile_command.add_argument(
        "--workflow-timeout-seconds", type=int, default=7200
    )
    reconcile_command.add_argument(
        "--cleanup-grace-seconds", type=int, default=3600
    )
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
        elif args.command == "migrate":
            result = migrate_legacy_records(client)
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
