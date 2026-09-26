#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Install, verify, or conservatively roll back the supervisor Skill."""

from __future__ import annotations

import argparse
import base64
import copy
import ctypes
import errno
import hashlib
import hmac
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


LEGACY_INSTALL_SCHEMA_VERSION = 1
INSTALL_SCHEMA_VERSION = 2
RECOVERY_SCHEMA_VERSION = 1
LEGACY_UPGRADE_SCHEMA_VERSION = 1
ROUTE_A_UPGRADE_SCHEMA_VERSION = 2
LEGACY_JOURNAL_SCHEMA_VERSION = 1
ROUTE_A_JOURNAL_SCHEMA_VERSION = 2
LEGACY_RESTORE_SCHEMA_VERSION = 1
ROUTE_A_RESTORE_SCHEMA_VERSION = 2
LEGACY_UPGRADE_RECEIPT_SCHEMA_VERSION = 1
ROUTE_A_UPGRADE_RECEIPT_SCHEMA_VERSION = 2
LEGACY_RESTORE_RECEIPT_SCHEMA_VERSION = 1
ROUTE_A_RESTORE_RECEIPT_SCHEMA_VERSION = 2
SWITCH_BACKEND_EVIDENCE_SCHEMA_VERSION = 1
TOGGLE_SCHEMA_VERSION = 1
PREFLIGHT_SCHEMA_VERSION = 1
PREFLIGHT_SELECTION_SOURCES = frozenset({"CODEX_HOME", "EXPLICIT_SKILLS_ROOT"})
MIN_SUPPORTED_PYTHON = (3, 11, 0)
MAX_EXCLUSIVE_SUPPORTED_PYTHON = (3, 15, 0)
MIN_SUPPORTED_MACOS_MAJOR = 14
SKILL_NAME = "vibe-project-lead-zh"
MANIFEST_NAME = "install-manifest.json"
PREPARED_MANIFEST_NAME = "prepared-manifest.json"
AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
DARWIN_AT_FDCWD = -2
DARWIN_RENAME_SWAP = 0x00000002
DARWIN_RENAME_EXCL = 0x00000004
RENAMEAT2_SYSCALLS = {
    "x86_64": 316,
    "amd64": 316,
    "aarch64": 276,
    "arm64": 276,
}
WSLPATH = Path("/usr/bin/wslpath")
WINDOWS_POWERSHELL = Path(
    "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"
)
WINDOWS_TARGET_EXISTS_EXIT = 17
WINDOWS_MOVE_TIMEOUT_SECONDS = 15
WINDOWS_MOVEFILEEX_NOREPLACE = "WINDOWS_MOVEFILEEX_NOREPLACE"
ROUTE_A_SWITCH_CAPABILITY = "NOREPLACE_ONLY"
EXPECTED_RUNTIME_ENTRIES = {
    "SKILL.md": "file",
    "agents": "directory",
    "agents/openai.yaml": "file",
    "references": "directory",
    "references/project-binding.md": "file",
    "references/evidence-screening.md": "file",
    "references/manager-workflow.md": "file",
    "references/safety-gates.md": "file",
    "references/acceptance-and-supervision.md": "file",
    "references/adaptive-delegation.md": "file",
    "references/portfolio.md": "file",
    "references/deployment-governance.md": "file",
    "references/human-delivery.md": "file",
    "references/SKILL_INDEX_ZH.md": "file",
    "scripts": "directory",
    "scripts/evidence_filter.py": "file",
}
RECOVERY_ROOT_NAME = ".skill-rollbacks"
RECOVERY_PREPARED_MANIFEST_NAME = "staging-recovery-prepared.json"
RECOVERY_FINAL_MANIFEST_NAME = "staging-recovery-manifest.json"
RECOVERY_REQUEST_KEYS = frozenset(
    {
        "recovery_schema_version",
        "operation",
        "source",
        "source_stage",
        "skills_root",
        "target",
        "install_state",
        "archive_root",
        "codex_home_identity",
        "skills_root_identity",
        "source_root_identity",
        "stage_root_identity",
        "source_entries",
        "stage_entries",
        "source_tree_digest",
        "stage_tree_digest",
        "target_filesystem",
        "runtime_layout",
        "neighbor_digest",
        "target_absent",
        "install_state_absent",
        "archive_root_condition",
    }
)
RECOVERY_PREPARED_MANIFEST_KEYS = frozenset(
    {
        "recovery_schema_version",
        "operation",
        "phase",
        "recovery_request_digest",
        "source",
        "source_stage",
        "skills_root",
        "target",
        "install_state",
        "archive_root",
        "archive_destination",
        "source_entries",
        "stage_entries",
        "source_tree_digest",
        "stage_tree_digest",
        "source_root_identity",
        "stage_root_identity",
        "target_filesystem",
        "neighbor_digest",
        "target_absent",
        "install_state_absent",
        "prepared_at_utc",
        "manifest_digest",
    }
)
RECOVERY_FINAL_MANIFEST_KEYS = frozenset(
    (RECOVERY_PREPARED_MANIFEST_KEYS - {"manifest_digest"})
    | {
        "prepared_manifest_digest",
        "archived_at_utc",
        "archived_stage_root_identity",
        "source_stage_absent",
        "post_neighbor_digest",
        "manifest_digest",
    }
)
V1_INSTALL_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "source",
        "target",
        "phase",
        "installed_at_utc",
        "entries",
        "manifest_digest",
    }
)
V2_INSTALL_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "source",
        "target",
        "phase",
        "installed_at_utc",
        "mode_policy",
        "source_root_mode",
        "target_root_mode",
        "source_entries",
        "entries",
        "mode_capability",
        "target_filesystem",
        "manifest_digest",
    }
)
TARGET_FILESYSTEM_KEYS = frozenset(
    {"device", "mount_target", "filesystem_type", "mount_options_sha256"}
)
TARGET_OBSERVED_CAPABILITY_KEYS = frozenset(
    {
        "status",
        "probe_relative_path",
        "directory_requested_modes",
        "directory_observed_modes",
        "file_requested_modes",
        "file_observed_modes",
        "directory_identity_before",
        "directory_identity_after",
        "file_identity_before",
        "file_identity_after",
        "file_size",
        "file_sha256",
        "pre_stage_tree_digest",
        "post_stage_tree_digest",
        "restored_directory_mode",
        "restored_file_mode",
    }
)
PROBE_IDENTITY_KEYS = frozenset({"type", "device", "inode"})
DIRECTORY_IDENTITY_KEYS = frozenset(
    {"type", "mode", "device", "inode", "size", "nlink", "mtime_ns"}
)
STABLE_DIRECTORY_IDENTITY_KEYS = frozenset({"type", "device", "inode"})
WSL_MOUNT_IDENTITY_KEYS = frozenset(
    {"mount_target", "filesystem_type", "mount_options_sha256"}
)
WINDOWS_VOLUME_IDENTITY_KEYS = frozenset(
    {"drive", "filesystem_name", "volume_serial"}
)
BACKEND_PROBE_IDENTITY_KEYS = frozenset(
    {
        "probe_id",
        "probe_root",
        "source_relative",
        "forward_destination_relative",
        "collision_destination_relative",
        "source_marker_sha256",
        "collision_marker_sha256",
    }
)
BACKEND_MOVE_EVIDENCE_KEYS = frozenset(
    {
        "status",
        "call_count",
        "return_classification",
        "win32_error",
        "source_before",
        "destination_before",
        "source_after",
        "destination_after",
    }
)
BACKEND_CLEANUP_EVIDENCE_KEYS = frozenset(
    {"status", "removed_relative_paths", "residue"}
)
WINDOWS_MOVE_RESULT_KEYS = frozenset(
    {"backend", "classification", "movefileex_call_count", "win32_error"}
)
SWITCH_BACKEND_EVIDENCE_V1_KEYS = frozenset(
    {
        "schema_version",
        "evidence_id",
        "approval_id",
        "backend",
        "capability",
        "installer_sha256",
        "backend_implementation_digest",
        "skills_root",
        "skills_root_stable_identity",
        "target_filesystem_identity",
        "wsl_mount_identity",
        "windows_volume_identity",
        "probe_identity",
        "forward_move",
        "collision_guard",
        "restore_move",
        "cleanup",
        "created_at_utc",
        "evidence_digest",
    }
)
SWITCH_CAPABILITIES = frozenset(
    {"EXCHANGE_SUPPORTED", "NOREPLACE_ONLY", "UNKNOWN"}
)
SWITCH_EVIDENCE_KEYS = frozenset(
    {
        "capability",
        "exchange_attempted",
        "exchange_result",
        "noreplace_attempted",
        "noreplace_result",
        "postconditions_verified",
        "restored",
        "left_identity_before",
        "right_identity_before",
        "left_identity_after",
        "right_identity_after",
        "left_identity_restored",
        "right_identity_restored",
    }
)
CANDIDATE_INVENTORY_KEYS = frozenset(
    {
        "state",
        "proof_kind",
        "discovery_id",
        "declared_name",
        "enabled",
        "duplicate_count",
        "load_errors",
        "future_locator",
        "source_skill_sha256",
    }
)
UPGRADE_REQUEST_V1_KEYS = frozenset(
    {
        "upgrade_schema_version",
        "journal_schema_version",
        "operation",
        "operation_id",
        "source",
        "source_head",
        "source_tree_digest",
        "source_root_identity",
        "target",
        "manifest",
        "old_manifest_digest",
        "old_target_identity",
        "old_target_tree_digest",
        "old_state_identity",
        "old_state_tree_digest",
        "new_stage_identity",
        "target_filesystem_identity",
        "mode_policy",
        "mode_capability",
        "candidate_inventory",
        "switch_capability",
        "switch_evidence",
        "approval_id",
        "recovery_directory",
        "phase",
        "previous_phase_digest",
        "created_at_utc",
        "request_digest",
    }
)
NOREPLACE_UPGRADE_PHASES = (
    "PREPARED",
    "OLD_SNAPSHOT_READY",
    "OLD_TARGET_ARCHIVED",
    "OLD_STATE_ARCHIVED",
    "NEW_TARGET_ACTIVE",
    "NEW_STATE_ACTIVE",
    "VERIFIED",
)
EXCHANGE_UPGRADE_PHASES = (
    "PREPARED",
    "OLD_SNAPSHOT_READY",
    "TARGET_EXCHANGED",
    "OLD_TARGET_ARCHIVED",
    "STATE_EXCHANGED",
    "OLD_STATE_ARCHIVED",
    "VERIFIED",
)
UPGRADE_TERMINAL_PHASES = frozenset({"UNKNOWN", "RECOVERY_REQUIRED"})
UPGRADE_OBSERVED_KEYS = frozenset(
    {
        "active_target",
        "active_state",
        "staging_target",
        "staging_state",
        "recovery_target",
        "recovery_state",
    }
)
UPGRADE_JOURNAL_RECEIPT_V1_KEYS = frozenset(
    {
        "journal_schema_version",
        "operation",
        "operation_id",
        "request_digest",
        "source_head",
        "source_tree_digest",
        "old_manifest_digest",
        "old_target_identity",
        "new_stage_identity",
        "target_filesystem_identity",
        "switch_capability",
        "phase",
        "previous_phase_digest",
        "observed_postconditions",
        "recovery_directory",
        "approval_id",
        "created_at_utc",
        "receipt_digest",
    }
)
UPGRADE_SUCCESS_RECEIPT_V1_KEYS = frozenset(
    {
        "receipt_schema_version",
        "operation",
        "operation_id",
        "status",
        "request_digest",
        "approval_id",
        "switch_capability",
        "gap_disclosure",
        "old_archive",
        "new_active",
        "journal",
        "journal_final_digest",
        "restore_confirmation_digest",
        "created_at_utc",
        "receipt_digest",
    }
)
UPGRADE_INSPECTION_V1_KEYS = frozenset(
    {
        "inspection_schema_version",
        "operation_id",
        "request_digest",
        "switch_capability",
        "classification",
        "exit_code",
        "last_phase",
        "journal_final_digest",
        "receipt_count",
        "observed_postconditions",
        "reasons",
    }
)
RESTORE_REQUEST_V1_KEYS = frozenset(
    {
        "restore_schema_version",
        "journal_schema_version",
        "operation",
        "operation_id",
        "source_receipt",
        "source_receipt_digest",
        "source_receipt_identity",
        "source_receipt_sha256",
        "source_upgrade_operation_id",
        "source_upgrade_request_digest",
        "target",
        "state",
        "source_archive_target",
        "source_archive_state",
        "active_target_snapshot",
        "active_state_snapshot",
        "archive_target_snapshot",
        "archive_state_snapshot",
        "active_manifest_digest",
        "archive_manifest_digest",
        "target_filesystem_identity",
        "switch_capability",
        "switch_evidence",
        "approval_id",
        "phase",
        "previous_phase_digest",
        "recovery_directory",
        "created_at_utc",
        "request_digest",
    }
)
RESTORE_OBSERVED_KEYS = frozenset(
    {
        "active_target",
        "active_state",
        "staging_target",
        "staging_state",
        "recovery_target",
        "recovery_state",
        "source_archive_target",
        "source_archive_state",
    }
)
RESTORE_JOURNAL_RECEIPT_V1_KEYS = frozenset(
    {
        "journal_schema_version",
        "operation",
        "operation_id",
        "request_digest",
        "source_receipt_digest",
        "switch_capability",
        "phase",
        "previous_phase_digest",
        "observed_postconditions",
        "recovery_directory",
        "approval_id",
        "created_at_utc",
        "receipt_digest",
    }
)
RESTORE_SUCCESS_RECEIPT_V1_KEYS = frozenset(
    {
        "receipt_schema_version",
        "operation",
        "operation_id",
        "status",
        "request_digest",
        "source_receipt",
        "source_receipt_digest",
        "approval_id",
        "switch_capability",
        "gap_disclosure",
        "restored_active",
        "archived_replaced_version",
        "journal",
        "journal_final_digest",
        "force_reload_state",
        "created_at_utc",
        "receipt_digest",
    }
)
ROUTE_A_BINDING_KEYS = frozenset(
    {
        "switch_backend",
        "switch_evidence_digest",
        "backend_implementation_digest",
        "skills_root_stable_identity",
        "wsl_mount_identity",
        "windows_volume_identity",
    }
)
UPGRADE_REQUEST_V2_KEYS = (
    UPGRADE_REQUEST_V1_KEYS - {"switch_evidence"}
) | ROUTE_A_BINDING_KEYS
RESTORE_REQUEST_V2_KEYS = (
    RESTORE_REQUEST_V1_KEYS - {"switch_evidence"}
) | ROUTE_A_BINDING_KEYS
UPGRADE_JOURNAL_RECEIPT_V2_KEYS = UPGRADE_JOURNAL_RECEIPT_V1_KEYS | {
    "switch_backend",
    "switch_evidence_digest",
    "backend_implementation_digest",
}
UPGRADE_SUCCESS_RECEIPT_V2_KEYS = UPGRADE_SUCCESS_RECEIPT_V1_KEYS | {
    "switch_backend",
    "switch_evidence_digest",
    "backend_implementation_digest",
}
UPGRADE_INSPECTION_V2_KEYS = UPGRADE_INSPECTION_V1_KEYS | {
    "upgrade_schema_version",
    "switch_backend",
    "switch_evidence_digest",
    "backend_implementation_digest",
}
RESTORE_JOURNAL_RECEIPT_V2_KEYS = RESTORE_JOURNAL_RECEIPT_V1_KEYS | {
    "switch_backend",
    "switch_evidence_digest",
    "backend_implementation_digest",
}
RESTORE_SUCCESS_RECEIPT_V2_KEYS = RESTORE_SUCCESS_RECEIPT_V1_KEYS | {
    "switch_backend",
    "switch_evidence_digest",
    "backend_implementation_digest",
}
NOREPLACE_RESTORE_PHASES = (
    "PREPARED",
    "ARCHIVE_COPY_READY",
    "CURRENT_TARGET_ARCHIVED",
    "CURRENT_STATE_ARCHIVED",
    "RESTORED_TARGET_ACTIVE",
    "RESTORED_STATE_ACTIVE",
    "VERIFIED",
)
EXCHANGE_RESTORE_PHASES = (
    "PREPARED",
    "ARCHIVE_COPY_READY",
    "TARGET_EXCHANGED",
    "CURRENT_TARGET_ARCHIVED",
    "STATE_EXCHANGED",
    "CURRENT_STATE_ARCHIVED",
    "VERIFIED",
)
RESTORE_TERMINAL_PHASES = frozenset({"UNKNOWN", "RECOVERY_REQUIRED"})
FILE_IDENTITY_KEYS = frozenset(
    {"type", "mode", "device", "inode", "size", "nlink", "mtime_ns"}
)
TOGGLE_REQUEST_KEYS = frozenset(
    {
        "toggle_schema_version",
        "operation",
        "approval_id",
        "config_path",
        "config_identity",
        "config_sha256",
        "locator",
        "locator_identity",
        "locator_sha256",
        "declared_name",
        "inventory_identity",
        "current_enabled",
        "requested_enabled",
        "config_entry_state",
        "native_request",
        "force_reload_required",
        "created_at_utc",
        "request_digest",
    }
)


class InstallError(RuntimeError):
    def __init__(
        self,
        reason: str,
        exit_code: int = 2,
        differences: list[str] | None = None,
        status: str | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code
        self.differences = differences or []
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def emit(status: str, exit_code: int = 0, **values: Any) -> int:
    print(json.dumps({"status": status, **values}, ensure_ascii=False, sort_keys=True))
    return exit_code


def canonical_digest(value: dict[str, Any]) -> str:
    material = {key: child for key, child in value.items() if key != "manifest_digest"}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _is_git_object_id(value: Any) -> bool:
    if not isinstance(value, str) or len(value) not in {40, 64}:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _is_safe_identifier(value: Any, *, maximum: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and all(0x20 <= ord(character) < 0x7F for character in value)
    )


def _is_safe_path_identifier(value: Any, *, maximum: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is not None
        and value not in {".", ".."}
    )


def upgrade_phase_graph(capability: str) -> tuple[str, ...]:
    if capability == "NOREPLACE_ONLY":
        return NOREPLACE_UPGRADE_PHASES
    if capability == "EXCHANGE_SUPPORTED":
        return EXCHANGE_UPGRADE_PHASES
    raise InstallError("upgrade_phase_invalid")


def validate_upgrade_transition(
    capability: str,
    previous_phase: str | None,
    phase: str,
) -> None:
    graph = upgrade_phase_graph(capability)
    if previous_phase is None:
        if phase != graph[0]:
            raise InstallError("upgrade_phase_invalid")
        return
    if previous_phase in UPGRADE_TERMINAL_PHASES or previous_phase == graph[-1]:
        raise InstallError("upgrade_phase_invalid")
    if phase in UPGRADE_TERMINAL_PHASES:
        if previous_phase not in graph[:-1]:
            raise InstallError("upgrade_phase_invalid")
        return
    try:
        previous_index = graph.index(previous_phase)
    except ValueError as error:
        raise InstallError("upgrade_phase_invalid") from error
    if previous_index + 1 >= len(graph) or graph[previous_index + 1] != phase:
        raise InstallError("upgrade_phase_invalid")


def journal_receipt_digest(receipt: dict[str, Any]) -> str:
    material = {
        key: value for key, value in receipt.items() if key != "receipt_digest"
    }
    return _canonical_json_digest(material)


def success_receipt_digest(receipt: dict[str, Any]) -> str:
    material = {
        key: value for key, value in receipt.items() if key != "receipt_digest"
    }
    return _canonical_json_digest(material)


def restore_phase_graph(capability: str) -> tuple[str, ...]:
    if capability == "NOREPLACE_ONLY":
        return NOREPLACE_RESTORE_PHASES
    if capability == "EXCHANGE_SUPPORTED":
        return EXCHANGE_RESTORE_PHASES
    raise InstallError("restore_switch_capability_invalid")


def validate_restore_transition(
    capability: str,
    previous_phase: str | None,
    phase: str,
) -> None:
    graph = restore_phase_graph(capability)
    if previous_phase is None:
        if phase != graph[0]:
            raise InstallError("restore_phase_transition_invalid")
        return
    if previous_phase in RESTORE_TERMINAL_PHASES or previous_phase == graph[-1]:
        raise InstallError("restore_phase_transition_invalid")
    if phase in RESTORE_TERMINAL_PHASES:
        if previous_phase not in graph[:-1]:
            raise InstallError("restore_phase_transition_invalid")
        return
    try:
        previous_index = graph.index(previous_phase)
    except ValueError as error:
        raise InstallError("restore_phase_transition_invalid") from error
    if previous_index + 1 >= len(graph) or graph[previous_index + 1] != phase:
        raise InstallError("restore_phase_transition_invalid")


def restore_request_digest(request: dict[str, Any]) -> str:
    material = {key: value for key, value in request.items() if key != "request_digest"}
    return _canonical_json_digest(material)


def restore_journal_receipt_digest(receipt: dict[str, Any]) -> str:
    material = {
        key: value for key, value in receipt.items() if key != "receipt_digest"
    }
    return _canonical_json_digest(material)


def toggle_request_digest(request: dict[str, Any]) -> str:
    material = {key: value for key, value in request.items() if key != "request_digest"}
    return _canonical_json_digest(material)


def _upgrade_journal_invalid() -> InstallError:
    return InstallError("upgrade_journal_invalid", 4, status="unknown")


def _validate_directory_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != DIRECTORY_IDENTITY_KEYS:
        raise InstallError("upgrade_request_invalid")
    if value.get("type") != "directory":
        raise InstallError("upgrade_request_invalid")
    for key in ("mode", "device", "inode", "size", "nlink", "mtime_ns"):
        if not _is_integer(value.get(key)) or value[key] < 0:
            raise InstallError("upgrade_request_invalid")


def _validate_probe_directory_snapshot(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != DIRECTORY_IDENTITY_KEYS | {"tree_digest"}
        or not _is_sha256(value.get("tree_digest"))
    ):
        raise InstallError("upgrade_request_invalid")
    _validate_directory_identity(
        {key: value[key] for key in DIRECTORY_IDENTITY_KEYS}
    )


def _validate_entries(value: Any) -> None:
    if not isinstance(value, dict):
        raise InstallError("manifest_invalid")
    for relative, entry in value.items():
        if not isinstance(relative, str) or not relative:
            raise InstallError("manifest_invalid")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or pure.as_posix() != relative
            or "." in pure.parts
            or ".." in pure.parts
        ):
            raise InstallError("manifest_invalid")
        if not isinstance(entry, dict):
            raise InstallError("manifest_invalid")
        entry_type = entry.get("type")
        mode = entry.get("mode")
        if not _is_integer(mode) or not 0 <= mode <= 0o7777:
            raise InstallError("manifest_invalid")
        if entry_type == "directory":
            if set(entry) != {"type", "mode"}:
                raise InstallError("manifest_invalid")
            continue
        if entry_type != "file" or set(entry) != {
            "type",
            "mode",
            "size",
            "sha256",
        }:
            raise InstallError("manifest_invalid")
        if not _is_integer(entry["size"]) or entry["size"] < 0:
            raise InstallError("manifest_invalid")
        if not _is_sha256(entry["sha256"]):
            raise InstallError("manifest_invalid")


def directory_identity(path: Path, reason: str) -> dict[str, Any]:
    absolute = Path(os.path.abspath(path))
    try:
        metadata = os.lstat(absolute)
    except OSError as error:
        raise InstallError(reason) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InstallError(reason)
    return {
        "type": "directory",
        "mode": stat.S_IMODE(metadata.st_mode),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
        "nlink": metadata.st_nlink,
        "mtime_ns": metadata.st_mtime_ns,
    }


def _decode_mountinfo_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _linux_filesystem_identity(path: Path) -> dict[str, Any]:
    absolute = Path(os.path.abspath(path))
    try:
        device = os.lstat(absolute).st_dev
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise InstallError("filesystem_identity_unavailable", 4) from error

    selected: tuple[int, str, str, list[str]] | None = None
    absolute_text = str(absolute)
    for line in lines:
        if " - " not in line:
            continue
        left_text, right_text = line.split(" - ", 1)
        left = left_text.split()
        right = right_text.split()
        if len(left) < 6 or len(right) < 3:
            continue
        mount_target = _decode_mountinfo_path(left[4])
        prefix = mount_target.rstrip("/") + "/"
        if absolute_text != mount_target and not absolute_text.startswith(prefix):
            continue
        options = left[5].split(",") + right[2].split(",")
        candidate = (len(mount_target), mount_target, right[0], options)
        if selected is None or candidate[0] > selected[0]:
            selected = candidate

    if selected is None:
        raise InstallError("filesystem_identity_unavailable", 4)
    _, mount_target, filesystem_type, options = selected
    normalized_options = ",".join(sorted(option for option in options if option))
    return {
        "device": device,
        "mount_target": mount_target,
        "filesystem_type": filesystem_type,
        "mount_options_sha256": hashlib.sha256(
            normalized_options.encode("utf-8")
        ).hexdigest(),
    }


class _DarwinFsid(ctypes.Structure):
    _fields_ = [("val", ctypes.c_int32 * 2)]


class _DarwinStatfs(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", _DarwinFsid),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_reserved", ctypes.c_uint32 * 8),
    ]


def _darwin_statfs(path: Path) -> dict[str, int | str]:
    unavailable = "filesystem_identity_unavailable"
    try:
        statfs_call = ctypes.CDLL(None, use_errno=True).statfs
        statfs_call.argtypes = [ctypes.c_char_p, ctypes.POINTER(_DarwinStatfs)]
        statfs_call.restype = ctypes.c_int
        record = _DarwinStatfs()
        if statfs_call(os.fsencode(path), ctypes.byref(record)) != 0:
            raise InstallError(unavailable, 4)

        def decoded_field(name: str, size: int) -> str:
            raw = ctypes.string_at(
                ctypes.addressof(record) + getattr(_DarwinStatfs, name).offset,
                size,
            )
            if b"\0" not in raw:
                raise InstallError(unavailable, 4)
            return raw.split(b"\0", 1)[0].decode("utf-8", errors="strict")

        mount_target = decoded_field("f_mntonname", 1024)
        filesystem_type = decoded_field("f_fstypename", 16)
        if not os.path.isabs(mount_target) or not filesystem_type:
            raise InstallError(unavailable, 4)
        mount_metadata = os.lstat(Path(mount_target))
        if not stat.S_ISDIR(mount_metadata.st_mode):
            raise InstallError(unavailable, 4)
    except (OSError, AttributeError, UnicodeError, ValueError) as error:
        raise InstallError(unavailable, 4) from error
    return {
        "device": mount_metadata.st_dev,
        "mount_target": mount_target,
        "filesystem_type": filesystem_type,
        "mount_flags": record.f_flags,
    }


def _darwin_filesystem_identity(path: Path) -> dict[str, Any]:
    unavailable = "filesystem_identity_unavailable"
    absolute = Path(os.path.abspath(path))
    try:
        before = directory_identity(absolute, unavailable)
        observed = _darwin_statfs(absolute)
        after = directory_identity(absolute, unavailable)
    except InstallError as error:
        raise InstallError(unavailable, 4) from error
    if (
        before != after
        or not _is_integer(observed.get("device"))
        or before["device"] != observed["device"]
        or not isinstance(observed.get("mount_target"), str)
        or not os.path.isabs(observed["mount_target"])
        or not isinstance(observed.get("filesystem_type"), str)
        or not observed["filesystem_type"]
        or not _is_integer(observed.get("mount_flags"))
        or not 0 <= observed["mount_flags"] <= 0xFFFFFFFF
    ):
        raise InstallError(unavailable, 4)
    normalized_options = f"darwin:{observed['mount_flags']:08x}"
    return {
        "device": observed["device"],
        "mount_target": observed["mount_target"],
        "filesystem_type": observed["filesystem_type"],
        "mount_options_sha256": hashlib.sha256(
            normalized_options.encode("utf-8")
        ).hexdigest(),
    }


def filesystem_identity(path: Path) -> dict[str, Any]:
    if sys.platform == "darwin":
        return _darwin_filesystem_identity(path)
    if sys.platform.startswith("linux"):
        return _linux_filesystem_identity(path)
    raise InstallError("filesystem_identity_unavailable", 4)


def require_nofollow(reason: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int) or nofollow == 0:
        raise InstallError(reason, 4)
    return nofollow


