#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate bounded adaptive-delegation contracts without project reads."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import unicodedata
from datetime import datetime
from pathlib import Path, PurePosixPath


LEGACY_BRIEF_SCHEMA_ID = "vibe-project-lead-zh-delegation-brief-v1"
LEGACY_RESULT_SCHEMA_ID = "vibe-project-lead-zh-delegation-result-v1"
BRIEF_SCHEMA_ID = "vibe-project-lead-zh-delegation-brief-v2"
RESULT_SCHEMA_ID = "vibe-project-lead-zh-delegation-result-v2"
EVALUATION_SCHEMA_VERSION = 3
MAX_INPUT_BYTES = 4 * 1024 * 1024

CAPTURE_TIME_FIELDS = frozenset(
    {
        "bound_at_utc",
        "captured_at_utc",
        "generated_at_utc",
        "returned_at_utc",
    }
)
BRIEF_FIELDS = frozenset(
    {
        "schema_id",
        "delegation_id",
        "parent_goal",
        "objective",
        "non_goals",
        "authority_project",
        "baseline",
        "mode",
        "read_scope",
        "write_allowlist",
        "allowed_commands",
        "prohibited_actions",
        "budget",
        "stop_conditions",
        "deliverables",
        "acceptance",
        "expiry",
        "reception",
    }
)
AUTHORITY_PROJECT_FIELDS = frozenset(
    {
        "logical_path",
        "physical_path",
        "git_top_level",
        "git_dir",
        "git_common_dir",
        "worktree_id",
    }
)
BASELINE_FIELDS = frozenset(
    {
        "main_thread_identity",
        "authority_identity",
        "required_file_hashes",
    }
)
BUDGET_FIELDS = frozenset(
    {
        "max_agents",
        "max_turns_per_agent",
        "max_wall_time_minutes",
        "max_model_calls",
        "model_policy",
        "token_budget",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema_id",
        "delegation_id",
        "status",
        "mode",
        "files_read",
        "files_modified",
        "commands",
        "source_baseline_sha256",
        "brief_sha256",
        "return_baseline",
        "findings",
        "candidate",
        "tests",
        "unknowns",
        "warnings",
        "scope_requests",
        "usage",
        "nested_delegation_attempted",
        "external_action_attempted",
        "recommendation",
        "returned_at_utc",
    }
)
FINDING_FIELDS = frozenset(
    {"finding_id", "severity", "summary", "evidence", "file", "line"}
)
TEST_FIELDS = frozenset({"command", "exit_code", "status", "summary"})
USAGE_FIELDS = frozenset(
    {"turns", "wall_time_seconds", "model_calls", "observed_tokens"}
)
CANDIDATE_FIELDS = frozenset(
    {"parent_head", "head", "commit", "diff_sha256", "changed_files"}
)
MODES = frozenset({"READ_ONLY", "REVIEW", "ISOLATED_WRITER"})
MODEL_POLICIES = frozenset({"USER_APPROVED", "RUNTIME_DEFAULT"})
RESULT_STATUSES = frozenset({"RETURNED", "STOPPED", "FAILED", "UNKNOWN"})
FINDING_SEVERITIES = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM", "LOW", "WARNING"}
)
TEST_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN", "NOT_RUN"})
RECOMMENDATIONS = frozenset(
    {"ACCEPT_FOR_MAIN_THREAD_REVIEW", "REJECT", "REINVESTIGATE"}
)
REQUIRED_PROHIBITIONS = frozenset(
    {
        "NESTED_DELEGATION",
        "ACCOUNT_OR_CREDENTIAL_ACCESS",
        "NETWORK_OR_EXTERNAL_SERVICE",
        "INSTALL_OR_CODEX_HOME_CHANGE",
        "PUSH_PR_RELEASE",
        "DEPLOYMENT",
        "MAIN_THREAD_DECISION",
        "WRITE_OUTSIDE_ALLOWLIST",
    }
)
READ_ONLY_PROHIBITIONS = frozenset({"FILESYSTEM_OR_GIT_METADATA_WRITE"})
REQUIRED_STOP_CONDITIONS = frozenset(
    {
        "BASELINE_DRIFT",
        "IDENTITY_INCOMPLETE_OR_BLOCKED",
        "ALLOWLIST_EXPANSION_REQUIRED",
        "BUDGET_EXHAUSTED",
        "EXTERNAL_PERMISSION_REQUIRED",
        "NESTED_DELEGATION_REQUESTED",
        "UNKNOWN_RESULT",
    }
)
REQUIRED_EXPIRY = frozenset(
    {
        "TASK_INTERRUPTED_OR_RESUMED",
        "PROJECT_OR_WORKTREE_CHANGED",
        "HEAD_CHANGED",
        "DIRTY_FINGERPRINT_CHANGED",
        "APPROVAL_SCOPE_CHANGED",
        "RUNTIME_CAPABILITY_CHANGED",
    }
)
DELEGATION_ID_PATTERN = re.compile(
    r"^D-1\.2-NATIVE-(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})/"
    r"(?:0[1-9]|[1-9][0-9])$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_HASH_PATTERN = re.compile(r"^[0-9a-f]{40}$")
UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
RECEPTION_FIELDS = frozenset({"task_id", "resume_policy"})
RECEPTION_CONTEXT_FIELDS = frozenset({
    "brief_sha256", "result_sha256", "current_task_id",
    "events", "events_complete", "evidence_complete",
})
RESUME_EVENTS = frozenset({"CONTEXT_COMPACTION", "SAME_TASK_RESUME"})
INVALIDATING_EVENTS = frozenset({
    "USER_PAUSED", "USER_CANCELLED", "APPROVAL_REVOKED", "TASK_SWITCH",
    "PROJECT_SWITCH", "TASK_INTERRUPTED",
})
LIFECYCLE_EXPIRY = frozenset({
    "TASK_ID_CHANGED", "USER_PAUSED_OR_CANCELLED", "APPROVAL_REVOKED",
    "LIFECYCLE_EVIDENCE_INCOMPLETE", "TASK_INTERRUPTED",
})


class DelegationContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _without_capture_time(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_capture_time(item)
            for key, item in value.items()
            if key not in CAPTURE_TIME_FIELDS
        }
    if isinstance(value, list):
        return [_without_capture_time(item) for item in value]
    return value


def canonical_contract_digest(value: object) -> str:
    """Return canonical SHA-256 after removing known capture-time fields."""
    encoded = json.dumps(
        _without_capture_time(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject(reason: str) -> None:
    raise DelegationContractError(reason)


def _require_exact_fields(value: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject("FIELD_TYPE_INVALID")
    if frozenset(value) != fields:
        _reject("SCHEMA_FIELDS_CHANGED")
    return value


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _require_text(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _reject("FIELD_TYPE_INVALID")
    if (not allow_empty and not value) or _has_control(value):
        _reject("FIELD_VALUE_INVALID")
    return value


def _require_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        _reject("FIELD_TYPE_INVALID")
    result: list[str] = []
    for item in value:
        result.append(_require_text(item))
    if len(set(result)) != len(result):
        _reject("FIELD_VALUE_INVALID")
    return result


def _require_repo_path(value: object) -> str:
    path = _require_text(value)
    if (
        path.startswith("/")
        or "\\" in path
        or any(character in path for character in "*?[]{}")
    ):
        _reject("FIELD_VALUE_INVALID")
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _reject("FIELD_VALUE_INVALID")
    return path


def _require_scope(value: object) -> list[str]:
    paths = _require_string_list(value)
    for path in paths:
        _require_repo_path(path)
    return paths


def _require_absolute_text_path(value: object) -> str:
    path = _require_text(value)
    if not path.startswith("/") or "\\" in path:
        _reject("FIELD_VALUE_INVALID")
    if any(part in {".", ".."} for part in PurePosixPath(path).parts):
        _reject("FIELD_VALUE_INVALID")
    return path


def _paths_overlap(paths: list[str]) -> bool:
    split = [PurePosixPath(path).parts for path in paths]
    for index, left in enumerate(split):
        for right in split[index + 1 :]:
            common = min(len(left), len(right))
            if left[:common] == right[:common]:
                return True
    return False


def _project_identity_module():
    if __package__:
        from workbench import project_identity
    else:
        import project_identity

    return project_identity


def _project_freshness_module():
    if __package__:
        from workbench import project_freshness
    else:
        import project_freshness

    return project_freshness


def _validate_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject("IDENTITY_SCHEMA_INVALID")
    project_identity = _project_identity_module()
    try:
        project_identity.validate_identity_schema(
            value,
            supported_versions=(2,),
        )
    except project_identity.IdentitySchemaError:
        _reject("IDENTITY_SCHEMA_INVALID")
    if (
        value.get("status") != "bound"
        or value.get("reason") is not None
        or value.get("binding_kind") != "GIT_WORKTREE"
        or value.get("is_git") is not True
        or value.get("write_eligibility") != "ELIGIBLE"
        or value.get("fingerprint_complete") is not True
        or value.get("fingerprint_applicability") != "REQUIRED"
        or value.get("fingerprint_reason") is not None
    ):
        _reject("IDENTITY_NOT_STABLE_WRITABLE_CANDIDATE")
    try:
        project_identity.validate_git_worktree_facts(value)
    except project_identity.IdentitySchemaError:
        _reject("IDENTITY_SCHEMA_INVALID")
    return value


def _validate_authority_project(
    value: object,
    authority_identity: dict[str, object],
) -> None:
    authority = _require_exact_fields(value, AUTHORITY_PROJECT_FIELDS)
    for field in AUTHORITY_PROJECT_FIELDS:
        if field == "worktree_id":
            _require_text(authority[field])
        else:
            _require_absolute_text_path(authority[field])
        if authority[field] != authority_identity.get(field):
            _reject("AUTHORITY_IDENTITY_MISMATCH")


def _validate_required_file_hashes(value: object, read_scope: list[str]) -> None:
    if not isinstance(value, dict):
        _reject("FIELD_TYPE_INVALID")
    if set(value) != set(read_scope):
        _reject("SCHEMA_FIELDS_CHANGED")
    for path, digest in value.items():
        _require_repo_path(path)
        if not isinstance(digest, str):
            _reject("FIELD_TYPE_INVALID")
        if not SHA256_PATTERN.fullmatch(digest):
            _reject("FIELD_VALUE_INVALID")


def _validate_commands(value: object) -> None:
    if not isinstance(value, list):
        _reject("FIELD_TYPE_INVALID")
    seen: set[tuple[str, ...]] = set()
    for command in value:
        if not isinstance(command, list) or not command:
            _reject("FIELD_TYPE_INVALID")
        argv = tuple(_require_text(argument) for argument in command)
        if argv in seen:
            _reject("FIELD_VALUE_INVALID")
        seen.add(argv)


def _validate_budget(value: object) -> None:
    budget = _require_exact_fields(value, BUDGET_FIELDS)
    for field in (
        "max_agents",
        "max_turns_per_agent",
        "max_wall_time_minutes",
        "max_model_calls",
    ):
        item = budget[field]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            _reject("BUDGET_INVALID")
    if (not isinstance(budget["model_policy"], str)
            or budget["model_policy"] not in MODEL_POLICIES):
        _reject("BUDGET_INVALID")
    token_budget = budget["token_budget"]
    if token_budget is not None and (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or token_budget <= 0
    ):
        _reject("BUDGET_INVALID")


def _validate_required_items(
    actual: list[str],
    required: frozenset[str],
    reason: str,
) -> None:
    if not required.issubset(actual):
        _reject(reason)


def validate_delegation_brief(value: object) -> dict[str, object]:
    """Validate v2, or parse v1 for diagnostics only; never migrate old approval."""
    if not isinstance(value, dict):
        _reject("INPUT_NOT_OBJECT")
    legacy = value.get("schema_id") == LEGACY_BRIEF_SCHEMA_ID
    fields = BRIEF_FIELDS - {"reception"} if legacy else BRIEF_FIELDS
    if frozenset(value) != fields:
        _reject("SCHEMA_FIELDS_CHANGED")
    brief = copy.deepcopy(value)

    if brief["schema_id"] not in (BRIEF_SCHEMA_ID, LEGACY_BRIEF_SCHEMA_ID):
        _reject("SCHEMA_UNSUPPORTED")
    delegation_id = _require_text(brief["delegation_id"])
    if not DELEGATION_ID_PATTERN.fullmatch(delegation_id):
        _reject("FIELD_VALUE_INVALID")
    _require_text(brief["parent_goal"])
    _require_text(brief["objective"])
    _require_string_list(brief["non_goals"])
    _require_string_list(brief["deliverables"])
    _require_string_list(brief["acceptance"])

    mode = brief["mode"]
    if not isinstance(mode, str):
        _reject("FIELD_TYPE_INVALID")
    if mode not in MODES:
        _reject("FIELD_VALUE_INVALID")

    read_scope = _require_scope(brief["read_scope"])
    write_allowlist = _require_scope(brief["write_allowlist"])
    _validate_commands(brief["allowed_commands"])

    baseline = _require_exact_fields(brief["baseline"], BASELINE_FIELDS)
    main_identity = _validate_identity(baseline["main_thread_identity"])
    authority_identity = _validate_identity(baseline["authority_identity"])
    _validate_required_file_hashes(
        baseline["required_file_hashes"],
        read_scope,
    )
    _validate_authority_project(brief["authority_project"], authority_identity)

    if mode in {"READ_ONLY", "REVIEW"}:
        if write_allowlist:
            _reject("READ_ONLY_WRITE_ALLOWLIST_NOT_EMPTY")
    else:
        if not write_allowlist:
            _reject("WRITER_ALLOWLIST_EMPTY")
        if _paths_overlap(write_allowlist):
            _reject("FIELD_VALUE_INVALID")
        if main_identity["git_common_dir"] != authority_identity["git_common_dir"]:
            _reject("WRITER_REPOSITORY_MISMATCH")
        if (
            main_identity["worktree_id"] == authority_identity["worktree_id"]
            or main_identity["physical_path"] == authority_identity["physical_path"]
        ):
            _reject("WRITER_WORKTREE_NOT_ISOLATED")
        if main_identity["head"] != authority_identity["head"]:
            _reject("WRITER_PARENT_HEAD_MISMATCH")

    _validate_budget(brief["budget"])
    prohibited_actions = _require_string_list(brief["prohibited_actions"])
    stop_conditions = _require_string_list(brief["stop_conditions"])
    expiry = _require_string_list(brief["expiry"])
    _validate_required_items(
        prohibited_actions,
        REQUIRED_PROHIBITIONS,
        "REQUIRED_PROHIBITION_MISSING",
    )
    if mode in {"READ_ONLY", "REVIEW"}:
        _validate_required_items(
            prohibited_actions,
            READ_ONLY_PROHIBITIONS,
            "REQUIRED_PROHIBITION_MISSING",
        )
    _validate_required_items(
        stop_conditions,
        REQUIRED_STOP_CONDITIONS,
        "REQUIRED_STOP_CONDITION_MISSING",
    )
    required_expiry = REQUIRED_EXPIRY
    if not legacy:
        reception = _require_exact_fields(brief["reception"], RECEPTION_FIELDS)
        task_id = _require_text(reception["task_id"])
        policy = _require_text(reception["resume_policy"])
        if not TASK_ID_PATTERN.fullmatch(task_id):
            _reject("FIELD_VALUE_INVALID")
        if policy not in {"EXPIRE", "REVALIDATE_READ_ONLY"}:
            _reject("FIELD_VALUE_INVALID")
        required_expiry = REQUIRED_EXPIRY | LIFECYCLE_EXPIRY
        if policy == "REVALIDATE_READ_ONLY":
            if mode not in {"READ_ONLY", "REVIEW"}:
                _reject("RECEPTION_POLICY_MODE_MISMATCH")
            if "TASK_INTERRUPTED_OR_RESUMED" in expiry:
                _reject("RECEPTION_POLICY_EXPIRY_CONFLICT")
            required_expiry = required_expiry - {"TASK_INTERRUPTED_OR_RESUMED"}
    _validate_required_items(expiry, required_expiry, "REQUIRED_EXPIRY_MISSING")
    return brief


def render_review_command(
    brief: object, command: object, *, transport: str
) -> str:
    """Encode one approved argv for a verified POSIX native transport; never run it."""
    validated = validate_delegation_brief(brief)
    if validated["mode"] not in {"READ_ONLY", "REVIEW"}:
        _reject("REVIEW_COMMAND_MODE_REQUIRED")
    if transport != "POSIX_SHELL_STRING":
        _reject("COMMAND_TRANSPORT_UNSUPPORTED")
    _validate_commands([command])
    if command not in validated["allowed_commands"]:
        _reject("COMMAND_NOT_ALLOWLISTED")
    program = PurePosixPath(command[0]).name.casefold()
    if program in {
        "sh", "bash", "dash", "zsh", "ksh", "fish", "csh", "tcsh",
        "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
        "eval", "exec", "source", ".", "env",
    } or command[0].startswith("-"):
        _reject("SHELL_COMMAND_FORBIDDEN")
    return "exec " + shlex.join(command)


def _require_sha256(value: object) -> str:
    if not isinstance(value, str):
        _reject("FIELD_TYPE_INVALID")
    if not SHA256_PATTERN.fullmatch(value):
        _reject("FIELD_VALUE_INVALID")
    return value


def _require_git_hash(value: object, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        _reject("FIELD_TYPE_INVALID")
    if not GIT_HASH_PATTERN.fullmatch(value):
        _reject("FIELD_VALUE_INVALID")
    return value


def _require_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _reject("FIELD_TYPE_INVALID")
    if value < 0:
        _reject("FIELD_VALUE_INVALID")
    return value


def _validate_findings(value: object) -> None:
    if not isinstance(value, list):
        _reject("FIELD_TYPE_INVALID")
    finding_ids: set[str] = set()
    for item in value:
        finding = _require_exact_fields(item, FINDING_FIELDS)
        finding_id = _require_text(finding["finding_id"])
        if finding_id in finding_ids:
            _reject("FIELD_VALUE_INVALID")
        finding_ids.add(finding_id)
        severity = finding["severity"]
        if not isinstance(severity, str):
            _reject("FIELD_TYPE_INVALID")
        if severity not in FINDING_SEVERITIES:
            _reject("FIELD_VALUE_INVALID")
        _require_text(finding["summary"])
        _require_string_list(finding["evidence"])
        file = finding["file"]
        if file is not None:
            _require_repo_path(file)
        line = finding["line"]
        if line is not None:
            if isinstance(line, bool) or not isinstance(line, int):
                _reject("FIELD_TYPE_INVALID")
            if line <= 0:
                _reject("FIELD_VALUE_INVALID")


def _validate_tests(value: object) -> None:
    if not isinstance(value, list):
        _reject("FIELD_TYPE_INVALID")
    for item in value:
        test = _require_exact_fields(item, TEST_FIELDS)
        _validate_commands([test["command"]])
        status = test["status"]
        if not isinstance(status, str):
            _reject("FIELD_TYPE_INVALID")
        if status not in TEST_STATUSES:
            _reject("FIELD_VALUE_INVALID")
        exit_code = test["exit_code"]
        if exit_code is not None:
            _require_nonnegative_int(exit_code)
        if status in {"PASS", "FAIL"} and exit_code is None:
            _reject("FIELD_VALUE_INVALID")
        if status == "PASS" and exit_code != 0:
            _reject("FIELD_VALUE_INVALID")
        if status == "NOT_RUN" and exit_code is not None:
            _reject("FIELD_VALUE_INVALID")
        _require_text(test["summary"])


def _validate_candidate(value: object) -> None:
    if value is None:
        return
    candidate = _require_exact_fields(value, CANDIDATE_FIELDS)
    _require_git_hash(candidate["parent_head"])
    _require_git_hash(candidate["head"])
    _require_git_hash(candidate["commit"], allow_none=True)
    _require_sha256(candidate["diff_sha256"])
    _require_scope(candidate["changed_files"])


def _validate_usage(value: object) -> None:
    usage = _require_exact_fields(value, USAGE_FIELDS)
    for field in ("turns", "wall_time_seconds", "model_calls"):
        _require_nonnegative_int(usage[field])
    observed_tokens = usage["observed_tokens"]
    if observed_tokens == "UNKNOWN":
        return
    _require_nonnegative_int(observed_tokens)


def _validate_utc_timestamp(value: object) -> None:
    timestamp = _require_text(value)
    if not UTC_TIMESTAMP_PATTERN.fullmatch(timestamp):
        _reject("FIELD_VALUE_INVALID")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _reject("FIELD_VALUE_INVALID")


def validate_delegation_result(value: object) -> dict[str, object]:
    """Validate v2 or parse historical v1 without making it receivable."""
    if not isinstance(value, dict):
        _reject("INPUT_NOT_OBJECT")
    legacy = value.get("schema_id") == LEGACY_RESULT_SCHEMA_ID
    fields = RESULT_FIELDS - {"brief_sha256"} if legacy else RESULT_FIELDS
    if frozenset(value) != fields:
        _reject("SCHEMA_FIELDS_CHANGED")
    result = copy.deepcopy(value)

    if result["schema_id"] not in (RESULT_SCHEMA_ID, LEGACY_RESULT_SCHEMA_ID):
        _reject("SCHEMA_UNSUPPORTED")
    if not legacy:
        _require_sha256(result["brief_sha256"])
    delegation_id = _require_text(result["delegation_id"])
    if not DELEGATION_ID_PATTERN.fullmatch(delegation_id):
        _reject("FIELD_VALUE_INVALID")
    status = result["status"]
    if not isinstance(status, str):
        _reject("FIELD_TYPE_INVALID")
    if status not in RESULT_STATUSES:
        _reject("FIELD_VALUE_INVALID")
    mode = result["mode"]
    if not isinstance(mode, str):
        _reject("FIELD_TYPE_INVALID")
    if mode not in MODES:
        _reject("FIELD_VALUE_INVALID")

    _require_scope(result["files_read"])
    _require_scope(result["files_modified"])
    _validate_commands(result["commands"])
    _require_sha256(result["source_baseline_sha256"])
    return_baseline = result["return_baseline"]
    if not isinstance(return_baseline, dict):
        _reject("IDENTITY_SCHEMA_INVALID")
    project_identity = _project_identity_module()
    try:
        project_identity.validate_identity_schema(
            return_baseline,
            supported_versions=(2,),
        )
    except project_identity.IdentitySchemaError:
        _reject("IDENTITY_SCHEMA_INVALID")

    _validate_findings(result["findings"])
    _validate_candidate(result["candidate"])
    _validate_tests(result["tests"])
    _require_string_list(result["unknowns"])
    _require_string_list(result["warnings"])
    _require_string_list(result["scope_requests"])
    _validate_usage(result["usage"])
    if not isinstance(result["nested_delegation_attempted"], bool):
        _reject("FIELD_TYPE_INVALID")
    if not isinstance(result["external_action_attempted"], bool):
        _reject("FIELD_TYPE_INVALID")
    recommendation = result["recommendation"]
    if not isinstance(recommendation, str):
        _reject("FIELD_TYPE_INVALID")
    if recommendation not in RECOMMENDATIONS:
        _reject("FIELD_VALUE_INVALID")
    _validate_utc_timestamp(result["returned_at_utc"])
    return result


def _evaluation(
    delegation_id: str,
    freshness: str,
    decision: str,
    reasons: list[str],
    *,
    consumption_record: dict[str, str] | None = None,
) -> dict[str, object]:
    consumable = (
        freshness == "FRESH"
        and decision == "ACCEPTED_FOR_MAIN_THREAD_REVIEW"
        and consumption_record is not None
    )
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "delegation_id": delegation_id,
        "freshness": freshness,
        "decision": decision,
        "reasons": sorted(set(reasons)),
        "consumable": consumable,
        "consumption_record": consumption_record if consumable else None,
        "write_authorized": False,
    }


def _result_contract_reasons(
    brief: dict[str, object],
    result: dict[str, object],
) -> list[str]:
    reasons: list[str] = []
    baseline = brief["baseline"]
    authority_source = baseline["authority_identity"]
    if (
        result["delegation_id"] != brief["delegation_id"]
        or result["mode"] != brief["mode"]
        or result["source_baseline_sha256"]
        != canonical_contract_digest(authority_source)
        or (result["schema_id"] == RESULT_SCHEMA_ID
            and result["brief_sha256"] != canonical_contract_digest(brief))
    ):
        reasons.append("RESULT_BRIEF_MISMATCH")

    if not set(result["files_read"]).issubset(brief["read_scope"]):
        reasons.append("RESULT_SCOPE_VIOLATION")
    if not set(result["files_modified"]).issubset(brief["write_allowlist"]):
        reasons.append("RESULT_SCOPE_VIOLATION")
    allowed_commands = {tuple(command) for command in brief["allowed_commands"]}
    actual_commands = {tuple(command) for command in result["commands"]}
    if not actual_commands.issubset(allowed_commands):
        reasons.append("RESULT_SCOPE_VIOLATION")
    for test in result["tests"]:
        command = tuple(test["command"])
        if command not in allowed_commands:
            reasons.append("RESULT_SCOPE_VIOLATION")
        if test["status"] in {"PASS", "FAIL"} and command not in actual_commands:
            reasons.append("RESULT_SCOPE_VIOLATION")

    mode = brief["mode"]
    candidate = result["candidate"]
    if mode in {"READ_ONLY", "REVIEW"}:
        if result["files_modified"] or candidate is not None:
            reasons.append("RESULT_SCOPE_VIOLATION")
    elif not isinstance(candidate, dict):
        reasons.append("RESULT_SCOPE_VIOLATION")
    else:
        changed_files = set(candidate["changed_files"])
        if (
            changed_files != set(result["files_modified"])
            or not changed_files.issubset(brief["write_allowlist"])
        ):
            reasons.append("RESULT_SCOPE_VIOLATION")
        return_baseline = result["return_baseline"]
        if (
            candidate["parent_head"] != authority_source["head"]
            or candidate["head"] != return_baseline["head"]
            or return_baseline["worktree_id"] != authority_source["worktree_id"]
            or return_baseline["git_common_dir"]
            != authority_source["git_common_dir"]
        ):
            reasons.append("RESULT_BRIEF_MISMATCH")

    usage = result["usage"]
    budget = brief["budget"]
    if (
        usage["turns"] > budget["max_turns_per_agent"]
        or usage["wall_time_seconds"] > budget["max_wall_time_minutes"] * 60
        or usage["model_calls"] > budget["max_model_calls"]
    ):
        reasons.append("RESULT_BUDGET_EXCEEDED")
    observed_tokens = usage["observed_tokens"]
    token_budget = budget["token_budget"]
    if (
        isinstance(observed_tokens, int)
        and not isinstance(observed_tokens, bool)
        and isinstance(token_budget, int)
        and not isinstance(token_budget, bool)
        and observed_tokens > token_budget
    ):
        reasons.append("RESULT_BUDGET_EXCEEDED")
    if result["external_action_attempted"]:
        reasons.append("RESULT_EXTERNAL_ACTION")
    if result["nested_delegation_attempted"]:
        reasons.append("RESULT_NESTED_DELEGATION")
    return sorted(set(reasons))


def _identity_evaluation_reasons(
    brief: dict[str, object],
    result: dict[str, object],
    current_main_identity: dict[str, object],
    current_authority_identity: dict[str, object],
) -> tuple[list[str], list[str]]:
    project_freshness = _project_freshness_module()
    baseline = brief["baseline"]
    comparisons: list[tuple[str, dict[str, object]]] = [
        (
            "MAIN",
            project_freshness.compare_identity(
                baseline["main_thread_identity"],
                current_main_identity,
            ),
        ),
        (
            "AUTHORITY",
            project_freshness.compare_identity(
                result["return_baseline"],
                current_authority_identity,
            ),
        ),
    ]
    if brief["mode"] in {"READ_ONLY", "REVIEW"}:
        comparisons.append(
            (
                "AUTHORITY",
                project_freshness.compare_identity(
                    baseline["authority_identity"],
                    result["return_baseline"],
                ),
            )
        )

    incomplete: list[str] = []
    stale: list[str] = []
    for label, comparison in comparisons:
        state = comparison["state"]
        if state in {"UNKNOWN", "AMBIGUOUS"}:
            incomplete.extend(comparison["reasons"])
            incomplete.append(f"{label}_IDENTITY_INCOMPLETE")
        elif state == "STALE":
            stale.extend(comparison["reasons"])
            stale.append(f"{label}_BASELINE_STALE")
    return sorted(set(incomplete)), sorted(set(stale))


def build_runtime_context(
    brief: object,
    *,
    baseline_runtime: object,
    current_runtime: object,
    current_main_identity: object,
) -> dict[str, object]:
    """Pin explicit runtime evidence to a brief without approving or consuming it."""
    validated = validate_delegation_brief(brief)
    current = _validate_identity(current_main_identity)
    comparison = _project_freshness_module().compare_runtime(
        baseline_runtime, current_runtime,
        baseline_surface=validated["baseline"]["main_thread_identity"]["runtime_surface"],
        current_surface=current["runtime_surface"],
    )
    if comparison["state"] == "UNKNOWN":
        _reject("RUNTIME_CONTEXT_INVALID")
    # STALE evidence is retained so the consumer can explain and reject drift.
    return {
        "brief_sha256": canonical_contract_digest(validated),
        "baseline": copy.deepcopy(baseline_runtime),
        "current": copy.deepcopy(current_runtime),
    }


def _reception_evaluation_reasons(
    brief: dict[str, object], result: dict[str, object], context: object,
) -> tuple[list[str], list[str]]:
    """Check caller-supplied lifecycle evidence, not its origin or authenticity.

    The main thread must verify the complete dispatch-to-reception trace, the
    actual approved brief, command evidence and returned result. This function
    cannot discover omitted events or recover consumed model-call permission.
    """
    if brief["schema_id"] != BRIEF_SCHEMA_ID or result["schema_id"] != RESULT_SCHEMA_ID:
        return ["LEGACY_CONTRACT_REQUIRES_NEW_DISPATCH"], []
    if (
        not isinstance(context, dict)
        or frozenset(context) != RECEPTION_CONTEXT_FIELDS
        or context["brief_sha256"] != canonical_contract_digest(brief)
        or context["result_sha256"] != canonical_contract_digest(result)
        or context["events_complete"] is not True
        or context["evidence_complete"] is not True
        or not isinstance(context["current_task_id"], str)
        or not TASK_ID_PATTERN.fullmatch(context["current_task_id"])
        or not isinstance(context["events"], list)
        or len(context["events"]) > 256
        or any(not isinstance(event, str) or event not in RESUME_EVENTS | INVALIDATING_EVENTS
               for event in context["events"])
    ):
        return ["RECEPTION_CONTEXT_INVALID"], []
    stale = []
    if context["current_task_id"] != brief["reception"]["task_id"]:
        stale.append("RECEPTION_TASK_CHANGED")
    events = set(context["events"])
    stale.extend("RECEPTION_INVALIDATED:" + event for event in events & INVALIDATING_EVENTS)
    if events & RESUME_EVENTS and brief["reception"]["resume_policy"] != "REVALIDATE_READ_ONLY":
        stale.append("RECEPTION_RESUME_NOT_ALLOWED")
    return [], stale


def evaluate_delegation_result(
    brief: object,
    result: object,
    *,
    current_main_identity: dict[str, object],
    current_authority_identity: dict[str, object],
    consumed_delegation_ids: frozenset[str] = frozenset(),
    runtime_context: dict[str, object] | None = None,
    reception_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a pure decision using main-thread runtime and lifecycle evidence.

    runtime_context contains exactly brief_sha256, baseline and current. Capture
    baseline before dispatch and current before consumption; neither may be
    inferred from the agent result. Missing evidence does not inherit old approval.
    reception_context binds the exact brief/result and complete lifecycle trace;
    it is supplied by the main thread, not inferred or certified by this function.
    Acceptance permits main-thread review only, never writes or additional calls.
    """
    delegation_id = ""
    if isinstance(result, dict) and isinstance(result.get("delegation_id"), str):
        delegation_id = result["delegation_id"]
    elif isinstance(brief, dict) and isinstance(brief.get("delegation_id"), str):
        delegation_id = brief["delegation_id"]
    try:
        validated_brief = validate_delegation_brief(brief)
        validated_result = validate_delegation_result(result)
    except DelegationContractError as error:
        return _evaluation(
            delegation_id,
            "CONTRACT_VIOLATION",
            "REJECTED",
            [error.reason],
        )

    delegation_id = validated_result["delegation_id"]
    contract_reasons = _result_contract_reasons(
        validated_brief,
        validated_result,
    )
    if contract_reasons:
        return _evaluation(
            delegation_id,
            "CONTRACT_VIOLATION",
            "REJECTED",
            contract_reasons,
        )

    incomplete_reasons, lifecycle_stale = _reception_evaluation_reasons(
        validated_brief, validated_result, reception_context,
    )
    if validated_result["status"] != "RETURNED":
        incomplete_reasons.append("RESULT_NOT_RETURNED")
    if validated_result["unknowns"]:
        incomplete_reasons.append("RESULT_HAS_UNKNOWNS")
    if validated_result["scope_requests"]:
        incomplete_reasons.append("RESULT_HAS_PENDING_SCOPE_REQUEST")
    test_statuses = [test["status"] for test in validated_result["tests"]]
    if not test_statuses or any(
        status in {"UNKNOWN", "NOT_RUN"} for status in test_statuses
    ):
        incomplete_reasons.append("RESULT_TEST_EVIDENCE_INCOMPLETE")
    if any(status == "FAIL" for status in test_statuses):
        incomplete_reasons.append("RESULT_TEST_FAILED")

    identity_incomplete, identity_stale = _identity_evaluation_reasons(
        validated_brief,
        validated_result,
        current_main_identity,
        current_authority_identity,
    )
    incomplete_reasons.extend(identity_incomplete)
    identity_stale.extend(lifecycle_stale)
    if (
        not isinstance(runtime_context, dict)
        or set(runtime_context) != {"brief_sha256", "baseline", "current"}
        or runtime_context["brief_sha256"] != canonical_contract_digest(validated_brief)
    ):
        incomplete_reasons.append("RUNTIME_CONTEXT_INVALID")
    else:
        runtime_state = _project_freshness_module().compare_runtime(
            runtime_context["baseline"], runtime_context["current"],
            baseline_surface=validated_brief["baseline"]["main_thread_identity"]["runtime_surface"],
            current_surface=(current_main_identity.get("runtime_surface")
                             if isinstance(current_main_identity, dict) else None),
        )
        if runtime_state["state"] == "UNKNOWN":
            incomplete_reasons.extend(runtime_state["reasons"])
        elif runtime_state["state"] == "STALE":
            identity_stale.extend(runtime_state["reasons"])
    if incomplete_reasons:
        return _evaluation(
            delegation_id,
            "INCOMPLETE",
            "REJECTED",
            incomplete_reasons,
        )
    if identity_stale:
        return _evaluation(
            delegation_id,
            "STALE",
            "REJECTED",
            identity_stale,
        )

    if delegation_id in consumed_delegation_ids:
        return _evaluation(
            delegation_id,
            "FRESH",
            "ALREADY_CONSUMED",
            ["DELEGATION_ALREADY_CONSUMED"],
        )

    consumption_record = {
        "delegation_id": delegation_id,
        "brief_sha256": canonical_contract_digest(validated_brief),
        "result_sha256": canonical_contract_digest(validated_result),
        "return_baseline_sha256": canonical_contract_digest(
            validated_result["return_baseline"]
        ),
        "runtime_context_sha256": canonical_contract_digest(runtime_context),
        "reception_context_sha256": canonical_contract_digest(reception_context),
    }
    return _evaluation(
        delegation_id,
        "FRESH",
        "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
        [],
        consumption_record=consumption_record,
    )


def _descriptor_identity(file_stat: os.stat_result) -> tuple[object, ...]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        stat.S_IFMT(file_stat.st_mode),
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _reject("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _read_json_regular(path: Path) -> object:
    """Read one bounded regular JSON file through a stable descriptor."""
    if not hasattr(os, "O_NOFOLLOW"):
        _reject("FIELD_VALUE_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError:
        _reject("FIELD_VALUE_INVALID")

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _reject("FIELD_VALUE_INVALID")
        if before.st_size < 0 or before.st_size > MAX_INPUT_BYTES:
            _reject("FIELD_VALUE_INVALID")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_INPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                _reject("FIELD_VALUE_INVALID")

        after = os.fstat(descriptor)
        if (
            _descriptor_identity(before) != _descriptor_identity(after)
            or total != before.st_size
        ):
            _reject("FIELD_VALUE_INVALID")
        data = b"".join(chunks)
    except OSError:
        _reject("FIELD_VALUE_INVALID")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("FIELD_VALUE_INVALID")


def _write_exclusive(path: Path, data: bytes) -> None:
    """Create one regular output without following or replacing a path."""
    if not hasattr(os, "O_NOFOLLOW"):
        _reject("FIELD_VALUE_INVALID")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )
    try:
        descriptor = os.open(os.fspath(path), flags, 0o600)
    except OSError:
        _reject("FIELD_VALUE_INVALID")

    completed = False
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        completed = True
    except OSError:
        _reject("FIELD_VALUE_INVALID")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            completed = False
        if not completed:
            try:
                os.unlink(path)
            except OSError:
                pass


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class _ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        _reject("FIELD_VALUE_INVALID")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ContractArgumentParser(description="Validate delegation contracts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    brief_parser = subparsers.add_parser("validate-brief")
    brief_parser.add_argument("--brief", required=True, type=Path)
    brief_parser.add_argument("--output", type=Path)

    context_parser = subparsers.add_parser("prepare-runtime-context")
    for name in ("brief", "baseline-runtime", "current-runtime", "current-main-identity"):
        context_parser.add_argument("--" + name, required=True, type=Path)
    context_parser.add_argument("--output", type=Path)

    result_parser = subparsers.add_parser("evaluate-result")
    result_parser.add_argument("--brief", required=True, type=Path)
    result_parser.add_argument("--result", required=True, type=Path)
    result_parser.add_argument("--current-main-identity", required=True, type=Path)
    result_parser.add_argument(
        "--current-authority-identity",
        required=True,
        type=Path,
    )
    result_parser.add_argument("--consumed-id", action="append", default=[])
    result_parser.add_argument(
        "--runtime-context", type=Path,
        help="Explicit main-thread baseline/current runtime evidence pinned to the brief",
    )
    result_parser.add_argument(
        "--reception-context", type=Path,
        help="Main-thread checked material digests, task and complete lifecycle events",
    )
    result_parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _validate_current_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject("IDENTITY_SCHEMA_INVALID")
    project_identity = _project_identity_module()
    try:
        project_identity.validate_identity_schema(value, supported_versions=(2,))
    except project_identity.IdentitySchemaError:
        _reject("IDENTITY_SCHEMA_INVALID")
    return value


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        brief = validate_delegation_brief(_read_json_regular(args.brief))
        if args.command == "validate-brief":
            receipt: object = brief
            exit_code = 0
        elif args.command == "prepare-runtime-context":
            receipt = build_runtime_context(
                brief,
                baseline_runtime=_read_json_regular(args.baseline_runtime),
                current_runtime=_read_json_regular(args.current_runtime),
                current_main_identity=_read_json_regular(args.current_main_identity),
            )
            exit_code = 0
        else:
            result = validate_delegation_result(_read_json_regular(args.result))
            current_main = _validate_current_identity(
                _read_json_regular(args.current_main_identity)
            )
            current_authority = _validate_current_identity(
                _read_json_regular(args.current_authority_identity)
            )
            consumed_ids: set[str] = set()
            for delegation_id in args.consumed_id:
                if (
                    not isinstance(delegation_id, str)
                    or not DELEGATION_ID_PATTERN.fullmatch(delegation_id)
                ):
                    _reject("FIELD_VALUE_INVALID")
                consumed_ids.add(delegation_id)
            receipt = evaluate_delegation_result(
                brief,
                result,
                current_main_identity=current_main,
                current_authority_identity=current_authority,
                consumed_delegation_ids=frozenset(consumed_ids),
                runtime_context=(_read_json_regular(args.runtime_context)
                                 if args.runtime_context else None),
                reception_context=(_read_json_regular(args.reception_context)
                                   if args.reception_context else None),
            )
            exit_code = (
                0
                if receipt["decision"] == "ACCEPTED_FOR_MAIN_THREAD_REVIEW"
                else 3
            )

        payload = _canonical_json_bytes(receipt)
        if args.output is not None:
            _write_exclusive(args.output, payload)
        sys.stdout.write(payload.decode("utf-8"))
        return exit_code
    except DelegationContractError as error:
        sys.stderr.write(f"delegation contract rejected: {error.reason}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
