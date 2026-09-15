#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build fail-closed, read-only multi-project Portfolio snapshots."""

from __future__ import annotations

import copy
import argparse
import hashlib
import json
import ntpath
import os
import posixpath
import re
import stat
import sys
import tomllib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from workbench import project_freshness, project_identity
else:
    import project_freshness
    import project_identity


REGISTRY_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
STATUS_SOURCE_SCHEMA = "JSON_STATUS_V1"
MAX_REGISTRY_BYTES = 262_144
MAX_STATUS_SOURCE_BYTES = 262_144
MAX_BASELINE_BYTES = 262_144
MAX_UNTRACKED_BYTES = 4 * 1024 * 1024

_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "portfolio_id", "display_name", "projects"}
)
_PROJECT_BASE_FIELDS = frozenset(
    {
        "id",
        "display_name",
        "path",
        "priority",
        "lifecycle",
        "depends_on",
        "status_source_schema",
    }
)
_PROJECT_STATUS_FIELDS = _PROJECT_BASE_FIELDS | {"status_source_path"}
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_LIFECYCLES = {
    "ACTIVE",
    "PAUSED",
    "WAITING_APPROVAL",
    "RELEASE_READY",
    "ARCHIVED",
}
_STATUS_FIELDS = frozenset(
    {
        "schema_version",
        "milestone",
        "milestone_state",
        "state",
        "blockers",
        "next_step",
        "milestone_value",
        "completion_confidence",
        "blocker_cost",
        "task_size",
        "approval_wait",
        "release_window",
        "risk_level",
        "updated_at_utc",
    }
)
_STATUS_NUMERIC_FIELDS = (
    "milestone_value",
    "completion_confidence",
    "blocker_cost",
    "task_size",
    "approval_wait",
    "release_window",
    "risk_level",
)
_MILESTONE_STATES = {"NOT_STARTED", "IN_PROGRESS", "COMPLETE", "BLOCKED"}
_STATUS_STATES = _LIFECYCLES | {"BLOCKED"}
_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema_version",
        "portfolio_id",
        "registry_sha256",
        "generated_at_utc",
        "portfolio_state",
        "write_authorized",
        "projects",
        "snapshot_sha256",
    }
)
_SNAPSHOT_PROJECT_FIELDS = frozenset(
    {
        "id",
        "display_name",
        "configured_path",
        "lifecycle",
        "priority",
        "depends_on",
        "capture_state",
        "identity",
        "status_source",
        "freshness",
        "recommendation",
    }
)
_STATUS_OUTPUT_FIELDS = frozenset(
    {
        "schema",
        "path",
        "content_sha256",
        "state",
        "reason",
        "milestone",
        "milestone_state",
        "blockers",
        "next_step",
        "milestone_value",
        "completion_confidence",
        "blocker_cost",
        "task_size",
        "approval_wait",
        "release_window",
        "risk_level",
        "updated_at_utc",
    }
)
_FRESHNESS_FIELDS = frozenset({"state", "reasons", "changed_fields", "verified_at_utc"})
_RECOMMENDATION_FIELDS = frozenset({"bucket", "rank", "rationale", "confidence"})


class PortfolioContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def canonical_portfolio_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject(reason: str) -> None:
    raise PortfolioContractError(reason)


def _valid_text(value: object, *, minimum: int, maximum: int) -> bool:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        return False
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return False
    return not any(unicodedata.category(character) == "Cc" for character in value)


def _require_text(value: object, *, minimum: int, maximum: int) -> str:
    if not _valid_text(value, minimum=minimum, maximum=maximum):
        _reject("FIELD_VALUE_INVALID")
    return value


def _normalized_absolute_path(value: object) -> str:
    path = _require_text(value, minimum=1, maximum=4096)
    kind = project_identity.classify_path_input(path)
    if kind == "UNSUPPORTED_OR_RELATIVE":
        _reject("FIELD_VALUE_INVALID")
    if kind == "WSL_POSIX_ABSOLUTE":
        return posixpath.normpath(path)
    return ntpath.normcase(ntpath.normpath(path))


def _valid_status_path(value: object) -> bool:
    if not _valid_text(value, minimum=1, maximum=4096):
        return False
    if value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _validate_dependencies(projects: list[dict[str, object]]) -> None:
    project_ids = {project["id"] for project in projects}
    graph: dict[str, list[str]] = {}
    for project in projects:
        project_id = project["id"]
        dependencies = project["depends_on"]
        if project_id in dependencies or len(set(dependencies)) != len(dependencies):
            _reject("FIELD_VALUE_INVALID")
        if any(dependency not in project_ids for dependency in dependencies):
            _reject("REGISTRY_DEPENDENCY_UNKNOWN")
        graph[project_id] = dependencies

    states: dict[str, int] = {}

    def visit(project_id: str) -> None:
        state = states.get(project_id, 0)
        if state == 1:
            _reject("REGISTRY_DEPENDENCY_CYCLE")
        if state == 2:
            return
        states[project_id] = 1
        for dependency in graph[project_id]:
            visit(dependency)
        states[project_id] = 2

    for project_id in sorted(graph):
        visit(project_id)


