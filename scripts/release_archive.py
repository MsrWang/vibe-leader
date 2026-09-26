#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build and consume the fixed Vibe Leader release format using only stdlib."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
from typing import Any
import unicodedata
import zipfile


ARCHIVE_NAME = "Vibe-Leader-3.1.0-GitHub.zip"
SCRIPT_ASSET_NAME = "release_archive.py"
CHECKSUM_NAME = "SHA256SUMS.txt"
TOP_LEVEL = "Vibe-Leader-3.1.0"
RELEASE_MANIFEST_NAME = "RELEASE-MANIFEST.json"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILE_COUNT = 500
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SKILL_PATH = "skill/vibe-project-lead-zh/SKILL.md"
SCRIPT_PATH = "scripts/release_archive.py"


def collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _path_parts(path: str) -> list[str]:
    if (
        not isinstance(path, str) or not path or "\\" in path or ":" in path
        or any(unicodedata.category(char).startswith("C") for char in path)
    ):
        raise ValueError("unsafe_path")
    parts = path.split("/")
    if any(part in ("", ".", "..") or part.rstrip(" .") != part or collision_key(part) == ".git" for part in parts):
        raise ValueError("unsafe_path")
    return parts


def _validate_paths(paths: list[str]) -> None:
    # Include implicit directories: A/one and a/two also collide on macOS.
    names: dict[str, tuple[str, bool]] = {}
    for path in paths:
        parts = _path_parts(path)
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            key = collision_key(prefix)
            is_file = index == len(parts)
            previous = names.get(key)
            if previous is not None and (previous != (prefix, False) or is_file):
                raise ValueError("path_collision")
            names[key] = (prefix, is_file)