def write_json_exclusive(
    path: Path,
    value: dict[str, Any],
    collision_reason: str = "install_state_collision",
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | require_nofollow("exclusive_manifest_unavailable")
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        if error.errno in {errno.EEXIST, errno.ELOOP}:
            raise InstallError(collision_reason) from error
        raise
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def hash_open_file(path: Path) -> tuple[str, int, int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | require_nofollow("unsafe_source_entry")
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise InstallError("unsafe_source_entry") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallError("unsafe_source_entry")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InstallError("source_changed_during_scan")
        return digest.hexdigest(), after.st_size, stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


def read_bounded_regular_file(
    path: Path,
    reason: str,
    *,
    maximum_bytes: int = 1024 * 1024,
) -> tuple[bytes, dict[str, Any], str]:
    absolute = Path(os.path.abspath(path))
    if not path.is_absolute() or absolute != path:
        raise InstallError(reason)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | require_nofollow(reason)
    )
    try:
        descriptor = os.open(absolute, flags)
    except OSError as error:
        raise InstallError(reason) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            raise InstallError(reason)
        content = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, maximum_bytes + 1)):
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise InstallError(reason)
        after = os.fstat(descriptor)
        identity_fields = (
            before.st_dev,
            before.st_ino,
            stat.S_IMODE(before.st_mode),
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
        )
        after_fields = (
            after.st_dev,
            after.st_ino,
            stat.S_IMODE(after.st_mode),
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_fields != after_fields or len(content) != after.st_size:
            raise InstallError(reason, 3)
        identity = {
            "type": "file",
            "mode": stat.S_IMODE(after.st_mode),
            "device": after.st_dev,
            "inode": after.st_ino,
            "size": after.st_size,
            "nlink": after.st_nlink,
            "mtime_ns": after.st_mtime_ns,
        }
        return bytes(content), identity, hashlib.sha256(content).hexdigest()
    except OSError as error:
        raise InstallError(reason) from error
    finally:
        os.close(descriptor)


def current_installer_sha256() -> str:
    installer = Path(os.path.abspath(__file__))
    _, identity, digest = read_bounded_regular_file(
        installer,
        "installer_identity_invalid",
    )
    if identity["nlink"] != 1:
        raise InstallError("installer_identity_invalid")
    return digest


def backend_implementation_digest(backend: str, installer_sha256: str) -> str:
    if backend != WINDOWS_MOVEFILEEX_NOREPLACE or not _is_sha256(installer_sha256):
        raise InstallError("switch_backend_evidence_invalid")
    return _canonical_json_digest(
        {"backend": backend, "installer_sha256": installer_sha256}
    )


def stable_directory_identity(path: Path, reason: str) -> dict[str, Any]:
    identity = directory_identity(path, reason)
    return {key: identity[key] for key in ("type", "device", "inode")}


def wsl_mount_identity(path: Path) -> dict[str, Any]:
    identity = filesystem_identity(path)
    return {
        "mount_target": identity["mount_target"],
        "filesystem_type": identity["filesystem_type"],
        "mount_options_sha256": identity["mount_options_sha256"],
    }


def switch_backend_evidence_digest(value: dict[str, Any]) -> str:
    material = {
        key: child for key, child in value.items() if key != "evidence_digest"
    }
    return _canonical_json_digest(material)


def _validate_backend_stable_identity(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != STABLE_DIRECTORY_IDENTITY_KEYS
        or value.get("type") != "directory"
    ):
        raise InstallError("switch_backend_evidence_invalid")
    for key in ("device", "inode"):
        if not _is_integer(value.get(key)) or value[key] < 0:
            raise InstallError("switch_backend_evidence_invalid")


def _validate_backend_wsl_mount(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != WSL_MOUNT_IDENTITY_KEYS:
        raise InstallError("switch_backend_evidence_invalid")
    if (
        not isinstance(value.get("mount_target"), str)
        or re.fullmatch(r"/mnt/[a-z]", value["mount_target"]) is None
        or not isinstance(value.get("filesystem_type"), str)
        or not value["filesystem_type"]
        or not _is_sha256(value.get("mount_options_sha256"))
    ):
        raise InstallError("switch_backend_evidence_invalid")


def _validate_backend_windows_volume(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != WINDOWS_VOLUME_IDENTITY_KEYS:
        raise InstallError("switch_backend_evidence_invalid")
    if (
        re.fullmatch(r"[A-Z]:", value.get("drive", "")) is None
        or re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,31}",
            value.get("filesystem_name", ""),
        )
        is None
        or re.fullmatch(r"[0-9A-F]{8}", value.get("volume_serial", ""))
        is None
    ):
        raise InstallError("switch_backend_evidence_invalid")


def _validate_backend_directory_snapshot(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != DIRECTORY_IDENTITY_KEYS | {"tree_digest"}
        or value.get("type") != "directory"
        or not _is_sha256(value.get("tree_digest"))
    ):
        raise InstallError("switch_backend_evidence_invalid")
    for key in ("mode", "device", "inode", "size", "nlink", "mtime_ns"):
        if not _is_integer(value.get(key)) or value[key] < 0:
            raise InstallError("switch_backend_evidence_invalid")


def _validate_backend_move_evidence(
    value: Any,
    *,
    classification: str,
) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != BACKEND_MOVE_EVIDENCE_KEYS
        or value.get("status") != "VERIFIED"
        or value.get("return_classification") != classification
        or value.get("call_count") != 1
        or isinstance(value.get("call_count"), bool)
        or not _is_integer(value.get("win32_error"))
    ):
        raise InstallError("switch_backend_evidence_invalid")
    for key in (
        "source_before",
        "destination_before",
        "source_after",
        "destination_after",
    ):
        child = value.get(key)
        if child is not None:
            _validate_backend_directory_snapshot(child)
    if classification == "VERIFIED":
        if (
            value["win32_error"] != 0
            or value["source_before"] is None
            or value["destination_before"] is not None
            or value["source_after"] is not None
            or value["destination_after"] != value["source_before"]
        ):
            raise InstallError("switch_backend_evidence_invalid")
        return
    if (
        classification != "TARGET_EXISTS"
        or value["win32_error"] not in {80, 183}
        or value["source_before"] is None
        or value["destination_before"] is None
        or value["source_after"] != value["source_before"]
        or value["destination_after"] != value["destination_before"]
        or value["source_before"] == value["destination_before"]
    ):
        raise InstallError("switch_backend_evidence_invalid")


def validate_switch_backend_evidence(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != SWITCH_BACKEND_EVIDENCE_V1_KEYS:
        raise InstallError("switch_backend_evidence_invalid")
    if (
        value.get("schema_version") != SWITCH_BACKEND_EVIDENCE_SCHEMA_VERSION
        or isinstance(value.get("schema_version"), bool)
        or re.fullmatch(r"[0-9a-f]{32}", value.get("evidence_id", "")) is None
        or not _is_safe_identifier(value.get("approval_id"))
        or value.get("backend") != WINDOWS_MOVEFILEEX_NOREPLACE
        or value.get("capability") != ROUTE_A_SWITCH_CAPABILITY
        or not _is_sha256(value.get("installer_sha256"))
        or not _is_sha256(value.get("backend_implementation_digest"))
        or not isinstance(value.get("created_at_utc"), str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
            value["created_at_utc"],
        )
        is None
        or not _is_sha256(value.get("evidence_digest"))
    ):
        raise InstallError("switch_backend_evidence_invalid")

    skills_root_text = value.get("skills_root")
    if not isinstance(skills_root_text, str):
        raise InstallError("switch_backend_evidence_invalid")
    skills_root = Path(skills_root_text)
    if not skills_root.is_absolute() or skills_root != Path(os.path.abspath(skills_root)):
        raise InstallError("switch_backend_evidence_invalid")

    _validate_backend_stable_identity(value.get("skills_root_stable_identity"))
    try:
        _validate_target_filesystem(value.get("target_filesystem_identity"))
    except InstallError as error:
        raise InstallError("switch_backend_evidence_invalid") from error
    _validate_backend_wsl_mount(value.get("wsl_mount_identity"))
    _validate_backend_windows_volume(value.get("windows_volume_identity"))

    filesystem = value["target_filesystem_identity"]
    mount = value["wsl_mount_identity"]
    volume = value["windows_volume_identity"]
    if (
        value["skills_root_stable_identity"]["device"] != filesystem["device"]
        or {
            key: filesystem[key]
            for key in WSL_MOUNT_IDENTITY_KEYS
        }
        != mount
        or volume["drive"] != f"{mount['mount_target'][-1].upper()}:"
    ):
        raise InstallError("switch_backend_evidence_invalid")

    probe = value.get("probe_identity")
    if not isinstance(probe, dict) or set(probe) != BACKEND_PROBE_IDENTITY_KEYS:
        raise InstallError("switch_backend_evidence_invalid")
    probe_id = probe.get("probe_id")
    probe_root_text = probe.get("probe_root")
    if (
        re.fullmatch(r"[0-9a-f]{32}", probe_id or "") is None
        or not isinstance(probe_root_text, str)
        or not Path(probe_root_text).is_absolute()
        or Path(probe_root_text) != Path(os.path.abspath(probe_root_text))
        or Path(probe_root_text).parent != skills_root
        or Path(probe_root_text).name
        != f".vibe-project-lead-zh-switch-probe-{probe_id}"
        or probe.get("source_relative") != "source"
        or probe.get("forward_destination_relative") != "forward"
        or probe.get("collision_destination_relative") != "collision"
        or not _is_sha256(probe.get("source_marker_sha256"))
        or not _is_sha256(probe.get("collision_marker_sha256"))
    ):
        raise InstallError("switch_backend_evidence_invalid")

    _validate_backend_move_evidence(value.get("forward_move"), classification="VERIFIED")
    _validate_backend_move_evidence(
        value.get("collision_guard"),
        classification="TARGET_EXISTS",
    )
    _validate_backend_move_evidence(value.get("restore_move"), classification="VERIFIED")
    forward = value["forward_move"]
    collision = value["collision_guard"]
    restore = value["restore_move"]
    if (
        forward["destination_after"] != collision["source_before"]
        or collision["source_after"] != restore["source_before"]
        or restore["destination_after"] != forward["source_before"]
    ):
        raise InstallError("switch_backend_evidence_invalid")

    cleanup = value.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or set(cleanup) != BACKEND_CLEANUP_EVIDENCE_KEYS
        or cleanup.get("status") != "VERIFIED"
        or cleanup.get("removed_relative_paths")
        != [
            "source/source-marker.bin",
            "collision/collision-marker.bin",
            "source",
            "collision",
            ".",
        ]
        or cleanup.get("residue") != []
    ):
        raise InstallError("switch_backend_evidence_invalid")

    if value["installer_sha256"] != current_installer_sha256():
        raise InstallError("switch_backend_evidence_invalid")
    if value["backend_implementation_digest"] != backend_implementation_digest(
        value["backend"],
        value["installer_sha256"],
    ):
        raise InstallError("switch_backend_evidence_invalid")
    if value["evidence_digest"] != switch_backend_evidence_digest(value):
        raise InstallError("switch_backend_evidence_invalid")


def scan_tree(root: Path, unsafe_reason: str = "unsafe_source_entry") -> dict[str, dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise InstallError(unsafe_reason)

    def refuse_walk_error(error: OSError) -> None:
        raise InstallError(unsafe_reason) from error

    entries: dict[str, dict[str, Any]] = {}
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=refuse_walk_error,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current)
        for name in directory_names:
            path = current_path / name
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
                raise InstallError(unsafe_reason)
            relative = path.relative_to(root).as_posix()
            entries[relative] = {
                "type": "directory",
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        for name in file_names:
            path = current_path / name
            if path.is_symlink():
                raise InstallError(unsafe_reason)
            try:
                digest, size, mode = hash_open_file(path)
            except InstallError as error:
                raise InstallError(unsafe_reason) from error
            relative = path.relative_to(root).as_posix()
            entries[relative] = {
                "type": "file",
                "mode": mode,
                "size": size,
                "sha256": digest,
            }
    return dict(sorted(entries.items()))


def canonical_tree_digest(entries: dict[str, dict[str, Any]]) -> str:
    encoded = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_content_entries(
    expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    differences: list[str] = []
    for relative in sorted(set(expected) | set(actual)):
        left = expected.get(relative)
        right = actual.get(relative)
        if not isinstance(left, dict) or not isinstance(right, dict):
            differences.append(relative)
            continue
        if left.get("type") != right.get("type"):
            differences.append(relative)
            continue
        if left.get("type") == "file" and (
            left.get("size") != right.get("size")
            or left.get("sha256") != right.get("sha256")
        ):
            differences.append(relative)
    return differences


def mode_differences(
    expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    return [
        relative
        for relative in sorted(set(expected) | set(actual))
        if not isinstance(expected.get(relative), dict)
        or not isinstance(actual.get(relative), dict)
        or expected[relative].get("mode") != actual[relative].get("mode")
    ]


def classify_mode_observations(
    directory_modes: list[int], file_modes: list[int]
) -> str:
    if directory_modes == [0o700, 0o750] and file_modes == [0o600, 0o640]:
        return "strict"
    if (
        len(directory_modes) == 2
        and len(file_modes) == 2
        and directory_modes[0] == directory_modes[1]
        and file_modes[0] == file_modes[1]
        and all(_is_integer(value) for value in directory_modes + file_modes)
    ):
        return "target-observed"
    raise InstallError(
        "mode_capability_unknown",
        4,
        status="unknown",
    )


def _probe_identity(metadata: os.stat_result, expected_type: str) -> dict[str, Any]:
    is_expected = (
        stat.S_ISDIR(metadata.st_mode)
        if expected_type == "directory"
        else stat.S_ISREG(metadata.st_mode)
    )
    if not is_expected:
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    return {
        "type": expected_type,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def _hash_descriptor(descriptor: int) -> tuple[str, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    if (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    return digest.hexdigest(), after.st_size


def probe_mode_capability(
    stage: Path,
    source_root_mode: int,
    source_entries: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, dict[str, Any]]]:
    probe_relative_path = "SKILL.md"
    source_file = source_entries.get(probe_relative_path)
    if not isinstance(source_file, dict) or source_file.get("type") != "file":
        raise InstallError("mode_capability_unknown", 4, status="unknown")

    pre_entries = scan_tree(stage)
    pre_tree_digest = canonical_tree_digest(pre_entries)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | require_nofollow("mode_capability_unknown")
    )
    file_flags = os.O_RDONLY | require_nofollow("mode_capability_unknown")
    try:
        directory_fd = os.open(stage, directory_flags)
    except OSError as error:
        raise InstallError("mode_capability_unknown", 4, status="unknown") from error
    try:
        file_fd = os.open(stage / probe_relative_path, file_flags)
    except OSError as error:
        os.close(directory_fd)
        raise InstallError("mode_capability_unknown", 4, status="unknown") from error

    directory_observed_modes: list[int] = []
    file_observed_modes: list[int] = []
    probe_failed = False
    restore_failed = False
    directory_before = os.fstat(directory_fd)
    file_before = os.fstat(file_fd)
    try:
        directory_path_before = os.lstat(stage)
        file_path_before = os.lstat(stage / probe_relative_path)
    except OSError:
        os.close(file_fd)
        os.close(directory_fd)
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    file_sha256, file_size = _hash_descriptor(file_fd)
    try:
        for requested in (0o700, 0o750):
            os.fchmod(directory_fd, requested)
            directory_observed_modes.append(
                stat.S_IMODE(os.fstat(directory_fd).st_mode)
            )
        for requested in (0o600, 0o640):
            os.fchmod(file_fd, requested)
            file_observed_modes.append(stat.S_IMODE(os.fstat(file_fd).st_mode))
    except (OSError, InstallError):
        probe_failed = True
    finally:
        try:
            os.fchmod(directory_fd, source_root_mode)
        except OSError:
            restore_failed = True
        try:
            os.fchmod(file_fd, source_file["mode"])
        except OSError:
            restore_failed = True

    try:
        directory_after = os.fstat(directory_fd)
        file_after = os.fstat(file_fd)
        directory_path_after = os.lstat(stage)
        file_path_after = os.lstat(stage / probe_relative_path)
        final_sha256, final_size = _hash_descriptor(file_fd)
    except (OSError, InstallError):
        os.close(file_fd)
        os.close(directory_fd)
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    os.close(file_fd)
    os.close(directory_fd)

    if probe_failed or restore_failed:
        raise InstallError("mode_capability_unknown", 4, status="unknown")

    directory_identity_before = _probe_identity(directory_before, "directory")
    directory_identity_after = _probe_identity(directory_after, "directory")
    file_identity_before = _probe_identity(file_before, "file")
    file_identity_after = _probe_identity(file_after, "file")
    directory_path_identity_before = _probe_identity(
        directory_path_before,
        "directory",
    )
    directory_path_identity_after = _probe_identity(
        directory_path_after,
        "directory",
    )
    file_path_identity_before = _probe_identity(file_path_before, "file")
    file_path_identity_after = _probe_identity(file_path_after, "file")
    if (
        directory_identity_before != directory_identity_after
        or directory_identity_before != directory_path_identity_before
        or directory_identity_after != directory_path_identity_after
        or file_identity_before != file_identity_after
        or file_identity_before != file_path_identity_before
        or file_identity_after != file_path_identity_after
        or file_size != final_size
        or file_sha256 != final_sha256
        or file_size != source_file.get("size")
        or file_sha256 != source_file.get("sha256")
    ):
        raise InstallError("mode_capability_unknown", 4, status="unknown")

    first_post_entries = scan_tree(stage)
    second_post_entries = scan_tree(stage)
    if first_post_entries != second_post_entries:
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    post_tree_digest = canonical_tree_digest(first_post_entries)
    policy = classify_mode_observations(
        directory_observed_modes,
        file_observed_modes,
    )
    restored_directory_mode = stat.S_IMODE(directory_after.st_mode)
    restored_file_mode = stat.S_IMODE(file_after.st_mode)
    if policy == "strict":
        if (
            restored_directory_mode != source_root_mode
            or first_post_entries != source_entries
        ):
            raise InstallError("mode_capability_unknown", 4, status="unknown")
        return "strict", {"status": "not_required"}, first_post_entries

    proof: dict[str, Any] = {
        "status": "posix_mode_not_preserved",
        "probe_relative_path": probe_relative_path,
        "directory_requested_modes": [0o700, 0o750],
        "directory_observed_modes": directory_observed_modes,
        "file_requested_modes": [0o600, 0o640],
        "file_observed_modes": file_observed_modes,
        "directory_identity_before": directory_identity_before,
        "directory_identity_after": directory_identity_after,
        "file_identity_before": file_identity_before,
        "file_identity_after": file_identity_after,
        "file_size": file_size,
        "file_sha256": file_sha256,
        "pre_stage_tree_digest": pre_tree_digest,
        "post_stage_tree_digest": post_tree_digest,
        "restored_directory_mode": restored_directory_mode,
        "restored_file_mode": restored_file_mode,
    }
    return "target-observed", proof, first_post_entries


def select_mode_policy(
    source: Path,
    stage: Path,
    skills_root: Path,
    source_entries: dict[str, dict[str, Any]],
    staged_entries: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, dict[str, Any]]]:
    content_differences = compare_content_entries(source_entries, staged_entries)
    if content_differences:
        raise InstallError("staging_hash_mismatch", 3, content_differences)

    source_identity_before = directory_identity(source, "mode_capability_unknown")
    source_root_mode = source_identity_before["mode"]
    staged_root_mode = directory_identity(stage, "mode_capability_unknown")["mode"]
    if source_root_mode == staged_root_mode and source_entries == staged_entries:
        return "strict", {"status": "not_required"}, staged_entries

    stage_filesystem = filesystem_identity(stage)
    root_filesystem = filesystem_identity(skills_root)
    if stage_filesystem != root_filesystem:
        raise InstallError("mode_capability_unknown", 4, status="unknown")

    policy, proof, final_entries = probe_mode_capability(
        stage,
        source_root_mode,
        source_entries,
    )
    current_source_entries = scan_tree(source)
    source_identity_after = directory_identity(source, "mode_capability_unknown")
    if (
        current_source_entries != source_entries
        or source_identity_before != source_identity_after
    ):
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    if compare_content_entries(source_entries, final_entries):
        raise InstallError("mode_capability_unknown", 4, status="unknown")
    return policy, proof, final_entries


def validate_runtime_layout(entries: dict[str, dict[str, Any]]) -> None:
    actual = {
        relative: metadata.get("type")
        for relative, metadata in entries.items()
    }
    if actual == EXPECTED_RUNTIME_ENTRIES:
        return
    differences = sorted(set(actual) ^ set(EXPECTED_RUNTIME_ENTRIES))
    differences.extend(
        relative
        for relative in sorted(set(actual) & set(EXPECTED_RUNTIME_ENTRIES))
        if actual[relative] != EXPECTED_RUNTIME_ENTRIES[relative]
    )
    raise InstallError(
        "unexpected_runtime_layout",
        2,
        sorted(set(differences)),
    )


def copy_regular_file(source: Path, destination: Path, mode: int) -> None:
    source_flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | require_nofollow("unsafe_source_entry")
    )
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    source_fd = os.open(source, source_flags)
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallError("unsafe_source_entry")
        destination_fd = os.open(destination, destination_flags, mode)
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fchmod(destination_fd, mode)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def copy_entries(source: Path, destination: Path, entries: dict[str, dict[str, Any]]) -> None:
    directories = [item for item in entries.items() if item[1]["type"] == "directory"]
    directories.sort(key=lambda item: (item[0].count("/"), item[0]))
    for relative, metadata in directories:
        path = destination / relative
        path.mkdir(mode=metadata["mode"])

    for relative, metadata in entries.items():
        if metadata["type"] == "file":
            copy_regular_file(source / relative, destination / relative, metadata["mode"])

    for relative, metadata in reversed(directories):
        os.chmod(destination / relative, metadata["mode"])
    os.chmod(destination, stat.S_IMODE(source.stat().st_mode))


def windows_drive_path(path: Path) -> str:
    if not WSLPATH.is_file() or not WINDOWS_POWERSHELL.is_file():
        raise InstallError("atomic_noreplace_unavailable", 4)
    try:
        result = subprocess.run(
            [str(WSLPATH), "-w", str(path)],
            check=False,
            capture_output=True,
            timeout=5,
        )
        converted = result.stdout.decode("utf-8").strip()
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
        raise InstallError("atomic_noreplace_unavailable", 4) from error
    windows_path = PureWindowsPath(converted)
    if (
        result.returncode != 0
        or "\x00" in converted
        or not windows_path.is_absolute()
        or len(windows_path.drive) != 2
        or windows_path.drive[1] != ":"
    ):
        raise InstallError("atomic_noreplace_unavailable", 4)
    return f"\\\\?\\{windows_path}"


def _windows_move_postconditions(
    source: Path,
    destination: Path,
    classification: str,
) -> str:
    try:
        source_metadata = os.lstat(source)
    except FileNotFoundError:
        source_is_absent = True
        source_is_directory = False
    except OSError:
        return "UNKNOWN"
    else:
        source_is_absent = False
        source_is_directory = (
            stat.S_ISDIR(source_metadata.st_mode)
            and not stat.S_ISLNK(source_metadata.st_mode)
        )

    try:
        destination_metadata = os.lstat(destination)
    except FileNotFoundError:
        destination_is_directory = False
    except OSError:
        return "UNKNOWN"
    else:
        destination_is_directory = (
            stat.S_ISDIR(destination_metadata.st_mode)
            and not stat.S_ISLNK(destination_metadata.st_mode)
        )

    if (
        classification == "VERIFIED"
        and source_is_absent
        and destination_is_directory
    ):
        return "VERIFIED"
    if (
        classification == "TARGET_EXISTS"
        and source_is_directory
        and destination_is_directory
    ):
        return "TARGET_EXISTS"
    return "UNKNOWN"


def windows_movefileex_noreplace(
    source: Path,
    destination: Path,
) -> dict[str, Any]:
    source = Path(source)
    destination = Path(destination)
    if (
        not source.is_absolute()
        or source != Path(os.path.abspath(source))
        or not destination.is_absolute()
        or destination != Path(os.path.abspath(destination))
        or source == destination
    ):
        raise InstallError("atomic_noreplace_unavailable", 4)
    source = strict_existing_directory(source, "atomic_noreplace_unavailable")
    destination_parent = strict_existing_directory(
        destination.parent,
        "atomic_noreplace_unavailable",
    )
    try:
        destination_metadata = os.lstat(destination)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise InstallError("atomic_noreplace_unavailable", 4) from error
    else:
        if (
            stat.S_ISLNK(destination_metadata.st_mode)
            or not stat.S_ISDIR(destination_metadata.st_mode)
            or strict_existing_directory(
                destination,
                "atomic_noreplace_unavailable",
            )
            != destination
        ):
            raise InstallError("atomic_noreplace_unavailable", 4)
    if filesystem_identity(source) != filesystem_identity(destination_parent):
        raise InstallError("atomic_noreplace_unavailable", 4)

    source_windows = windows_drive_path(source)
    destination_windows = windows_drive_path(destination)
    if PureWindowsPath(source_windows).drive.casefold() != PureWindowsPath(
        destination_windows
    ).drive.casefold():
        raise InstallError("atomic_noreplace_unavailable", 4)

    source_encoded = base64.b64encode(source_windows.encode("utf-8")).decode("ascii")
    destination_encoded = base64.b64encode(destination_windows.encode("utf-8")).decode(
        "ascii"
    )
    powershell = f'''$ErrorActionPreference = "Stop"
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{source_encoded}"))
$destination = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{destination_encoded}"))
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class AtomicDirectoryMove {{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileExW(string source, string destination, uint flags);
}}
'@
$moved = [AtomicDirectoryMove]::MoveFileExW($source, $destination, 8)
if ($moved) {{ $code = 0 }} else {{
    $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
}}
[Console]::Out.WriteLine([uint32]$code)
'''
    encoded_command = base64.b64encode(powershell.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_command,
            ],
            check=False,
            capture_output=True,
            timeout=WINDOWS_MOVE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "backend": WINDOWS_MOVEFILEEX_NOREPLACE,
            "classification": "UNKNOWN",
            "movefileex_call_count": 1,
            "win32_error": None,
        }

    win32_error: int | None = None
    try:
        stdout = result.stdout
        if (
            result.returncode != 0
            or not isinstance(stdout, bytes)
            or len(stdout) > 32
        ):
            raise ValueError
        decoded = stdout.decode("ascii")
        if re.fullmatch(r"[0-9]{1,10}\r?\n?", decoded) is None:
            raise ValueError
        win32_error = int(decoded.strip(), 10)
        if not 0 <= win32_error <= 0xFFFFFFFF:
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        classification = "UNKNOWN"
    else:
        if win32_error == 0:
            classification = "VERIFIED"
        elif win32_error in {80, 183}:
            classification = "TARGET_EXISTS"
        else:
            classification = "UNKNOWN"

    if classification != "UNKNOWN":
        classification = _windows_move_postconditions(
            source,
            destination,
            classification,
        )
    return {
        "backend": WINDOWS_MOVEFILEEX_NOREPLACE,
        "classification": classification,
        "movefileex_call_count": 1,
        "win32_error": win32_error,
    }


def move_directory_for_backend(
    backend: str,
    source: Path,
    destination: Path,
) -> dict[str, Any]:
    if backend != WINDOWS_MOVEFILEEX_NOREPLACE:
        raise InstallError("switch_backend_not_implemented")
    result = windows_movefileex_noreplace(source, destination)
    if result["classification"] == "UNKNOWN":
        raise InstallError(
            "backend_move_outcome_unknown",
            4,
            status="unknown",
        )
    if result["classification"] == "TARGET_EXISTS":
        raise InstallError(
            "backend_move_target_exists",
            3,
            status="recovery_required",
        )
    return result


def windows_move_noreplace(source: Path, destination: Path) -> None:
    result = windows_movefileex_noreplace(source, destination)
    if result["classification"] == "VERIFIED":
        return
    if result["classification"] == "TARGET_EXISTS":
        raise InstallError("target_exists")
    raise InstallError("atomic_move_outcome_unknown", 4)


def windows_volume_identity(path: Path) -> dict[str, Any]:
    try:
        path = strict_existing_directory(
            Path(path),
            "windows_volume_identity_unknown",
        )
        windows_path = windows_drive_path(path)
        windows_drive = PureWindowsPath(windows_path).drive[-2:].upper()
        if re.fullmatch(r"[A-Z]:", windows_drive) is None:
            raise ValueError
        drive_root = windows_drive + "\\"
        drive_encoded = base64.b64encode(drive_root.encode("utf-8")).decode("ascii")
        powershell = f'''$ErrorActionPreference = "Stop"
$root = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{drive_encoded}"))
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class FixedVolumeIdentity {{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetVolumeInformationW(
        string root, StringBuilder volumeName, uint volumeNameSize,
        out uint volumeSerial, out uint maximumComponentLength,
        out uint filesystemFlags, StringBuilder filesystemName,
        uint filesystemNameSize);
}}
'@
$volumeName = New-Object Text.StringBuilder 261
$filesystemName = New-Object Text.StringBuilder 33
$serial = [uint32]0
$maximum = [uint32]0
$flags = [uint32]0
$ok = [FixedVolumeIdentity]::GetVolumeInformationW(
    $root, $volumeName, 261, [ref]$serial, [ref]$maximum,
    [ref]$flags, $filesystemName, 33)
if (-not $ok) {{ exit 19 }}
$value = [ordered]@{{
    drive = $root.Substring(0, 2).ToUpperInvariant()
    filesystem_name = $filesystemName.ToString().ToUpperInvariant()
    volume_serial = $serial.ToString("X8")
}}
[Console]::Out.Write(($value | ConvertTo-Json -Compress))
'''
        encoded_command = base64.b64encode(powershell.encode("utf-16le")).decode(
            "ascii"
        )
        result = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_command,
            ],
            check=False,
            capture_output=True,
            timeout=WINDOWS_MOVE_TIMEOUT_SECONDS,
        )
        if (
            result.returncode != 0
            or not isinstance(result.stdout, bytes)
            or len(result.stdout) > 512
        ):
            raise ValueError
        value = json.loads(result.stdout.decode("ascii"))
        if not isinstance(value, dict) or set(value) != WINDOWS_VOLUME_IDENTITY_KEYS:
            raise ValueError
        normalized = {
            "drive": value["drive"].upper(),
            "filesystem_name": value["filesystem_name"].upper(),
            "volume_serial": value["volume_serial"].upper(),
        }
        _validate_backend_windows_volume(normalized)
        if normalized["drive"] != windows_drive:
            raise ValueError
        return normalized
    except (
        AttributeError,
        InstallError,
        json.JSONDecodeError,
        OSError,
        subprocess.TimeoutExpired,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise InstallError(
            "windows_volume_identity_unknown",
            4,
            status="unknown",
        ) from error


def _new_attestation_marker() -> bytes:
    return os.urandom(32)


def _attestation_output_path(
    output: Path,
    codex_home: Path,
    skills_root: Path,
) -> Path:
    output = Path(output)
    absolute = Path(os.path.abspath(output))
    if not output.is_absolute() or output != absolute:
        raise InstallError("switch_backend_evidence_output_invalid")
    parent = strict_existing_directory(
        absolute.parent,
        "switch_backend_evidence_output_invalid",
    )
    if parent != absolute.parent or codex_home == absolute or codex_home in absolute.parents:
        raise InstallError("switch_backend_evidence_output_invalid")
    if skills_root == absolute or skills_root in absolute.parents:
        raise InstallError("switch_backend_evidence_output_invalid")
    try:
        os.lstat(absolute)
    except FileNotFoundError:
        return absolute
    except OSError as error:
        raise InstallError("switch_backend_evidence_output_invalid") from error
    raise InstallError("switch_backend_evidence_output_invalid")


def _write_attestation_marker(path: Path, content: bytes) -> tuple[dict[str, Any], str]:
    if not isinstance(content, bytes) or not content:
        raise InstallError("switch_backend_attestation_unknown", 4, status="unknown")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | require_nofollow("switch_backend_attestation_unknown")
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short marker write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent, "switch_backend_attestation_unknown")
        _, identity, digest = read_bounded_regular_file(
            path,
            "switch_backend_attestation_unknown",
        )
    except (InstallError, OSError) as error:
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        ) from error
    if identity["nlink"] != 1 or identity["size"] != len(content):
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )
    return identity, digest


def _verify_attestation_marker(
    path: Path,
    expected_identity: dict[str, Any],
    expected_digest: str,
) -> None:
    try:
        _, identity, digest = read_bounded_regular_file(
            path,
            "switch_backend_attestation_unknown",
        )
    except InstallError as error:
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        ) from error
    if identity != expected_identity or not hmac.compare_digest(digest, expected_digest):
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )


