#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Track substantive strategy attempts and enforce route reassessment."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Literal


SCHEMA_VERSION = 1
MAX_TEXT_LENGTH = 4096
MAX_LIST_LENGTH = 128

StrategyState = Literal[
    "READY",
    "RETRY_ALLOWED",
    "RETRY_NOT_JUSTIFIED",
    "ROUTE_REASSESSMENT_REQUIRED",
    "EXTERNAL_STOP",
]

IDENTITY_FIELDS = (
    "goal_digest",
    "mechanism",
    "key_assumptions",
    "target_environment",
    "side_effect_class",
)
SURFACE_FIELDS = frozenset(
    {"route_id", "request_id", "temp_dir", "command", "output_format"}
)
ROUTE_REQUIRED_FIELDS = frozenset(IDENTITY_FIELDS)
STRATEGY_FIELDS = frozenset({"schema_version", "route", "attempts"})
ATTEMPT_FIELDS = frozenset(
    {
        "attempt_number",
        "outcome",
        "failure_summary",
        "evidence_references",
        "evidence_delta",
        "correction",
    }
)
ATTEMPT_OUTCOMES = frozenset({"FAILED", "SUCCEEDED", "UNKNOWN"})
LOCAL_SIDE_EFFECTS = frozenset({"LOCAL_READ", "LOCAL_REVERSIBLE"})
EXTERNAL_SIDE_EFFECTS = frozenset(
    {
        "NETWORK",
        "ACCOUNT",
        "CREDENTIAL",
        "PAID",
        "BUSINESS_WRITE",
        "PUSH",
        "PUBLISH",
        "DEPLOY",
        "DELETE",
        "PRODUCTION_MIGRATION",
    }
)
SIDE_EFFECT_CLASSES = LOCAL_SIDE_EFFECTS | EXTERNAL_SIDE_EFFECTS
COMPARISON_FIELDS = frozenset({"schema_version", "alternatives"})
ALTERNATIVE_FIELDS = frozenset(
    {
        "route",
        "expected_effect",
        "risk",
        "maintenance_cost",
        "evidence_gaps",
        "changes_user_outcome",
        "changes_data_model",
        "changes_cost",
        "changes_external_effect",
        "changes_maintenance_owner",
    }
)
CHANGE_FIELDS = (
    "changes_user_outcome",
    "changes_data_model",
    "changes_cost",
    "changes_external_effect",
    "changes_maintenance_owner",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StrategyContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise StrategyContractError(reason)


def _text(value: object) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    if not value or len(value) > MAX_TEXT_LENGTH or "\x00" in value:
        _fail("FIELD_VALUE_INVALID")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail("FIELD_VALUE_INVALID")
    return value


def _text_list(value: object, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        _fail("FIELD_TYPE_INVALID")
    if len(value) > MAX_LIST_LENGTH or (not allow_empty and not value):
        _fail("FIELD_VALUE_INVALID")
    result = [_text(item) for item in value]
    if len(set(result)) != len(result):
        _fail("FIELD_VALUE_INVALID")
    return result


def _route_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    fields = frozenset(value)
    if not ROUTE_REQUIRED_FIELDS.issubset(fields) or not fields.issubset(
        ROUTE_REQUIRED_FIELDS | SURFACE_FIELDS
    ):
        _fail("SCHEMA_FIELDS_CHANGED")
    goal_digest = value.get("goal_digest")
    if not isinstance(goal_digest, str):
        _fail("FIELD_TYPE_INVALID")
    if SHA256.fullmatch(goal_digest) is None:
        _fail("FIELD_VALUE_INVALID")
    mechanism = _text(value.get("mechanism"))
    assumptions = sorted(
        _text_list(value.get("key_assumptions"), allow_empty=False),
        key=lambda item: item.encode("utf-8"),
    )
    target_environment = _text(value.get("target_environment"))
    side_effect_class = value.get("side_effect_class")
    if not isinstance(side_effect_class, str):
        _fail("FIELD_TYPE_INVALID")
    if side_effect_class not in SIDE_EFFECT_CLASSES:
        _fail("FIELD_VALUE_INVALID")
    for field in SURFACE_FIELDS - {"route_id"}:
        if field in value:
            _text(value[field])
    return {
        "goal_digest": goal_digest,
        "mechanism": mechanism,
        "key_assumptions": assumptions,
        "target_environment": target_environment,
        "side_effect_class": side_effect_class,
    }


def _identity_digest(identity: dict[str, object]) -> str:
    try:
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail("FIELD_VALUE_INVALID")
    return hashlib.sha256(encoded).hexdigest()


def canonical_route_id(value: object) -> str:
    return _identity_digest(_route_identity(value))


def _validate_route(value: object) -> dict[str, object]:
    identity = _route_identity(value)
    assert isinstance(value, dict)
    if "route_id" in value:
        route_id = value["route_id"]
        if not isinstance(route_id, str) or SHA256.fullmatch(route_id) is None:
            _fail("ROUTE_ID_MISMATCH")
        if route_id != _identity_digest(identity):
            _fail("ROUTE_ID_MISMATCH")
    validated = copy.deepcopy(value)
    validated["key_assumptions"] = identity["key_assumptions"]
    return validated


def _validate_attempt(value: object, expected_number: int) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != ATTEMPT_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    number = value.get("attempt_number")
    if isinstance(number, bool) or not isinstance(number, int):
        _fail("FIELD_TYPE_INVALID")
    if number != expected_number:
        _fail("ATTEMPT_SEQUENCE_INVALID")
    outcome = value.get("outcome")
    if not isinstance(outcome, str):
        _fail("FIELD_TYPE_INVALID")
    if outcome not in ATTEMPT_OUTCOMES:
        _fail("FIELD_VALUE_INVALID")
    _text(value.get("failure_summary"))
    _text_list(value.get("evidence_references"), allow_empty=False)
    _text_list(value.get("evidence_delta"))
    _text(value.get("correction"))
    return copy.deepcopy(value)


def validate_strategy_history(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    if frozenset(value) != STRATEGY_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    route = _validate_route(value.get("route"))
    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        _fail("FIELD_TYPE_INVALID")
    if len(attempts) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    validated_attempts: list[dict[str, object]] = []
    terminal = False
    for expected, attempt in enumerate(attempts, start=1):
        if terminal:
            _fail("ATTEMPT_AFTER_TERMINAL")
        validated = _validate_attempt(attempt, expected)
        validated_attempts.append(validated)
        outcome = validated["outcome"]
        if outcome in {"SUCCEEDED", "UNKNOWN"}:
            terminal = True
        elif route["side_effect_class"] in EXTERNAL_SIDE_EFFECTS:
            terminal = True
        elif outcome == "FAILED" and not _latest_delta_is_new(validated_attempts):
            terminal = True
        elif sum(item["outcome"] == "FAILED" for item in validated_attempts) >= 4:
            terminal = True
    return {
        "schema_version": SCHEMA_VERSION,
        "route": route,
        "attempts": validated_attempts,
    }


def _latest_delta_is_new(attempts: list[dict[str, object]]) -> bool:
    latest = attempts[-1]
    delta = latest["evidence_delta"]
    assert isinstance(delta, list)
    if not delta:
        return False
    previous: set[str] = set()
    for attempt in attempts[:-1]:
        for field in ("evidence_references", "evidence_delta"):
            items = attempt[field]
            assert isinstance(items, list)
            previous.update(str(item) for item in items)
    return any(str(item) not in previous for item in delta)


def _decision(
    route_id: str,
    state: StrategyState,
    failure_count: int,
    *,
    next_attempt: bool,
    evidence_delta: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "route_id": route_id,
        "state": state,
        "failure_count": failure_count,
        "evidence_delta": sorted(evidence_delta),
        "next_attempt_allowed": next_attempt,
        "route_comparison_required": state == "ROUTE_REASSESSMENT_REQUIRED",
        "write_authorized": False,
    }


def evaluate_strategy(value: object) -> dict[str, object]:
    history = validate_strategy_history(value)
    route_value = history["route"]
    attempts = history["attempts"]
    assert isinstance(route_value, dict) and isinstance(attempts, list)
    route_id = canonical_route_id(route_value)
    failures = [item for item in attempts if item["outcome"] == "FAILED"]
    latest_delta = list(attempts[-1]["evidence_delta"]) if attempts else []
    side_effect_class = route_value["side_effect_class"]

    if attempts and side_effect_class in EXTERNAL_SIDE_EFFECTS:
        if len(attempts) == 1 and attempts[-1]["outcome"] == "SUCCEEDED":
            return _decision(
                route_id,
                "READY",
                0,
                next_attempt=False,
                evidence_delta=latest_delta,
            )
        return _decision(
            route_id,
            "EXTERNAL_STOP",
            len(failures),
            next_attempt=False,
            evidence_delta=latest_delta,
        )
    if any(item["outcome"] == "UNKNOWN" for item in attempts):
        return _decision(
            route_id,
            "RETRY_NOT_JUSTIFIED",
            len(failures),
            next_attempt=False,
            evidence_delta=latest_delta,
        )
    if attempts and attempts[-1]["outcome"] == "SUCCEEDED":
        return _decision(
            route_id,
            "READY",
            len(failures),
            next_attempt=False,
            evidence_delta=latest_delta,
        )
    if len(failures) >= 4:
        return _decision(
            route_id,
            "ROUTE_REASSESSMENT_REQUIRED",
            len(failures),
            next_attempt=False,
            evidence_delta=latest_delta,
        )
    if failures and not _latest_delta_is_new(attempts):
        return _decision(
            route_id,
            "RETRY_NOT_JUSTIFIED",
            len(failures),
            next_attempt=False,
            evidence_delta=latest_delta,
        )
    return _decision(
        route_id,
        "RETRY_ALLOWED" if failures else "READY",
        len(failures),
        next_attempt=True,
        evidence_delta=latest_delta,
    )


def _validate_alternative(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != ALTERNATIVE_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    _validate_route(value.get("route"))
    for field in ("expected_effect", "risk", "maintenance_cost"):
        _text(value.get(field))
    _text_list(value.get("evidence_gaps"))
    for field in CHANGE_FIELDS:
        if type(value.get(field)) is not bool:
            _fail("FIELD_TYPE_INVALID")
    return copy.deepcopy(value)


def compare_routes(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    if frozenset(value) != COMPARISON_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    alternatives = value.get("alternatives")
    if not isinstance(alternatives, list):
        _fail("FIELD_TYPE_INVALID")
    if len(alternatives) < 2 or len(alternatives) > MAX_LIST_LENGTH:
        _fail("ROUTE_REASSESSMENT_REQUIRED")
    validated = [_validate_alternative(item) for item in alternatives]
    route_ids = [canonical_route_id(item["route"]) for item in validated]
    mechanisms = [str(item["route"]["mechanism"]) for item in validated]
    if len(set(route_ids)) < 2 or len(set(mechanisms)) < 2:
        _fail("ROUTE_REASSESSMENT_REQUIRED")
    ordered = sorted(
        (
            {
                "route_id": route_id,
                "mechanism": item["route"]["mechanism"],
                "expected_effect": item["expected_effect"],
                "risk": item["risk"],
                "maintenance_cost": item["maintenance_cost"],
                "evidence_gaps": sorted(item["evidence_gaps"]),
                **{field: item[field] for field in CHANGE_FIELDS},
            }
            for route_id, item in zip(route_ids, validated, strict=True)
        ),
        key=lambda item: str(item["route_id"]),
    )
    return {
        "schema_version": 1,
        "state": "ROUTE_COMPARISON_COMPLETE",
        "route_ids": sorted(route_ids),
        "alternatives": ordered,
        "user_decision_required": any(
            item[field] is True for item in validated for field in CHANGE_FIELDS
        ),
        "write_authorized": False,
    }
