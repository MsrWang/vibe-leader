#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Produce a fail-closed, read-only identity snapshot for one project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SCHEMA_VERSION = 2
DIRTY_FINGERPRINT_SCHEMA = 3
DEFAULT_MAX_UNTRACKED_BYTES = 64 * 1024 * 1024
SCHEMA_1_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "reason",
        "requested_cwd",
        "pwd",
        "realpath",
        "is_git",
        "git_top_level",
        "git_dir",
        "git_common_dir",
        "worktree_id",
        "branch",
        "head",
        "dirty",
        "dirty_fingerprint",
        "fingerprint_complete",
        "remotes",
        "bound_at_utc",
    }
)
SCHEMA_2_FIELDS = frozenset(
    {
        *SCHEMA_1_FIELDS,
        "binding_kind",
        "write_eligibility",
        "path_input_kind",
        "resolution_traits",
        "logical_path",
        "physical_path",
        "filesystem_identity",
        "aliases",
        "runtime_surface",
        "git",
        "dirty_fingerprint_schema",
        "fingerprint_applicability",
        "fingerprint_reason",
        "captured_at_utc",
    }
)
IDENTITY_SCHEMA_FIELDS: dict[int, frozenset[str]] = {
    1: SCHEMA_1_FIELDS,
    2: SCHEMA_2_FIELDS,
}
SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.*)$")
WINDOWS_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
WSL_UNC_LOCALHOST = re.compile(
    r"^\\\\wsl\.localhost\\[^\\]+(?:\\.*)?$",
    re.IGNORECASE,
)
WSL_UNC_DOLLAR = re.compile(
    r"^\\\\wsl\$\\[^\\]+(?:\\.*)?$",
    re.IGNORECASE,
)


class IdentityReadError(RuntimeError):
    pass


class IdentitySchemaError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PathResolutionError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate_identity_schema(
    value: object,
    *,
    supported_versions: tuple[int, ...] = (SCHEMA_VERSION,),
) -> int:
    """Return an explicitly supported exact schema version or fail closed."""
    if not isinstance(value, dict):
        raise IdentitySchemaError("IDENTITY_NOT_OBJECT")
    version = value.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version not in supported_versions
        or version not in IDENTITY_SCHEMA_FIELDS
    ):
        raise IdentitySchemaError("SCHEMA_UNSUPPORTED")
    if frozenset(value) != IDENTITY_SCHEMA_FIELDS[version]:
        raise IdentitySchemaError("SCHEMA_FIELDS_CHANGED")
    return version


def _has_forbidden_path_text(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return True
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return True
    return any(unicodedata.category(character) == "Cc" for character in value)


def validate_runtime_surface(value: object) -> None:
    """Validate the collector's OS/WSL facts, not CLI version or binary evidence."""
    if (
        not isinstance(value, dict)
        or set(value) != {"platform", "is_wsl", "wsl_distro_name"}
        or _has_forbidden_path_text(value["platform"])
        or not value["platform"].strip()
        or type(value["is_wsl"]) is not bool
        or not (
            value["wsl_distro_name"] is None
            or isinstance(value["wsl_distro_name"], str)
            and (value["wsl_distro_name"] == ""
                 or not _has_forbidden_path_text(value["wsl_distro_name"]))
        )
    ):
        raise IdentitySchemaError("IDENTITY_FACTS_INVALID")


def validate_git_worktree_facts(value: object) -> None:
    """Validate concrete capture facts, not permissions or non-Git diagnostics."""
    validate_identity_schema(value, supported_versions=(2,))

    def require(condition: bool) -> None:
        if not condition:
            raise IdentitySchemaError("IDENTITY_FACTS_INVALID")

    require(value["binding_kind"] == "GIT_WORKTREE" and value["is_git"] is True)
    for field in (
        "requested_cwd", "pwd", "realpath", "logical_path", "physical_path",
        "git_top_level", "git_dir", "git_common_dir",
    ):
        require(classify_path_input(value[field]) != "UNSUPPORTED_OR_RELATIVE")
    require(not _has_forbidden_path_text(value["worktree_id"]))
    require(type(value["dirty"]) is bool)
    require(type(value["dirty_fingerprint_schema"]) is int
            and value["dirty_fingerprint_schema"] == DIRTY_FINGERPRINT_SCHEMA)
    digest = value["dirty_fingerprint"]
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)

    filesystem = value["filesystem_identity"]
    require(isinstance(filesystem, dict)
            and set(filesystem) == {"st_dev", "st_ino", "object_type"})
    require(filesystem["object_type"] == "directory")
    for field in ("st_dev", "st_ino"):
        require(type(filesystem[field]) is int and filesystem[field] >= 0)
    validate_runtime_surface(value["runtime_surface"])
    require(isinstance(value["aliases"], list))
    for alias in value["aliases"]:
        require(isinstance(alias, dict) and set(alias) == {
            "input", "kind", "resolution_traits", "converted_path",
            "logical_path", "physical_path", "filesystem_identity",
            "relation_to_workspace", "reason",
        })
        require(alias["relation_to_workspace"] == "SAME_OBJECT"
                and alias["reason"] is None)
        input_kind = classify_path_input(alias["input"])
        require(input_kind != "UNSUPPORTED_OR_RELATIVE" and alias["kind"] == input_kind)
        for field in ("logical_path", "physical_path"):
            require(classify_path_input(alias[field]) != "UNSUPPORTED_OR_RELATIVE")
        traits = alias["resolution_traits"]
        require(isinstance(traits, list) and all(
            isinstance(trait, str) and trait in {"CONVERTED", "SYMLINK_ALIAS"}
            for trait in traits
        ))
        require(len(traits) == len(set(traits)))
        converted = alias["converted_path"]
        if "CONVERTED" in traits:
            require(classify_path_input(converted) != "UNSUPPORTED_OR_RELATIVE"
                    and converted == alias["logical_path"])
        else:
            require(converted is None)
        alias_filesystem = alias["filesystem_identity"]
        require(isinstance(alias_filesystem, dict)
                and set(alias_filesystem) == {"st_dev", "st_ino", "object_type"})
        require(alias_filesystem["object_type"] == "directory")
        for field in ("st_dev", "st_ino"):
            require(type(alias_filesystem[field]) is int
                    and alias_filesystem[field] >= 0)
        # A SAME_OBJECT claim needs the same concrete directory identity.
        # This checks capture consistency; it neither resolves paths nor grants authority.
        require(alias_filesystem == filesystem)

    topology = value["git"]
    require(isinstance(topology, dict))
    for field in (
        "is_inside_worktree", "is_inside_git_dir", "is_bare",
        "is_detached", "is_unborn",
    ):
        require(type(topology.get(field)) is bool)
    require(topology["is_inside_worktree"] and not topology["is_inside_git_dir"]
            and not topology["is_bare"])
    head, branch = value["head"], value["branch"]
    if topology["is_unborn"]:
        require(head is None or head == "<unborn>")
        require(not topology["is_detached"] and not _has_forbidden_path_text(branch))
    else:
        require(isinstance(head, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head) is not None)
        if topology["is_detached"]:
            require(branch is None)
        else:
            require(not _has_forbidden_path_text(branch))