def validate_registry(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject("INPUT_NOT_OBJECT")
    if value.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        _reject("SCHEMA_UNSUPPORTED")
    if frozenset(value) != _TOP_LEVEL_FIELDS:
        _reject("SCHEMA_FIELDS_CHANGED")
    if not _ID_PATTERN.fullmatch(str(value.get("portfolio_id", ""))):
        _reject("FIELD_VALUE_INVALID")
    _require_text(value.get("display_name"), minimum=1, maximum=120)
    projects_value = value.get("projects")
    if not isinstance(projects_value, list):
        _reject("FIELD_TYPE_INVALID")
    if not projects_value:
        _reject("FIELD_VALUE_INVALID")

    projects: list[dict[str, object]] = []
    project_ids: set[str] = set()
    normalized_paths: set[str] = set()
    for raw_project in projects_value:
        if not isinstance(raw_project, dict):
            _reject("FIELD_TYPE_INVALID")
        schema = raw_project.get("status_source_schema")
        if schema not in {"NONE", STATUS_SOURCE_SCHEMA}:
            _reject("FIELD_VALUE_INVALID")
        expected_fields = (
            _PROJECT_STATUS_FIELDS if schema == STATUS_SOURCE_SCHEMA else _PROJECT_BASE_FIELDS
        )
        if frozenset(raw_project) != expected_fields:
            if frozenset(raw_project) in {_PROJECT_BASE_FIELDS, _PROJECT_STATUS_FIELDS}:
                _reject("FIELD_VALUE_INVALID")
            _reject("SCHEMA_FIELDS_CHANGED")

        project_id = raw_project.get("id")
        if not isinstance(project_id, str) or not _ID_PATTERN.fullmatch(project_id):
            _reject("FIELD_VALUE_INVALID")
        if project_id in project_ids:
            _reject("FIELD_VALUE_INVALID")
        project_ids.add(project_id)
        _require_text(raw_project.get("display_name"), minimum=1, maximum=120)

        normalized_path = _normalized_absolute_path(raw_project.get("path"))
        if normalized_path in normalized_paths:
            _reject("REGISTRY_PATH_DUPLICATE")
        normalized_paths.add(normalized_path)

        priority = raw_project.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            _reject("FIELD_TYPE_INVALID")
        if not 0 <= priority <= 100:
            _reject("FIELD_VALUE_INVALID")
        if raw_project.get("lifecycle") not in _LIFECYCLES:
            _reject("FIELD_VALUE_INVALID")
        dependencies = raw_project.get("depends_on")
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str) for dependency in dependencies
        ):
            _reject("FIELD_TYPE_INVALID")
        if schema == STATUS_SOURCE_SCHEMA and not _valid_status_path(
            raw_project.get("status_source_path")
        ):
            _reject("FIELD_VALUE_INVALID")
        projects.append(copy.deepcopy(raw_project))

    _validate_dependencies(projects)
    return copy.deepcopy(value)


