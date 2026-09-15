#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate delivery outcomes without inferring execution or user acceptance."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Literal

from workbench.safe_public_text import SafePublicTextError, validate_safe_public_text


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 1024 * 1024
MAX_TEXT_LENGTH = 4096
MAX_LIST_LENGTH = 128
MAX_TREE_DEPTH = 16
MAX_TREE_NODES = 4096

OutcomeStatus = Literal[
    "PLANNED",
    "IMPLEMENTED",
    "VERIFIED",
    "PARTIAL",
    "MISSING",
    "UNKNOWN",
    "NOT_APPLICABLE",
]
OutcomeState = Literal[
    "INCOMPLETE",
    "READY_FOR_GUIDED_UAT",
    "DELIVERY_VERIFIED",
    "USER_ACCEPTED",
]

OUTCOME_STATUSES = frozenset(
    {
        "PLANNED",
        "IMPLEMENTED",
        "VERIFIED",
        "PARTIAL",
        "MISSING",
        "UNKNOWN",
        "NOT_APPLICABLE",
    }
)
BLOCKING_STATUSES = frozenset(
    {"PLANNED", "IMPLEMENTED", "PARTIAL", "MISSING", "UNKNOWN"}
)
BASE_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "revision",
        "original_goal",
        "safe_goal_summary",
        "observable_outcomes",
        "capabilities",
        "non_goals",
        "evidence_requirements",
        "accepted_limitations",
    }
)
OUTCOME_FIELDS = BASE_OUTCOME_FIELDS | {"previous_goal_digest"}
OBSERVABLE_OUTCOME_FIELDS = frozenset(
    {"outcome_id", "description", "critical", "capability_ids"}
)
CAPABILITY_FIELDS = frozenset(
    {
        "capability_id",
        "description",
        "critical",
        "status",
        "evidence_requirement_ids",
        "evidence_references",
    }
)
EVIDENCE_REQUIREMENT_FIELDS = frozenset(
    {"evidence_id", "category", "description"}
)
FORBIDDEN_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "cookie",
        "authorization",
        "private_key",
        "remote_url",
    }
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_SAFE_GOAL_SUMMARY_LENGTH = 256


class OutcomeContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise OutcomeContractError(reason)


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("FIELD_TYPE_INVALID")
            if key.casefold() in FORBIDDEN_KEYS:
                _fail("SECRET_SHAPED_FIELD")
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


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("SCHEMA_FIELDS_CHANGED")
        result[key] = value
    return result


def _validate_identifier(value: object) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    if IDENTIFIER.fullmatch(value) is None:
        _fail("FIELD_VALUE_INVALID")
    return value


def _validate_text(value: object) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    if not value or len(value) > MAX_TEXT_LENGTH or "\x00" in value:
        _fail("FIELD_VALUE_INVALID")
    return value


def _validate_public_text(value: object) -> str:
    text = _validate_text(value)
    try:
        return validate_safe_public_text(text)
    except SafePublicTextError:
        _fail("SECRET_VALUE_REJECTED")


def _validate_safe_goal_summary(value: object, original_goal: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SAFE_GOAL_SUMMARY_LENGTH
        or "\n" in value
        or "\r" in value
        or value == original_goal
        or original_goal in value
    ):
        _fail("SAFE_GOAL_SUMMARY_REQUIRED")
    return _validate_public_text(value)


