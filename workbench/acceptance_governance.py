#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Classify U0-U3 acceptance risk without impersonating the user."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from types import MappingProxyType
from typing import Literal


SCHEMA_VERSION = 1
MAX_TEXT_LENGTH = 4096
MAX_LIST_LENGTH = 128
MAX_TREE_DEPTH = 16
MAX_TREE_NODES = 4096

AcceptanceLevel = Literal["U0", "U1", "U2", "U3"]

LEVEL_RANK = MappingProxyType({"U0": 0, "U1": 1, "U2": 2, "U3": 3})
FACTOR_LEVELS = MappingProxyType(
    {
        "user_visibility": MappingProxyType(
            {"NONE": "U0", "LOW": "U1", "HIGH": "U2"}
        ),
        "business_impact": MappingProxyType(
            {"NONE": "U0", "LOW": "U1", "HIGH": "U2", "CRITICAL": "U2"}
        ),
        "data_effect": MappingProxyType(
            {
                "NONE": "U0",
                "READ_ONLY": "U1",
                "BEHAVIOR_CHANGE": "U2",
                "LOSS_RISK": "U3",
            }
        ),
        "data_sensitivity": MappingProxyType(
            {
                "NONE": "U0",
                "PUBLIC": "U1",
                "INTERNAL": "U2",
                "SENSITIVE": "U3",
                "UNKNOWN": "U3",
            }
        ),
        "external_effect": MappingProxyType(
            {
                "NONE": "U0",
                "ACCOUNT": "U3",
                "COST": "U3",
                "PUBLIC": "U3",
                "PRODUCTION": "U3",
            }
        ),
        "reversibility": MappingProxyType(
            {
                "IMMEDIATE": "U0",
                "TESTED": "U1",
                "UNVERIFIED": "U2",
                "IRREVERSIBLE": "U3",
            }
        ),
        "automated_coverage": MappingProxyType(
            {"COMPLETE": "U0", "PARTIAL": "U1", "NONE": "U2", "UNKNOWN": "U2"}
        ),
        "environment_gap": MappingProxyType(
            {"NONE": "U0", "LOW": "U1", "HIGH": "U2", "UNKNOWN": "U2"}
        ),
        "novelty": MappingProxyType({"KNOWN": "U0", "NEW": "U1", "UNKNOWN": "U2"}),
    }
)
CASE_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "outcome_decision",
        "factors",
        "safety_unknown",
        "waiver",
        "human_requirements",
    }
)
OUTCOME_DECISION_FIELDS = frozenset(
    {"state", "final_uat_eligible", "blocking_capability_ids", "critical_unknown"}
)
OUTCOME_STATES = frozenset({"INCOMPLETE", "DELIVERY_VERIFIED"})
WAIVER_FIELDS = frozenset({"reason", "evidence_references"})
HUMAN_REQUIREMENT_FIELDS = frozenset(
    {"requirement_id", "kind", "description", "action_request_digest"}
)
REQUIREMENT_KINDS = (
    "PRE_ACTION_APPROVAL",
    "POST_ACTION_OBSERVATION",
    "ROLLBACK_VERIFICATION",
)
USER_EVENT_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "task_id",
        "acceptance_case_digest",
        "requirement_id",
        "kind",
        "result",
        "source_event_id",
        "source_event_digest",
        "source_event_role",
        "action_request_digest",
        "action_result_digest",
    }
)
U3_ACTION_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "action_request_digest",
        "pre_action_receipt_digest",
        "state",
        "action_result_digest",
        "evidence_references",
    }
)
U3_ACTION_STATES = frozenset({"SUCCEEDED", "FAILED", "UNKNOWN"})
FORBIDDEN_KEYS = frozenset(
    {"token", "secret", "password", "cookie", "authorization", "private_key", "remote_url"}
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AcceptanceContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise AcceptanceContractError(reason)


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("FIELD_TYPE_INVALID")
            if key.casefold() in FORBIDDEN_KEYS:
                _fail("FIELD_VALUE_INVALID")
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)