def classify_path_input(value: str) -> str:
    """Classify path syntax without consulting the filesystem."""
    if _has_forbidden_path_text(value):
        return "UNSUPPORTED_OR_RELATIVE"
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        return "UNSUPPORTED_OR_RELATIVE"
    if WSL_UNC_LOCALHOST.fullmatch(value):
        return "WSL_UNC_LOCALHOST"
    if WSL_UNC_DOLLAR.fullmatch(value):
        return "WSL_UNC_DOLLAR"
    if value.startswith(("\\\\", "//")):
        return "UNSUPPORTED_OR_RELATIVE"
    if WINDOWS_DRIVE_ABSOLUTE.match(value):
        return "WINDOWS_DRIVE_ABSOLUTE"
    if value.startswith("/"):
        return "WSL_POSIX_ABSOLUTE"
    return "UNSUPPORTED_OR_RELATIVE"


def make_wslpath_converter(
    executable: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Callable[[str], str]:
    """Build an argv-only Windows-to-WSL path converter."""
    if not executable or _has_forbidden_path_text(executable):
        raise PathResolutionError("PATH_CONVERTER_UNAVAILABLE")

    def convert(value: str) -> str:
        try:
            result = runner(
                [executable, "-u", "--", value],
                check=False,
                capture_output=True,
                shell=False,
            )
        except OSError as exc:
            raise PathResolutionError("PATH_CONVERTER_UNAVAILABLE") from exc
        if result.returncode != 0:
            raise PathResolutionError("PATH_CONVERSION_FAILED")
        try:
            output = result.stdout.decode("utf-8", "strict")
        except (AttributeError, UnicodeDecodeError) as exc:
            raise PathResolutionError("PATH_CONVERSION_FAILED") from exc
        lines = output.splitlines()
        if len(lines) != 1 or classify_path_input(lines[0]) != "WSL_POSIX_ABSOLUTE":
            raise PathResolutionError("PATH_CONVERSION_FAILED")
        return lines[0]

    return convert


def _empty_alias_record(requested_path: str, kind: str) -> dict[str, object]:
    return {
        "input": requested_path,
        "kind": kind,
        "resolution_traits": [],
        "converted_path": None,
        "logical_path": None,
        "physical_path": None,
        "filesystem_identity": None,
        "relation_to_workspace": "UNKNOWN",
        "reason": None,
    }


def _filesystem_identity(path: str) -> dict[str, object]:
    metadata = os.stat(path, follow_symlinks=True)
    if not stat.S_ISDIR(metadata.st_mode):
        raise PathResolutionError("PATH_NOT_DIRECTORY")
    return {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "object_type": "directory",
    }


def _same_filesystem_object(left: dict[str, object], right: dict[str, object]) -> bool:
    return (left["st_dev"], left["st_ino"]) == (right["st_dev"], right["st_ino"])


def _resolve_native_workspace(
    requested_path: str,
    *,
    native_resolver: Callable[[str], str],
) -> tuple[str, dict[str, object]]:
    kind = classify_path_input(requested_path)
    if kind not in {"WINDOWS_DRIVE_ABSOLUTE", "WSL_UNC_LOCALHOST", "WSL_UNC_DOLLAR"}:
        raise PathResolutionError("CROSS_SURFACE_IDENTITY_UNPROVEN")
    logical_path = native_resolver(requested_path)
    if _has_forbidden_path_text(logical_path):
        raise PathResolutionError("PATH_NATIVE_RESOLUTION_FAILED")
    physical_path = os.path.realpath(logical_path)
    return physical_path, _filesystem_identity(physical_path)


def resolve_path_alias(
    requested_path: str,
    *,
    runtime_surface: dict[str, object],
    workspace_pwd: str,
    converter: Callable[[str], str] | None = None,
    native_resolver: Callable[[str], str] | None = None,
) -> dict[str, object]:
    """Resolve one path representation without guessing across surfaces."""
    kind = classify_path_input(requested_path)
    record = _empty_alias_record(requested_path, kind)
    if kind == "UNSUPPORTED_OR_RELATIVE":
        record["reason"] = "PATH_UNSUPPORTED"
        return record

    platform = str(runtime_surface.get("platform", "")).lower()
    is_wsl = runtime_surface.get("is_wsl") is True
    is_windows = platform in {"windows", "win32", "nt"}
    logical_path: str | None = None

    try:
        if kind == "WSL_POSIX_ABSOLUTE":
            if is_windows:
                record["reason"] = "CROSS_SURFACE_IDENTITY_UNPROVEN"
                return record
            logical_path = requested_path
        elif kind == "WINDOWS_DRIVE_ABSOLUTE":
            if is_wsl:
                if converter is None:
                    raise PathResolutionError("PATH_CONVERTER_UNAVAILABLE")
                logical_path = converter(requested_path)
                if classify_path_input(logical_path) != "WSL_POSIX_ABSOLUTE":
                    raise PathResolutionError("PATH_CONVERSION_FAILED")
                record["converted_path"] = logical_path
                record["resolution_traits"] = ["CONVERTED"]
            elif is_windows:
                if native_resolver is None:
                    raise PathResolutionError("PATH_NATIVE_RESOLVER_UNAVAILABLE")
                logical_path = native_resolver(requested_path)
                if _has_forbidden_path_text(logical_path):
                    raise PathResolutionError("PATH_NATIVE_RESOLUTION_FAILED")
            else:
                raise PathResolutionError("PATH_SURFACE_UNSUPPORTED")
        else:
            if is_windows:
                if native_resolver is None:
                    raise PathResolutionError("PATH_NATIVE_RESOLVER_UNAVAILABLE")
                logical_path = native_resolver(requested_path)
                if _has_forbidden_path_text(logical_path):
                    raise PathResolutionError("PATH_NATIVE_RESOLUTION_FAILED")
            elif is_wsl:
                parts = requested_path.split("\\")
                requested_distro = parts[3] if len(parts) > 3 else ""
                current_distro = runtime_surface.get("wsl_distro_name")
                if _has_forbidden_path_text(current_distro):
                    raise PathResolutionError("DISTRO_IDENTITY_UNKNOWN")
                same_distro = requested_distro == current_distro
                if requested_distro.isascii() and str(current_distro).isascii():
                    same_distro = same_distro or (
                        requested_distro.casefold() == str(current_distro).casefold()
                    )
                if not same_distro:
                    raise PathResolutionError("DISTRO_MISMATCH")
                logical_path = "/" + "/".join(parts[4:])
                record["converted_path"] = logical_path
                record["resolution_traits"] = ["CONVERTED"]
            else:
                raise PathResolutionError("PATH_SURFACE_UNSUPPORTED")
    except (OSError, PathResolutionError) as exc:
        record["reason"] = (
            exc.reason if isinstance(exc, PathResolutionError) else "PATH_RESOLUTION_FAILED"
        )
        return record

    record["logical_path"] = logical_path
    if is_windows:
        if native_resolver is None:
            record["reason"] = "PATH_NATIVE_RESOLVER_UNAVAILABLE"
            return record
        workspace_kind = classify_path_input(workspace_pwd)
        if workspace_kind == "WSL_POSIX_ABSOLUTE":
            record["reason"] = "CROSS_SURFACE_IDENTITY_UNPROVEN"
            return record
        try:
            physical_path, target_identity = _resolve_native_workspace(
                requested_path,
                native_resolver=native_resolver,
            )
            _, workspace_identity = _resolve_native_workspace(
                workspace_pwd,
                native_resolver=native_resolver,
            )
        except (OSError, PathResolutionError) as exc:
            record["reason"] = (
                exc.reason if isinstance(exc, PathResolutionError) else "PATH_RESOLUTION_FAILED"
            )
            return record
        traits = set(record["resolution_traits"])
        if os.path.abspath(logical_path) != physical_path:
            traits.add("SYMLINK_ALIAS")
        record.update(
            resolution_traits=sorted(traits),
            physical_path=physical_path,
            filesystem_identity=target_identity,
            relation_to_workspace=(
                "SAME_OBJECT"
                if _same_filesystem_object(target_identity, workspace_identity)
                else "DIFFERENT_OBJECT"
            ),
            reason=None,
        )
        return record

    try:
        if not os.path.isdir(logical_path):
            raise PathResolutionError("PATH_NOT_DIRECTORY")
        physical_path = os.path.realpath(logical_path)
        target_identity = _filesystem_identity(physical_path)
        workspace_identity = _filesystem_identity(os.path.realpath(workspace_pwd))
    except (OSError, PathResolutionError) as exc:
        record["reason"] = (
            exc.reason if isinstance(exc, PathResolutionError) else "PATH_RESOLUTION_FAILED"
        )
        return record

    traits = set(record["resolution_traits"])
    if os.path.abspath(logical_path) != physical_path:
        traits.add("SYMLINK_ALIAS")
    record.update(
        resolution_traits=sorted(traits),
        physical_path=physical_path,
        filesystem_identity=target_identity,
        relation_to_workspace=(
            "SAME_OBJECT"
            if _same_filesystem_object(target_identity, workspace_identity)
            else "DIFFERENT_OBJECT"
        ),
    )
    return record


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def detect_runtime_surface() -> dict[str, object]:
    release = ""
    try:
        release = os.uname().release.lower()
    except AttributeError:
        pass
    is_wsl = sys.platform.startswith("linux") and (
        "microsoft" in release or bool(os.environ.get("WSL_INTEROP"))
    )
    distro = os.environ.get("WSL_DISTRO_NAME") if is_wsl else None
    if distro is not None and _has_forbidden_path_text(distro):
        distro = None
    platform = "windows" if os.name == "nt" else sys.platform
    return {
        "platform": platform,
        "is_wsl": is_wsl,
        "wsl_distro_name": distro,
    }


def _git_payload(
    *,
    is_inside_worktree: bool,
    is_inside_git_dir: bool,
    is_bare: bool,
    branch: str | None,
    head: str | None,
    has_remotes: bool,
) -> dict[str, object]:
    applicable = is_bare or is_inside_worktree
    return {
        "is_inside_worktree": is_inside_worktree,
        "is_inside_git_dir": is_inside_git_dir,
        "is_bare": is_bare,
        "is_detached": bool(is_inside_worktree and head and branch is None),
        "is_unborn": bool(applicable and head is None),
        "remote_authority": "UNDETERMINED" if has_remotes else "NOT_APPLICABLE",
        "fork_relation": "UNDETERMINED" if has_remotes else "NOT_APPLICABLE",
        "fork_authority_source": None,
    }


def base_payload(requested_cwd: str) -> dict[str, object]:
    captured_at = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "incomplete",
        "reason": None,
        "binding_kind": "NONE",
        "write_eligibility": "UNKNOWN",
        "requested_cwd": requested_cwd,
        "pwd": os.path.abspath(requested_cwd),
        "realpath": None,
        "path_input_kind": classify_path_input(requested_cwd),
        "resolution_traits": [],
        "logical_path": None,
        "physical_path": None,
        "filesystem_identity": None,
        "aliases": [],
        "runtime_surface": detect_runtime_surface(),
        "is_git": False,
        "git_top_level": None,
        "git_dir": None,
        "git_common_dir": None,
        "worktree_id": None,
        "branch": None,
        "head": None,
        "dirty": False,
        "dirty_fingerprint": None,
        "fingerprint_complete": True,
        "dirty_fingerprint_schema": DIRTY_FINGERPRINT_SCHEMA,
        "fingerprint_applicability": "REQUIRED",
        "fingerprint_reason": None,
        "remotes": {},
        "git": _git_payload(
            is_inside_worktree=False,
            is_inside_git_dir=False,
            is_bare=False,
            branch=None,
            head=None,
            has_remotes=False,
        ),
        "bound_at_utc": captured_at,
        "captured_at_utc": captured_at,
    }