def _validate_text_list(value: object, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        _fail("FIELD_TYPE_INVALID")
    if (not allow_empty and not value) or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    result: list[str] = []
    for item in value:
        result.append(_validate_text(item))
    return result


def _validate_public_text_list(
    value: object, *, allow_empty: bool = True
) -> list[str]:
    return [
        _validate_public_text(item)
        for item in _validate_text_list(value, allow_empty=allow_empty)
    ]


def _validate_id_list(value: object, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        _fail("FIELD_TYPE_INVALID")
    if (not allow_empty and not value) or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    result = [_validate_identifier(item) for item in value]
    if len(set(result)) != len(result):
        _fail("FIELD_VALUE_INVALID")
    return result


def parse_outcome_json(value: str | bytes) -> dict[str, object]:
    if isinstance(value, bytes):
        if len(value) > MAX_JSON_BYTES:
            _fail("JSON_INVALID")
        try:
            text = value.decode("utf-8", "strict")
        except UnicodeDecodeError:
            _fail("JSON_INVALID")
    elif isinstance(value, str):
        try:
            encoded = value.encode("utf-8", "strict")
        except UnicodeEncodeError:
            _fail("JSON_INVALID")
        if len(encoded) > MAX_JSON_BYTES:
            _fail("JSON_INVALID")
        text = value
    else:
        _fail("FIELD_TYPE_INVALID")
    if text.startswith("\ufeff"):
        _fail("JSON_INVALID")
    try:
        parsed = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except OutcomeContractError:
        raise
    except (UnicodeError, ValueError, TypeError):
        _fail("JSON_INVALID")
    return validate_outcome_contract(parsed)


def _validate_outcomes(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            _fail("FIELD_TYPE_INVALID")
        if frozenset(item) != OBSERVABLE_OUTCOME_FIELDS:
            _fail("SCHEMA_FIELDS_CHANGED")
        if type(item.get("critical")) is not bool:
            _fail("FIELD_TYPE_INVALID")
        result.append(
            {
                "outcome_id": _validate_identifier(item.get("outcome_id")),
                "description": _validate_text(item.get("description")),
                "critical": item["critical"],
                "capability_ids": _validate_id_list(item.get("capability_ids")),
            }
        )
    return result


def _validate_capabilities(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            _fail("FIELD_TYPE_INVALID")
        if frozenset(item) != CAPABILITY_FIELDS:
            _fail("SCHEMA_FIELDS_CHANGED")
        if type(item.get("critical")) is not bool:
            _fail("FIELD_TYPE_INVALID")
        status = item.get("status")
        if not isinstance(status, str):
            _fail("FIELD_TYPE_INVALID")
        if status not in OUTCOME_STATUSES:
            _fail("FIELD_VALUE_INVALID")
        if item["critical"] and status == "NOT_APPLICABLE":
            _fail("FIELD_VALUE_INVALID")
        evidence_references = _validate_public_text_list(
            item.get("evidence_references")
        )
        result.append(
            {
                "capability_id": _validate_identifier(item.get("capability_id")),
                "description": _validate_text(item.get("description")),
                "critical": item["critical"],
                "status": status,
                "evidence_requirement_ids": _validate_id_list(
                    item.get("evidence_requirement_ids")
                ),
                "evidence_references": evidence_references,
            }
        )
    return result


def _validate_evidence_requirements(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            _fail("FIELD_TYPE_INVALID")
        if frozenset(item) != EVIDENCE_REQUIREMENT_FIELDS:
            _fail("SCHEMA_FIELDS_CHANGED")
        result.append(
            {
                "evidence_id": _validate_identifier(item.get("evidence_id")),
                "category": _validate_identifier(item.get("category")),
                "description": _validate_text(item.get("description")),
            }
        )
    return result


def _require_unique_ids(items: list[dict[str, object]], field: str) -> None:
    identifiers = [str(item[field]) for item in items]
    if len(set(identifiers)) != len(identifiers):
        _fail("FIELD_VALUE_INVALID")


def _validate_mappings(
    outcomes: list[dict[str, object]],
    capabilities: list[dict[str, object]],
    evidence_requirements: list[dict[str, object]],
) -> None:
    capability_by_id = {str(item["capability_id"]): item for item in capabilities}
    evidence_ids = {str(item["evidence_id"]) for item in evidence_requirements}
    referenced_capabilities: set[str] = set()
    referenced_evidence: set[str] = set()

    for outcome in outcomes:
        capability_ids = [str(item) for item in outcome["capability_ids"]]
        if any(item not in capability_by_id for item in capability_ids):
            _fail("OUTCOME_MAPPING_INCOMPLETE")
        referenced_capabilities.update(capability_ids)
        if outcome["critical"] and not any(
            capability_by_id[item]["critical"] is True for item in capability_ids
        ):
            _fail("OUTCOME_MAPPING_INCOMPLETE")

    if referenced_capabilities != set(capability_by_id):
        _fail("OUTCOME_MAPPING_INCOMPLETE")

    for capability in capabilities:
        requirement_ids = [str(item) for item in capability["evidence_requirement_ids"]]
        if any(item not in evidence_ids for item in requirement_ids):
            _fail("OUTCOME_MAPPING_INCOMPLETE")
        referenced_evidence.update(requirement_ids)
        if capability["status"] == "VERIFIED" and not capability["evidence_references"]:
            _fail("OUTCOME_MAPPING_INCOMPLETE")

    if referenced_evidence != evidence_ids:
        _fail("OUTCOME_MAPPING_INCOMPLETE")


def validate_outcome_contract(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    _validate_bounded_tree(value)
    _reject_forbidden_keys(value)

    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        _fail("GOAL_REVISION_INVALID")
    if "safe_goal_summary" not in value:
        _fail("SAFE_GOAL_SUMMARY_REQUIRED")
    expected_fields = BASE_OUTCOME_FIELDS if revision == 1 else OUTCOME_FIELDS
    if frozenset(value) != expected_fields:
        if "previous_goal_digest" in value or revision > 1:
            _fail("GOAL_REVISION_INVALID")
        _fail("SCHEMA_FIELDS_CHANGED")
    if revision > 1:
        previous_digest = value.get("previous_goal_digest")
        if not isinstance(previous_digest, str) or SHA256.fullmatch(previous_digest) is None:
            _fail("GOAL_REVISION_INVALID")

    _validate_identifier(value.get("task_id"))
    original_goal = _validate_text(value.get("original_goal"))
    _validate_safe_goal_summary(value.get("safe_goal_summary"), original_goal)
    outcomes = _validate_outcomes(value.get("observable_outcomes"))
    capabilities = _validate_capabilities(value.get("capabilities"))
    evidence_requirements = _validate_evidence_requirements(
        value.get("evidence_requirements")
    )
    _validate_text_list(value.get("non_goals"))
    _validate_text_list(value.get("accepted_limitations"))

    _require_unique_ids(outcomes, "outcome_id")
    _require_unique_ids(capabilities, "capability_id")
    _require_unique_ids(evidence_requirements, "evidence_id")
    _validate_mappings(outcomes, capabilities, evidence_requirements)
    return copy.deepcopy(value)


def canonical_outcome_digest(value: object) -> str:
    validated = validate_outcome_contract(value)
    encoded = json.dumps(
        validated,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_outcome_revision(
    previous: object, current: object
) -> dict[str, object]:
    previous_contract = validate_outcome_contract(previous)
    current_contract = validate_outcome_contract(current)
    if (
        current_contract["task_id"] != previous_contract["task_id"]
        or current_contract["revision"] != previous_contract["revision"] + 1
        or current_contract.get("previous_goal_digest")
        != canonical_outcome_digest(previous_contract)
    ):
        _fail("GOAL_REVISION_INVALID")
    return current_contract


def _blocking_outcomes(
    contract: dict[str, object], blocking_capability_ids: list[str]
) -> list[str]:
    blocking = set(blocking_capability_ids)
    outcomes = contract["observable_outcomes"]
    assert isinstance(outcomes, list)
    return sorted(
        str(item["outcome_id"])
        for item in outcomes
        if isinstance(item, dict)
        and item["critical"] is True
        and blocking.intersection(str(value) for value in item["capability_ids"])
    )


def _sorted_evidence_references(contract: dict[str, object]) -> list[str]:
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    return sorted(
        {
            str(reference)
            for capability in capabilities
            if isinstance(capability, dict)
            for reference in capability["evidence_references"]
        }
    )


def evaluate_outcome_contract(value: object) -> dict[str, object]:
    contract = validate_outcome_contract(value)
    capabilities = contract["capabilities"]
    assert isinstance(capabilities, list)
    blocking = sorted(
        str(item["capability_id"])
        for item in capabilities
        if isinstance(item, dict)
        and item["critical"] is True
        and item["status"] in BLOCKING_STATUSES
    )
    verified = not blocking
    original_goal = str(contract["original_goal"])
    return {
        "schema_version": 1,
        "task_id": contract["task_id"],
        "revision": contract["revision"],
        "state": "DELIVERY_VERIFIED" if verified else "INCOMPLETE",
        "blocking_outcome_ids": _blocking_outcomes(contract, blocking),
        "blocking_capability_ids": blocking,
        "final_uat_eligible": verified,
        "completion_eligible": False,
        "evidence_references": _sorted_evidence_references(contract),
        "goal_display": {
            "safe_summary": contract["safe_goal_summary"],
            "original_goal_sha256": hashlib.sha256(
                original_goal.encode("utf-8", "strict")
            ).hexdigest(),
            "original_text_state": "RETAINED_IN_OUTCOME_CONTRACT",
        },
        "write_authorized": False,
    }