def _validate_bounded_tree(value: object, *, depth: int = 0) -> None:
    stack = [(value, depth)]
    seen_container_ids: set[int] = set()
    total_nodes = 0
    while stack:
        item, item_depth = stack.pop()
        total_nodes += 1
        if total_nodes > MAX_TREE_NODES or item_depth > MAX_TREE_DEPTH:
            _fail("FIELD_VALUE_INVALID")
        if item is None or isinstance(item, (bool, int, float)):
            continue
        if isinstance(item, str):
            if not item or len(item) > MAX_TEXT_LENGTH or "\x00" in item:
                _fail("FIELD_VALUE_INVALID")
            try:
                item.encode("utf-8", "strict")
            except UnicodeEncodeError:
                _fail("FIELD_VALUE_INVALID")
            continue
        if isinstance(item, (list, dict)):
            if id(item) in seen_container_ids or len(item) > MAX_LIST_LENGTH:
                _fail("FIELD_VALUE_INVALID")
            seen_container_ids.add(id(item))
            if isinstance(item, dict):
                for key in item:
                    if (
                        not isinstance(key, str)
                        or not key
                        or len(key) > 128
                        or "\x00" in key
                    ):
                        _fail("FIELD_VALUE_INVALID")
                    try:
                        key.encode("utf-8", "strict")
                    except UnicodeEncodeError:
                        _fail("FIELD_VALUE_INVALID")
                children = item.values()
            else:
                children = item
            stack.extend((child, item_depth + 1) for child in children)
            continue
        _fail("FIELD_VALUE_INVALID")


def _text(value: object, *, identifier: bool = False) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    if not value or len(value) > MAX_TEXT_LENGTH or "\x00" in value:
        _fail("FIELD_VALUE_INVALID")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail("FIELD_VALUE_INVALID")
    if identifier and IDENTIFIER.fullmatch(value) is None:
        _fail("FIELD_VALUE_INVALID")
    return value


def _sha256(value: object) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    if SHA256.fullmatch(value) is None:
        _fail("FIELD_VALUE_INVALID")
    return value


def _canonical_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8", "strict")
    except (TypeError, UnicodeEncodeError, ValueError):
        _fail("FIELD_VALUE_INVALID")
    return hashlib.sha256(encoded).hexdigest()


