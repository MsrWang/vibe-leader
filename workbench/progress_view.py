#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build deterministic, layered Chinese delivery progress reports."""

from __future__ import annotations

import copy
import json
import re
from pathlib import PurePosixPath

from workbench.safe_public_text import (
    SafePublicTextError,
    escape_markdown_text,
    validate_safe_public_text,
)


SCHEMA_VERSION = 1
MAX_TEXT_LENGTH = 4096
MAX_LIST_LENGTH = 128
MAX_TREE_DEPTH = 16
MAX_TREE_NODES = 4096

PROGRESS_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "current_phase",
        "milestones",
        "user_visible_capabilities",
        "selected_strategy",
        "implementation_logic",
        "verification",
        "strategy_status",
        "alternative_routes",
        "user_participation",
        "next_step",
        "stop_conditions",
        "technical_appendix",
        "outcome_decision",
        "execution_decision",
        "acceptance_decision",
    }
)
REPORT_FIELDS = frozenset(
    (PROGRESS_FIELDS - {"outcome_decision", "execution_decision", "acceptance_decision"})
    | {
        "goal_display",
        "current_conclusion",
        "completion_basis",
        "completion_state",
        "write_authorized",
    }
)
GOAL_DISPLAY_FIELDS = frozenset(
    {"safe_summary", "original_goal_sha256", "original_text_state"}
)
MILESTONE_FIELDS = frozenset({"completed", "current", "remaining"})
CAPABILITY_FIELDS = frozenset(
    {"capability_id", "name", "environment", "status", "critical"}
)
SELECTED_STRATEGY_FIELDS = frozenset({"route_id", "mechanism", "selection_reason"})
VERIFICATION_FIELDS = frozenset({"verified", "warnings", "unverified", "unknown"})
STRATEGY_STATUS_FIELDS = frozenset(
    {"route_id", "failure_count", "evidence_delta", "state"}
)
ALTERNATIVE_ROUTE_FIELDS = frozenset({"route_id", "mechanism", "tradeoff"})
USER_PARTICIPATION_FIELDS = frozenset(
    {"required", "reason", "one_next_action", "expected_result", "side_effects", "failure_stop"}
)
TECHNICAL_APPENDIX_FIELDS = frozenset(
    {
        "project_path",
        "branch",
        "head",
        "dirty_fingerprint",
        "files",
        "check_ids",
        "test_counts",
        "reviewer_result",
        "digests",
        "commits",
        "rollback_reference",
    }
)
TEST_COUNT_FIELDS = frozenset({"total", "passed", "failed", "errors", "skipped"})
REVIEWER_RESULT_FIELDS = frozenset({"state", "critical", "high", "medium", "low"})
REVIEWER_STATES = frozenset(
    {
        "NOT_APPLICABLE",
        "PENDING",
        "PASS",
        "STOP_CRITICAL_HIGH",
        "CONTRACT_FAILURE",
        "ENVIRONMENT_UNSUPPORTED",
    }
)
OUTCOME_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "revision",
        "state",
        "blocking_outcome_ids",
        "blocking_capability_ids",
        "final_uat_eligible",
        "completion_eligible",
        "evidence_references",
        "goal_display",
        "write_authorized",
    }
)
EXECUTION_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "envelope_id",
        "task_id",
        "eligible",
        "reason",
        "drift_fields",
        "remaining_actions",
        "authority_created",
        "external_write_authorized",
        "write_authorized",
    }
)
ACCEPTANCE_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "computed_level",
        "level",
        "mode",
        "human_required",
        "reasons",
        "outcome_ready",
        "write_authorized",
        "accepted",
        "acceptance_state",
        "reason",
        "requirement_summary",
        "action_state",
        "completion_eligible",
    }
)
REQUIREMENT_KINDS = (
    "PRE_ACTION_APPROVAL",
    "POST_ACTION_OBSERVATION",
    "ROLLBACK_VERIFICATION",
)
REQUIREMENT_SUMMARY_FIELDS = frozenset(REQUIREMENT_KINDS)
REQUIREMENT_COUNT_FIELDS = frozenset({"required", "pass", "fail"})
ACTION_STATES = frozenset({"NOT_REQUIRED", "PENDING", "SUCCEEDED", "FAILED", "UNKNOWN"})
COMPLETION_BASIS_FIELDS = frozenset({"outcome", "execution", "acceptance"})
BASIS_OUTCOME_FIELDS = frozenset(
    {
        "task_id",
        "state",
        "blocking_outcome_ids",
        "blocking_capability_ids",
        "final_uat_eligible",
        "completion_eligible",
        "goal_display",
        "write_authorized",
    }
)
BASIS_EXECUTION_FIELDS = frozenset(
    {
        "envelope_id",
        "task_id",
        "eligible",
        "reason",
        "drift_fields",
        "remaining_actions",
        "authority_created",
        "external_write_authorized",
        "write_authorized",
    }
)
BASIS_ACCEPTANCE_FIELDS = frozenset(
    {
        "task_id",
        "computed_level",
        "level",
        "mode",
        "human_required",
        "outcome_ready",
        "accepted",
        "acceptance_state",
        "reason",
        "requirement_summary",
        "action_state",
        "completion_eligible",
        "write_authorized",
    }
)
COMPLETION_STATE_FIELDS = frozenset(
    {
        "task_state",
        "outcome_state",
        "execution_eligible",
        "strategy_state",
        "acceptance_state",
        "completion_eligible",
        "reason_codes",
    }
)
TASK_STATES = frozenset({"INCOMPLETE", "WAITING_USER", "COMPLETE"})
OUTCOME_STATUSES = frozenset(
    {"PLANNED", "IMPLEMENTED", "VERIFIED", "PARTIAL", "MISSING", "UNKNOWN", "NOT_APPLICABLE"}
)
BLOCKING_STATUSES = frozenset({"PLANNED", "IMPLEMENTED", "PARTIAL", "MISSING", "UNKNOWN"})
STRATEGY_STATES = frozenset(
    {"READY", "RETRY_ALLOWED", "RETRY_NOT_JUSTIFIED", "ROUTE_REASSESSMENT_REQUIRED", "EXTERNAL_STOP"}
)
ACCEPTANCE_LEVELS = frozenset({"U0", "U1", "U2", "U3"})
ACCEPTANCE_LEVEL_RANK = {"U0": 0, "U1": 1, "U2": 2, "U3": 3}
ACCEPTANCE_MODES = {
    "U0": "WAIVER_REQUIRED",
    "U1": "WAIVER_REQUIRED",
    "U2": "GUIDED_OBSERVATION_REQUIRED",
    "U3": "PRE_ACTION_AND_POST_ACTION_HUMAN_GATE",
}
ACCEPTANCE_STATES = frozenset(
    {
        "BLOCKED_BY_OUTCOME",
        "WAIVER_PENDING",
        "WAIVED_WITH_EVIDENCE",
        "USER_OBSERVATION_PENDING",
        "USER_OBSERVATION_FAILED",
        "U3_PRE_ACTION_PENDING",
        "U3_PRE_ACTION_FAILED",
        "U3_ACTION_PENDING",
        "U3_ACTION_FAILED",
        "U3_ACTION_UNKNOWN",
        "U3_POST_ACTION_PENDING",
        "U3_POST_ACTION_FAILED",
        "U3_ROLLBACK_VERIFICATION_PENDING",
        "U3_ROLLBACK_VERIFICATION_FAILED",
        "USER_OBSERVED",
    }
)
WAITING_USER_STATES = frozenset(
    {
        "USER_OBSERVATION_PENDING",
        "U3_PRE_ACTION_PENDING",
        "U3_POST_ACTION_PENDING",
        "U3_ROLLBACK_VERIFICATION_PENDING",
    }
)
TERMINAL_ACCEPTANCE_STATES = frozenset({"WAIVED_WITH_EVIDENCE", "USER_OBSERVED"})
COMPLETION_STRATEGY_STATE = "READY"
EXECUTION_REASONS = frozenset(
    {
        "STABLE_RESUME",
        "TASK_OR_ENVELOPE_CHANGED",
        "RESUME_ANCHOR_DRIFT",
        "ENVELOPE_INACTIVE",
    }
)
EXECUTION_LOCAL_ACTIONS = frozenset(
    {"READ", "EDIT", "TEST", "STATIC_CHECK", "DOC_UPDATE", "LOCAL_COMMIT", "REVIEW"}
)
EXECUTION_IDENTITY_DRIFT_FIELDS = frozenset(
    {"resume_event", "task_id", "envelope_id"}
)
EXECUTION_ANCHOR_DRIFT_FIELDS = frozenset(
    {
        "environment",
        "outcome_digest",
        "design_digest",
        "plan_digest",
        "allowlist_digest",
        "permission_scope_digest",
        "side_effect_scope_digest",
        "resume_state_digest",
        "authority_project.logical_path",
        "authority_project.physical_path",
        "authority_project.git_root",
        "authority_project.git_dir",
        "authority_project.git_common_dir",
        "authority_project.worktree_identity",
        "authority_project.branch",
        "authority_project.head",
        "authority_project.dirty_fingerprint",
    }
)
CONCLUSIONS = {
    "COMPLETE": "用户目标已完成并通过所需验收",
    "WAITING_USER": "技术前置条件已满足，正在等待所需用户操作或观察",
    "INCOMPLETE": "用户目标尚未完成",
}
FORBIDDEN_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "cookie",
        "authorization",
        "private_key",
        "remote_url",
        "env_value",
        "raw_diff",
        "untracked_content",
    }
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEAD = re.compile(r"^[0-9a-f]{40}$")
DIGEST_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*:[0-9a-f]{64}$")
ROLLBACK_REFERENCE = re.compile(r"^git-parent:[0-9a-f]{40}$")


class ProgressContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise ProgressContractError(reason)


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


def _reject_sensitive_or_writable(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("FIELD_TYPE_INVALID")
            if key.casefold() in FORBIDDEN_KEYS:
                _fail("SECRET_SHAPED_FIELD")
            if key == "write_authorized" and item is not False:
                _fail("PROGRESS_INPUT_INCONSISTENT")
            _reject_sensitive_or_writable(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_or_writable(item)


def _safe_display_text(value: object) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    if not value or len(value) > MAX_TEXT_LENGTH or "\x00" in value:
        _fail("FIELD_VALUE_INVALID")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail("FIELD_VALUE_INVALID")
    try:
        validate_safe_public_text(value)
    except SafePublicTextError:
        _fail("SECRET_VALUE_REJECTED")
    return value


def _validate_public_strings(value: object) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            _safe_display_text(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _safe_goal_summary(value: object) -> str:
    try:
        text = validate_safe_public_text(value)
    except SafePublicTextError:
        _fail("SECRET_VALUE_REJECTED")
    if len(text) > 256 or "\n" in text or "\r" in text:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    return text


def _text(value: object, *, identifier: bool = False) -> str:
    text = _safe_display_text(value)
    if identifier and IDENTIFIER.fullmatch(text) is None:
        _fail("FIELD_VALUE_INVALID")
    return text


def _string_list(
    value: object, *, allow_empty: bool = True, identifier: bool = False
) -> list[str]:
    if not isinstance(value, list):
        _fail("FIELD_TYPE_INVALID")
    if len(value) > MAX_LIST_LENGTH or (not allow_empty and not value):
        _fail("FIELD_VALUE_INVALID")
    result = [_text(item, identifier=identifier) for item in value]
    if len(set(result)) != len(result):
        _fail("FIELD_VALUE_INVALID")
    return result


def _exact_object(value: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != fields:
        _fail("SCHEMA_FIELDS_CHANGED")
    return value


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("FIELD_TYPE_INVALID")
    if value < 0 or value > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    return value


def _technical_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("FIELD_TYPE_INVALID")
    if value < 0:
        _fail("FIELD_VALUE_INVALID")
    return value


def _relative_path(value: object) -> str:
    text = _safe_display_text(value)
    path = PurePosixPath(text)
    if path.is_absolute() or text in {"", "."} or ".." in path.parts or str(path) != text:
        _fail("FIELD_VALUE_INVALID")
    return text


def _validate_milestones(value: object) -> dict[str, object]:
    item = _exact_object(value, MILESTONE_FIELDS)
    for field in MILESTONE_FIELDS:
        _string_list(item[field])
    return copy.deepcopy(item)


def _validate_capabilities(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_VALUE_INVALID")
    result: list[dict[str, object]] = []
    identifiers: set[str] = set()
    for raw in value:
        item = _exact_object(raw, CAPABILITY_FIELDS)
        capability_id = _text(item["capability_id"], identifier=True)
        _text(item["name"])
        _text(item["environment"])
        status = item["status"]
        if not isinstance(status, str):
            _fail("FIELD_TYPE_INVALID")
        if status not in OUTCOME_STATUSES:
            _fail("FIELD_VALUE_INVALID")
        if type(item["critical"]) is not bool:
            _fail("FIELD_TYPE_INVALID")
        if item["critical"] is True and status == "NOT_APPLICABLE":
            _fail("PROGRESS_INPUT_INCONSISTENT")
        if capability_id in identifiers:
            _fail("FIELD_VALUE_INVALID")
        identifiers.add(capability_id)
        result.append(copy.deepcopy(item))
    return result


def _validate_selected_strategy(value: object) -> dict[str, object]:
    item = _exact_object(value, SELECTED_STRATEGY_FIELDS)
    if not isinstance(item["route_id"], str) or SHA256.fullmatch(item["route_id"]) is None:
        _fail("FIELD_VALUE_INVALID")
    _text(item["mechanism"])
    _text(item["selection_reason"])
    return copy.deepcopy(item)


def _validate_verification(value: object) -> dict[str, object]:
    item = _exact_object(value, VERIFICATION_FIELDS)
    seen: set[str] = set()
    for field in VERIFICATION_FIELDS:
        values = _string_list(item[field])
        if seen.intersection(values):
            _fail("PROGRESS_INPUT_INCONSISTENT")
        seen.update(values)
    return copy.deepcopy(item)


def _validate_strategy_status(value: object) -> dict[str, object]:
    item = _exact_object(value, STRATEGY_STATUS_FIELDS)
    if not isinstance(item["route_id"], str) or SHA256.fullmatch(item["route_id"]) is None:
        _fail("FIELD_VALUE_INVALID")
    _nonnegative_int(item["failure_count"])
    _string_list(item["evidence_delta"])
    state = item["state"]
    if not isinstance(state, str):
        _fail("FIELD_TYPE_INVALID")
    if state not in STRATEGY_STATES:
        _fail("FIELD_VALUE_INVALID")
    return copy.deepcopy(item)


def _validate_alternatives(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_LIST_LENGTH:
        _fail("FIELD_TYPE_INVALID")
    result: list[dict[str, object]] = []
    route_ids: set[str] = set()
    for raw in value:
        item = _exact_object(raw, ALTERNATIVE_ROUTE_FIELDS)
        route_id = item["route_id"]
        if not isinstance(route_id, str) or SHA256.fullmatch(route_id) is None:
            _fail("FIELD_VALUE_INVALID")
        _text(item["mechanism"])
        _text(item["tradeoff"])
        if route_id in route_ids:
            _fail("FIELD_VALUE_INVALID")
        route_ids.add(route_id)
        result.append(copy.deepcopy(item))
    return result


def _validate_user_participation(value: object) -> dict[str, object]:
    item = _exact_object(value, USER_PARTICIPATION_FIELDS)
    if type(item["required"]) is not bool:
        _fail("FIELD_TYPE_INVALID")
    for field in USER_PARTICIPATION_FIELDS - {"required"}:
        _text(item[field])
    return copy.deepcopy(item)


def _validate_technical_appendix(value: object) -> dict[str, object]:
    item = _exact_object(value, TECHNICAL_APPENDIX_FIELDS)
    path_text = _safe_display_text(item["project_path"])
    path = PurePosixPath(path_text)
    if not path.is_absolute() or path_text == "/" or str(path) != path_text:
        _fail("FIELD_VALUE_INVALID")
    _text(item["branch"])
    if not isinstance(item["head"], str) or HEAD.fullmatch(item["head"]) is None:
        _fail("FIELD_VALUE_INVALID")
    if not isinstance(item["dirty_fingerprint"], str) or SHA256.fullmatch(item["dirty_fingerprint"]) is None:
        _fail("FIELD_VALUE_INVALID")
    files = item["files"]
    if not isinstance(files, list) or len(files) > MAX_LIST_LENGTH:
        _fail("FIELD_TYPE_INVALID")
    validated_files = [_relative_path(file) for file in files]
    if len(set(validated_files)) != len(validated_files):
        _fail("FIELD_VALUE_INVALID")
    _string_list(item["check_ids"], identifier=True)
    counts = _exact_object(item["test_counts"], TEST_COUNT_FIELDS)
    values = {
        field: _technical_nonnegative_int(counts[field])
        for field in TEST_COUNT_FIELDS
    }
    if values["total"] != sum(values[field] for field in ("passed", "failed", "errors", "skipped")):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    reviewer = _exact_object(item["reviewer_result"], REVIEWER_RESULT_FIELDS)
    reviewer_state = reviewer["state"]
    if not isinstance(reviewer_state, str):
        _fail("FIELD_TYPE_INVALID")
    if reviewer_state not in REVIEWER_STATES:
        _fail("FIELD_VALUE_INVALID")
    reviewer_counts = {
        field: _technical_nonnegative_int(reviewer[field])
        for field in REVIEWER_RESULT_FIELDS - {"state"}
    }
    if reviewer_state in {"PASS", "NOT_APPLICABLE"} and (
        reviewer_counts["critical"] or reviewer_counts["high"]
    ):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if reviewer_state == "NOT_APPLICABLE" and any(reviewer_counts.values()):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if reviewer_state == "STOP_CRITICAL_HIGH" and not (
        reviewer_counts["critical"] or reviewer_counts["high"]
    ):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    digests = _string_list(item["digests"])
    if any(DIGEST_REFERENCE.fullmatch(digest) is None for digest in digests):
        _fail("FIELD_VALUE_INVALID")
    commits = _string_list(item["commits"])
    if any(HEAD.fullmatch(commit) is None for commit in commits):
        _fail("FIELD_VALUE_INVALID")
    rollback = _safe_display_text(item["rollback_reference"])
    if ROLLBACK_REFERENCE.fullmatch(rollback) is None:
        _fail("FIELD_VALUE_INVALID")
    validated = copy.deepcopy(item)
    validated["files"] = validated_files
    return validated


def _validate_outcome_projection(value: object) -> dict[str, object]:
    item = _exact_object(value, BASIS_OUTCOME_FIELDS)
    _text(item["task_id"], identifier=True)
    state = item["state"]
    if not isinstance(state, str):
        _fail("FIELD_TYPE_INVALID")
    if state not in {"INCOMPLETE", "DELIVERY_VERIFIED"}:
        _fail("FIELD_VALUE_INVALID")
    blockers_o = _string_list(item["blocking_outcome_ids"], identifier=True)
    blockers_c = _string_list(item["blocking_capability_ids"], identifier=True)
    for field in ("final_uat_eligible", "completion_eligible", "write_authorized"):
        if type(item[field]) is not bool:
            _fail("FIELD_TYPE_INVALID")
    if item["write_authorized"] is not False or item["completion_eligible"] is not False:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if state == "DELIVERY_VERIFIED":
        if blockers_o or blockers_c or item["final_uat_eligible"] is not True:
            _fail("PROGRESS_INPUT_INCONSISTENT")
    elif not blockers_c or item["final_uat_eligible"] is not False:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    result = copy.deepcopy(item)
    result["goal_display"] = _validate_goal_display(item["goal_display"])
    return result


def _validate_goal_display(value: object) -> dict[str, object]:
    item = _exact_object(value, GOAL_DISPLAY_FIELDS)
    summary = _safe_goal_summary(item["safe_summary"])
    digest = item["original_goal_sha256"]
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        _fail("FIELD_VALUE_INVALID")
    if item["original_text_state"] != "RETAINED_IN_OUTCOME_CONTRACT":
        _fail("PROGRESS_INPUT_INCONSISTENT")
    return {
        "safe_summary": summary,
        "original_goal_sha256": digest,
        "original_text_state": "RETAINED_IN_OUTCOME_CONTRACT",
    }


def _validate_outcome_decision(value: object) -> dict[str, object]:
    item = _exact_object(value, OUTCOME_DECISION_FIELDS)
    if item["schema_version"] != 1 or isinstance(item["schema_version"], bool):
        _fail("FIELD_VALUE_INVALID")
    revision = item["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        _fail("FIELD_TYPE_INVALID")
    _string_list(item["evidence_references"])
    projected = {field: copy.deepcopy(item[field]) for field in BASIS_OUTCOME_FIELDS}
    return _validate_outcome_projection(projected)


def _validate_execution_projection(value: object) -> dict[str, object]:
    item = _exact_object(value, BASIS_EXECUTION_FIELDS)
    _text(item["envelope_id"], identifier=True)
    _text(item["task_id"], identifier=True)
    for field in ("eligible", "authority_created", "external_write_authorized", "write_authorized"):
        if type(item[field]) is not bool:
            _fail("FIELD_TYPE_INVALID")
    reason = item["reason"]
    if not isinstance(reason, str) or reason not in EXECUTION_REASONS:
        _fail("EXECUTION_DECISION_INCONSISTENT")
    drift = item["drift_fields"]
    remaining = item["remaining_actions"]
    if not isinstance(drift, list) or not isinstance(remaining, list):
        _fail("EXECUTION_DECISION_INCONSISTENT")
    if (
        len(drift) > MAX_LIST_LENGTH
        or len(remaining) > MAX_LIST_LENGTH
        or any(not isinstance(value, str) for value in drift + remaining)
        or len(set(drift)) != len(drift)
        or len(set(remaining)) != len(remaining)
        or drift != sorted(drift, key=lambda value: value.encode("utf-8"))
        or remaining != sorted(remaining, key=lambda value: value.encode("utf-8"))
        or any(value not in EXECUTION_LOCAL_ACTIONS for value in remaining)
    ):
        _fail("EXECUTION_DECISION_INCONSISTENT")
    if any(
        item[field] is not False
        for field in ("authority_created", "external_write_authorized", "write_authorized")
    ):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if reason == "STABLE_RESUME":
        valid_row = item["eligible"] is True and not drift
    elif reason == "TASK_OR_ENVELOPE_CHANGED":
        valid_row = (
            item["eligible"] is False
            and bool(drift)
            and set(drift).issubset(EXECUTION_IDENTITY_DRIFT_FIELDS)
            and not remaining
        )
    elif reason == "RESUME_ANCHOR_DRIFT":
        valid_row = (
            item["eligible"] is False
            and bool(drift)
            and set(drift).issubset(EXECUTION_ANCHOR_DRIFT_FIELDS)
            and not remaining
        )
    else:
        valid_row = item["eligible"] is False and not drift and not remaining
    if not valid_row:
        _fail("EXECUTION_DECISION_INCONSISTENT")
    return copy.deepcopy(item)


def _validate_execution_decision(value: object) -> dict[str, object]:
    item = _exact_object(value, EXECUTION_DECISION_FIELDS)
    if item["schema_version"] != 1 or isinstance(item["schema_version"], bool):
        _fail("FIELD_VALUE_INVALID")
    projected = {field: copy.deepcopy(item[field]) for field in BASIS_EXECUTION_FIELDS}
    return _validate_execution_projection(projected)


def _validate_requirement_summary(value: object) -> dict[str, object]:
    summary = _exact_object(value, REQUIREMENT_SUMMARY_FIELDS)
    result: dict[str, object] = {}
    for kind in REQUIREMENT_KINDS:
        counts = _exact_object(summary[kind], REQUIREMENT_COUNT_FIELDS)
        validated = {
            field: _nonnegative_int(counts[field]) for field in REQUIREMENT_COUNT_FIELDS
        }
        if validated["pass"] + validated["fail"] > validated["required"]:
            _fail("PROGRESS_INPUT_INCONSISTENT")
        result[kind] = validated
    return result


def _require_acceptance_semantics(item: dict[str, object]) -> None:
    computed_level = str(item["computed_level"])
    level = str(item["level"])
    if ACCEPTANCE_LEVEL_RANK[level] < ACCEPTANCE_LEVEL_RANK[computed_level]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    human_required = level in {"U2", "U3"}
    if item["mode"] != ACCEPTANCE_MODES[level] or item["human_required"] is not human_required:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if item["accepted"] is not item["completion_eligible"]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    summary = item["requirement_summary"]
    assert isinstance(summary, dict)
    pre = summary["PRE_ACTION_APPROVAL"]
    post = summary["POST_ACTION_OBSERVATION"]
    rollback = summary["ROLLBACK_VERIFICATION"]
    assert all(isinstance(counts, dict) for counts in (pre, post, rollback))
    state = str(item["acceptance_state"])
    reason = str(item["reason"])
    action_state = str(item["action_state"])
    accepted = item["accepted"] is True

    if level in {"U0", "U1"}:
        if any(any(counts.values()) for counts in (pre, post, rollback)) or action_state != "NOT_REQUIRED":
            _fail("PROGRESS_INPUT_INCONSISTENT")
    elif level == "U2":
        if any(pre.values()) or any(rollback.values()) or post["required"] < 1 or action_state != "NOT_REQUIRED":
            _fail("PROGRESS_INPUT_INCONSISTENT")
    else:
        if pre["required"] != 1 or post["required"] < 1 or rollback["required"] != 1:
            _fail("PROGRESS_INPUT_INCONSISTENT")
        post_complete = (
            post["pass"] == post["required"] and post["fail"] == 0
        )
        if (rollback["pass"] or rollback["fail"]) and not post_complete:
            _fail("PROGRESS_INPUT_INCONSISTENT")

    if item["outcome_ready"] is not True:
        if accepted or state != "BLOCKED_BY_OUTCOME" or reason != "CRITICAL_CAPABILITY_UNVERIFIED":
            _fail("PROGRESS_INPUT_INCONSISTENT")
        if any(counts["pass"] or counts["fail"] for counts in (pre, post, rollback)):
            _fail("PROGRESS_INPUT_INCONSISTENT")
        if action_state != ("PENDING" if level == "U3" else "NOT_REQUIRED"):
            _fail("PROGRESS_INPUT_INCONSISTENT")
        return

    if level in {"U0", "U1"}:
        expected = (
            ("WAIVED_WITH_EVIDENCE", "ACCEPTED", True)
            if accepted
            else ("WAIVER_PENDING", "WAIVER_EVIDENCE_REQUIRED", False)
        )
        if (state, reason, accepted) != expected:
            _fail("PROGRESS_INPUT_INCONSISTENT")
        return

    if level == "U2":
        if state == "USER_OBSERVATION_PENDING":
            valid = not accepted and reason == "USER_EVENT_RECEIPT_REQUIRED" and post["fail"] == 0 and post["pass"] < post["required"]
        elif state == "USER_OBSERVATION_FAILED":
            valid = not accepted and reason == "OBSERVATION_FAILED" and post["fail"] > 0
        elif state == "USER_OBSERVED":
            valid = accepted and reason == "ACCEPTED" and post["pass"] == post["required"] and post["fail"] == 0
        else:
            valid = False
        if not valid:
            _fail("PROGRESS_INPUT_INCONSISTENT")
        return

    expected_reason = {
        "U3_PRE_ACTION_PENDING": "U3_PRE_ACTION_REQUIRED",
        "U3_PRE_ACTION_FAILED": "U3_PRE_ACTION_FAILED",
        "U3_ACTION_PENDING": "U3_ACTION_RESULT_REQUIRED",
        "U3_ACTION_FAILED": "U3_ACTION_FAILED",
        "U3_ACTION_UNKNOWN": "U3_ACTION_UNKNOWN",
        "U3_POST_ACTION_PENDING": "USER_EVENT_RECEIPT_REQUIRED",
        "U3_POST_ACTION_FAILED": "U3_POST_ACTION_FAILED",
        "U3_ROLLBACK_VERIFICATION_PENDING": "U3_ROLLBACK_VERIFICATION_REQUIRED",
        "U3_ROLLBACK_VERIFICATION_FAILED": "U3_ROLLBACK_VERIFICATION_FAILED",
        "USER_OBSERVED": "ACCEPTED",
    }
    if state not in expected_reason or reason != expected_reason[state]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    pre_empty = pre["pass"] == pre["fail"] == 0
    pre_passed = pre["pass"] == 1 and pre["fail"] == 0
    post_empty = post["pass"] == post["fail"] == 0
    post_complete = post["pass"] == post["required"] and post["fail"] == 0
    rollback_empty = rollback["pass"] == rollback["fail"] == 0
    valid = {
        "U3_PRE_ACTION_PENDING": pre_empty and post_empty and rollback_empty and action_state == "PENDING",
        "U3_PRE_ACTION_FAILED": pre["fail"] == 1 and pre["pass"] == 0 and post_empty and rollback_empty and action_state == "PENDING",
        "U3_ACTION_PENDING": pre_passed and post_empty and rollback_empty and action_state == "PENDING",
        "U3_ACTION_FAILED": pre_passed and post_empty and rollback_empty and action_state == "FAILED",
        "U3_ACTION_UNKNOWN": pre_passed and post_empty and rollback_empty and action_state == "UNKNOWN",
        "U3_POST_ACTION_PENDING": pre_passed and post["pass"] < post["required"] and post["fail"] == 0 and rollback_empty and action_state == "SUCCEEDED",
        "U3_POST_ACTION_FAILED": pre_passed and post["fail"] > 0 and rollback_empty and action_state == "SUCCEEDED",
        "U3_ROLLBACK_VERIFICATION_PENDING": pre_passed and post_complete and rollback_empty and action_state == "SUCCEEDED",
        "U3_ROLLBACK_VERIFICATION_FAILED": pre_passed and post_complete and rollback["fail"] == 1 and action_state == "SUCCEEDED",
        "USER_OBSERVED": pre_passed and post_complete and rollback["pass"] == 1 and rollback["fail"] == 0 and action_state == "SUCCEEDED",
    }[state]
    if not valid or accepted != (state == "USER_OBSERVED"):
        _fail("PROGRESS_INPUT_INCONSISTENT")


def _validate_acceptance_projection(value: object) -> dict[str, object]:
    item = _exact_object(value, BASIS_ACCEPTANCE_FIELDS)
    _text(item["task_id"], identifier=True)
    computed_level = item["computed_level"]
    level = item["level"]
    if not isinstance(computed_level, str) or not isinstance(level, str):
        _fail("FIELD_TYPE_INVALID")
    if computed_level not in ACCEPTANCE_LEVELS or level not in ACCEPTANCE_LEVELS:
        _fail("FIELD_VALUE_INVALID")
    _text(item["mode"], identifier=True)
    for field in ("human_required", "outcome_ready", "accepted", "completion_eligible", "write_authorized"):
        if type(item[field]) is not bool:
            _fail("FIELD_TYPE_INVALID")
    state = item["acceptance_state"]
    if not isinstance(state, str) or state not in ACCEPTANCE_STATES:
        _fail("FIELD_VALUE_INVALID")
    _text(item["reason"], identifier=True)
    summary = _validate_requirement_summary(item["requirement_summary"])
    action_state = item["action_state"]
    if not isinstance(action_state, str) or action_state not in ACTION_STATES:
        _fail("FIELD_VALUE_INVALID")
    validated = copy.deepcopy(item)
    validated["requirement_summary"] = summary
    _require_acceptance_semantics(validated)
    return validated


def _validate_acceptance_decision(value: object) -> dict[str, object]:
    item = _exact_object(value, ACCEPTANCE_DECISION_FIELDS)
    if item["schema_version"] != 1 or isinstance(item["schema_version"], bool):
        _fail("FIELD_VALUE_INVALID")
    _string_list(item["reasons"])
    projected = {field: copy.deepcopy(item[field]) for field in BASIS_ACCEPTANCE_FIELDS}
    return _validate_acceptance_projection(projected)


def _project_completion_basis(
    outcome: dict[str, object], execution: dict[str, object], acceptance: dict[str, object]
) -> dict[str, object]:
    return {
        "outcome": copy.deepcopy(outcome),
        "execution": copy.deepcopy(execution),
        "acceptance": copy.deepcopy(acceptance),
    }


def _validate_completion_basis(value: object) -> dict[str, object]:
    item = _exact_object(value, COMPLETION_BASIS_FIELDS)
    return {
        "outcome": _validate_outcome_projection(item["outcome"]),
        "execution": _validate_execution_projection(item["execution"]),
        "acceptance": _validate_acceptance_projection(item["acceptance"]),
    }


def _derive_completion_state(
    basis: dict[str, object],
    strategy_status: dict[str, object],
    capabilities: list[dict[str, object]],
    verification: dict[str, object],
    technical_appendix: dict[str, object],
) -> dict[str, object]:
    outcome = basis["outcome"]
    execution = basis["execution"]
    acceptance = basis["acceptance"]
    assert all(isinstance(item, dict) for item in (outcome, execution, acceptance, strategy_status, verification, technical_appendix))
    counts = technical_appendix["test_counts"]
    reviewer = technical_appendix["reviewer_result"]
    assert isinstance(counts, dict) and isinstance(reviewer, dict)
    reasons: list[str] = []
    if outcome["state"] != "DELIVERY_VERIFIED" or outcome["final_uat_eligible"] is not True:
        reasons.append("OUTCOME_INCOMPLETE")
    execution_ready = (
        execution["eligible"] is True
        and execution["reason"] == "STABLE_RESUME"
        and not execution["drift_fields"]
    )
    if not execution_ready:
        reasons.append("EXECUTION_INELIGIBLE")
    if any(
        execution[field] is not False
        for field in ("authority_created", "external_write_authorized", "write_authorized")
    ):
        reasons.append("EXECUTION_AUTHORITY_INVALID")
    if strategy_status["state"] != COMPLETION_STRATEGY_STATE:
        reasons.append("STRATEGY_NOT_READY")
    if any(
        item["critical"] is True and item["status"] in BLOCKING_STATUSES
        for item in capabilities
    ):
        reasons.append("CRITICAL_CAPABILITY_BLOCKED")
    if verification["unknown"]:
        reasons.append("VERIFICATION_UNKNOWN")
    if counts["failed"] or counts["errors"]:
        reasons.append("TESTS_FAILED")
    if reviewer["critical"] or reviewer["high"]:
        reasons.append("REVIEW_CRITICAL_HIGH")
    if reviewer["state"] not in {"PASS", "NOT_APPLICABLE"}:
        reasons.append("REVIEW_NOT_PASS")
    nonhuman_ready = not reasons
    acceptance_state = str(acceptance["acceptance_state"])
    if (
        nonhuman_ready
        and acceptance["accepted"] is True
        and acceptance["completion_eligible"] is True
        and acceptance_state in TERMINAL_ACCEPTANCE_STATES
    ):
        task_state = "COMPLETE"
        reasons = ["ALL_GATES_SATISFIED"]
    elif nonhuman_ready and acceptance_state in WAITING_USER_STATES:
        task_state = "WAITING_USER"
        reasons = ["USER_ACTION_REQUIRED"]
    else:
        task_state = "INCOMPLETE"
        if not reasons:
            reasons.append("ACCEPTANCE_INCOMPLETE")
    return {
        "task_state": task_state,
        "outcome_state": outcome["state"],
        "execution_eligible": execution["eligible"],
        "strategy_state": strategy_status["state"],
        "acceptance_state": acceptance_state,
        "completion_eligible": task_state == "COMPLETE",
        "reason_codes": sorted(reasons),
    }


def _validate_completion_state(value: object) -> dict[str, object]:
    item = _exact_object(value, COMPLETION_STATE_FIELDS)
    task_state = item["task_state"]
    if not isinstance(task_state, str) or task_state not in TASK_STATES:
        _fail("FIELD_VALUE_INVALID")
    outcome_state = item["outcome_state"]
    if not isinstance(outcome_state, str) or outcome_state not in {"INCOMPLETE", "DELIVERY_VERIFIED"}:
        _fail("FIELD_VALUE_INVALID")
    if type(item["execution_eligible"]) is not bool or type(item["completion_eligible"]) is not bool:
        _fail("FIELD_TYPE_INVALID")
    strategy_state = item["strategy_state"]
    if not isinstance(strategy_state, str) or strategy_state not in STRATEGY_STATES:
        _fail("FIELD_VALUE_INVALID")
    acceptance_state = item["acceptance_state"]
    if not isinstance(acceptance_state, str) or acceptance_state not in ACCEPTANCE_STATES:
        _fail("FIELD_VALUE_INVALID")
    reasons = _string_list(item["reason_codes"], allow_empty=False, identifier=True)
    if reasons != sorted(reasons):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    return copy.deepcopy(item)


def _require_common_consistency(source: dict[str, object], *, basis_key: str | None = None) -> None:
    if basis_key is None:
        outcome = source["outcome_decision"]
        execution = source["execution_decision"]
        acceptance = source["acceptance_decision"]
    else:
        basis = source[basis_key]
        assert isinstance(basis, dict)
        outcome = basis["outcome"]
        execution = basis["execution"]
        acceptance = basis["acceptance"]
        if source["goal_display"] != outcome["goal_display"]:
            _fail("PROGRESS_INPUT_INCONSISTENT")
    selected = source["selected_strategy"]
    strategy = source["strategy_status"]
    capabilities = source["user_visible_capabilities"]
    participation = source["user_participation"]
    assert all(isinstance(item, dict) for item in (outcome, execution, acceptance, selected, strategy, participation))
    assert isinstance(capabilities, list)
    task_id = source["task_id"]
    if any(item["task_id"] != task_id for item in (outcome, execution, acceptance)):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if selected["route_id"] != strategy["route_id"]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if outcome["final_uat_eligible"] != acceptance["outcome_ready"]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if participation["required"] != acceptance["human_required"]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    blocking = sorted(
        str(item["capability_id"])
        for item in capabilities
        if item["critical"] is True and item["status"] in BLOCKING_STATUSES
    )
    if blocking != sorted(outcome["blocking_capability_ids"]):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    expected_state = "INCOMPLETE" if blocking else "DELIVERY_VERIFIED"
    if outcome["state"] != expected_state or outcome["final_uat_eligible"] != (not blocking):
        _fail("PROGRESS_INPUT_INCONSISTENT")
    alternatives = source["alternative_routes"]
    assert isinstance(alternatives, list)
    if strategy["state"] == "ROUTE_REASSESSMENT_REQUIRED":
        mechanisms = {str(item["mechanism"]) for item in alternatives}
        if strategy["failure_count"] < 4 or len(alternatives) < 2 or len(mechanisms) < 2:
            _fail("PROGRESS_INPUT_INCONSISTENT")
        if "第五次" in str(source["next_step"]):
            _fail("PROGRESS_INPUT_INCONSISTENT")
    if not outcome["final_uat_eligible"]:
        next_words = f"{source.get('current_conclusion', '')} {source['next_step']}"
        if "最终 UAT" in next_words or "开始 UAT" in next_words:
            _fail("PROGRESS_INPUT_INCONSISTENT")


def validate_progress_input(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    _validate_bounded_tree(value)
    _reject_sensitive_or_writable(value)
    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    if frozenset(value) != PROGRESS_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    _text(value["task_id"], identifier=True)
    for field in ("current_phase", "next_step"):
        _text(value[field])
    _validate_milestones(value["milestones"])
    _validate_capabilities(value["user_visible_capabilities"])
    _validate_selected_strategy(value["selected_strategy"])
    _string_list(value["implementation_logic"], allow_empty=False)
    _validate_verification(value["verification"])
    _validate_strategy_status(value["strategy_status"])
    _validate_alternatives(value["alternative_routes"])
    _validate_user_participation(value["user_participation"])
    _string_list(value["stop_conditions"], allow_empty=False)
    _validate_technical_appendix(value["technical_appendix"])
    _validate_outcome_decision(value["outcome_decision"])
    _validate_execution_decision(value["execution_decision"])
    _validate_acceptance_decision(value["acceptance_decision"])
    validated = copy.deepcopy(value)
    _require_common_consistency(validated)
    return validated


def _deterministic_report(report: dict[str, object]) -> dict[str, object]:
    report["user_visible_capabilities"] = sorted(
        report["user_visible_capabilities"], key=lambda item: str(item["capability_id"])
    )
    report["alternative_routes"] = sorted(
        report["alternative_routes"], key=lambda item: str(item["route_id"])
    )
    verification = report["verification"]
    technical = report["technical_appendix"]
    assert isinstance(verification, dict) and isinstance(technical, dict)
    for field in VERIFICATION_FIELDS:
        verification[field] = sorted(verification[field], key=lambda item: str(item).encode("utf-8"))
    for field in ("files", "check_ids", "digests", "commits"):
        technical[field] = sorted(technical[field], key=lambda item: str(item).encode("utf-8"))
    report["stop_conditions"] = sorted(report["stop_conditions"], key=lambda item: str(item).encode("utf-8"))
    return report


def build_progress_report(value: object) -> dict[str, object]:
    source = validate_progress_input(value)
    outcome = _validate_outcome_decision(source["outcome_decision"])
    execution = _validate_execution_decision(source["execution_decision"])
    acceptance = _validate_acceptance_decision(source["acceptance_decision"])
    basis = _project_completion_basis(outcome, execution, acceptance)
    completion = _derive_completion_state(
        basis,
        source["strategy_status"],
        source["user_visible_capabilities"],
        source["verification"],
        source["technical_appendix"],
    )
    report = {
        key: copy.deepcopy(source[key])
        for key in PROGRESS_FIELDS
        if key not in {"outcome_decision", "execution_decision", "acceptance_decision"}
    }
    report.update(
        goal_display=copy.deepcopy(outcome["goal_display"]),
        current_conclusion=CONCLUSIONS[str(completion["task_state"])],
        completion_basis=basis,
        completion_state=completion,
        write_authorized=False,
    )
    report = _deterministic_report(report)
    _validate_public_strings(report)
    return report


def _validate_report(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    _validate_bounded_tree(value)
    _reject_sensitive_or_writable(value)
    _validate_public_strings(value)
    if frozenset(value) != REPORT_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    if value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        _fail("SCHEMA_UNSUPPORTED")
    if value.get("write_authorized") is not False:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    _text(value.get("task_id"), identifier=True)
    for field in ("current_conclusion", "current_phase", "next_step"):
        _text(value.get(field))
    _validate_goal_display(value.get("goal_display"))
    _validate_milestones(value.get("milestones"))
    capabilities = _validate_capabilities(value.get("user_visible_capabilities"))
    _validate_selected_strategy(value.get("selected_strategy"))
    _string_list(value.get("implementation_logic"), allow_empty=False)
    verification = _validate_verification(value.get("verification"))
    strategy = _validate_strategy_status(value.get("strategy_status"))
    _validate_alternatives(value.get("alternative_routes"))
    _validate_user_participation(value.get("user_participation"))
    _string_list(value.get("stop_conditions"), allow_empty=False)
    technical = _validate_technical_appendix(value.get("technical_appendix"))
    basis = _validate_completion_basis(value.get("completion_basis"))
    completion = _validate_completion_state(value.get("completion_state"))
    validated = copy.deepcopy(value)
    _require_common_consistency(validated, basis_key="completion_basis")
    expected = _derive_completion_state(basis, strategy, capabilities, verification, technical)
    if completion != expected:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    if value["current_conclusion"] != CONCLUSIONS[str(expected["task_state"])]:
        _fail("PROGRESS_INPUT_INCONSISTENT")
    return validated


def render_progress_json(value: object) -> str:
    report = _validate_report(value)
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bullets(items: list[object]) -> str:
    if not items:
        return "- 无"
    return "\n".join(f"- {_markdown_text(item)}" for item in items)


def _markdown_text(value: object) -> str:
    if not isinstance(value, str):
        _fail("FIELD_TYPE_INVALID")
    try:
        return escape_markdown_text(value)
    except SafePublicTextError:
        _fail("SECRET_VALUE_REJECTED")


def render_progress_markdown(value: object) -> str:
    report = _validate_report(value)
    milestones = report["milestones"]
    capabilities = report["user_visible_capabilities"]
    selected = report["selected_strategy"]
    verification = report["verification"]
    strategy = report["strategy_status"]
    alternatives = report["alternative_routes"]
    participation = report["user_participation"]
    technical = report["technical_appendix"]
    completion = report["completion_state"]
    goal_display = report["goal_display"]
    assert all(isinstance(item, dict) for item in (milestones, selected, verification, strategy, participation, technical, completion, goal_display))
    assert isinstance(capabilities, list) and isinstance(alternatives, list)
    capability_lines = [
        f"{item['capability_id']} | {item['name']} | {item['environment']} | {item['status']}"
        for item in capabilities
    ]
    alternative_lines = [
        f"{item['mechanism']}：{item['tradeoff']}（{item['route_id']}）"
        for item in alternatives
    ]
    participation_state = "需要" if participation["required"] else "暂不需要"
    return "\n\n".join(
        (
            "# 中文研发主管进度卡",
            "## 当前结论\n\n"
            f"目标摘要：{_markdown_text(goal_display['safe_summary'])}\n\n"
            f"原始目标绑定：{_markdown_text(goal_display['original_goal_sha256'])}\n\n"
            f"原文状态：{_markdown_text(goal_display['original_text_state'])}\n\n"
            f"结构化状态：{_markdown_text(completion['task_state'])}\n\n"
            f"当前结论：{_markdown_text(report['current_conclusion'])}\n\n"
            f"原因代码：{', '.join(_markdown_text(item) for item in completion['reason_codes'])}",
            "## 当前阶段与总体进度\n\n"
            f"当前阶段：{_markdown_text(report['current_phase'])}\n\n"
            f"已完成：\n{_bullets(milestones['completed'])}\n\n"
            f"正在进行：\n{_bullets(milestones['current'])}\n\n"
            f"剩余：\n{_bullets(milestones['remaining'])}",
            f"## 已实现功能\n\n{_bullets(capability_lines)}",
            "## 当前方案\n\n"
            f"方案：{_markdown_text(selected['mechanism'])}\n\n"
            f"选择原因：{_markdown_text(selected['selection_reason'])}",
            f"## 实现逻辑\n\n{_bullets(report['implementation_logic'])}",
            "## 证据与未知\n\n"
            f"已验证：\n{_bullets(verification['verified'])}\n\n"
            f"警告：\n{_bullets(verification['warnings'])}\n\n"
            f"未验证：\n{_bullets(verification['unverified'])}\n\n"
            f"UNKNOWN：\n{_bullets(verification['unknown'])}",
            "## 问题与路线尝试\n\n"
            f"路线状态：{_markdown_text(strategy['state'])}\n\n"
            f"累计实质失败：{strategy['failure_count']}\n\n"
            f"本次证据变化：\n{_bullets(strategy['evidence_delta'])}",
            f"## 备选路线\n\n{_bullets(alternative_lines)}",
            "## 是否需要你参与\n\n"
            f"{participation_state}。{_markdown_text(participation['reason'])}\n\n"
            f"操作：{_markdown_text(participation['one_next_action'])}\n\n"
            f"预期：{_markdown_text(participation['expected_result'])}\n\n"
            f"副作用：{_markdown_text(participation['side_effects'])}\n\n"
            f"失败停止点：{_markdown_text(participation['failure_stop'])}",
            "## 下一步\n\n"
            f"{_markdown_text(report['next_step'])}\n\n"
            f"停止条件：\n{_bullets(report['stop_conditions'])}",
            "## 技术附录\n\n```json\n"
            + json.dumps(technical, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```",
        )
    ) + "\n"