def _attestation_directory_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        return _snapshot_optional_directory(path)
    except InstallError as error:
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        ) from error


def _attested_backend_move(
    source: Path,
    destination: Path,
    expected_classification: str,
) -> dict[str, Any]:
    source_before = _attestation_directory_snapshot(source)
    destination_before = _attestation_directory_snapshot(destination)
    if source_before is None:
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )
    try:
        result = windows_movefileex_noreplace(source, destination)
    except (InstallError, OSError) as error:
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        ) from error
    if (
        not isinstance(result, dict)
        or set(result) != WINDOWS_MOVE_RESULT_KEYS
        or result.get("backend") != WINDOWS_MOVEFILEEX_NOREPLACE
        or result.get("movefileex_call_count") != 1
        or isinstance(result.get("movefileex_call_count"), bool)
        or result.get("classification") != expected_classification
    ):
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )
    win32_error = result.get("win32_error")
    if (
        expected_classification == "VERIFIED"
        and win32_error != 0
    ) or (
        expected_classification == "TARGET_EXISTS"
        and win32_error not in {80, 183}
    ):
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )
    source_after = _attestation_directory_snapshot(source)
    destination_after = _attestation_directory_snapshot(destination)
    if expected_classification == "VERIFIED":
        postconditions_match = (
            destination_before is None
            and source_after is None
            and destination_after == source_before
        )
    else:
        postconditions_match = (
            destination_before is not None
            and source_after == source_before
            and destination_after == destination_before
            and source_before != destination_before
        )
    if not postconditions_match:
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )
    return {
        "status": "VERIFIED",
        "call_count": 1,
        "return_classification": expected_classification,
        "win32_error": win32_error,
        "source_before": source_before,
        "destination_before": destination_before,
        "source_after": source_after,
        "destination_after": destination_after,
    }


def _attestation_path_is_absent(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError as error:
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        ) from error
    return False


def _remove_attested_marker(
    path: Path,
    expected_identity: dict[str, Any],
    expected_digest: str,
) -> None:
    _verify_attestation_marker(path, expected_identity, expected_digest)
    try:
        os.unlink(path)
        _fsync_directory(path.parent, "switch_backend_attestation_cleanup_failed")
    except (InstallError, OSError) as error:
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        ) from error
    if not _attestation_path_is_absent(path):
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        )


def _remove_empty_attested_directory(
    path: Path,
    expected_stable_identity: dict[str, Any],
) -> None:
    first = _attestation_directory_snapshot(path)
    second = _attestation_directory_snapshot(path)
    empty_digest = canonical_tree_digest({})
    if (
        first is None
        or second != first
        or first["tree_digest"] != empty_digest
        or {
            key: first[key] for key in ("type", "device", "inode")
        }
        != expected_stable_identity
    ):
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        )
    try:
        os.rmdir(path)
        _fsync_directory(path.parent, "switch_backend_attestation_cleanup_failed")
    except (InstallError, OSError) as error:
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        ) from error
    if not _attestation_path_is_absent(path):
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        )


def read_switch_backend_evidence(path: Path) -> dict[str, Any]:
    absolute = Path(os.path.abspath(path))
    if not Path(path).is_absolute() or Path(path) != absolute:
        raise InstallError("switch_backend_evidence_invalid")
    try:
        if strict_existing_directory(
            absolute.parent,
            "switch_backend_evidence_invalid",
        ) != absolute.parent:
            raise InstallError("switch_backend_evidence_invalid")
        content, identity, _ = read_bounded_regular_file(
            absolute,
            "switch_backend_evidence_invalid",
        )
        if identity["nlink"] != 1:
            raise InstallError("switch_backend_evidence_invalid")
        value = json.loads(content.decode("utf-8"))
    except (InstallError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallError("switch_backend_evidence_invalid") from error
    validate_switch_backend_evidence(value)
    return value


def attest_switch_backend(
    skills_root: Path,
    backend: str,
    approval_id: str,
    output: Path,
) -> int:
    codex_home = validate_codex_home()
    skills_root = validate_skills_root(Path(skills_root))
    if backend != WINDOWS_MOVEFILEEX_NOREPLACE:
        raise InstallError("switch_backend_not_implemented")
    if not _is_safe_identifier(approval_id):
        raise InstallError("approval_id_invalid")
    output = _attestation_output_path(Path(output), codex_home, skills_root)

    installer_sha256 = current_installer_sha256()
    root_stable_identity = stable_directory_identity(
        skills_root,
        "switch_backend_attestation_unknown",
    )
    target_filesystem_identity = filesystem_identity(skills_root)
    wsl_identity = wsl_mount_identity(skills_root)
    volume_identity = windows_volume_identity(skills_root)
    if (
        root_stable_identity["device"] != target_filesystem_identity["device"]
        or {
            key: target_filesystem_identity[key]
            for key in WSL_MOUNT_IDENTITY_KEYS
        }
        != wsl_identity
        or volume_identity["drive"] != f"{wsl_identity['mount_target'][-1].upper()}:"
    ):
        raise InstallError(
            "switch_backend_attestation_unknown",
            4,
            status="unknown",
        )

    probe_id = uuid.uuid4().hex
    probe_root = _mkdir_exclusive(
        skills_root / f".{SKILL_NAME}-switch-probe-{probe_id}",
        mode=0o700,
        collision_reason="switch_backend_probe_collision",
    )
    source = _mkdir_exclusive(
        probe_root / "source",
        mode=0o700,
        collision_reason="switch_backend_probe_collision",
    )
    collision = _mkdir_exclusive(
        probe_root / "collision",
        mode=0o700,
        collision_reason="switch_backend_probe_collision",
    )
    forward = probe_root / "forward"
    source_marker = source / "source-marker.bin"
    collision_marker = collision / "collision-marker.bin"
    source_marker_identity, source_marker_digest = _write_attestation_marker(
        source_marker,
        _new_attestation_marker(),
    )
    collision_marker_identity, collision_marker_digest = _write_attestation_marker(
        collision_marker,
        _new_attestation_marker(),
    )
    source_stable_identity = stable_directory_identity(
        source,
        "switch_backend_attestation_unknown",
    )
    collision_stable_identity = stable_directory_identity(
        collision,
        "switch_backend_attestation_unknown",
    )
    probe_stable_identity = stable_directory_identity(
        probe_root,
        "switch_backend_attestation_unknown",
    )

    forward_move = _attested_backend_move(source, forward, "VERIFIED")
    _verify_attestation_marker(
        forward / source_marker.name,
        source_marker_identity,
        source_marker_digest,
    )
    collision_guard = _attested_backend_move(forward, collision, "TARGET_EXISTS")
    _verify_attestation_marker(
        forward / source_marker.name,
        source_marker_identity,
        source_marker_digest,
    )
    _verify_attestation_marker(
        collision_marker,
        collision_marker_identity,
        collision_marker_digest,
    )
    restore_move = _attested_backend_move(forward, source, "VERIFIED")
    _verify_attestation_marker(
        source_marker,
        source_marker_identity,
        source_marker_digest,
    )
    _verify_attestation_marker(
        collision_marker,
        collision_marker_identity,
        collision_marker_digest,
    )

    _remove_attested_marker(
        source_marker,
        source_marker_identity,
        source_marker_digest,
    )
    _remove_attested_marker(
        collision_marker,
        collision_marker_identity,
        collision_marker_digest,
    )
    _remove_empty_attested_directory(source, source_stable_identity)
    _remove_empty_attested_directory(collision, collision_stable_identity)
    _remove_empty_attested_directory(probe_root, probe_stable_identity)
    if any(
        not _attestation_path_is_absent(path)
        for path in (probe_root, source, forward, collision)
    ):
        raise InstallError(
            "switch_backend_attestation_cleanup_failed",
            4,
            status="recovery_required",
        )

    current_bindings = (
        current_installer_sha256(),
        stable_directory_identity(skills_root, "switch_backend_attestation_unknown"),
        filesystem_identity(skills_root),
        wsl_mount_identity(skills_root),
        windows_volume_identity(skills_root),
    )
    if current_bindings != (
        installer_sha256,
        root_stable_identity,
        target_filesystem_identity,
        wsl_identity,
        volume_identity,
    ):
        raise InstallError("switch_backend_attestation_drift", 3, status="drift")

    evidence = {
        "schema_version": SWITCH_BACKEND_EVIDENCE_SCHEMA_VERSION,
        "evidence_id": uuid.uuid4().hex,
        "approval_id": approval_id,
        "backend": backend,
        "capability": ROUTE_A_SWITCH_CAPABILITY,
        "installer_sha256": installer_sha256,
        "backend_implementation_digest": backend_implementation_digest(
            backend,
            installer_sha256,
        ),
        "skills_root": str(skills_root),
        "skills_root_stable_identity": root_stable_identity,
        "target_filesystem_identity": target_filesystem_identity,
        "wsl_mount_identity": wsl_identity,
        "windows_volume_identity": volume_identity,
        "probe_identity": {
            "probe_id": probe_id,
            "probe_root": str(probe_root),
            "source_relative": "source",
            "forward_destination_relative": "forward",
            "collision_destination_relative": "collision",
            "source_marker_sha256": source_marker_digest,
            "collision_marker_sha256": collision_marker_digest,
        },
        "forward_move": forward_move,
        "collision_guard": collision_guard,
        "restore_move": restore_move,
        "cleanup": {
            "status": "VERIFIED",
            "removed_relative_paths": [
                "source/source-marker.bin",
                "collision/collision-marker.bin",
                "source",
                "collision",
                ".",
            ],
            "residue": [],
        },
        "created_at_utc": utc_now(),
    }
    evidence["evidence_digest"] = switch_backend_evidence_digest(evidence)
    validate_switch_backend_evidence(evidence)
    write_json_exclusive(
        output,
        evidence,
        collision_reason="switch_backend_evidence_output_invalid",
    )
    return emit(
        "switch_backend_attested",
        evidence_id=evidence["evidence_id"],
        evidence_digest=evidence["evidence_digest"],
        output=str(output),
    )


def _darwin_renameatx_np_direct(source: Path, destination: Path, flags: int) -> None:
    if flags == RENAME_NOREPLACE:
        native_flags = DARWIN_RENAME_EXCL
    elif flags == RENAME_EXCHANGE:
        native_flags = DARWIN_RENAME_SWAP
    else:
        raise InstallError("invalid_rename_flags", 4)

    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameatx_np", None)
    if function is None:
        reason = (
            "exchange_unsupported"
            if flags == RENAME_EXCHANGE
            else "atomic_noreplace_unavailable"
        )
        raise InstallError(reason, 4)
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        DARWIN_AT_FDCWD,
        os.fsencode(source),
        DARWIN_AT_FDCWD,
        os.fsencode(destination),
        native_flags,
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if flags == RENAME_NOREPLACE and error_number == errno.EEXIST:
        raise InstallError("target_exists")
    if error_number in {
        errno.ENOSYS,
        errno.EINVAL,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }:
        reason = (
            "exchange_unsupported"
            if flags == RENAME_EXCHANGE
            else "atomic_noreplace_unavailable"
        )
        raise InstallError(reason, 4)
    reason = (
        "exchange_probe_outcome_unknown"
        if flags == RENAME_EXCHANGE
        else "noreplace_probe_outcome_unknown"
    )
    raise InstallError(reason, 4, status="unknown")


def rename_noreplace(source: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        _darwin_renameatx_np_direct(source, destination, RENAME_NOREPLACE)
        return
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameat2", None)
    if function is None:
        syscall_number = RENAMEAT2_SYSCALLS.get(platform.machine().lower())
        syscall = getattr(library, "syscall", None)
        if syscall_number is None or syscall is None:
            raise InstallError("atomic_noreplace_unavailable", 4)
        syscall.restype = ctypes.c_long
        result = syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_int(AT_FDCWD),
            ctypes.c_char_p(os.fsencode(source)),
            ctypes.c_int(AT_FDCWD),
            ctypes.c_char_p(os.fsencode(destination)),
            ctypes.c_uint(RENAME_NOREPLACE),
        )
    else:
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            AT_FDCWD,
            os.fsencode(source),
            AT_FDCWD,
            os.fsencode(destination),
            RENAME_NOREPLACE,
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise InstallError("target_exists")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        windows_move_noreplace(source, destination)
        return
    raise InstallError("atomic_rename_failed", 4)


def renameat2_direct(source: Path, destination: Path, flags: int) -> None:
    """Call renameat2 without selecting or falling back to another backend."""
    if sys.platform == "darwin":
        _darwin_renameatx_np_direct(source, destination, flags)
        return
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameat2", None)
    if function is None:
        syscall_number = RENAMEAT2_SYSCALLS.get(platform.machine().lower())
        syscall = getattr(library, "syscall", None)
        if syscall_number is None or syscall is None:
            reason = (
                "exchange_unsupported"
                if flags == RENAME_EXCHANGE
                else "atomic_noreplace_unavailable"
            )
            raise InstallError(reason, 4)
        syscall.restype = ctypes.c_long
        result = syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_int(AT_FDCWD),
            ctypes.c_char_p(os.fsencode(source)),
            ctypes.c_int(AT_FDCWD),
            ctypes.c_char_p(os.fsencode(destination)),
            ctypes.c_uint(flags),
        )
    else:
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            AT_FDCWD,
            os.fsencode(source),
            AT_FDCWD,
            os.fsencode(destination),
            flags,
        )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if flags == RENAME_NOREPLACE and error_number == errno.EEXIST:
        raise InstallError("target_exists")
    if error_number in {
        errno.ENOSYS,
        errno.EINVAL,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }:
        reason = (
            "exchange_unsupported"
            if flags == RENAME_EXCHANGE
            else "atomic_noreplace_unavailable"
        )
        raise InstallError(reason, 4)
    reason = (
        "exchange_probe_outcome_unknown"
        if flags == RENAME_EXCHANGE
        else "noreplace_probe_outcome_unknown"
    )
    raise InstallError(reason, 4, status="unknown")


def _probe_directory_snapshot(path: Path) -> dict[str, Any]:
    identity = directory_identity(path, "switch_probe_outcome_unknown")
    identity["tree_digest"] = canonical_tree_digest(
        scan_tree(path, "switch_probe_outcome_unknown")
    )
    return identity


def _new_switch_evidence(capability: str) -> dict[str, Any]:
    return {
        "capability": capability,
        "exchange_attempted": False,
        "exchange_result": "NOT_ATTEMPTED",
        "noreplace_attempted": False,
        "noreplace_result": "NOT_ATTEMPTED",
        "postconditions_verified": False,
        "restored": False,
        "left_identity_before": None,
        "right_identity_before": None,
        "left_identity_after": None,
        "right_identity_after": None,
        "left_identity_restored": None,
        "right_identity_restored": None,
    }


def probe_noreplace_capability(root: Path) -> dict[str, Any]:
    root = strict_existing_directory(Path(os.path.abspath(root)), "unsafe_skills_root")
    evidence = _new_switch_evidence("NOREPLACE_ONLY")
    evidence["noreplace_attempted"] = True
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{SKILL_NAME}-noreplace-probe-",
            dir=root,
        ) as temporary:
            temporary_root = Path(temporary)
            left = temporary_root / "left"
            right = temporary_root / "right"
            left.mkdir()
            (left / "marker").write_text("left\n", encoding="utf-8")
            before = _probe_directory_snapshot(left)
            evidence["left_identity_before"] = before
            renameat2_direct(left, right, RENAME_NOREPLACE)
            if left.exists() or left.is_symlink():
                raise InstallError(
                    "noreplace_probe_outcome_unknown",
                    4,
                    status="unknown",
                )
            after = _probe_directory_snapshot(right)
            evidence["right_identity_after"] = after
            if after != before:
                raise InstallError(
                    "noreplace_probe_outcome_unknown",
                    4,
                    status="unknown",
                )
            if sys.platform == "darwin":
                renameat2_direct(right, left, RENAME_NOREPLACE)
            else:
                os.rename(right, left)
            restored = _probe_directory_snapshot(left)
            evidence["left_identity_restored"] = restored
            if right.exists() or right.is_symlink() or restored != before:
                raise InstallError(
                    "noreplace_probe_outcome_unknown",
                    4,
                    status="unknown",
                )
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(
            "noreplace_probe_outcome_unknown",
            4,
            status="unknown",
        ) from error
    evidence["noreplace_result"] = "VERIFIED"
    evidence["postconditions_verified"] = True
    evidence["restored"] = True
    return evidence


def probe_switch_capability(root: Path) -> tuple[str, dict[str, Any]]:
    root = strict_existing_directory(Path(os.path.abspath(root)), "unsafe_skills_root")
    evidence = _new_switch_evidence("UNKNOWN")
    evidence["exchange_attempted"] = True
    exchange_unsupported = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{SKILL_NAME}-exchange-probe-",
            dir=root,
        ) as temporary:
            temporary_root = Path(temporary)
            left = temporary_root / "left"
            right = temporary_root / "right"
            left.mkdir()
            right.mkdir()
            (left / "marker").write_text("left\n", encoding="utf-8")
            (right / "marker").write_text("right\n", encoding="utf-8")
            left_before = _probe_directory_snapshot(left)
            right_before = _probe_directory_snapshot(right)
            evidence["left_identity_before"] = left_before
            evidence["right_identity_before"] = right_before
            try:
                renameat2_direct(left, right, RENAME_EXCHANGE)
            except InstallError as error:
                if error.reason == "exchange_unsupported":
                    exchange_unsupported = True
                    evidence["exchange_result"] = "UNSUPPORTED"
                else:
                    evidence["exchange_result"] = "UNKNOWN"
                    return "UNKNOWN", evidence
            if not exchange_unsupported:
                left_after = _probe_directory_snapshot(left)
                right_after = _probe_directory_snapshot(right)
                evidence["left_identity_after"] = left_after
                evidence["right_identity_after"] = right_after
                if left_after != right_before or right_after != left_before:
                    evidence["exchange_result"] = "UNKNOWN"
                    return "UNKNOWN", evidence
                try:
                    renameat2_direct(left, right, RENAME_EXCHANGE)
                except (InstallError, OSError):
                    evidence["exchange_result"] = "UNKNOWN"
                    return "UNKNOWN", evidence
                left_restored = _probe_directory_snapshot(left)
                right_restored = _probe_directory_snapshot(right)
                evidence["left_identity_restored"] = left_restored
                evidence["right_identity_restored"] = right_restored
                if left_restored != left_before or right_restored != right_before:
                    evidence["exchange_result"] = "UNKNOWN"
                    return "UNKNOWN", evidence
                evidence["capability"] = "EXCHANGE_SUPPORTED"
                evidence["exchange_result"] = "VERIFIED"
                evidence["postconditions_verified"] = True
                evidence["restored"] = True
                return "EXCHANGE_SUPPORTED", evidence
    except OSError:
        evidence["exchange_result"] = "UNKNOWN"
        return "UNKNOWN", evidence

    if not exchange_unsupported:
        return "UNKNOWN", evidence
    try:
        noreplace = probe_noreplace_capability(root)
    except (InstallError, OSError):
        evidence["noreplace_attempted"] = True
        evidence["noreplace_result"] = "UNKNOWN"
        return "UNKNOWN", evidence
    noreplace["exchange_attempted"] = True
    noreplace["exchange_result"] = "UNSUPPORTED"
    return "NOREPLACE_ONLY", noreplace


def strict_existing_directory(path: Path, reason: str) -> Path:
    if not path.is_absolute():
        raise InstallError(reason)
    absolute = Path(os.path.abspath(path))
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise InstallError(reason) from error
    if absolute != resolved or absolute.is_symlink() or not absolute.is_dir():
        raise InstallError(reason)
    return absolute


def validate_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    if not raw:
        raise InstallError("codex_home_not_set")
    return strict_existing_directory(Path(raw), "unsafe_codex_home")


def validate_skills_root(path: Path) -> Path:
    codex_home = validate_codex_home()
    expected = strict_existing_directory(codex_home / "skills", "unsafe_skills_root")
    candidate = strict_existing_directory(Path(path), "unsafe_skills_root")
    if candidate != expected:
        raise InstallError("skills_root_mismatch")
    return candidate


def _canonical_json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _staging_recovery_not_ready(
    differences: list[str] | None = None,
) -> InstallError:
    return InstallError(
        "staging_recovery_not_ready",
        2,
        differences,
        "error",
    )


def _require_recovery_fact(condition: bool, difference: str) -> None:
    if not condition:
        raise _staging_recovery_not_ready([difference])


def validate_recovery_anchors(anchors: dict[str, Any]) -> dict[str, Any]:
    """Validate a caller-supplied internal binding; never discover a default."""
    invalid = _staging_recovery_not_ready(["<recovery-anchors>"])
    if not isinstance(anchors, dict):
        raise invalid
    value = copy.deepcopy(anchors)
    if set(value) != {
        "stage_basename", "runtime_entries", "stage_root_identity",
        "source_root_mode", "file_anchors", "source_entry_modes",
        "stage_entry_modes",
    }:
        raise invalid
    basename = value["stage_basename"]
    if (
        not isinstance(basename, str)
        or re.fullmatch(r"\.vibe-project-lead-zh-stage-[A-Za-z0-9_-]+", basename) is None
        or len(basename) > 255
    ):
        raise invalid
    layout = value["runtime_entries"]
    if not isinstance(layout, dict) or not layout or layout.get("SKILL.md") != "file":
        raise invalid
    for relative, entry_type in layout.items():
        if (
            not isinstance(relative, str) or not relative
            or "\\" in relative or ":" in relative or "\x00" in relative
        ):
            raise invalid
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute() or pure.as_posix() != relative
            or "." in pure.parts or ".." in pure.parts
            or entry_type not in ("file", "directory")
            or (str(pure.parent) != "." and layout.get(str(pure.parent)) != "directory")
        ):
            raise invalid
    try:
        _validate_directory_identity(value["stage_root_identity"])
    except InstallError as error:
        raise invalid from error
    if value["stage_root_identity"]["mode"] > 0o7777:
        raise invalid
    root_mode = value["source_root_mode"]
    if not _is_integer(root_mode) or not 0 <= root_mode <= 0o7777:
        raise invalid
    for name in ("source_entry_modes", "stage_entry_modes"):
        modes = value[name]
        if not isinstance(modes, dict) or set(modes) != set(layout):
            raise invalid
        if any(not _is_integer(mode) or not 0 <= mode <= 0o7777 for mode in modes.values()):
            raise invalid
    files = {relative for relative, entry_type in layout.items() if entry_type == "file"}
    file_anchors = value["file_anchors"]
    if not isinstance(file_anchors, dict) or set(file_anchors) != files:
        raise invalid
    for anchor in file_anchors.values():
        if (
            not isinstance(anchor, tuple) or len(anchor) != 2
            or not _is_integer(anchor[0]) or anchor[0] < 0
            or not isinstance(anchor[1], str)
            or re.fullmatch(r"[0-9a-f]{64}", anchor[1]) is None
        ):
            raise invalid
    return value


def _path_is_absent(path: Path) -> bool:
    return not path.exists() and not path.is_symlink()