def _string_list(
    value: object,
    *,
    allow_empty: bool = True,
    identifier: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        _fail("FIELD_TYPE_INVALID")
    if len(value) > MAX_LIST_LENGTH or (not allow_empty and not value):
        _fail("FIELD_VALUE_INVALID")
    result = [_text(item, identifier=identifier) for item in value]
    if len(set(result)) != len(result):
        _fail("FIELD_VALUE_INVALID")
    return result


def _validate_outcome_decision(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != OUTCOME_DECISION_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    state = value.get("state")
    if not isinstance(state, str):
        _fail("FIELD_TYPE_INVALID")
    if state not in OUTCOME_STATES:
        _fail("FIELD_VALUE_INVALID")
    if type(value.get("final_uat_eligible")) is not bool or type(
        value.get("critical_unknown")
    ) is not bool:
        _fail("FIELD_TYPE_INVALID")
    blockers = _string_list(value.get("blocking_capability_ids"), identifier=True)
    if value["final_uat_eligible"] is True:
        if state != "DELIVERY_VERIFIED" or blockers or value["critical_unknown"] is True:
            _fail("FIELD_VALUE_INVALID")
    elif state == "DELIVERY_VERIFIED":
        _fail("FIELD_VALUE_INVALID")
    if value["critical_unknown"] is True and not blockers:
        _fail("FIELD_VALUE_INVALID")
    return copy.deepcopy(value)


def _validate_waiver(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != WAIVER_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    _text(value.get("reason"))
    try:
        _string_list(value.get("evidence_references"), allow_empty=False)
    except AcceptanceContractError:
        _fail("WAIVER_EVIDENCE_REQUIRED")
    return copy.deepcopy(value)


def _validate_human_requirements(value: object) -> list[dict[str, object]]:
    try:
        if not isinstance(value, list) or len(value) > MAX_LIST_LENGTH:
            _fail("HUMAN_REQUIREMENTS_INVALID")
        validated: list[dict[str, object]] = []
        ids: set[str] = set()
        for item in value:
            if not isinstance(item, dict) or frozenset(item) != HUMAN_REQUIREMENT_FIELDS:
                _fail("HUMAN_REQUIREMENTS_INVALID")
            requirement_id = _text(item.get("requirement_id"), identifier=True)
            kind = item.get("kind")
            if not isinstance(kind, str) or kind not in REQUIREMENT_KINDS:
                _fail("HUMAN_REQUIREMENTS_INVALID")
            _text(item.get("description"))
            digest = item.get("action_request_digest")
            if digest is not None:
                _sha256(digest)
            if requirement_id in ids:
                _fail("HUMAN_REQUIREMENTS_INVALID")
            ids.add(requirement_id)
            validated.append(copy.deepcopy(item))
        return validated
    except AcceptanceContractError as exc:
        if exc.reason == "HUMAN_REQUIREMENTS_INVALID":
            raise
        _fail("HUMAN_REQUIREMENTS_INVALID")


def _compute_level(case: dict[str, object]) -> AcceptanceLevel:
    factors = case["factors"]
    assert isinstance(factors, dict)
    computed = max(
        (FACTOR_LEVELS[name][str(factors[name])] for name in FACTOR_LEVELS),
        key=LEVEL_RANK.__getitem__,
    )
    outcome = case["outcome_decision"]
    assert isinstance(outcome, dict)
    if outcome["critical_unknown"] is True and LEVEL_RANK[computed] < LEVEL_RANK["U2"]:
        computed = "U2"
    if case["safety_unknown"] is True:
        computed = "U3"
    return computed


def _validate_requirements_for_level(
    requirements: list[dict[str, object]], level: AcceptanceLevel
) -> None:
    kinds = [str(item["kind"]) for item in requirements]
    if level in {"U0", "U1"}:
        if requirements:
            _fail("HUMAN_REQUIREMENTS_INVALID")
        return
    if level == "U2":
        if not requirements or any(
            kind != "POST_ACTION_OBSERVATION" for kind in kinds
        ) or any(item["action_request_digest"] is not None for item in requirements):
            _fail("HUMAN_REQUIREMENTS_INVALID")
        return
    if (
        kinds.count("PRE_ACTION_APPROVAL") != 1
        or kinds.count("POST_ACTION_OBSERVATION") < 1
        or kinds.count("ROLLBACK_VERIFICATION") != 1
    ):
        _fail("HUMAN_REQUIREMENTS_INVALID")
    digests = {item["action_request_digest"] for item in requirements}
    if None in digests or len(digests) != 1:
        _fail("HUMAN_REQUIREMENTS_INVALID")


def _validated_case_and_levels(
    value: object, *, user_override: AcceptanceLevel | None = None
) -> tuple[dict[str, object], AcceptanceLevel, AcceptanceLevel]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    _validate_bounded_tree(value)
    _reject_forbidden_keys(value)
    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    if frozenset(value) != CASE_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    _text(value.get("task_id"), identifier=True)
    _validate_outcome_decision(value.get("outcome_decision"))
    factors = value.get("factors")
    if not isinstance(factors, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(factors) != frozenset(FACTOR_LEVELS):
        _fail("SCHEMA_FIELDS_CHANGED")
    for factor, mapping in FACTOR_LEVELS.items():
        factor_value = factors.get(factor)
        if not isinstance(factor_value, str):
            _fail("FIELD_TYPE_INVALID")
        if factor_value not in mapping:
            _fail("FIELD_VALUE_INVALID")
    if type(value.get("safety_unknown")) is not bool:
        _fail("FIELD_TYPE_INVALID")
    _validate_waiver(value.get("waiver"))
    requirements = _validate_human_requirements(value.get("human_requirements"))
    validated = copy.deepcopy(value)
    validated["human_requirements"] = requirements
    computed = _compute_level(validated)
    if user_override is not None:
        if not isinstance(user_override, str):
            _fail("FIELD_TYPE_INVALID")
        if user_override not in LEVEL_RANK:
            _fail("ACCEPTANCE_LEVEL_INVALID")
        if LEVEL_RANK[user_override] < LEVEL_RANK[computed]:
            _fail("USER_OVERRIDE_CANNOT_LOWER")
    effective = max((computed, user_override or computed), key=LEVEL_RANK.__getitem__)
    _validate_requirements_for_level(requirements, effective)
    return validated, computed, effective


def validate_acceptance_case(value: object) -> dict[str, object]:
    validated, _computed, _effective = _validated_case_and_levels(value)
    return validated


def _classification(
    case: dict[str, object], computed: AcceptanceLevel, level: AcceptanceLevel
) -> dict[str, object]:
    factors = case["factors"]
    assert isinstance(factors, dict)
    reasons = sorted(
        f"{name}:{factors[name]}->{FACTOR_LEVELS[name][str(factors[name])]}"
        for name in FACTOR_LEVELS
        if LEVEL_RANK[FACTOR_LEVELS[name][str(factors[name])]] > 0
    )
    outcome = case["outcome_decision"]
    assert isinstance(outcome, dict)
    if outcome["critical_unknown"] is True:
        reasons.append("critical_unknown->U2")
    if case["safety_unknown"] is True:
        reasons.append("safety_unknown->U3")
    if level != computed:
        reasons.append(f"user_override->{level}")
    mode = {
        "U0": "WAIVER_REQUIRED",
        "U1": "WAIVER_REQUIRED",
        "U2": "GUIDED_OBSERVATION_REQUIRED",
        "U3": "PRE_ACTION_AND_POST_ACTION_HUMAN_GATE",
    }[level]
    return {
        "schema_version": 1,
        "task_id": case["task_id"],
        "computed_level": computed,
        "level": level,
        "mode": mode,
        "human_required": level in {"U2", "U3"},
        "reasons": sorted(reasons),
        "outcome_ready": outcome["final_uat_eligible"],
        "write_authorized": False,
    }


def classify_acceptance(
    value: object, *, user_override: AcceptanceLevel | None = None
) -> dict[str, object]:
    case, computed, level = _validated_case_and_levels(
        value, user_override=user_override
    )
    return _classification(case, computed, level)


def canonical_acceptance_case_digest(
    value: object, *, user_override: AcceptanceLevel | None = None
) -> str:
    case, _computed, level = _validated_case_and_levels(
        value, user_override=user_override
    )
    return _canonical_digest({"case": case, "effective_level": level})


def canonical_user_event_source_digest(message: object) -> str:
    text = _text(message)
    return hashlib.sha256(text.encode("utf-8", "strict")).hexdigest()


def _validate_receipt_shape(value: object) -> dict[str, object]:
    try:
        if not isinstance(value, dict) or frozenset(value) != USER_EVENT_RECEIPT_FIELDS:
            _fail("USER_EVENT_RECEIPT_INVALID")
        version = value.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
            _fail("USER_EVENT_RECEIPT_INVALID")
        for field in ("receipt_id", "task_id", "requirement_id", "source_event_id"):
            _text(value.get(field), identifier=True)
        _sha256(value.get("acceptance_case_digest"))
        _sha256(value.get("source_event_digest"))
        kind = value.get("kind")
        if not isinstance(kind, str) or kind not in REQUIREMENT_KINDS:
            _fail("USER_EVENT_RECEIPT_INVALID")
        result = value.get("result")
        if not isinstance(result, str) or result not in {"PASS", "FAIL"}:
            _fail("USER_EVENT_RECEIPT_INVALID")
        if value.get("source_event_role") != "USER":
            _fail("USER_EVENT_RECEIPT_INVALID")
        for field in ("action_request_digest", "action_result_digest"):
            if value.get(field) is not None:
                _sha256(value[field])
        if kind == "PRE_ACTION_APPROVAL" and value.get("action_result_digest") is not None:
            _fail("USER_EVENT_RECEIPT_INVALID")
        return copy.deepcopy(value)
    except AcceptanceContractError as exc:
        if exc.reason == "USER_EVENT_RECEIPT_INVALID":
            raise
        _fail("USER_EVENT_RECEIPT_INVALID")


def canonical_user_event_receipt_digest(value: object) -> str:
    return _canonical_digest(_validate_receipt_shape(value))


def _validate_receipts(
    value: object | None,
    *,
    case: dict[str, object],
    case_digest: str,
    level: AcceptanceLevel,
) -> list[dict[str, object]]:
    if value is None:
        return []
    try:
        _validate_bounded_tree(value)
        _reject_forbidden_keys(value)
        if not isinstance(value, list) or len(value) > MAX_LIST_LENGTH:
            _fail("USER_EVENT_RECEIPT_INVALID")
        requirements = case["human_requirements"]
        assert isinstance(requirements, list)
        by_id = {str(item["requirement_id"]): item for item in requirements}
        validated: list[dict[str, object]] = []
        receipt_ids: set[str] = set()
        requirement_ids: set[str] = set()
        for raw in value:
            item = _validate_receipt_shape(raw)
            requirement = by_id.get(str(item["requirement_id"]))
            if requirement is None:
                _fail("USER_EVENT_RECEIPT_INVALID")
            if (
                item["task_id"] != case["task_id"]
                or item["acceptance_case_digest"] != case_digest
                or item["kind"] != requirement["kind"]
                or item["action_request_digest"]
                != requirement["action_request_digest"]
            ):
                _fail("USER_EVENT_RECEIPT_INVALID")
            if level == "U2" and (
                item["action_request_digest"] is not None
                or item["action_result_digest"] is not None
            ):
                _fail("USER_EVENT_RECEIPT_INVALID")
            if level == "U3" and item["kind"] != "PRE_ACTION_APPROVAL":
                if item["action_result_digest"] is None:
                    _fail("USER_EVENT_RECEIPT_INVALID")
            receipt_id = str(item["receipt_id"])
            requirement_id = str(item["requirement_id"])
            if receipt_id in receipt_ids or requirement_id in requirement_ids:
                _fail("USER_EVENT_RECEIPT_INVALID")
            receipt_ids.add(receipt_id)
            requirement_ids.add(requirement_id)
            validated.append(item)
        if level == "U3":
            stage_rank = {
                "PRE_ACTION_APPROVAL": 0,
                "POST_ACTION_OBSERVATION": 1,
                "ROLLBACK_VERIFICATION": 2,
            }
            observed_ranks = [stage_rank[str(item["kind"])] for item in validated]
            if observed_ranks != sorted(observed_ranks):
                _fail("USER_EVENT_RECEIPT_INVALID")
        return validated
    except AcceptanceContractError as exc:
        if exc.reason == "USER_EVENT_RECEIPT_INVALID":
            raise
        _fail("USER_EVENT_RECEIPT_INVALID")


def _validate_u3_action_result(
    value: object,
    *,
    case: dict[str, object],
    pre_receipt: dict[str, object],
) -> dict[str, object]:
    try:
        _validate_bounded_tree(value)
        _reject_forbidden_keys(value)
        if not isinstance(value, dict) or frozenset(value) != U3_ACTION_RESULT_FIELDS:
            _fail("U3_ACTION_RESULT_INVALID")
        version = value.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
            _fail("U3_ACTION_RESULT_INVALID")
        _text(value.get("task_id"), identifier=True)
        request_digest = _sha256(value.get("action_request_digest"))
        pre_digest = _sha256(value.get("pre_action_receipt_digest"))
        result_digest = _sha256(value.get("action_result_digest"))
        state = value.get("state")
        if not isinstance(state, str) or state not in U3_ACTION_STATES:
            _fail("U3_ACTION_RESULT_INVALID")
        references = _string_list(
            value.get("evidence_references"), allow_empty=False
        )
        canonical_references = sorted(references, key=lambda item: item.encode("utf-8"))
        validated = copy.deepcopy(value)
        validated["evidence_references"] = canonical_references
        requirements = case["human_requirements"]
        assert isinstance(requirements, list)
        expected_request = next(
            item["action_request_digest"]
            for item in requirements
            if item["kind"] == "PRE_ACTION_APPROVAL"
        )
        if (
            value["task_id"] != case["task_id"]
            or request_digest != expected_request
            or pre_digest != canonical_user_event_receipt_digest(pre_receipt)
        ):
            _fail("U3_ACTION_RESULT_INVALID")
        payload = {
            key: item
            for key, item in validated.items()
            if key != "action_result_digest"
        }
        if result_digest != _canonical_digest(payload):
            _fail("U3_ACTION_RESULT_INVALID")
        return validated
    except AcceptanceContractError as exc:
        if exc.reason == "U3_ACTION_RESULT_INVALID":
            raise
        _fail("U3_ACTION_RESULT_INVALID")


def _requirement_summary(
    requirements: list[dict[str, object]], receipts: list[dict[str, object]]
) -> dict[str, dict[str, int]]:
    return {
        kind: {
            "required": sum(item["kind"] == kind for item in requirements),
            "pass": sum(
                item["kind"] == kind and item["result"] == "PASS"
                for item in receipts
            ),
            "fail": sum(
                item["kind"] == kind and item["result"] == "FAIL"
                for item in receipts
            ),
        }
        for kind in REQUIREMENT_KINDS
    }


def _evaluation_result(
    classification: dict[str, object],
    *,
    requirements: list[dict[str, object]],
    receipts: list[dict[str, object]],
    accepted: bool,
    state: str,
    reason: str,
    action_state: str,
) -> dict[str, object]:
    return {
        **copy.deepcopy(classification),
        "accepted": accepted,
        "acceptance_state": state,
        "reason": reason,
        "requirement_summary": _requirement_summary(requirements, receipts),
        "action_state": action_state,
        "completion_eligible": accepted,
        "write_authorized": False,
    }


def evaluate_acceptance(
    value: object,
    receipts: object | None = None,
    *,
    user_override: AcceptanceLevel | None = None,
    u3_action_result: object | None = None,
) -> dict[str, object]:
    case, computed, level = _validated_case_and_levels(
        value, user_override=user_override
    )
    classification = _classification(case, computed, level)
    requirements = case["human_requirements"]
    outcome = case["outcome_decision"]
    assert isinstance(requirements, list) and isinstance(outcome, dict)
    digest = _canonical_digest({"case": case, "effective_level": level})
    observed = _validate_receipts(
        receipts,
        case=case,
        case_digest=digest,
        level=level,
    )
    default_action_state = "PENDING" if level == "U3" else "NOT_REQUIRED"
    if outcome["final_uat_eligible"] is not True:
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="BLOCKED_BY_OUTCOME",
            reason="CRITICAL_CAPABILITY_UNVERIFIED",
            action_state=default_action_state,
        )

    if level in {"U0", "U1"}:
        if observed:
            _fail("USER_EVENT_RECEIPT_INVALID")
        if case["waiver"] is None:
            return _evaluation_result(
                classification,
                requirements=requirements,
                receipts=observed,
                accepted=False,
                state="WAIVER_PENDING",
                reason="WAIVER_EVIDENCE_REQUIRED",
                action_state="NOT_REQUIRED",
            )
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=True,
            state="WAIVED_WITH_EVIDENCE",
            reason="ACCEPTED",
            action_state="NOT_REQUIRED",
        )

    if level == "U2":
        if u3_action_result is not None:
            _fail("U3_ACTION_RESULT_INVALID")
        if any(item["result"] == "FAIL" for item in observed):
            return _evaluation_result(
                classification,
                requirements=requirements,
                receipts=observed,
                accepted=False,
                state="USER_OBSERVATION_FAILED",
                reason="OBSERVATION_FAILED",
                action_state="NOT_REQUIRED",
            )
        if len(observed) != len(requirements):
            return _evaluation_result(
                classification,
                requirements=requirements,
                receipts=observed,
                accepted=False,
                state="USER_OBSERVATION_PENDING",
                reason="USER_EVENT_RECEIPT_REQUIRED",
                action_state="NOT_REQUIRED",
            )
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=True,
            state="USER_OBSERVED",
            reason="ACCEPTED",
            action_state="NOT_REQUIRED",
        )

    by_kind = {
        kind: [item for item in observed if item["kind"] == kind]
        for kind in REQUIREMENT_KINDS
    }
    pre = by_kind["PRE_ACTION_APPROVAL"]
    later = by_kind["POST_ACTION_OBSERVATION"] + by_kind["ROLLBACK_VERIFICATION"]
    if not pre:
        if later or u3_action_result is not None:
            _fail("USER_EVENT_RECEIPT_INVALID")
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_PRE_ACTION_PENDING",
            reason="U3_PRE_ACTION_REQUIRED",
            action_state="PENDING",
        )
    pre_receipt = pre[0]
    if pre_receipt["result"] == "FAIL":
        if later or u3_action_result is not None:
            _fail("USER_EVENT_RECEIPT_INVALID")
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_PRE_ACTION_FAILED",
            reason="U3_PRE_ACTION_FAILED",
            action_state="PENDING",
        )
    if u3_action_result is None:
        if later:
            _fail("USER_EVENT_RECEIPT_INVALID")
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_ACTION_PENDING",
            reason="U3_ACTION_RESULT_REQUIRED",
            action_state="PENDING",
        )

    action = _validate_u3_action_result(
        u3_action_result, case=case, pre_receipt=pre_receipt
    )
    action_state = str(action["state"])
    if action_state != "SUCCEEDED":
        if later:
            _fail("USER_EVENT_RECEIPT_INVALID")
        state = f"U3_ACTION_{action_state}"
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state=state,
            reason=state,
            action_state=action_state,
        )

    for item in later:
        if item["action_result_digest"] != action["action_result_digest"]:
            _fail("USER_EVENT_RECEIPT_INVALID")
    post = by_kind["POST_ACTION_OBSERVATION"]
    required_post = sum(
        item["kind"] == "POST_ACTION_OBSERVATION" for item in requirements
    )
    rollback = by_kind["ROLLBACK_VERIFICATION"]
    if rollback and (
        len(post) != required_post
        or any(item["result"] != "PASS" for item in post)
    ):
        _fail("USER_EVENT_RECEIPT_INVALID")
    if any(item["result"] == "FAIL" for item in post):
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_POST_ACTION_FAILED",
            reason="U3_POST_ACTION_FAILED",
            action_state="SUCCEEDED",
        )
    if len(post) != required_post:
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_POST_ACTION_PENDING",
            reason="USER_EVENT_RECEIPT_REQUIRED",
            action_state="SUCCEEDED",
        )
    if any(item["result"] == "FAIL" for item in rollback):
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_ROLLBACK_VERIFICATION_FAILED",
            reason="U3_ROLLBACK_VERIFICATION_FAILED",
            action_state="SUCCEEDED",
        )
    if not rollback:
        return _evaluation_result(
            classification,
            requirements=requirements,
            receipts=observed,
            accepted=False,
            state="U3_ROLLBACK_VERIFICATION_PENDING",
            reason="U3_ROLLBACK_VERIFICATION_REQUIRED",
            action_state="SUCCEEDED",
        )
    return _evaluation_result(
        classification,
        requirements=requirements,
        receipts=observed,
        accepted=True,
        state="USER_OBSERVED",
        reason="ACCEPTED",
        action_state="SUCCEEDED",
    )
