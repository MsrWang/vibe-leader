#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Select relevant local evidence excerpts without exposing source paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence


MAX_JSON_BYTES = 1 * 1024 * 1024
MAX_QUERY_CHARS = 4_096
MAX_FILES = 64
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
WINDOW_BYTES = 4_096
OVERLAP_BYTES = 512
MAX_CANDIDATES = 20_000
MAX_SELECTED = 24

_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_.-])[\"']?[A-Za-z0-9_.-]*"
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|authorization|cookie)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?[^\s,;}\]\"']+",
    re.IGNORECASE,
)
_AUTH_HEADER = re.compile(
    r"(?im)^\s*(?:authorization|proxy-authorization|cookie|set-cookie)"
    r"\s*:\s*\S+"
)
_AUTH_SCHEME = re.compile(r"\b(?:bearer|basic)\s+\S+", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE)
_COMMON_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{8,}|"
    r"whsec_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,})\b",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}"
    r"@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[ -]?)?(?:\(\d{2,4}\)[ -]?)?"
    r"\d{3,4}[ -]\d{4}(?!\d)|(?<!\d)1[3-9]\d{9}(?!\d)"
)
_FILE_URI = re.compile(r"\bfile://\S+", re.IGNORECASE)
_FILESYSTEM_PATH = re.compile(
    r"(?<![A-Za-z0-9_:/])/(?:[^/\s\"'<>]+/)*[^/\s\"'<>]+"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\/\s\"'<>]+[\\/])*"
    r"[^\\/\s\"'<>]+"
    r"|\\\\[^\\\s\"'<>]+\\[^\\\s\"'<>]+(?:\\[^\\\s\"'<>]+)*"
)
_LATIN_TOKEN = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]+")