def scan_neighbor_roots(
    skills_root: Path,
    excluded_name: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with os.scandir(skills_root) as children:
            for child in children:
                if child.name == excluded_name:
                    continue
                metadata = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    entry_type = "directory"
                elif stat.S_ISREG(metadata.st_mode):
                    entry_type = "file"
                elif stat.S_ISLNK(metadata.st_mode):
                    entry_type = "symlink"
                else:
                    entry_type = "other"
                records.append(
                    {
                        "name": child.name,
                        "type": entry_type,
                        "device": metadata.st_dev,
                        "inode": metadata.st_ino,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "size": metadata.st_size,
                        "nlink": metadata.st_nlink,
                        "mtime_ns": metadata.st_mtime_ns,
                    }
                )
    except OSError as error:
        raise _staging_recovery_not_ready(["<neighbor-scan>"]) from error
    return sorted(records, key=lambda record: record["name"])


def _validate_recovery_tree(
    label: str,
    entries: dict[str, dict[str, Any]],
    expected_modes: dict[str, int],
    anchors: dict[str, Any],
) -> None:
    differences: list[str] = []
    actual_layout = {
        relative: entry.get("type") for relative, entry in entries.items()
    }
    if actual_layout != anchors["runtime_entries"]:
        differences.extend(
            f"{label}:{relative}"
            for relative in sorted(
                set(actual_layout) | set(anchors["runtime_entries"])
            )
            if actual_layout.get(relative)
            != anchors["runtime_entries"].get(relative)
        )

    expected_files = {
        relative
        for relative, entry_type in anchors["runtime_entries"].items()
        if entry_type == "file"
    }
    if set(anchors["file_anchors"]) != expected_files:
        differences.append(f"{label}:<file-anchor-set>")
    for relative in sorted(expected_files):
        entry = entries.get(relative)
        anchor = anchors["file_anchors"].get(relative)
        if (
            not isinstance(entry, dict)
            or entry.get("type") != "file"
            or not isinstance(anchor, tuple)
            or len(anchor) != 2
            or entry.get("size") != anchor[0]
            or entry.get("sha256") != anchor[1]
        ):
            differences.append(f"{label}:{relative}")

    actual_modes = {
        relative: entry.get("mode") for relative, entry in entries.items()
    }
    if actual_modes != expected_modes:
        differences.extend(
            f"{label}-mode:{relative}"
            for relative in sorted(set(actual_modes) | set(expected_modes))
            if actual_modes.get(relative) != expected_modes.get(relative)
        )
    if differences:
        raise _staging_recovery_not_ready(sorted(set(differences)))


def build_staging_recovery_request(
    source: Path,
    stage: Path,
    skills_root: Path,
    *,
    anchors: dict[str, Any],
) -> dict[str, Any]:
    anchors = validate_recovery_anchors(anchors)
    try:
        root = validate_skills_root(skills_root)
        codex_home = validate_codex_home()
        source_absolute = strict_existing_directory(
            Path(source),
            "unsafe_source_entry",
        )
        stage_candidate = Path(os.path.abspath(stage))
        _require_recovery_fact(
            stage_candidate.parent == root,
            "<stage-parent>",
        )
        _require_recovery_fact(
            stage_candidate.name == anchors["stage_basename"],
            "<stage-basename>",
        )
        stage_absolute = strict_existing_directory(
            stage_candidate,
            "unsafe_failed_stage",
        )
        _require_recovery_fact(
            source_absolute.name == SKILL_NAME,
            "<source-basename>",
        )

        codex_home_identity = directory_identity(
            codex_home,
            "unsafe_codex_home",
        )
        skills_root_identity = directory_identity(root, "unsafe_skills_root")
        source_identity_before = directory_identity(
            source_absolute,
            "unsafe_source_entry",
        )
        stage_identity_before = directory_identity(
            stage_absolute,
            "unsafe_failed_stage",
        )
        _require_recovery_fact(
            source_identity_before["mode"] == anchors["source_root_mode"],
            "source:<root-mode>",
        )
        _require_recovery_fact(
            stage_identity_before == anchors["stage_root_identity"],
            "stage:<root-identity>",
        )

        first_source_entries = scan_tree(source_absolute)
        second_source_entries = scan_tree(source_absolute)
        first_stage_entries = scan_tree(stage_absolute)
        second_stage_entries = scan_tree(stage_absolute)
        source_identity_after = directory_identity(
            source_absolute,
            "unsafe_source_entry",
        )
        stage_identity_after = directory_identity(
            stage_absolute,
            "unsafe_failed_stage",
        )
        _require_recovery_fact(
            source_identity_before == source_identity_after,
            "source:<root-identity-drift>",
        )
        _require_recovery_fact(
            stage_identity_before == stage_identity_after,
            "stage:<root-identity-drift>",
        )
        _require_recovery_fact(
            first_source_entries == second_source_entries,
            "source:<scan-instability>",
        )
        _require_recovery_fact(
            first_stage_entries == second_stage_entries,
            "stage:<scan-instability>",
        )

        source_entries = first_source_entries
        stage_entries = first_stage_entries
        _validate_recovery_tree(
            "source",
            source_entries,
            anchors["source_entry_modes"],
            anchors,
        )
        _validate_recovery_tree(
            "stage",
            stage_entries,
            anchors["stage_entry_modes"],
            anchors,
        )
        content_differences = compare_content_entries(source_entries, stage_entries)
        if content_differences:
            raise _staging_recovery_not_ready(
                [f"content:{relative}" for relative in content_differences]
            )

        target = root / SKILL_NAME
        install_state = root / f".{SKILL_NAME}-install"
        _require_recovery_fact(_path_is_absent(target), "<target-present>")
        _require_recovery_fact(
            _path_is_absent(install_state),
            "<install-state-present>",
        )

        stage_filesystem = filesystem_identity(stage_absolute)
        root_filesystem = filesystem_identity(root)
        _require_recovery_fact(
            stage_filesystem == root_filesystem,
            "<stage-filesystem-mismatch>",
        )

        first_neighbors = scan_neighbor_roots(root, anchors["stage_basename"])
        second_neighbors = scan_neighbor_roots(root, anchors["stage_basename"])
        _require_recovery_fact(
            first_neighbors == second_neighbors,
            "<neighbor-scan-instability>",
        )
        neighbor_digest = _canonical_json_digest(first_neighbors)

        archive_root = codex_home / RECOVERY_ROOT_NAME
        if archive_root.exists() or archive_root.is_symlink():
            archive_absolute = strict_existing_directory(
                archive_root,
                "unsafe_rollback_root",
            )
            _require_recovery_fact(
                archive_absolute.parent == codex_home,
                "<archive-root-parent>",
            )
            archive_root_condition = {
                "status": "existing",
                "identity": directory_identity(
                    archive_absolute,
                    "unsafe_rollback_root",
                ),
            }
        else:
            archive_root_condition = {
                "status": "creatable",
                "parent_identity": codex_home_identity,
            }

        _require_recovery_fact(_path_is_absent(target), "<target-present>")
        _require_recovery_fact(
            _path_is_absent(install_state),
            "<install-state-present>",
        )
        _require_recovery_fact(
            directory_identity(codex_home, "unsafe_codex_home")
            == codex_home_identity,
            "<codex-home-identity-drift>",
        )
        _require_recovery_fact(
            directory_identity(root, "unsafe_skills_root")
            == skills_root_identity,
            "<skills-root-identity-drift>",
        )

        request: dict[str, Any] = {
            "recovery_schema_version": RECOVERY_SCHEMA_VERSION,
            "operation": "archive_failed_staging",
            "source": str(source_absolute),
            "source_stage": str(stage_absolute),
            "skills_root": str(root),
            "target": str(target),
            "install_state": str(install_state),
            "archive_root": str(archive_root),
            "codex_home_identity": codex_home_identity,
            "skills_root_identity": skills_root_identity,
            "source_root_identity": source_identity_before,
            "stage_root_identity": stage_identity_before,
            "source_entries": source_entries,
            "stage_entries": stage_entries,
            "source_tree_digest": canonical_tree_digest(source_entries),
            "stage_tree_digest": canonical_tree_digest(stage_entries),
            "target_filesystem": stage_filesystem,
            "runtime_layout": {
                "valid": True,
                "expected_entries": sorted(anchors["runtime_entries"]),
            },
            "neighbor_digest": neighbor_digest,
            "target_absent": True,
            "install_state_absent": True,
            "archive_root_condition": archive_root_condition,
        }
        _require_recovery_fact(
            set(request) == RECOVERY_REQUEST_KEYS,
            "<recovery-request-schema>",
        )
        return request
    except InstallError as error:
        if error.reason == "staging_recovery_not_ready":
            raise
        differences = error.differences or [f"<{error.reason}>"]
        raise _staging_recovery_not_ready(differences) from error
    except OSError as error:
        raise _staging_recovery_not_ready(["<filesystem-read>"]) from error


def recovery_request_digest(request: dict[str, Any]) -> str:
    if set(request) != RECOVERY_REQUEST_KEYS:
        raise _staging_recovery_not_ready(["<recovery-request-schema>"])
    return _canonical_json_digest(request)


def build_recovery_prepared_manifest(
    request: dict[str, Any],
    digest: str,
    archive_destination: Path,
) -> dict[str, Any]:
    if (
        set(request) != RECOVERY_REQUEST_KEYS
        or not _is_sha256(digest)
        or not hmac.compare_digest(digest, recovery_request_digest(request))
    ):
        raise InstallError("recovery_manifest_invalid")
    destination = Path(os.path.abspath(archive_destination))
    archive_root = Path(request["archive_root"])
    if (
        not destination.is_absolute()
        or destination.name != Path(request["source_stage"]).name
        or destination.parent.parent != archive_root
    ):
        raise InstallError("recovery_manifest_invalid")

    value: dict[str, Any] = {
        "recovery_schema_version": RECOVERY_SCHEMA_VERSION,
        "operation": "archive_failed_staging",
        "phase": "prepared",
        "recovery_request_digest": digest,
        "source": request["source"],
        "source_stage": request["source_stage"],
        "skills_root": request["skills_root"],
        "target": request["target"],
        "install_state": request["install_state"],
        "archive_root": request["archive_root"],
        "archive_destination": str(destination),
        "source_entries": request["source_entries"],
        "stage_entries": request["stage_entries"],
        "source_tree_digest": request["source_tree_digest"],
        "stage_tree_digest": request["stage_tree_digest"],
        "source_root_identity": request["source_root_identity"],
        "stage_root_identity": request["stage_root_identity"],
        "target_filesystem": request["target_filesystem"],
        "neighbor_digest": request["neighbor_digest"],
        "target_absent": request["target_absent"],
        "install_state_absent": request["install_state_absent"],
        "prepared_at_utc": utc_now(),
    }
    value["manifest_digest"] = canonical_digest(value)
    if set(value) != RECOVERY_PREPARED_MANIFEST_KEYS:
        raise InstallError("recovery_manifest_invalid")
    return value


def build_recovery_final_manifest(
    prepared: dict[str, Any],
    archived_identity: dict[str, Any],
    post_neighbor_digest: str,
) -> dict[str, Any]:
    if (
        set(prepared) != RECOVERY_PREPARED_MANIFEST_KEYS
        or prepared.get("phase") != "prepared"
        or prepared.get("manifest_digest") != canonical_digest(prepared)
        or set(archived_identity) != {
            "type",
            "mode",
            "device",
            "inode",
            "size",
            "nlink",
            "mtime_ns",
        }
        or archived_identity.get("type") != "directory"
        or not _is_sha256(post_neighbor_digest)
    ):
        raise InstallError("recovery_manifest_invalid")

    value = {
        key: child
        for key, child in prepared.items()
        if key != "manifest_digest"
    }
    value["phase"] = "archived"
    value.update(
        {
            "prepared_manifest_digest": prepared["manifest_digest"],
            "archived_at_utc": utc_now(),
            "archived_stage_root_identity": archived_identity,
            "source_stage_absent": True,
            "post_neighbor_digest": post_neighbor_digest,
        }
    )
    value["manifest_digest"] = canonical_digest(value)
    if set(value) != RECOVERY_FINAL_MANIFEST_KEYS:
        raise InstallError("recovery_manifest_invalid")
    return value


def _archive_collision() -> InstallError:
    return InstallError("install_state_collision", 2, status="error")


def _prepare_archive_root(request: dict[str, Any]) -> Path:
    archive_root = Path(request["archive_root"])
    codex_home = archive_root.parent
    condition = request["archive_root_condition"]
    if archive_root.name != RECOVERY_ROOT_NAME:
        raise InstallError("staging_recovery_drift", 3)
    if condition.get("status") == "existing":
        root = strict_existing_directory(archive_root, "unsafe_rollback_root")
        if (
            root.parent != codex_home
            or directory_identity(root, "unsafe_rollback_root")
            != condition.get("identity")
        ):
            raise InstallError("staging_recovery_drift", 3)
        return root
    if condition.get("status") != "creatable" or not _path_is_absent(archive_root):
        raise InstallError("staging_recovery_drift", 3)
    if (
        directory_identity(codex_home, "unsafe_codex_home")
        != condition.get("parent_identity")
    ):
        raise InstallError("staging_recovery_drift", 3)
    try:
        archive_root.mkdir(mode=0o700)
    except FileExistsError as error:
        raise _archive_collision() from error
    root = strict_existing_directory(archive_root, "unsafe_rollback_root")
    if root.parent != codex_home:
        raise InstallError("staging_recovery_drift", 3)
    return root


def _create_recovery_directory(archive_root: Path) -> Path:
    name = uuid.uuid4().hex
    if len(name) != 32 or name != name.lower():
        raise InstallError("recovery_directory_name_invalid", 4)
    try:
        int(name, 16)
    except ValueError as error:
        raise InstallError("recovery_directory_name_invalid", 4) from error
    recovery_directory = archive_root / name
    try:
        recovery_directory.mkdir(mode=0o700)
    except FileExistsError as error:
        raise _archive_collision() from error
    directory = strict_existing_directory(
        recovery_directory,
        "unsafe_recovery_directory",
    )
    if directory.parent != archive_root:
        raise InstallError("staging_recovery_drift", 3)
    return directory


def _raise_ambiguous_archive_outcome(
    stage: Path,
    archive_destination: Path,
    cause: BaseException,
) -> None:
    try:
        _path_is_absent(stage)
        archive_destination.exists()
        archive_destination.is_symlink()
    except OSError:
        pass
    raise InstallError(
        "staging_archive_outcome_unknown",
        4,
        status="unknown",
    ) from cause


def _adjudicate_archived_stage(
    request: dict[str, Any],
    stage: Path,
    archive_destination: Path,
) -> tuple[dict[str, Any], str]:
    try:
        if not _path_is_absent(stage):
            raise InstallError("source_stage_still_present")
        destination = strict_existing_directory(
            archive_destination,
            "unsafe_archived_stage",
        )
        archived_identity = directory_identity(
            destination,
            "unsafe_archived_stage",
        )
        archived_entries = scan_tree(destination, "unsafe_archived_stage")
    except (InstallError, OSError) as error:
        raise InstallError(
            "staging_archive_outcome_unknown",
            4,
            status="unknown",
        ) from error

    expected_identity = request["stage_root_identity"]
    if (
        archived_identity["device"],
        archived_identity["inode"],
    ) != (
        expected_identity["device"],
        expected_identity["inode"],
    ):
        raise InstallError(
            "staging_archive_outcome_unknown",
            4,
            status="unknown",
        )
    differences = compare_entries(request["stage_entries"], archived_entries)
    if differences:
        raise InstallError("archived_staging_mismatch", 3, differences)

    target = Path(request["target"])
    install_state = Path(request["install_state"])
    if not _path_is_absent(target) or not _path_is_absent(install_state):
        raise InstallError(
            "staging_archive_outcome_unknown",
            4,
            status="unknown",
        )
    try:
        neighbors = scan_neighbor_roots(
            Path(request["skills_root"]),
            Path(request["source_stage"]).name,
        )
        post_neighbor_digest = _canonical_json_digest(neighbors)
    except (InstallError, OSError) as error:
        raise InstallError(
            "staging_archive_outcome_unknown",
            4,
            status="unknown",
        ) from error
    if not hmac.compare_digest(
        post_neighbor_digest,
        request["neighbor_digest"],
    ):
        raise InstallError(
            "staging_archive_outcome_unknown",
            4,
            status="unknown",
        )
    return archived_identity, post_neighbor_digest


def archive_staging(
    source: Path,
    stage: Path,
    skills_root: Path,
    check_only: bool,
    confirmation: str | None,
    *,
    anchors: dict[str, Any],
) -> int:
    if not isinstance(check_only, bool) or check_only == (confirmation is not None):
        raise InstallError("staging_recovery_not_ready", 2, status="error")
    request = build_staging_recovery_request(
        source, stage, skills_root, anchors=anchors,
    )
    digest = recovery_request_digest(request)
    if check_only:
        if confirmation is not None:
            raise InstallError("staging_recovery_not_ready", 2, status="error")
        return emit(
            "staging_recovery_ready",
            source=request["source"],
            source_stage=request["source_stage"],
            skills_root=request["skills_root"],
            target=request["target"],
            install_state=request["install_state"],
            archive_root=request["archive_root"],
            codex_home_identity=request["codex_home_identity"],
            skills_root_identity=request["skills_root_identity"],
            source_root_identity=request["source_root_identity"],
            stage_root_identity=request["stage_root_identity"],
            source_tree_digest=request["source_tree_digest"],
            stage_tree_digest=request["stage_tree_digest"],
            target_filesystem=request["target_filesystem"],
            entry_count=len(request["stage_entries"]),
            target_absent=request["target_absent"],
            install_state_absent=request["install_state_absent"],
            neighbor_digest=request["neighbor_digest"],
            archive_root_condition=request["archive_root_condition"],
            recovery_request_digest=digest,
        )

    if confirmation is None or not hmac.compare_digest(confirmation, digest):
        raise InstallError("staging_recovery_drift", 3)

    archive_root = _prepare_archive_root(request)
    recovery_directory = _create_recovery_directory(archive_root)
    archive_destination = recovery_directory / Path(request["source_stage"]).name
    if not _path_is_absent(archive_destination):
        raise _archive_collision()
    prepared = build_recovery_prepared_manifest(
        request,
        digest,
        archive_destination,
    )
    prepared_path = recovery_directory / RECOVERY_PREPARED_MANIFEST_NAME
    try:
        write_json_exclusive(prepared_path, prepared)
    except InstallError as error:
        if error.reason == "install_state_collision":
            raise _archive_collision() from error
        raise

    stage_absolute = Path(request["source_stage"])
    try:
        rename_noreplace(stage_absolute, archive_destination)
    except (InstallError, OSError) as error:
        _raise_ambiguous_archive_outcome(
            stage_absolute,
            archive_destination,
            error,
        )

    archived_identity, post_neighbor_digest = _adjudicate_archived_stage(
        request,
        stage_absolute,
        archive_destination,
    )
    try:
        final = build_recovery_final_manifest(
            prepared,
            archived_identity,
            post_neighbor_digest,
        )
        final_path = recovery_directory / RECOVERY_FINAL_MANIFEST_NAME
        write_json_exclusive(final_path, final)
    except (InstallError, OSError) as error:
        raise InstallError(
            "recovery_manifest_incomplete",
            4,
            status="unknown",
        ) from error

    return emit(
        "staging_archived",
        archive_directory=str(recovery_directory),
        archive_destination=str(archive_destination),
        prepared_manifest_digest=prepared["manifest_digest"],
        manifest_digest=final["manifest_digest"],
        entry_count=len(request["stage_entries"]),
        recoverable=True,
    )


def validate_skill_target(path: Path) -> Path:
    codex_home = validate_codex_home()
    skills_root = strict_existing_directory(codex_home / "skills", "unsafe_skills_root")
    absolute = Path(os.path.abspath(path))
    if absolute != skills_root / SKILL_NAME:
        raise InstallError("target_outside_codex_home")
    return absolute


def expected_manifest_path(target: Path) -> Path:
    return target.parent / f".{target.name}-install" / MANIFEST_NAME


def read_manifest_document(
    path: Path,
    *,
    maximum_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | require_nofollow("safe_manifest_read_unavailable")
    )
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        raise InstallError("manifest_not_found") from error
    except OSError as error:
        raise InstallError("manifest_invalid") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallError("manifest_invalid")
        if before.st_size > maximum_bytes:
            raise InstallError("manifest_too_large")
        content = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, maximum_bytes + 1)):
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise InstallError("manifest_too_large")
        after = os.fstat(descriptor)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InstallError("manifest_changed_during_read", 3)
    except OSError as error:
        raise InstallError("manifest_invalid") from error
    finally:
        os.close(descriptor)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallError("manifest_invalid") from error
    if not isinstance(value, dict):
        raise InstallError("manifest_invalid")
    return value


def _validate_manifest_common(value: dict[str, Any]) -> None:
    if value.get("name") != SKILL_NAME:
        raise InstallError("manifest_invalid")
    for key in ("source", "target"):
        child = value.get(key)
        if not isinstance(child, str) or not Path(child).is_absolute():
            raise InstallError("manifest_invalid")
    if value.get("phase") not in {"prepared", "installed"}:
        raise InstallError("manifest_invalid")
    if not isinstance(value.get("installed_at_utc"), str):
        raise InstallError("manifest_invalid")
    if not _is_sha256(value.get("manifest_digest")):
        raise InstallError("manifest_invalid")


def validate_v1_install_manifest(value: dict[str, Any]) -> None:
    if set(value) != V1_INSTALL_MANIFEST_KEYS:
        raise InstallError("manifest_invalid")
    if value.get("schema_version") != LEGACY_INSTALL_SCHEMA_VERSION:
        raise InstallError("manifest_invalid")
    _validate_manifest_common(value)
    _validate_entries(value.get("entries"))


