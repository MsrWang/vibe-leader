#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate bounded local execution authority and stable resume anchors."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Literal


SCHEMA_VERSION = 1
MAX_TEXT_LENGTH = 4096
MAX_LIST_LENGTH = 128

ActionClass = Literal[
    "LOCAL_READ", "LOCAL_REVERSIBLE", "EXTERNAL_OR_IRREVERSIBLE"
]
ResumeEvent = Literal[
    "SAME_TASK_RESUME", "CONTEXT_COMPACTION", "TASK_SWITCH", "PROJECT_SWITCH"
]

LOCAL_ACTIONS = frozenset(
    {"READ", "EDIT", "TEST", "STATIC_CHECK", "DOC_UPDATE", "LOCAL_COMMIT", "REVIEW"}
)
EXTERNAL_ACTIONS = frozenset(
    {
        "NETWORK",
        "INSTALL",
        "ACCOUNT",
        "CREDENTIAL",
        "PAID",
        "PUSH",
        "PUBLISH",
        "DEPLOY",
        "DELETE",
        "BUSINESS_WRITE",
        "PRODUCTION_MIGRATION",
    }
)
ACTION_CLASSES = frozenset(
    {"LOCAL_READ", "LOCAL_REVERSIBLE", "EXTERNAL_OR_IRREVERSIBLE"}
)
RESUME_EVENTS = frozenset(
    {"SAME_TASK_RESUME", "CONTEXT_COMPACTION", "TASK_SWITCH", "PROJECT_SWITCH"}
)
AUTHORITY_SOURCES = frozenset({"USER_REQUEST", "APPROVED_DESIGN", "APPROVED_PLAN"})
ENVIRONMENTS = frozenset({"local", "test", "staging", "production"})
LIFECYCLE_STATES = frozenset({"ACTIVE", "CONSUMED", "CANCELLED"})
ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "envelope_id",
        "task_id",
        "environment",
        "authority_source",
        "authority_project",
        "outcome_digest",
        "design_digest",
        "plan_digest",
        "allowlist",
        "behavior_allowlist",
        "allowlist_digest",
        "permission_scope_digest",
        "side_effect_scope_digest",
        "resume_state_digest",
        "allowed_actions",
        "forbidden_actions",
        "expected_artifacts",
        "stop_conditions",
        "reviewer_budget",
        "lifecycle",
        "consumed_actions",
    }
)
AUTHORITY_PROJECT_FIELDS = frozenset(
    {
        "logical_path",
        "physical_path",
        "git_root",
        "git_dir",
        "git_common_dir",
        "worktree_identity",
        "branch",
        "head",
        "dirty_fingerprint",
    }
)
CONTENT_DIGEST_FIELDS = frozenset({"outcome_digest", "design_digest", "plan_digest"})
SCOPE_DIGEST_FIELDS = frozenset(
    {
        "allowlist_digest",
        "permission_scope_digest",
        "side_effect_scope_digest",
        "resume_state_digest",
    }
)
DIGEST_FIELDS = CONTENT_DIGEST_FIELDS | SCOPE_DIGEST_FIELDS
CURRENT_BINDING_FIELDS = frozenset(
    {
        "task_id",
        "envelope_id",
        "environment",
        "authority_project",
    }
) | DIGEST_FIELDS
CURRENT_OPTIONAL_FIELDS = frozenset({"captured_at_utc"})
REVIEWER_BUDGET_FIELDS = frozenset({"max_reviewers", "max_rounds", "max_calls"})
LIFECYCLE_FIELDS = frozenset({"state", "same_task_resume_allowed"})
ACTION_FIELDS = frozenset({"class", "name", "scope"})
FILE_SCOPE_FIELDS = frozenset({"kind", "paths"})
BEHAVIOR_SCOPE_FIELDS = frozenset({"kind", "behavior_id"})
FILE_ONLY_ACTIONS = frozenset({"READ", "EDIT", "DOC_UPDATE", "LOCAL_COMMIT"})
DUAL_SCOPE_ACTIONS = frozenset({"TEST", "STATIC_CHECK", "REVIEW"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEAD = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ExecutionContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise ExecutionContractError(reason)


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
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail("FIELD_VALUE_INVALID")
    return hashlib.sha256(encoded).hexdigest()


def _utf8_sorted(values: list[str]) -> list[str]:
    return sorted(values, key=lambda item: item.encode("utf-8"))


def _absolute_path(value: object) -> str:
    text = _text(value)
    path = PurePosixPath(text)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or str(path) != text
        or text == "/"
    ):
        _fail("FIELD_VALUE_INVALID")
    return text


def _relative_path(value: object) -> str:
    text = _text(value)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or text == "."
        or ".." in path.parts
        or str(path) != text
    ):
        _fail("FIELD_VALUE_INVALID")
    return text