class EvidenceInputError(ValueError):
    """Input or source could not be processed without leaking its contents."""

    def __init__(self, reason: str, *, status: str = "UNKNOWN"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class CandidateView:
    """The only candidate fields visible to a selector."""

    candidate_id: str
    text: str


@dataclass(frozen=True)
class _SourceSnapshot:
    source_ref: str
    relative_path: str
    relative_bytes: bytes
    resolved_path: str | None
    identity: tuple[int, int, int, int, int, int]
    content: bytes
    content_sha256: str


@dataclass(frozen=True)
class _Candidate:
    view: CandidateView
    source_index: int
    start_byte: int
    end_byte: int
    content_sha256: str


class LocalCandidateSelector(Protocol):
    def select(
        self, query: str, candidates: Sequence[CandidateView]
    ) -> Sequence[str]:
        """Select locally from a gated query and anonymous, path-free views."""


def _result(
    status: str,
    mode: str,
    *,
    candidate_count: int = 0,
    selected_count: int = 0,
    readback_count: int = 0,
    fallback_reason: str | None = None,
    items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "readback_count": readback_count,
        "fallback_reason": fallback_reason,
        "items": items or [],
    }


def _scan_projection(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _contains_sensitive_content(value: str, *, is_path_field: bool = False) -> bool:
    scanned = _scan_projection(value)
    patterns = (
        _CREDENTIAL_ASSIGNMENT,
        _AUTH_HEADER,
        _AUTH_SCHEME,
        _PRIVATE_KEY,
        _COMMON_TOKEN,
        _JWT,
        _EMAIL,
        _PHONE,
    )
    if not is_path_field:
        patterns += (_FILE_URI, _FILESYSTEM_PATH)
    return any(
        pattern.search(scanned) is not None
        for pattern in patterns
    )


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


LINUX_SECURE_READ = "LINUX_PROC_FD"
DARWIN_SECURE_READ = "DARWIN_OPENAT"


def _secure_read_backend(platform_name: str | None = None) -> str | None:
    selected = sys.platform if platform_name is None else platform_name
    common = (
        os.name == "posix"
        and isinstance(getattr(os, "O_NOFOLLOW", None), int)
        and isinstance(getattr(os, "O_DIRECTORY", None), int)
        and os.open in os.supports_dir_fd
    )
    if not common:
        return None
    if selected == "darwin":
        nofollow_any = getattr(os, "O_NOFOLLOW_ANY", None)
        return DARWIN_SECURE_READ if isinstance(nofollow_any, int) and nofollow_any else None
    if selected.startswith("linux"):
        if (
            isinstance(getattr(os, "O_PATH", None), int)
            and os.readlink in os.supports_dir_fd
            and os.path.isdir("/proc/self/fd")
        ):
            return LINUX_SECURE_READ
    return None


def _open_root_linux(project_root: str) -> tuple[int, str, tuple[int, int, int, int, int, int]]:
    resolved_root = os.path.realpath(project_root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(resolved_root, flags)
    except OSError:
        raise EvidenceInputError("PATH_UNSAFE") from None
    metadata = os.fstat(root_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(root_fd)
        raise EvidenceInputError("PATH_UNSAFE")
    try:
        descriptor_path = os.readlink(f"/proc/self/fd/{root_fd}")
    except OSError:
        os.close(root_fd)
        raise EvidenceInputError("SECURE_READ_UNAVAILABLE") from None
    if os.path.realpath(descriptor_path) != resolved_root:
        os.close(root_fd)
        raise EvidenceInputError("PATH_UNSAFE")
    return root_fd, resolved_root, _file_identity(metadata)


def _open_root_darwin(project_root: str) -> tuple[int, str, tuple[int, int, int, int, int, int]]:
    root_path = os.path.abspath(project_root)
    try:
        before = os.lstat(root_path)
        root_fd = os.open(
            root_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW_ANY | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        raise EvidenceInputError("PATH_UNSAFE") from None
    try:
        after = os.lstat(root_path)
        descriptor = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(descriptor.st_mode)
            or _file_identity(before) != _file_identity(after)
            or _file_identity(after) != _file_identity(descriptor)
        ):
            raise EvidenceInputError("PATH_UNSAFE")
        return root_fd, root_path, _file_identity(descriptor)
    except EvidenceInputError:
        os.close(root_fd)
        raise
    except OSError:
        os.close(root_fd)
        raise EvidenceInputError("PATH_UNSAFE") from None


def _open_root(project_root: str) -> tuple[int, str, tuple[int, int, int, int, int, int], str]:
    backend = _secure_read_backend()
    if backend == LINUX_SECURE_READ:
        root_fd, root_path, identity = _open_root_linux(project_root)
    elif backend == DARWIN_SECURE_READ:
        root_fd, root_path, identity = _open_root_darwin(project_root)
    else:
        raise EvidenceInputError("SECURE_READ_UNAVAILABLE")
    return root_fd, root_path, identity, backend


def _validate_relative_path(value: object) -> tuple[str, bytes]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1_024
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise EvidenceInputError("PATH_UNSAFE")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise EvidenceInputError("PATH_UNSAFE") from None
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise EvidenceInputError("PATH_UNSAFE")
    return value, encoded


def _open_relative_file(root_fd: int, relative_bytes: bytes, backend: str) -> tuple[int, int, bytes]:
    parts = relative_bytes.split(b"/")
    parent_fd = os.dup(root_fd)
    leaf_fd = -1
    file_fd = -1
    try:
        for part in parts[:-1]:
            before = os.lstat(part, dir_fd=parent_fd)
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            try:
                after = os.lstat(part, dir_fd=parent_fd)
                descriptor = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(descriptor.st_mode)
                    or _file_identity(before) != _file_identity(after)
                    or _file_identity(after) != _file_identity(descriptor)
                ):
                    raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
            except Exception:
                os.close(next_fd)
                raise
            os.close(parent_fd)
            parent_fd = next_fd
        if backend == LINUX_SECURE_READ:
            leaf_fd = os.open(
                parts[-1],
                os.O_PATH | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            leaf_metadata = os.fstat(leaf_fd)
            leaf_identity = _file_identity(leaf_metadata)
            if not stat.S_ISREG(leaf_metadata.st_mode):
                raise EvidenceInputError("PATH_UNSAFE")
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            if _file_identity(os.fstat(file_fd)) != leaf_identity:
                raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
            os.close(leaf_fd)
            leaf_fd = -1
        elif backend == DARWIN_SECURE_READ:
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW_ANY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise EvidenceInputError("PATH_UNSAFE")
        else:
            raise EvidenceInputError("SECURE_READ_UNAVAILABLE")
        return parent_fd, file_fd, parts[-1]
    except EvidenceInputError:
        if file_fd >= 0:
            os.close(file_fd)
        if leaf_fd >= 0:
            os.close(leaf_fd)
        os.close(parent_fd)
        raise
    except OSError:
        if file_fd >= 0:
            os.close(file_fd)
        if leaf_fd >= 0:
            os.close(leaf_fd)
        os.close(parent_fd)
        raise EvidenceInputError("PATH_UNSAFE") from None


def _resolved_file_path(file_fd: int, root_path: str, backend: str) -> str | None:
    if backend == DARWIN_SECURE_READ:
        return None
    try:
        path = os.readlink(f"/proc/self/fd/{file_fd}")
        resolved = os.path.realpath(path)
        if os.path.commonpath((root_path, resolved)) != root_path:
            raise EvidenceInputError("PATH_UNSAFE")
        return resolved
    except (OSError, ValueError):
        raise EvidenceInputError("SECURE_READ_UNAVAILABLE") from None


def _read_all(file_fd: int, limit: int) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    before = os.fstat(file_fd)
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceInputError("PATH_UNSAFE")
    if before.st_size < 0 or before.st_size > limit:
        raise EvidenceInputError("INPUT_LIMIT_EXCEEDED")
    os.lseek(file_fd, 0, os.SEEK_SET)
    content = bytearray()
    while True:
        block = os.read(file_fd, min(1024 * 1024, limit + 1 - len(content)))
        if not block:
            break
        content.extend(block)
        if len(content) > limit:
            raise EvidenceInputError("INPUT_LIMIT_EXCEEDED")
    after = os.fstat(file_fd)
    if _file_identity(before) != _file_identity(after) or len(content) != after.st_size:
        raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
    return bytes(content), _file_identity(after)


def _decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise EvidenceInputError("INVALID_UTF8") from None
    if any(
        unicodedata.category(character) == "Cc"
        and character not in "\r\n\t"
        for character in text
    ):
        raise EvidenceInputError("INVALID_UTF8")
    return text


def _candidate_ranges(content: bytes) -> list[tuple[int, int]]:
    if not content:
        return []
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(content):
        end = min(start + WINDOW_BYTES, len(content))
        while end < len(content) and content[end] & 0xC0 == 0x80:
            end -= 1
        if end <= start:
            raise EvidenceInputError("INVALID_UTF8")
        ranges.append((start, end))
        if end == len(content):
            break
        next_start = max(start + 1, end - OVERLAP_BYTES)
        while next_start < len(content) and content[next_start] & 0xC0 == 0x80:
            next_start += 1
        start = next_start
    return ranges


def _tokens(text: str) -> Counter[str]:
    scanned = _scan_projection(text)
    tokens: Counter[str] = Counter(_LATIN_TOKEN.findall(scanned))
    for match in _CJK_RUN.finditer(scanned):
        run = match.group(0)
        if len(run) == 1:
            tokens[run] += 1
        else:
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


class LocalLexicalSelector:
    """Deterministic local relevance scorer; no service or model is called."""

    def select(self, query: str, candidates: Sequence[CandidateView]) -> Sequence[str]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return ()
        scored: list[tuple[int, str]] = []
        for candidate in candidates:
            content_tokens = _tokens(candidate.text)
            score = sum(
                min(content_tokens[token], 3) * (2 if len(token) == 2 and any(
                    "\u3400" <= character <= "\u9fff" or "\u3040" <= character <= "\u30ff"
                    for character in token
                ) else 1)
                for token in query_tokens
                if token in content_tokens
            )
            if score > 0:
                scored.append((score, candidate.candidate_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(candidate_id for _, candidate_id in scored[:MAX_SELECTED])


class _SimpleLocalFallback:
    """Smaller deterministic fallback used if the primary scorer fails."""

    def select(self, query: str, candidates: Sequence[CandidateView]) -> Sequence[str]:
        query_tokens = set(_tokens(query))
        scored = []
        for candidate in candidates:
            score = len(query_tokens.intersection(_tokens(candidate.text)))
            if score:
                scored.append((score, candidate.candidate_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(candidate_id for _, candidate_id in scored[:MAX_SELECTED])


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _read_sources(
    root_fd: int,
    root_path: str,
    paths: list[tuple[str, bytes]],
    backend: str,
) -> list[_SourceSnapshot]:
    snapshots: list[_SourceSnapshot] = []
    total_bytes = 0
    for relative_path, relative_bytes in paths:
        parent_fd, file_fd, _name = _open_relative_file(root_fd, relative_bytes, backend)
        try:
            resolved_path = _resolved_file_path(file_fd, root_path, backend)
            if _contains_sensitive_content(
                relative_path, is_path_field=True
            ) or (resolved_path is not None and _contains_sensitive_content(
                resolved_path, is_path_field=True
            )):
                raise EvidenceInputError("SENSITIVE_INPUT_BLOCKED", status="BLOCKED")
            content, identity = _read_all(file_fd, MAX_FILE_BYTES)
        finally:
            os.close(file_fd)
            os.close(parent_fd)
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            raise EvidenceInputError("INPUT_LIMIT_EXCEEDED")
        text = _decode_text(content)
        if _contains_sensitive_content(text):
            raise EvidenceInputError("SENSITIVE_INPUT_BLOCKED", status="BLOCKED")
        snapshots.append(
            _SourceSnapshot(
                source_ref=f"F{len(snapshots) + 1:04d}",
                relative_path=relative_path,
                relative_bytes=relative_bytes,
                resolved_path=resolved_path,
                identity=identity,
                content=content,
                content_sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return snapshots


def _build_candidates(
    snapshots: list[_SourceSnapshot],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for source_index, snapshot in enumerate(snapshots):
        for start, end in _candidate_ranges(snapshot.content):
            if len(candidates) >= MAX_CANDIDATES:
                raise EvidenceInputError("INPUT_LIMIT_EXCEEDED")
            raw = snapshot.content[start:end]
            try:
                text = raw.decode("utf-8", "strict")
            except UnicodeDecodeError:
                raise EvidenceInputError("INVALID_UTF8") from None
            candidate_id = f"C{len(candidates) + 1:04d}"
            candidates.append(
                _Candidate(
                    view=CandidateView(candidate_id, text),
                    source_index=source_index,
                    start_byte=start,
                    end_byte=end,
                    content_sha256=hashlib.sha256(raw).hexdigest(),
                )
            )
    return candidates


def _valid_selection(value: object, known_ids: set[str]) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_SELECTED:
        raise ValueError("invalid selector result")
    if any(not isinstance(item, str) or item not in known_ids for item in value):
        raise ValueError("invalid selector result")
    if len(set(value)) != len(value):
        raise ValueError("invalid selector result")
    return list(value)


def _select(
    query: str,
    candidates: list[_Candidate],
    primary: LocalCandidateSelector,
    fallback: LocalCandidateSelector,
) -> tuple[list[str], str, str | None]:
    known_ids = {candidate.view.candidate_id for candidate in candidates}
    views = tuple(candidate.view for candidate in candidates)
    try:
        selected = _valid_selection(primary.select(query, views), known_ids)
        return selected, "LOCAL_FILTER", None
    except Exception:
        try:
            selected = _valid_selection(fallback.select(query, views), known_ids)
            return selected, "LOCAL_FALLBACK", "SELECTOR_ERROR"
        except Exception:
            raise EvidenceInputError("SELECTOR_ERROR") from None


def _verify_snapshots(
    root_fd: int,
    root_path: str,
    snapshots: list[_SourceSnapshot],
    backend: str,
) -> None:
    for snapshot in snapshots:
        parent_fd, file_fd, _name = _open_relative_file(
            root_fd, snapshot.relative_bytes, backend
        )
        try:
            if snapshot.resolved_path is not None and _resolved_file_path(file_fd, root_path, backend) != snapshot.resolved_path:
                raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
            current, identity = _read_all(file_fd, MAX_FILE_BYTES)
            if (
                identity != snapshot.identity
                or hashlib.sha256(current).hexdigest() != snapshot.content_sha256
            ):
                raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
        finally:
            os.close(file_fd)
            os.close(parent_fd)


def _readback(
    root_fd: int,
    root_path: str,
    snapshots: list[_SourceSnapshot],
    candidates: list[_Candidate],
    selected_ids: list[str],
    backend: str,
) -> list[dict[str, object]]:
    selected = {candidate_id for candidate_id in selected_ids}
    by_source: dict[int, list[_Candidate]] = {}
    for candidate in candidates:
        if candidate.view.candidate_id in selected:
            by_source.setdefault(candidate.source_index, []).append(candidate)
    items: list[dict[str, object]] = []
    for source_index in sorted(by_source):
        snapshot = snapshots[source_index]
        parent_fd, file_fd, _name = _open_relative_file(root_fd, snapshot.relative_bytes, backend)
        try:
            if snapshot.resolved_path is not None and _resolved_file_path(file_fd, root_path, backend) != snapshot.resolved_path:
                raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
            current, identity = _read_all(file_fd, MAX_FILE_BYTES)
            if identity != snapshot.identity or hashlib.sha256(current).hexdigest() != snapshot.content_sha256:
                raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
            for candidate in by_source[source_index]:
                os.lseek(file_fd, candidate.start_byte, os.SEEK_SET)
                raw = bytearray()
                remaining = candidate.end_byte - candidate.start_byte
                while remaining:
                    block = os.read(file_fd, remaining)
                    if not block:
                        raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
                    raw.extend(block)
                    remaining -= len(block)
                if hashlib.sha256(raw).hexdigest() != candidate.content_sha256:
                    raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
                try:
                    text = bytes(raw).decode("utf-8", "strict")
                except UnicodeDecodeError:
                    raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK") from None
                items.append({
                    "source_ref": snapshot.source_ref,
                    "candidate_id": candidate.view.candidate_id,
                    "byte_start": candidate.start_byte,
                    "byte_end": candidate.end_byte,
                    "text": text,
                })
            if _file_identity(os.fstat(file_fd)) != snapshot.identity:
                raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
        finally:
            os.close(file_fd)
            os.close(parent_fd)
    items.sort(key=lambda item: str(item["candidate_id"]))
    return items


def screen_project_files(
    query: str,
    files: list[str],
    *,
    enabled: bool = True,
) -> dict[str, object]:
    """Screen relative project files and return only verified source excerpts."""

    if not isinstance(enabled, bool):
        return _result("UNKNOWN", "UNKNOWN", fallback_reason="INPUT_SCHEMA_INVALID")
    if not enabled:
        return _result("OK", "BYPASS", fallback_reason="DISABLED")
    try:
        if (
            not isinstance(query, str)
            or not query
            or len(query) > MAX_QUERY_CHARS
            or not isinstance(files, list)
            or len(files) > MAX_FILES
        ):
            raise EvidenceInputError("INPUT_SCHEMA_INVALID")
        try:
            query.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise EvidenceInputError("INPUT_SCHEMA_INVALID") from None
        paths = [_validate_relative_path(path) for path in files]
        if len({path for path, _ in paths}) != len(paths):
            raise EvidenceInputError("PATH_UNSAFE")
        if _contains_sensitive_content(query):
            raise EvidenceInputError("SENSITIVE_INPUT_BLOCKED", status="BLOCKED")
        root_fd, root_path, root_identity, backend = _open_root(".")
        try:
            if _contains_sensitive_content(root_path, is_path_field=True):
                raise EvidenceInputError("SENSITIVE_INPUT_BLOCKED", status="BLOCKED")
            snapshots = _read_sources(root_fd, root_path, paths, backend)
            candidates = _build_candidates(snapshots)
            selected_ids: list[str] = []
            mode = "LOCAL_FILTER"
            fallback_reason = None
            if candidates:
                selected_ids, mode, fallback_reason = _select(
                    query,
                    candidates,
                    LocalLexicalSelector(),
                    _SimpleLocalFallback(),
                )
            try:
                if not candidates:
                    _verify_snapshots(root_fd, root_path, snapshots, backend)
                    if _file_identity(os.fstat(root_fd)) != root_identity:
                        raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
                    return _result(
                        "OK",
                        mode,
                        fallback_reason="NO_CANDIDATE_WINDOWS",
                    )
                if not selected_ids:
                    _verify_snapshots(root_fd, root_path, snapshots, backend)
                    if _file_identity(os.fstat(root_fd)) != root_identity:
                        raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
                    return _result(
                        "OK",
                        mode,
                        candidate_count=len(candidates),
                        fallback_reason=fallback_reason or "NO_LOCAL_MATCH",
                    )
                items = _readback(
                    root_fd, root_path, snapshots, candidates, selected_ids, backend
                )
                _verify_snapshots(root_fd, root_path, snapshots, backend)
                if _file_identity(os.fstat(root_fd)) != root_identity:
                    raise EvidenceInputError("SOURCE_CHANGED_DURING_READBACK")
            except EvidenceInputError as error:
                return _result(
                    "UNKNOWN",
                    "UNKNOWN",
                    candidate_count=len(candidates),
                    selected_count=len(selected_ids),
                    fallback_reason=error.reason,
                )
            return _result(
                "OK",
                mode,
                candidate_count=len(candidates),
                selected_count=len(selected_ids),
                readback_count=len(items),
                fallback_reason=fallback_reason,
                items=items,
            )
        finally:
            os.close(root_fd)
    except EvidenceInputError as error:
        return _result(
            error.status,
            "BLOCKED" if error.status == "BLOCKED" else "UNKNOWN",
            fallback_reason=error.reason,
        )
    except Exception:
        return _result("UNKNOWN", "UNKNOWN", fallback_reason="PATH_UNSAFE")


def _parse_request(raw: bytes) -> tuple[str, list[str], bool]:
    if len(raw) > MAX_JSON_BYTES:
        raise EvidenceInputError("INPUT_LIMIT_EXCEEDED")
    try:
        payload = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise EvidenceInputError("INPUT_SCHEMA_INVALID") from None
    if (
        not isinstance(payload, dict)
        or not set(payload).issubset({"query", "files", "enabled"})
        or not {"query", "files"}.issubset(payload)
    ):
        raise EvidenceInputError("INPUT_SCHEMA_INVALID")
    query, files, enabled = payload["query"], payload["files"], payload.get("enabled", True)
    if (
        not isinstance(query, str)
        or not query
        or len(query) > MAX_QUERY_CHARS
        or not isinstance(files, list)
        or len(files) > MAX_FILES
        or not isinstance(enabled, bool)
    ):
        raise EvidenceInputError("INPUT_SCHEMA_INVALID")
    try:
        query.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise EvidenceInputError("INPUT_SCHEMA_INVALID") from None
    for path in files:
        _validate_relative_path(path)
    if len(set(files)) != len(files):
        raise EvidenceInputError("PATH_UNSAFE")
    return query, files, enabled


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
    try:
        query, files, enabled = _parse_request(raw)
        result = screen_project_files(query, files, enabled=enabled)
    except EvidenceInputError as error:
        result = _result(
            error.status,
            "BLOCKED" if error.status == "BLOCKED" else "UNKNOWN",
            fallback_reason=error.reason,
        )
    except Exception:
        result = _result("UNKNOWN", "UNKNOWN", fallback_reason="INPUT_SCHEMA_INVALID")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if result["status"] == "OK" else 3


if __name__ == "__main__":
    raise SystemExit(main())