def _validate_target_filesystem(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != TARGET_FILESYSTEM_KEYS:
        raise InstallError("manifest_invalid")
    if not _is_integer(value.get("device")) or value["device"] < 0:
        raise InstallError("manifest_invalid")
    if not isinstance(value.get("mount_target"), str) or not value["mount_target"]:
        raise InstallError("manifest_invalid")
    if not isinstance(value.get("filesystem_type"), str) or not value["filesystem_type"]:
        raise InstallError("manifest_invalid")
    if not _is_sha256(value.get("mount_options_sha256")):
        raise InstallError("manifest_invalid")


def _validate_probe_identity(value: Any, expected_type: str) -> None:
    if not isinstance(value, dict) or set(value) != PROBE_IDENTITY_KEYS:
        raise InstallError("manifest_invalid")
    if value.get("type") != expected_type:
        raise InstallError("manifest_invalid")
    for key in ("device", "inode"):
        if not _is_integer(value.get(key)) or value[key] < 0:
            raise InstallError("manifest_invalid")


def _validate_target_observed_capability(
    capability: Any,
    source_entries: dict[str, dict[str, Any]],
    entries: dict[str, dict[str, Any]],
    source_root_mode: int,
    target_root_mode: int,
) -> None:
    if (
        not isinstance(capability, dict)
        or set(capability) != TARGET_OBSERVED_CAPABILITY_KEYS
        or capability.get("status") != "posix_mode_not_preserved"
        or capability.get("probe_relative_path") != "SKILL.md"
        or capability.get("directory_requested_modes") != [0o700, 0o750]
        or capability.get("file_requested_modes") != [0o600, 0o640]
    ):
        raise InstallError("manifest_invalid")
    directory_observed = capability.get("directory_observed_modes")
    file_observed = capability.get("file_observed_modes")
    if not isinstance(directory_observed, list) or not isinstance(file_observed, list):
        raise InstallError("manifest_invalid")
    try:
        policy = classify_mode_observations(directory_observed, file_observed)
    except InstallError as error:
        raise InstallError("manifest_invalid") from error
    if policy != "target-observed":
        raise InstallError("manifest_invalid")

    _validate_probe_identity(capability.get("directory_identity_before"), "directory")
    _validate_probe_identity(capability.get("directory_identity_after"), "directory")
    _validate_probe_identity(capability.get("file_identity_before"), "file")
    _validate_probe_identity(capability.get("file_identity_after"), "file")
    if capability["directory_identity_before"] != capability["directory_identity_after"]:
        raise InstallError("manifest_invalid")
    if capability["file_identity_before"] != capability["file_identity_after"]:
        raise InstallError("manifest_invalid")
    if not _is_integer(capability.get("file_size")) or capability["file_size"] < 0:
        raise InstallError("manifest_invalid")
    for key in ("file_sha256", "pre_stage_tree_digest", "post_stage_tree_digest"):
        if not _is_sha256(capability.get(key)):
            raise InstallError("manifest_invalid")
    for key in ("restored_directory_mode", "restored_file_mode"):
        if not _is_integer(capability.get(key)) or not 0 <= capability[key] <= 0o7777:
            raise InstallError("manifest_invalid")

    source_probe = source_entries.get("SKILL.md")
    target_probe = entries.get("SKILL.md")
    if (
        not isinstance(source_probe, dict)
        or not isinstance(target_probe, dict)
        or capability["file_size"] != source_probe.get("size")
        or capability["file_sha256"] != source_probe.get("sha256")
        or capability["file_size"] != target_probe.get("size")
        or capability["file_sha256"] != target_probe.get("sha256")
        or capability["restored_directory_mode"] != target_root_mode
        or capability["restored_file_mode"] != target_probe.get("mode")
    ):
        raise InstallError("manifest_invalid")
    if compare_content_entries(source_entries, entries):
        raise InstallError("manifest_invalid")
    if source_root_mode == target_root_mode and not mode_differences(
        source_entries, entries
    ):
        raise InstallError("manifest_invalid")


def validate_v2_install_manifest(value: dict[str, Any]) -> None:
    if set(value) != V2_INSTALL_MANIFEST_KEYS:
        raise InstallError("manifest_invalid")
    if value.get("schema_version") != INSTALL_SCHEMA_VERSION:
        raise InstallError("manifest_invalid")
    _validate_manifest_common(value)
    for key in ("source_root_mode", "target_root_mode"):
        mode = value.get(key)
        if not _is_integer(mode) or not 0 <= mode <= 0o7777:
            raise InstallError("manifest_invalid")
    _validate_entries(value.get("source_entries"))
    _validate_entries(value.get("entries"))
    _validate_target_filesystem(value.get("target_filesystem"))

    mode_policy = value.get("mode_policy")
    if mode_policy == "strict":
        if value.get("mode_capability") != {"status": "not_required"}:
            raise InstallError("manifest_invalid")
        if value["source_root_mode"] != value["target_root_mode"]:
            raise InstallError("manifest_invalid")
        if value["source_entries"] != value["entries"]:
            raise InstallError("manifest_invalid")
        return
    if mode_policy == "target-observed":
        _validate_target_observed_capability(
            value.get("mode_capability"),
            value["source_entries"],
            value["entries"],
            value["source_root_mode"],
            value["target_root_mode"],
        )
        return
    raise InstallError("manifest_invalid")


def build_v2_install_manifest(
    *,
    source: Path,
    target: Path,
    phase: str,
    installed_at_utc: str,
    mode_policy: str,
    source_root_mode: int,
    target_root_mode: int,
    source_entries: dict[str, dict[str, Any]],
    entries: dict[str, dict[str, Any]],
    mode_capability: dict[str, Any],
    target_filesystem: dict[str, Any],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": INSTALL_SCHEMA_VERSION,
        "name": SKILL_NAME,
        "source": str(source),
        "target": str(target),
        "phase": phase,
        "installed_at_utc": installed_at_utc,
        "mode_policy": mode_policy,
        "source_root_mode": source_root_mode,
        "target_root_mode": target_root_mode,
        "source_entries": source_entries,
        "entries": entries,
        "mode_capability": mode_capability,
        "target_filesystem": target_filesystem,
    }
    value["manifest_digest"] = canonical_digest(value)
    validate_v2_install_manifest(value)
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    value = read_manifest_document(path)
    if value.get("manifest_digest") != canonical_digest(value):
        raise InstallError("manifest_digest_invalid")
    schema_version = value.get("schema_version")
    if schema_version == LEGACY_INSTALL_SCHEMA_VERSION:
        validate_v1_install_manifest(value)
    elif schema_version == INSTALL_SCHEMA_VERSION:
        validate_v2_install_manifest(value)
    else:
        raise InstallError("manifest_invalid")
    return value


def require_installed_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("phase") != "installed":
        raise InstallError("manifest_phase_invalid")


def validate_manifest_binding(target: Path, manifest_path: Path, manifest: dict[str, Any]) -> None:
    target_absolute = Path(os.path.abspath(target))
    expected_path = expected_manifest_path(target_absolute)
    if Path(os.path.abspath(manifest_path)) != expected_path:
        raise InstallError("manifest_location_mismatch")
    if manifest.get("target") != str(target_absolute):
        raise InstallError("manifest_target_mismatch")
    if manifest.get("name") != target_absolute.name or target_absolute.name != SKILL_NAME:
        raise InstallError("manifest_target_mismatch")


def validate_manifest_location(target: Path, manifest_path: Path) -> Path:
    absolute = Path(os.path.abspath(manifest_path))
    if absolute != expected_manifest_path(Path(os.path.abspath(target))):
        raise InstallError("manifest_location_mismatch")
    strict_existing_directory(absolute.parent, "unsafe_install_state")
    return absolute


def compare_entries(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return [
        relative
        for relative in sorted(set(expected) | set(actual))
        if expected.get(relative) != actual.get(relative)
    ]


def compare_installed_snapshot(
    manifest: dict[str, Any],
    target: Path,
    unsafe_reason: str = "unsafe_target_entry",
) -> list[str]:
    try:
        actual = scan_tree(target, unsafe_reason)
        actual_root_mode = directory_identity(target, unsafe_reason)["mode"]
    except InstallError as error:
        if error.reason == unsafe_reason:
            return ["<unsafe-target-entry>"]
        raise
    differences = compare_entries(manifest["entries"], actual)
    if (
        manifest["schema_version"] == INSTALL_SCHEMA_VERSION
        and manifest["target_root_mode"] != actual_root_mode
    ):
        differences.insert(0, "<root-mode>")
    return differences


def verify_internal(target: Path, manifest_path: Path) -> tuple[dict[str, Any], list[str]]:
    manifest_path = validate_manifest_location(target, manifest_path)
    manifest = load_manifest(manifest_path)
    validate_manifest_binding(target, manifest_path, manifest)
    require_installed_manifest(manifest)
    return manifest, compare_installed_snapshot(manifest, target)


def _platform_facts() -> dict[str, str]:
    return {
        "system": sys.platform,
        "machine": platform.machine().lower(),
        "macos_version": platform.mac_ver()[0],
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_releaselevel": sys.version_info.releaselevel,
    }


def _python_runtime_reason(facts: dict[str, str]) -> str | None:
    if (
        facts.get("python_implementation") != "CPython"
        or facts.get("python_releaselevel") != "final"
    ):
        return "RUNTIME_UNVERIFIED"
    version = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", facts.get("python_version", ""))
    if version is None:
        return "RUNTIME_UNVERIFIED"
    parsed = tuple(map(int, version.groups()))
    if parsed < MIN_SUPPORTED_PYTHON:
        return "PYTHON_UPDATE_REQUIRED"
    if parsed >= MAX_EXCLUSIVE_SUPPORTED_PYTHON:
        return "RUNTIME_UNVERIFIED"
    return None


def _macos_runtime_reason(facts: dict[str, str]) -> str | None:
    version = re.fullmatch(r"(\d+)(?:\.\d+){0,2}", facts.get("macos_version", ""))
    if version is None:
        return "MACOS_VERSION_UNVERIFIED"
    if int(version.group(1)) < MIN_SUPPORTED_MACOS_MAJOR:
        return "MACOS_UPDATE_REQUIRED"
    return None


def build_preflight(
    source: Path, skills_root: Path, selection_source: str,
) -> dict[str, Any]:
    """Read explicit installation facts without creating directories or probes."""
    report: dict[str, Any] = {
        "preflight_schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": "NOT_READY",
        "read_only": True,
        "platform": None,
        "source": None,
        "skills_root": None,
        "install_mode": "BLOCKED",
        "pending_write_checks": ["MODE_CAPABILITY_PROBE", "SWITCH_CAPABILITY_PROBE"],
        "reasons": [],
    }
    reasons = report["reasons"]
    if selection_source not in PREFLIGHT_SELECTION_SOURCES:
        reasons.append("SELECTION_SOURCE_INVALID")
        return report

    if selection_source == "CODEX_HOME":
        try:
            home = validate_codex_home()
            if Path(os.environ["CODEX_HOME"]) != home:
                raise InstallError("unsafe_codex_home")
        except (InstallError, OSError, ValueError, RuntimeError):
            reasons.append("CODEX_HOME_INVALID")
            return report
        if Path(skills_root) != home / "skills":
            reasons.append("CODEX_HOME_SELECTION_MISMATCH")
            return report

    try:
        source_path = strict_existing_directory(Path(source), "unsafe_source_entry")
        if source_path != Path(source):
            raise InstallError("unsafe_source_entry")
        entries = scan_tree(source_path)
        validate_runtime_layout(entries)
        report["source"] = {
            "file_count": sum(entry["type"] == "file" for entry in entries.values()),
            "tree_digest": canonical_tree_digest(entries),
        }
    except (InstallError, OSError, ValueError, RuntimeError):
        reasons.append("SOURCE_LAYOUT_INVALID")
        return report

    try:
        facts = _platform_facts()
    except (OSError, ValueError, RuntimeError):
        reasons.append("PLATFORM_FACTS_UNAVAILABLE")
        return report
    report["platform"] = facts
    if facts.get("system") != "darwin":
        reasons.append("PLATFORM_UNSUPPORTED")
    else:
        macos_reason = _macos_runtime_reason(facts)
        if macos_reason is not None:
            reasons.append(macos_reason)
    if facts.get("machine") != "arm64":
        reasons.append("ARCHITECTURE_UNSUPPORTED")
    runtime_reason = _python_runtime_reason(facts)
    if runtime_reason is not None:
        reasons.append(runtime_reason)

    try:
        root = strict_existing_directory(Path(skills_root), "unsafe_skills_root")
        if root != Path(skills_root):
            raise InstallError("unsafe_skills_root")
        before = directory_identity(root, "unsafe_skills_root")
    except (InstallError, OSError, ValueError, RuntimeError):
        reasons.append("SKILLS_ROOT_INVALID")
        return report
    try:
        filesystem = filesystem_identity(root)
        _validate_target_filesystem(filesystem)
        if (
            filesystem["device"] != before["device"]
            or not os.path.isabs(filesystem["mount_target"])
            or re.fullmatch(r"[A-Za-z0-9_.+-]+", filesystem["filesystem_type"]) is None
            or directory_identity(root, "unsafe_skills_root") != before
        ):
            raise InstallError("filesystem_identity_unavailable")
        # Public reports retain the four identity keys without disclosing a path.
        public_filesystem = {
            **filesystem,
            "mount_target": "sha256:" + hashlib.sha256(
                filesystem["mount_target"].encode("utf-8", errors="strict")
            ).hexdigest(),
        }
        report["skills_root"] = {
            "selection_source": selection_source,
            "stable_identity": {key: before[key] for key in ("type", "device", "inode")},
            "filesystem_identity": public_filesystem,
        }
    except (InstallError, OSError, ValueError, RuntimeError):
        reasons.append("FILESYSTEM_IDENTITY_UNAVAILABLE")
        return report

    target = root / SKILL_NAME
    state = root / f".{SKILL_NAME}-install"
    try:
        present = []
        for path in (target, state):
            try:
                path.lstat()
            except FileNotFoundError:
                present.append(False)
            else:
                present.append(True)
        if present == [False, False]:
            report["install_mode"] = "FRESH_INSTALL"
        elif present == [True, True]:
            strict_existing_directory(target, "unsafe_target_entry")
            strict_existing_directory(state, "unsafe_install_state")
            _, differences = verify_internal(target, state / MANIFEST_NAME)
            if differences:
                reasons.append("INSTALLED_TREE_DRIFT")
            else:
                report["install_mode"] = "CONTROLLED_UPGRADE"
        else:
            reasons.append("INSTALL_STATE_INCOMPLETE")
    except (InstallError, OSError, ValueError, RuntimeError, TypeError):
        reasons.append("INSTALLED_MANIFEST_INVALID")
    if not reasons:
        report["status"] = "READY"
    return report


def _read_release_source_head(source: Path) -> str:
    """Recheck a release tree already bound by the archive's expected_commit gate.

    This local manifest is an integrity inventory, not a signature or trust anchor.
    Only the fixed release root may supply it; never search ancestors for one.
    """
    reason = "source_head_unavailable"

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_key")
            value[key] = item
        return value

    try:
        top = source.parents[1]
        if top.name != "Vibe-Leader-3.1.0" or source != top / "skill" / SKILL_NAME:
            raise ValueError("release_layout_invalid")
        for directory in (top, source.parent, source):
            strict_existing_directory(directory, reason)
            if stat.S_IMODE(directory.lstat().st_mode) != 0o755:
                raise ValueError("release_directory_mode_invalid")
        content, _, _ = read_bounded_regular_file(top / "RELEASE-MANIFEST.json", reason)
        manifest = json.loads(content.decode("utf-8"), object_pairs_hook=unique_object)
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"format_version", "source_commit", "top_level", "files"}
            or type(manifest["format_version"]) is not int or manifest["format_version"] != 1
            or manifest["top_level"] != "Vibe-Leader-3.1.0"
            or not isinstance(manifest["source_commit"], str)
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", manifest["source_commit"]) is None
            or not isinstance(manifest["files"], list) or len(manifest["files"]) > 499
        ):
            raise ValueError("release_manifest_invalid")
        prefix = f"skill/{SKILL_NAME}/"
        expected: dict[str, dict[str, Any]] = {}
        names: dict[str, tuple[str, bool]] = {}
        skill_paths = []
        total = len(content)
        for record in manifest["files"]:
            if (
                not isinstance(record, dict) or set(record) != {"path", "mode", "size", "sha256"}
                or not isinstance(record["path"], str)
                or type(record["mode"]) is not int or record["mode"] not in (0o644, 0o755)
                or type(record["size"]) is not int or not 0 <= record["size"] <= 25 * 1024 * 1024
                or not isinstance(record["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
            ):
                raise ValueError("release_record_invalid")
            path = record["path"]
            parts = path.split("/")
            if (
                "\\" in path or ":" in path
                or any(unicodedata.category(char).startswith("C") for char in path)
                or any(part in ("", ".", "..") or part.rstrip(" .") != part
                       or unicodedata.normalize("NFC", part).casefold() == ".git" for part in parts)
                or path == "RELEASE-MANIFEST.json"
            ):
                raise ValueError("release_path_invalid")
            for index in range(1, len(parts) + 1):
                partial = "/".join(parts[:index])
                key = unicodedata.normalize("NFC", partial).casefold()
                is_file = index == len(parts)
                previous = names.get(key)
                if previous is not None and (previous != (partial, False) or is_file):
                    raise ValueError("release_path_collision")
                names[key] = (partial, is_file)
            total += record["size"]
            if total > 50 * 1024 * 1024:
                raise ValueError("release_size_invalid")
            if parts[-1] == "SKILL.md":
                skill_paths.append(path)
            if path.startswith(prefix):
                relative = path[len(prefix):]
                expected[relative] = {
                    "type": "file", "mode": record["mode"],
                    "size": record["size"], "sha256": record["sha256"],
                }
                relative_parts = relative.split("/")
                for index in range(1, len(relative_parts)):
                    expected["/".join(relative_parts[:index])] = {"type": "directory", "mode": 0o755}
        if skill_paths != [prefix + "SKILL.md"] or expected != scan_tree(source, reason):
            raise ValueError("release_skill_inventory_invalid")
        return manifest["source_commit"]
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError, InstallError) as error:
        raise InstallError(reason, 4, status="unknown") from error


def read_source_head(source: Path) -> str:
    source = Path(os.path.abspath(source))
    release_root = source.parents[1] if len(source.parents) >= 2 else None
    release_layout = (
        release_root is not None
        and release_root.name == "Vibe-Leader-3.1.0"
        and source == release_root / "skill" / SKILL_NAME
    )
    current = source
    git_directory: Path | None = None
    while True:
        candidate = current / ".git"
        if candidate.is_dir() and not candidate.is_symlink():
            git_directory = candidate
            break
        if candidate.is_file() and not candidate.is_symlink():
            content = candidate.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir: "):
                raise InstallError("source_head_unavailable", 4, status="unknown")
            resolved = Path(content[8:])
            if not resolved.is_absolute():
                resolved = current / resolved
            try:
                git_directory = resolved.resolve(strict=True)
            except OSError as error:
                raise InstallError(
                    "source_head_unavailable",
                    4,
                    status="unknown",
                ) from error
            if git_directory.is_symlink() or not git_directory.is_dir():
                raise InstallError("source_head_unavailable", 4, status="unknown")
            break
        if candidate.is_symlink() or candidate.exists():
            raise InstallError("source_head_unavailable", 4, status="unknown")
        if current.parent == current or (release_layout and current == release_root):
            return _read_release_source_head(source)
        current = current.parent

    head_path = git_directory / "HEAD"
    try:
        head_text = head_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise InstallError("source_head_unavailable", 4, status="unknown") from error
    if head_text.startswith("ref: "):
        reference = head_text[5:]
        if not re.fullmatch(r"refs/[A-Za-z0-9._/-]+", reference) or ".." in reference:
            raise InstallError("source_head_unavailable", 4, status="unknown")
        reference_path = git_directory / reference
        try:
            head_text = reference_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            common_directory_path = git_directory / "commondir"
            try:
                common_relative = common_directory_path.read_text(
                    encoding="utf-8"
                ).strip()
                common_directory = (git_directory / common_relative).resolve(strict=True)
                head_text = (common_directory / reference).read_text(
                    encoding="ascii"
                ).strip()
            except (OSError, UnicodeDecodeError) as error:
                raise InstallError(
                    "source_head_unavailable",
                    4,
                    status="unknown",
                ) from error
        except (OSError, UnicodeDecodeError) as error:
            raise InstallError("source_head_unavailable", 4, status="unknown") from error
    if not _is_git_object_id(head_text):
        raise InstallError("source_head_unavailable", 4, status="unknown")
    return head_text


def candidate_inventory_identity(
    source: Path,
    target: Path,
    source_entries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    skill_entry = source_entries.get("SKILL.md")
    if not isinstance(skill_entry, dict) or skill_entry.get("type") != "file":
        raise InstallError("candidate_inventory_not_unique")
    try:
        text = (source / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise InstallError("candidate_inventory_not_unique") from error
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    names = [] if match is None else re.findall(
        r"^name:\s*['\"]?([A-Za-z0-9-]+)['\"]?\s*$",
        match.group(1),
        re.MULTILINE,
    )
    declared_name = names[0] if len(names) == 1 else None
    state = "READY" if declared_name == SKILL_NAME else "NAME_MISMATCH"
    return {
        "state": state,
        "proof_kind": "PROCESS_FREE_SOURCE_LAYOUT",
        "discovery_id": SKILL_NAME,
        "declared_name": declared_name,
        "enabled": True,
        "duplicate_count": 1,
        "load_errors": [],
        "future_locator": str(Path(os.path.abspath(target)) / "SKILL.md"),
        "source_skill_sha256": skill_entry["sha256"],
    }


def _validate_candidate_inventory(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != CANDIDATE_INVENTORY_KEYS:
        raise InstallError("upgrade_request_invalid")
    if (
        value.get("state") != "READY"
        or value.get("proof_kind") != "PROCESS_FREE_SOURCE_LAYOUT"
        or value.get("discovery_id") != SKILL_NAME
        or value.get("declared_name") != SKILL_NAME
        or value.get("enabled") is not True
        or value.get("duplicate_count") != 1
        or value.get("load_errors") != []
        or not isinstance(value.get("future_locator"), str)
        or not Path(value["future_locator"]).is_absolute()
        or not _is_sha256(value.get("source_skill_sha256"))
    ):
        raise InstallError("candidate_inventory_not_unique")


def _validate_switch_evidence(value: Any, capability: str) -> None:
    if not isinstance(value, dict) or set(value) != SWITCH_EVIDENCE_KEYS:
        raise InstallError("upgrade_request_invalid")
    if value.get("capability") != capability:
        raise InstallError("upgrade_request_invalid")
    for key in (
        "exchange_attempted",
        "noreplace_attempted",
        "postconditions_verified",
        "restored",
    ):
        if not isinstance(value.get(key), bool):
            raise InstallError("upgrade_request_invalid")
    if value.get("exchange_result") not in {
        "NOT_ATTEMPTED",
        "UNSUPPORTED",
        "VERIFIED",
        "UNKNOWN",
    } or value.get("noreplace_result") not in {
        "NOT_ATTEMPTED",
        "VERIFIED",
        "UNKNOWN",
    }:
        raise InstallError("upgrade_request_invalid")
    for key in (
        "left_identity_before",
        "right_identity_before",
        "left_identity_after",
        "right_identity_after",
        "left_identity_restored",
        "right_identity_restored",
    ):
        child = value.get(key)
        if child is not None:
            _validate_probe_directory_snapshot(child)
    if capability == "UNKNOWN":
        if value["postconditions_verified"] or value["restored"]:
            raise InstallError("upgrade_request_invalid")
        return
    if not value["postconditions_verified"] or not value["restored"]:
        raise InstallError("upgrade_request_invalid")
    if capability == "EXCHANGE_SUPPORTED" and (
        value["exchange_result"] != "VERIFIED"
        or value["noreplace_attempted"]
        or value["noreplace_result"] != "NOT_ATTEMPTED"
        or value["left_identity_after"] != value["right_identity_before"]
        or value["right_identity_after"] != value["left_identity_before"]
        or value["left_identity_restored"] != value["left_identity_before"]
        or value["right_identity_restored"] != value["right_identity_before"]
    ):
        raise InstallError("upgrade_request_invalid")
    if capability == "NOREPLACE_ONLY" and (
        value["exchange_result"] != "UNSUPPORTED"
        or not value["noreplace_attempted"]
        or value["noreplace_result"] != "VERIFIED"
    ):
        raise InstallError("upgrade_request_invalid")


def upgrade_request_digest(request: dict[str, Any]) -> str:
    material = {key: value for key, value in request.items() if key != "request_digest"}
    return _canonical_json_digest(material)


def _validate_route_a_binding(request: dict[str, Any], reason: str) -> None:
    if (
        request.get("switch_capability") != ROUTE_A_SWITCH_CAPABILITY
        or request.get("switch_backend") != WINDOWS_MOVEFILEEX_NOREPLACE
        or not _is_sha256(request.get("switch_evidence_digest"))
        or not _is_sha256(request.get("backend_implementation_digest"))
    ):
        raise InstallError(reason)
    try:
        _validate_backend_stable_identity(request.get("skills_root_stable_identity"))
        _validate_backend_wsl_mount(request.get("wsl_mount_identity"))
        _validate_backend_windows_volume(request.get("windows_volume_identity"))
    except InstallError as error:
        raise InstallError(reason) from error
    if request["windows_volume_identity"]["drive"] != (
        f"{request['wsl_mount_identity']['mount_target'][-1].upper()}:"
    ):
        raise InstallError(reason)


def _validate_upgrade_request_common(request: dict[str, Any]) -> None:
    if (
        request.get("operation") != "upgrade"
        or not _is_safe_path_identifier(request.get("operation_id"))
        or not _is_safe_identifier(request.get("approval_id"))
        or request.get("phase") != "PREPARED"
        or request.get("previous_phase_digest") is not None
        or request.get("new_stage_identity") is not None
        or request.get("recovery_directory") is not None
        or not isinstance(request.get("created_at_utc"), str)
        or not _is_git_object_id(request.get("source_head"))
        or not _is_sha256(request.get("source_tree_digest"))
        or not _is_sha256(request.get("old_manifest_digest"))
        or not _is_sha256(request.get("old_target_tree_digest"))
        or not _is_sha256(request.get("old_state_tree_digest"))
        or request.get("switch_capability") not in SWITCH_CAPABILITIES
        or request.get("mode_policy") not in {"strict", "target-observed"}
        or not _is_sha256(request.get("request_digest"))
    ):
        raise InstallError("upgrade_request_invalid")
    for key in ("source", "target", "manifest"):
        if not isinstance(request.get(key), str) or not Path(request[key]).is_absolute():
            raise InstallError("upgrade_request_invalid")
    _validate_directory_identity(request.get("source_root_identity"))
    _validate_directory_identity(request.get("old_target_identity"))
    _validate_directory_identity(request.get("old_state_identity"))
    _validate_target_filesystem(request.get("target_filesystem_identity"))
    _validate_candidate_inventory(request.get("candidate_inventory"))


def validate_upgrade_request_v1(request: Any) -> None:
    if (
        not isinstance(request, dict)
        or set(request) != UPGRADE_REQUEST_V1_KEYS
        or request.get("upgrade_schema_version") != LEGACY_UPGRADE_SCHEMA_VERSION
        or isinstance(request.get("upgrade_schema_version"), bool)
        or request.get("journal_schema_version") != LEGACY_JOURNAL_SCHEMA_VERSION
        or isinstance(request.get("journal_schema_version"), bool)
    ):
        raise InstallError("upgrade_request_invalid")
    _validate_upgrade_request_common(request)
    _validate_switch_evidence(
        request.get("switch_evidence"),
        request["switch_capability"],
    )
    if request["request_digest"] != upgrade_request_digest(request):
        raise InstallError("upgrade_request_digest_invalid")


def validate_upgrade_request_v2(request: Any) -> None:
    if (
        not isinstance(request, dict)
        or set(request) != UPGRADE_REQUEST_V2_KEYS
        or request.get("upgrade_schema_version") != ROUTE_A_UPGRADE_SCHEMA_VERSION
        or isinstance(request.get("upgrade_schema_version"), bool)
        or request.get("journal_schema_version") != ROUTE_A_JOURNAL_SCHEMA_VERSION
        or isinstance(request.get("journal_schema_version"), bool)
    ):
        raise InstallError("upgrade_request_invalid")
    _validate_upgrade_request_common(request)
    _validate_route_a_binding(request, "upgrade_request_invalid")
    if request["request_digest"] != upgrade_request_digest(request):
        raise InstallError("upgrade_request_digest_invalid")


def validate_upgrade_request(request: Any) -> None:
    if not isinstance(request, dict):
        raise InstallError("upgrade_request_invalid")
    version = request.get("upgrade_schema_version")
    if version == LEGACY_UPGRADE_SCHEMA_VERSION and not isinstance(version, bool):
        validate_upgrade_request_v1(request)
        return
    if version == ROUTE_A_UPGRADE_SCHEMA_VERSION and not isinstance(version, bool):
        validate_upgrade_request_v2(request)
        return
    raise InstallError("upgrade_request_invalid")


def _build_upgrade_request(
    source: Path,
    target: Path,
    manifest_path: Path,
    *,
    approval_id: str,
    switch_backend_evidence_path: Path | None,
) -> dict[str, Any]:
    if not _is_safe_identifier(approval_id):
        raise InstallError("approval_id_invalid")
    source = Path(os.path.abspath(source))
    target = validate_skill_target(target)
    manifest_path = validate_manifest_location(target, manifest_path)
    old_manifest = load_manifest(manifest_path)
    validate_manifest_binding(target, manifest_path, old_manifest)
    require_installed_manifest(old_manifest)

    old_target_entries = scan_tree(target, "unsafe_target_entry")
    old_target_identity = directory_identity(target, "unsafe_target_entry")
    old_differences = compare_entries(old_manifest["entries"], old_target_entries)
    if (
        old_manifest.get("schema_version") == INSTALL_SCHEMA_VERSION
        and old_manifest.get("target_root_mode") != old_target_identity["mode"]
    ):
        old_differences.insert(0, "<root-mode>")
    if old_differences:
        raise InstallError("old_install_drift", 3, old_differences)

    state_dir = strict_existing_directory(
        manifest_path.parent,
        "unsafe_install_state",
    )
    old_state_entries = scan_tree(state_dir, "unsafe_install_state")
    old_state_identity = directory_identity(state_dir, "unsafe_install_state")
    source_entries = scan_tree(source)
    source_identity = directory_identity(source, "unsafe_source_entry")
    validate_runtime_layout(source_entries)
    source_head = read_source_head(source)
    candidate_inventory = candidate_inventory_identity(
        source,
        target,
        source_entries,
    )
    _validate_candidate_inventory(candidate_inventory)

    target_filesystem_identity = filesystem_identity(target)
    route_a_evidence: dict[str, Any] | None = None
    route_a_bindings: dict[str, Any] = {}
    if switch_backend_evidence_path is None:
        switch_capability, switch_evidence = probe_switch_capability(target.parent)
        if switch_capability == "UNKNOWN":
            raise InstallError(
                "switch_capability_unknown",
                4,
                status="unknown",
            )
        _validate_switch_evidence(switch_evidence, switch_capability)
    else:
        evidence_path = Path(switch_backend_evidence_path)
        evidence_absolute = Path(os.path.abspath(evidence_path))
        protected_roots = (source, target.parent)
        if (
            not evidence_path.is_absolute()
            or evidence_path != evidence_absolute
            or any(
                evidence_absolute == root or evidence_absolute.is_relative_to(root)
                for root in protected_roots
            )
        ):
            raise InstallError("switch_backend_evidence_invalid")
        route_a_evidence = read_switch_backend_evidence(evidence_absolute)
        current_installer = current_installer_sha256()
        current_root_identity = stable_directory_identity(
            target.parent,
            "switch_backend_evidence_drift",
        )
        current_wsl_mount = wsl_mount_identity(target.parent)
        if (
            route_a_evidence["skills_root"] != str(target.parent)
            or route_a_evidence["installer_sha256"] != current_installer
            or route_a_evidence["backend_implementation_digest"]
            != backend_implementation_digest(
                route_a_evidence["backend"],
                current_installer,
            )
            or route_a_evidence["skills_root_stable_identity"]
            != current_root_identity
            or route_a_evidence["target_filesystem_identity"]
            != target_filesystem_identity
            or route_a_evidence["wsl_mount_identity"] != current_wsl_mount
            or route_a_evidence["windows_volume_identity"]["drive"]
            != f"{current_wsl_mount['mount_target'][-1].upper()}:"
        ):
            raise InstallError("switch_backend_evidence_drift", 3, status="drift")
        switch_capability = route_a_evidence["capability"]
        route_a_bindings = {
            "switch_backend": route_a_evidence["backend"],
            "switch_evidence_digest": route_a_evidence["evidence_digest"],
            "backend_implementation_digest": route_a_evidence[
                "backend_implementation_digest"
            ],
            "skills_root_stable_identity": route_a_evidence[
                "skills_root_stable_identity"
            ],
            "wsl_mount_identity": route_a_evidence["wsl_mount_identity"],
            "windows_volume_identity": route_a_evidence["windows_volume_identity"],
        }

    current_source_entries = scan_tree(source)
    current_source_identity = directory_identity(source, "unsafe_source_entry")
    current_source_head = read_source_head(source)
    source_differences = compare_entries(source_entries, current_source_entries)
    if current_source_identity != source_identity:
        source_differences.insert(0, "<root-identity>")
    if current_source_head != source_head:
        source_differences.insert(0, "<head>")
    if source_differences:
        raise InstallError("source_changed_during_preflight", 3, source_differences)

    current_target_entries = scan_tree(target, "unsafe_target_entry")
    current_target_identity = directory_identity(target, "unsafe_target_entry")
    target_differences = compare_entries(old_target_entries, current_target_entries)
    if current_target_identity != old_target_identity:
        target_differences.insert(0, "<root-identity>")
    if target_differences:
        raise InstallError("target_changed_during_preflight", 3, target_differences)
    current_state_entries = scan_tree(state_dir, "unsafe_install_state")
    current_state_identity = directory_identity(state_dir, "unsafe_install_state")
    state_differences = compare_entries(old_state_entries, current_state_entries)
    if current_state_identity != old_state_identity:
        state_differences.insert(0, "<root-identity>")
    if state_differences:
        raise InstallError("state_changed_during_preflight", 3, state_differences)
    if filesystem_identity(target) != target_filesystem_identity:
        raise InstallError("target_filesystem_changed_during_preflight", 3)
    if route_a_evidence is not None and (
        current_installer_sha256() != route_a_evidence["installer_sha256"]
        or stable_directory_identity(target.parent, "switch_backend_evidence_drift")
        != route_a_evidence["skills_root_stable_identity"]
        or wsl_mount_identity(target.parent) != route_a_evidence["wsl_mount_identity"]
    ):
        raise InstallError("switch_backend_evidence_drift", 3, status="drift")

    request: dict[str, Any] = {
        "upgrade_schema_version": (
            LEGACY_UPGRADE_SCHEMA_VERSION
            if route_a_evidence is None
            else ROUTE_A_UPGRADE_SCHEMA_VERSION
        ),
        "journal_schema_version": (
            LEGACY_JOURNAL_SCHEMA_VERSION
            if route_a_evidence is None
            else ROUTE_A_JOURNAL_SCHEMA_VERSION
        ),
        "operation": "upgrade",
        "operation_id": uuid.uuid4().hex,
        "source": str(source),
        "source_head": source_head,
        "source_tree_digest": canonical_tree_digest(source_entries),
        "source_root_identity": source_identity,
        "target": str(target),
        "manifest": str(manifest_path),
        "old_manifest_digest": old_manifest["manifest_digest"],
        "old_target_identity": old_target_identity,
        "old_target_tree_digest": canonical_tree_digest(old_target_entries),
        "old_state_identity": old_state_identity,
        "old_state_tree_digest": canonical_tree_digest(old_state_entries),
        "new_stage_identity": None,
        "target_filesystem_identity": target_filesystem_identity,
        "mode_policy": old_manifest.get("mode_policy", "strict"),
        "mode_capability": old_manifest.get(
            "mode_capability",
            {"status": "legacy_manifest"},
        ),
        "candidate_inventory": candidate_inventory,
        "switch_capability": switch_capability,
        "approval_id": approval_id,
        "recovery_directory": None,
        "phase": "PREPARED",
        "previous_phase_digest": None,
        "created_at_utc": utc_now(),
    }
    if route_a_evidence is None:
        request["switch_evidence"] = switch_evidence
    else:
        request.update(route_a_bindings)
    request["request_digest"] = upgrade_request_digest(request)
    validate_upgrade_request(request)
    return request


def build_upgrade_request_v1(
    source: Path,
    target: Path,
    manifest_path: Path,
    *,
    approval_id: str,
) -> dict[str, Any]:
    return _build_upgrade_request(
        source,
        target,
        manifest_path,
        approval_id=approval_id,
        switch_backend_evidence_path=None,
    )


def build_upgrade_request_v2(
    source: Path,
    target: Path,
    manifest_path: Path,
    *,
    approval_id: str,
    switch_backend_evidence_path: Path,
) -> dict[str, Any]:
    return _build_upgrade_request(
        source,
        target,
        manifest_path,
        approval_id=approval_id,
        switch_backend_evidence_path=switch_backend_evidence_path,
    )


def build_upgrade_request(
    source: Path,
    target: Path,
    manifest_path: Path,
    *,
    approval_id: str,
    switch_backend_evidence_path: Path | None = None,
) -> dict[str, Any]:
    if switch_backend_evidence_path is None:
        return build_upgrade_request_v1(
            source,
            target,
            manifest_path,
            approval_id=approval_id,
        )
    return build_upgrade_request_v2(
        source,
        target,
        manifest_path,
        approval_id=approval_id,
        switch_backend_evidence_path=switch_backend_evidence_path,
    )


def prepare_upgrade(
    source: Path,
    target: Path,
    manifest_path: Path,
    *,
    approval_id: str,
    output: Path | None,
    switch_backend_evidence_path: Path | None = None,
) -> int:
    output_value: str | None = None
    output_path: Path | None = None
    if output is not None:
        output_path = Path(os.path.abspath(output))
        output_parent = strict_existing_directory(
            output_path.parent,
            "unsafe_upgrade_output",
        )
        source_root = Path(os.path.abspath(source))
        protected_roots = (
            source_root,
            Path(os.path.abspath(target)).parent,
        )
        if any(
            output_path == root or output_path.is_relative_to(root)
            for root in protected_roots
        ):
            raise InstallError("unsafe_upgrade_output")
        if output_parent != output_path.parent:
            raise InstallError("unsafe_upgrade_output")

    request = build_upgrade_request(
        source,
        target,
        manifest_path,
        approval_id=approval_id,
        switch_backend_evidence_path=switch_backend_evidence_path,
    )
    if output_path is not None:
        write_json_exclusive(
            output_path,
            request,
            collision_reason="upgrade_request_collision",
        )
        output_value = str(output_path)
    return emit(
        "upgrade_prepared",
        request=request,
        request_digest=request["request_digest"],
        output=output_value,
    )


def read_upgrade_request(path: Path) -> dict[str, Any]:
    absolute = Path(os.path.abspath(path))
    value = read_manifest_document(absolute)
    try:
        validate_upgrade_request(value)
    except InstallError as error:
        if error.reason == "upgrade_request_digest_invalid":
            raise
        raise InstallError("upgrade_request_invalid") from error
    return value


def _fsync_directory(path: Path, reason: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | require_nofollow(reason)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise InstallError(reason, 4, status="unknown") from error
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise InstallError(reason, 4, status="unknown") from error
    finally:
        os.close(descriptor)


def _mkdir_exclusive(path: Path, *, mode: int, collision_reason: str) -> Path:
    try:
        path.mkdir(mode=mode)
    except FileExistsError as error:
        raise InstallError(collision_reason, 2) from error
    except OSError as error:
        raise InstallError("filesystem_operation_failed", 4, status="unknown") from error
    _fsync_directory(path.parent, "directory_sync_failed")
    return strict_existing_directory(path, "unsafe_upgrade_directory")


def _snapshot_optional_directory(path: Path) -> dict[str, Any] | None:
    absolute = Path(os.path.abspath(path))
    try:
        metadata = os.lstat(absolute)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InstallError(
            "upgrade_postconditions_unknown",
            4,
            status="unknown",
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InstallError(
            "upgrade_postconditions_unknown",
            4,
            status="unknown",
        )
    identity = directory_identity(absolute, "upgrade_postconditions_unknown")
    identity["tree_digest"] = canonical_tree_digest(
        scan_tree(absolute, "upgrade_postconditions_unknown")
    )
    return identity


def _upgrade_paths(
    request: dict[str, Any],
    codex_home: Path,
) -> dict[str, Path]:
    target = Path(request["target"])
    state = Path(request["manifest"]).parent
    skills_root = target.parent
    operation_id = request["operation_id"]
    recovery_root = codex_home / RECOVERY_ROOT_NAME
    recovery = recovery_root / operation_id
    return {
        "target": target,
        "state": state,
        "skills_root": skills_root,
        "stage_target": skills_root / f".{SKILL_NAME}-upgrade-{operation_id}-target",
        "stage_state": skills_root / f".{SKILL_NAME}-upgrade-{operation_id}-state",
        "recovery_root": recovery_root,
        "recovery": recovery,
        "recovery_target": recovery / SKILL_NAME,
        "recovery_state": recovery / state.name,
        "journal": recovery / "journal",
        "request_payload": recovery / "upgrade-request.json",
        "success_receipt": recovery / "upgrade-success-receipt.json",
    }


def _upgrade_observed(paths: dict[str, Path]) -> dict[str, Any]:
    value = {
        "active_target": _snapshot_optional_directory(paths["target"]),
        "active_state": _snapshot_optional_directory(paths["state"]),
        "staging_target": _snapshot_optional_directory(paths["stage_target"]),
        "staging_state": _snapshot_optional_directory(paths["stage_state"]),
        "recovery_target": _snapshot_optional_directory(paths["recovery_target"]),
        "recovery_state": _snapshot_optional_directory(paths["recovery_state"]),
    }
    if set(value) != UPGRADE_OBSERVED_KEYS:
        raise InstallError("upgrade_postconditions_unknown", 4, status="unknown")
    return value


def _write_upgrade_phase(
    request: dict[str, Any],
    paths: dict[str, Path],
    phase: str,
    previous_phase: str | None,
    previous_digest: str | None,
    sequence: int,
    bound_new_stage_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_upgrade_transition(
        request["switch_capability"],
        previous_phase,
        phase,
    )
    observed = _upgrade_observed(paths)
    new_stage_identity = bound_new_stage_identity or observed["staging_target"]
    is_route_a = (
        request["upgrade_schema_version"] == ROUTE_A_UPGRADE_SCHEMA_VERSION
    )
    receipt: dict[str, Any] = {
        "journal_schema_version": (
            ROUTE_A_JOURNAL_SCHEMA_VERSION
            if is_route_a
            else LEGACY_JOURNAL_SCHEMA_VERSION
        ),
        "operation": "upgrade",
        "operation_id": request["operation_id"],
        "request_digest": request["request_digest"],
        "source_head": request["source_head"],
        "source_tree_digest": request["source_tree_digest"],
        "old_manifest_digest": request["old_manifest_digest"],
        "old_target_identity": request["old_target_identity"],
        "new_stage_identity": new_stage_identity,
        "target_filesystem_identity": request["target_filesystem_identity"],
        "switch_capability": request["switch_capability"],
        "phase": phase,
        "previous_phase_digest": previous_digest,
        "observed_postconditions": observed,
        "recovery_directory": str(paths["recovery"]),
        "approval_id": request["approval_id"],
        "created_at_utc": utc_now(),
    }
    if is_route_a:
        receipt.update(
            {
                "switch_backend": request["switch_backend"],
                "switch_evidence_digest": request["switch_evidence_digest"],
                "backend_implementation_digest": request[
                    "backend_implementation_digest"
                ],
            }
        )
    receipt["receipt_digest"] = journal_receipt_digest(receipt)
    expected_keys = (
        UPGRADE_JOURNAL_RECEIPT_V2_KEYS
        if is_route_a
        else UPGRADE_JOURNAL_RECEIPT_V1_KEYS
    )
    if set(receipt) != expected_keys:
        raise InstallError("upgrade_journal_invalid", 4, status="unknown")
    filename = f"{sequence:03d}-{phase.lower().replace('_', '-')}.json"
    write_json_exclusive(
        paths["journal"] / filename,
        receipt,
        collision_reason="upgrade_journal_collision",
    )
    _fsync_directory(paths["journal"], "journal_sync_failed")
    return receipt


def _append_upgrade_terminal(
    request: dict[str, Any],
    paths: dict[str, Path],
    previous: dict[str, Any],
    sequence: int,
    phase: str,
) -> None:
    try:
        _write_upgrade_phase(
            request,
            paths,
            phase,
            previous["phase"],
            previous["receipt_digest"],
            sequence,
            previous["new_stage_identity"],
        )
    except (InstallError, OSError):
        return


def _call_phase_hook(phase_hook: Any, phase: str) -> None:
    if phase_hook is not None:
        phase_hook(phase)


def _require_snapshot(
    actual: dict[str, Any] | None,
    identity: dict[str, Any],
    tree_digest: str,
    reason: str,
) -> None:
    expected = dict(identity)
    expected["tree_digest"] = tree_digest
    if actual != expected:
        raise InstallError(reason, 3)


def _revalidate_upgrade_preflight(
    request: dict[str, Any],
    paths: dict[str, Path],
) -> None:
    source = strict_existing_directory(
        Path(request["source"]),
        "unsafe_source_entry",
    )
    source_identity = directory_identity(source, "unsafe_source_entry")
    source_entries = scan_tree(source)
    source_head = read_source_head(source)
    if (
        source_identity != request["source_root_identity"]
        or canonical_tree_digest(source_entries) != request["source_tree_digest"]
        or source_head != request["source_head"]
    ):
        raise InstallError("source_changed_after_approval", 3)

    observed = _upgrade_observed(paths)
    _require_snapshot(
        observed["active_target"],
        request["old_target_identity"],
        request["old_target_tree_digest"],
        "target_changed_after_approval",
    )
    _require_snapshot(
        observed["active_state"],
        request["old_state_identity"],
        request["old_state_tree_digest"],
        "state_changed_after_approval",
    )
    manifest = load_manifest(Path(request["manifest"]))
    validate_manifest_binding(paths["target"], Path(request["manifest"]), manifest)
    require_installed_manifest(manifest)
    if manifest["manifest_digest"] != request["old_manifest_digest"]:
        raise InstallError("old_manifest_changed_after_approval", 3)
    if filesystem_identity(paths["target"]) != request["target_filesystem_identity"]:
        raise InstallError("target_filesystem_changed_after_approval", 3)
    if request["upgrade_schema_version"] == LEGACY_UPGRADE_SCHEMA_VERSION:
        capability, evidence = probe_switch_capability(paths["skills_root"])
        if capability != request["switch_capability"]:
            raise InstallError("switch_capability_changed_after_approval", 3)
        _validate_switch_evidence(evidence, capability)
        return

    if request["upgrade_schema_version"] != ROUTE_A_UPGRADE_SCHEMA_VERSION:
        raise InstallError("upgrade_request_invalid")
    current_installer = current_installer_sha256()
    current_wsl_mount = wsl_mount_identity(paths["skills_root"])
    if (
        request["switch_capability"] != ROUTE_A_SWITCH_CAPABILITY
        or request["switch_backend"] != WINDOWS_MOVEFILEEX_NOREPLACE
        or current_installer_sha256() != current_installer
        or request["backend_implementation_digest"]
        != backend_implementation_digest(request["switch_backend"], current_installer)
        or stable_directory_identity(
            paths["skills_root"],
            "switch_backend_binding_changed_after_approval",
        )
        != request["skills_root_stable_identity"]
        or current_wsl_mount != request["wsl_mount_identity"]
        or request["target_filesystem_identity"]
        != filesystem_identity(paths["skills_root"])
    ):
        raise InstallError("switch_backend_binding_changed_after_approval", 3)
    current_volume = windows_volume_identity(paths["skills_root"])
    if current_volume != request["windows_volume_identity"]:
        raise InstallError("windows_volume_identity_changed_after_approval", 3)


def _revalidate_upgrade_candidate(
    paths: dict[str, Path],
    frozen: dict[str, Any],
) -> None:
    if set(frozen) != UPGRADE_OBSERVED_KEYS:
        raise InstallError("upgrade_candidate_snapshot_invalid", 3)
    observed = _upgrade_observed(paths)
    if observed["staging_target"] != frozen["staging_target"]:
        raise InstallError("upgrade_candidate_target_drift", 3)
    if observed["staging_state"] != frozen["staging_state"]:
        raise InstallError("upgrade_candidate_state_drift", 3)


def _prepare_upgrade_candidate(
    request: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    source = Path(request["source"])
    source_entries = scan_tree(source)
    source_identity = directory_identity(source, "unsafe_source_entry")
    source_root_mode = source_identity["mode"]
    if (
        canonical_tree_digest(source_entries) != request["source_tree_digest"]
        or source_identity != request["source_root_identity"]
        or read_source_head(source) != request["source_head"]
    ):
        raise InstallError("source_changed_after_approval", 3)
    validate_runtime_layout(source_entries)

    stage_target = _mkdir_exclusive(
        paths["stage_target"],
        mode=source_root_mode,
        collision_reason="upgrade_stage_collision",
    )
    try:
        copy_entries(source, stage_target, source_entries)
        staged_entries = scan_tree(stage_target)
        mode_policy, mode_capability, staged_entries = select_mode_policy(
            source,
            stage_target,
            paths["skills_root"],
            source_entries,
            staged_entries,
        )
        second_staged_entries = scan_tree(stage_target)
        second_source_entries = scan_tree(source)
        if staged_entries != second_staged_entries:
            raise InstallError("upgrade_stage_changed_during_prepare", 3)
        if (
            canonical_tree_digest(second_source_entries)
            != request["source_tree_digest"]
            or directory_identity(source, "unsafe_source_entry")
            != request["source_root_identity"]
            or read_source_head(source) != request["source_head"]
        ):
            raise InstallError("source_changed_after_approval", 3)
        if mode_policy != request["mode_policy"]:
            raise InstallError("upgrade_mode_policy_drift", 3)
        if filesystem_identity(stage_target) != request["target_filesystem_identity"]:
            raise InstallError("upgrade_stage_filesystem_drift", 3)
        target_root_mode = directory_identity(
            stage_target,
            "unsafe_upgrade_stage",
        )["mode"]

        stage_state = _mkdir_exclusive(
            paths["stage_state"],
            mode=0o700,
            collision_reason="upgrade_stage_collision",
        )
        installed_at = utc_now()
        prepared_manifest = build_v2_install_manifest(
            source=source,
            target=paths["target"],
            phase="prepared",
            installed_at_utc=installed_at,
            mode_policy=mode_policy,
            source_root_mode=source_root_mode,
            target_root_mode=target_root_mode,
            source_entries=source_entries,
            entries=staged_entries,
            mode_capability=mode_capability,
            target_filesystem=request["target_filesystem_identity"],
        )
        installed_manifest = build_v2_install_manifest(
            source=source,
            target=paths["target"],
            phase="installed",
            installed_at_utc=installed_at,
            mode_policy=mode_policy,
            source_root_mode=source_root_mode,
            target_root_mode=target_root_mode,
            source_entries=source_entries,
            entries=staged_entries,
            mode_capability=mode_capability,
            target_filesystem=request["target_filesystem_identity"],
        )
        write_json_exclusive(
            stage_state / PREPARED_MANIFEST_NAME,
            prepared_manifest,
            collision_reason="upgrade_stage_collision",
        )
        write_json_exclusive(
            stage_state / MANIFEST_NAME,
            installed_manifest,
            collision_reason="upgrade_stage_collision",
        )
        _fsync_directory(stage_state, "upgrade_stage_sync_failed")
        if scan_tree(stage_target) != staged_entries:
            raise InstallError("upgrade_stage_changed_during_prepare", 3)
        loaded = load_manifest(stage_state / MANIFEST_NAME)
        if loaded != installed_manifest:
            raise InstallError("upgrade_stage_manifest_drift", 3)
        return installed_manifest
    except Exception:
        raise


def _verify_upgrade_success(
    request: dict[str, Any],
    paths: dict[str, Path],
    installed_manifest: dict[str, Any],
) -> None:
    manifest, differences = verify_internal(paths["target"], paths["state"] / MANIFEST_NAME)
    if differences or manifest != installed_manifest:
        raise InstallError("upgraded_install_drift", 4, differences, status="unknown")
    observed = _upgrade_observed(paths)
    if observed["staging_target"] is not None or observed["staging_state"] is not None:
        raise InstallError("upgrade_staging_outcome_unknown", 4, status="unknown")
    _require_snapshot(
        observed["recovery_target"],
        request["old_target_identity"],
        request["old_target_tree_digest"],
        "old_target_archive_drift",
    )
    _require_snapshot(
        observed["recovery_state"],
        request["old_state_identity"],
        request["old_state_tree_digest"],
        "old_state_archive_drift",
    )
    archived_manifest = load_manifest(paths["recovery_state"] / MANIFEST_NAME)
    if archived_manifest["manifest_digest"] != request["old_manifest_digest"]:
        raise InstallError("old_manifest_archive_drift", 3)


def _build_upgrade_success_receipt(
    request: dict[str, Any],
    paths: dict[str, Path],
    installed_manifest: dict[str, Any],
    final_phase: dict[str, Any],
) -> dict[str, Any]:
    observed = final_phase["observed_postconditions"]
    is_route_a = (
        request["upgrade_schema_version"] == ROUTE_A_UPGRADE_SCHEMA_VERSION
    )
    restore_material = {
        "operation_id": request["operation_id"],
        "old_manifest_digest": request["old_manifest_digest"],
        "new_manifest_digest": installed_manifest["manifest_digest"],
        "old_target_tree_digest": request["old_target_tree_digest"],
        "old_state_tree_digest": request["old_state_tree_digest"],
        "recovery_directory": str(paths["recovery"]),
    }
    receipt: dict[str, Any] = {
        "receipt_schema_version": (
            ROUTE_A_UPGRADE_RECEIPT_SCHEMA_VERSION
            if is_route_a
            else LEGACY_UPGRADE_RECEIPT_SCHEMA_VERSION
        ),
        "operation": "upgrade",
        "operation_id": request["operation_id"],
        "status": "VERIFIED",
        "request_digest": request["request_digest"],
        "approval_id": request["approval_id"],
        "switch_capability": request["switch_capability"],
        "gap_disclosure": (
            "TARGET_AND_STATE_EXCHANGED_SEPARATELY"
            if request["switch_capability"] == "EXCHANGE_SUPPORTED"
            else "TARGET_AND_STATE_SWITCHED_SEPARATELY"
        ),
        "old_archive": {
            "recovery_directory": str(paths["recovery"]),
            "target": str(paths["recovery_target"]),
            "state": str(paths["recovery_state"]),
            "manifest_digest": request["old_manifest_digest"],
            "target_tree_digest": request["old_target_tree_digest"],
            "state_tree_digest": request["old_state_tree_digest"],
            "target_identity": observed["recovery_target"],
            "state_identity": observed["recovery_state"],
        },
        "new_active": {
            "target": str(paths["target"]),
            "state": str(paths["state"]),
            "manifest_digest": installed_manifest["manifest_digest"],
            "target_identity": observed["active_target"],
            "state_identity": observed["active_state"],
            "target_tree_digest": canonical_tree_digest(
                scan_tree(paths["target"])
            ),
            "state_tree_digest": canonical_tree_digest(
                scan_tree(paths["state"])
            ),
            "candidate_inventory": request["candidate_inventory"],
            "force_reload_state": "PENDING_CALLER_VERIFICATION",
        },
        "journal": str(paths["journal"]),
        "journal_final_digest": final_phase["receipt_digest"],
        "restore_confirmation_digest": _canonical_json_digest(restore_material),
        "created_at_utc": utc_now(),
    }
    if is_route_a:
        receipt.update(
            {
                "switch_backend": request["switch_backend"],
                "switch_evidence_digest": request["switch_evidence_digest"],
                "backend_implementation_digest": request[
                    "backend_implementation_digest"
                ],
            }
        )
    receipt["receipt_digest"] = success_receipt_digest(receipt)
    expected_keys = (
        UPGRADE_SUCCESS_RECEIPT_V2_KEYS
        if is_route_a
        else UPGRADE_SUCCESS_RECEIPT_V1_KEYS
    )
    if set(receipt) != expected_keys:
        raise InstallError("upgrade_receipt_invalid", 4, status="unknown")
    return receipt


def upgrade(
    request_path: Path,
    confirmation: str,
    *,
    phase_hook: Any = None,
) -> int:
    request = read_upgrade_request(request_path)
    if not hmac.compare_digest(confirmation, request["request_digest"]):
        raise InstallError("confirmation_mismatch", 2)
    if request["switch_capability"] not in {
        "NOREPLACE_ONLY",
        "EXCHANGE_SUPPORTED",
    }:
        raise InstallError("upgrade_capability_not_implemented", 2)

    codex_home = validate_codex_home()
    target = validate_skill_target(Path(request["target"]))
    if target != Path(request["target"]):
        raise InstallError("upgrade_request_invalid")
    paths = _upgrade_paths(request, codex_home)
    if paths["skills_root"] != strict_existing_directory(
        codex_home / "skills",
        "unsafe_skills_root",
    ):
        raise InstallError("upgrade_request_invalid")
    if paths["state"] != expected_manifest_path(target).parent:
        raise InstallError("upgrade_request_invalid")

    recovery_root = paths["recovery_root"]
    try:
        recovery_root.mkdir(mode=0o700)
        _fsync_directory(codex_home, "directory_sync_failed")
    except FileExistsError:
        recovery_root = strict_existing_directory(
            recovery_root,
            "unsafe_rollback_root",
        )
    if filesystem_identity(recovery_root) != request["target_filesystem_identity"]:
        raise InstallError("upgrade_archive_filesystem_mismatch", 3)
    _mkdir_exclusive(
        paths["recovery"],
        mode=0o700,
        collision_reason="upgrade_recovery_collision",
    )
    _mkdir_exclusive(
        paths["journal"],
        mode=0o700,
        collision_reason="upgrade_journal_collision",
    )
    write_json_exclusive(
        paths["request_payload"],
        request,
        collision_reason="upgrade_request_collision",
    )
    _fsync_directory(paths["recovery"], "journal_sync_failed")

    sequence = 0
    current = _write_upgrade_phase(
        request,
        paths,
        "PREPARED",
        None,
        None,
        sequence,
    )
    sequence += 1
    try:
        _call_phase_hook(phase_hook, "PREPARED")
        installed_manifest = _prepare_upgrade_candidate(request, paths)
        current = _write_upgrade_phase(
            request,
            paths,
            "OLD_SNAPSHOT_READY",
            current["phase"],
            current["receipt_digest"],
            sequence,
        )
        sequence += 1
        _call_phase_hook(phase_hook, "OLD_SNAPSHOT_READY")

        _revalidate_upgrade_candidate(
            paths,
            current["observed_postconditions"],
        )
        _revalidate_upgrade_preflight(request, paths)
        if request["switch_capability"] == "EXCHANGE_SUPPORTED":
            renameat2_direct(
                paths["target"],
                paths["stage_target"],
                RENAME_EXCHANGE,
            )
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "TARGET_EXCHANGED",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "TARGET_EXCHANGED")

            rename_noreplace(paths["stage_target"], paths["recovery_target"])
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            _fsync_directory(paths["recovery"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "OLD_TARGET_ARCHIVED",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "OLD_TARGET_ARCHIVED")

            renameat2_direct(
                paths["state"],
                paths["stage_state"],
                RENAME_EXCHANGE,
            )
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "STATE_EXCHANGED",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "STATE_EXCHANGED")

            rename_noreplace(paths["stage_state"], paths["recovery_state"])
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            _fsync_directory(paths["recovery"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "OLD_STATE_ARCHIVED",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "OLD_STATE_ARCHIVED")
        else:
            route_a = (
                request["upgrade_schema_version"]
                == ROUTE_A_UPGRADE_SCHEMA_VERSION
            )

            def move_noreplace(source: Path, destination: Path) -> None:
                if route_a:
                    move_directory_for_backend(
                        request["switch_backend"],
                        source,
                        destination,
                    )
                else:
                    rename_noreplace(source, destination)

            move_noreplace(paths["target"], paths["recovery_target"])
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            _fsync_directory(paths["recovery"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "OLD_TARGET_ARCHIVED",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "OLD_TARGET_ARCHIVED")

            move_noreplace(paths["state"], paths["recovery_state"])
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            _fsync_directory(paths["recovery"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "OLD_STATE_ARCHIVED",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "OLD_STATE_ARCHIVED")

            move_noreplace(paths["stage_target"], paths["target"])
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "NEW_TARGET_ACTIVE",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "NEW_TARGET_ACTIVE")

            move_noreplace(paths["stage_state"], paths["state"])
            _fsync_directory(paths["skills_root"], "upgrade_move_sync_failed")
            current = _write_upgrade_phase(
                request,
                paths,
                "NEW_STATE_ACTIVE",
                current["phase"],
                current["receipt_digest"],
                sequence,
                current["new_stage_identity"],
            )
            sequence += 1
            _call_phase_hook(phase_hook, "NEW_STATE_ACTIVE")

        _verify_upgrade_success(request, paths, installed_manifest)
        current = _write_upgrade_phase(
            request,
            paths,
            "VERIFIED",
            current["phase"],
            current["receipt_digest"],
            sequence,
            current["new_stage_identity"],
        )
        sequence += 1
        _call_phase_hook(phase_hook, "VERIFIED")
        success = _build_upgrade_success_receipt(
            request,
            paths,
            installed_manifest,
            current,
        )
        write_json_exclusive(
            paths["success_receipt"],
            success,
            collision_reason="upgrade_receipt_collision",
        )
        _fsync_directory(paths["recovery"], "journal_sync_failed")
        return emit(
            "upgrade_verified",
            receipt=str(paths["success_receipt"]),
            receipt_digest=success["receipt_digest"],
            recovery_directory=str(paths["recovery"]),
        )
    except InstallError as error:
        terminal = "UNKNOWN" if error.exit_code == 4 else "RECOVERY_REQUIRED"
        _append_upgrade_terminal(
            request,
            paths,
            current,
            sequence,
            terminal,
        )
        raise
    except OSError as error:
        _append_upgrade_terminal(
            request,
            paths,
            current,
            sequence,
            "UNKNOWN",
        )
        raise InstallError(
            "upgrade_filesystem_outcome_unknown",
            4,
            status="unknown",
        ) from error


def _validate_journal_receipt(
    receipt: Any,
    request: dict[str, Any],
    previous: dict[str, Any] | None,
) -> None:
    request_version = request.get("upgrade_schema_version")
    if request_version == LEGACY_UPGRADE_SCHEMA_VERSION:
        expected_keys = UPGRADE_JOURNAL_RECEIPT_V1_KEYS
        expected_journal_version = LEGACY_JOURNAL_SCHEMA_VERSION
    elif request_version == ROUTE_A_UPGRADE_SCHEMA_VERSION:
        expected_keys = UPGRADE_JOURNAL_RECEIPT_V2_KEYS
        expected_journal_version = ROUTE_A_JOURNAL_SCHEMA_VERSION
    else:
        raise _upgrade_journal_invalid()
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise _upgrade_journal_invalid()
    if (
        receipt.get("journal_schema_version") != expected_journal_version
        or receipt.get("operation") != "upgrade"
        or receipt.get("operation_id") != request["operation_id"]
        or receipt.get("request_digest") != request["request_digest"]
        or receipt.get("source_head") != request["source_head"]
        or receipt.get("source_tree_digest") != request["source_tree_digest"]
        or receipt.get("old_manifest_digest") != request["old_manifest_digest"]
        or receipt.get("old_target_identity") != request["old_target_identity"]
        or receipt.get("target_filesystem_identity")
        != request["target_filesystem_identity"]
        or receipt.get("switch_capability") != request["switch_capability"]
        or receipt.get("recovery_directory") != request["recovery_directory"]
        or receipt.get("approval_id") != request["approval_id"]
        or not isinstance(receipt.get("created_at_utc"), str)
        or not _is_sha256(receipt.get("receipt_digest"))
        or receipt["receipt_digest"] != journal_receipt_digest(receipt)
        or not isinstance(receipt.get("observed_postconditions"), dict)
        or set(receipt["observed_postconditions"]) != UPGRADE_OBSERVED_KEYS
    ):
        raise _upgrade_journal_invalid()
    if request_version == ROUTE_A_UPGRADE_SCHEMA_VERSION and any(
        receipt.get(key) != request.get(key)
        for key in (
            "switch_backend",
            "switch_evidence_digest",
            "backend_implementation_digest",
        )
    ):
        raise _upgrade_journal_invalid()
    previous_phase = None if previous is None else previous["phase"]
    previous_digest = None if previous is None else previous["receipt_digest"]
    if receipt.get("previous_phase_digest") != previous_digest:
        raise _upgrade_journal_invalid()
    try:
        validate_upgrade_transition(
            request["switch_capability"],
            previous_phase,
            receipt.get("phase"),
        )
    except InstallError as error:
        raise _upgrade_journal_invalid() from error
    if previous is not None and previous.get("new_stage_identity") is not None:
        if receipt.get("new_stage_identity") != previous["new_stage_identity"]:
            raise _upgrade_journal_invalid()


def _request_directory_snapshot(
    request: dict[str, Any],
    identity_key: str,
    digest_key: str,
) -> dict[str, Any]:
    value = dict(request[identity_key])
    value["tree_digest"] = request[digest_key]
    return value


def _validate_upgrade_phase_postconditions(
    receipts: list[dict[str, Any]],
    request: dict[str, Any],
) -> None:
    snapshot = next(
        (receipt for receipt in receipts if receipt["phase"] == "OLD_SNAPSHOT_READY"),
        None,
    )
    if snapshot is None:
        if any(
            receipt["phase"] not in {"PREPARED", *UPGRADE_TERMINAL_PHASES}
            for receipt in receipts
        ):
            raise _upgrade_journal_invalid()
        return
    candidate_target = snapshot["new_stage_identity"]
    candidate_state = snapshot["observed_postconditions"]["staging_state"]
    if (
        candidate_target is None
        or candidate_state is None
        or snapshot["observed_postconditions"]["staging_target"]
        != candidate_target
    ):
        raise _upgrade_journal_invalid()
    old_target = _request_directory_snapshot(
        request,
        "old_target_identity",
        "old_target_tree_digest",
    )
    old_state = _request_directory_snapshot(
        request,
        "old_state_identity",
        "old_state_tree_digest",
    )
    common = {
        "PREPARED": (old_target, old_state, None, None, None, None),
        "OLD_SNAPSHOT_READY": (
            old_target,
            old_state,
            candidate_target,
            candidate_state,
            None,
            None,
        ),
        "VERIFIED": (
            candidate_target,
            candidate_state,
            None,
            None,
            old_target,
            old_state,
        ),
    }
    if request["switch_capability"] == "NOREPLACE_ONLY":
        expected = {
            **common,
            "OLD_TARGET_ARCHIVED": (
                None,
                old_state,
                candidate_target,
                candidate_state,
                old_target,
                None,
            ),
            "OLD_STATE_ARCHIVED": (
                None,
                None,
                candidate_target,
                candidate_state,
                old_target,
                old_state,
            ),
            "NEW_TARGET_ACTIVE": (
                candidate_target,
                None,
                None,
                candidate_state,
                old_target,
                old_state,
            ),
            "NEW_STATE_ACTIVE": (
                candidate_target,
                candidate_state,
                None,
                None,
                old_target,
                old_state,
            ),
        }
    elif request["switch_capability"] == "EXCHANGE_SUPPORTED":
        expected = {
            **common,
            "TARGET_EXCHANGED": (
                candidate_target,
                old_state,
                old_target,
                candidate_state,
                None,
                None,
            ),
            "OLD_TARGET_ARCHIVED": (
                candidate_target,
                old_state,
                None,
                candidate_state,
                old_target,
                None,
            ),
            "STATE_EXCHANGED": (
                candidate_target,
                candidate_state,
                None,
                old_state,
                old_target,
                None,
            ),
            "OLD_STATE_ARCHIVED": (
                candidate_target,
                candidate_state,
                None,
                None,
                old_target,
                old_state,
            ),
        }
    else:
        raise _upgrade_journal_invalid()
    keys = (
        "active_target",
        "active_state",
        "staging_target",
        "staging_state",
        "recovery_target",
        "recovery_state",
    )
    for receipt in receipts:
        phase = receipt["phase"]
        if phase in UPGRADE_TERMINAL_PHASES:
            continue
        phase_expected = expected.get(phase)
        if phase_expected is None:
            raise _upgrade_journal_invalid()
        observed = receipt["observed_postconditions"]
        if any(observed[key] != value for key, value in zip(keys, phase_expected)):
            raise _upgrade_journal_invalid()


def _load_upgrade_success_receipt(
    path: Path,
    request: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if _path_is_absent(path):
        return None
    value = read_manifest_document(path)
    receipt_version = value.get("receipt_schema_version")
    if receipt_version == LEGACY_UPGRADE_RECEIPT_SCHEMA_VERSION:
        expected_keys = UPGRADE_SUCCESS_RECEIPT_V1_KEYS
    elif receipt_version == ROUTE_A_UPGRADE_RECEIPT_SCHEMA_VERSION:
        expected_keys = UPGRADE_SUCCESS_RECEIPT_V2_KEYS
    else:
        raise _upgrade_journal_invalid()
    if (
        set(value) != expected_keys
        or value.get("operation") != "upgrade"
        or value.get("status") != "VERIFIED"
        or not _is_sha256(value.get("receipt_digest"))
        or value["receipt_digest"] != success_receipt_digest(value)
    ):
        raise _upgrade_journal_invalid()
    if request is not None:
        request_version = request.get("upgrade_schema_version")
        expected_receipt_version = (
            ROUTE_A_UPGRADE_RECEIPT_SCHEMA_VERSION
            if request_version == ROUTE_A_UPGRADE_SCHEMA_VERSION
            else LEGACY_UPGRADE_RECEIPT_SCHEMA_VERSION
        )
        if receipt_version != expected_receipt_version:
            raise _upgrade_journal_invalid()
        if request_version == ROUTE_A_UPGRADE_SCHEMA_VERSION and any(
            value.get(key) != request.get(key)
            for key in (
                "switch_backend",
                "switch_evidence_digest",
                "backend_implementation_digest",
            )
        ):
            raise _upgrade_journal_invalid()
    return value


def _inspection_expected_postconditions(
    request: dict[str, Any],
    last: dict[str, Any],
    current: dict[str, Any],
    success: dict[str, Any] | None,
) -> tuple[str, int, list[str]]:
    phase = last["phase"]
    if phase in UPGRADE_TERMINAL_PHASES:
        return phase, 4, ["TERMINAL_JOURNAL_PHASE"]
    if phase != "VERIFIED" or success is None:
        return "UNKNOWN", 4, ["MISSING_VERIFIED_RECEIPT"]
    if (
        success.get("operation_id") != request["operation_id"]
        or success.get("request_digest") != request["request_digest"]
        or success.get("approval_id") != request["approval_id"]
        or success.get("switch_capability") != request["switch_capability"]
        or success.get("journal_final_digest") != last["receipt_digest"]
        or success.get("journal")
        != str(Path(request["recovery_directory"]) / "journal")
    ):
        raise _upgrade_journal_invalid()
    if request["upgrade_schema_version"] == ROUTE_A_UPGRADE_SCHEMA_VERSION and any(
        success.get(key) != request.get(key)
        for key in (
            "switch_backend",
            "switch_evidence_digest",
            "backend_implementation_digest",
        )
    ):
        raise _upgrade_journal_invalid()
    old_archive = success.get("old_archive")
    new_active = success.get("new_active")
    if not isinstance(old_archive, dict) or not isinstance(new_active, dict):
        raise _upgrade_journal_invalid()
    expected_snapshots = {
        "active_target": new_active.get("target_identity"),
        "active_state": new_active.get("state_identity"),
        "recovery_target": old_archive.get("target_identity"),
        "recovery_state": old_archive.get("state_identity"),
    }
    if any(
        not isinstance(value, dict)
        or set(value) != DIRECTORY_IDENTITY_KEYS | {"tree_digest"}
        for value in expected_snapshots.values()
    ):
        raise _upgrade_journal_invalid()
    if (
        current["active_target"] is None
        or current["active_state"] is None
        or current["staging_target"] is not None
        or current["staging_state"] is not None
        or current["recovery_target"] is None
        or current["recovery_state"] is None
    ):
        return "UNKNOWN", 4, ["CONTRADICTORY_POSTCONDITIONS"]
    for key, expected in expected_snapshots.items():
        actual = current[key]
        actual_root = {child: actual[child] for child in DIRECTORY_IDENTITY_KEYS}
        expected_root = {
            child: expected[child] for child in DIRECTORY_IDENTITY_KEYS
        }
        if actual_root != expected_root:
            return "UNKNOWN", 4, ["CONTRADICTORY_POSTCONDITIONS"]
    drift_reasons = []
    digest_contracts = (
        ("active_target", new_active.get("target_tree_digest"), "ACTIVE_TARGET_CONTENT_DRIFT"),
        ("active_state", new_active.get("state_tree_digest"), "ACTIVE_STATE_CONTENT_DRIFT"),
        ("recovery_target", old_archive.get("target_tree_digest"), "OLD_TARGET_ARCHIVE_DRIFT"),
        ("recovery_state", old_archive.get("state_tree_digest"), "OLD_STATE_ARCHIVE_DRIFT"),
    )
    for key, expected_digest, reason in digest_contracts:
        if not _is_sha256(expected_digest):
            raise _upgrade_journal_invalid()
        if current[key]["tree_digest"] != expected_digest:
            drift_reasons.append(reason)
    if drift_reasons:
        return "DRIFT", 3, drift_reasons
    return "VERIFIED", 0, []


def inspect_upgrade(journal_path: Path) -> dict[str, Any]:
    journal = strict_existing_directory(
        Path(os.path.abspath(journal_path)),
        "unsafe_upgrade_journal",
    )
    recovery = strict_existing_directory(
        journal.parent,
        "unsafe_recovery_directory",
    )
    if journal.name != "journal":
        raise _upgrade_journal_invalid()
    request_path = recovery / "upgrade-request.json"
    request = read_upgrade_request(request_path)
    if request["recovery_directory"] not in {None, str(recovery)}:
        raise _upgrade_journal_invalid()
    request = dict(request)
    request["recovery_directory"] = str(recovery)
    if recovery.name != request["operation_id"]:
        raise _upgrade_journal_invalid()

    try:
        entries = sorted(journal.iterdir())
    except OSError as error:
        raise _upgrade_journal_invalid() from error
    receipts: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for sequence, path in enumerate(entries):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.name.split("-", 1)[0] != f"{sequence:03d}"
            or path.suffix != ".json"
        ):
            raise _upgrade_journal_invalid()
        receipt = read_manifest_document(path)
        _validate_journal_receipt(receipt, request, previous)
        receipts.append(receipt)
        previous = receipt
    if not receipts:
        raise _upgrade_journal_invalid()
    _validate_upgrade_phase_postconditions(receipts, request)

    paths = _upgrade_paths(request, recovery.parent.parent)
    if paths["recovery"] != recovery or paths["journal"] != journal:
        raise _upgrade_journal_invalid()
    current = _upgrade_observed(paths)
    success = _load_upgrade_success_receipt(paths["success_receipt"], request)
    classification, exit_code, reasons = _inspection_expected_postconditions(
        request,
        receipts[-1],
        current,
        success,
    )
    result: dict[str, Any] = {
        "inspection_schema_version": 1,
        "operation_id": request["operation_id"],
        "request_digest": request["request_digest"],
        "switch_capability": request["switch_capability"],
        "classification": classification,
        "exit_code": exit_code,
        "last_phase": receipts[-1]["phase"],
        "journal_final_digest": receipts[-1]["receipt_digest"],
        "receipt_count": len(receipts),
        "observed_postconditions": current,
        "reasons": reasons,
    }
    if request["upgrade_schema_version"] == ROUTE_A_UPGRADE_SCHEMA_VERSION:
        result.update(
            {
                "upgrade_schema_version": ROUTE_A_UPGRADE_SCHEMA_VERSION,
                "switch_backend": request["switch_backend"],
                "switch_evidence_digest": request["switch_evidence_digest"],
                "backend_implementation_digest": request[
                    "backend_implementation_digest"
                ],
            }
        )
        expected_keys = UPGRADE_INSPECTION_V2_KEYS
    else:
        expected_keys = UPGRADE_INSPECTION_V1_KEYS
    if set(result) != expected_keys:
        raise _upgrade_journal_invalid()
    return result


def _load_bound_restore_receipt(
    receipt_path: Path,
    codex_home: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if not receipt_path.is_absolute():
        raise InstallError("restore_receipt_location_invalid")
    absolute = Path(os.path.abspath(receipt_path))
    recovery_root = strict_existing_directory(
        codex_home / RECOVERY_ROOT_NAME,
        "unsafe_rollback_root",
    )
    recovery = strict_existing_directory(
        absolute.parent,
        "restore_receipt_location_invalid",
    )
    if (
        recovery.parent != recovery_root
        or absolute != recovery / "upgrade-success-receipt.json"
    ):
        raise InstallError("restore_receipt_location_invalid")
    try:
        receipt = _load_upgrade_success_receipt(absolute)
    except InstallError as error:
        raise InstallError("restore_receipt_invalid") from error
    if receipt is None:
        raise InstallError("restore_receipt_invalid")
    if receipt.get("operation_id") != recovery.name:
        raise InstallError("restore_receipt_location_invalid")

    request_path = recovery / "upgrade-request.json"
    try:
        upgrade_request = read_upgrade_request(request_path)
    except InstallError as error:
        raise InstallError("restore_receipt_invalid") from error
    if (
        upgrade_request.get("operation_id") != receipt["operation_id"]
        or upgrade_request.get("request_digest") != receipt["request_digest"]
        or upgrade_request.get("approval_id") != receipt["approval_id"]
        or upgrade_request.get("switch_capability") != receipt["switch_capability"]
    ):
        raise InstallError("restore_receipt_invalid")
    if receipt["receipt_schema_version"] == ROUTE_A_UPGRADE_RECEIPT_SCHEMA_VERSION:
        if (
            upgrade_request.get("upgrade_schema_version")
            != ROUTE_A_UPGRADE_SCHEMA_VERSION
            or any(
                receipt.get(key) != upgrade_request.get(key)
                for key in (
                    "switch_backend",
                    "switch_evidence_digest",
                    "backend_implementation_digest",
                )
            )
        ):
            raise InstallError("restore_receipt_invalid")
    elif upgrade_request.get("upgrade_schema_version") != LEGACY_UPGRADE_SCHEMA_VERSION:
        raise InstallError("restore_receipt_invalid")

    old_archive = receipt.get("old_archive")
    new_active = receipt.get("new_active")
    old_keys = {
        "recovery_directory",
        "target",
        "state",
        "manifest_digest",
        "target_tree_digest",
        "state_tree_digest",
        "target_identity",
        "state_identity",
    }
    new_keys = {
        "target",
        "state",
        "manifest_digest",
        "target_identity",
        "state_identity",
        "target_tree_digest",
        "state_tree_digest",
        "candidate_inventory",
        "force_reload_state",
    }
    if (
        not isinstance(old_archive, dict)
        or set(old_archive) != old_keys
        or not isinstance(new_active, dict)
        or set(new_active) != new_keys
    ):
        raise InstallError("restore_receipt_invalid")
    target = validate_skill_target(Path(str(new_active.get("target", ""))))
    state = expected_manifest_path(target).parent
    expected_archive_target = recovery / SKILL_NAME
    expected_archive_state = recovery / state.name
    if (
        new_active.get("target") != str(target)
        or new_active.get("state") != str(state)
        or old_archive.get("recovery_directory") != str(recovery)
        or old_archive.get("target") != str(expected_archive_target)
        or old_archive.get("state") != str(expected_archive_state)
        or receipt.get("journal") != str(recovery / "journal")
        or receipt.get("switch_capability") not in {
            "NOREPLACE_ONLY",
            "EXCHANGE_SUPPORTED",
        }
    ):
        raise InstallError("restore_receipt_invalid")
    for key in ("manifest_digest", "target_tree_digest", "state_tree_digest"):
        if not _is_sha256(old_archive.get(key)) or not _is_sha256(
            new_active.get(key)
        ):
            raise InstallError("restore_receipt_invalid")
    try:
        _validate_probe_directory_snapshot(old_archive.get("target_identity"))
        _validate_probe_directory_snapshot(old_archive.get("state_identity"))
        _validate_probe_directory_snapshot(new_active.get("target_identity"))
        _validate_probe_directory_snapshot(new_active.get("state_identity"))
    except InstallError as error:
        raise InstallError("restore_receipt_invalid") from error
    if (
        old_archive["target_identity"]["tree_digest"]
        != old_archive["target_tree_digest"]
        or old_archive["state_identity"]["tree_digest"]
        != old_archive["state_tree_digest"]
        or new_active["target_identity"]["tree_digest"]
        != new_active["target_tree_digest"]
        or new_active["state_identity"]["tree_digest"]
        != new_active["state_tree_digest"]
        or not _is_sha256(receipt.get("restore_confirmation_digest"))
    ):
        raise InstallError("restore_receipt_invalid")
    restore_material = {
        "operation_id": receipt["operation_id"],
        "old_manifest_digest": old_archive["manifest_digest"],
        "new_manifest_digest": new_active["manifest_digest"],
        "old_target_tree_digest": old_archive["target_tree_digest"],
        "old_state_tree_digest": old_archive["state_tree_digest"],
        "recovery_directory": old_archive["recovery_directory"],
    }
    if not hmac.compare_digest(
        receipt["restore_confirmation_digest"],
        _canonical_json_digest(restore_material),
    ):
        raise InstallError("restore_receipt_invalid")
    return absolute, receipt, upgrade_request


def _require_restore_snapshot(
    actual: dict[str, Any] | None,
    expected: dict[str, Any],
    reason: str,
) -> None:
    if actual != expected:
        raise InstallError(reason, 3)


def _validate_restore_request_common(request: dict[str, Any]) -> None:
    if (
        request.get("operation") != "restore-version"
        or not _is_safe_path_identifier(request.get("operation_id"))
        or not str(request["operation_id"]).startswith("restore-")
        or not _is_safe_path_identifier(request.get("source_upgrade_operation_id"))
        or not _is_safe_identifier(request.get("approval_id"))
        or request.get("phase") != "PREPARED"
        or request.get("previous_phase_digest") is not None
        or not isinstance(request.get("created_at_utc"), str)
        or request.get("switch_capability")
        not in {"NOREPLACE_ONLY", "EXCHANGE_SUPPORTED"}
    ):
        raise InstallError("restore_request_invalid")
    for key in (
        "source_receipt_digest",
        "source_receipt_sha256",
        "source_upgrade_request_digest",
        "active_manifest_digest",
        "archive_manifest_digest",
        "request_digest",
    ):
        if not _is_sha256(request.get(key)):
            raise InstallError("restore_request_invalid")
    for key in (
        "source_receipt",
        "target",
        "state",
        "source_archive_target",
        "source_archive_state",
        "recovery_directory",
    ):
        if not isinstance(request.get(key), str) or not Path(request[key]).is_absolute():
            raise InstallError("restore_request_invalid")
    try:
        _validate_file_identity(
            request.get("source_receipt_identity"),
            "restore_request_invalid",
        )
        for key in (
            "active_target_snapshot",
            "active_state_snapshot",
            "archive_target_snapshot",
            "archive_state_snapshot",
        ):
            _validate_probe_directory_snapshot(request.get(key))
        _validate_target_filesystem(request.get("target_filesystem_identity"))
    except InstallError as error:
        raise InstallError("restore_request_invalid") from error


def validate_restore_request_v1(request: Any) -> None:
    if (
        not isinstance(request, dict)
        or set(request) != RESTORE_REQUEST_V1_KEYS
        or request.get("restore_schema_version") != LEGACY_RESTORE_SCHEMA_VERSION
        or isinstance(request.get("restore_schema_version"), bool)
        or request.get("journal_schema_version") != LEGACY_JOURNAL_SCHEMA_VERSION
        or isinstance(request.get("journal_schema_version"), bool)
    ):
        raise InstallError("restore_request_invalid")
    _validate_restore_request_common(request)
    try:
        _validate_switch_evidence(
            request.get("switch_evidence"),
            request["switch_capability"],
        )
    except InstallError as error:
        raise InstallError("restore_request_invalid") from error
    if request["request_digest"] != restore_request_digest(request):
        raise InstallError("restore_request_digest_invalid")


def validate_restore_request_v2(request: Any) -> None:
    if (
        not isinstance(request, dict)
        or set(request) != RESTORE_REQUEST_V2_KEYS
        or request.get("restore_schema_version") != ROUTE_A_RESTORE_SCHEMA_VERSION
        or isinstance(request.get("restore_schema_version"), bool)
        or request.get("journal_schema_version") != ROUTE_A_JOURNAL_SCHEMA_VERSION
        or isinstance(request.get("journal_schema_version"), bool)
    ):
        raise InstallError("restore_request_invalid")
    _validate_restore_request_common(request)
    _validate_route_a_binding(request, "restore_request_invalid")
    if request["request_digest"] != restore_request_digest(request):
        raise InstallError("restore_request_digest_invalid")


def validate_restore_request(request: Any) -> None:
    if not isinstance(request, dict):
        raise InstallError("restore_request_invalid")
    version = request.get("restore_schema_version")
    if version == LEGACY_RESTORE_SCHEMA_VERSION and not isinstance(version, bool):
        validate_restore_request_v1(request)
        return
    if version == ROUTE_A_RESTORE_SCHEMA_VERSION and not isinstance(version, bool):
        validate_restore_request_v2(request)
        return
    raise InstallError("restore_request_invalid")


def _build_restore_request(
    receipt_path: Path,
    confirmation: str,
    approval_id: str,
    *,
    switch_backend_evidence_path: Path | None,
    expected_restore_schema: int | None = None,
) -> dict[str, Any]:
    if not _is_safe_identifier(approval_id):
        raise InstallError("approval_id_invalid")
    codex_home = validate_codex_home()
    receipt_path, receipt, upgrade_request = _load_bound_restore_receipt(
        receipt_path,
        codex_home,
    )
    _, receipt_identity, receipt_sha256 = read_bounded_regular_file(
        receipt_path,
        "restore_receipt_invalid",
    )
    if receipt_identity["nlink"] != 1:
        raise InstallError("restore_receipt_invalid")
    if not isinstance(confirmation, str) or not hmac.compare_digest(
        confirmation,
        receipt["restore_confirmation_digest"],
    ):
        raise InstallError("confirmation_mismatch")
    if approval_id == receipt["approval_id"]:
        raise InstallError("restore_approval_must_be_new")

    is_route_a = (
        receipt["receipt_schema_version"]
        == ROUTE_A_UPGRADE_RECEIPT_SCHEMA_VERSION
    )
    if expected_restore_schema is not None and expected_restore_schema != (
        ROUTE_A_RESTORE_SCHEMA_VERSION
        if is_route_a
        else LEGACY_RESTORE_SCHEMA_VERSION
    ):
        raise InstallError("restore_request_invalid")
    target = validate_skill_target(Path(receipt["new_active"]["target"]))
    state = expected_manifest_path(target).parent
    route_a_evidence: dict[str, Any] | None = None
    route_a_bindings: dict[str, Any] = {}
    if is_route_a:
        if switch_backend_evidence_path is None:
            raise InstallError("restore_evidence_required")
        evidence_path = Path(switch_backend_evidence_path)
        evidence_absolute = Path(os.path.abspath(evidence_path))
        if (
            not evidence_path.is_absolute()
            or evidence_path != evidence_absolute
            or evidence_absolute == codex_home
            or evidence_absolute.is_relative_to(codex_home)
        ):
            raise InstallError("switch_backend_evidence_invalid")
        route_a_evidence = read_switch_backend_evidence(evidence_absolute)
        if hmac.compare_digest(
            route_a_evidence["evidence_digest"],
            receipt["switch_evidence_digest"],
        ):
            raise InstallError("restore_evidence_must_be_fresh")
        current_installer = current_installer_sha256()
        current_filesystem = filesystem_identity(target)
        current_wsl_mount = wsl_mount_identity(target.parent)
        if (
            route_a_evidence["backend"] != receipt["switch_backend"]
            or route_a_evidence["capability"] != ROUTE_A_SWITCH_CAPABILITY
            or route_a_evidence["skills_root"] != str(target.parent)
            or route_a_evidence["installer_sha256"] != current_installer
            or route_a_evidence["backend_implementation_digest"]
            != backend_implementation_digest(
                route_a_evidence["backend"],
                current_installer,
            )
            or route_a_evidence["skills_root_stable_identity"]
            != stable_directory_identity(
                target.parent,
                "restore_switch_backend_evidence_drift",
            )
            or route_a_evidence["target_filesystem_identity"]
            != current_filesystem
            or route_a_evidence["wsl_mount_identity"] != current_wsl_mount
            or route_a_evidence["windows_volume_identity"]["drive"]
            != f"{current_wsl_mount['mount_target'][-1].upper()}:"
            or receipt["backend_implementation_digest"]
            != route_a_evidence["backend_implementation_digest"]
        ):
            raise InstallError("restore_switch_backend_evidence_drift", 3)
        route_a_bindings = {
            "switch_backend": route_a_evidence["backend"],
            "switch_evidence_digest": route_a_evidence["evidence_digest"],
            "backend_implementation_digest": route_a_evidence[
                "backend_implementation_digest"
            ],
            "skills_root_stable_identity": route_a_evidence[
                "skills_root_stable_identity"
            ],
            "wsl_mount_identity": route_a_evidence["wsl_mount_identity"],
            "windows_volume_identity": route_a_evidence["windows_volume_identity"],
        }
    elif switch_backend_evidence_path is not None:
        raise InstallError("restore_evidence_not_applicable")

    inspection = inspect_upgrade(Path(receipt["journal"]))
    if inspection["classification"] == "DRIFT":
        raise InstallError("restore_source_drift", 3, inspection["reasons"])
    if inspection["classification"] != "VERIFIED":
        raise InstallError(
            "restore_source_unknown",
            4,
            inspection["reasons"],
            status="unknown",
        )

    archive_target = strict_existing_directory(
        Path(receipt["old_archive"]["target"]),
        "unsafe_restore_archive",
    )
    archive_state = strict_existing_directory(
        Path(receipt["old_archive"]["state"]),
        "unsafe_restore_archive",
    )
    active_manifest, active_differences = verify_internal(
        target,
        state / MANIFEST_NAME,
    )
    if active_differences:
        raise InstallError("restore_active_drift", 3, active_differences)
    archive_manifest = load_manifest(archive_state / MANIFEST_NAME)
    require_installed_manifest(archive_manifest)
    if (
        archive_manifest.get("target") != str(target)
        or compare_installed_snapshot(
            archive_manifest,
            archive_target,
            "unsafe_restore_archive",
        )
    ):
        raise InstallError("restore_archive_drift", 3)
    if active_manifest["manifest_digest"] != receipt["new_active"]["manifest_digest"]:
        raise InstallError("restore_active_manifest_drift", 3)
    if archive_manifest["manifest_digest"] != receipt["old_archive"]["manifest_digest"]:
        raise InstallError("restore_archive_manifest_drift", 3)

    active_target = _snapshot_optional_directory(target)
    active_state = _snapshot_optional_directory(state)
    source_archive_target = _snapshot_optional_directory(archive_target)
    source_archive_state = _snapshot_optional_directory(archive_state)
    _require_restore_snapshot(
        active_target,
        receipt["new_active"]["target_identity"],
        "restore_active_drift",
    )
    _require_restore_snapshot(
        active_state,
        receipt["new_active"]["state_identity"],
        "restore_active_drift",
    )
    _require_restore_snapshot(
        source_archive_target,
        receipt["old_archive"]["target_identity"],
        "restore_archive_drift",
    )
    _require_restore_snapshot(
        source_archive_state,
        receipt["old_archive"]["state_identity"],
        "restore_archive_drift",
    )
    target_filesystem = filesystem_identity(target)
    if target_filesystem != upgrade_request["target_filesystem_identity"]:
        raise InstallError("restore_target_filesystem_drift", 3)
    if route_a_evidence is None:
        capability, evidence = probe_switch_capability(target.parent)
        if capability == "UNKNOWN":
            raise InstallError(
                "restore_switch_capability_unknown",
                4,
                status="unknown",
            )
        if capability != receipt["switch_capability"]:
            raise InstallError("restore_switch_capability_drift", 3)
        _validate_switch_evidence(evidence, capability)
    else:
        capability = route_a_evidence["capability"]
        if (
            current_installer_sha256() != route_a_evidence["installer_sha256"]
            or stable_directory_identity(
                target.parent,
                "restore_switch_backend_evidence_drift",
            )
            != route_a_evidence["skills_root_stable_identity"]
            or filesystem_identity(target)
            != route_a_evidence["target_filesystem_identity"]
            or wsl_mount_identity(target.parent)
            != route_a_evidence["wsl_mount_identity"]
        ):
            raise InstallError("restore_switch_backend_evidence_drift", 3)

    operation_id = f"restore-{uuid.uuid4().hex}"
    recovery = codex_home / RECOVERY_ROOT_NAME / operation_id
    request: dict[str, Any] = {
        "restore_schema_version": (
            ROUTE_A_RESTORE_SCHEMA_VERSION
            if is_route_a
            else LEGACY_RESTORE_SCHEMA_VERSION
        ),
        "journal_schema_version": (
            ROUTE_A_JOURNAL_SCHEMA_VERSION
            if is_route_a
            else LEGACY_JOURNAL_SCHEMA_VERSION
        ),
        "operation": "restore-version",
        "operation_id": operation_id,
        "source_receipt": str(receipt_path),
        "source_receipt_digest": receipt["receipt_digest"],
        "source_receipt_identity": receipt_identity,
        "source_receipt_sha256": receipt_sha256,
        "source_upgrade_operation_id": receipt["operation_id"],
        "source_upgrade_request_digest": receipt["request_digest"],
        "target": str(target),
        "state": str(state),
        "source_archive_target": str(archive_target),
        "source_archive_state": str(archive_state),
        "active_target_snapshot": active_target,
        "active_state_snapshot": active_state,
        "archive_target_snapshot": source_archive_target,
        "archive_state_snapshot": source_archive_state,
        "active_manifest_digest": active_manifest["manifest_digest"],
        "archive_manifest_digest": archive_manifest["manifest_digest"],
        "target_filesystem_identity": target_filesystem,
        "switch_capability": capability,
        "approval_id": approval_id,
        "phase": "PREPARED",
        "previous_phase_digest": None,
        "recovery_directory": str(recovery),
        "created_at_utc": utc_now(),
    }
    if route_a_evidence is None:
        request["switch_evidence"] = evidence
    else:
        request.update(route_a_bindings)
    request["request_digest"] = restore_request_digest(request)
    validate_restore_request(request)
    return request


def build_restore_request_v1(
    receipt_path: Path,
    confirmation: str,
    approval_id: str,
) -> dict[str, Any]:
    return _build_restore_request(
        receipt_path,
        confirmation,
        approval_id,
        switch_backend_evidence_path=None,
        expected_restore_schema=LEGACY_RESTORE_SCHEMA_VERSION,
    )


def build_restore_request_v2(
    receipt_path: Path,
    confirmation: str,
    approval_id: str,
    *,
    switch_backend_evidence_path: Path,
) -> dict[str, Any]:
    return _build_restore_request(
        receipt_path,
        confirmation,
        approval_id,
        switch_backend_evidence_path=switch_backend_evidence_path,
        expected_restore_schema=ROUTE_A_RESTORE_SCHEMA_VERSION,
    )


def build_restore_request(
    receipt_path: Path,
    confirmation: str,
    approval_id: str,
    *,
    switch_backend_evidence_path: Path | None = None,
) -> dict[str, Any]:
    return _build_restore_request(
        receipt_path,
        confirmation,
        approval_id,
        switch_backend_evidence_path=switch_backend_evidence_path,
    )


def _restore_paths(request: dict[str, Any], codex_home: Path) -> dict[str, Path]:
    target = Path(request["target"])
    state = Path(request["state"])
    skills_root = target.parent
    operation_id = request["operation_id"]
    recovery_root = codex_home / RECOVERY_ROOT_NAME
    recovery = recovery_root / operation_id
    paths = {
        "target": target,
        "state": state,
        "skills_root": skills_root,
        "stage_target": skills_root / f".{SKILL_NAME}-{operation_id}-target",
        "stage_state": skills_root / f".{SKILL_NAME}-{operation_id}-state",
        "recovery_root": recovery_root,
        "recovery": recovery,
        "recovery_target": recovery / SKILL_NAME,
        "recovery_state": recovery / state.name,
        "journal": recovery / "journal",
        "request_payload": recovery / "restore-request.json",
        "success_receipt": recovery / "restore-success-receipt.json",
        "source_archive_target": Path(request["source_archive_target"]),
        "source_archive_state": Path(request["source_archive_state"]),
    }
    if recovery != Path(request["recovery_directory"]):
        raise InstallError("restore_request_invalid")
    return paths


def _restore_observed(paths: dict[str, Path]) -> dict[str, Any]:
    observed = {
        "active_target": _snapshot_optional_directory(paths["target"]),
        "active_state": _snapshot_optional_directory(paths["state"]),
        "staging_target": _snapshot_optional_directory(paths["stage_target"]),
        "staging_state": _snapshot_optional_directory(paths["stage_state"]),
        "recovery_target": _snapshot_optional_directory(paths["recovery_target"]),
        "recovery_state": _snapshot_optional_directory(paths["recovery_state"]),
        "source_archive_target": _snapshot_optional_directory(
            paths["source_archive_target"]
        ),
        "source_archive_state": _snapshot_optional_directory(
            paths["source_archive_state"]
        ),
    }
    if set(observed) != RESTORE_OBSERVED_KEYS:
        raise InstallError("restore_postconditions_unknown", 4, status="unknown")
    return observed


def _write_restore_phase(
    request: dict[str, Any],
    paths: dict[str, Path],
    phase: str,
    previous: dict[str, Any] | None,
    sequence: int,
) -> dict[str, Any]:
    previous_phase = None if previous is None else previous["phase"]
    previous_digest = None if previous is None else previous["receipt_digest"]
    validate_restore_transition(
        request["switch_capability"],
        previous_phase,
        phase,
    )
    is_route_a = (
        request["restore_schema_version"] == ROUTE_A_RESTORE_SCHEMA_VERSION
    )
    receipt: dict[str, Any] = {
        "journal_schema_version": (
            ROUTE_A_JOURNAL_SCHEMA_VERSION
            if is_route_a
            else LEGACY_JOURNAL_SCHEMA_VERSION
        ),
        "operation": "restore-version",
        "operation_id": request["operation_id"],
        "request_digest": request["request_digest"],
        "source_receipt_digest": request["source_receipt_digest"],
        "switch_capability": request["switch_capability"],
        "phase": phase,
        "previous_phase_digest": previous_digest,
        "observed_postconditions": _restore_observed(paths),
        "recovery_directory": str(paths["recovery"]),
        "approval_id": request["approval_id"],
        "created_at_utc": utc_now(),
    }
    if is_route_a:
        receipt.update(
            {
                "switch_backend": request["switch_backend"],
                "switch_evidence_digest": request["switch_evidence_digest"],
                "backend_implementation_digest": request[
                    "backend_implementation_digest"
                ],
            }
        )
    receipt["receipt_digest"] = restore_journal_receipt_digest(receipt)
    expected_keys = (
        RESTORE_JOURNAL_RECEIPT_V2_KEYS
        if is_route_a
        else RESTORE_JOURNAL_RECEIPT_V1_KEYS
    )
    if set(receipt) != expected_keys:
        raise InstallError("restore_journal_invalid", 4, status="unknown")
    filename = f"{sequence:03d}-{phase.lower().replace('_', '-')}.json"
    write_json_exclusive(
        paths["journal"] / filename,
        receipt,
        collision_reason="restore_journal_collision",
    )
    _fsync_directory(paths["journal"], "journal_sync_failed")
    return receipt


def _restore_journal_tail(
    request: dict[str, Any],
    paths: dict[str, Path],
) -> tuple[dict[str, Any], int]:
    is_route_a = (
        request["restore_schema_version"] == ROUTE_A_RESTORE_SCHEMA_VERSION
    )
    expected_keys = (
        RESTORE_JOURNAL_RECEIPT_V2_KEYS
        if is_route_a
        else RESTORE_JOURNAL_RECEIPT_V1_KEYS
    )
    expected_journal_version = (
        ROUTE_A_JOURNAL_SCHEMA_VERSION
        if is_route_a
        else LEGACY_JOURNAL_SCHEMA_VERSION
    )
    try:
        entries = sorted(paths["journal"].iterdir())
    except OSError as error:
        raise InstallError("restore_journal_invalid", 4, status="unknown") from error
    previous: dict[str, Any] | None = None
    for sequence, path in enumerate(entries):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.name.split("-", 1)[0] != f"{sequence:03d}"
            or path.suffix != ".json"
        ):
            raise InstallError("restore_journal_invalid", 4, status="unknown")
        receipt = read_manifest_document(path)
        previous_phase = None if previous is None else previous["phase"]
        previous_digest = None if previous is None else previous["receipt_digest"]
        if (
            set(receipt) != expected_keys
            or receipt.get("journal_schema_version")
            != expected_journal_version
            or receipt.get("operation") != "restore-version"
            or receipt.get("operation_id") != request["operation_id"]
            or receipt.get("request_digest") != request["request_digest"]
            or receipt.get("source_receipt_digest")
            != request["source_receipt_digest"]
            or receipt.get("switch_capability") != request["switch_capability"]
            or receipt.get("previous_phase_digest") != previous_digest
            or receipt.get("recovery_directory") != str(paths["recovery"])
            or receipt.get("approval_id") != request["approval_id"]
            or not isinstance(receipt.get("created_at_utc"), str)
            or not _is_sha256(receipt.get("receipt_digest"))
            or receipt["receipt_digest"] != restore_journal_receipt_digest(receipt)
            or not isinstance(receipt.get("observed_postconditions"), dict)
            or set(receipt["observed_postconditions"]) != RESTORE_OBSERVED_KEYS
        ):
            raise InstallError("restore_journal_invalid", 4, status="unknown")
        if is_route_a and any(
            receipt.get(key) != request.get(key)
            for key in (
                "switch_backend",
                "switch_evidence_digest",
                "backend_implementation_digest",
            )
        ):
            raise InstallError("restore_journal_invalid", 4, status="unknown")
        try:
            validate_restore_transition(
                request["switch_capability"],
                previous_phase,
                receipt.get("phase"),
            )
            for snapshot in receipt["observed_postconditions"].values():
                if snapshot is not None:
                    _validate_probe_directory_snapshot(snapshot)
        except InstallError as error:
            raise InstallError(
                "restore_journal_invalid",
                4,
                status="unknown",
            ) from error
        previous = receipt
    if previous is None:
        raise InstallError("restore_journal_invalid", 4, status="unknown")
    return previous, len(entries)


def _append_restore_terminal(
    request: dict[str, Any],
    paths: dict[str, Path],
    previous: dict[str, Any],
    sequence: int,
    phase: str,
) -> None:
    try:
        durable_previous, durable_sequence = _restore_journal_tail(request, paths)
        if durable_previous["phase"] in {
            *RESTORE_TERMINAL_PHASES,
            restore_phase_graph(request["switch_capability"])[-1],
        }:
            return
        _write_restore_phase(
            request,
            paths,
            phase,
            durable_previous,
            durable_sequence,
        )
    except (InstallError, OSError):
        return


def _copy_restore_archive(
    request: dict[str, Any],
    paths: dict[str, Path],
) -> None:
    for source_key, stage_key, expected_key in (
        ("source_archive_target", "stage_target", "archive_target_snapshot"),
        ("source_archive_state", "stage_state", "archive_state_snapshot"),
    ):
        source = strict_existing_directory(
            paths[source_key],
            "unsafe_restore_archive",
        )
        entries = scan_tree(source, "unsafe_restore_archive")
        source_identity = directory_identity(source, "unsafe_restore_archive")
        expected = request[expected_key]
        if (
            source_identity
            != {key: expected[key] for key in DIRECTORY_IDENTITY_KEYS}
            or canonical_tree_digest(entries) != expected["tree_digest"]
        ):
            raise InstallError("restore_archive_drift", 3)
        stage = _mkdir_exclusive(
            paths[stage_key],
            mode=source_identity["mode"],
            collision_reason="restore_stage_collision",
        )
        copy_entries(source, stage, entries)
        staged_entries = scan_tree(stage, "unsafe_restore_stage")
        staged_identity = directory_identity(stage, "unsafe_restore_stage")
        if (
            staged_entries != entries
            or staged_identity["mode"] != source_identity["mode"]
            or filesystem_identity(stage) != request["target_filesystem_identity"]
        ):
            raise InstallError("restore_stage_drift", 3)


def _revalidate_restore_preflight(
    request: dict[str, Any],
    paths: dict[str, Path],
) -> None:
    _, receipt_identity, receipt_sha256 = read_bounded_regular_file(
        Path(request["source_receipt"]),
        "restore_source_receipt_drift",
    )
    if (
        receipt_identity != request["source_receipt_identity"]
        or receipt_sha256 != request["source_receipt_sha256"]
    ):
        raise InstallError("restore_source_receipt_drift", 3)
    observed = _restore_observed(paths)
    contracts = (
        ("active_target", "active_target_snapshot", "restore_active_drift"),
        ("active_state", "active_state_snapshot", "restore_active_drift"),
        (
            "source_archive_target",
            "archive_target_snapshot",
            "restore_archive_drift",
        ),
        (
            "source_archive_state",
            "archive_state_snapshot",
            "restore_archive_drift",
        ),
    )
    for observed_key, request_key, reason in contracts:
        _require_restore_snapshot(observed[observed_key], request[request_key], reason)
    if observed["staging_target"] is None or observed["staging_state"] is None:
        raise InstallError("restore_stage_drift", 3)
    if canonical_tree_digest(scan_tree(paths["stage_target"])) != request[
        "archive_target_snapshot"
    ]["tree_digest"]:
        raise InstallError("restore_stage_drift", 3)
    if canonical_tree_digest(scan_tree(paths["stage_state"])) != request[
        "archive_state_snapshot"
    ]["tree_digest"]:
        raise InstallError("restore_stage_drift", 3)
    active_manifest = load_manifest(paths["state"] / MANIFEST_NAME)
    archive_manifest = load_manifest(paths["source_archive_state"] / MANIFEST_NAME)
    if active_manifest["manifest_digest"] != request["active_manifest_digest"]:
        raise InstallError("restore_active_manifest_drift", 3)
    if archive_manifest["manifest_digest"] != request["archive_manifest_digest"]:
        raise InstallError("restore_archive_manifest_drift", 3)
    if filesystem_identity(paths["target"]) != request["target_filesystem_identity"]:
        raise InstallError("restore_target_filesystem_drift", 3)
    if request["restore_schema_version"] == LEGACY_RESTORE_SCHEMA_VERSION:
        capability, evidence = probe_switch_capability(paths["skills_root"])
        if capability != request["switch_capability"]:
            raise InstallError("restore_switch_capability_drift", 3)
        _validate_switch_evidence(evidence, capability)
        return

    if request["restore_schema_version"] != ROUTE_A_RESTORE_SCHEMA_VERSION:
        raise InstallError("restore_request_invalid")
    current_installer = current_installer_sha256()
    if (
        request["switch_capability"] != ROUTE_A_SWITCH_CAPABILITY
        or request["switch_backend"] != WINDOWS_MOVEFILEEX_NOREPLACE
        or request["backend_implementation_digest"]
        != backend_implementation_digest(request["switch_backend"], current_installer)
        or stable_directory_identity(
            paths["skills_root"],
            "restore_switch_backend_binding_drift",
        )
        != request["skills_root_stable_identity"]
        or wsl_mount_identity(paths["skills_root"])
        != request["wsl_mount_identity"]
        or filesystem_identity(paths["skills_root"])
        != request["target_filesystem_identity"]
    ):
        raise InstallError("restore_switch_backend_binding_drift", 3)
    current_volume = windows_volume_identity(paths["skills_root"])
    if current_volume != request["windows_volume_identity"]:
        raise InstallError("restore_windows_volume_identity_drift", 3)


def _verify_restore_success(
    request: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    observed = _restore_observed(paths)
    if observed["staging_target"] is not None or observed["staging_state"] is not None:
        raise InstallError("restore_staging_outcome_unknown", 4, status="unknown")
    contracts = (
        ("active_target", "archive_target_snapshot", "restored_target_drift"),
        ("active_state", "archive_state_snapshot", "restored_state_drift"),
        ("recovery_target", "active_target_snapshot", "new_archive_target_drift"),
        ("recovery_state", "active_state_snapshot", "new_archive_state_drift"),
        (
            "source_archive_target",
            "archive_target_snapshot",
            "source_archive_target_drift",
        ),
        (
            "source_archive_state",
            "archive_state_snapshot",
            "source_archive_state_drift",
        ),
    )
    for observed_key, request_key, reason in contracts:
        actual = observed[observed_key]
        expected_digest = request[request_key]["tree_digest"]
        if actual is None or actual["tree_digest"] != expected_digest:
            raise InstallError(reason, 4, status="unknown")
    restored_manifest, differences = verify_internal(
        paths["target"],
        paths["state"] / MANIFEST_NAME,
    )
    if differences or restored_manifest["manifest_digest"] != request[
        "archive_manifest_digest"
    ]:
        raise InstallError("restored_install_drift", 4, differences, status="unknown")
    archived_manifest = load_manifest(paths["recovery_state"] / MANIFEST_NAME)
    if archived_manifest["manifest_digest"] != request["active_manifest_digest"]:
        raise InstallError("new_archive_manifest_drift", 4, status="unknown")
    return observed


def _build_restore_success_receipt(
    request: dict[str, Any],
    paths: dict[str, Path],
    final_phase: dict[str, Any],
) -> dict[str, Any]:
    observed = final_phase["observed_postconditions"]
    is_route_a = (
        request["restore_schema_version"] == ROUTE_A_RESTORE_SCHEMA_VERSION
    )
    receipt: dict[str, Any] = {
        "receipt_schema_version": (
            ROUTE_A_RESTORE_RECEIPT_SCHEMA_VERSION
            if is_route_a
            else LEGACY_RESTORE_RECEIPT_SCHEMA_VERSION
        ),
        "operation": "restore-version",
        "operation_id": request["operation_id"],
        "status": "VERIFIED",
        "request_digest": request["request_digest"],
        "source_receipt": request["source_receipt"],
        "source_receipt_digest": request["source_receipt_digest"],
        "approval_id": request["approval_id"],
        "switch_capability": request["switch_capability"],
        "gap_disclosure": (
            "TARGET_AND_STATE_EXCHANGED_SEPARATELY"
            if request["switch_capability"] == "EXCHANGE_SUPPORTED"
            else "TARGET_AND_STATE_SWITCHED_SEPARATELY"
        ),
        "restored_active": {
            "target": str(paths["target"]),
            "state": str(paths["state"]),
            "manifest_digest": request["archive_manifest_digest"],
            "target_identity": observed["active_target"],
            "state_identity": observed["active_state"],
        },
        "archived_replaced_version": {
            "recovery_directory": str(paths["recovery"]),
            "target": str(paths["recovery_target"]),
            "state": str(paths["recovery_state"]),
            "manifest_digest": request["active_manifest_digest"],
            "target_identity": observed["recovery_target"],
            "state_identity": observed["recovery_state"],
        },
        "journal": str(paths["journal"]),
        "journal_final_digest": final_phase["receipt_digest"],
        "force_reload_state": "PENDING_CALLER_VERIFICATION",
        "created_at_utc": utc_now(),
    }
    if is_route_a:
        receipt.update(
            {
                "switch_backend": request["switch_backend"],
                "switch_evidence_digest": request["switch_evidence_digest"],
                "backend_implementation_digest": request[
                    "backend_implementation_digest"
                ],
            }
        )
    receipt["receipt_digest"] = success_receipt_digest(receipt)
    expected_keys = (
        RESTORE_SUCCESS_RECEIPT_V2_KEYS
        if is_route_a
        else RESTORE_SUCCESS_RECEIPT_V1_KEYS
    )
    if set(receipt) != expected_keys:
        raise InstallError("restore_receipt_invalid", 4, status="unknown")
    return receipt


def restore_version(
    receipt_path: Path,
    confirmation: str,
    approval_id: str,
    *,
    switch_backend_evidence_path: Path | None = None,
    phase_hook: Any = None,
) -> int:
    request = build_restore_request(
        receipt_path,
        confirmation,
        approval_id,
        switch_backend_evidence_path=switch_backend_evidence_path,
    )
    codex_home = validate_codex_home()
    validate_restore_request(request)
    paths = _restore_paths(request, codex_home)
    if paths["skills_root"] != strict_existing_directory(
        codex_home / "skills",
        "unsafe_skills_root",
    ):
        raise InstallError("restore_request_invalid")
    recovery_root = strict_existing_directory(
        paths["recovery_root"],
        "unsafe_rollback_root",
    )
    if filesystem_identity(recovery_root) != request["target_filesystem_identity"]:
        raise InstallError("restore_archive_filesystem_drift", 3)
    _mkdir_exclusive(
        paths["recovery"],
        mode=0o700,
        collision_reason="restore_recovery_collision",
    )
    _mkdir_exclusive(
        paths["journal"],
        mode=0o700,
        collision_reason="restore_journal_collision",
    )
    write_json_exclusive(
        paths["request_payload"],
        request,
        collision_reason="restore_request_collision",
    )
    _fsync_directory(paths["recovery"], "journal_sync_failed")

    sequence = 0
    current = _write_restore_phase(request, paths, "PREPARED", None, sequence)
    sequence += 1
    try:
        _call_phase_hook(phase_hook, "PREPARED")
        _copy_restore_archive(request, paths)
        current = _write_restore_phase(
            request,
            paths,
            "ARCHIVE_COPY_READY",
            current,
            sequence,
        )
        sequence += 1
        _call_phase_hook(phase_hook, "ARCHIVE_COPY_READY")
        _revalidate_restore_preflight(request, paths)

        if request["switch_capability"] == "EXCHANGE_SUPPORTED":
            renameat2_direct(paths["target"], paths["stage_target"], RENAME_EXCHANGE)
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "TARGET_EXCHANGED",
                current,
                sequence,
            )
            sequence += 1
            rename_noreplace(paths["stage_target"], paths["recovery_target"])
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            _fsync_directory(paths["recovery"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "CURRENT_TARGET_ARCHIVED",
                current,
                sequence,
            )
            sequence += 1
            renameat2_direct(paths["state"], paths["stage_state"], RENAME_EXCHANGE)
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "STATE_EXCHANGED",
                current,
                sequence,
            )
            sequence += 1
            rename_noreplace(paths["stage_state"], paths["recovery_state"])
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            _fsync_directory(paths["recovery"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "CURRENT_STATE_ARCHIVED",
                current,
                sequence,
            )
            sequence += 1
        else:
            route_a = (
                request["restore_schema_version"]
                == ROUTE_A_RESTORE_SCHEMA_VERSION
            )

            def move_noreplace(source: Path, destination: Path) -> None:
                if route_a:
                    move_directory_for_backend(
                        request["switch_backend"],
                        source,
                        destination,
                    )
                else:
                    rename_noreplace(source, destination)

            move_noreplace(paths["target"], paths["recovery_target"])
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            _fsync_directory(paths["recovery"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "CURRENT_TARGET_ARCHIVED",
                current,
                sequence,
            )
            sequence += 1
            _call_phase_hook(phase_hook, "CURRENT_TARGET_ARCHIVED")
            move_noreplace(paths["state"], paths["recovery_state"])
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            _fsync_directory(paths["recovery"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "CURRENT_STATE_ARCHIVED",
                current,
                sequence,
            )
            sequence += 1
            _call_phase_hook(phase_hook, "CURRENT_STATE_ARCHIVED")
            move_noreplace(paths["stage_target"], paths["target"])
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "RESTORED_TARGET_ACTIVE",
                current,
                sequence,
            )
            sequence += 1
            _call_phase_hook(phase_hook, "RESTORED_TARGET_ACTIVE")
            move_noreplace(paths["stage_state"], paths["state"])
            _fsync_directory(paths["skills_root"], "restore_move_sync_failed")
            current = _write_restore_phase(
                request,
                paths,
                "RESTORED_STATE_ACTIVE",
                current,
                sequence,
            )
            sequence += 1
            _call_phase_hook(phase_hook, "RESTORED_STATE_ACTIVE")

        _verify_restore_success(request, paths)
        current = _write_restore_phase(
            request,
            paths,
            "VERIFIED",
            current,
            sequence,
        )
        _call_phase_hook(phase_hook, "VERIFIED")
        success = _build_restore_success_receipt(request, paths, current)
        write_json_exclusive(
            paths["success_receipt"],
            success,
            collision_reason="restore_receipt_collision",
        )
        _fsync_directory(paths["recovery"], "journal_sync_failed")
        return emit(
            "restore_verified",
            receipt=str(paths["success_receipt"]),
            receipt_digest=success["receipt_digest"],
            recovery_directory=str(paths["recovery"]),
        )
    except InstallError as error:
        terminal = "UNKNOWN" if error.exit_code == 4 else "RECOVERY_REQUIRED"
        _append_restore_terminal(
            request,
            paths,
            current,
            sequence,
            terminal,
        )
        raise
    except OSError as error:
        _append_restore_terminal(
            request,
            paths,
            current,
            sequence,
            "UNKNOWN",
        )
        raise InstallError(
            "restore_filesystem_outcome_unknown",
            4,
            status="unknown",
        ) from error


def _validate_file_identity(value: Any, reason: str) -> None:
    if not isinstance(value, dict) or set(value) != FILE_IDENTITY_KEYS:
        raise InstallError(reason)
    if value.get("type") != "file":
        raise InstallError(reason)
    for key in ("mode", "device", "inode", "size", "nlink", "mtime_ns"):
        if not _is_integer(value.get(key)) or value[key] < 0:
            raise InstallError(reason)


def _declared_skill_name(content: bytes, reason: str) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InstallError(reason) from error
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    names = [] if match is None else re.findall(
        r"^name:\s*['\"]?([A-Za-z0-9-]+)['\"]?\s*$",
        match.group(1),
        re.MULTILINE,
    )
    if len(names) != 1:
        raise InstallError(reason)
    return names[0]


def _toggle_inventory_identity(
    inventory: Any,
    locator: Path,
) -> dict[str, Any]:
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != 2
        or not _is_sha256(inventory.get("inventory_sha256"))
        or not isinstance(inventory.get("skills"), list)
        or not isinstance(inventory.get("load_errors"), list)
    ):
        raise InstallError("toggle_inventory_invalid")
    skills = inventory["skills"]
    if any(not isinstance(item, dict) for item in skills):
        raise InstallError("toggle_inventory_invalid")
    matches = [
        item
        for item in skills
        if item.get("declared_name") == SKILL_NAME
    ]
    if len(matches) != 1:
        raise InstallError("toggle_supervisor_not_unique")
    selected = matches[0]
    required = {
        "name",
        "declared_name",
        "source_namespace",
        "path",
        "scope",
        "enabled",
    }
    if (
        not required <= set(selected)
        or not isinstance(selected.get("name"), str)
        or selected["name"].rsplit(":", 1)[-1] != SKILL_NAME
        or selected.get("declared_name") != SKILL_NAME
        or not isinstance(selected.get("source_namespace"), str)
        or not selected["source_namespace"]
        or selected.get("path") != str(locator)
        or not isinstance(selected.get("scope"), str)
        or not isinstance(selected.get("enabled"), bool)
    ):
        raise InstallError("toggle_inventory_invalid")
    related_load_errors = []
    for error in inventory["load_errors"]:
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("path"), str)
            or not isinstance(error.get("message"), str)
        ):
            raise InstallError("toggle_inventory_invalid")
        if error["path"] == str(locator):
            related_load_errors.append(error)
    if related_load_errors:
        raise InstallError("toggle_inventory_load_error")
    return {
        "inventory_schema_version": 2,
        "discovery_id": selected["name"],
        "source_namespace": selected["source_namespace"],
        "declared_name": selected["declared_name"],
        "path": selected["path"],
        "scope": selected["scope"],
        "enabled": selected["enabled"],
        "matching_declared_name_count": len(matches),
        "locator_load_error_count": len(related_load_errors),
    }


def _config_skill_state(content: bytes, locator: Path) -> tuple[str, bool]:
    if tomllib is None:
        raise InstallError("toggle_config_invalid")
    try:
        parsed = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise InstallError("toggle_config_invalid") from error
    skills = parsed.get("skills", {})
    if skills is None:
        skills = {}
    if not isinstance(skills, dict):
        raise InstallError("toggle_config_invalid")
    rows = skills.get("config", [])
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise InstallError("toggle_config_invalid")
    matches = []
    for row in rows:
        if not isinstance(row, dict):
            raise InstallError("toggle_config_invalid")
        if row.get("path") == str(locator):
            matches.append(row)
    if len(matches) > 1:
        raise InstallError("toggle_config_duplicate_locator")
    if not matches:
        return "ABSENT_DEFAULT_ENABLED", True
    enabled = matches[0].get("enabled")
    if not isinstance(enabled, bool):
        raise InstallError("toggle_config_invalid")
    return ("EXPLICIT_ENABLED" if enabled else "EXPLICIT_DISABLED"), enabled


def _capture_toggle_inputs(
    config_path: Path,
    inventory: Any,
    locator: Path,
) -> dict[str, Any]:
    codex_home = validate_codex_home()
    config = Path(os.path.abspath(config_path))
    if not config_path.is_absolute() or config != codex_home / "config.toml":
        raise InstallError("toggle_config_path_invalid")
    target = strict_existing_directory(
        validate_skill_target(locator.parent),
        "toggle_locator_invalid",
    )
    exact_locator = target / "SKILL.md"
    if not locator.is_absolute() or Path(os.path.abspath(locator)) != exact_locator:
        raise InstallError("toggle_locator_invalid")
    config_content, config_identity, config_sha256 = read_bounded_regular_file(
        config,
        "toggle_config_invalid",
    )
    locator_content, locator_identity, locator_sha256 = read_bounded_regular_file(
        exact_locator,
        "toggle_locator_invalid",
    )
    if config_identity["nlink"] != 1:
        raise InstallError("toggle_config_invalid")
    if locator_identity["nlink"] != 1:
        raise InstallError("toggle_locator_invalid")
    if _declared_skill_name(locator_content, "toggle_locator_invalid") != SKILL_NAME:
        raise InstallError("toggle_locator_invalid")
    inventory_identity = _toggle_inventory_identity(inventory, exact_locator)
    config_entry_state, config_enabled = _config_skill_state(
        config_content,
        exact_locator,
    )
    if inventory_identity["enabled"] != config_enabled:
        raise InstallError("toggle_current_state_drift", 3)
    return {
        "config_path": str(config),
        "config_identity": config_identity,
        "config_sha256": config_sha256,
        "locator": str(exact_locator),
        "locator_identity": locator_identity,
        "locator_sha256": locator_sha256,
        "declared_name": SKILL_NAME,
        "inventory_identity": inventory_identity,
        "current_enabled": inventory_identity["enabled"],
        "config_entry_state": config_entry_state,
    }


def build_toggle_request(
    config_path: Path,
    inventory: Any,
    *,
    locator: Path,
    enabled: bool,
    approval_id: str,
) -> dict[str, Any]:
    if not _is_safe_identifier(approval_id):
        raise InstallError("approval_id_invalid")
    if not isinstance(enabled, bool):
        raise InstallError("toggle_enabled_invalid")
    captured = _capture_toggle_inputs(config_path, inventory, locator)
    if captured["current_enabled"] == enabled:
        raise InstallError("toggle_no_state_change")
    request: dict[str, Any] = {
        "toggle_schema_version": TOGGLE_SCHEMA_VERSION,
        "operation": "skill-toggle-request",
        "approval_id": approval_id,
        **captured,
        "requested_enabled": enabled,
        "native_request": {
            "method": "skills/config/write",
            "params": {"path": captured["locator"], "enabled": enabled},
        },
        "force_reload_required": True,
        "created_at_utc": utc_now(),
    }
    request["request_digest"] = toggle_request_digest(request)
    validate_toggle_request(request, config_path, inventory)
    return request


def validate_toggle_request(
    request: dict[str, Any],
    config_path: Path,
    inventory: Any,
) -> None:
    if not isinstance(request, dict) or set(request) != TOGGLE_REQUEST_KEYS:
        raise InstallError("toggle_request_invalid")
    if (
        request.get("toggle_schema_version") != TOGGLE_SCHEMA_VERSION
        or request.get("operation") != "skill-toggle-request"
        or not _is_safe_identifier(request.get("approval_id"))
        or request.get("declared_name") != SKILL_NAME
        or not isinstance(request.get("current_enabled"), bool)
        or not isinstance(request.get("requested_enabled"), bool)
        or request["current_enabled"] == request["requested_enabled"]
        or request.get("force_reload_required") is not True
        or not isinstance(request.get("created_at_utc"), str)
        or not _is_sha256(request.get("request_digest"))
        or request["request_digest"] != toggle_request_digest(request)
    ):
        raise InstallError("toggle_request_invalid")
    _validate_file_identity(request.get("config_identity"), "toggle_request_invalid")
    _validate_file_identity(request.get("locator_identity"), "toggle_request_invalid")
    if not _is_sha256(request.get("config_sha256")) or not _is_sha256(
        request.get("locator_sha256")
    ):
        raise InstallError("toggle_request_invalid")
    captured = _capture_toggle_inputs(
        config_path,
        inventory,
        Path(str(request.get("locator", ""))),
    )
    for key, value in captured.items():
        if request.get(key) != value:
            reason = "toggle_config_drift" if key.startswith("config_") else "toggle_request_drift"
            raise InstallError(reason, 3)
    expected_native = {
        "method": "skills/config/write",
        "params": {
            "path": request["locator"],
            "enabled": request["requested_enabled"],
        },
    }
    if request.get("native_request") != expected_native:
        raise InstallError("toggle_request_invalid")


def install(source: Path, skills_root: Path) -> int:
    root = validate_skills_root(skills_root)
    source = Path(os.path.abspath(source))
    if source.name != SKILL_NAME:
        raise InstallError("unexpected_skill_name")
    target = root / SKILL_NAME
    state_dir = root / f".{SKILL_NAME}-install"
    if target.exists() or target.is_symlink():
        raise InstallError("target_exists")
    if state_dir.exists() or state_dir.is_symlink():
        raise InstallError("install_state_exists")

    source_entries = scan_tree(source)
    source_root_mode = directory_identity(source, "unsafe_source_entry")["mode"]
    validate_runtime_layout(source_entries)
    stage = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-stage-", dir=root))
    stage.chmod(source_root_mode)
    copy_entries(source, stage, source_entries)
    current_source_entries = scan_tree(source)
    current_source_root_mode = directory_identity(source, "unsafe_source_entry")[
        "mode"
    ]
    source_differences = compare_entries(source_entries, current_source_entries)
    if current_source_root_mode != source_root_mode:
        source_differences.insert(0, "<root-mode>")
    if source_differences:
        raise InstallError(
            "source_changed_during_staging",
            3,
            source_differences,
        )
    staged_entries = scan_tree(stage)
    mode_policy, mode_capability, staged_entries = select_mode_policy(
        source,
        stage,
        root,
        source_entries,
        staged_entries,
    )
    second_staged_entries = scan_tree(stage)
    if second_staged_entries != staged_entries:
        raise InstallError("staging_changed_after_probe", 3)
    staged_root_mode = directory_identity(stage, "unsafe_source_entry")["mode"]

    state_dir.mkdir()
    prepared_path = state_dir / PREPARED_MANIFEST_NAME
    installed_at_utc = utc_now()
    prepared_manifest = build_v2_install_manifest(
        source=source,
        target=target,
        phase="prepared",
        installed_at_utc=installed_at_utc,
        mode_policy=mode_policy,
        source_root_mode=source_root_mode,
        target_root_mode=staged_root_mode,
        source_entries=source_entries,
        entries=staged_entries,
        mode_capability=mode_capability,
        target_filesystem=filesystem_identity(stage),
    )
    write_json_exclusive(prepared_path, prepared_manifest)

    rename_noreplace(stage, target)
    try:
        installed_entries = scan_tree(target, "unsafe_target_entry")
        installed_root_mode = directory_identity(target, "unsafe_target_entry")[
            "mode"
        ]
        differences = compare_entries(staged_entries, installed_entries)
        if installed_root_mode != staged_root_mode:
            differences.insert(0, "<root-mode>")
    except InstallError as error:
        if error.reason != "unsafe_target_entry":
            raise
        differences = ["<unsafe-target-entry>"]
    if differences:
        raise InstallError("installed_hash_mismatch", 3, differences)
    manifest_path = state_dir / MANIFEST_NAME
    manifest = build_v2_install_manifest(
        source=source,
        target=target,
        phase="installed",
        installed_at_utc=installed_at_utc,
        mode_policy=mode_policy,
        source_root_mode=source_root_mode,
        target_root_mode=installed_root_mode,
        source_entries=source_entries,
        entries=installed_entries,
        mode_capability=mode_capability,
        target_filesystem=prepared_manifest["target_filesystem"],
    )
    write_json_exclusive(manifest_path, manifest)
    return emit(
        "installed",
        target=str(target),
        manifest=str(manifest_path),
        manifest_digest=manifest["manifest_digest"],
        files=sum(entry["type"] == "file" for entry in staged_entries.values()),
    )


def verify(target: Path, manifest_path: Path) -> int:
    target = validate_skill_target(target)
    _, differences = verify_internal(target, manifest_path)
    if differences:
        raise InstallError("target_drift", 3, differences)
    return emit("verified", target=str(Path(os.path.abspath(target))))


def rollback(target: Path, manifest_path: Path, confirmation: str) -> int:
    target = validate_skill_target(target)
    manifest_path = validate_manifest_location(target, manifest_path)
    manifest = load_manifest(manifest_path)
    validate_manifest_binding(target, manifest_path, manifest)
    require_installed_manifest(manifest)
    if confirmation != manifest["manifest_digest"]:
        raise InstallError("confirmation_mismatch")
    differences = compare_installed_snapshot(manifest, target)
    if differences:
        raise InstallError("target_drift", 3, differences)

    target = Path(os.path.abspath(target))
    state_dir = strict_existing_directory(
        Path(os.path.abspath(manifest_path)).parent,
        "unsafe_install_state",
    )
    codex_home = validate_codex_home()
    rollback_root = codex_home / ".skill-rollbacks"
    try:
        rollback_root.mkdir(mode=0o700)
    except FileExistsError:
        rollback_root = strict_existing_directory(
            rollback_root,
            "unsafe_rollback_root",
        )

    recovery_directory = rollback_root / uuid.uuid4().hex
    try:
        recovery_directory.mkdir(mode=0o700)
    except FileExistsError as error:
        raise InstallError("install_state_collision") from error

    recovery_target = recovery_directory / SKILL_NAME
    recovery_state = recovery_directory / state_dir.name
    rename_noreplace(target, recovery_target)

    def restore_target() -> None:
        try:
            rename_noreplace(recovery_target, target)
        except (InstallError, OSError) as error:
            raise InstallError("rollback_recovery_outcome_unknown", 4) from error

    differences = compare_installed_snapshot(manifest, recovery_target)
    if differences:
        restore_target()
        raise InstallError("target_drift", 3, differences)

    try:
        rename_noreplace(state_dir, recovery_state)
    except InstallError as error:
        if error.reason.endswith("_outcome_unknown"):
            raise
        restore_target()
        raise InstallError("rollback_state_archive_failed", 4) from error

    archived_manifest_path = recovery_state / MANIFEST_NAME
    try:
        archived_manifest = read_manifest_document(archived_manifest_path)
    except InstallError:
        archived_manifest = None
    if archived_manifest != manifest:
        try:
            rename_noreplace(recovery_state, state_dir)
            restore_target()
        except (InstallError, OSError) as error:
            if isinstance(error, InstallError) and error.reason.endswith(
                "_outcome_unknown"
            ):
                raise
            raise InstallError("rollback_recovery_outcome_unknown", 4) from error
        raise InstallError(
            "archived_manifest_mismatch",
            3,
            [MANIFEST_NAME],
        )

    if (
        target.exists()
        or target.is_symlink()
        or state_dir.exists()
        or state_dir.is_symlink()
    ):
        raise InstallError("rollback_completion_outcome_unknown", 4)

    return emit(
        "rolled_back",
        target=str(target),
        recoverable=True,
        recovery_directory=str(recovery_directory),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--source", required=True, type=Path)
    preflight_parser.add_argument("--skills-root", required=True, type=Path)
    preflight_parser.add_argument(
        "--selection-source",
        required=True,
        choices=tuple(sorted(PREFLIGHT_SELECTION_SOURCES)),
    )

    install_parser = commands.add_parser("install")
    install_parser.add_argument("--source", required=True, type=Path)
    install_parser.add_argument("--skills-root", required=True, type=Path)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--target", required=True, type=Path)
    verify_parser.add_argument("--manifest", required=True, type=Path)

    rollback_parser = commands.add_parser("rollback")
    rollback_parser.add_argument("--target", required=True, type=Path)
    rollback_parser.add_argument("--manifest", required=True, type=Path)
    rollback_parser.add_argument("--confirm", required=True)

    attest_parser = commands.add_parser("attest-switch-backend")
    attest_parser.add_argument("--skills-root", required=True, type=Path)
    attest_parser.add_argument(
        "--backend",
        required=True,
        choices=(WINDOWS_MOVEFILEEX_NOREPLACE,),
    )
    attest_parser.add_argument("--approval-id", required=True)
    attest_parser.add_argument("--output", required=True, type=Path)

    prepare_upgrade_parser = commands.add_parser("prepare-upgrade")
    prepare_upgrade_parser.add_argument("--source", required=True, type=Path)
    prepare_upgrade_parser.add_argument("--target", required=True, type=Path)
    prepare_upgrade_parser.add_argument("--manifest", required=True, type=Path)
    prepare_upgrade_parser.add_argument("--approval-id", required=True)
    prepare_upgrade_parser.add_argument("--output", type=Path)
    prepare_upgrade_parser.add_argument(
        "--switch-backend-evidence",
        type=Path,
    )

    upgrade_parser = commands.add_parser("upgrade")
    upgrade_parser.add_argument("--request", required=True, type=Path)
    upgrade_parser.add_argument("--confirm-request", required=True)

    inspect_upgrade_parser = commands.add_parser("inspect-upgrade")
    inspect_upgrade_parser.add_argument("--journal", required=True, type=Path)

    restore_parser = commands.add_parser("restore-version")
    restore_parser.add_argument("--receipt", required=True, type=Path)
    restore_parser.add_argument("--approval-id", required=True)
    restore_parser.add_argument("--confirm", required=True)
    restore_parser.add_argument("--switch-backend-evidence", type=Path)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "preflight":
            report = build_preflight(args.source, args.skills_root, args.selection_source)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report["status"] == "READY" else 4
        try:
            runtime_reason = _python_runtime_reason(_platform_facts())
        except Exception:
            runtime_reason = "RUNTIME_UNVERIFIED"
        if runtime_reason is not None:
            raise InstallError(runtime_reason, 4)
        if args.command == "install":
            return install(args.source, args.skills_root)
        if args.command == "verify":
            return verify(args.target, args.manifest)
        if args.command == "rollback":
            return rollback(args.target, args.manifest, args.confirm)
        if args.command == "attest-switch-backend":
            return attest_switch_backend(
                args.skills_root,
                args.backend,
                args.approval_id,
                args.output,
            )
        if args.command == "prepare-upgrade":
            return prepare_upgrade(
                args.source,
                args.target,
                args.manifest,
                approval_id=args.approval_id,
                output=args.output,
                switch_backend_evidence_path=args.switch_backend_evidence,
            )
        if args.command == "upgrade":
            return upgrade(args.request, args.confirm_request)
        if args.command == "inspect-upgrade":
            result = inspect_upgrade(args.journal)
            status = {
                "VERIFIED": "verified",
                "DRIFT": "drift",
            }.get(result["classification"], "unknown")
            return emit(status, result["exit_code"], inspection=result)
        if args.command == "restore-version":
            return restore_version(
                args.receipt,
                args.confirm,
                args.approval_id,
                switch_backend_evidence_path=args.switch_backend_evidence,
            )
        raise InstallError("unsupported_command", 2)
    except InstallError as error:
        if error.status is not None:
            status = error.status
        elif error.reason.endswith("_outcome_unknown"):
            status = "unknown"
        else:
            status = "drift" if error.exit_code == 3 else "refused"
        return emit(
            status,
            error.exit_code,
            reason=error.reason,
            differences=error.differences,
        )
    except OSError:
        return emit("error", 4, reason="filesystem_operation_failed")


if __name__ == "__main__":
    raise SystemExit(main())