def _metadata_tuple(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _path_directory_identity(path: os.PathLike[str] | str) -> tuple[int, ...]:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise PortfolioContractError("PORTFOLIO_ROOT_CHANGED") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        _reject("PORTFOLIO_ROOT_CHANGED")
    return _metadata_tuple(metadata)


def _entry_metadata_at(directory_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def _read_fd_bytes(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def load_registry(portfolio_root: Path) -> dict[str, object]:
    root_path = os.fspath(portfolio_root)
    initial_root = _path_directory_identity(root_path)
    root_fd = -1
    registry_fd = -1
    try:
        root_fd = os.open(
            root_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        if _metadata_tuple(os.fstat(root_fd)) != initial_root:
            _reject("PORTFOLIO_ROOT_CHANGED")
        try:
            before = _entry_metadata_at(root_fd, "registry.toml")
        except OSError as exc:
            raise PortfolioContractError("REGISTRY_NOT_REGULAR") from exc
        if not stat.S_ISREG(before.st_mode):
            _reject("REGISTRY_NOT_REGULAR")
        if before.st_size > MAX_REGISTRY_BYTES:
            _reject("REGISTRY_TOO_LARGE")
        try:
            registry_fd = os.open(
                "registry.toml",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_fd,
            )
        except OSError as exc:
            raise PortfolioContractError("REGISTRY_NOT_REGULAR") from exc
        opened = os.fstat(registry_fd)
        if not stat.S_ISREG(opened.st_mode):
            _reject("REGISTRY_NOT_REGULAR")
        if _metadata_tuple(opened) != _metadata_tuple(before):
            _reject("REGISTRY_CHANGED_DURING_READ")
        data = _read_fd_bytes(registry_fd, MAX_REGISTRY_BYTES)
        if len(data) > MAX_REGISTRY_BYTES:
            _reject("REGISTRY_TOO_LARGE")
        after = os.fstat(registry_fd)
        if _metadata_tuple(after) != _metadata_tuple(opened):
            _reject("REGISTRY_CHANGED_DURING_READ")
        if _path_directory_identity(root_path) != initial_root:
            _reject("PORTFOLIO_ROOT_CHANGED")
    except PortfolioContractError:
        raise
    except OSError as exc:
        raise PortfolioContractError("PORTFOLIO_ROOT_CHANGED") from exc
    finally:
        if registry_fd >= 0:
            os.close(registry_fd)
        if root_fd >= 0:
            os.close(root_fd)

    try:
        decoded = data.decode("utf-8", "strict")
        parsed = tomllib.loads(decoded)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PortfolioContractError("FIELD_VALUE_INVALID") from exc
    return validate_registry(parsed)


def _filesystem_identity_from_metadata(metadata: os.stat_result) -> dict[str, object]:
    return {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "object_type": "directory",
    }


def _validate_expected_root_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(value) != {
        "st_dev",
        "st_ino",
        "object_type",
    }:
        _reject("STATUS_SOURCE_OUTSIDE_PROJECT")
    if (
        isinstance(value.get("st_dev"), bool)
        or not isinstance(value.get("st_dev"), int)
        or isinstance(value.get("st_ino"), bool)
        or not isinstance(value.get("st_ino"), int)
        or value.get("object_type") != "directory"
    ):
        _reject("STATUS_SOURCE_OUTSIDE_PROJECT")
    return value


def _none_status_source() -> dict[str, object]:
    return {
        "schema": "NONE",
        "path": None,
        "content_sha256": None,
        "state": "UNKNOWN",
        "reason": "STATUS_SOURCE_NOT_CONFIGURED",
        "milestone": None,
        "milestone_state": None,
        "blockers": [],
        "next_step": None,
        "milestone_value": None,
        "completion_confidence": None,
        "blocker_cost": None,
        "task_size": None,
        "approval_wait": None,
        "release_window": None,
        "risk_level": None,
        "updated_at_utc": None,
    }


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _validate_status_value(value: object, source_path: str, data: bytes) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(value) != _STATUS_FIELDS:
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    if value.get("schema_version") != 1:
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    if not _valid_text(value.get("milestone"), minimum=1, maximum=240):
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    if not _valid_text(value.get("next_step"), minimum=1, maximum=500):
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    blockers = value.get("blockers")
    if (
        not isinstance(blockers, list)
        or len(blockers) > 50
        or any(not _valid_text(item, minimum=1, maximum=500) for item in blockers)
    ):
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    if value.get("milestone_state") not in _MILESTONE_STATES:
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    if value.get("state") not in _STATUS_STATES:
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    for field in _STATUS_NUMERIC_FIELDS:
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 100:
            _reject("STATUS_SOURCE_SCHEMA_INVALID")
    updated = value.get("updated_at_utc")
    if not isinstance(updated, str) or not _UTC_PATTERN.fullmatch(updated):
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    try:
        datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PortfolioContractError("STATUS_SOURCE_SCHEMA_INVALID") from exc

    return {
        "schema": STATUS_SOURCE_SCHEMA,
        "path": source_path,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "state": value["state"],
        "reason": None,
        "milestone": value["milestone"],
        "milestone_state": value["milestone_state"],
        "blockers": copy.deepcopy(value["blockers"]),
        "next_step": value["next_step"],
        "milestone_value": value["milestone_value"],
        "completion_confidence": value["completion_confidence"],
        "blocker_cost": value["blocker_cost"],
        "task_size": value["task_size"],
        "approval_wait": value["approval_wait"],
        "release_window": value["release_window"],
        "risk_level": value["risk_level"],
        "updated_at_utc": value["updated_at_utc"],
    }


def read_status_source(
    project: dict[str, object],
    *,
    expected_root_identity: dict[str, object],
) -> dict[str, object]:
    if project.get("status_source_schema") == "NONE":
        return _none_status_source()
    if project.get("status_source_schema") != STATUS_SOURCE_SCHEMA or not _valid_status_path(
        project.get("status_source_path")
    ):
        _reject("STATUS_SOURCE_SCHEMA_INVALID")
    expected = _validate_expected_root_identity(expected_root_identity)
    root_path = project.get("path")
    if not isinstance(root_path, str):
        _reject("STATUS_SOURCE_OUTSIDE_PROJECT")
    components = project["status_source_path"].split("/")
    descriptors: list[int] = []
    directory_entries: list[tuple[int, str, tuple[int, ...]]] = []
    final_fd = -1
    try:
        root_fd = os.open(
            root_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        descriptors.append(root_fd)
        root_metadata = os.fstat(root_fd)
        if _filesystem_identity_from_metadata(root_metadata) != expected:
            _reject("STATUS_SOURCE_OUTSIDE_PROJECT")

        parent_fd = root_fd
        for component in components[:-1]:
            try:
                metadata = _entry_metadata_at(parent_fd, component)
            except OSError as exc:
                raise PortfolioContractError("STATUS_SOURCE_NOT_REGULAR") from exc
            if not stat.S_ISDIR(metadata.st_mode):
                _reject("STATUS_SOURCE_NOT_REGULAR")
            try:
                child_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise PortfolioContractError("STATUS_SOURCE_NOT_REGULAR") from exc
            descriptors.append(child_fd)
            opened_directory = _metadata_tuple(os.fstat(child_fd))
            if opened_directory != _metadata_tuple(metadata):
                _reject("STATUS_SOURCE_CHANGED_DURING_READ")
            directory_entries.append((parent_fd, component, opened_directory))
            parent_fd = child_fd

        final_name = components[-1]
        try:
            before = _entry_metadata_at(parent_fd, final_name)
        except OSError as exc:
            raise PortfolioContractError("STATUS_SOURCE_NOT_REGULAR") from exc
        if not stat.S_ISREG(before.st_mode):
            _reject("STATUS_SOURCE_NOT_REGULAR")
        if before.st_size > MAX_STATUS_SOURCE_BYTES:
            _reject("STATUS_SOURCE_TOO_LARGE")
        try:
            final_fd = os.open(
                final_name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise PortfolioContractError("STATUS_SOURCE_NOT_REGULAR") from exc
        opened = os.fstat(final_fd)
        if not stat.S_ISREG(opened.st_mode):
            _reject("STATUS_SOURCE_NOT_REGULAR")
        if _metadata_tuple(opened) != _metadata_tuple(before):
            _reject("STATUS_SOURCE_CHANGED_DURING_READ")
        data = _read_fd_bytes(final_fd, MAX_STATUS_SOURCE_BYTES)
        if len(data) > MAX_STATUS_SOURCE_BYTES:
            _reject("STATUS_SOURCE_TOO_LARGE")
        if _metadata_tuple(os.fstat(final_fd)) != _metadata_tuple(opened):
            _reject("STATUS_SOURCE_CHANGED_DURING_READ")
        try:
            current_entry = _entry_metadata_at(parent_fd, final_name)
        except OSError as exc:
            raise PortfolioContractError("STATUS_SOURCE_CHANGED_DURING_READ") from exc
        if _metadata_tuple(current_entry) != _metadata_tuple(opened):
            _reject("STATUS_SOURCE_CHANGED_DURING_READ")
        for directory_fd, component, opened_directory in directory_entries:
            try:
                current_directory = _entry_metadata_at(directory_fd, component)
            except OSError as exc:
                raise PortfolioContractError("STATUS_SOURCE_CHANGED_DURING_READ") from exc
            if _metadata_tuple(current_directory) != opened_directory:
                _reject("STATUS_SOURCE_CHANGED_DURING_READ")
        try:
            current_root = os.stat(root_path, follow_symlinks=False)
        except OSError as exc:
            raise PortfolioContractError("STATUS_SOURCE_OUTSIDE_PROJECT") from exc
        if _filesystem_identity_from_metadata(current_root) != expected:
            _reject("STATUS_SOURCE_OUTSIDE_PROJECT")
    except PortfolioContractError:
        raise
    except OSError as exc:
        raise PortfolioContractError("STATUS_SOURCE_OUTSIDE_PROJECT") from exc
    finally:
        if final_fd >= 0:
            os.close(final_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    try:
        if data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("BOM")
        decoded = data.decode("utf-8", "strict")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PortfolioContractError("STATUS_SOURCE_SCHEMA_INVALID") from exc
    return _validate_status_value(parsed, project["status_source_path"], data)


_IDENTITY_SUMMARY_FIELDS = (
    "status",
    "binding_kind",
    "physical_path",
    "git_top_level",
    "git_common_dir",
    "worktree_id",
    "branch",
    "head",
    "dirty",
    "dirty_fingerprint",
    "fingerprint_complete",
)


def _validate_identity(value: object) -> dict[str, object]:
    try:
        project_identity.validate_identity_schema(value, supported_versions=(2,))
    except project_identity.IdentitySchemaError as exc:
        raise PortfolioContractError("IDENTITY_SCHEMA_INVALID") from exc
    return value


def _identity_summary(value: dict[str, object]) -> dict[str, object]:
    return {field: copy.deepcopy(value.get(field)) for field in _IDENTITY_SUMMARY_FIELDS}


def _identity_is_ambiguous(value: dict[str, object]) -> bool:
    if value.get("status") == "ambiguous":
        return True
    aliases = value.get("aliases")
    return isinstance(aliases, list) and any(
        isinstance(alias, dict)
        and alias.get("relation_to_workspace") in {"AMBIGUOUS", "DIFFERENT_OBJECT"}
        for alias in aliases
    )


def classify_capture_pair(
    first: dict[str, object],
    second: dict[str, object],
) -> dict[str, object]:
    first = _validate_identity(first)
    second = _validate_identity(second)
    result = {
        "capture_state": "CURRENT_INCOMPLETE",
        "reason": "IDENTITY_CAPTURE_INCOMPLETE",
        "identity": _identity_summary(second),
        "filesystem_identity": copy.deepcopy(second.get("filesystem_identity")),
        "write_authorized": False,
    }
    if _identity_is_ambiguous(first) or _identity_is_ambiguous(second):
        result.update(
            capture_state="AMBIGUOUS",
            reason="IDENTITY_CHANGED_DURING_CAPTURE",
        )
        return result
    if project_freshness.canonical_json_digest(
        first
    ) != project_freshness.canonical_json_digest(second):
        result["reason"] = "STATE_CHANGED_DURING_CAPTURE"
        return result
    if any(value.get("status") != "bound" or value.get("reason") is not None for value in (first, second)):
        return result

    git_complete = all(
        value.get("binding_kind") == "GIT_WORKTREE"
        and value.get("is_git") is True
        and value.get("fingerprint_applicability") == "REQUIRED"
        and value.get("fingerprint_complete") is True
        and value.get("fingerprint_reason") is None
        for value in (first, second)
    )
    non_write_class_complete = all(
        value.get("binding_kind") in {"NON_GIT_DIRECTORY", "BARE_GIT"}
        and value.get("fingerprint_applicability") == "NOT_APPLICABLE"
        for value in (first, second)
    )
    if git_complete or non_write_class_complete:
        result.update(capture_state="CURRENT_COMPLETE", reason=None)
    return result


def _unavailable_status_source(project: dict[str, object], reason: str) -> dict[str, object]:
    result = _none_status_source()
    result.update(
        schema=project.get("status_source_schema", "NONE"),
        path=project.get("status_source_path"),
        reason=reason,
    )
    return result


def _capture_identity(
    identity_collector,
    project_path: str,
) -> tuple[dict[str, object] | None, str | None]:
    try:
        value = identity_collector(
            project_path,
            MAX_UNTRACKED_BYTES,
            workspace_pwd=project_path,
            aliases=(),
        )
    except Exception:
        return None, "IDENTITY_CAPTURE_INCOMPLETE"
    try:
        return _validate_identity(value), None
    except PortfolioContractError as exc:
        return None, exc.reason


def capture_project(
    project: dict[str, object],
    *,
    baseline_project: dict[str, object] | None,
    identity_collector=project_identity.collect_identity,
) -> dict[str, object]:
    del baseline_project
    project_path = project.get("path")
    if not isinstance(project_path, str):
        _reject("FIELD_VALUE_INVALID")

    first, first_error = _capture_identity(identity_collector, project_path)
    status_error: str | None = first_error
    status_source: dict[str, object]
    if first is None:
        status_source = _unavailable_status_source(
            project,
            first_error or "IDENTITY_CAPTURE_INCOMPLETE",
        )
    else:
        try:
            expected_identity = first.get("filesystem_identity")
            if not isinstance(expected_identity, dict):
                _reject("IDENTITY_CAPTURE_INCOMPLETE")
            status_source = read_status_source(
                project,
                expected_root_identity=expected_identity,
            )
        except PortfolioContractError as exc:
            status_error = exc.reason
            status_source = _unavailable_status_source(project, exc.reason)

    second, second_error = _capture_identity(identity_collector, project_path)
    if first is not None and second is not None:
        classification = classify_capture_pair(first, second)
    else:
        available = second or first
        classification = {
            "capture_state": "CURRENT_INCOMPLETE",
            "reason": first_error or second_error or "IDENTITY_CAPTURE_INCOMPLETE",
            "identity": (
                _identity_summary(available)
                if available is not None
                else {field: None for field in _IDENTITY_SUMMARY_FIELDS}
            ),
            "filesystem_identity": (
                copy.deepcopy(available.get("filesystem_identity"))
                if available is not None
                else None
            ),
            "write_authorized": False,
        }
    if status_error is not None and classification["capture_state"] != "AMBIGUOUS":
        classification["capture_state"] = "CURRENT_INCOMPLETE"
        classification["reason"] = status_error

    return {
        "id": project.get("id"),
        "display_name": project.get("display_name"),
        "configured_path": project_path,
        "lifecycle": project.get("lifecycle"),
        "priority": project.get("priority"),
        "depends_on": copy.deepcopy(project.get("depends_on", [])),
        "capture_state": classification["capture_state"],
        "reason": classification["reason"],
        "identity": classification["identity"],
        "filesystem_identity": classification["filesystem_identity"],
        "status_source": status_source,
        "write_authorized": False,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _snapshot_digest(value: object) -> str:
    candidate = copy.deepcopy(value)
    if isinstance(candidate, dict):
        candidate.pop("snapshot_sha256", None)
        candidate.pop("generated_at_utc", None)
        projects = candidate.get("projects")
        if isinstance(projects, list):
            for project in projects:
                if isinstance(project, dict):
                    freshness = project.get("freshness")
                    if isinstance(freshness, dict):
                        freshness.pop("verified_at_utc", None)
    return canonical_portfolio_digest(candidate)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not _UTC_PATTERN.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _valid_text_list(
    value: object,
    *,
    maximum_items: int,
    maximum_length: int,
) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum_items
        and all(_valid_text(item, minimum=1, maximum=maximum_length) for item in value)
    )


def _snapshot_identity_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or frozenset(value) != set(_IDENTITY_SUMMARY_FIELDS):
        return False
    if all(value.get(field) is None for field in _IDENTITY_SUMMARY_FIELDS):
        return True
    if (
        value.get("status") not in {"bound", "incomplete", "ambiguous", "unsupported"}
        or value.get("binding_kind")
        not in {"GIT_WORKTREE", "NON_GIT_DIRECTORY", "BARE_GIT", "NONE"}
        or not isinstance(value.get("dirty"), bool)
        or not isinstance(value.get("fingerprint_complete"), bool)
    ):
        return False
    for field in ("physical_path", "git_top_level", "git_common_dir", "worktree_id", "branch"):
        item = value.get(field)
        if item is not None and not _valid_text(item, minimum=1, maximum=4096):
            return False
    head = value.get("head")
    if head is not None and (
        not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) is None
    ):
        return False
    fingerprint = value.get("dirty_fingerprint")
    return fingerprint is None or _valid_sha256(fingerprint)


def _snapshot_status_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or frozenset(value) != _STATUS_OUTPUT_FIELDS:
        return False
    schema = value.get("schema")
    path = value.get("path")
    if schema not in {"NONE", STATUS_SOURCE_SCHEMA}:
        return False
    if schema == "NONE":
        if path is not None:
            return False
    elif not _valid_status_path(path):
        return False

    reason = value.get("reason")
    if reason is not None:
        empty_fields = (
            "content_sha256",
            "milestone",
            "milestone_state",
            "next_step",
            *_STATUS_NUMERIC_FIELDS,
            "updated_at_utc",
        )
        return (
            _valid_text(reason, minimum=1, maximum=240)
            and value.get("state") == "UNKNOWN"
            and value.get("blockers") == []
            and all(value.get(field) is None for field in empty_fields)
        )

    if schema != STATUS_SOURCE_SCHEMA or not _valid_sha256(value.get("content_sha256")):
        return False
    if (
        value.get("state") not in _STATUS_STATES
        or value.get("milestone_state") not in _MILESTONE_STATES
        or not _valid_text(value.get("milestone"), minimum=1, maximum=240)
        or not _valid_text(value.get("next_step"), minimum=1, maximum=500)
        or not _valid_text_list(value.get("blockers"), maximum_items=50, maximum_length=500)
        or not _valid_utc_timestamp(value.get("updated_at_utc"))
    ):
        return False
    return all(
        not isinstance(value.get(field), bool)
        and isinstance(value.get(field), int)
        and 0 <= value[field] <= 100
        for field in _STATUS_NUMERIC_FIELDS
    )


def _snapshot_freshness_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or frozenset(value) != _FRESHNESS_FIELDS:
        return False
    state = value.get("state")
    reasons = value.get("reasons")
    changed = value.get("changed_fields")
    if (
        state not in {"FRESH", "STALE", "UNKNOWN", "AMBIGUOUS"}
        or not _valid_text_list(reasons, maximum_items=100, maximum_length=500)
        or not _valid_text_list(changed, maximum_items=100, maximum_length=500)
        or len(reasons) != len(set(reasons))
        or len(changed) != len(set(changed))
        or not _valid_utc_timestamp(value.get("verified_at_utc"))
    ):
        return False
    if state == "FRESH":
        return reasons == [] and changed == []
    if state == "STALE":
        return bool(reasons) and bool(changed)
    return bool(reasons) and changed == []


def _snapshot_recommendation_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or frozenset(value) != _RECOMMENDATION_FIELDS:
        return False
    rank = value.get("rank")
    return (
        value.get("bucket")
        in {"ACTIONABLE", "BLOCKED", "WAITING_APPROVAL", "PAUSED", "ARCHIVED"}
        and value.get("confidence") in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
        and not isinstance(rank, bool)
        and isinstance(rank, int)
        and rank >= 1
        and _valid_text_list(value.get("rationale"), maximum_items=100, maximum_length=500)
        and bool(value.get("rationale"))
    )


def _snapshot_schema_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or frozenset(value) != _SNAPSHOT_FIELDS:
        return False
    if (
        value.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(value.get("portfolio_id"), str)
        or _ID_PATTERN.fullmatch(value["portfolio_id"]) is None
        or not _valid_sha256(value.get("registry_sha256"))
        or not _valid_utc_timestamp(value.get("generated_at_utc"))
        or value.get("portfolio_state") not in {"COMPLETE", "PARTIAL", "UNKNOWN", "BLOCKED"}
        or value.get("write_authorized") is not False
        or not isinstance(value.get("projects"), list)
        or not value.get("projects")
        or not _valid_sha256(value.get("snapshot_sha256"))
    ):
        return False
    project_ids: list[str] = []
    ranks: list[int] = []
    for project in value["projects"]:
        if not isinstance(project, dict) or frozenset(project) != _SNAPSHOT_PROJECT_FIELDS:
            return False
        if (
            not isinstance(project.get("id"), str)
            or _ID_PATTERN.fullmatch(project["id"]) is None
            or not _valid_text(project.get("display_name"), minimum=1, maximum=120)
            or not isinstance(project.get("configured_path"), str)
            or project_identity.classify_path_input(project["configured_path"])
            == "UNSUPPORTED_OR_RELATIVE"
            or project.get("lifecycle") not in _LIFECYCLES
            or isinstance(project.get("priority"), bool)
            or not isinstance(project.get("priority"), int)
            or not 0 <= project["priority"] <= 100
            or not isinstance(project.get("depends_on"), list)
            or any(
                not isinstance(dependency, str) or _ID_PATTERN.fullmatch(dependency) is None
                for dependency in project.get("depends_on", [])
            )
            or len(project["depends_on"]) != len(set(project["depends_on"]))
            or project["id"] in project["depends_on"]
            or not _snapshot_identity_is_valid(project.get("identity"))
            or not _snapshot_status_is_valid(project.get("status_source"))
            or not _snapshot_freshness_is_valid(project.get("freshness"))
            or not _snapshot_recommendation_is_valid(project.get("recommendation"))
            or project.get("capture_state")
            not in {"CURRENT_COMPLETE", "CURRENT_INCOMPLETE", "AMBIGUOUS"}
        ):
            return False
        project_ids.append(project["id"])
        ranks.append(project["recommendation"]["rank"])
    if len(project_ids) != len(set(project_ids)) or sorted(ranks) != list(
        range(1, len(ranks) + 1)
    ):
        return False
    known_ids = set(project_ids)
    if any(
        dependency not in known_ids
        for project in value["projects"]
        for dependency in project["depends_on"]
    ):
        return False
    return True


def validate_snapshot(value: object) -> dict[str, object]:
    if not _snapshot_schema_is_valid(value):
        _reject("SNAPSHOT_SCHEMA_INVALID")
    if value["snapshot_sha256"] != _snapshot_digest(value):
        _reject("SNAPSHOT_DIGEST_MISMATCH")
    return copy.deepcopy(value)


def _config_changed_fields(
    baseline_project: dict[str, object],
    current_project: dict[str, object],
) -> list[str]:
    fields = (
        ("configured_path", "config.path"),
        ("lifecycle", "config.lifecycle"),
        ("priority", "config.priority"),
        ("depends_on", "config.depends_on"),
    )
    return [label for field, label in fields if baseline_project.get(field) != current_project.get(field)]


def _freshness_for_project(
    current: dict[str, object],
    baseline_project: dict[str, object] | None,
    verified_at_utc: str,
) -> dict[str, object]:
    capture_state = current["capture_state"]
    status_source = current["status_source"]
    if capture_state == "AMBIGUOUS":
        return {
            "state": "AMBIGUOUS",
            "reasons": [current.get("reason") or "IDENTITY_CHANGED_DURING_CAPTURE"],
            "changed_fields": [],
            "verified_at_utc": verified_at_utc,
        }
    if capture_state != "CURRENT_COMPLETE":
        return {
            "state": "UNKNOWN",
            "reasons": [current.get("reason") or "IDENTITY_CAPTURE_INCOMPLETE"],
            "changed_fields": [],
            "verified_at_utc": verified_at_utc,
        }
    if current["identity"].get("binding_kind") != "GIT_WORKTREE":
        return {
            "state": "UNKNOWN",
            "reasons": ["UNSUPPORTED_IDENTITY_CLASS"],
            "changed_fields": [],
            "verified_at_utc": verified_at_utc,
        }
    if status_source.get("schema") != STATUS_SOURCE_SCHEMA or status_source.get("reason") is not None:
        return {
            "state": "UNKNOWN",
            "reasons": [status_source.get("reason") or "STATUS_SOURCE_NOT_CONFIGURED"],
            "changed_fields": [],
            "verified_at_utc": verified_at_utc,
        }
    if baseline_project is None:
        return {
            "state": "UNKNOWN",
            "reasons": ["NO_PORTFOLIO_BASELINE"],
            "changed_fields": [],
            "verified_at_utc": verified_at_utc,
        }

    changed_fields = _config_changed_fields(baseline_project, current)
    if canonical_portfolio_digest(baseline_project.get("identity")) != canonical_portfolio_digest(
        current.get("identity")
    ):
        changed_fields.append("identity")
    if canonical_portfolio_digest(
        baseline_project.get("status_source")
    ) != canonical_portfolio_digest(status_source):
        changed_fields.append("status_source")
    changed_fields = sorted(set(changed_fields))
    if changed_fields:
        return {
            "state": "STALE",
            "reasons": [f"FIELD_CHANGED:{field}" for field in changed_fields],
            "changed_fields": changed_fields,
            "verified_at_utc": verified_at_utc,
        }
    return {
        "state": "FRESH",
        "reasons": [],
        "changed_fields": [],
        "verified_at_utc": verified_at_utc,
    }


def _unmet_dependency_ids(
    project: dict[str, object],
    projects_by_id: dict[str, dict[str, object]],
) -> list[str]:
    unmet: list[str] = []
    for dependency_id in project["depends_on"]:
        dependency = projects_by_id[dependency_id]
        if (
            dependency["capture_state"] != "CURRENT_COMPLETE"
            or dependency["status_source"].get("reason") is not None
            or dependency["status_source"].get("milestone_state") != "COMPLETE"
            or dependency["freshness"]["state"] in {"UNKNOWN", "AMBIGUOUS"}
        ):
            unmet.append(dependency_id)
    return unmet


def _initial_recommendation(
    project: dict[str, object],
    projects_by_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    status = project["status_source"]
    unmet_dependencies = _unmet_dependency_ids(project, projects_by_id)
    if (
        project["capture_state"] != "CURRENT_COMPLETE"
        or status.get("reason") is not None
        or status.get("state") == "BLOCKED"
        or status.get("milestone_state") == "BLOCKED"
        or unmet_dependencies
    ):
        bucket = "BLOCKED"
    elif project["lifecycle"] == "ARCHIVED" or status.get("state") == "ARCHIVED":
        bucket = "ARCHIVED"
    elif project["lifecycle"] == "PAUSED" or status.get("state") == "PAUSED":
        bucket = "PAUSED"
    elif project["lifecycle"] == "WAITING_APPROVAL" or status.get("state") == "WAITING_APPROVAL":
        bucket = "WAITING_APPROVAL"
    else:
        bucket = "ACTIONABLE"
    freshness = project["freshness"]["state"]
    confidence = {
        "FRESH": "HIGH",
        "STALE": "MEDIUM",
        "UNKNOWN": "UNKNOWN",
        "AMBIGUOUS": "UNKNOWN",
    }[freshness]
    rationale = [
        f"BUCKET:{bucket}",
        f"FRESHNESS:{freshness}",
        f"PRIORITY:{project['priority']}",
        f"MILESTONE:{status.get('milestone') or 'UNKNOWN'}",
        "BLOCKERS:" + (",".join(status.get("blockers") or []) or "NONE"),
        f"RELEASE_WINDOW:{_numeric_status(project, 'release_window', 0)}",
        f"MILESTONE_VALUE:{_numeric_status(project, 'milestone_value', 0)}",
        f"BLOCKER_COST:{_numeric_status(project, 'blocker_cost', 0)}",
        f"COMPLETION_CONFIDENCE:{_numeric_status(project, 'completion_confidence', 0)}",
        f"RISK_LEVEL:{_numeric_status(project, 'risk_level', 101)}",
        f"APPROVAL_WAIT:{_numeric_status(project, 'approval_wait', 101)}",
        f"TASK_SIZE:{_numeric_status(project, 'task_size', 101)}",
        f"ID:{project['id']}",
    ]
    if project["depends_on"]:
        rationale.append(
            "DEPENDENCIES_UNMET:" + ",".join(unmet_dependencies)
            if unmet_dependencies
            else "DEPENDENCIES_SATISFIED"
        )
    return {
        "bucket": bucket,
        "rank": 0,
        "rationale": rationale,
        "confidence": confidence,
    }


_BUCKET_ORDER = {
    "ACTIONABLE": 0,
    "BLOCKED": 1,
    "WAITING_APPROVAL": 2,
    "PAUSED": 3,
    "ARCHIVED": 4,
}
_FRESHNESS_ORDER = {"FRESH": 0, "STALE": 1, "UNKNOWN": 2, "AMBIGUOUS": 3}


def _numeric_status(project: dict[str, object], field: str, default: int) -> int:
    value = project["status_source"].get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _rank_key(project: dict[str, object]) -> tuple[object, ...]:
    recommendation = project["recommendation"]
    return (
        _BUCKET_ORDER[recommendation["bucket"]],
        -project["priority"],
        _FRESHNESS_ORDER[project["freshness"]["state"]],
        -_numeric_status(project, "release_window", 0),
        -_numeric_status(project, "milestone_value", 0),
        -_numeric_status(project, "blocker_cost", 0),
        -_numeric_status(project, "completion_confidence", 0),
        _numeric_status(project, "risk_level", 101),
        _numeric_status(project, "approval_wait", 101),
        _numeric_status(project, "task_size", 101),
        project["id"],
    )


def _has_duplicate_project_object(captures: list[dict[str, object]]) -> bool:
    filesystem_objects: set[tuple[object, object, object]] = set()
    git_objects: set[tuple[object, object]] = set()
    for capture in captures:
        filesystem = capture.get("filesystem_identity")
        if isinstance(filesystem, dict):
            key = (
                filesystem.get("st_dev"),
                filesystem.get("st_ino"),
                filesystem.get("object_type"),
            )
            if key in filesystem_objects:
                return True
            filesystem_objects.add(key)
        identity = capture.get("identity")
        if isinstance(identity, dict) and identity.get("binding_kind") == "GIT_WORKTREE":
            key = (identity.get("git_common_dir"), identity.get("worktree_id"))
            if key in git_objects:
                return True
            git_objects.add(key)
    return False


def _baseline_config_matches_registry(
    baseline: dict[str, object],
    registry: dict[str, object],
) -> bool:
    baseline_by_id = {project["id"]: project for project in baseline["projects"]}
    if set(baseline_by_id) != {project["id"] for project in registry["projects"]}:
        return False
    allowed_config_changed = False
    for project in registry["projects"]:
        previous = baseline_by_id[project["id"]]
        previous_status = previous.get("status_source")
        if (
            previous.get("display_name") != project["display_name"]
            or not isinstance(previous_status, dict)
            or previous_status.get("schema") != project["status_source_schema"]
            or previous_status.get("path") != project.get("status_source_path")
        ):
            return False
        allowed_config_changed = allowed_config_changed or any(
            previous.get(field) != project[registry_field]
            for field, registry_field in (
                ("configured_path", "path"),
                ("lifecycle", "lifecycle"),
                ("priority", "priority"),
                ("depends_on", "depends_on"),
            )
        )
    return allowed_config_changed


def build_portfolio_snapshot(
    registry: dict[str, object],
    *,
    baseline: dict[str, object] | None = None,
    identity_collector=project_identity.collect_identity,
    generated_at_utc: str | None = None,
) -> dict[str, object]:
    registry = validate_registry(registry)
    generated = generated_at_utc or _utc_now()
    if not _UTC_PATTERN.fullmatch(generated):
        _reject("FIELD_VALUE_INVALID")
    registry_sha256 = canonical_portfolio_digest(registry)
    validated_baseline: dict[str, object] | None = None
    if baseline is not None:
        try:
            validated_baseline = validate_snapshot(baseline)
        except PortfolioContractError as exc:
            reason = (
                "BASELINE_DIGEST_MISMATCH"
                if exc.reason == "SNAPSHOT_DIGEST_MISMATCH"
                else "BASELINE_SCHEMA_INVALID"
            )
            raise PortfolioContractError(reason) from exc
        if validated_baseline["portfolio_id"] != registry["portfolio_id"]:
            _reject("BASELINE_REGISTRY_MISMATCH")
        if (
            validated_baseline["registry_sha256"] != registry_sha256
            and not _baseline_config_matches_registry(validated_baseline, registry)
        ):
            _reject("BASELINE_REGISTRY_MISMATCH")
    baseline_by_id = (
        {project["id"]: project for project in validated_baseline["projects"]}
        if validated_baseline is not None
        else {}
    )

    projects: list[dict[str, object]] = []
    internal_captures: list[dict[str, object]] = []
    for project_config in registry["projects"]:
        captured = capture_project(
            project_config,
            baseline_project=baseline_by_id.get(project_config["id"]),
            identity_collector=identity_collector,
        )
        internal_captures.append(captured)
        project = {
            "id": captured["id"],
            "display_name": captured["display_name"],
            "configured_path": captured["configured_path"],
            "lifecycle": captured["lifecycle"],
            "priority": captured["priority"],
            "depends_on": copy.deepcopy(captured["depends_on"]),
            "capture_state": captured["capture_state"],
            "identity": copy.deepcopy(captured["identity"]),
            "status_source": copy.deepcopy(captured["status_source"]),
        }
        project["freshness"] = _freshness_for_project(
            captured,
            baseline_by_id.get(captured["id"]),
            generated,
        )
        projects.append(project)

    projects_by_id = {project["id"]: project for project in projects}
    duplicate_object = _has_duplicate_project_object(internal_captures)
    if duplicate_object:
        for project in projects:
            project["capture_state"] = "AMBIGUOUS"
            project["freshness"] = {
                "state": "AMBIGUOUS",
                "reasons": ["PROJECT_OBJECT_DUPLICATE"],
                "changed_fields": [],
                "verified_at_utc": generated,
            }
            project["recommendation"] = {
                "bucket": "BLOCKED",
                "rank": 0,
                "rationale": ["PROJECT_OBJECT_DUPLICATE"],
                "confidence": "UNKNOWN",
            }
    else:
        for project in projects:
            project["recommendation"] = _initial_recommendation(project, projects_by_id)
        projects.sort(key=_rank_key)

    usable = sum(
        project["capture_state"] == "CURRENT_COMPLETE"
        and project["status_source"]["reason"] is None
        for project in projects
    )
    complete = all(
        project["capture_state"] == "CURRENT_COMPLETE"
        and project["status_source"]["reason"] is None
        and project["freshness"]["state"] in {"FRESH", "STALE"}
        for project in projects
    )
    portfolio_state = (
        "BLOCKED"
        if duplicate_object
        else ("COMPLETE" if complete else ("PARTIAL" if usable else "UNKNOWN"))
    )
    for rank, project in enumerate(projects, start=1):
        project["recommendation"]["rank"] = rank

    result = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "portfolio_id": registry["portfolio_id"],
        "registry_sha256": registry_sha256,
        "generated_at_utc": generated,
        "portfolio_state": portfolio_state,
        "write_authorized": False,
        "projects": projects,
        "snapshot_sha256": "",
    }
    result["snapshot_sha256"] = _snapshot_digest(result)
    return validate_snapshot(result)


def _markdown_cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        text = "; ".join(str(item) for item in value) if value else "-"
    else:
        text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_portfolio_markdown(snapshot: object) -> str:
    value = validate_snapshot(snapshot)
    lines = [
        "# 多项目 Portfolio",
        "",
        f"生成时间：{_markdown_cell(value['generated_at_utc'])}",
        f"总状态：{_markdown_cell(value['portfolio_state'])}",
        "写入授权：false",
        "",
        "| 顺序 | 项目 | 当前阶段 | 里程碑 | 阻塞/依赖 | 下一步 | freshness | 验证时间 | 理由 |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for project in value["projects"]:
        status = project["status_source"]
        freshness = project["freshness"]
        blockers = list(status["blockers"])
        if project["depends_on"]:
            blockers.append("依赖: " + ", ".join(project["depends_on"]))
        if status["reason"] is not None:
            blockers.append(status["reason"])
        freshness_text = freshness["state"]
        if freshness["reasons"]:
            freshness_text += ": " + ", ".join(freshness["reasons"])
        row = (
            project["recommendation"]["rank"],
            project["display_name"],
            f"{project['lifecycle']} / {status['state']}",
            status["milestone"],
            blockers,
            status["next_step"],
            freshness_text,
            freshness["verified_at_utc"],
            project["recommendation"]["rationale"],
        )
        lines.append("| " + " | ".join(_markdown_cell(item) for item in row) + " |")
    lines.extend(
        [
            "",
            "该视图仅提供只读建议；选择项目必须进入新任务并重新绑定。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_project_handoff(snapshot: object, project_id: str) -> dict[str, object]:
    value = validate_snapshot(snapshot)
    selected = next(
        (project for project in value["projects"] if project["id"] == project_id),
        None,
    )
    if selected is None:
        _reject("PROJECT_NOT_FOUND")
    if selected["capture_state"] != "CURRENT_COMPLETE":
        _reject("PROJECT_NOT_HANDOFF_ELIGIBLE")
    return {
        "project_id": selected["id"],
        "display_name": selected["display_name"],
        "target_path": selected["configured_path"],
        "required_new_task": True,
        "first_command": "pwd",
        "required_rechecks": [
            "path",
            "git",
            "head",
            "dirty",
            "freshness",
            "project_rules",
        ],
        "inherited_approvals": [],
        "write_authorized": False,
    }


class _PortfolioArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise PortfolioContractError("FIELD_VALUE_INVALID")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _PortfolioArgumentParser(add_help=True)
    parser.add_argument("--portfolio-root", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--view", choices=("json", "markdown"), default="json")
    parser.add_argument("--handoff-project")
    arguments = parser.parse_args(argv)
    if (
        project_identity.classify_path_input(arguments.portfolio_root)
        == "UNSUPPORTED_OR_RELATIVE"
        or (
            arguments.baseline is not None
            and project_identity.classify_path_input(arguments.baseline)
            == "UNSUPPORTED_OR_RELATIVE"
        )
        or (arguments.handoff_project is not None and arguments.view == "markdown")
    ):
        _reject("FIELD_VALUE_INVALID")
    return arguments


def _load_baseline(path: str) -> dict[str, object]:
    descriptor = -1
    try:
        metadata = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BASELINE_BYTES:
            _reject("BASELINE_SCHEMA_INVALID")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = os.fstat(descriptor)
        if _metadata_tuple(opened) != _metadata_tuple(metadata):
            _reject("BASELINE_SCHEMA_INVALID")
        data = _read_fd_bytes(descriptor, MAX_BASELINE_BYTES)
        if len(data) > MAX_BASELINE_BYTES or _metadata_tuple(os.fstat(descriptor)) != _metadata_tuple(opened):
            _reject("BASELINE_SCHEMA_INVALID")
    except PortfolioContractError:
        raise
    except OSError as exc:
        raise PortfolioContractError("BASELINE_SCHEMA_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        if data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("BOM")
        parsed = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PortfolioContractError("BASELINE_SCHEMA_INVALID") from exc
    if not isinstance(parsed, dict):
        _reject("BASELINE_SCHEMA_INVALID")
    return parsed


def _snapshot_exit_code(snapshot: dict[str, object]) -> int:
    return {
        "COMPLETE": 0,
        "PARTIAL": 3,
        "UNKNOWN": 3,
        "BLOCKED": 4,
    }[snapshot["portfolio_state"]]


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse_args(argv)
        registry = load_registry(Path(arguments.portfolio_root))
        baseline = _load_baseline(arguments.baseline) if arguments.baseline else None
        snapshot = build_portfolio_snapshot(registry, baseline=baseline)
        if arguments.handoff_project is not None:
            output: object = build_project_handoff(snapshot, arguments.handoff_project)
            exit_code = 0
        elif arguments.view == "markdown":
            sys.stdout.write(render_portfolio_markdown(snapshot))
            return _snapshot_exit_code(snapshot)
        else:
            output = snapshot
            exit_code = _snapshot_exit_code(snapshot)
        json.dump(output, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        return exit_code
    except PortfolioContractError as exc:
        sys.stderr.write(f"portfolio rejected: {exc.reason}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