def _unique_string_list(
    value: object,
    *,
    allowed: frozenset[str] | None = None,
    allow_empty: bool = True,
    paths: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        _fail("FIELD_TYPE_INVALID")
    if len(value) > MAX_LIST_LENGTH or (not allow_empty and not value):
        _fail("FIELD_VALUE_INVALID")
    result: list[str] = []
    for item in value:
        checked = _relative_path(item) if paths else _text(item, identifier=allowed is not None)
        if allowed is not None and checked not in allowed:
            _fail("FIELD_VALUE_INVALID")
        result.append(checked)
    if len(set(result)) != len(result):
        _fail("FIELD_VALUE_INVALID")
    return result


def _validate_allowlist(value: object) -> list[str]:
    paths = _unique_string_list(value, allow_empty=False, paths=True)
    pure = [PurePosixPath(item) for item in paths]
    for index, left in enumerate(pure):
        for right in pure[index + 1 :]:
            if left in right.parents or right in left.parents:
                _fail("FIELD_VALUE_INVALID")
    return paths


def _validate_authority_project(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != AUTHORITY_PROJECT_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    for field in (
        "logical_path",
        "physical_path",
        "git_root",
        "git_dir",
        "git_common_dir",
    ):
        _absolute_path(value.get(field))
    _text(value.get("branch"))
    head = value.get("head")
    if not isinstance(head, str) or HEAD.fullmatch(head) is None:
        _fail("FIELD_VALUE_INVALID")
    _sha256(value.get("dirty_fingerprint"))
    worktree_identity = _sha256(value.get("worktree_identity"))
    expected_identity = _canonical_digest(
        {
            "git_common_dir": value["git_common_dir"],
            "git_dir": value["git_dir"],
            "physical_path": value["physical_path"],
        }
    )
    if worktree_identity != expected_identity:
        _fail("DIGEST_MISMATCH")
    return copy.deepcopy(value)


def _validate_reviewer_budget(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != REVIEWER_BUDGET_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    result: dict[str, int] = {}
    for field in sorted(REVIEWER_BUDGET_FIELDS):
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int):
            _fail("FIELD_TYPE_INVALID")
        if item < 0 or item > 128:
            _fail("FIELD_VALUE_INVALID")
        result[field] = item
    return result


def _validate_lifecycle(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != LIFECYCLE_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    state = value.get("state")
    if not isinstance(state, str):
        _fail("FIELD_TYPE_INVALID")
    if state not in LIFECYCLE_STATES:
        _fail("FIELD_VALUE_INVALID")
    if type(value.get("same_task_resume_allowed")) is not bool:
        _fail("FIELD_TYPE_INVALID")
    return copy.deepcopy(value)


def validate_execution_envelope(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    if frozenset(value) != ENVELOPE_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    _text(value.get("envelope_id"), identifier=True)
    _text(value.get("task_id"), identifier=True)
    environment = _text(value.get("environment"), identifier=True)
    if environment not in ENVIRONMENTS:
        _fail("FIELD_VALUE_INVALID")
    authority_source = value.get("authority_source")
    if not isinstance(authority_source, str) or not authority_source:
        _fail("AUTHORITY_SOURCE_MISSING")
    if authority_source not in AUTHORITY_SOURCES:
        _fail("FIELD_VALUE_INVALID")
    project = _validate_authority_project(value.get("authority_project"))
    for field in CONTENT_DIGEST_FIELDS:
        _sha256(value.get(field))
    allowlist = _validate_allowlist(value.get("allowlist"))
    behavior_allowlist = _unique_string_list(
        value.get("behavior_allowlist"), allow_empty=False
    )
    for behavior_id in behavior_allowlist:
        _text(behavior_id, identifier=True)
    allowed_actions = _unique_string_list(
        value.get("allowed_actions"), allowed=LOCAL_ACTIONS, allow_empty=False
    )
    forbidden_actions = _unique_string_list(
        value.get("forbidden_actions"), allowed=EXTERNAL_ACTIONS, allow_empty=False
    )
    expected_artifacts = _unique_string_list(value.get("expected_artifacts"))
    stop_conditions = _unique_string_list(value.get("stop_conditions"))
    budget = _validate_reviewer_budget(value.get("reviewer_budget"))
    lifecycle = _validate_lifecycle(value.get("lifecycle"))
    consumed = _unique_string_list(value.get("consumed_actions"), allowed=LOCAL_ACTIONS)
    if not set(consumed).issubset(allowed_actions):
        _fail("FIELD_VALUE_INVALID")
    if "REVIEW" in allowed_actions and any(budget[field] < 1 for field in REVIEWER_BUDGET_FIELDS):
        _fail("FIELD_VALUE_INVALID")
    expected_digests = {
        "allowlist_digest": _canonical_digest(
            {
                "behavior_allowlist": _utf8_sorted(behavior_allowlist),
                "file_allowlist": _utf8_sorted(allowlist),
            }
        ),
        "permission_scope_digest": _canonical_digest(
            {
                "allowed_actions": _utf8_sorted(allowed_actions),
                "authority_source": authority_source,
                "reviewer_budget": budget,
                "same_task_resume_allowed": lifecycle["same_task_resume_allowed"],
            }
        ),
        "side_effect_scope_digest": _canonical_digest(
            {
                "environment": value["environment"],
                "expected_artifacts": _utf8_sorted(expected_artifacts),
                "forbidden_actions": _utf8_sorted(forbidden_actions),
                "stop_conditions": _utf8_sorted(stop_conditions),
            }
        ),
        "resume_state_digest": _canonical_digest(
            {
                "consumed_actions": _utf8_sorted(consumed),
                "lifecycle_state": lifecycle["state"],
            }
        ),
    }
    for field, expected in expected_digests.items():
        if _sha256(value.get(field)) != expected:
            _fail("DIGEST_MISMATCH")
    assert isinstance(project, dict)
    validated = copy.deepcopy(value)
    for field, items in (
        ("allowlist", allowlist),
        ("behavior_allowlist", behavior_allowlist),
        ("allowed_actions", allowed_actions),
        ("forbidden_actions", forbidden_actions),
        ("expected_artifacts", expected_artifacts),
        ("stop_conditions", stop_conditions),
        ("consumed_actions", consumed),
    ):
        validated[field] = _utf8_sorted(items)
    return validated


def canonical_envelope_digest(value: object) -> str:
    validated = validate_execution_envelope(value)
    encoded = json.dumps(
        validated,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_action(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if value.get("scope") is None or value.get("scope") == {}:
        _fail("ACTION_SCOPE_REQUIRED")
    if frozenset(value) != ACTION_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    action_class = value.get("class")
    name = value.get("name")
    if not isinstance(action_class, str) or not isinstance(name, str):
        _fail("FIELD_TYPE_INVALID")
    if action_class not in ACTION_CLASSES:
        _fail("FIELD_VALUE_INVALID")
    if name not in LOCAL_ACTIONS | EXTERNAL_ACTIONS:
        _fail("FIELD_VALUE_INVALID")
    if action_class == "LOCAL_READ" and name != "READ":
        _fail("FIELD_VALUE_INVALID")
    if action_class == "LOCAL_REVERSIBLE" and name not in LOCAL_ACTIONS:
        _fail("FIELD_VALUE_INVALID")
    if action_class == "EXTERNAL_OR_IRREVERSIBLE" and name not in EXTERNAL_ACTIONS:
        _fail("FIELD_VALUE_INVALID")
    scope = value.get("scope")
    if not isinstance(scope, dict):
        _fail("FIELD_TYPE_INVALID")
    kind = scope.get("kind")
    if not isinstance(kind, str):
        _fail("FIELD_TYPE_INVALID")
    if kind == "FILE_SET":
        if frozenset(scope) != FILE_SCOPE_FIELDS:
            _fail("FIELD_VALUE_INVALID")
        if scope.get("paths") == []:
            _fail("ACTION_SCOPE_REQUIRED")
        _unique_string_list(scope.get("paths"), allow_empty=False, paths=True)
        if name not in FILE_ONLY_ACTIONS | DUAL_SCOPE_ACTIONS:
            _fail("FIELD_VALUE_INVALID")
    elif kind == "BEHAVIOR":
        if frozenset(scope) != BEHAVIOR_SCOPE_FIELDS:
            _fail("FIELD_VALUE_INVALID")
        _text(scope.get("behavior_id"), identifier=True)
        if name not in DUAL_SCOPE_ACTIONS | EXTERNAL_ACTIONS:
            _fail("FIELD_VALUE_INVALID")
    else:
        _fail("FIELD_VALUE_INVALID")
    return copy.deepcopy(value)


def _action_result(
    envelope: dict[str, object], action: dict[str, object], eligible: bool, reason: str
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "envelope_id": envelope["envelope_id"],
        "task_id": envelope["task_id"],
        "action_class": action["class"],
        "action_name": action["name"],
        "eligible": eligible,
        "reason": reason,
        "authority_created": False,
        "external_write_authorized": False,
        "write_authorized": False,
    }


def _path_is_allowed(path: str, allowlist: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate == allowed or PurePosixPath(allowed) in candidate.parents
        for allowed in map(PurePosixPath, allowlist)
    )


def evaluate_action(envelope: object, action: object) -> dict[str, object]:
    bound = validate_execution_envelope(envelope)
    requested = _validate_action(action)
    scope = requested["scope"]
    assert isinstance(scope, dict)
    if scope["kind"] == "FILE_SET":
        allowlist = bound["allowlist"]
        paths = scope["paths"]
        assert isinstance(allowlist, list) and isinstance(paths, list)
        scope_allowed = all(_path_is_allowed(path, allowlist) for path in paths)
    else:
        behaviors = bound["behavior_allowlist"]
        assert isinstance(behaviors, list)
        scope_allowed = scope["behavior_id"] in behaviors
    if not scope_allowed:
        return _action_result(bound, requested, False, "ACTION_OUT_OF_SCOPE")
    if requested["class"] == "EXTERNAL_OR_IRREVERSIBLE":
        return _action_result(bound, requested, False, "EXTERNAL_APPROVAL_REQUIRED")
    lifecycle = bound["lifecycle"]
    assert isinstance(lifecycle, dict)
    consumed = bound["consumed_actions"]
    assert isinstance(consumed, list)
    if lifecycle["state"] != "ACTIVE" or requested["name"] in consumed:
        return _action_result(bound, requested, False, "ENVELOPE_INACTIVE")
    allowed_actions = bound["allowed_actions"]
    assert isinstance(allowed_actions, list)
    if requested["name"] not in allowed_actions:
        return _action_result(bound, requested, False, "ACTION_OUT_OF_SCOPE")
    return _action_result(bound, requested, True, "ACTION_ALLOWED")


def _validate_current_binding(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    fields = frozenset(value)
    if not CURRENT_BINDING_FIELDS.issubset(fields) or not fields.issubset(
        CURRENT_BINDING_FIELDS | CURRENT_OPTIONAL_FIELDS
    ):
        _fail("SCHEMA_FIELDS_CHANGED")
    _text(value.get("task_id"), identifier=True)
    _text(value.get("envelope_id"), identifier=True)
    environment = _text(value.get("environment"), identifier=True)
    if environment not in ENVIRONMENTS:
        _fail("FIELD_VALUE_INVALID")
    _validate_authority_project(value.get("authority_project"))
    for field in DIGEST_FIELDS:
        _sha256(value.get(field))
    if "captured_at_utc" in value:
        _text(value["captured_at_utc"])
    return copy.deepcopy(value)


def _resume_result(
    bound: dict[str, object],
    eligible: bool,
    reason: str,
    *,
    drift_fields: list[str] | None = None,
) -> dict[str, object]:
    allowed = bound["allowed_actions"]
    consumed = set(bound["consumed_actions"])
    assert isinstance(allowed, list)
    remaining = sorted(item for item in allowed if item not in consumed) if eligible else []
    return {
        "schema_version": 1,
        "envelope_id": bound["envelope_id"],
        "task_id": bound["task_id"],
        "eligible": eligible,
        "reason": reason,
        "drift_fields": sorted(drift_fields or []),
        "remaining_actions": remaining,
        "authority_created": False,
        "external_write_authorized": False,
        "write_authorized": False,
    }


def evaluate_resume(
    envelope: object, current: object, *, event: ResumeEvent
) -> dict[str, object]:
    bound = validate_execution_envelope(envelope)
    now = _validate_current_binding(current)
    if not isinstance(event, str):
        _fail("FIELD_TYPE_INVALID")
    if event not in RESUME_EVENTS:
        _fail("FIELD_VALUE_INVALID")
    if event in {"TASK_SWITCH", "PROJECT_SWITCH"}:
        return _resume_result(
            bound,
            False,
            "TASK_OR_ENVELOPE_CHANGED",
            drift_fields=["resume_event"],
        )
    lifecycle = bound["lifecycle"]
    assert isinstance(lifecycle, dict)
    if lifecycle["state"] != "ACTIVE" or lifecycle["same_task_resume_allowed"] is not True:
        return _resume_result(bound, False, "ENVELOPE_INACTIVE")
    identity_drift = [
        field
        for field in ("task_id", "envelope_id")
        if bound[field] != now[field]
    ]
    if identity_drift:
        return _resume_result(
            bound,
            False,
            "TASK_OR_ENVELOPE_CHANGED",
            drift_fields=identity_drift,
        )
    project = bound["authority_project"]
    current_project = now["authority_project"]
    assert isinstance(project, dict) and isinstance(current_project, dict)
    drift = []
    if bound["environment"] != now["environment"]:
        drift.append("environment")
    drift.extend(
        f"authority_project.{field}"
        for field in AUTHORITY_PROJECT_FIELDS
        if project[field] != current_project[field]
    )
    drift.extend(field for field in DIGEST_FIELDS if bound[field] != now[field])
    if drift:
        return _resume_result(bound, False, "RESUME_ANCHOR_DRIFT", drift_fields=drift)
    return _resume_result(bound, True, "STABLE_RESUME")
