#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build a complete Chinese navigation view from Codex skills/list output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
EXIT_DRIFT = 3
EXIT_PROTOCOL = 4
DRIFT_LABELS_ZH = {
    "duplicate": "重名",
    "disabled": "已禁用",
    "unmapped": "未映射",
    "load_error": "加载错误",
    "missing_or_renamed": "缺失或改名",
}
WARNING_CLASSES = (
    "NETWORK_BLOCK_EXPECTED",
    "UPSTREAM_METADATA_IGNORED",
    "SKILL_LOAD_WARNING",
    "STATE_ISOLATION_WARNING",
    "OTHER_QUERY_WARNING",
)
DIFF_IMPACTS = (
    "BLOCKING_SUPERVISOR",
    "BLOCKING_ROUTE",
    "NON_BLOCKING_DRIFT",
    "INFORMATIONAL",
)
SUPERVISOR_SKILL_ID = "vibe-project-lead-zh"


class ProtocolError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "PROTOCOL_ERROR",
        warning_signature: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.warning_signature = warning_signature


@dataclass(frozen=True)
class QueryDeadlines:
    version_seconds: float = 5.0
    initialize_seconds: float = 5.0
    skills_list_seconds: float = 10.0
    backfill_seconds: float = 3.0
    terminate_seconds: float = 2.0


@dataclass(frozen=True)
class CandidateMount:
    skill_id: str
    source_dir: Path
    target_dir: Path

    @property
    def source_locator(self) -> Path:
        return self.source_dir / "SKILL.md"

    @property
    def target_locator(self) -> Path:
        return self.target_dir / "SKILL.md"


@dataclass(frozen=True)
class LocatorSnapshot:
    declared_name: str
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _filesystem_identity(path: Path) -> dict[str, int]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ProtocolError(
            "state root is unavailable",
            code="STATE_ROOT_AMBIGUOUS",
        ) from error
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ProtocolError(
            "state root is not an exact directory",
            code="STATE_ROOT_AMBIGUOUS",
        )
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def capture_query_roots(codex_home: Path, sqlite_home: Path) -> dict[str, object]:
    records: dict[str, object] = {}
    for name, raw_path in (("codex_home", codex_home), ("sqlite_home", sqlite_home)):
        try:
            normalized = raw_path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ProtocolError(
                f"{name} is unavailable",
                code="STATE_ROOT_AMBIGUOUS",
            ) from error
        records[name] = {
            "normalized_path_sha256": hashlib.sha256(
                os.fsencode(str(normalized))
            ).hexdigest(),
            "filesystem_identity": _filesystem_identity(normalized),
        }
    return records


def _single_sqlite_database(source_home: Path) -> Path:
    try:
        candidates = sorted(
            path
            for path in source_home.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and (
                path.name == "state.sqlite"
                or (path.name.startswith("state_") and path.name.endswith(".sqlite"))
            )
        )
    except OSError as error:
        raise ProtocolError(
            "SQLite source root is unreadable",
            code="SQLITE_SNAPSHOT_FAILED",
        ) from error
    if len(candidates) != 1:
        raise ProtocolError(
            "SQLite source database is missing or ambiguous",
            code="SQLITE_SNAPSHOT_FAILED",
        )
    return candidates[0]