def emit(payload: dict[str, object], exit_code: int) -> int:
    json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return exit_code


@dataclass
class DirectoryAnchor:
    path: str
    descriptor: int
    parent_descriptor: int
    name: bytes
    identity: tuple[int, int, int]


def _open_directory_anchor(path: str) -> DirectoryAnchor:
    if not _descriptor_anchoring_available():
        raise PathResolutionError("untracked_directory_anchor_unavailable")
    physical_path = os.path.realpath(path)
    parent, name = os.path.split(os.fsencode(physical_path))
    if not parent or not name:
        raise PathResolutionError("git_descriptor_anchor_unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
        | os.O_DIRECTORY
    )
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        parent_descriptor = os.open(parent, flags)
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        identity = _directory_identity(os.fstat(descriptor))
        return DirectoryAnchor(
            path=physical_path,
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            name=name,
            identity=identity,
        )
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise PathResolutionError("git_descriptor_anchor_unavailable") from exc


def _close_directory_anchor(anchor: DirectoryAnchor | None) -> None:
    if anchor is None:
        return
    os.close(anchor.descriptor)
    os.close(anchor.parent_descriptor)


def _directory_anchor_object_is_current(anchor: DirectoryAnchor) -> bool:
    try:
        anchored_entry = os.stat(
            anchor.name,
            dir_fd=anchor.parent_descriptor,
            follow_symlinks=False,
        )
        current_path = os.stat(anchor.path, follow_symlinks=False)
        opened = os.fstat(anchor.descriptor)
    except OSError:
        return False
    return (
        stat.S_ISDIR(anchored_entry.st_mode)
        and stat.S_ISDIR(current_path.st_mode)
        and _directory_identity(anchored_entry) == anchor.identity
        and _directory_identity(current_path) == anchor.identity
        and _directory_identity(opened) == anchor.identity
    )


def _directory_anchor_contains(
    root: DirectoryAnchor,
    requested: DirectoryAnchor,
) -> bool:
    try:
        relative = os.path.relpath(requested.path, root.path)
    except ValueError:
        return False
    components = tuple(part for part in relative.split(os.sep) if part not in {"", "."})
    if any(part == ".." for part in components):
        return False
    if not components:
        return root.identity == requested.identity
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
        | os.O_DIRECTORY
    )
    parent_fd = root.descriptor
    opened: list[int] = []
    try:
        for component in components:
            child_fd = os.open(os.fsencode(component), flags, dir_fd=parent_fd)
            opened.append(child_fd)
            parent_fd = child_fd
        return _directory_identity(os.fstat(parent_fd)) == requested.identity
    except OSError:
        return False
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _directory_anchor_is_stable(anchor: DirectoryAnchor) -> bool:
    return _directory_anchor_object_is_current(anchor)