def _object_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _file_record(path: str, mode: int, content: bytes) -> dict[str, Any]:
    return {"path": path, "mode": mode, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _git(repo: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("git_read_failed") from error
    return result.stdout


def _write_exclusive(parent_fd: int, name: str, content: bytes, mode: int) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent_fd)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fchmod(stream.fileno(), mode)


def build_archive(repo: Path, commit: str, output_dir: Path) -> dict[str, Any]:
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("dirty_repository")
    if not commit or commit.startswith("-"):
        raise ValueError("invalid_commit")
    resolved = _git(repo, "rev-parse", "--verify", commit + "^{commit}").decode("ascii").strip()
    if not _object_id(resolved):
        raise ValueError("invalid_commit")
    files: dict[str, tuple[int, bytes]] = {}
    total = 0
    for entry in _git(repo, "ls-tree", "-rz", "--full-tree", resolved).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split(b" ")
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError("unsafe_git_entry")
        path = raw_path.decode("utf-8")
        _path_parts(path)
        content = _git(repo, "cat-file", "blob", object_id.decode("ascii"))
        total += len(content)
        if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(files) + 2 > MAX_FILE_COUNT:
            raise ValueError("release_limit_exceeded")
        files[path] = (int(mode, 8) & 0o777, content)
    if RELEASE_MANIFEST_NAME in files or SCRIPT_PATH not in files:
        raise ValueError("release_layout_invalid")
    if [path for path in files if path.split("/")[-1] == "SKILL.md"] != [SKILL_PATH]:
        raise ValueError("skill_inventory_not_unique")
    manifest = {
        "format_version": FORMAT_VERSION,
        "source_commit": resolved,
        "top_level": TOP_LEVEL,
        "files": [_file_record(path, *files[path]) for path in sorted(files, key=lambda value: value.encode("utf-8"))],
    }
    manifest_bytes = _json_bytes(manifest)
    if len(manifest_bytes) > MAX_FILE_BYTES or total + len(manifest_bytes) > MAX_TOTAL_BYTES:
        raise ValueError("release_limit_exceeded")
    files[RELEASE_MANIFEST_NAME] = (0o644, manifest_bytes)
    _validate_paths(list(files))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for path in sorted(files, key=lambda value: value.encode("utf-8")):
            mode, content = files[path]
            info = zipfile.ZipInfo(TOP_LEVEL + "/" + path, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.extra = b""
            info.comment = b""
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, content)
    archive_bytes = buffer.getvalue()
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("release_limit_exceeded")
    assets = {ARCHIVE_NAME: archive_bytes, SCRIPT_ASSET_NAME: files[SCRIPT_PATH][1]}
    assets[CHECKSUM_NAME] = b"".join(
        hashlib.sha256(content).hexdigest().encode("ascii") + b"  " + name.encode("ascii") + b"\n"
        for name, content in assets.items()
    )
    descriptor = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in assets:
            try:
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError("output_exists")
        for name, content in assets.items():
            _write_exclusive(descriptor, name, content, 0o755 if name == SCRIPT_ASSET_NAME else 0o644)
    finally:
        os.close(descriptor)
    return {"status": "BUILT", "source_commit": resolved, "archive": ARCHIVE_NAME, "file_count": len(files)}


def _read_regular(path: Path, maximum: int, reason: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise ValueError(reason)
            content = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if len(content) > maximum or len(content) != after.st_size or any(
                getattr(before, field) != getattr(after, field) for field in fields
            ):
                raise ValueError(reason)
            return content
    except OSError as error:
        raise ValueError(reason) from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifest_invalid")
        result[key] = value
    return result


def _check_manifest(content: bytes, files: dict[str, tuple[int, bytes]]) -> dict[str, Any]:
    try:
        manifest = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"format_version", "source_commit", "top_level", "files"}
            or type(manifest["format_version"]) is not int
            or manifest["format_version"] != FORMAT_VERSION
            or manifest["top_level"] != TOP_LEVEL
            or not _object_id(manifest["source_commit"])
            or not isinstance(manifest["files"], list)
        ):
            raise ValueError("manifest_invalid")
        expected: dict[str, dict[str, Any]] = {}
        for record in manifest["files"]:
            if (
                not isinstance(record, dict) or set(record) != {"path", "mode", "size", "sha256"}
                or not isinstance(record["path"], str)
                or type(record["mode"]) is not int or record["mode"] not in (0o644, 0o755)
                or type(record["size"]) is not int or not 0 <= record["size"] <= MAX_FILE_BYTES
                or not isinstance(record["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
                or record["path"] in expected
            ):
                raise ValueError("manifest_invalid")
            expected[record["path"]] = record
        _validate_paths(list(expected))
        actual = {path: _file_record(path, mode, data) for path, (mode, data) in files.items() if path != RELEASE_MANIFEST_NAME}
        if expected != actual:
            raise ValueError("manifest_invalid")
        return manifest
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValueError("manifest_invalid") from error


def _verified_archive(
    archive: Path, checksums: Path, expected_commit: str | None,
) -> tuple[dict[str, Any], dict[str, tuple[int, bytes]]]:
    # Keep these exact bytes through extraction; never reopen an already verified ZIP.
    content = _read_regular(archive, MAX_ARCHIVE_BYTES, "archive_limit_exceeded")
    script = _read_regular(Path(__file__), MAX_FILE_BYTES, "script_unavailable")
    archive_digest = hashlib.sha256(content).hexdigest()
    script_digest = hashlib.sha256(script).hexdigest()
    expected_checksums = (
        f"{archive_digest}  {ARCHIVE_NAME}\n{script_digest}  {SCRIPT_ASSET_NAME}\n"
    ).encode("ascii")
    if _read_regular(checksums, 1024, "checksum_mismatch") != expected_checksums:
        raise ValueError("checksum_mismatch")
    if expected_commit is not None and not _object_id(expected_commit):
        raise ValueError("source_commit_mismatch")
    files: dict[str, tuple[int, bytes]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as opened:
            infos = opened.infolist()
            if len(infos) > MAX_FILE_COUNT:
                raise ValueError("archive_limit_exceeded")
            # Validate every header and every path before reading any entry body.
            total = 0
            paths = []
            for info in infos:
                if info.orig_filename != info.filename:
                    raise ValueError("unsafe_path")
                paths.append(info.filename)
                if (
                    info.create_system != 3
                    or info.external_attr >> 16 not in (stat.S_IFREG | 0o644, stat.S_IFREG | 0o755)
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.flag_bits & ~0x800
                    or info.compress_size != info.file_size
                ):
                    raise ValueError("unsafe_zip_entry")
                total += info.file_size
                if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise ValueError("archive_limit_exceeded")
            _validate_paths(paths)
            if any(not path.startswith(TOP_LEVEL + "/") for path in paths):
                raise ValueError("release_layout_invalid")
            relatives = [path[len(TOP_LEVEL) + 1:] for path in paths]
            if [path for path in relatives if path.split("/")[-1] == "SKILL.md"] != [SKILL_PATH]:
                raise ValueError("skill_inventory_not_unique")
            if RELEASE_MANIFEST_NAME not in relatives:
                raise ValueError("manifest_invalid")
            # ZIP consumers differ on whether local or central flags win. Accept
            # only matching headers, including the local name, CRC and sizes.
            for info in infos:
                offset = info.header_offset
                if offset < 0 or offset + 30 > len(content):
                    raise ValueError("unsafe_zip_entry")
                header = struct.unpack_from("<4s5H3I2H", content, offset)
                signature, _, flags, method, _, _, crc, compressed, size, name_size, extra_size = header
                name_start = offset + 30
                body_start = name_start + name_size + extra_size
                name = content[name_start:name_start + name_size]
                expected_name = info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "cp437")
                if (
                    signature != b"PK\x03\x04" or flags != info.flag_bits or method != info.compress_type
                    or crc != info.CRC or compressed != info.compress_size or size != info.file_size
                    or name != expected_name or body_start + size > opened.start_dir
                ):
                    raise ValueError("unsafe_zip_entry")
            for info, path in zip(infos, relatives):
                data = opened.read(info)
                if len(data) != info.file_size:
                    raise ValueError("unsafe_zip_entry")
                files[path] = (stat.S_IMODE(info.external_attr >> 16), data)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
        raise ValueError("unsafe_zip_entry") from error
    manifest_mode, manifest_bytes = files[RELEASE_MANIFEST_NAME]
    if manifest_mode != 0o644:
        raise ValueError("manifest_invalid")
    manifest = _check_manifest(manifest_bytes, files)
    if expected_commit is not None and manifest["source_commit"] != expected_commit:
        raise ValueError("source_commit_mismatch")
    if SCRIPT_PATH not in files or files[SCRIPT_PATH][1] != script:
        raise ValueError("script_archive_mismatch")
    return {
        "status": "VERIFIED", "source_commit": manifest["source_commit"],
        "file_count": len(files), "archive_sha256": archive_digest, "script_sha256": script_digest,
    }, files


def verify_archive(archive: Path, checksums: Path, *, expected_commit: str | None) -> dict[str, Any]:
    report, _ = _verified_archive(archive, checksums, expected_commit)
    return report


def extract_archive(
    archive: Path, checksums: Path, destination: Path, *, expected_commit: str | None,
) -> dict[str, Any]:
    report, files = _verified_archive(archive, checksums, expected_commit)
    destination.mkdir(mode=0o700)
    root_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created_directories: set[str] = set()
    try:
        os.fchmod(root_fd, 0o700)
        for path in sorted(files, key=lambda value: value.encode("utf-8")):
            parts = [TOP_LEVEL, *_path_parts(path)]
            parent_fd = os.dup(root_fd)
            try:
                for index, component in enumerate(parts[:-1], 1):
                    prefix = "/".join(parts[:index])
                    if prefix not in created_directories:
                        os.mkdir(component, mode=0o755, dir_fd=parent_fd)
                        created_directories.add(prefix)
                    next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = next_fd
                    os.fchmod(parent_fd, 0o755)
                mode, content = files[path]
                _write_exclusive(parent_fd, parts[-1], content, mode)
            finally:
                os.close(parent_fd)
    finally:
        os.close(root_fd)
    return {**report, "status": "EXTRACTED"}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid_arguments")


def main() -> int:
    try:
        parser = _Parser(add_help=False)
        commands = parser.add_subparsers(dest="command", required=True)
        build = commands.add_parser("build", add_help=False)
        build.add_argument("--repo", type=Path, required=True)
        build.add_argument("--commit", required=True)
        build.add_argument("--output-dir", type=Path, required=True)
        for command in ("verify", "extract"):
            subparser = commands.add_parser(command, add_help=False)
            subparser.add_argument("--archive", type=Path, required=True)
            subparser.add_argument("--checksums", type=Path, required=True)
            subparser.add_argument("--expected-commit", required=True)
            if command == "extract":
                subparser.add_argument("--destination", type=Path, required=True)
        arguments = parser.parse_args()
        if arguments.command == "build":
            result = build_archive(arguments.repo, arguments.commit, arguments.output_dir)
        elif arguments.command == "verify":
            result = verify_archive(arguments.archive, arguments.checksums, expected_commit=arguments.expected_commit)
        else:
            result = extract_archive(arguments.archive, arguments.checksums, arguments.destination, expected_commit=arguments.expected_commit)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        # Never print exception text from filesystem, Git, ZIP or argument parsing.
        reason = str(error) if type(error) is ValueError and re.fullmatch(r"[a-z_]+", str(error)) else "release_operation_failed"
        print(json.dumps({"status": "REJECTED", "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