def _regular_file_digest(path: Path) -> tuple[str, os.stat_result]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ProtocolError(
            "safe SQLite identity read is unavailable",
            code="SQLITE_SNAPSHOT_FAILED",
        )
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProtocolError(
                "SQLite source is not a regular file",
                code="SQLITE_SNAPSHOT_FAILED",
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ProtocolError(
                "SQLite source changed during identity read",
                code="SQLITE_SNAPSHOT_FAILED",
            )
        return digest.hexdigest(), after
    except OSError as error:
        raise ProtocolError(
            "SQLite source identity read failed",
            code="SQLITE_SNAPSHOT_FAILED",
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def snapshot_sqlite_home(
    source_home: Path,
    destination_root: Path,
) -> dict[str, object]:
    try:
        source_root = source_home.resolve(strict=True)
        destination = destination_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProtocolError(
            "SQLite snapshot root is unavailable",
            code="SQLITE_SNAPSHOT_FAILED",
        ) from error
    if (
        source_home.is_symlink()
        or destination_root.is_symlink()
        or not source_root.is_dir()
        or not destination.is_dir()
        or source_root == destination
        or destination.is_relative_to(source_root)
    ):
        raise ProtocolError(
            "SQLite snapshot roots are unsafe",
            code="SQLITE_SNAPSHOT_FAILED",
        )
    source_database = _single_sqlite_database(source_root)
    source_digest, source_metadata = _regular_file_digest(source_database)
    snapshot_home: Path | None = None
    snapshot_database: Path | None = None
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        snapshot_home = Path(
            tempfile.mkdtemp(prefix="sqlite-snapshot-", dir=destination)
        )
        os.chmod(snapshot_home, 0o700)
        snapshot_database = snapshot_home / source_database.name
        source_uri = f"{source_database.resolve().as_uri()}?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True)
        table_count = source_connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type IN ('table', 'view', 'index', 'trigger')"
        ).fetchone()[0]
        if not isinstance(table_count, int) or table_count == 0:
            raise ProtocolError(
                "SQLite source has no usable schema",
                code="SQLITE_SNAPSHOT_FAILED",
            )
        destination_connection = sqlite3.connect(snapshot_database)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        os.chmod(snapshot_database, 0o600)
        destination_digest, destination_metadata = _regular_file_digest(
            snapshot_database
        )
        if destination_digest != source_digest:
            # A WAL-backed source can have a different byte representation after backup.
            copied = sqlite3.connect(snapshot_database)
            try:
                integrity = copied.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                copied.close()
            if integrity != "ok":
                raise ProtocolError(
                    "SQLite snapshot integrity check failed",
                    code="SQLITE_SNAPSHOT_FAILED",
                )
        return {
            "destination_path": str(snapshot_database),
            "source_filesystem_identity": {
                "device": source_metadata.st_dev,
                "inode": source_metadata.st_ino,
                "size": source_metadata.st_size,
            },
            "destination_filesystem_identity": {
                "device": destination_metadata.st_dev,
                "inode": destination_metadata.st_ino,
                "size": destination_metadata.st_size,
            },
            "source_content_digest": source_digest,
            "state": "SNAPSHOT_READY",
        }
    except ProtocolError:
        if snapshot_home is not None:
            shutil.rmtree(snapshot_home, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as error:
        if snapshot_home is not None:
            shutil.rmtree(snapshot_home, ignore_errors=True)
        raise ProtocolError(
            "SQLite snapshot failed",
            code="SQLITE_SNAPSHOT_FAILED",
        ) from error
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProtocolError(f"refusing symlink output: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_policy(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            policy = tomllib.load(handle)
    except UnicodeError as error:
        raise ProtocolError("policy must be UTF-8") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ProtocolError("unable to read policy") from error
    if policy.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported policy schema")
    if not isinstance(policy.get("skills"), dict):
        raise ProtocolError("policy has no skill map")
    return policy


def read_response(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeError as error:
        raise ProtocolError("skills/list response must be UTF-8") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError("unable to read skills/list response") from error
    if not isinstance(value, dict):
        raise ProtocolError("skills/list response must be an object")
    return value


def normalize_errors(errors: list[Any]) -> list[dict[str, str]]:
    normalized = [
        {
            "path": str(error.get("path", "")) if isinstance(error, dict) else "",
            "message": (
                str(error.get("message", error))
                if isinstance(error, dict)
                else str(error)
            ),
        }
        for error in errors
    ]
    return sorted(normalized, key=lambda item: (item["path"], item["message"]))


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inventory_sha256(normalized: dict[str, Any]) -> str:
    material = {
        "cwd": normalized["cwd"],
        "skills": [
            {
                key: item[key]
                for key in (
                    "name",
                    "description",
                    "path",
                    "scope",
                    "enabled",
                    "source_namespace",
                    "declared_name",
                )
            }
            for item in sorted(
                normalized["skills"],
                key=lambda item: (item["name"], item["scope"], item["path"]),
            )
        ],
        "errors": normalize_errors(normalized["errors"]),
    }
    return _canonical_json_sha256(material)


def _classify_query_warning(value: str) -> str:
    lowered = value.casefold()
    if "network" in lowered and any(
        marker in lowered for marker in ("unavailable", "blocked", "disabled", "isolation")
    ):
        return "NETWORK_BLOCK_EXPECTED"
    if "upstream" in lowered and any(
        marker in lowered for marker in ("metadata", "refresh", "ignored")
    ):
        return "UPSTREAM_METADATA_IGNORED"
    if "skill" in lowered and any(
        marker in lowered for marker in ("load", "metadata", "frontmatter")
    ):
        return "SKILL_LOAD_WARNING"
    if any(
        marker in lowered
        for marker in ("temporary state", "state cleanup", "state isolation", "sqlite snapshot")
    ):
        return "STATE_ISOLATION_WARNING"
    return "OTHER_QUERY_WARNING"


def normalize_query_warnings(warnings: list[str]) -> dict[str, object]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for raw in warnings:
        sanitized = _sanitize_diagnostic_line(str(raw), ())
        warning_class = _classify_query_warning(sanitized)
        signature = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        key = (warning_class, signature)
        if key not in grouped:
            grouped[key] = {
                "class": warning_class,
                "sha256": signature,
                "occurrences": 0,
            }
        grouped[key]["occurrences"] = int(grouped[key]["occurrences"]) + 1
    signatures = sorted(
        grouped.values(),
        key=lambda item: (str(item["class"]), str(item["sha256"])),
    )
    classes = sorted({str(item["class"]) for item in signatures})
    return {
        "status": "WARNING" if "OTHER_QUERY_WARNING" in classes else "OK",
        "warning_count": len(warnings),
        "warning_classes": classes,
        "warning_signatures": signatures,
    }


def decode_declared_name(raw: str) -> str:
    value: Any = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ProtocolError("skill locator declared name is invalid") from error
    elif value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9-]+", value) is None:
        raise ProtocolError("skill locator declared name is invalid")
    return value


def read_locator_snapshot(locator: str) -> LocatorSnapshot:
    path = Path(locator)
    if not path.is_absolute() or path.name != "SKILL.md" or path.is_symlink():
        raise ProtocolError("skill locator is not an exact regular SKILL.md")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int) or nofollow == 0:
        raise ProtocolError("safe skill locator read is unavailable")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | nofollow,
        )
    except OSError as error:
        raise ProtocolError("skill locator is unreadable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProtocolError("skill locator is not a regular file")
        if before.st_size > 1024 * 1024:
            raise ProtocolError("skill locator exceeds 1 MiB")
        content = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            content.extend(chunk)
            if len(content) > 1024 * 1024:
                raise ProtocolError("skill locator exceeds 1 MiB")
        after = os.fstat(descriptor)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ProtocolError("skill locator changed during read")
    finally:
        os.close(descriptor)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("skill locator is not UTF-8") from error
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if match is None:
        raise ProtocolError("skill locator frontmatter is invalid")
    names = re.findall(r"^name:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
    if len(names) != 1:
        raise ProtocolError("skill locator declared name is ambiguous")
    return LocatorSnapshot(
        declared_name=decode_declared_name(names[0]),
        sha256=hashlib.sha256(content).hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
    )


def read_declared_name(locator: str) -> str:
    return read_locator_snapshot(locator).declared_name


def split_discovery_identity(discovery_id: str, declared_name: str) -> tuple[str, str]:
    if ":" not in discovery_id:
        if discovery_id != declared_name:
            raise ProtocolError("discovery id does not match declared name")
        return "standalone", declared_name
    namespace, suffix = discovery_id.rsplit(":", 1)
    if not namespace or suffix != declared_name:
        raise ProtocolError("namespaced discovery id does not match declared name")
    return namespace, declared_name


def add_declared_identities(
    normalized: dict[str, Any],
    candidate: CandidateMount | None = None,
) -> None:
    candidate_redirect_used = False
    for item in normalized["skills"]:
        locator = Path(item["path"])
        read_locator = locator
        if (
            candidate is not None
            and item["name"] == candidate.skill_id
            and locator == candidate.target_locator
        ):
            if candidate_redirect_used:
                raise ProtocolError("candidate discovery is duplicated at target")
            read_locator = candidate.source_locator
            candidate_redirect_used = True
        declared_name = read_declared_name(str(read_locator))
        source_namespace, declared_name = split_discovery_identity(
            item["name"], declared_name
        )
        item["source_namespace"] = source_namespace
        item["declared_name"] = declared_name
    if candidate is not None and not candidate_redirect_used:
        raise ProtocolError("candidate discovery is missing at expected target")


def _sanitize_diagnostic_line(line: str, private_paths: tuple[str, ...]) -> str:
    sanitized = line
    for path in sorted((value for value in private_paths if value), key=len, reverse=True):
        sanitized = sanitized.replace(path, "<path>")
    sanitized = re.sub(
        r"(?i)\b(authorization|token|cookie|password|secret)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        sanitized,
    )
    sanitized = re.sub(r"(?<![A-Za-z0-9])/(?:[^\s/:]+/)+[^\s:]*", "<path>", sanitized)
    return sanitized[:4096]


def _bounded_diagnostics(
    stderr_lines: list[str],
    private_paths: tuple[str, ...] = (),
) -> list[str]:
    lines = [
        _sanitize_diagnostic_line(line, private_paths)
        for line in stderr_lines[:40]
    ]
    encoded = "\n".join(lines).encode("utf-8")[:4096]
    return encoded.decode("utf-8", errors="ignore").splitlines()


def _warning_signature(
    stderr_lines: list[str],
    private_paths: tuple[str, ...] = (),
) -> dict[str, object]:
    sanitized = _bounded_diagnostics(stderr_lines, private_paths)
    material = "\n".join(sanitized).encode("utf-8")
    return {
        "line_count": len(sanitized),
        "byte_count": len(material),
        "sha256": hashlib.sha256(material).hexdigest(),
    }


def _classify_process_exit(stderr_lines: list[str]) -> str:
    combined = "\n".join(stderr_lines).casefold()
    if "sqlite" in combined and any(
        marker in combined
        for marker in (
            "initialization failed",
            "unable to open database",
            "database is locked",
            "malformed",
        )
    ):
        return "SQLITE_INIT_FAILED"
    return "PROCESS_EXIT"


def wait_for_response(
    process: subprocess.Popen[str],
    request_id: int,
    timeout_seconds: float,
    stderr_lines: list[str],
    *,
    backfill_seconds: float = 3.0,
    private_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    if process.stdout is None or process.stderr is None:
        raise ProtocolError("app-server pipes unavailable", code="PROTOCOL_ERROR")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_seconds
    backfill_deadline: float | None = None
    try:
        while True:
            now = time.monotonic()
            active_deadline = min(
                deadline,
                backfill_deadline if backfill_deadline is not None else deadline,
            )
            if now >= active_deadline:
                break
            if process.poll() is not None and not selector.get_map():
                break
            events = selector.select(max(0.0, active_deadline - now))
            if not events:
                break
            for key, _ in events:
                line = key.fileobj.readline()
                if line == "":
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    stderr_lines.append(line.rstrip())
                    if "backfill" in line.casefold() and backfill_deadline is None:
                        backfill_deadline = time.monotonic() + backfill_seconds
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ProtocolError(
                        "non-JSON app-server stdout",
                        code="PROTOCOL_ERROR",
                        warning_signature=_warning_signature(
                            stderr_lines,
                            private_paths,
                        ),
                    ) from error
                if not isinstance(message, dict):
                    raise ProtocolError(
                        "app-server message is not an object",
                        code="PROTOCOL_ERROR",
                    )
                if message.get("id") == request_id:
                    if "error" in message:
                        raise ProtocolError(
                            "app-server returned an error",
                            code="PROTOCOL_ERROR",
                            warning_signature=_warning_signature(
                                stderr_lines,
                                private_paths,
                            ),
                        )
                    return message
        return_code = process.poll()
        if return_code is not None:
            code = _classify_process_exit(stderr_lines)
            raise ProtocolError(
                f"app-server exited before response {request_id}",
                code=code,
                warning_signature=_warning_signature(stderr_lines, private_paths),
            )
        if backfill_deadline is not None:
            raise ProtocolError(
                "app-server backfill deadline expired",
                code="BACKFILL_TIMEOUT",
                warning_signature=_warning_signature(stderr_lines, private_paths),
            )
        raise ProtocolError(
            f"app-server response {request_id} timed out",
            code="PROTOCOL_TIMEOUT",
            warning_signature=_warning_signature(stderr_lines, private_paths),
        )
    finally:
        selector.close()


def ensure_host_target_absent(candidate: CandidateMount) -> None:
    if os.path.lexists(candidate.target_dir) or os.path.lexists(
        candidate.target_locator
    ):
        raise ProtocolError("candidate target exists on host", code="PROTOCOL_ERROR")


def build_isolated_query_command(
    codex_bin: str,
    bwrap_bin: str,
    codex_home: Path,
    sqlite_snapshot_home: Path,
    candidate: CandidateMount | None = None,
) -> list[str]:
    resolved_codex_home = codex_home.resolve(strict=True)
    resolved_sqlite_home = sqlite_snapshot_home.resolve(strict=True)
    command = [
        bwrap_bin,
        "--die-with-parent",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp",
        "--overlay-src",
        str(resolved_codex_home),
        "--tmp-overlay",
        str(resolved_codex_home),
        "--bind",
        str(resolved_sqlite_home),
        str(resolved_sqlite_home),
        "--dev-bind",
        "/dev",
        "/dev",
        "--proc",
        "/proc",
        "--setenv",
        "CODEX_HOME",
        str(resolved_codex_home),
        "--setenv",
        "CODEX_SQLITE_HOME",
        str(resolved_sqlite_home),
    ]
    if candidate is not None:
        ensure_host_target_absent(candidate)
        command.extend(
            [
                "--ro-bind",
                str(candidate.source_dir.resolve(strict=True)),
                str(candidate.target_dir),
            ]
        )
    command.extend(
        [
            "--",
            codex_bin,
            "app-server",
            "--stdio",
            "--disable",
            "remote_plugin",
            "--disable",
            "multi_agent",
        ]
    )
    return command


def query_app_server(
    codex_bin: str,
    bwrap_bin: str,
    codex_home: str | Path,
    sqlite_home: str | Path,
    cwd: str,
    deadlines: QueryDeadlines,
    candidate: CandidateMount | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if not os.path.isfile(bwrap_bin) or not os.access(bwrap_bin, os.X_OK):
        raise ProtocolError(
            "network isolation unavailable: bubblewrap executable not found",
            code="ISOLATION_UNAVAILABLE",
        )
    if not os.path.isfile(codex_bin) or not os.access(codex_bin, os.X_OK):
        raise ProtocolError(
            "Codex executable not found",
            code="CODEX_BINARY_UNAVAILABLE",
        )
    codex_home_path = Path(codex_home).resolve(strict=True)
    sqlite_home_path = Path(sqlite_home).resolve(strict=True)
    capture_query_roots(codex_home_path, sqlite_home_path)
    temporary_parent = Path("/var/tmp")
    if not temporary_parent.is_dir():
        raise ProtocolError(
            "isolated query temporary parent is unavailable",
            code="ISOLATION_UNAVAILABLE",
        )
    with tempfile.TemporaryDirectory(
        prefix="vibe-project-lead-inventory-",
        dir=temporary_parent,
    ) as temporary:
        temporary_root = Path(temporary)
        receipt = snapshot_sqlite_home(sqlite_home_path, temporary_root)
        snapshot_home = Path(str(receipt["destination_path"])).parent
        command = build_isolated_query_command(
            codex_bin,
            bwrap_bin,
            codex_home_path,
            snapshot_home,
            candidate,
        )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise ProtocolError(
                "unable to start isolated app-server",
                code="ISOLATION_UNAVAILABLE",
            ) from error
        stderr_lines: list[str] = []
        private_paths = (
            str(codex_home_path),
            str(sqlite_home_path),
            str(temporary_root),
        )
        try:
            if process.stdin is None:
                raise ProtocolError(
                    "app-server stdin unavailable",
                    code="PROTOCOL_ERROR",
                )
            initialize = {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "vibe-project-lead-index",
                        "version": "1.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            }
            process.stdin.write(
                json.dumps(initialize, separators=(",", ":")) + "\n"
            )
            process.stdin.flush()
            wait_for_response(
                process,
                1,
                deadlines.initialize_seconds,
                stderr_lines,
                backfill_seconds=deadlines.backfill_seconds,
                private_paths=private_paths,
            )

            process.stdin.write('{"method":"initialized","params":{}}\n')
            request = {
                "id": 2,
                "method": "skills/list",
                "params": {"cwds": [cwd], "forceReload": True},
            }
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            response = wait_for_response(
                process,
                2,
                deadlines.skills_list_seconds,
                stderr_lines,
                backfill_seconds=deadlines.backfill_seconds,
                private_paths=private_paths,
            )
            return response, _bounded_diagnostics(stderr_lines, private_paths)
        except (OSError, BrokenPipeError) as error:
            raise ProtocolError(
                "unable to communicate with app-server",
                code="PROTOCOL_ERROR",
                warning_signature=_warning_signature(stderr_lines, private_paths),
            ) from error
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=deadlines.terminate_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=deadlines.terminate_seconds)
                    except subprocess.TimeoutExpired:
                        pass
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            if candidate is not None:
                ensure_host_target_absent(candidate)


def resolve_query_executables(codex_bin: str, bwrap_bin: str) -> tuple[str, str]:
    resolved_bwrap = shutil.which(bwrap_bin) if os.sep not in bwrap_bin else bwrap_bin
    if (
        not resolved_bwrap
        or not os.path.isfile(resolved_bwrap)
        or not os.access(resolved_bwrap, os.X_OK)
    ):
        raise ProtocolError(
            "network isolation unavailable: bubblewrap executable not found",
            code="ISOLATION_UNAVAILABLE",
        )
    resolved_codex = shutil.which(codex_bin) if os.sep not in codex_bin else codex_bin
    if (
        not resolved_codex
        or not os.path.isfile(resolved_codex)
        or not os.access(resolved_codex, os.X_OK)
    ):
        raise ProtocolError(
            "Codex executable not found",
            code="CODEX_BINARY_UNAVAILABLE",
        )
    return os.path.realpath(resolved_codex), os.path.realpath(resolved_bwrap)


def query_codex_version(codex_bin: str, timeout_seconds: float) -> str:
    try:
        result = subprocess.run(
            [codex_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise ProtocolError(
            "Codex version query timed out",
            code="PROTOCOL_TIMEOUT",
        ) from error
    except OSError as error:
        raise ProtocolError(
            "unable to start Codex version query",
            code="CODEX_BINARY_UNAVAILABLE",
        ) from error
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        raise ProtocolError(
            "Codex version process exited without a version",
            code="PROCESS_EXIT",
        )
    return version


def select_cwd_result(response: dict[str, Any], expected_cwd: str | None) -> dict[str, Any]:
    try:
        data = response["result"]["data"]
    except (KeyError, TypeError) as error:
        raise ProtocolError("skills/list result is missing data") from error
    if not isinstance(data, list) or not data:
        raise ProtocolError("skills/list returned no cwd data")

    if expected_cwd is not None:
        matches = [item for item in data if isinstance(item, dict) and item.get("cwd") == expected_cwd]
        if len(matches) == 1:
            return matches[0]
        if len(data) != 1:
            raise ProtocolError("skills/list cwd is ambiguous")
    if len(data) != 1 or not isinstance(data[0], dict):
        raise ProtocolError("skills/list cwd is ambiguous")
    return data[0]


def normalize_response(response: dict[str, Any], expected_cwd: str | None) -> dict[str, Any]:
    selected = select_cwd_result(response, expected_cwd)
    skills = selected.get("skills")
    errors = selected.get("errors", [])
    if not isinstance(selected.get("cwd"), str) or not isinstance(skills, list):
        raise ProtocolError("skills/list cwd entry is malformed")
    if not isinstance(errors, list):
        raise ProtocolError("skills/list errors must be a list")

    normalized: list[dict[str, Any]] = []
    for item in skills:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ProtocolError("skills/list contains a malformed skill")
        normalized.append(
            {
                "name": item["name"],
                "description": str(item.get("description", "")),
                "path": str(item.get("path", "")),
                "scope": str(item.get("scope", "unknown")),
                "enabled": bool(item.get("enabled", False)),
            }
        )
    normalized.sort(key=lambda item: (item["name"], item["scope"], item["path"]))
    return {
        "cwd": selected["cwd"],
        "skills": normalized,
        "errors": normalize_errors(errors),
    }


def validate_candidate_source(
    skill_id: str,
    source_dir: Path,
) -> tuple[Path, LocatorSnapshot]:
    if skill_id != "vibe-project-lead-zh":
        raise ProtocolError("candidate id is not approved")
    if not source_dir.is_absolute() or source_dir.is_symlink():
        raise ProtocolError("candidate source is not an exact directory")
    try:
        resolved_source = source_dir.resolve(strict=True)
        expected_source = (
            Path(__file__).resolve().parents[1] / "skill" / skill_id
        ).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProtocolError("candidate source is unavailable") from error
    if (
        resolved_source != expected_source
        or source_dir != resolved_source
        or not resolved_source.is_dir()
    ):
        raise ProtocolError("candidate source is outside the current checkout")
    locator = resolved_source / "SKILL.md"
    if locator.is_symlink():
        raise ProtocolError("candidate source locator is a symlink")
    snapshot = read_locator_snapshot(str(locator))
    if snapshot.declared_name != skill_id:
        raise ProtocolError("candidate source declared name does not match id")
    return resolved_source, snapshot


def validate_candidate_mount(
    skill_id: str,
    source_dir: Path,
    requested_target: Path,
    base: dict[str, Any],
    codex_home: str,
) -> CandidateMount:
    anchors = [item for item in base["skills"] if item["name"] == "using-superpowers"]
    if len(anchors) != 1 or not anchors[0]["enabled"]:
        raise ProtocolError("base discovery has no unique enabled using-superpowers")
    if any(
        "using-superpowers" in f"{error['path']} {error['message']}"
        for error in base["errors"]
    ):
        raise ProtocolError("base discovery has a using-superpowers load error")
    anchor = anchors[0]
    if anchor.get("declared_name") != "using-superpowers":
        raise ProtocolError("using-superpowers locator identity is invalid")

    anchor_locator = Path(anchor["path"])
    anchor_directory = anchor_locator.parent
    skill_root = anchor_directory.parent
    try:
        resolved_home = Path(codex_home).resolve(strict=True)
        resolved_root = skill_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProtocolError("candidate Skill root is unavailable") from error
    if (
        anchor_directory.is_symlink()
        or skill_root.is_symlink()
        or resolved_root != skill_root
        or not resolved_root.is_dir()
        or resolved_root == resolved_home
        or not resolved_root.is_relative_to(resolved_home)
    ):
        raise ProtocolError("candidate Skill root is outside the Codex home boundary")

    if not requested_target.is_absolute():
        raise ProtocolError("candidate target must be an absolute normalized path")
    normalized_target = Path(os.path.normpath(str(requested_target)))
    expected_target = resolved_root / skill_id
    if requested_target != normalized_target or normalized_target != expected_target:
        raise ProtocolError("candidate target does not match the discovered Skill root")
    if any(item["name"] == skill_id for item in base["skills"]):
        raise ProtocolError("candidate is already present in base discovery")

    candidate = CandidateMount(
        skill_id=skill_id,
        source_dir=source_dir,
        target_dir=expected_target,
    )
    if candidate.source_dir == candidate.target_dir:
        raise ProtocolError("candidate source and target must differ")
    ensure_host_target_absent(candidate)
    return candidate


def raw_discovery_view(
    normalized: dict[str, Any],
    excluded_skill_id: str | None = None,
) -> dict[str, Any]:
    skills = [
        {
            key: item[key]
            for key in ("name", "description", "path", "scope", "enabled")
        }
        for item in normalized["skills"]
        if item["name"] != excluded_skill_id
    ]
    return {
        "cwd": normalized["cwd"],
        "skills": sorted(
            skills,
            key=lambda item: (item["name"], item["scope"], item["path"]),
        ),
        "errors": normalize_errors(normalized["errors"]),
    }


def validate_candidate_discovery(
    base: dict[str, Any],
    discovered: dict[str, Any],
    candidate: CandidateMount,
) -> None:
    expected = [
        item
        for item in discovered["skills"]
        if item["name"] == candidate.skill_id
        and Path(item["path"]) == candidate.target_locator
    ]
    if len(expected) != 1:
        raise ProtocolError("candidate discovery is missing or duplicated at expected target")
    if any(
        Path(item["path"]) == candidate.target_locator
        and item["name"] != candidate.skill_id
        for item in discovered["skills"]
    ):
        raise ProtocolError("candidate target was reported under the wrong discovery id")
    if raw_discovery_view(base) != raw_discovery_view(
        discovered,
        excluded_skill_id=candidate.skill_id,
    ):
        raise ProtocolError("base discovery changed during candidate query")


def ensure_candidate_source_unchanged(
    candidate: CandidateMount,
    expected: LocatorSnapshot,
) -> None:
    if read_locator_snapshot(str(candidate.source_locator)) != expected:
        raise ProtocolError("candidate source changed during query")


def remap_candidate_warning(warning: str, candidate: CandidateMount) -> str:
    return warning.replace(
        str(candidate.source_locator),
        str(candidate.target_locator),
    ).replace(str(candidate.source_dir), str(candidate.target_dir))


def collect_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "ids" and isinstance(child, list):
                found.update(item for item in child if isinstance(item, str))
            else:
                found.update(collect_ids(child))
    return found


def required_skill_ids(policy: dict[str, Any]) -> set[str]:
    required = {policy["primary_router"]} if isinstance(policy.get("primary_router"), str) else set()
    required.update(collect_ids(policy.get("mandatory", {})))
    for name, value in policy.items():
        if name.startswith("required"):
            required.update(collect_ids(value))
    return required


def describe_error(error: Any) -> str:
    if isinstance(error, dict):
        path = str(error.get("path", "unknown path"))
        message = str(error.get("message", "unknown load error"))
        return f"{path}: {message}"
    return str(error)


def _runtime_surface() -> str:
    if os.name == "nt":
        return "WINDOWS_NATIVE"
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return "WSL"
    return "POSIX"


def _binary_identity(codex_bin: str, *, captured: bool) -> str | None:
    if not captured:
        return None
    path = Path(codex_bin)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _not_captured_home_identity() -> dict[str, str]:
    return {"state": "NOT_CAPTURED_INPUT_MODE"}


def _minimal_skill_identity(item: dict[str, Any] | None) -> dict[str, object] | None:
    if item is None:
        return None
    return {
        key: item.get(key)
        for key in (
            "name",
            "source_namespace",
            "declared_name",
            "scope",
            "enabled",
            "path",
            "description",
        )
    }


def _skill_groups(inventory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in inventory.get("skills", []):
        if isinstance(raw, dict) and isinstance(raw.get("name"), str):
            grouped[raw["name"]].append(raw)
    for values in grouped.values():
        values.sort(
            key=lambda item: (
                str(item.get("scope", "")),
                str(item.get("path", "")),
                str(item.get("description", "")),
            )
        )
    return dict(grouped)


def _append_inventory_diff(
    values: list[dict[str, object]],
    *,
    diff_type: str,
    skill: str,
    impact: str,
    before: object,
    after: object,
    action_zh: str,
) -> None:
    if impact not in DIFF_IMPACTS:
        raise ProtocolError("inventory diff impact is invalid")
    values.append(
        {
            "type": diff_type,
            "skill": skill,
            "impact": impact,
            "before": before,
            "after": after,
            "action_zh": action_zh,
        }
    )


def _diff_impact(skill: str, *, route_blocking: bool = True) -> str:
    if skill == SUPERVISOR_SKILL_ID:
        return "BLOCKING_SUPERVISOR"
    return "BLOCKING_ROUTE" if route_blocking else "NON_BLOCKING_DRIFT"


def _declared_identity_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("declared_name", "")),
        str(item.get("scope", "")),
    )


def compare_inventory(
    baseline: dict[str, object],
    current: dict[str, object],
) -> dict[str, object]:
    if baseline.get("schema_version") != 2 or current.get("schema_version") != 2:
        raise ProtocolError("inventory comparison requires schema 2")
    before_groups = _skill_groups(baseline)
    after_groups = _skill_groups(current)
    diff: list[dict[str, object]] = []
    route_states = {
        name: "READY"
        for name in sorted(set(before_groups) | set(after_groups))
    }
    for name, values in after_groups.items():
        if len(values) > 1 or any(not bool(item.get("enabled")) for item in values):
            route_states[name] = "BLOCKED"
    after_errors = normalize_errors(list(current.get("load_errors", [])))
    current_error_skills = sorted(
        {
            name
            for name, values in after_groups.items()
            if any(
                str(error.get("path", "")) == str(value.get("path", ""))
                for error in after_errors
                for value in values
            )
        }
    )
    for name in current_error_skills:
        route_states[name] = "BLOCKED"

    removed_names = set(before_groups) - set(after_groups)
    added_names = set(after_groups) - set(before_groups)
    rename_pairs: list[tuple[str, str]] = []
    for old_name in sorted(removed_names):
        old_item = before_groups[old_name][0]
        candidates = [
            new_name
            for new_name in sorted(added_names)
            if _declared_identity_key(after_groups[new_name][0])
            == _declared_identity_key(old_item)
        ]
        if len(candidates) == 1:
            new_name = candidates[0]
            rename_pairs.append((old_name, new_name))
            added_names.remove(new_name)
            removed_names.remove(old_name)

    for old_name, new_name in rename_pairs:
        impact = _diff_impact(old_name)
        _append_inventory_diff(
            diff,
            diff_type="SOURCE_NAMESPACE_CHANGED",
            skill=old_name,
            impact=impact,
            before=_minimal_skill_identity(before_groups[old_name][0]),
            after=_minimal_skill_identity(after_groups[new_name][0]),
            action_zh="刷新该 Skill 的 namespace 与 locator 后再调用。",
        )
        route_states[old_name] = "BLOCKED"
        route_states[new_name] = "BLOCKED"

    for name in sorted(removed_names):
        before = before_groups[name][0]
        impact = _diff_impact(name)
        _append_inventory_diff(
            diff,
            diff_type="SKILL_REMOVED",
            skill=name,
            impact=impact,
            before=_minimal_skill_identity(before),
            after=None,
            action_zh="确认是否卸载、改名或发现失败。",
        )
        if bool(before.get("required")) or name == SUPERVISOR_SKILL_ID:
            _append_inventory_diff(
                diff,
                diff_type="SKILL_RENAMED_OR_MISSING",
                skill=name,
                impact=impact,
                before=_minimal_skill_identity(before),
                after=None,
                action_zh="恢复必需 Skill 或更新已审阅的稳定映射。",
            )
        route_states[name] = "BLOCKED"

    for name in sorted(added_names):
        added_values = after_groups[name]
        _append_inventory_diff(
            diff,
            diff_type="SKILL_ADDED",
            skill=name,
            impact="NON_BLOCKING_DRIFT",
            before=None,
            after=_minimal_skill_identity(after_groups[name][0]),
            action_zh="审阅新 Skill 后再决定是否加入稳定中文映射。",
        )
        if len(added_values) > 1:
            _append_inventory_diff(
                diff,
                diff_type="SKILL_DUPLICATE",
                skill=name,
                impact=_diff_impact(name),
                before=0,
                after=len(added_values),
                action_zh="消除同一 discovery ID 的重复 locator。",
            )
            route_states[name] = "BLOCKED"
        if any(not bool(item.get("enabled")) for item in added_values):
            _append_inventory_diff(
                diff,
                diff_type="SKILL_DISABLED",
                skill=name,
                impact=_diff_impact(name),
                before=None,
                after=False,
                action_zh="启用并重新发现该 Skill 后再调用。",
            )
            route_states[name] = "BLOCKED"

    for name in sorted(set(before_groups) & set(after_groups)):
        before_values = before_groups[name]
        after_values = after_groups[name]
        before = before_values[0]
        after = after_values[0]
        impact = _diff_impact(name)
        if len(after_values) > 1:
            route_states[name] = "BLOCKED"
        if len(before_values) != len(after_values) and len(after_values) > 1:
            _append_inventory_diff(
                diff,
                diff_type="SKILL_DUPLICATE",
                skill=name,
                impact=impact,
                before=len(before_values),
                after=len(after_values),
                action_zh="消除同一 discovery ID 的重复 locator。",
            )
            route_states[name] = "BLOCKED"
        if bool(before.get("enabled")) != bool(after.get("enabled")):
            diff_type = "SKILL_ENABLED" if after.get("enabled") else "SKILL_DISABLED"
            _append_inventory_diff(
                diff,
                diff_type=diff_type,
                skill=name,
                impact=impact,
                before=bool(before.get("enabled")),
                after=bool(after.get("enabled")),
                action_zh="重新校验 enabled 状态后再调用。",
            )
            if not after.get("enabled"):
                route_states[name] = "BLOCKED"
        if before.get("source_namespace") != after.get("source_namespace"):
            _append_inventory_diff(
                diff,
                diff_type="SOURCE_NAMESPACE_CHANGED",
                skill=name,
                impact=impact,
                before=before.get("source_namespace"),
                after=after.get("source_namespace"),
                action_zh="确认 namespace 与 discovery ID 后刷新路由。",
            )
            route_states[name] = "BLOCKED"
        if before.get("path") != after.get("path"):
            _append_inventory_diff(
                diff,
                diff_type="LOCATOR_CHANGED",
                skill=name,
                impact=impact,
                before=before.get("path"),
                after=after.get("path"),
                action_zh="读取并校验新 locator 的 SKILL.md 后再调用。",
            )
            route_states[name] = "BLOCKED"
        metadata_fields = ("description", "scope", "declared_name")
        before_metadata = {key: before.get(key) for key in metadata_fields}
        after_metadata = {key: after.get(key) for key in metadata_fields}
        if before_metadata != after_metadata:
            _append_inventory_diff(
                diff,
                diff_type="METADATA_CHANGED",
                skill=name,
                impact=(
                    impact
                    if before.get("declared_name") != after.get("declared_name")
                    else "NON_BLOCKING_DRIFT"
                ),
                before=before_metadata,
                after=after_metadata,
                action_zh="审阅当前语义元数据并刷新索引。",
            )
            if before.get("declared_name") != after.get("declared_name"):
                route_states[name] = "BLOCKED"

    before_runtime = baseline.get("runtime", {})
    after_runtime = current.get("runtime", {})
    if not isinstance(before_runtime, dict) or not isinstance(after_runtime, dict):
        raise ProtocolError("inventory runtime identity is malformed")
    runtime_fields = ("surface", "codex_version", "codex_bin_sha256")
    before_runtime_core = {key: before_runtime.get(key) for key in runtime_fields}
    after_runtime_core = {key: after_runtime.get(key) for key in runtime_fields}
    if before_runtime_core != after_runtime_core:
        _append_inventory_diff(
            diff,
            diff_type="RUNTIME_CHANGED",
            skill="",
            impact="INFORMATIONAL",
            before=before_runtime_core,
            after=after_runtime_core,
            action_zh="确认当前执行面与 Codex 版本后刷新基线。",
        )
    home_fields = ("codex_home_identity", "sqlite_home_identity")
    before_homes = {key: before_runtime.get(key) for key in home_fields}
    after_homes = {key: after_runtime.get(key) for key in home_fields}
    if before_homes != after_homes:
        _append_inventory_diff(
            diff,
            diff_type="HOME_IDENTITY_CHANGED",
            skill="",
            impact="INFORMATIONAL",
            before=before_homes,
            after=after_homes,
            action_zh="不要合并状态根；在当前执行面重新生成 inventory。",
        )
    before_errors = normalize_errors(list(baseline.get("load_errors", [])))
    if before_errors != after_errors:
        _append_inventory_diff(
            diff,
            diff_type="LOAD_ERROR_CHANGED",
            skill=current_error_skills[0] if len(current_error_skills) == 1 else "",
            impact=(
                _diff_impact(current_error_skills[0])
                if len(current_error_skills) == 1
                else "BLOCKING_ROUTE"
            ),
            before=before_errors,
            after=after_errors,
            action_zh="解决关联 locator 的加载错误后再调用。",
        )

    diff.sort(key=lambda item: (str(item["type"]), str(item["skill"])))
    return {
        "baseline_inventory_sha256": baseline.get("inventory_sha256"),
        "inventory_sha256": current.get("inventory_sha256"),
        "diff": diff,
        "supervisor_state": route_states.get(SUPERVISOR_SKILL_ID, "BLOCKED"),
        "route_states": dict(sorted(route_states.items())),
    }


def analyze(
    normalized: dict[str, Any],
    policy: dict[str, Any],
    query_warnings: list[str],
    codex_version: str,
    codex_bin: str,
    *,
    runtime_surface: str | None = None,
    home_identities: dict[str, object] | None = None,
    isolation_state: str = "OFFLINE_FIXTURE",
) -> dict[str, Any]:
    skills = normalized["skills"]
    labels = policy["skills"]
    raw_inventory_hash = inventory_sha256(normalized)
    counts = Counter(item["name"] for item in skills)
    drift: list[dict[str, str]] = []

    for name, count in sorted(counts.items()):
        if count > 1:
            drift.append({"type": "duplicate", "skill": name, "detail": f"发现 {count} 个同名项"})
    for item in skills:
        if not item["enabled"]:
            drift.append({"type": "disabled", "skill": item["name"], "detail": "运行时已禁用"})
        if item["name"] not in labels:
            drift.append({"type": "unmapped", "skill": item["name"], "detail": "缺少中文导航映射"})
    for error in normalized["errors"]:
        drift.append({"type": "load_error", "skill": "", "detail": describe_error(error)})
    discovered_names = set(counts)
    for missing in sorted(required_skill_ids(policy) - discovered_names):
        drift.append(
            {
                "type": "missing_or_renamed",
                "skill": missing,
                "detail": "策略要求的 Skill 未发现，可能缺失或改名",
            }
        )

    for item in skills:
        mapping = labels.get(item["name"], {})
        item.update(
            {
                "label_zh": mapping.get("label_zh", item["name"]),
                "purpose_zh": mapping.get("purpose_zh", item["description"]),
                "category": mapping.get("category", "unmapped"),
                "phases": mapping.get("phases", []),
                "mapping_status": "已映射" if mapping else "未映射",
                "required": item["name"] in required_skill_ids(policy),
            }
        )

    drift_counts = Counter(item["type"] for item in drift)

    warning_record = normalize_query_warnings(query_warnings)
    homes = home_identities or {
        "codex_home": _not_captured_home_identity(),
        "sqlite_home": _not_captured_home_identity(),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "cwd": normalized["cwd"],
        "codex_version": codex_version,
        "codex_bin": codex_bin,
        "runtime": {
            "surface": runtime_surface or (
                _runtime_surface()
                if home_identities is not None
                else "OFFLINE_FIXTURE"
            ),
            "codex_version": codex_version,
            "codex_bin_sha256": _binary_identity(
                codex_bin,
                captured=home_identities is not None,
            ),
            "codex_home_identity": homes["codex_home"],
            "sqlite_home_identity": homes["sqlite_home"],
        },
        "query": {
            "isolation": {"state": isolation_state},
            **warning_record,
        },
        "load_errors": normalize_errors(normalized["errors"]),
        "inventory_sha256": raw_inventory_hash,
        "baseline_inventory_sha256": None,
        "diff": [],
        "summary": {
            "total": len(skills),
            "enabled": sum(bool(item["enabled"]) for item in skills),
            "disabled": sum(not bool(item["enabled"]) for item in skills),
            "duplicate": drift_counts["duplicate"],
            "unmapped": drift_counts["unmapped"],
            "missing_or_renamed": drift_counts["missing_or_renamed"],
            "load_errors": len(normalized["errors"]),
            "query_warnings": len(query_warnings),
            "drift_count": len(drift),
        },
        "skills": skills,
        "drift": drift,
    }


def escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_index(inventory: dict[str, Any], policy: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "# Codex Skill 中文索引",
        "",
        f"- schema_version: `{inventory['schema_version']}`",
        f"- generated_at_utc: `{inventory['generated_at_utc']}`",
        f"- cwd: `{inventory['cwd']}`",
        f"- codex_version: `{escape_cell(inventory['codex_version'])}`",
        f"- codex_bin: `{escape_cell(inventory['codex_bin'])}`",
        f"- inventory_sha256: `{inventory['inventory_sha256']}`",
        f"- runtime_surface: `{escape_cell(inventory['runtime']['surface'])}`",
        f"- warning_classes: `{escape_cell(', '.join(inventory['query']['warning_classes']) or 'none')}`",
        f"- total / enabled / disabled: `{summary['total']} / {summary['enabled']} / {summary['disabled']}`",
        f"- duplicate / unmapped / missing_or_renamed: `{summary['duplicate']} / {summary['unmapped']} / {summary['missing_or_renamed']}`",
        f"- load_errors / query_warnings: `{summary['load_errors']} / {summary['query_warnings']}`",
        "",
        f"共发现 {summary['total']} 个 Skill。",
        "",
        "此文件是运行时发现清单的中文导航视图，不是 Skill 行为权威。执行前仍需读取目标 Skill 当前的 `SKILL.md`。",
        "",
        "## inventory cwd 比较边界",
        "",
        "`cwd` 是生成快照时的 inventory cwd，不是后续绑定的业务项目。只有相同规范化 inventory cwd、相同 Codex runtime 和相同 Skill root 的完整发现才能比较 `inventory_sha256`；不同 cwd 的完整哈希不可直接比较。",
        "",
        "在不同业务项目中，对准备调用的条目执行逐 locator 校验，并交叉核对 discovery ID、source namespace、declared name、enabled、duplicate、load error 和当前元数据。快照外的 project-local Skill 标记为 `UNINDEXED_PROJECT_SKILL`，不得猜测 locator 或手工补行。",
        "",
    ]

    if inventory["query"]["warning_signatures"]:
        lines.extend(["## 查询警告", ""])
        for warning in inventory["query"]["warning_signatures"]:
            lines.append(
                "- `{warning_class}` / `{sha256}` / occurrences: `{occurrences}`".format(
                    warning_class=escape_cell(warning["class"]),
                    sha256=escape_cell(warning["sha256"]),
                    occurrences=escape_cell(warning["occurrences"]),
                )
            )
        lines.append("")

    if inventory["drift"]:
        lines.extend(["## 发现漂移", ""])
        for item in inventory["drift"]:
            skill = f" `{item['skill']}`" if item["skill"] else ""
            drift_label = DRIFT_LABELS_ZH.get(item["type"], item["type"])
            lines.append(f"- **{escape_cell(drift_label)}**{skill}：{escape_cell(item['detail'])}")
        lines.append("")

    categories = policy.get("categories", {})
    phases = policy.get("phases", {})
    stable_grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for skill_id, mapping in policy["skills"].items():
        stable_grouped[str(mapping.get("category", "unmapped"))].append(
            (skill_id, mapping)
        )

    def category_key(category: str) -> tuple[int, str]:
        config = categories.get(category, {})
        return int(config.get("order", 999)), category

    ordered_categories = sorted(stable_grouped, key=category_key)
    lines.extend(
        [
            "## 稳定中文映射",
            "",
            "此区只包含已审阅的稳定 Skill ID、中文用途和选择阶段，不保存当前或版本化 locator。",
            "",
            "### 分类导航",
            "",
        ]
    )
    for category in ordered_categories:
        label = str(categories.get(category, {}).get("label_zh", category))
        lines.append(f"- [{escape_cell(label)}](#{escape_cell(label)})")
    lines.append("")

    for category in ordered_categories:
        config = categories.get(category, {})
        lines.extend([f"### {config.get('label_zh', category)}", ""])
        lines.append("| 中文名称 | stable Skill ID | 适用阶段 | 用途 |")
        lines.append("|---|---|---|---|")
        for skill_id, mapping in sorted(stable_grouped[category]):
            phase_labels = [
                phases.get(phase, {}).get("label_zh", phase)
                for phase in mapping.get("phases", [])
            ]
            lines.append(
                "| {label} | `{name}` | {phase_text} | {purpose} |".format(
                    label=escape_cell(mapping.get("label_zh", skill_id)),
                    name=escape_cell(skill_id),
                    phase_text=escape_cell("、".join(phase_labels) or "按需"),
                    purpose=escape_cell(mapping.get("purpose_zh", "")),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## 本次动态发现",
            "",
            "此区来自本次隔离 discovery；每次路由都必须校验 discovery ID、namespace、declared name、enabled、duplicate、load error 和 locator，不能按同名文件搜索替代项。",
            "",
        ]
    )
    dynamic_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for skill in inventory["skills"]:
        dynamic_grouped[skill["category"]].append(skill)
    for category in sorted(dynamic_grouped, key=category_key):
        config = categories.get(category, {})
        lines.extend([f"### {config.get('label_zh', category)}", ""])
        lines.append(
            "| 中文名称 | discovery ID | source namespace | declared name | 适用阶段 | 用途 | 状态 | scope | 映射 | source locator |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for skill in sorted(
            dynamic_grouped[category],
            key=lambda item: (item["name"], item["scope"], item["path"]),
        ):
            phase_labels = [phases.get(phase, {}).get("label_zh", phase) for phase in skill["phases"]]
            state = "启用" if skill["enabled"] else "已禁用"
            lines.append(
                "| {label} | `{name}` | `{namespace}` | `{declared}` | {phase_text} | {purpose} | {state} | `{scope}` | {mapping} | `{path}` |".format(
                    label=escape_cell(skill["label_zh"]),
                    name=escape_cell(skill["name"]),
                    namespace=escape_cell(skill["source_namespace"]),
                    declared=escape_cell(skill["declared_name"]),
                    phase_text=escape_cell("、".join(phase_labels) or "按需"),
                    purpose=escape_cell(skill["purpose_zh"]),
                    state=state,
                    scope=escape_cell(skill["scope"]),
                    mapping=skill["mapping_status"],
                    path=escape_cell(skill["path"]),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _route_ids(values: list[str] | tuple[str, ...]) -> set[str]:
    if not isinstance(values, (list, tuple)):
        raise ProtocolError("route IDs must be a list or tuple")
    if any(
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(character.isspace() for character in value)
        or any(not part for part in value.split(":"))
        for value in values
    ):
        raise ProtocolError("route ID is invalid")
    return set(values)


def assess_skill_route(
    response: dict[str, Any],
    *,
    expected_cwd: str,
    target_ids: list[str] | tuple[str, ...] = (),
    required_ids: list[str] | tuple[str, ...] = (),
    expected_locators: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Check selected identities; never grant execution or full-inventory status."""
    if not isinstance(expected_cwd, str) or not Path(expected_cwd).is_absolute():
        raise ProtocolError("route cwd must be explicit and absolute")
    targets = _route_ids(target_ids)
    required = _route_ids(required_ids)
    selected = targets | required | {SUPERVISOR_SKILL_ID}
    pins = {} if expected_locators is None else expected_locators
    if not isinstance(pins, dict) or not set(pins) <= selected:
        raise ProtocolError("locator pins must refer to selected IDs")
    if any(not isinstance(value, str) or not Path(value).is_absolute()
           for value in pins.values()):
        raise ProtocolError("locator pin must be an absolute path")
    result = response.get("result") if isinstance(response, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("cwd"), str)
        for entry in data
    ):
        raise ProtocolError("route discovery data is malformed")
    matches = [entry for entry in data if entry["cwd"] == expected_cwd]
    if len(matches) != 1:
        raise ProtocolError("route cwd is missing or ambiguous")
    entry = matches[0]
    skills, errors = entry.get("skills"), entry.get("errors", [])
    if not isinstance(skills, list) or not isinstance(errors, list):
        raise ProtocolError("route discovery lists are malformed")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in skills:
        if (not isinstance(item, dict)
                or not isinstance(item.get("name"), str) or not item["name"]
                or not isinstance(item.get("path"), str) or not item["path"]):
            raise ProtocolError("route discovery identity is malformed")
        groups[item["name"]].append(item)

    error_ids: set[str] = set()
    unattributed_errors = 0
    for error in errors:
        path = error.get("path") if isinstance(error, dict) else None
        owners = {
            name for name, items in groups.items()
            if isinstance(path, str) and path
            and any(item["path"] == path for item in items)
        }
        if not owners:
            unattributed_errors += 1
        else:
            error_ids.update(owners)

    blocked: list[dict[str, str]] = []
    verified: list[dict[str, Any]] = []
    for name in sorted(selected):
        items = groups.get(name, [])
        reason = None
        if not items:
            reason = "MISSING"
        elif len(items) != 1:
            reason = "DUPLICATE"
        elif type(items[0].get("enabled")) is not bool:
            raise ProtocolError("selected enabled must be a boolean")
        elif not items[0]["enabled"]:
            reason = "DISABLED"
        elif name in error_ids:
            reason = "LOAD_ERROR"
        if reason is not None:
            blocked.append({"skill_id": name, "reason": reason})
            continue
        item = items[0]
        if not isinstance(item.get("scope"), str) or not item["scope"]:
            raise ProtocolError("selected scope must be explicit")
        if name in pins and pins[name] != item["path"]:
            blocked.append({"skill_id": name, "reason": "LOCATOR_CHANGED"})
            continue
        try:
            snapshot = read_locator_snapshot(item["path"])
            namespace, declared = split_discovery_identity(name, snapshot.declared_name)
        except (ProtocolError, OSError, ValueError):
            blocked.append({"skill_id": name, "reason": "LOCATOR_UNVERIFIED"})
            continue
        verified.append({
            "skill_id": name, "source_namespace": namespace,
            "declared_name": declared, "scope": item["scope"],
            "locator": item["path"], "locator_sha256": snapshot.sha256,
        })

    unrelated = []
    for name in sorted(set(groups) - selected):
        items = groups[name]
        reasons = []
        if len(items) > 1:
            reasons.append("DUPLICATE")
        if any(type(item.get("enabled")) is not bool for item in items):
            reasons.append("ENABLED_NOT_BOOLEAN")
        elif any(not item["enabled"] for item in items):
            reasons.append("DISABLED")
        if name in error_ids:
            reasons.append("LOAD_ERROR")
        if reasons:
            unrelated.append({"skill_id": name, "reasons": reasons})
    if any(item["skill_id"] == SUPERVISOR_SKILL_ID for item in blocked):
        status = "BLOCKED_SUPERVISOR"
    elif unattributed_errors:
        status = "UNKNOWN"
    elif blocked:
        status = "BLOCKED_ROUTE"
    else:
        status = "VERIFIED"
    return {
        "schema_version": 1,
        "kind": "selected-skill-route-check",
        "status": status,
        "cwd": expected_cwd,
        "selection": {
            "supervisor_id": SUPERVISOR_SKILL_ID,
            "target_ids": sorted(targets),
            "required_ids": sorted(required),
        },
        "verified": verified,
        "blocked": blocked,
        "unrelated_findings": unrelated,
        "unattributed_error_count": unattributed_errors,
        "freshness": "NOT_VERIFIED",
        "inventory_completeness": "NOT_CAPTURED",
        "instructions_loaded": False,
        "write_authorized": False,
    }


def select_route_dependencies(
    policy: dict[str, Any],
    *,
    target_ids: list[str] | tuple[str, ...] = (),
    required_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Select declared current requirements, independently of full-audit policy."""
    routing = policy.get("routing") if isinstance(policy, dict) else None
    if not isinstance(routing, dict):
        raise ProtocolError("policy has no current-route contract")
    core = _route_ids(routing.get("core_ids", []))
    if core != {SUPERVISOR_SKILL_ID}:
        raise ProtocolError("current-route core must be the supervisor only")
    targets = _route_ids(target_ids)
    required = _route_ids(required_ids)
    return {
        "target_ids": sorted(targets),
        "required_ids": sorted(core | required),
        "selection_basis": {
            "product_core": sorted(core),
            "user_targets": sorted(targets),
            "current_requirements": sorted(required),
        },
        "requirement_completeness": "CALLER_DECLARED",
    }


def render_stable_navigation(policy: dict[str, Any]) -> str:
    """Render only the static capability allowlist, never a discovery snapshot."""
    select_route_dependencies(policy)
    capabilities = policy["routing"].get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ProtocolError("routing capabilities must be a nonempty list")
    rows = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ProtocolError("routing capability must be an object")
        label, purpose = capability.get("label_zh"), capability.get("purpose_zh")
        if any(not isinstance(value, str) or not value.strip()
               for value in (label, purpose)):
            raise ProtocolError("routing capability text is missing")
        examples = sorted(_route_ids(capability.get("example_ids", [])))
        if not examples:
            raise ProtocolError("routing capability needs an example ID")
        rows.append("| " + " | ".join(
            escape_cell(value) for value in (label, purpose, "、".join(examples))
        ) + " |")
    lines = [
        "# Skill 能力导航",
        "",
        "## 稳定中文映射",
        "",
        "本页描述能力与示例 discovery ID，不是本机已安装、已启用或完整库存的证明。",
        "主管自身是产品必需项；扩展示例不是启动依赖。系统、用户和当前已加载规则要求的强制 Skill 仍按当前动作生效。",
        "本次动态发现决定目标是否可用；导航未列出的目标记为 UNINDEXED_PROJECT_SKILL 覆盖提示，其当前身份仍按主管工作流逐 locator 校验。",
        "",
        "| 能力 | 用途 | 示例 discovery ID（不代表当前可用） |",
        "|---|---|---|",
        *rows,
        "",
        "## 与本地库存报告的边界",
        "",
        "本页不保存个人 discovery 快照。显式库存工具生成的本地报告另存到操作者指定的位置，不写回安装目录。",
        "inventory cwd 是动态报告的来源信息，不是当前业务项目绑定。完整 inventory_sha256 仅比较相同规范化 inventory cwd、相同 runtime 与同一 Skill 根的完整发现；不同 cwd 的完整哈希不可直接比较。",
        "目标身份可用不等于正文已读、指令已执行或写入已获批准。具体读取顺序、异常分类和降级边界见主管工作流。",
        "",
        "<!--",
        "SPDX-License-Identifier: MPL-2.0",
        "This Source Code Form is subject to the terms of the Mozilla Public",
        "License, v. 2.0. If a copy of the MPL was not distributed with this",
        "file, You can obtain one at https://mozilla.org/MPL/2.0/.",
        "-->",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--query", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--codex-bin")
    parser.add_argument("--codex-version")
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
    )
    parser.add_argument(
        "--codex-sqlite-home",
        default=os.environ.get(
            "CODEX_SQLITE_HOME",
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        ),
    )
    parser.add_argument("--bwrap-bin", default="bwrap")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--version-timeout", type=float)
    parser.add_argument("--initialize-timeout", type=float)
    parser.add_argument("--skills-list-timeout", type=float)
    parser.add_argument("--backfill-timeout", type=float)
    parser.add_argument("--terminate-timeout", type=float)
    parser.add_argument("--candidate-id")
    parser.add_argument("--candidate-source", type=Path)
    parser.add_argument("--candidate-target", type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--inventory-output", type=Path)
    return parser.parse_args()


def print_result(status: str, **values: Any) -> None:
    print(json.dumps({"status": status, **values}, ensure_ascii=False, sort_keys=True))


def main() -> int:
    args = parse_args()
    try:
        legacy_timeout = args.timeout
        defaults = QueryDeadlines()
        deadlines = QueryDeadlines(
            version_seconds=(
                args.version_timeout
                if args.version_timeout is not None
                else min(defaults.version_seconds, legacy_timeout)
            ),
            initialize_seconds=(
                args.initialize_timeout
                if args.initialize_timeout is not None
                else min(defaults.initialize_seconds, legacy_timeout)
            ),
            skills_list_seconds=(
                args.skills_list_timeout
                if args.skills_list_timeout is not None
                else min(defaults.skills_list_seconds, legacy_timeout)
            ),
            backfill_seconds=(
                args.backfill_timeout
                if args.backfill_timeout is not None
                else min(defaults.backfill_seconds, legacy_timeout)
            ),
            terminate_seconds=(
                args.terminate_timeout
                if args.terminate_timeout is not None
                else min(defaults.terminate_seconds, legacy_timeout)
            ),
        )
        if any(value <= 0 for value in deadlines.__dict__.values()):
            raise ProtocolError("query deadlines must be positive")
        policy = load_policy(args.policy)
        query_warnings: list[str] = []
        home_identities: dict[str, object] | None = None
        isolation_state = "OFFLINE_FIXTURE"
        candidate_mount: CandidateMount | None = None
        candidate_snapshot: LocatorSnapshot | None = None
        candidate_values = (
            args.candidate_id,
            args.candidate_source,
            args.candidate_target,
        )
        candidate_count = sum(value is not None for value in candidate_values)
        if candidate_count not in (0, 3):
            raise ProtocolError("candidate arguments must be supplied together")
        if args.query:
            if not args.cwd:
                raise ProtocolError("--cwd is required with --query")
            if not args.codex_bin:
                raise ProtocolError("--codex-bin is required with --query")
            if args.codex_version is not None:
                raise ProtocolError("--codex-version is input-mode provenance only")
            resolved_codex, resolved_bwrap = resolve_query_executables(
                args.codex_bin,
                args.bwrap_bin,
            )
            codex_version = query_codex_version(
                resolved_codex,
                deadlines.version_seconds,
            )
            codex_bin = resolved_codex
            home_identities = capture_query_roots(
                Path(args.codex_home),
                Path(args.codex_sqlite_home),
            )
            isolation_state = "BWRAP_NO_NETWORK"
            if candidate_count:
                source_dir, candidate_snapshot = validate_candidate_source(
                    args.candidate_id,
                    args.candidate_source,
                )
                base_response, base_warnings = query_app_server(
                    resolved_codex,
                    resolved_bwrap,
                    args.codex_home,
                    args.codex_sqlite_home,
                    args.cwd,
                    deadlines,
                )
                base = normalize_response(base_response, args.cwd)
                add_declared_identities(base)
                candidate_mount = validate_candidate_mount(
                    args.candidate_id,
                    source_dir,
                    args.candidate_target,
                    base,
                    args.codex_home,
                )
                ensure_candidate_source_unchanged(
                    candidate_mount,
                    candidate_snapshot,
                )
                response, candidate_warnings = query_app_server(
                    resolved_codex,
                    resolved_bwrap,
                    args.codex_home,
                    args.codex_sqlite_home,
                    args.cwd,
                    deadlines,
                    candidate_mount,
                )
                ensure_host_target_absent(candidate_mount)
                ensure_candidate_source_unchanged(
                    candidate_mount,
                    candidate_snapshot,
                )
                normalized = normalize_response(response, args.cwd)
                validate_candidate_discovery(base, normalized, candidate_mount)
                add_declared_identities(normalized, candidate_mount)
                query_warnings = [
                    *(
                        f"base: {remap_candidate_warning(warning, candidate_mount)}"
                        for warning in base_warnings
                    ),
                    *(
                        f"candidate: {remap_candidate_warning(warning, candidate_mount)}"
                        for warning in candidate_warnings
                    ),
                ]
            else:
                response, query_warnings = query_app_server(
                    resolved_codex,
                    resolved_bwrap,
                    args.codex_home,
                    args.codex_sqlite_home,
                    args.cwd,
                    deadlines,
                )
                normalized = normalize_response(response, args.cwd)
                add_declared_identities(normalized)
        else:
            if candidate_count:
                raise ProtocolError("candidate arguments require query mode")
            if not args.codex_version or not args.codex_bin:
                raise ProtocolError(
                    "input mode requires --codex-version and --codex-bin provenance"
                )
            codex_version = args.codex_version
            codex_bin = args.codex_bin
            response = read_response(args.input)
            normalized = normalize_response(response, None)
            add_declared_identities(normalized)
        inventory = analyze(
            normalized,
            policy,
            query_warnings,
            codex_version,
            codex_bin,
            home_identities=home_identities,
            isolation_state=isolation_state,
        )
        if candidate_mount is not None and candidate_snapshot is not None:
            ensure_host_target_absent(candidate_mount)
            ensure_candidate_source_unchanged(candidate_mount, candidate_snapshot)
        atomic_write(args.output, render_index(inventory, policy))
        if args.inventory_output:
            if candidate_mount is not None and candidate_snapshot is not None:
                ensure_host_target_absent(candidate_mount)
                ensure_candidate_source_unchanged(candidate_mount, candidate_snapshot)
            atomic_write(
                args.inventory_output,
                json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
    except ProtocolError as error:
        values: dict[str, object] = {
            "code": error.code,
            "reason": str(error),
        }
        if error.warning_signature is not None:
            values["warning_signature"] = error.warning_signature
        print_result("protocol_error", **values)
        return EXIT_PROTOCOL

    status = "drift" if inventory["drift"] else "ok"
    print_result(status, **inventory["summary"])
    return EXIT_DRIFT if inventory["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