def _assert_directory_anchor(anchor: DirectoryAnchor, reason: str) -> None:
    if not _directory_anchor_is_stable(anchor):
        raise PathResolutionError(reason)


def git(
    repo: str,
    *args: str,
    check: bool = True,
    anchor: DirectoryAnchor | None = None,
    worktree_anchor: DirectoryAnchor | None = None,
) -> subprocess.CompletedProcess[bytes]:
    # Inherited Git overrides can redirect the repository, inject configuration,
    # write trace files or start helpers. Identity is scoped to the explicit cwd.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_NO_LAZY_FETCH": "1", "LC_ALL": "C"})
    command_repo = repo
    run_options: dict[str, object] = {}
    pass_fds: set[int] = set()
    if anchor is not None:
        _assert_directory_anchor(anchor, "project_root_replaced_during_git_command")
        command_repo = f"/proc/self/fd/{anchor.descriptor}"
        if not os.path.isdir("/proc/self/fd"):
            raise PathResolutionError("git_descriptor_execution_unavailable")
        pass_fds.add(anchor.descriptor)
    if worktree_anchor is not None:
        _assert_directory_anchor(
            worktree_anchor,
            "project_root_replaced_during_git_command",
        )
        worktree_repo = f"/proc/self/fd/{worktree_anchor.descriptor}"
        if not os.path.isdir("/proc/self/fd"):
            raise PathResolutionError("git_descriptor_execution_unavailable")
        pass_fds.add(worktree_anchor.descriptor)
        run_options["work_tree"] = worktree_repo
    if pass_fds:
        run_options["pass_fds"] = tuple(sorted(pass_fds))
    # Use only plumbing that does not convert worktree contents. In particular,
    # --no-ext-diff/--no-textconv do NOT prevent status/diff from running clean
    # filters. --no-lazy-fetch fails closed on Git versions lacking this guard.
    if not args or args[0] not in {
        "rev-parse", "symbolic-ref", "show-ref", "remote", "ls-files", "ls-tree"
    }:
        raise IdentityReadError("git command outside identity read set")
    argv = ["git", "--no-pager", "--no-lazy-fetch",
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.untrackedCache=false", "-c", "submodule.recurse=false"]
    if worktree_anchor is not None:
        argv.append(f"--work-tree={run_options.pop('work_tree')}")
    argv.extend(["-C", command_repo])
    argv.extend(args)
    result = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        env=env,
        **run_options,
    )
    if anchor is not None:
        _assert_directory_anchor(anchor, "project_root_replaced_during_git_command")
    if worktree_anchor is not None:
        _assert_directory_anchor(
            worktree_anchor,
            "project_root_replaced_during_git_command",
        )
    if check and result.returncode != 0:
        raise IdentityReadError(f"git command failed: {args[0]}")
    return result


def git_text(
    repo: str,
    *args: str,
    check: bool = True,
    anchor: DirectoryAnchor | None = None,
) -> str:
    result = git(repo, *args, check=check, anchor=anchor)
    return result.stdout.decode("utf-8", "surrogateescape").strip()


def _head_state(repo: str, *, anchor: DirectoryAnchor) -> tuple[str | None, str | None]:
    symbolic = git(repo, "symbolic-ref", "--short", "-q", "HEAD",
                   check=False, anchor=anchor)
    branch = symbolic.stdout.decode("utf-8", "surrogateescape").strip() or None
    if symbolic.stderr or not (
        symbolic.returncode == 0 and not _has_forbidden_path_text(branch)
        or symbolic.returncode == 1 and not symbolic.stdout
    ):
        raise IdentityReadError("unable to determine symbolic HEAD")

    resolved = git(repo, "rev-parse", "--verify", "--quiet", "HEAD",
                   check=False, anchor=anchor)
    if resolved.returncode == 0 and not resolved.stderr:
        head = resolved.stdout.decode("utf-8", "surrogateescape").strip()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head):
            return branch, head
    elif resolved.returncode == 1 and not resolved.stdout and not resolved.stderr and branch:
        # An unresolved HEAD alone does not prove unborn: its branch ref must
        # be positively classified as missing, rather than unreadable/broken.
        reference = git_text(repo, "symbolic-ref", "-q", "HEAD", anchor=anchor)
        if reference.startswith("refs/") and not _has_forbidden_path_text(reference):
            exists = git(repo, "show-ref", "--exists", reference,
                         check=False, anchor=anchor)
            if exists.returncode == 2 and not exists.stdout:
                return branch, None
    raise IdentityReadError("unable to resolve HEAD or prove unborn branch")


def sanitize_remote(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    if "://" not in value:
        match = SCP_REMOTE.match(value)
        if match:
            host, path = match.groups()
            path = path.split("?", 1)[0].split("#", 1)[0]
            return f"{host}:{path}"
        return value.split("?", 1)[0].split("#", 1)[0]

    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parts.port}" if parts.port is not None else ""
        return urlunsplit((parts.scheme, f"{hostname}{port}", parts.path, "", ""))
    except (TypeError, ValueError):
        scheme = value.split(":", 1)[0]
        return f"{scheme}://[invalid-remote-url]"


def collect_remotes(
    repo: str,
    *,
    anchor: DirectoryAnchor | None = None,
) -> dict[str, object]:
    """Record names only; null values preserve the map shape without reading URLs."""
    names = [name for name in git_text(repo, "remote", anchor=anchor).splitlines() if name]
    if any(_has_forbidden_path_text(name) for name in names):
        raise IdentityReadError("invalid remote name")
    return {name: None for name in sorted(set(names))}


def untracked_paths(status_bytes: bytes) -> list[bytes]:
    return sorted(
        record[2:]
        for record in status_bytes.split(b"\0")
        if record.startswith(b"? ")
    )


def _update_digest_field(
    digest: "hashlib._Hash",
    label: bytes,
    value: bytes,
) -> None:
    digest.update(label)
    digest.update(b"\0")
    digest.update(str(len(value)).encode("ascii"))
    digest.update(b"\0")
    digest.update(value)
    digest.update(b"\0")


def _open_stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _path_components(relative: bytes) -> tuple[bytes, ...]:
    components = tuple(relative.split(b"/"))
    if not components or any(component in {b"", b".", b".."} for component in components):
        raise PathResolutionError("untracked_path_outside_project")
    return components


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _descriptor_anchoring_available() -> bool:
    supports_dir_fd = {
        getattr(function, "__name__", None)
        for function in getattr(os, "supports_dir_fd", set())
    }
    supports_follow_symlinks = {
        getattr(function, "__name__", None)
        for function in getattr(os, "supports_follow_symlinks", set())
    }
    return (
        getattr(os, "O_NOFOLLOW", None) is not None
        and getattr(os, "O_DIRECTORY", None) is not None
        and "open" in supports_dir_fd
        and "stat" in supports_dir_fd
        and "stat" in supports_follow_symlinks
    )


def _open_parent_chain(
    root_fd: int,
    components: tuple[bytes, ...],
) -> tuple[list[int], list[tuple[int, int, int]]]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise PathResolutionError("untracked_directory_anchor_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
    parent_fd = root_fd
    opened_parents: list[int] = []
    try:
        for component in components[:-1]:
            child_fd = os.open(
                component,
                flags | directory_flag,
                dir_fd=parent_fd,
            )
            opened_parents.append(child_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                raise PathResolutionError("untracked_parent_replaced_during_binding")
            parent_fd = child_fd
        return opened_parents, [
            _directory_identity(os.fstat(descriptor)) for descriptor in opened_parents
        ]
    except PathResolutionError:
        for descriptor in reversed(opened_parents):
            os.close(descriptor)
        raise
    except OSError as exc:
        for descriptor in reversed(opened_parents):
            os.close(descriptor)
        raise PathResolutionError("untracked_parent_replaced_during_binding") from exc


def _close_untracked_anchors(
    anchors: dict[
        bytes,
        tuple[
            tuple[bytes, ...],
            list[int],
            list[tuple[int, int, int]],
            os.stat_result,
        ],
    ],
) -> None:
    for _components, parent_fds, _parent_identities, _file_metadata in anchors.values():
        for descriptor in reversed(parent_fds):
            os.close(descriptor)


def _anchor_untracked_paths(
    root_fd: int,
    paths: list[bytes],
) -> dict[
    bytes,
    tuple[
        tuple[bytes, ...],
        list[int],
        list[tuple[int, int, int]],
        os.stat_result,
    ],
]:
    anchors = {}
    try:
        for relative in paths:
            components = _path_components(relative)
            parent_fds, parent_identities = _open_parent_chain(root_fd, components)
            parent_fd = parent_fds[-1] if parent_fds else root_fd
            try:
                metadata = os.stat(components[-1], dir_fd=parent_fd, follow_symlinks=False)
            except OSError as exc:
                for descriptor in reversed(parent_fds):
                    os.close(descriptor)
                raise PathResolutionError("untracked_file_replaced_during_binding") from exc
            if not stat.S_ISREG(metadata.st_mode):
                for descriptor in reversed(parent_fds):
                    os.close(descriptor)
                raise PathResolutionError("untracked_file_type_unsupported")
            anchors[relative] = (
                components,
                parent_fds,
                parent_identities,
                metadata,
            )
        return anchors
    except (OSError, PathResolutionError):
        _close_untracked_anchors(anchors)
        raise


def _verify_untracked_path(
    root_fd: int,
    components: tuple[bytes, ...],
    expected_parent_identities: list[tuple[int, int, int]],
    expected_file_metadata: os.stat_result,
) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise PathResolutionError("untracked_directory_anchor_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | directory_flag
    parent_fd = root_fd
    current_parents: list[int] = []
    try:
        for index, component in enumerate(components[:-1]):
            try:
                current_fd = os.open(component, flags, dir_fd=parent_fd)
            except OSError as exc:
                raise PathResolutionError("untracked_parent_replaced_during_binding") from exc
            current_parents.append(current_fd)
            try:
                current_identity = _directory_identity(os.fstat(current_fd))
            except OSError as exc:
                raise PathResolutionError("untracked_parent_replaced_during_binding") from exc
            if current_identity != expected_parent_identities[index]:
                raise PathResolutionError("untracked_parent_replaced_during_binding")
            parent_fd = current_fd
        try:
            metadata = os.stat(components[-1], dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise PathResolutionError("untracked_file_replaced_during_binding") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise PathResolutionError("untracked_file_type_unsupported")
        if _file_identity(metadata) != _file_identity(expected_file_metadata):
            raise PathResolutionError("untracked_file_replaced_during_binding")
    finally:
        for descriptor in reversed(current_parents):
            os.close(descriptor)


def _open_project_root(repo: str) -> int:
    if not _descriptor_anchoring_available():
        raise PathResolutionError("untracked_directory_anchor_unavailable")
    nofollow = os.O_NOFOLLOW
    directory_flag = os.O_DIRECTORY
    try:
        return os.open(
            os.fsencode(repo),
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | directory_flag,
        )
    except OSError as exc:
        raise PathResolutionError("untracked_parent_replaced_during_binding") from exc


def _project_root_is_stable(repo: str, root_fd: int) -> bool:
    try:
        path_metadata = os.stat(repo, follow_symlinks=False)
        opened_metadata = os.fstat(root_fd)
    except OSError:
        return False
    return (
        stat.S_ISDIR(path_metadata.st_mode)
        and (path_metadata.st_dev, path_metadata.st_ino)
        == (opened_metadata.st_dev, opened_metadata.st_ino)
    )


def _project_root_matches_identity(
    root_fd: int,
    expected_identity: dict[str, object],
) -> bool:
    try:
        opened_metadata = os.fstat(root_fd)
    except OSError:
        return False
    return (
        expected_identity.get("object_type") == "directory"
        and stat.S_ISDIR(opened_metadata.st_mode)
        and (opened_metadata.st_dev, opened_metadata.st_ino)
        == (expected_identity.get("st_dev"), expected_identity.get("st_ino"))
    )


def hash_untracked(
    digest: "hashlib._Hash",
    repo: str,
    paths: list[bytes],
    max_bytes: int,
    *,
    root_fd: int | None = None,
    anchors: dict[
        bytes,
        tuple[
            tuple[bytes, ...],
            list[int],
            list[tuple[int, int, int]],
            os.stat_result,
        ],
    ]
    | None = None,
) -> tuple[bool, str | None]:
    total = 0
    owned_root_fd = root_fd is None
    owned_anchors = anchors is None
    try:
        if root_fd is None:
            root_fd = _open_project_root(repo)
        if not _project_root_is_stable(repo, root_fd):
            return False, "untracked_parent_replaced_during_binding"
        if anchors is None:
            anchors = _anchor_untracked_paths(root_fd, paths)
        if set(anchors) != set(paths):
            return False, "untracked_paths_changed_during_binding"

        for relative in paths:
            if relative.startswith(b"/") or b".." in relative.split(b"/"):
                return False, "untracked_path_outside_project"

            descriptor: int | None = None
            try:
                components, parent_fds, parent_identities, metadata = anchors[relative]
                _verify_untracked_path(
                    root_fd,
                    components,
                    parent_identities,
                    metadata,
                )
                if total + metadata.st_size > max_bytes:
                    return False, "untracked_hash_limit_exceeded"
                nofollow = getattr(os, "O_NOFOLLOW", None)
                if nofollow is None:
                    return False, "untracked_directory_anchor_unavailable"
                parent_fd = parent_fds[-1] if parent_fds else root_fd
                descriptor = os.open(
                    components[-1],
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow,
                    dir_fd=parent_fd,
                )
                opened = os.fstat(descriptor)
                if _open_stat_identity(metadata) != _open_stat_identity(opened):
                    return False, "untracked_file_replaced_during_binding"

                _update_digest_field(digest, b"untracked-path", relative)
                _update_digest_field(digest, b"untracked-type", b"regular")
                _update_digest_field(
                    digest,
                    b"untracked-size",
                    str(opened.st_size).encode("ascii"),
                )
                content_digest = hashlib.sha256()
                while chunk := os.read(descriptor, 1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        return False, "untracked_hash_limit_exceeded"
                    content_digest.update(chunk)
                _update_digest_field(
                    digest,
                    b"untracked-content-sha256",
                    content_digest.digest(),
                )

                final = os.fstat(descriptor)
                if _open_stat_identity(opened) != _open_stat_identity(final):
                    if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (
                        final.st_dev,
                        final.st_ino,
                        stat.S_IFMT(final.st_mode),
                    ):
                        return False, "untracked_file_replaced_during_binding"
                    return False, "untracked_file_changed_during_binding"
                _verify_untracked_path(
                    root_fd,
                    components,
                    parent_identities,
                    metadata,
                )
            except PathResolutionError as exc:
                return False, exc.reason
            except (FileNotFoundError, OSError):
                return False, "untracked_file_changed_during_binding"
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        if not _project_root_is_stable(repo, root_fd):
            return False, "untracked_parent_replaced_during_binding"
        return True, None
    except PathResolutionError as exc:
        return False, exc.reason
    finally:
        if owned_anchors and anchors is not None:
            _close_untracked_anchors(anchors)
        if owned_root_fd and root_fd is not None:
            os.close(root_fd)


def _index_entries(data: bytes) -> list[tuple[bytes, bytes, bytes, bytes]]:
    entries = []
    for record in data.split(b"\0"):
        if not record:
            continue
        try:
            header, path = record.split(b"\t", 1)
            mode, oid, stage = header.split(b" ")
        except ValueError as exc:
            raise IdentityReadError("invalid index entry") from exc
        _path_components(path)
        if (mode not in {b"100644", b"100755", b"120000", b"160000"}
                or stage not in {b"0", b"1", b"2", b"3"}
                or re.fullmatch(b"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None):
            raise IdentityReadError("unsupported index entry")
        entries.append((path, mode, oid, stage))
    return sorted(entries)


def _head_entries(data: bytes) -> list[tuple[bytes, bytes, bytes, bytes]]:
    entries = []
    for record in data.split(b"\0"):
        if not record:
            continue
        try:
            header, path = record.split(b"\t", 1)
            mode, kind, oid = header.split(b" ")
        except ValueError as exc:
            raise IdentityReadError("invalid tree entry") from exc
        if kind not in {b"blob", b"commit"}:
            raise IdentityReadError("unsupported tree entry")
        # Reuse the index parser's path, mode and object-id validation.
        entries.extend(_index_entries(mode + b" " + oid + b" 0\t" + path + b"\0"))
    return sorted(entries)


def _hash_tracked_path(
    digest: "hashlib._Hash", root_fd: int, path: bytes, mode: bytes, oid: bytes,
) -> bool:
    """Hash raw bytes without Git attributes/filters; return whether index differs."""
    components = _path_components(path)
    parent_fd = root_fd
    parents: list[int] = []
    parent_identities: list[tuple[int, int, int]] = []
    descriptor: int | None = None
    _update_digest_field(digest, b"tracked-path", path)
    try:
        try:
            for component in components[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                                | os.O_DIRECTORY, dir_fd=parent_fd)
                parents.append(child)
                parent_identities.append(_directory_identity(os.fstat(child)))
                parent_fd = child
            metadata = os.stat(components[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            _update_digest_field(digest, b"tracked-state", b"missing")
            return True
        except OSError as exc:
            raise PathResolutionError("tracked_parent_replaced_during_binding") from exc
        if mode == b"160000":
            # A gitlink OID does not attest the nested worktree's content. Never
            # recurse through submodule commands or silently call it complete.
            raise PathResolutionError("tracked_submodule_not_fingerprinted")
        if stat.S_ISLNK(metadata.st_mode):
            content = os.readlink(components[-1], dir_fd=parent_fd)
            raw = os.fsencode(content)
            actual_mode = b"120000"
            blob = hashlib.new("sha1" if len(oid) == 40 else "sha256")
            blob.update(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw)
            content_hash = hashlib.sha256(raw).digest()
            final = os.stat(components[-1], dir_fd=parent_fd, follow_symlinks=False)
        elif stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(components[-1], os.O_RDONLY | os.O_CLOEXEC
                                 | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if _open_stat_identity(opened) != _open_stat_identity(metadata):
                raise PathResolutionError("tracked_file_replaced_during_binding")
            actual_mode = b"100755" if opened.st_mode & 0o111 else b"100644"
            blob = hashlib.new("sha1" if len(oid) == 40 else "sha256")
            blob.update(b"blob " + str(opened.st_size).encode("ascii") + b"\0")
            raw_hash = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                blob.update(chunk)
                raw_hash.update(chunk)
            content_hash = raw_hash.digest()
            final = os.fstat(descriptor)
        else:
            raise PathResolutionError("tracked_file_type_unsupported")
        if (_open_stat_identity(metadata) != _open_stat_identity(final)
                or metadata.st_ctime_ns != final.st_ctime_ns):
            raise PathResolutionError("tracked_file_changed_during_binding")
        # Reopen the original parent chain without following symlinks. No content
        # from a replacement parent is read to perform this final identity check.
        current_parents, current_identities = _open_parent_chain(root_fd, components)
        try:
            if current_identities != parent_identities:
                raise PathResolutionError("tracked_parent_replaced_during_binding")
            current = os.stat(components[-1],
                              dir_fd=current_parents[-1] if current_parents else root_fd,
                              follow_symlinks=False)
            if (_open_stat_identity(current) != _open_stat_identity(final)
                    or current.st_ctime_ns != final.st_ctime_ns):
                raise PathResolutionError("tracked_file_replaced_during_binding")
        finally:
            for fd in reversed(current_parents):
                os.close(fd)
        _update_digest_field(digest, b"tracked-mode", actual_mode)
        _update_digest_field(digest, b"tracked-content-sha256", content_hash)
        return actual_mode != mode or blob.hexdigest().encode("ascii") != oid
    except OSError as exc:
        raise PathResolutionError("tracked_file_changed_during_binding") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for fd in reversed(parents):
            os.close(fd)


def dirty_state(
    repo: str,
    head: str | None,
    max_untracked_bytes: int,
    *,
    expected_root_identity: dict[str, object],
    git_anchor: DirectoryAnchor | None = None,
) -> tuple[bool, str, bool, str | None]:
    digest = hashlib.sha256()
    _update_digest_field(digest, b"schema", b"dirty-fingerprint-v3-raw")
    root_fd: int | None = None
    anchors = {}
    dirty = False

    def read_git(*args: str) -> bytes:
        return git(repo, *args, anchor=git_anchor, worktree_anchor=git_anchor).stdout

    try:
        root_fd = _open_project_root(repo)
        if not _project_root_matches_identity(root_fd, expected_root_identity):
            return False, digest.hexdigest(), False, "project_root_replaced_during_binding"
        listed = read_git("ls-files", "--others", "--exclude-standard", "-z")
        listed_paths = sorted(path for path in listed.split(b"\0") if path)
        anchors = _anchor_untracked_paths(root_fd, listed_paths)
        index = read_git("ls-files", "--stage", "-z")
        entries = _index_entries(index)
        tree = read_git("ls-tree", "-rz", "--full-tree", head) if head else b""
        dirty = bool(listed_paths) or entries != _head_entries(tree)
        _update_digest_field(digest, b"head", (head or "<unborn>").encode("ascii"))
        _update_digest_field(digest, b"index-entries", index)
        _update_digest_field(digest, b"head-tree", tree)
        seen = set()
        for path, mode, oid, _stage in entries:
            if path in seen:
                continue
            seen.add(path)
            dirty = _hash_tracked_path(digest, root_fd, path, mode, oid) or dirty
        current_listed = read_git("ls-files", "--others", "--exclude-standard", "-z")
        current_paths = sorted(path for path in current_listed.split(b"\0") if path)
        if current_paths != listed_paths:
            return dirty, digest.hexdigest(), False, "untracked_paths_changed_during_binding"
        complete, reason = hash_untracked(digest, repo, current_paths, max_untracked_bytes,
                                          root_fd=root_fd, anchors=anchors)
        if complete and index != read_git("ls-files", "--stage", "-z"):
            complete, reason = False, "tracked_index_changed_during_binding"
        return dirty, digest.hexdigest(), complete, reason
    except IdentityReadError:
        return dirty, digest.hexdigest(), False, "git_fingerprint_command_failed"
    except PathResolutionError as exc:
        return dirty, digest.hexdigest(), False, exc.reason
    finally:
        _close_untracked_anchors(anchors)
        if root_fd is not None:
            os.close(root_fd)


def _identity_comparison_value(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"bound_at_utc", "captured_at_utc"}
    }


def _identity_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        _identity_comparison_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_stable_identity(
    cwd: str,
    max_untracked_bytes: int,
    *,
    workspace_pwd: str | None = None,
    aliases: tuple[str, ...] = (),
) -> dict[str, object]:
    """Capture identity exactly twice and reject any observed drift."""
    first = collect_identity(
        cwd,
        max_untracked_bytes,
        workspace_pwd=workspace_pwd,
        aliases=aliases,
    )
    second = collect_identity(
        cwd,
        max_untracked_bytes,
        workspace_pwd=workspace_pwd,
        aliases=aliases,
    )
    first_digest = _identity_digest(first)
    second_digest = _identity_digest(second)
    facts_complete = True
    for capture in (first, second):
        try:
            validate_git_worktree_facts(capture)
        except IdentitySchemaError:
            facts_complete = False
    candidates = all(
        capture.get("status") == "bound"
        and capture.get("binding_kind") == "GIT_WORKTREE"
        and capture.get("write_eligibility") == "READ_ONLY"
        and capture.get("fingerprint_complete") is True
        and capture.get("fingerprint_applicability") == "REQUIRED"
        and capture.get("reason") is None
        for capture in (first, second)
    )
    status = "UNKNOWN"
    reason: str | None = "INCOMPLETE_IDENTITY_CAPTURE"
    identity: dict[str, object] | None = None
    if not candidates or not facts_complete:
        status = "UNKNOWN"
        reason = "INCOMPLETE_IDENTITY_CAPTURE"
    elif first_digest != second_digest:
        status = "UNKNOWN"
        reason = "STATE_CHANGED_DURING_CAPTURE"
    elif workspace_pwd is not None:
        status = "STABLE"
        reason = None
        identity = dict(second, write_eligibility="ELIGIBLE")
    write_eligibility = (
        identity.get("write_eligibility")
        if identity is not None
        and identity.get("write_eligibility")
        in {"ELIGIBLE", "READ_ONLY", "BLOCKED", "UNKNOWN"}
        else "BLOCKED"
    )
    return {
        "schema_version": 1,
        "status": status,
        "reason": reason,
        "write_eligibility": write_eligibility,
        "capture_count": 2,
        "first_identity_sha256": first_digest,
        "second_identity_sha256": second_digest,
        "identity": identity,
    }


def _git_boolean(
    repo: str,
    argument: str,
    *,
    anchor: DirectoryAnchor | None = None,
) -> tuple[bool | None, subprocess.CompletedProcess[bytes]]:
    result = git(repo, "rev-parse", argument, check=False, anchor=anchor)
    value = result.stdout.strip()
    if result.returncode == 0 and value in {b"true", b"false"}:
        return value == b"true", result
    return None, result


def _is_not_git_result(*results: subprocess.CompletedProcess[bytes]) -> bool:
    combined = b"\n".join(result.stderr.lower() for result in results)
    return b"not a git repository" in combined


def _default_path_adapters(
    runtime_surface: dict[str, object],
) -> tuple[Callable[[str], str] | None, Callable[[str], str] | None]:
    converter = (
        make_wslpath_converter("wslpath")
        if runtime_surface.get("is_wsl") is True
        else None
    )
    native_resolver = os.path.abspath if os.name == "nt" else None
    return converter, native_resolver


def _anchor_identity(anchor: DirectoryAnchor) -> dict[str, object]:
    return {
        "st_dev": anchor.identity[0],
        "st_ino": anchor.identity[1],
        "object_type": "directory",
    }


def _collect_git_details(
    payload: dict[str, object],
    realpath: str,
    max_untracked_bytes: int,
) -> dict[str, object]:
    requested_anchor = _open_directory_anchor(realpath)
    root_anchor: DirectoryAnchor | None = None
    try:
        is_bare, bare_result = _git_boolean(
            realpath,
            "--is-bare-repository",
            anchor=requested_anchor,
        )
        is_inside_worktree, worktree_result = _git_boolean(
            realpath,
            "--is-inside-work-tree",
            anchor=requested_anchor,
        )
        is_inside_git_dir, git_dir_result = _git_boolean(
            realpath,
            "--is-inside-git-dir",
            anchor=requested_anchor,
        )
        if is_bare is None and is_inside_worktree is None and is_inside_git_dir is None:
            if not _is_not_git_result(bare_result, worktree_result, git_dir_result):
                raise IdentityReadError("unable to determine git identity")
            payload.update(
                status="bound",
                binding_kind="NON_GIT_DIRECTORY",
                write_eligibility="READ_ONLY",
                is_git=False,
                dirty=False,
                dirty_fingerprint=hashlib.sha256(b"non-git-clean").hexdigest(),
                fingerprint_complete=False,
                fingerprint_applicability="NOT_APPLICABLE",
                fingerprint_reason="LEGACY_NON_GIT_MARKER",
            )
            return payload
        if None in {is_bare, is_inside_worktree, is_inside_git_dir}:
            raise IdentityReadError("incomplete git topology")

        branch, head = _head_state(realpath, anchor=requested_anchor)
        git_dir = os.path.realpath(
            git_text(
                realpath,
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
                anchor=requested_anchor,
            )
        )
        common_dir = os.path.realpath(
            git_text(
                realpath,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                anchor=requested_anchor,
            )
        )
        remotes = collect_remotes(realpath, anchor=requested_anchor)
        nested_git = _git_payload(
            is_inside_worktree=bool(is_inside_worktree),
            is_inside_git_dir=bool(is_inside_git_dir),
            is_bare=bool(is_bare),
            branch=branch,
            head=head,
            has_remotes=bool(remotes),
        )

        if is_bare:
            payload.update(
                status="bound",
                binding_kind="BARE_GIT",
                write_eligibility="READ_ONLY",
                is_git=True,
                git_top_level=None,
                git_dir=git_dir,
                git_common_dir=common_dir,
                branch=branch,
                head=head,
                dirty=False,
                dirty_fingerprint=None,
                fingerprint_complete=False,
                fingerprint_applicability="NOT_APPLICABLE",
                fingerprint_reason="NOT_APPLICABLE_BARE",
                remotes=remotes,
                git=nested_git,
            )
            return payload

        if not is_inside_worktree:
            payload.update(
                status="unsupported",
                reason="GIT_DIRECTORY_NOT_WORKTREE",
                binding_kind="NONE",
                write_eligibility="READ_ONLY",
                is_git=True,
                git_dir=git_dir,
                git_common_dir=common_dir,
                branch=branch,
                head=head,
                remotes=remotes,
                git=nested_git,
            )
            return payload

        top_level = os.path.realpath(
            git_text(
                realpath,
                "rev-parse",
                "--show-toplevel",
                anchor=requested_anchor,
            )
        )
        root_anchor = _open_directory_anchor(top_level)
        if not _directory_anchor_object_is_current(requested_anchor):
            raise PathResolutionError("requested_workspace_replaced_during_binding")
        if not _directory_anchor_contains(root_anchor, requested_anchor):
            raise PathResolutionError("git_worktree_root_mismatch")
        _assert_directory_anchor(
            requested_anchor,
            "requested_workspace_replaced_during_binding",
        )
        dirty, fingerprint, complete, reason = dirty_state(
            top_level,
            head,
            max_untracked_bytes,
            expected_root_identity=_anchor_identity(root_anchor),
            git_anchor=root_anchor,
        )
        requested_workspace_stable = _directory_anchor_is_stable(requested_anchor)
        if not requested_workspace_stable:
            complete = False
            if reason is None:
                reason = "requested_workspace_replaced_during_binding"
        worktree_material = b"worktree-id-v2\0" + "\0".join(
            (top_level, git_dir, common_dir)
        ).encode("utf-8")
        payload.update(
            status="bound" if complete else "incomplete",
            reason=reason,
            binding_kind="GIT_WORKTREE",
            write_eligibility="READ_ONLY" if complete else "BLOCKED",
            is_git=True,
            git_top_level=top_level,
            git_dir=git_dir,
            git_common_dir=common_dir,
            worktree_id=hashlib.sha256(worktree_material).hexdigest()[:16],
            branch=branch,
            head=head,
            dirty=dirty,
            dirty_fingerprint=fingerprint,
            fingerprint_complete=complete,
            fingerprint_applicability="REQUIRED",
            fingerprint_reason=reason,
            remotes=remotes,
            git=nested_git,
        )
        return payload
    finally:
        _close_directory_anchor(root_anchor)
        _close_directory_anchor(requested_anchor)


def collect_identity(
    cwd: str,
    max_untracked_bytes: int,
    *,
    workspace_pwd: str | None = None,
    aliases: tuple[str, ...] = (),
) -> dict[str, object]:
    payload = base_payload(cwd)
    runtime_surface = payload["runtime_surface"]
    converter, native_resolver = _default_path_adapters(runtime_surface)
    bound_workspace = workspace_pwd if workspace_pwd is not None else cwd
    primary = resolve_path_alias(
        cwd,
        runtime_surface=runtime_surface,
        workspace_pwd=bound_workspace,
        converter=converter,
        native_resolver=native_resolver,
    )
    payload.update(
        path_input_kind=primary["kind"],
        resolution_traits=primary["resolution_traits"],
        logical_path=primary["logical_path"],
        physical_path=primary["physical_path"],
        filesystem_identity=primary["filesystem_identity"],
    )
    if workspace_pwd is not None and primary["relation_to_workspace"] in {
        "DIFFERENT_OBJECT",
        "AMBIGUOUS",
    }:
        payload.update(
            status="ambiguous",
            reason="STOP_WRONG_WORKSPACE",
            write_eligibility="BLOCKED",
        )
        return payload
    if workspace_pwd is not None and primary["relation_to_workspace"] == "UNKNOWN":
        payload.update(
            status="incomplete",
            reason=primary["reason"] or "WORKSPACE_IDENTITY_UNKNOWN",
            write_eligibility="BLOCKED",
        )
        return payload
    if primary["physical_path"] is None:
        payload.update(
            status="unsupported",
            reason=primary["reason"],
            write_eligibility="BLOCKED",
        )
        return payload

    realpath = str(primary["physical_path"])
    payload["realpath"] = realpath
    alias_records = [
        resolve_path_alias(
            alias,
            runtime_surface=runtime_surface,
            workspace_pwd=bound_workspace,
            converter=converter,
            native_resolver=native_resolver,
        )
        for alias in sorted(aliases)
    ]
    payload["aliases"] = alias_records
    ambiguous_alias = any(
        alias["relation_to_workspace"] in {"DIFFERENT_OBJECT", "AMBIGUOUS"}
        for alias in alias_records
    )
    unknown_alias = any(
        alias["relation_to_workspace"] == "UNKNOWN"
        for alias in alias_records
    )
    if ambiguous_alias:
        payload.update(
            status="ambiguous",
            reason="AMBIGUOUS_PROJECT_ALIAS",
            write_eligibility="BLOCKED",
        )
        return payload
    if unknown_alias:
        payload.update(
            status="incomplete",
            reason="UNKNOWN_PROJECT_ALIAS",
            write_eligibility="BLOCKED",
        )
        return payload

    try:
        return _collect_git_details(payload, realpath, max_untracked_bytes)
    except IdentityReadError:
        payload.update(
            status="incomplete",
            reason="identity_read_failed",
            write_eligibility="BLOCKED",
            fingerprint_complete=False,
            fingerprint_reason="identity_read_failed",
        )
        return payload
    except PathResolutionError as exc:
        payload.update(
            status="incomplete",
            reason=exc.reason,
            write_eligibility="BLOCKED",
            fingerprint_complete=False,
            fingerprint_reason=exc.reason,
        )
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--expect-realpath")
    parser.add_argument("--workspace-pwd")
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--stable", action="store_true",
                        help="Capture twice; requires --workspace-pwd for an eligible Git identity")
    parser.add_argument(
        "--max-untracked-bytes",
        type=int,
        default=DEFAULT_MAX_UNTRACKED_BYTES,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = base_payload(args.cwd)
    if args.max_untracked_bytes < 0:
        payload["reason"] = "invalid_untracked_hash_limit"
        return emit(payload, 2)
    if not os.path.isdir(args.cwd):
        payload["status"] = "ambiguous"
        payload["reason"] = "cwd_not_directory"
        return emit(payload, 2)

    realpath = os.path.realpath(args.cwd)
    if args.expect_realpath and realpath != os.path.realpath(args.expect_realpath):
        payload["status"] = "ambiguous"
        payload["reason"] = "expected_realpath_mismatch"
        payload["realpath"] = realpath
        return emit(payload, 2)

    try:
        if args.stable:
            capture = capture_stable_identity(
                args.cwd, args.max_untracked_bytes,
                workspace_pwd=args.workspace_pwd, aliases=tuple(args.alias),
            )
            if capture["status"] == "STABLE":
                return emit(capture["identity"], 0)
            return emit(capture, 3)
        payload = collect_identity(
            args.cwd,
            args.max_untracked_bytes,
            workspace_pwd=args.workspace_pwd,
            aliases=tuple(args.alias),
        )
    except IdentityReadError:
        payload["reason"] = "identity_read_failed"
        return emit(payload, 3)

    if payload["status"] == "bound":
        return emit(payload, 0)
    if payload["status"] in {"ambiguous", "unsupported"}:
        return emit(payload, 2)
    return emit(payload, 3)


if __name__ == "__main__":
    raise SystemExit(main())
