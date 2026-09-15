# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import base64
import copy
import importlib.util
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path, PureWindowsPath
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_skill.py"
SKILL_NAME = "vibe-project-lead-zh"
EXPECTED_RUNTIME_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/project-binding.md",
    "references/manager-workflow.md",
    "references/safety-gates.md",
    "references/acceptance-and-supervision.md",
    "references/adaptive-delegation.md",
    "references/portfolio.md",
    "references/deployment-governance.md",
    "references/human-delivery.md",
    "references/SKILL_INDEX_ZH.md",
}

SPEC = importlib.util.spec_from_file_location("install_skill", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load installer from {SCRIPT}")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
WSLPATH = Path("/usr/bin/wslpath")
WINDOWS_POWERSHELL = Path(
    "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"
)
WINDOWS_MOVE_AVAILABLE = WSLPATH.is_file() and WINDOWS_POWERSHELL.is_file()
WINDOWS_INTEROP_ENABLED = (
    WINDOWS_MOVE_AVAILABLE
    and os.environ.get("VIBE_RUN_WINDOWS_NOREPLACE_INTEROP") == "1"
)
WINDOWS_INTEROP_REQUIRED = (
    os.environ.get("VIBE_REQUIRE_WINDOWS_NOREPLACE_INTEROP") == "1"
)


def windows_drive_temp_root() -> Path | None:
    supplied = os.environ.get("VIBE_INSTALL_DRVFS_TEST_ROOT")
    if supplied is not None:
        root = Path(supplied)
        absolute = Path(os.path.abspath(root))
        try:
            metadata = os.lstat(absolute)
            resolved = absolute.resolve(strict=True)
        except OSError:
            return None
        if (
            not root.is_absolute()
            or root != absolute
            or resolved != absolute
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            return None
        return absolute
    if (
        not WINDOWS_INTEROP_ENABLED
        or WINDOWS_INTEROP_REQUIRED
        or not WINDOWS_MOVE_AVAILABLE
    ):
        return None
    try:
        windows_result = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                    "[IO.Path]::GetTempPath()"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        windows_path = windows_result.stdout.strip()
        parsed = PureWindowsPath(windows_path)
        if (
            windows_result.returncode != 0
            or not parsed.is_absolute()
            or len(parsed.drive) != 2
            or parsed.drive[1] != ":"
        ):
            return None
        wsl_result = subprocess.run(
            [str(WSLPATH), "-u", "--", windows_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    root = Path(wsl_result.stdout.strip())
    if wsl_result.returncode != 0 or not root.is_absolute() or not root.is_dir():
        return None
    return root


def validated_cross_filesystem_test_roots():
    """Bind explicit, disjoint disposable/protected roots without creating files."""
    effective_home = os.environ.get("CODEX_HOME")
    if (
        not effective_home
        or os.environ.get("VIBE_INSTALL_PROTECTED_CODEX_HOME") != effective_home
    ):
        raise AssertionError("CROSS_FILESYSTEM_ROOT_UNSAFE")
    roots = {}
    for role, variable in (
        ("disposable", "VIBE_INSTALL_DRVFS_TEST_ROOT"),
        ("protected", "VIBE_INSTALL_PROTECTED_CODEX_HOME"),
    ):
        raw = os.environ.get(variable)
        if not raw or not raw.strip():
            raise AssertionError("CROSS_FILESYSTEM_ROOT_UNSAFE")
        path = Path(raw)
        if (
            not path.is_absolute()
            or raw != str(path)
            or path != Path(os.path.abspath(path))
        ):
            raise AssertionError("CROSS_FILESYSTEM_ROOT_UNSAFE")
        try:
            before = path.lstat()
            resolved = path.resolve(strict=True)
            after = path.lstat()
        except (OSError, ValueError, RuntimeError):
            raise AssertionError("CROSS_FILESYSTEM_ROOT_UNSAFE") from None
        identity = (before.st_dev, before.st_ino, before.st_mode)
        if (
            resolved != path
            or not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or identity != (after.st_dev, after.st_ino, after.st_mode)
        ):
            raise AssertionError("CROSS_FILESYSTEM_ROOT_UNSAFE")
        roots[role] = (path, *identity)
    disposable = roots["disposable"][0]
    protected = roots["protected"][0]
    if (
        disposable == protected
        or disposable.is_relative_to(protected)
        or protected.is_relative_to(disposable)
    ):
        raise AssertionError("CROSS_FILESYSTEM_ROOT_UNSAFE")
    return roots


def valid_backend_evidence_fixture(skills_root):
    skills_root = Path(skills_root)
    probe_root = skills_root / (
        ".vibe-project-lead-zh-switch-probe-" + "2" * 32
    )
    source = {
        "type": "directory",
        "mode": 0o700,
        "device": 7,
        "inode": 11,
        "size": 4096,
        "nlink": 1,
        "mtime_ns": 100,
        "tree_digest": "a" * 64,
    }
    collision = dict(source)
    collision.update({"inode": 12, "tree_digest": "b" * 64})
    installer_sha256 = INSTALLER.current_installer_sha256()
    value = {
        "schema_version": 1,
        "evidence_id": "1" * 32,
        "approval_id": "ATTEST-UNIT-001",
        "backend": "WINDOWS_MOVEFILEEX_NOREPLACE",
        "capability": "NOREPLACE_ONLY",
        "installer_sha256": installer_sha256,
        "backend_implementation_digest": INSTALLER.backend_implementation_digest(
            "WINDOWS_MOVEFILEEX_NOREPLACE",
            installer_sha256,
        ),
        "skills_root": str(skills_root),
        "skills_root_stable_identity": {
            "type": "directory",
            "device": 7,
            "inode": 10,
        },
        "target_filesystem_identity": {
            "device": 7,
            "mount_target": "/mnt/c",
            "filesystem_type": "9p",
            "mount_options_sha256": "c" * 64,
        },
        "wsl_mount_identity": {
            "mount_target": "/mnt/c",
            "filesystem_type": "9p",
            "mount_options_sha256": "c" * 64,
        },
        "windows_volume_identity": {
            "drive": "C:",
            "filesystem_name": "NTFS",
            "volume_serial": "12AB34CD",
        },
        "probe_identity": {
            "probe_id": "2" * 32,
            "probe_root": str(probe_root),
            "source_relative": "source",
            "forward_destination_relative": "forward",
            "collision_destination_relative": "collision",
            "source_marker_sha256": "d" * 64,
            "collision_marker_sha256": "e" * 64,
        },
        "forward_move": {
            "status": "VERIFIED",
            "call_count": 1,
            "return_classification": "VERIFIED",
            "win32_error": 0,
            "source_before": source,
            "destination_before": None,
            "source_after": None,
            "destination_after": source,
        },
        "collision_guard": {
            "status": "VERIFIED",
            "call_count": 1,
            "return_classification": "TARGET_EXISTS",
            "win32_error": 183,
            "source_before": source,
            "destination_before": collision,
            "source_after": source,
            "destination_after": collision,
        },
        "restore_move": {
            "status": "VERIFIED",
            "call_count": 1,
            "return_classification": "VERIFIED",
            "win32_error": 0,
            "source_before": source,
            "destination_before": None,
            "source_after": None,
            "destination_after": source,
        },
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
        "created_at_utc": "2026-09-07T00:00:00Z",
    }
    value["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(value)
    return value


class InstallSkillTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.tempdir = Path(self._tempdir.name)
        self.codex_home = self.tempdir / "codex-home"
        self.skills_root = self.codex_home / "skills"
        self.skills_root.mkdir(parents=True)
        self.source = self.tempdir / "source" / SKILL_NAME
        (self.source / "agents").mkdir(parents=True)
        (self.source / "references").mkdir()
        (self.source / "SKILL.md").write_text(
            "---\nname: vibe-project-lead-zh\ndescription: test\n---\n",
            encoding="utf-8",
        )
        (self.source / "agents" / "openai.yaml").write_text(
            'interface:\n  display_name: "中文跨项目研发主管"\n',
            encoding="utf-8",
        )
        for filename in (
            "project-binding.md",
            "manager-workflow.md",
            "safety-gates.md",
            "acceptance-and-supervision.md",
            "adaptive-delegation.md",
            "portfolio.md",
            "deployment-governance.md",
            "human-delivery.md",
            "SKILL_INDEX_ZH.md",
        ):
            (self.source / "references" / filename).write_text(
                f"# {filename}\n",
                encoding="utf-8",
            )
        actual_files = {
            path.relative_to(self.source).as_posix()
            for path in self.source.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual_files, EXPECTED_RUNTIME_FILES)
        self.target = self.skills_root / SKILL_NAME
        self.state_dir = self.skills_root / f".{SKILL_NAME}-install"
        self.prepared_manifest = self.state_dir / "prepared-manifest.json"
        self.manifest = self.state_dir / "install-manifest.json"
        self.config_marker = self.tempdir / "config.toml"
        self.config_marker.write_text("preserve = true\n", encoding="utf-8")
        self.neighbor = self.skills_root / "existing-skill"
        self.neighbor.mkdir()
        (self.neighbor / "owned-by-user").write_text("keep", encoding="utf-8")

    def run_cli(self, *args, codex_home=None, timeout=None):
        environment = os.environ.copy()
        if codex_home is False:
            environment.pop("CODEX_HOME", None)
        else:
            environment["CODEX_HOME"] = str(codex_home or self.codex_home)
        return subprocess.run(
            [sys.executable, SCRIPT, *map(str, args)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=timeout,
        )

    def make_windows_drive_tempdir(self) -> Path:
        root = windows_drive_temp_root()
        if root is None:
            if WINDOWS_INTEROP_REQUIRED:
                raise AssertionError(
                    "required Windows noreplace interop root is unavailable"
                )
            self.skipTest("Windows drive-backed temporary root is NOT_APPLICABLE")
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="vibe-windows-move-",
                dir=root,
            )
        except OSError:
            self.skipTest("Windows drive-backed temporary root is NOT_APPLICABLE")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def install(self):
        return self.run_cli(
            "install",
            "--source",
            self.source,
            "--skills-root",
            self.skills_root,
        )

    def verify(self, target=None):
        return self.run_cli(
            "verify",
            "--target",
            target or self.target,
            "--manifest",
            self.manifest,
        )

    def manifest_payload(self):
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def write_manifest_payload(self, payload):
        payload["manifest_digest"] = INSTALLER.canonical_digest(payload)
        self.manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def result_payload(self, result):
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def rollback(self, confirmation):
        return self.run_cli(
            "rollback",
            "--target",
            self.target,
            "--manifest",
            self.manifest,
            "--confirm",
            confirmation,
        )

    def assert_neighbors_untouched(self):
        self.assertEqual(self.config_marker.read_text(encoding="utf-8"), "preserve = true\n")
        self.assertEqual((self.neighbor / "owned-by-user").read_text(encoding="utf-8"), "keep")

    def make_posix_source_and_stage(self, temporary):
        root = Path(temporary)
        source = root / "source" / SKILL_NAME
        shutil.copytree(self.source, source)
        source.chmod(0o755)
        (source / "agents").chmod(0o755)
        (source / "references").chmod(0o755)
        for child in source.rglob("*"):
            if child.is_file():
                child.chmod(
                    0o600
                    if child.name == "SKILL_INDEX_ZH.md"
                    else 0o644
                )
        skills_root = root / "codex-home" / "skills"
        skills_root.mkdir(parents=True)
        source_entries = INSTALLER.scan_tree(source)
        stage = skills_root / ".mode-probe-stage"
        stage.mkdir(mode=0o755)
        INSTALLER.copy_entries(source, stage, source_entries)
        return source, stage, skills_root, source_entries

    def make_versioned_source(self, label):
        destination = self.tempdir / label / SKILL_NAME
        shutil.copytree(self.source, destination)
        (destination / "references" / "SKILL_INDEX_ZH.md").write_text(
            f"# {label} index\n",
            encoding="utf-8",
        )
        return destination

    def test_existing_target_is_never_overwritten(self):
        self.target.mkdir()
        marker = self.target / "owned-by-user"
        marker.write_text("keep", encoding="utf-8")

        result = self.install()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "target_exists")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.manifest.exists())
        self.assert_neighbors_untouched()

    def test_install_requires_codex_home(self):
        result = self.run_cli(
            "install",
            "--source",
            self.source,
            "--skills-root",
            self.skills_root,
            codex_home=False,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "codex_home_not_set")

    def test_install_rejects_extra_runtime_file(self):
        (self.source / "references" / "extra.md").write_text(
            "extra\n",
            encoding="utf-8",
        )

        result = self.install()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            self.result_payload(result)["reason"],
            "unexpected_runtime_layout",
        )
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())

    def test_install_rejects_missing_runtime_file(self):
        (self.source / "references" / "safety-gates.md").unlink()

        result = self.install()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            self.result_payload(result)["reason"],
            "unexpected_runtime_layout",
        )
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())

    def test_install_rejects_directory_instead_of_runtime_file(self):
        locator = self.source / "references" / "manager-workflow.md"
        locator.unlink()
        locator.mkdir()

        result = self.install()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            self.result_payload(result)["reason"],
            "unexpected_runtime_layout",
        )
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())

    def test_install_rejects_skills_root_outside_codex_home(self):
        other_root = self.tempdir / "other-skills"
        other_root.mkdir(parents=True)

        result = self.run_cli(
            "install",
            "--source",
            self.source,
            "--skills-root",
            other_root,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "skills_root_mismatch")

    def test_install_rejects_codex_home_reached_through_symlinked_ancestor(self):
        real_parent = self.tempdir / "real-parent"
        aliased_parent = self.tempdir / "aliased-parent"
        real_home = real_parent / "codex-home"
        real_skills = real_home / "skills"
        real_skills.mkdir(parents=True)
        os.symlink(real_parent, aliased_parent, target_is_directory=True)
        aliased_home = aliased_parent / "codex-home"

        result = self.run_cli(
            "install",
            "--source",
            self.source,
            "--skills-root",
            aliased_home / "skills",
            codex_home=aliased_home,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "unsafe_codex_home")
        self.assertFalse((real_skills / SKILL_NAME).exists())

    def test_atomic_commit_never_replaces_existing_empty_directory(self):
        source = self.skills_root / ".commit-source"
        source.mkdir()
        payload = source / "payload"
        payload.write_text("new", encoding="utf-8")
        destination = self.skills_root / ".commit-target"
        destination.mkdir()

        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.rename_noreplace(source, destination)

        self.assertEqual(raised.exception.reason, "target_exists")
        self.assertEqual(payload.read_text(encoding="utf-8"), "new")
        self.assertEqual(list(destination.iterdir()), [])

    @unittest.skipUnless(os.name == "posix" and Path("/tmp").is_dir(), "requires POSIX /tmp")
    def test_linux_noreplace_backend_moves_directory(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "payload").write_text("new", encoding="utf-8")
            destination = root / "target"

            INSTALLER.rename_noreplace(source, destination)

            self.assertFalse(source.exists())
            self.assertEqual((destination / "payload").read_text(encoding="utf-8"), "new")

    @unittest.skipUnless(WINDOWS_INTEROP_ENABLED, "requires opted-in WSL Windows interop")
    def test_windows_fallback_moves_directory_without_overwriting(self):
        root = self.make_windows_drive_tempdir()
        source = root / ".windows-source"
        source.mkdir()
        (source / "payload").write_text("new", encoding="utf-8")
        destination = root / ".windows-target"

        INSTALLER.windows_move_noreplace(source, destination)

        self.assertFalse(source.exists())
        self.assertEqual((destination / "payload").read_text(encoding="utf-8"), "new")

    @unittest.skipUnless(WINDOWS_INTEROP_ENABLED, "requires opted-in WSL Windows interop")
    def test_windows_fallback_refuses_existing_empty_directory(self):
        root = self.make_windows_drive_tempdir()
        source = root / ".windows-source"
        source.mkdir()
        payload = source / "payload"
        payload.write_text("new", encoding="utf-8")
        destination = root / ".windows-target"
        destination.mkdir()

        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.windows_move_noreplace(source, destination)

        self.assertEqual(raised.exception.reason, "target_exists")
        self.assertEqual(payload.read_text(encoding="utf-8"), "new")
        self.assertEqual(list(destination.iterdir()), [])

    @unittest.skipUnless(WINDOWS_INTEROP_ENABLED, "requires opted-in WSL Windows interop")
    def test_windows_fallback_treats_powershell_metacharacters_as_path_data(self):
        root = self.make_windows_drive_tempdir()
        source = root / ".windows-$(exit 97);source"
        source.mkdir()
        (source / "payload").write_text("new", encoding="utf-8")
        destination = root / ".windows-$(exit 98);target"

        INSTALLER.windows_move_noreplace(source, destination)

        self.assertFalse(source.exists())
        self.assertEqual((destination / "payload").read_text(encoding="utf-8"), "new")

    def test_windows_ambiguous_results_are_unknown_without_retry(self):
        outcomes = [
            OSError("powershell failed to start"),
            subprocess.TimeoutExpired("powershell.exe", 15),
            subprocess.CompletedProcess([], 18),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                source = self.skills_root / f".missing-source-{type(outcome).__name__}"
                destination = self.skills_root / f".apparent-target-{type(outcome).__name__}"
                source.mkdir()
                destination.mkdir()
                with mock.patch.object(
                    INSTALLER,
                    "windows_drive_path",
                    side_effect=[r"\\?\C:\source", r"\\?\C:\target"],
                ):
                    with mock.patch.object(
                        INSTALLER.subprocess,
                        "run",
                        side_effect=outcome if isinstance(outcome, BaseException) else None,
                        return_value=None if isinstance(outcome, BaseException) else outcome,
                    ) as run:
                        with self.assertRaises(INSTALLER.InstallError) as raised:
                            INSTALLER.windows_move_noreplace(source, destination)

                self.assertEqual(raised.exception.reason, "atomic_move_outcome_unknown")
                self.assertEqual(run.call_count, 1)

    def test_windows_exit_zero_with_contradictory_postconditions_is_unknown_without_retry(self):
        source = self.skills_root / ".windows-source"
        source.mkdir()
        destination = self.skills_root / ".windows-target"

        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            side_effect=[r"\\?\C:\source", r"\\?\C:\target"],
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run:
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.windows_move_noreplace(source, destination)

        self.assertEqual(raised.exception.reason, "atomic_move_outcome_unknown")
        self.assertEqual(run.call_count, 1)

    def test_windows_exit_target_exists_with_contradictory_postconditions_is_unknown_without_retry(self):
        source = self.skills_root / ".windows-source"
        source.mkdir()
        destination = self.skills_root / ".windows-target"

        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            side_effect=[r"\\?\C:\source", r"\\?\C:\target"],
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], INSTALLER.WINDOWS_TARGET_EXISTS_EXIT
                ),
            ) as run:
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.windows_move_noreplace(source, destination)

        self.assertEqual(raised.exception.reason, "atomic_move_outcome_unknown")
        self.assertEqual(run.call_count, 1)

    def test_windows_structured_backend_moves_once(self):
        source = self.skills_root / ".structured-source"
        destination = self.skills_root / ".structured-target"
        source.mkdir()
        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            side_effect=[r"\\?\C:\source", r"\\?\C:\target"],
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=b"0\n",
                    stderr=b"",
                ),
            ) as run:
                with mock.patch.object(
                    INSTALLER,
                    "_windows_move_postconditions",
                    return_value="VERIFIED",
                ):
                    result = INSTALLER.windows_movefileex_noreplace(
                        source,
                        destination,
                    )
        self.assertEqual(result["classification"], "VERIFIED")
        self.assertEqual(result["movefileex_call_count"], 1)
        self.assertEqual(result["win32_error"], 0)
        self.assertEqual(run.call_count, 1)

    def test_windows_structured_backend_collision_is_not_success(self):
        source = self.skills_root / ".structured-source"
        destination = self.skills_root / ".structured-target"
        source.mkdir()
        destination.mkdir()
        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            side_effect=[r"\\?\C:\source", r"\\?\C:\target"],
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=b"183\n",
                    stderr=b"",
                ),
            ) as run:
                result = INSTALLER.windows_movefileex_noreplace(
                    source,
                    destination,
                )

        self.assertEqual(result["classification"], "TARGET_EXISTS")
        self.assertEqual(result["movefileex_call_count"], 1)
        self.assertEqual(result["win32_error"], 183)
        self.assertTrue(source.is_dir())
        self.assertTrue(destination.is_dir())
        self.assertEqual(run.call_count, 1)

    def test_windows_volume_identity_is_strict(self):
        source = self.skills_root
        valid = json.dumps(
            {
                "drive": "c:",
                "filesystem_name": "ntfs",
                "volume_serial": "12ab34cd",
            },
            separators=(",", ":"),
        ).encode("ascii")
        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            return_value=r"\\?\C:\skills",
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=valid,
                    stderr=b"",
                ),
            ) as run:
                identity = INSTALLER.windows_volume_identity(source)
        self.assertEqual(
            identity,
            {
                "drive": "C:",
                "filesystem_name": "NTFS",
                "volume_serial": "12AB34CD",
            },
        )
        self.assertEqual(run.call_count, 1)

        malformed = (
            b"not-json",
            b'{"drive":"D:","filesystem_name":"NTFS","volume_serial":"12AB34CD"}',
            b'{"drive":"C:","filesystem_name":"NTFS","volume_serial":"123"}',
            b'{"drive":"C:","filesystem_name":"NTFS","volume_serial":"12AB34CD","extra":1}',
        )
        for stdout in malformed:
            with self.subTest(stdout=stdout):
                with mock.patch.object(
                    INSTALLER,
                    "windows_drive_path",
                    return_value=r"\\?\C:\skills",
                ):
                    with mock.patch.object(
                        INSTALLER.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            [],
                            0,
                            stdout=stdout,
                            stderr=b"",
                        ),
                    ) as run:
                        with self.assertRaises(INSTALLER.InstallError) as raised:
                            INSTALLER.windows_volume_identity(source)
                self.assertEqual(
                    raised.exception.reason,
                    "windows_volume_identity_unknown",
                )
                self.assertEqual(run.call_count, 1)

    def test_windows_structured_backend_rejects_cross_drive_before_move(self):
        source = self.skills_root / ".structured-source"
        destination = self.skills_root / ".structured-target"
        source.mkdir()
        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            side_effect=[r"\\?\C:\source", r"\\?\D:\target"],
        ):
            with mock.patch.object(INSTALLER.subprocess, "run") as run:
                with self.assertRaises(INSTALLER.InstallError):
                    INSTALLER.windows_movefileex_noreplace(source, destination)
        run.assert_not_called()

    def test_windows_structured_backend_treats_metacharacters_as_base64_data(self):
        source = self.skills_root / ".structured-source"
        destination = self.skills_root / ".structured-target"
        source.mkdir()
        source_windows = r"\\?\C:\$(exit 97);source"
        destination_windows = r"\\?\C:\$(exit 98);target"
        with mock.patch.object(
            INSTALLER,
            "windows_drive_path",
            side_effect=[source_windows, destination_windows],
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=b"5\n",
                    stderr=b"",
                ),
            ) as run:
                result = INSTALLER.windows_movefileex_noreplace(
                    source,
                    destination,
                )

        self.assertEqual(result["classification"], "UNKNOWN")
        encoded_command = run.call_args.args[0][-1]
        script = base64.b64decode(encoded_command).decode("utf-16le")
        self.assertNotIn("$(exit 97)", script)
        self.assertNotIn("$(exit 98)", script)
        self.assertIn(
            base64.b64encode(source_windows.encode("utf-8")).decode("ascii"),
            script,
        )
        self.assertIn(
            base64.b64encode(destination_windows.encode("utf-8")).decode("ascii"),
            script,
        )

    def test_move_directory_for_backend_accepts_only_exact_backend(self):
        source = self.skills_root / ".structured-source"
        destination = self.skills_root / ".structured-target"
        source.mkdir()
        with mock.patch.object(INSTALLER, "windows_movefileex_noreplace") as move:
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.move_directory_for_backend(
                    "WINDOWS_MOVEFILEEX_REPLACE",
                    source,
                    destination,
                )
        self.assertEqual(
            raised.exception.reason,
            "switch_backend_not_implemented",
        )
        move.assert_not_called()

    def test_cli_reports_ambiguous_move_as_unknown(self):
        def ambiguous_move(source, destination):
            raise INSTALLER.InstallError("atomic_move_outcome_unknown", 4)

        arguments = [
            str(SCRIPT),
            "install",
            "--source",
            str(self.source),
            "--skills-root",
            str(self.skills_root),
        ]
        output = io.StringIO()
        with mock.patch.object(INSTALLER, "rename_noreplace", side_effect=ambiguous_move):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with mock.patch.object(sys, "argv", arguments):
                    with redirect_stdout(output):
                        return_code = INSTALLER.main()

        self.assertEqual(return_code, 4)
        self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
        self.assertFalse(self.target.exists())
        self.assertTrue(self.prepared_manifest.is_file())
        self.assertFalse(self.manifest.exists())
        stages = list(self.skills_root.glob(f".{SKILL_NAME}-stage-*"))
        self.assertEqual(len(stages), 1)
        self.assertEqual(INSTALLER.scan_tree(stages[0]), INSTALLER.scan_tree(self.source))

    def test_install_manifest_matches_every_file(self):
        result = self.install()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.target.is_dir())
        payload = self.manifest_payload()
        self.assertEqual(payload["phase"], "installed")
        prepared = json.loads(self.prepared_manifest.read_text(encoding="utf-8"))
        self.assertEqual(prepared["phase"], "prepared")
        self.assertEqual(payload["name"], SKILL_NAME)
        self.assertTrue(payload["manifest_digest"])
        self.assertIn("SKILL.md", payload["entries"])
        self.assertIn("agents", payload["entries"])
        expected_digest = hashlib.sha256(
            (self.target / "SKILL.md").read_bytes()
        ).hexdigest()
        self.assertEqual(payload["entries"]["SKILL.md"]["sha256"], expected_digest)
        self.assertEqual(self.verify().returncode, 0)
        self.assert_neighbors_untouched()

    def test_repository_runtime_package_installs_and_verifies(self):
        repository_source = ROOT / "skill" / SKILL_NAME
        source_files = {
            path.relative_to(repository_source).as_posix()
            for path in repository_source.rglob("*")
            if path.is_file()
        }
        self.assertEqual(source_files, EXPECTED_RUNTIME_FILES)

        installed = self.run_cli(
            "install",
            "--source",
            repository_source,
            "--skills-root",
            self.skills_root,
        )

        self.assertEqual(
            installed.returncode,
            0,
            installed.stdout + installed.stderr,
        )
        installed_files = {
            path.relative_to(self.target).as_posix()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(installed_files, source_files)
        verified = self.verify()
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_repository_candidate_conservative_upgrade_and_rollback_preserve_inputs(self):
        repository_source = ROOT / "skill" / SKILL_NAME
        repository_before = INSTALLER.scan_tree(repository_source)
        self.assertEqual(self.install().returncode, 0)
        old_manifest = self.manifest_payload()
        old_rollback = self.rollback(old_manifest["manifest_digest"])
        self.assertEqual(old_rollback.returncode, 0, old_rollback.stdout + old_rollback.stderr)
        old_archive = Path(json.loads(old_rollback.stdout)["recovery_directory"])
        archived_manifest = old_archive / self.state_dir.name / "install-manifest.json"
        archived_bytes = archived_manifest.read_bytes()
        old_archive_entries = INSTALLER.scan_tree(old_archive)

        installed = self.run_cli("install", "--source", repository_source,
                                 "--skills-root", self.skills_root)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        self.assertEqual(json.loads(installed.stdout)["files"], 11)
        verified = self.verify()
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        current_manifest = self.manifest_payload()
        self.assertEqual({p for p, e in current_manifest["entries"].items()
                          if e["type"] == "file"}, EXPECTED_RUNTIME_FILES)
        rolled_back = self.rollback(current_manifest["manifest_digest"])
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stdout + rolled_back.stderr)
        current_archive = Path(json.loads(rolled_back.stdout)["recovery_directory"])
        self.assertNotEqual(current_archive, old_archive)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())
        self.assertEqual(INSTALLER.scan_tree(current_archive / SKILL_NAME), current_manifest["entries"])
        self.assertEqual(INSTALLER.scan_tree(old_archive), old_archive_entries)
        self.assertEqual(archived_manifest.read_bytes(), archived_bytes)
        self.assertEqual(INSTALLER.scan_tree(repository_source), repository_before)
        self.assert_neighbors_untouched()

    def test_repository_candidate_missing_or_extra_file_is_refused_without_install(self):
        repository_source = ROOT / "skill" / SKILL_NAME
        repository_before = INSTALLER.scan_tree(repository_source)
        for kind in ("missing", "extra"):
            with self.subTest(kind=kind):
                candidate = self.tempdir / kind / SKILL_NAME
                shutil.copytree(repository_source, candidate)
                if kind == "missing":
                    (candidate / "references" / "human-delivery.md").unlink()
                else:
                    (candidate / "unexpected.md").write_text("synthetic extra file\n", encoding="utf-8")
                before = INSTALLER.scan_tree(self.codex_home)
                result = self.run_cli("install", "--source", candidate,
                                      "--skills-root", self.skills_root)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["reason"], "unexpected_runtime_layout")
                self.assertEqual(INSTALLER.scan_tree(self.codex_home), before)
                self.assertFalse(self.target.exists())
                self.assertFalse(self.state_dir.exists())
        self.assertEqual(INSTALLER.scan_tree(repository_source), repository_before)
        self.assert_neighbors_untouched()

    def test_new_install_writes_strict_v2_manifest(self):
        result = self.install()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = self.manifest_payload()
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual(value["mode_policy"], "strict")
        self.assertEqual(value["mode_capability"], {"status": "not_required"})
        self.assertEqual(value["source_entries"], value["entries"])
        self.assertEqual(value["source_root_mode"], value["target_root_mode"])
        self.assertEqual(set(value), INSTALLER.V2_INSTALL_MANIFEST_KEYS)

    def test_v1_manifest_remains_verifiable_and_is_not_rewritten(self):
        self.assertEqual(self.install().returncode, 0)
        installed = self.manifest_payload()
        legacy = {
            "schema_version": 1,
            "name": installed["name"],
            "source": installed["source"],
            "target": installed["target"],
            "phase": "installed",
            "installed_at_utc": installed["installed_at_utc"],
            "entries": installed["entries"],
        }
        self.write_manifest_payload(legacy)
        before = self.manifest.read_bytes()

        result = self.verify()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_v1_manifest_remains_rollback_compatible_and_unmodified(self):
        self.assertEqual(self.install().returncode, 0)
        installed = self.manifest_payload()
        legacy = {
            "schema_version": 1,
            "name": installed["name"],
            "source": installed["source"],
            "target": installed["target"],
            "phase": "installed",
            "installed_at_utc": installed["installed_at_utc"],
            "entries": installed["entries"],
        }
        self.write_manifest_payload(legacy)
        before = self.manifest.read_bytes()

        result = self.rollback(legacy["manifest_digest"])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archived = Path(self.result_payload(result)["recovery_directory"])
        archived_manifest = archived / self.state_dir.name / self.manifest.name
        self.assertEqual(archived_manifest.read_bytes(), before)

    def test_v2_verify_detects_target_root_mode_drift(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            codex_home = Path(temporary) / "codex-home"
            skills_root = codex_home / "skills"
            skills_root.mkdir(parents=True)
            target = skills_root / SKILL_NAME
            manifest = skills_root / f".{SKILL_NAME}-install" / "install-manifest.json"
            installed = self.run_cli(
                "install",
                "--source",
                self.source,
                "--skills-root",
                skills_root,
                codex_home=codex_home,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            original = target.stat().st_mode & 0o7777
            target.chmod(0o700 if original != 0o700 else 0o750)
            result = self.run_cli(
                "verify",
                "--target",
                target,
                "--manifest",
                manifest,
                codex_home=codex_home,
            )

        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("<root-mode>", self.result_payload(result)["differences"])

    def test_v2_manifest_rejects_unknown_or_inconsistent_fields(self):
        self.assertEqual(self.install().returncode, 0)
        entries = INSTALLER.scan_tree(self.target)
        root_mode = self.target.stat().st_mode & 0o7777
        base = {
            "schema_version": 2,
            "name": SKILL_NAME,
            "source": str(self.source),
            "target": str(self.target),
            "phase": "installed",
            "installed_at_utc": "2026-08-06T00:00:00Z",
            "mode_policy": "strict",
            "source_root_mode": root_mode,
            "target_root_mode": root_mode,
            "source_entries": copy.deepcopy(entries),
            "entries": copy.deepcopy(entries),
            "mode_capability": {"status": "not_required"},
            "target_filesystem": {
                "device": self.target.stat().st_dev,
                "mount_target": "/",
                "filesystem_type": "test",
                "mount_options_sha256": "0" * 64,
            },
        }
        cases = {}

        extra = copy.deepcopy(base)
        extra["unexpected"] = True
        cases["extra-field"] = extra

        missing = copy.deepcopy(base)
        del missing["source_entries"]
        cases["missing-field"] = missing

        unknown_policy = copy.deepcopy(base)
        unknown_policy["mode_policy"] = "ignore"
        cases["unknown-policy"] = unknown_policy

        malformed_capability = copy.deepcopy(base)
        malformed_capability["mode_capability"] = {"status": "anything"}
        cases["malformed-capability"] = malformed_capability

        mismatched_strict = copy.deepcopy(base)
        mismatched_strict["source_entries"]["SKILL.md"]["mode"] ^= 0o100
        cases["strict-mismatch"] = mismatched_strict

        bad_root_mode = copy.deepcopy(base)
        bad_root_mode["source_root_mode"] = "0755"
        cases["bad-root-mode"] = bad_root_mode

        for label, payload in cases.items():
            with self.subTest(label=label):
                self.write_manifest_payload(payload)
                result = self.verify()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(
                    self.result_payload(result)["reason"],
                    "manifest_invalid",
                )

    def test_recovery_manifest_is_not_accepted_as_install_manifest(self):
        self.assertEqual(self.install().returncode, 0)
        recovery = {
            "recovery_schema_version": 1,
            "operation": "archive_failed_staging",
            "phase": "archived",
        }
        self.write_manifest_payload(recovery)

        result = self.verify()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "manifest_invalid")

    def test_mode_capability_classifies_exact_posix_observations(self):
        result = INSTALLER.classify_mode_observations(
            [0o700, 0o750],
            [0o600, 0o640],
        )

        self.assertEqual(result, "strict")

    def test_mode_capability_classifies_stable_folding(self):
        result = INSTALLER.classify_mode_observations(
            [0o777, 0o777],
            [0o777, 0o777],
        )

        self.assertEqual(result, "target-observed")

    def test_mode_capability_rejects_mixed_or_fluctuating_observations(self):
        cases = (
            ([0o700, 0o750], [0o777, 0o777]),
            ([0o777, 0o755], [0o777, 0o777]),
            ([0o777, 0o777], [0o777, 0o755]),
        )

        for directory_modes, file_modes in cases:
            with self.subTest(
                directory_modes=directory_modes,
                file_modes=file_modes,
            ):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.classify_mode_observations(
                        directory_modes,
                        file_modes,
                    )
                self.assertEqual(
                    raised.exception.reason,
                    "mode_capability_unknown",
                )
                self.assertEqual(raised.exception.exit_code, 4)

    def test_mode_capability_selects_strict_on_posix(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            staged_entries = INSTALLER.scan_tree(stage)

            policy, proof, final_entries = INSTALLER.select_mode_policy(
                source,
                stage,
                skills_root,
                source_entries,
                staged_entries,
            )

        self.assertEqual(policy, "strict")
        self.assertEqual(proof, {"status": "not_required"})
        self.assertEqual(final_entries, source_entries)

    def test_mode_capability_rejects_content_mismatch_before_probe(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            (stage / "SKILL.md").write_text("changed\n", encoding="utf-8")
            staged_entries = INSTALLER.scan_tree(stage)
            with mock.patch.object(INSTALLER, "probe_mode_capability") as probe:
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.select_mode_policy(
                        source,
                        stage,
                        skills_root,
                        source_entries,
                        staged_entries,
                    )

        self.assertEqual(raised.exception.reason, "staging_hash_mismatch")
        self.assertEqual(raised.exception.exit_code, 3)
        probe.assert_not_called()

    def test_mode_capability_rejects_same_size_content_mismatch(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            probe_path = stage / "SKILL.md"
            original = probe_path.read_bytes()
            probe_path.write_bytes(bytes(byte ^ 1 for byte in original))
            staged_entries = INSTALLER.scan_tree(stage)

            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.select_mode_policy(
                    source,
                    stage,
                    skills_root,
                    source_entries,
                    staged_entries,
                )

        self.assertEqual(raised.exception.reason, "staging_hash_mismatch")
        self.assertEqual(raised.exception.exit_code, 3)

    def test_mode_capability_rejects_target_filesystem_mismatch(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            (stage / "SKILL.md").chmod(0o600)
            staged_entries = INSTALLER.scan_tree(stage)
            identities = [
                {
                    "device": 1,
                    "mount_target": "/first",
                    "filesystem_type": "first",
                    "mount_options_sha256": "1" * 64,
                },
                {
                    "device": 2,
                    "mount_target": "/second",
                    "filesystem_type": "second",
                    "mount_options_sha256": "2" * 64,
                },
            ]
            with mock.patch.object(
                INSTALLER,
                "filesystem_identity",
                side_effect=identities,
            ):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.select_mode_policy(
                        source,
                        stage,
                        skills_root,
                        source_entries,
                        staged_entries,
                    )

        self.assertEqual(raised.exception.reason, "mode_capability_unknown")
        self.assertEqual(raised.exception.exit_code, 4)

    def test_mode_capability_syscall_failure_is_unknown(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            (stage / "SKILL.md").chmod(0o600)
            staged_entries = INSTALLER.scan_tree(stage)
            with mock.patch.object(os, "fchmod", side_effect=PermissionError):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.select_mode_policy(
                        source,
                        stage,
                        skills_root,
                        source_entries,
                        staged_entries,
                    )

        self.assertEqual(raised.exception.reason, "mode_capability_unknown")
        self.assertEqual(raised.exception.exit_code, 4)

    def test_mode_capability_detects_probe_path_replacement(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            (stage / "SKILL.md").chmod(0o600)
            staged_entries = INSTALLER.scan_tree(stage)
            probe_path = stage / "SKILL.md"
            original_content = probe_path.read_bytes()
            real_fchmod = os.fchmod
            call_count = 0

            def replace_path_during_probe(descriptor, mode):
                nonlocal call_count
                call_count += 1
                real_fchmod(descriptor, mode)
                if call_count == 3:
                    probe_path.unlink()
                    probe_path.write_bytes(original_content)
                    probe_path.chmod(source_entries["SKILL.md"]["mode"])

            with mock.patch.object(
                os,
                "fchmod",
                side_effect=replace_path_during_probe,
            ):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.select_mode_policy(
                        source,
                        stage,
                        skills_root,
                        source_entries,
                        staged_entries,
                    )

        self.assertEqual(raised.exception.reason, "mode_capability_unknown")
        self.assertEqual(raised.exception.exit_code, 4)

    def test_mode_capability_builds_complete_target_observed_proof(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            source, stage, skills_root, source_entries = (
                self.make_posix_source_and_stage(temporary)
            )
            stage.chmod(0o777)
            (stage / "SKILL.md").chmod(0o777)
            staged_entries = INSTALLER.scan_tree(stage)
            with mock.patch.object(os, "fchmod", return_value=None):
                policy, proof, final_entries = INSTALLER.select_mode_policy(
                    source,
                    stage,
                    skills_root,
                    source_entries,
                    staged_entries,
                )

        self.assertEqual(policy, "target-observed")
        self.assertEqual(set(proof), INSTALLER.TARGET_OBSERVED_CAPABILITY_KEYS)
        self.assertEqual(proof["status"], "posix_mode_not_preserved")
        self.assertEqual(proof["directory_requested_modes"], [0o700, 0o750])
        self.assertEqual(proof["directory_observed_modes"], [0o777, 0o777])
        self.assertEqual(proof["file_requested_modes"], [0o600, 0o640])
        self.assertEqual(proof["file_observed_modes"], [0o777, 0o777])
        self.assertEqual(
            proof["directory_identity_before"],
            proof["directory_identity_after"],
        )
        self.assertEqual(
            proof["file_identity_before"],
            proof["file_identity_after"],
        )
        self.assertEqual(final_entries, staged_entries)

    def test_install_keeps_prepared_state_when_committed_tree_is_corrupt(self):
        def move_then_corrupt(source, destination):
            os.rename(source, destination)
            (destination / "SKILL.md").write_text("corrupt\n", encoding="utf-8")

        with mock.patch.object(INSTALLER, "rename_noreplace", side_effect=move_then_corrupt):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.install(self.source, self.skills_root)

        self.assertEqual(raised.exception.reason, "installed_hash_mismatch")
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.manifest.exists())
        prepared = json.loads(self.prepared_manifest.read_text(encoding="utf-8"))
        self.assertEqual(prepared["phase"], "prepared")

    def test_manifest_write_never_overwrites_existing_file(self):
        state = self.tempdir / "owned-state"
        state.mkdir()
        path = state / "install-manifest.json"
        path.write_text("owned by another process\n", encoding="utf-8")

        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.write_json_exclusive(path, {"schema_version": 1})

        self.assertEqual(raised.exception.reason, "install_state_collision")
        self.assertEqual(path.read_text(encoding="utf-8"), "owned by another process\n")

    def test_manifest_write_fails_closed_without_nofollow(self):
        state = self.tempdir / "new-state"
        state.mkdir()
        path = state / "install-manifest.json"

        with mock.patch.object(INSTALLER.os, "O_NOFOLLOW", None):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.write_json_exclusive(path, {"schema_version": 1})

        self.assertEqual(raised.exception.reason, "exclusive_manifest_unavailable")
        self.assertFalse(path.exists())

    def test_manifest_read_fails_closed_without_nofollow(self):
        self.assertEqual(self.install().returncode, 0)

        with mock.patch.object(INSTALLER.os, "O_NOFOLLOW", None):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.read_manifest_document(self.manifest)

        self.assertEqual(raised.exception.reason, "safe_manifest_read_unavailable")

    def test_manifest_read_rejects_oversized_json(self):
        oversized = self.tempdir / "oversized-manifest.json"
        oversized.write_text(
            json.dumps({"padding": "x" * (1024 * 1024)}) + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.read_manifest_document(oversized)

        self.assertEqual(raised.exception.reason, "manifest_too_large")

    def test_install_fails_closed_without_nofollow(self):
        with mock.patch.object(INSTALLER.os, "O_NOFOLLOW", None):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.install(self.source, self.skills_root)

        self.assertEqual(raised.exception.reason, "unsafe_source_entry")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())

    def test_install_preserves_evidence_when_atomic_move_fails(self):
        def fail_move(source, destination):
            raise INSTALLER.InstallError("atomic_rename_failed", 4)

        with mock.patch.object(INSTALLER, "rename_noreplace", side_effect=fail_move):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.install(self.source, self.skills_root)

        self.assertEqual(raised.exception.reason, "atomic_rename_failed")
        self.assertTrue(self.prepared_manifest.is_file())
        self.assertFalse(self.manifest.exists())
        stages = list(self.skills_root.glob(f".{SKILL_NAME}-stage-*"))
        self.assertEqual(len(stages), 1)
        self.assertEqual(INSTALLER.scan_tree(stages[0]), INSTALLER.scan_tree(self.source))

    def test_install_rejects_source_change_after_staging(self):
        real_copy_entries = INSTALLER.copy_entries

        def copy_then_mutate(source, destination, entries):
            real_copy_entries(source, destination, entries)
            (source / "late-file.txt").write_text("late\n", encoding="utf-8")

        with mock.patch.object(INSTALLER, "copy_entries", side_effect=copy_then_mutate):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.install(self.source, self.skills_root)

        self.assertEqual(raised.exception.reason, "source_changed_during_staging")
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.manifest.exists())

    def test_install_rejects_symlink_in_source(self):
        os.symlink(self.source / "SKILL.md", self.source / "references" / "linked.md")

        result = self.install()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "unsafe_source_entry")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.manifest.exists())

    @unittest.skipUnless(os.name == "posix" and Path("/tmp").is_dir(), "requires POSIX /tmp")
    def test_install_rejects_unreadable_source_directory(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            codex_home = root / "codex-home"
            skills_root = codex_home / "skills"
            skills_root.mkdir(parents=True)
            source = root / "source" / SKILL_NAME
            unreadable = source / "references"
            unreadable.mkdir(parents=True)
            (source / "SKILL.md").write_text("test\n", encoding="utf-8")
            (unreadable / "policy.toml").write_text("schema_version = 1\n", encoding="utf-8")
            unreadable.chmod(0)
            try:
                result = self.run_cli(
                    "install",
                    "--source",
                    source,
                    "--skills-root",
                    skills_root,
                    codex_home=codex_home,
                )
            finally:
                unreadable.chmod(0o700)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "unsafe_source_entry")

    @unittest.skipUnless(hasattr(os, "mkfifo") and Path("/tmp").is_dir(), "requires FIFO")
    def test_install_rejects_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            codex_home = root / "codex-home"
            skills_root = codex_home / "skills"
            skills_root.mkdir(parents=True)
            source = root / "source" / SKILL_NAME
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("test\n", encoding="utf-8")
            os.mkfifo(source / "blocked.pipe")

            result = self.run_cli(
                "install",
                "--source",
                source,
                "--skills-root",
                skills_root,
                codex_home=codex_home,
                timeout=2,
            )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "unsafe_source_entry")

    def test_install_rejects_existing_state_directory(self):
        self.state_dir.mkdir()
        marker = self.state_dir / "do-not-delete"
        marker.write_text("keep", encoding="utf-8")

        result = self.install()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "install_state_exists")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.target.exists())

    def test_verify_detects_extra_or_modified_files(self):
        self.assertEqual(self.install().returncode, 0)
        (self.target / "extra.txt").write_text("unexpected", encoding="utf-8")

        result = self.verify()

        self.assertEqual(result.returncode, 3, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "drift")
        self.assertIn("extra.txt", payload["differences"])

    def test_verify_detects_same_size_content_change(self):
        self.assertEqual(self.install().returncode, 0)
        changed = self.target / "SKILL.md"
        original = changed.read_bytes()
        replacement = bytes(byte ^ 1 for byte in original)
        self.assertEqual(len(replacement), len(original))
        changed.write_bytes(replacement)

        result = self.verify()

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("SKILL.md", self.result_payload(result)["differences"])

    def test_verify_rejects_non_utf8_manifest_as_json(self):
        self.assertEqual(self.install().returncode, 0)
        self.manifest.write_bytes(b"\xff\xfe\x00")

        result = self.verify()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "manifest_invalid")

    def test_verify_rejects_prepared_manifest_at_final_path(self):
        self.assertEqual(self.install().returncode, 0)
        payload = self.manifest_payload()
        payload["phase"] = "prepared"
        self.write_manifest_payload(payload)

        result = self.verify()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "manifest_phase_invalid")

    def test_verify_rejects_manifest_from_wrong_location(self):
        self.assertEqual(self.install().returncode, 0)
        other_manifest = self.tempdir / "install-manifest.json"
        shutil.copyfile(self.manifest, other_manifest)

        result = self.run_cli(
            "verify",
            "--target",
            self.target,
            "--manifest",
            other_manifest,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "manifest_location_mismatch")

    def test_verify_rejects_symlinked_install_state_directory(self):
        self.assertEqual(self.install().returncode, 0)
        outside_state = self.tempdir / "outside-install-state"
        self.state_dir.rename(outside_state)
        os.symlink(outside_state, self.state_dir, target_is_directory=True)

        result = self.verify()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "unsafe_install_state")

    def test_verify_rejects_manifest_bound_to_a_different_target(self):
        self.assertEqual(self.install().returncode, 0)
        other_home = self.tempdir / "other-codex-home"
        other_root = other_home / "skills"
        other_root.mkdir(parents=True)
        other_target = other_root / SKILL_NAME
        shutil.copytree(self.target, other_target)
        other_state = other_root / f".{SKILL_NAME}-install"
        other_state.mkdir()
        other_manifest = other_state / "install-manifest.json"
        shutil.copyfile(self.manifest, other_manifest)

        result = self.run_cli(
            "verify",
            "--target",
            other_target,
            "--manifest",
            other_manifest,
            codex_home=other_home,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "manifest_target_mismatch")

    def test_rollback_requires_exact_manifest_digest(self):
        self.assertEqual(self.install().returncode, 0)

        result = self.rollback("wrong")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(self.target.exists())
        self.assertTrue(self.manifest.exists())

    def test_rollback_rejects_prepared_manifest_at_final_path(self):
        self.assertEqual(self.install().returncode, 0)
        payload = self.manifest_payload()
        payload["phase"] = "prepared"
        self.write_manifest_payload(payload)

        result = self.rollback(payload["manifest_digest"])

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.result_payload(result)["reason"], "manifest_phase_invalid")
        self.assertTrue(self.target.exists())
        self.assertTrue(self.state_dir.exists())

    def test_rollback_refuses_drift_without_deleting_anything(self):
        self.assertEqual(self.install().returncode, 0)
        confirmation = self.manifest_payload()["manifest_digest"]
        changed = self.target / "SKILL.md"
        changed.write_text("user changed this\n", encoding="utf-8")

        result = self.rollback(confirmation)

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(changed.read_text(encoding="utf-8"), "user changed this\n")
        self.assertTrue(self.manifest.exists())

    def test_rollback_refuses_drift_before_any_move(self):
        self.assertEqual(self.install().returncode, 0)
        confirmation = self.manifest_payload()["manifest_digest"]
        (self.target / "extra.txt").write_text("drift", encoding="utf-8")

        with mock.patch.object(INSTALLER, "rename_noreplace") as move:
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.rollback(
                            self.target,
                            self.manifest,
                            confirmation,
                        )

        self.assertEqual(raised.exception.reason, "target_drift")
        move.assert_not_called()

    def test_verified_rollback_archives_skill_and_state_for_recovery(self):
        self.assertEqual(self.install().returncode, 0)
        manifest = self.manifest_payload()
        confirmation = manifest["manifest_digest"]

        result = self.rollback(confirmation)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.result_payload(result)
        self.assertTrue(payload["recoverable"])
        recovery_directory = Path(payload["recovery_directory"])
        self.assertEqual(recovery_directory.parent, self.codex_home / ".skill-rollbacks")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())
        archived_target = recovery_directory / SKILL_NAME
        archived_state = recovery_directory / self.state_dir.name
        self.assertEqual(
            (archived_target / "SKILL.md").read_text(encoding="utf-8"),
            (self.source / "SKILL.md").read_text(encoding="utf-8"),
        )
        archived_manifest = json.loads(
            (archived_state / self.manifest.name).read_text(encoding="utf-8")
        )
        self.assertEqual(archived_manifest, manifest)
        self.assertTrue((archived_state / self.prepared_manifest.name).is_file())
        self.assert_neighbors_untouched()

    def test_rollback_restores_target_when_state_archive_fails(self):
        self.assertEqual(self.install().returncode, 0)
        manifest = self.manifest_payload()
        real_rename = INSTALLER.rename_noreplace

        def fail_state_archive(source, destination):
            if source == self.state_dir:
                raise INSTALLER.InstallError("atomic_rename_failed", 4)
            real_rename(source, destination)

        with mock.patch.object(INSTALLER, "rename_noreplace", side_effect=fail_state_archive):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError):
                        INSTALLER.rollback(
                            self.target,
                            self.manifest,
                            manifest["manifest_digest"],
                        )

        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.state_dir.is_dir())
        self.assertEqual(INSTALLER.scan_tree(self.target), manifest["entries"])

    def test_rollback_state_archive_unknown_is_not_retried(self):
        self.assertEqual(self.install().returncode, 0)
        confirmation = self.manifest_payload()["manifest_digest"]
        real_rename = INSTALLER.rename_noreplace
        calls = []

        def make_second_move_unknown(source, destination):
            calls.append((source, destination))
            if len(calls) == 2:
                raise INSTALLER.InstallError("atomic_move_outcome_unknown", 4)
            real_rename(source, destination)

        output = io.StringIO()
        arguments = [
            str(SCRIPT),
            "rollback",
            "--target",
            str(self.target),
            "--manifest",
            str(self.manifest),
            "--confirm",
            confirmation,
        ]
        with mock.patch.object(
            INSTALLER,
            "rename_noreplace",
            side_effect=make_second_move_unknown,
        ):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with mock.patch.object(sys, "argv", arguments):
                    with redirect_stdout(output):
                        return_code = INSTALLER.main()

        self.assertEqual(return_code, 4)
        self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
        self.assertEqual(len(calls), 2)

    def test_rollback_unknown_recovery_is_not_retried(self):
        self.assertEqual(self.install().returncode, 0)
        confirmation = self.manifest_payload()["manifest_digest"]
        real_rename = INSTALLER.rename_noreplace
        calls = []

        def fail_archive_then_recovery(source, destination):
            calls.append((source, destination))
            if len(calls) == 2:
                raise INSTALLER.InstallError("atomic_rename_failed", 4)
            if len(calls) == 3:
                raise INSTALLER.InstallError("atomic_move_outcome_unknown", 4)
            real_rename(source, destination)

        output = io.StringIO()
        arguments = [
            str(SCRIPT),
            "rollback",
            "--target",
            str(self.target),
            "--manifest",
            str(self.manifest),
            "--confirm",
            confirmation,
        ]
        with mock.patch.object(
            INSTALLER,
            "rename_noreplace",
            side_effect=fail_archive_then_recovery,
        ):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with mock.patch.object(sys, "argv", arguments):
                    with redirect_stdout(output):
                        return_code = INSTALLER.main()

        self.assertEqual(return_code, 4)
        self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
        self.assertEqual(len(calls), 3)

    def test_rollback_uses_one_snapshot_and_restores_on_archived_manifest_drift(self):
        self.assertEqual(self.install().returncode, 0)
        original = self.manifest_payload()
        changed = dict(original)
        changed["source"] = str(self.tempdir / "changed-after-confirmation")
        changed["manifest_digest"] = INSTALLER.canonical_digest(changed)
        real_load = INSTALLER.load_manifest
        load_count = 0

        def load_then_mutate(path):
            nonlocal load_count
            load_count += 1
            loaded = real_load(path)
            if load_count == 1:
                self.write_manifest_payload(changed)
            return loaded

        with mock.patch.object(INSTALLER, "load_manifest", side_effect=load_then_mutate):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.rollback(
                            self.target,
                            self.manifest,
                            original["manifest_digest"],
                        )

        self.assertEqual(raised.exception.reason, "archived_manifest_mismatch")
        self.assertEqual(load_count, 1)
        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.state_dir.is_dir())

    def test_rollback_never_reports_success_when_active_target_reappears(self):
        self.assertEqual(self.install().returncode, 0)
        manifest = self.manifest_payload()
        real_rename = INSTALLER.rename_noreplace
        calls = []

        def move_then_recreate_target(source, destination):
            calls.append((source, destination))
            real_rename(source, destination)
            if len(calls) == 2:
                self.target.mkdir()
                (self.target / "concurrent").write_text("keep", encoding="utf-8")

        with mock.patch.object(
            INSTALLER,
            "rename_noreplace",
            side_effect=move_then_recreate_target,
        ):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.rollback(
                            self.target,
                            self.manifest,
                            manifest["manifest_digest"],
                        )

        self.assertEqual(raised.exception.reason, "rollback_completion_outcome_unknown")
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            (self.target / "concurrent").read_text(encoding="utf-8"),
            "keep",
        )

    def test_rollback_never_reports_success_when_active_state_reappears(self):
        self.assertEqual(self.install().returncode, 0)
        manifest = self.manifest_payload()
        real_rename = INSTALLER.rename_noreplace
        calls = []

        def move_then_recreate_state(source, destination):
            calls.append((source, destination))
            real_rename(source, destination)
            if len(calls) == 2:
                self.state_dir.mkdir()
                (self.state_dir / "concurrent").write_text("keep", encoding="utf-8")

        with mock.patch.object(
            INSTALLER,
            "rename_noreplace",
            side_effect=move_then_recreate_state,
        ):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.rollback(
                            self.target,
                            self.manifest,
                            manifest["manifest_digest"],
                        )

        self.assertEqual(raised.exception.reason, "rollback_completion_outcome_unknown")
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            (self.state_dir / "concurrent").read_text(encoding="utf-8"),
            "keep",
        )

    def run_archive_preserving_upgrade(self, old_source, new_source):
        calls = []

        def invoke(*arguments):
            calls.append(tuple(map(str, arguments)))
            return self.run_cli(*arguments)

        old_install = invoke(
            "install", "--source", old_source, "--skills-root", self.skills_root
        )
        self.assertEqual(old_install.returncode, 0, old_install.stderr)
        old_manifest_bytes = self.manifest.read_bytes()
        old_manifest = self.manifest_payload()

        old_verify = invoke(
            "verify", "--target", self.target, "--manifest", self.manifest
        )
        self.assertEqual(old_verify.returncode, 0, old_verify.stderr)

        rollback = invoke(
            "rollback",
            "--target",
            self.target,
            "--manifest",
            self.manifest,
            "--confirm",
            old_manifest["manifest_digest"],
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        recovery_directory = Path(self.result_payload(rollback)["recovery_directory"])

        new_install = invoke(
            "install", "--source", new_source, "--skills-root", self.skills_root
        )
        self.assertEqual(new_install.returncode, 0, new_install.stderr)
        new_manifest = self.manifest_payload()

        new_verify = invoke(
            "verify", "--target", self.target, "--manifest", self.manifest
        )
        self.assertEqual(new_verify.returncode, 0, new_verify.stderr)
        return {
            "calls": calls,
            "old_manifest": old_manifest,
            "old_manifest_bytes": old_manifest_bytes,
            "new_manifest": new_manifest,
            "recovery_directory": recovery_directory,
        }

    def test_existing_commands_upgrade_index_and_preserve_old_archive(self):
        old_source = self.make_versioned_source("old-source")
        new_source = self.make_versioned_source("new-source")

        self.assertEqual(
            INSTALLER.compare_content_entries(
                INSTALLER.scan_tree(old_source),
                INSTALLER.scan_tree(new_source),
            ),
            ["references/SKILL_INDEX_ZH.md"],
        )

        outcome = self.run_archive_preserving_upgrade(old_source, new_source)

        self.assertIsInstance(outcome, dict)
        self.assertEqual(
            [call[0] for call in outcome["calls"]],
            ["install", "verify", "rollback", "install", "verify"],
        )
        self.assertEqual(
            INSTALLER.compare_content_entries(
                outcome["old_manifest"]["source_entries"],
                outcome["new_manifest"]["source_entries"],
            ),
            ["references/SKILL_INDEX_ZH.md"],
        )
        archived_target = outcome["recovery_directory"] / SKILL_NAME
        archived_state = outcome["recovery_directory"] / self.state_dir.name
        self.assertTrue(archived_target.is_dir())
        self.assertTrue(archived_state.is_dir())
        self.assertEqual(
            (archived_state / self.manifest.name).read_bytes(),
            outcome["old_manifest_bytes"],
        )
        self.assertEqual(
            INSTALLER.compare_content_entries(
                outcome["old_manifest"]["entries"],
                INSTALLER.scan_tree(archived_target),
            ),
            [],
        )
        self.assert_neighbors_untouched()

    def test_new_install_conflict_runs_once_without_restoring_archive(self):
        old_source = self.make_versioned_source("old-conflict-source")
        new_source = self.make_versioned_source("new-conflict-source")
        calls = []

        def invoke(*arguments):
            calls.append(tuple(map(str, arguments)))
            return self.run_cli(*arguments)

        old_install = invoke(
            "install", "--source", old_source, "--skills-root", self.skills_root
        )
        self.assertEqual(old_install.returncode, 0, old_install.stderr)
        old_manifest = self.manifest_payload()
        old_verify = invoke(
            "verify", "--target", self.target, "--manifest", self.manifest
        )
        self.assertEqual(old_verify.returncode, 0, old_verify.stderr)
        rollback = invoke(
            "rollback",
            "--target",
            self.target,
            "--manifest",
            self.manifest,
            "--confirm",
            old_manifest["manifest_digest"],
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        recovery_directory = Path(self.result_payload(rollback)["recovery_directory"])
        archived_manifest = (
            recovery_directory / self.state_dir.name / self.manifest.name
        )
        archived_bytes = archived_manifest.read_bytes()

        self.target.mkdir()
        marker = self.target / "owned-by-user"
        marker.write_text("keep", encoding="utf-8")
        new_install = invoke(
            "install", "--source", new_source, "--skills-root", self.skills_root
        )

        self.assertEqual(new_install.returncode, 2, new_install.stderr)
        self.assertEqual(self.result_payload(new_install)["reason"], "target_exists")
        self.assertEqual(
            sum(
                call[0] == "install" and str(new_source) in call
                for call in calls
            ),
            1,
        )
        self.assertEqual(archived_manifest.read_bytes(), archived_bytes)
        self.assertTrue((recovery_directory / SKILL_NAME).is_dir())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.state_dir.exists())
        self.assert_neighbors_untouched()


class UpgradePreflightTests(unittest.TestCase):
    SOURCE_HEAD = "1" * 40

    def setUp(self):
        InstallSkillTests.setUp(self)
        self.old_source = InstallSkillTests.make_versioned_source(
            self,
            "upgrade-old-source",
        )
        self.new_source = InstallSkillTests.make_versioned_source(
            self,
            "upgrade-new-source",
        )
        result = InstallSkillTests.run_cli(
            self,
            "install",
            "--source",
            self.old_source,
            "--skills-root",
            self.skills_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def snapshot_tree(self, root):
        root = Path(root)
        snapshot = {}
        if not root.exists():
            return snapshot
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                snapshot[relative] = ("directory", stat.S_IMODE(metadata.st_mode))
            elif stat.S_ISREG(metadata.st_mode):
                snapshot[relative] = (
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            else:
                snapshot[relative] = ("other", stat.S_IFMT(metadata.st_mode))
        return snapshot

    def capability(self):
        snapshot = INSTALLER.directory_identity(
            self.target,
            "unsafe_target_entry",
        )
        snapshot["tree_digest"] = "3" * 64
        return (
            "NOREPLACE_ONLY",
            {
                "capability": "NOREPLACE_ONLY",
                "exchange_attempted": True,
                "exchange_result": "UNSUPPORTED",
                "noreplace_attempted": True,
                "noreplace_result": "VERIFIED",
                "postconditions_verified": True,
                "restored": True,
                "left_identity_before": snapshot,
                "right_identity_before": None,
                "left_identity_after": None,
                "right_identity_after": snapshot,
                "left_identity_restored": snapshot,
                "right_identity_restored": None,
            },
        )

    def build_request(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(),
                ):
                    return INSTALLER.build_upgrade_request(
                        self.new_source,
                        self.target,
                        self.manifest,
                        approval_id="UPGRADE-PREFLIGHT-TEST-001",
                    )

    def test_prepare_upgrade_requires_verified_old_manifest(self):
        (self.target / "SKILL.md").write_text("drift\n", encoding="utf-8")

        with self.assertRaises(INSTALLER.InstallError) as raised:
            self.build_request()

        self.assertEqual(raised.exception.reason, "old_install_drift")
        self.assertEqual(raised.exception.exit_code, 3)

    def test_prepare_upgrade_rejects_source_or_target_drift(self):
        real_scan = INSTALLER.scan_tree
        source_calls = 0

        def source_drift(root, *args, **kwargs):
            nonlocal source_calls
            if Path(os.path.abspath(root)) == Path(os.path.abspath(self.new_source)):
                source_calls += 1
                if source_calls == 2:
                    (self.new_source / "SKILL.md").write_text(
                        "---\nname: vibe-project-lead-zh\ndescription: changed\n---\n",
                        encoding="utf-8",
                    )
            return real_scan(root, *args, **kwargs)

        with mock.patch.object(INSTALLER, "scan_tree", side_effect=source_drift):
            with self.assertRaises(INSTALLER.InstallError) as source_error:
                self.build_request()
        self.assertEqual(source_error.exception.reason, "source_changed_during_preflight")
        self.assertEqual(source_error.exception.exit_code, 3)

        (self.new_source / "SKILL.md").write_text(
            "---\nname: vibe-project-lead-zh\ndescription: test\n---\n",
            encoding="utf-8",
        )
        target_calls = 0

        def target_drift(root, *args, **kwargs):
            nonlocal target_calls
            result = real_scan(root, *args, **kwargs)
            if Path(os.path.abspath(root)) == Path(os.path.abspath(self.target)):
                target_calls += 1
                if target_calls == 1:
                    (self.target / "SKILL.md").write_text("drift\n", encoding="utf-8")
            return result

        with mock.patch.object(INSTALLER, "scan_tree", side_effect=target_drift):
            with self.assertRaises(INSTALLER.InstallError) as target_error:
                self.build_request()
        self.assertEqual(target_error.exception.reason, "target_changed_during_preflight")
        self.assertEqual(target_error.exception.exit_code, 3)

    def test_prepare_upgrade_rejects_source_head_drift(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                side_effect=[self.SOURCE_HEAD, "2" * 40],
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(),
                ):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.build_upgrade_request(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="UPGRADE-PREFLIGHT-TEST-HEAD",
                        )

        self.assertEqual(raised.exception.reason, "source_changed_during_preflight")
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertIn("<head>", raised.exception.differences)

    def test_prepare_upgrade_requires_unique_candidate_inventory(self):
        duplicate = {
            "state": "DUPLICATE",
            "proof_kind": "PROCESS_FREE_SOURCE_LAYOUT",
            "discovery_id": SKILL_NAME,
            "declared_name": SKILL_NAME,
            "enabled": True,
            "duplicate_count": 2,
            "load_errors": [],
            "future_locator": str(self.target / "SKILL.md"),
            "source_skill_sha256": "2" * 64,
        }
        with mock.patch.object(
            INSTALLER,
            "candidate_inventory_identity",
            return_value=duplicate,
        ):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                self.build_request()

        self.assertEqual(raised.exception.reason, "candidate_inventory_not_unique")

    def test_prepare_upgrade_binds_filesystem_and_mode_capability(self):
        request = self.build_request()
        manifest = INSTALLER.load_manifest(self.manifest)

        self.assertEqual(request["upgrade_schema_version"], 1)
        self.assertEqual(request["journal_schema_version"], 1)
        self.assertEqual(set(request), INSTALLER.UPGRADE_REQUEST_V1_KEYS)
        self.assertEqual(request["source_head"], self.SOURCE_HEAD)
        self.assertEqual(
            request["source_tree_digest"],
            INSTALLER.canonical_tree_digest(INSTALLER.scan_tree(self.new_source)),
        )
        self.assertEqual(request["old_manifest_digest"], manifest["manifest_digest"])
        self.assertEqual(request["mode_policy"], manifest["mode_policy"])
        self.assertEqual(request["mode_capability"], manifest["mode_capability"])
        self.assertEqual(
            request["target_filesystem_identity"],
            INSTALLER.filesystem_identity(self.target),
        )
        self.assertEqual(request["switch_capability"], "NOREPLACE_ONLY")
        self.assertEqual(request["phase"], "PREPARED")
        self.assertIsNone(request["previous_phase_digest"])
        self.assertIsNone(request["new_stage_identity"])
        self.assertTrue(INSTALLER.validate_upgrade_request(request) is None)
        self.assertEqual(
            request["request_digest"],
            INSTALLER.upgrade_request_digest(request),
        )

    def test_upgrade_request_binds_the_same_runtime_source_tree(self):
        request = self.build_request()
        source_entries = INSTALLER.scan_tree(self.new_source)

        self.assertEqual(
            request["source_tree_digest"],
            INSTALLER.canonical_tree_digest(source_entries),
        )
        self.assertEqual(
            request["candidate_inventory"]["source_skill_sha256"],
            source_entries["SKILL.md"]["sha256"],
        )
        self.assertEqual(
            {
                relative
                for relative, entry in source_entries.items()
                if entry["type"] == "file"
            },
            EXPECTED_RUNTIME_FILES,
        )

    def test_exchange_probe_records_postconditions(self):
        def fake_rename(source, destination, flags):
            source = Path(source)
            destination = Path(destination)
            if flags == INSTALLER.RENAME_EXCHANGE:
                temporary = source.parent / ".fake-exchange"
                os.rename(source, temporary)
                os.rename(destination, source)
                os.rename(temporary, destination)
                return
            os.rename(source, destination)

        with mock.patch.object(
            INSTALLER,
            "renameat2_direct",
            side_effect=fake_rename,
        ):
            capability, evidence = INSTALLER.probe_switch_capability(self.skills_root)

        self.assertEqual(capability, "EXCHANGE_SUPPORTED")
        self.assertEqual(evidence["capability"], "EXCHANGE_SUPPORTED")
        self.assertTrue(evidence["exchange_attempted"])
        self.assertFalse(evidence["noreplace_attempted"])
        self.assertEqual(evidence["exchange_result"], "VERIFIED")
        self.assertTrue(evidence["postconditions_verified"])
        self.assertTrue(evidence["restored"])
        self.assertEqual(evidence["left_identity_after"], evidence["right_identity_before"])
        self.assertEqual(evidence["right_identity_after"], evidence["left_identity_before"])
        self.assertEqual(
            evidence["left_identity_restored"],
            evidence["left_identity_before"],
        )
        self.assertEqual(
            evidence["right_identity_restored"],
            evidence["right_identity_before"],
        )

    def test_unproven_exchange_returns_unknown_without_fallback(self):
        with mock.patch.object(
            INSTALLER,
            "renameat2_direct",
            side_effect=INSTALLER.InstallError(
                "exchange_probe_outcome_unknown",
                4,
                status="unknown",
            ),
        ):
            with mock.patch.object(INSTALLER, "probe_noreplace_capability") as fallback:
                capability, evidence = INSTALLER.probe_switch_capability(
                    self.skills_root
                )

        self.assertEqual(capability, "UNKNOWN")
        self.assertEqual(evidence["exchange_result"], "UNKNOWN")
        self.assertFalse(evidence["noreplace_attempted"])
        self.assertFalse(evidence["postconditions_verified"])
        fallback.assert_not_called()

    def test_noreplace_probe_is_process_free(self):
        observed_flags = []

        def direct_probe(source, destination, flags):
            observed_flags.append(flags)
            if flags == INSTALLER.RENAME_EXCHANGE:
                raise INSTALLER.InstallError("exchange_unsupported", 4)
            os.rename(source, destination)

        with mock.patch.object(
            INSTALLER,
            "renameat2_direct",
            side_effect=direct_probe,
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                side_effect=AssertionError("preflight must be process-free"),
            ):
                capability, evidence = INSTALLER.probe_switch_capability(
                    self.skills_root
                )

        self.assertEqual(capability, "NOREPLACE_ONLY")
        self.assertEqual(
            observed_flags,
            [INSTALLER.RENAME_EXCHANGE, INSTALLER.RENAME_NOREPLACE],
        )
        self.assertEqual(evidence["noreplace_result"], "VERIFIED")
        self.assertTrue(evidence["postconditions_verified"])
        self.assertTrue(evidence["restored"])

    def test_prepare_upgrade_is_read_only_until_explicit_output(self):
        before = self.snapshot_tree(self.codex_home)
        output = self.tempdir / "requests" / "upgrade-request.json"
        output.parent.mkdir()

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(),
                ):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        code = INSTALLER.prepare_upgrade(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="UPGRADE-PREFLIGHT-TEST-002",
                            output=None,
                        )

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "upgrade_prepared")
        self.assertIsNone(payload["output"])
        self.assertFalse(output.exists())
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(),
                ):
                    with redirect_stdout(io.StringIO()):
                        code = INSTALLER.prepare_upgrade(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="UPGRADE-PREFLIGHT-TEST-003",
                            output=output,
                        )

        self.assertEqual(code, 0)
        request = json.loads(output.read_text(encoding="utf-8"))
        INSTALLER.validate_upgrade_request(request)
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

    def test_prepare_upgrade_rejects_output_inside_protected_trees(self):
        before_home = self.snapshot_tree(self.codex_home)
        before_source = self.snapshot_tree(self.new_source)

        for output in (
            self.target / "upgrade-request.json",
            self.state_dir / "upgrade-request.json",
            self.skills_root / "upgrade-request.json",
            self.new_source / "upgrade-request.json",
        ):
            with self.subTest(output=output):
                with mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": str(self.codex_home)},
                ):
                    with mock.patch.object(
                        INSTALLER,
                        "read_source_head",
                        return_value=self.SOURCE_HEAD,
                    ):
                        with mock.patch.object(
                            INSTALLER,
                            "probe_switch_capability",
                            return_value=self.capability(),
                        ):
                            with self.assertRaises(INSTALLER.InstallError) as raised:
                                INSTALLER.prepare_upgrade(
                                    self.new_source,
                                    self.target,
                                    self.manifest,
                                    approval_id="UPGRADE-PREFLIGHT-TEST-PROTECTED",
                                    output=output,
                                )
                self.assertEqual(raised.exception.reason, "unsafe_upgrade_output")
                self.assertFalse(output.exists())

        self.assertEqual(self.snapshot_tree(self.codex_home), before_home)
        self.assertEqual(self.snapshot_tree(self.new_source), before_source)


class WindowsBackendEvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.tempdir = Path(self._tempdir.name)
        self.skills_root = self.tempdir / "codex-home" / "skills"
        self.skills_root.mkdir(parents=True)

    @staticmethod
    def directory_snapshot(inode, digest):
        return {
            "type": "directory",
            "mode": 0o700,
            "device": 7,
            "inode": inode,
            "size": 4096,
            "nlink": 1,
            "mtime_ns": 100,
            "tree_digest": digest,
        }

    def legacy_switch_evidence(self):
        source = self.directory_snapshot(11, "a" * 64)
        return {
            "capability": "NOREPLACE_ONLY",
            "exchange_attempted": True,
            "exchange_result": "UNSUPPORTED",
            "noreplace_attempted": True,
            "noreplace_result": "VERIFIED",
            "postconditions_verified": True,
            "restored": True,
            "left_identity_before": source,
            "right_identity_before": None,
            "left_identity_after": None,
            "right_identity_after": source,
            "left_identity_restored": source,
            "right_identity_restored": None,
        }

    def upgrade_request_v1(self):
        base = self.tempdir.resolve()
        request = {
            "upgrade_schema_version": 1,
            "journal_schema_version": 1,
            "operation": "upgrade",
            "operation_id": "1" * 32,
            "source": str(base / "source"),
            "source_head": "1" * 40,
            "source_tree_digest": "1" * 64,
            "source_root_identity": {
                key: value
                for key, value in self.directory_snapshot(20, "2" * 64).items()
                if key != "tree_digest"
            },
            "target": str(base / "codex-home" / "skills" / SKILL_NAME),
            "manifest": str(
                base / "codex-home" / "skills" / f".{SKILL_NAME}-install" / "install-manifest.json"
            ),
            "old_manifest_digest": "2" * 64,
            "old_target_identity": {
                key: value
                for key, value in self.directory_snapshot(21, "3" * 64).items()
                if key != "tree_digest"
            },
            "old_target_tree_digest": "3" * 64,
            "old_state_identity": {
                key: value
                for key, value in self.directory_snapshot(22, "4" * 64).items()
                if key != "tree_digest"
            },
            "old_state_tree_digest": "4" * 64,
            "new_stage_identity": None,
            "target_filesystem_identity": {
                "device": 7,
                "mount_target": "/mnt/c",
                "filesystem_type": "9p",
                "mount_options_sha256": "c" * 64,
            },
            "mode_policy": "strict",
            "mode_capability": {"status": "legacy_manifest"},
            "candidate_inventory": {
                "state": "READY",
                "proof_kind": "PROCESS_FREE_SOURCE_LAYOUT",
                "discovery_id": SKILL_NAME,
                "declared_name": SKILL_NAME,
                "enabled": True,
                "duplicate_count": 1,
                "load_errors": [],
                "future_locator": str(
                    base / "codex-home" / "skills" / SKILL_NAME / "SKILL.md"
                ),
                "source_skill_sha256": "5" * 64,
            },
            "switch_capability": "NOREPLACE_ONLY",
            "switch_evidence": self.legacy_switch_evidence(),
            "approval_id": "UPGRADE-UNIT-001",
            "recovery_directory": None,
            "phase": "PREPARED",
            "previous_phase_digest": None,
            "created_at_utc": "2026-09-07T00:00:00Z",
        }
        request["request_digest"] = INSTALLER.upgrade_request_digest(request)
        return request

    def restore_request_v1(self):
        base = self.tempdir.resolve()
        request = {
            "restore_schema_version": 1,
            "journal_schema_version": 1,
            "operation": "restore-version",
            "operation_id": "restore-" + "1" * 32,
            "source_receipt": str(base / "upgrade-success.json"),
            "source_receipt_digest": "1" * 64,
            "source_receipt_identity": {
                "type": "file",
                "mode": 0o600,
                "device": 7,
                "inode": 31,
                "size": 100,
                "nlink": 1,
                "mtime_ns": 100,
            },
            "source_receipt_sha256": "2" * 64,
            "source_upgrade_operation_id": "2" * 32,
            "source_upgrade_request_digest": "3" * 64,
            "target": str(base / "codex-home" / "skills" / SKILL_NAME),
            "state": str(base / "codex-home" / "skills" / f".{SKILL_NAME}-install"),
            "source_archive_target": str(base / "archive" / "target"),
            "source_archive_state": str(base / "archive" / "state"),
            "active_target_snapshot": self.directory_snapshot(41, "4" * 64),
            "active_state_snapshot": self.directory_snapshot(42, "5" * 64),
            "archive_target_snapshot": self.directory_snapshot(43, "6" * 64),
            "archive_state_snapshot": self.directory_snapshot(44, "7" * 64),
            "active_manifest_digest": "8" * 64,
            "archive_manifest_digest": "9" * 64,
            "target_filesystem_identity": {
                "device": 7,
                "mount_target": "/mnt/c",
                "filesystem_type": "9p",
                "mount_options_sha256": "c" * 64,
            },
            "switch_capability": "NOREPLACE_ONLY",
            "switch_evidence": self.legacy_switch_evidence(),
            "approval_id": "RESTORE-UNIT-001",
            "phase": "PREPARED",
            "previous_phase_digest": None,
            "recovery_directory": str(base / "restore-recovery"),
            "created_at_utc": "2026-09-07T00:00:00Z",
        }
        request["request_digest"] = INSTALLER.restore_request_digest(request)
        return request

    @staticmethod
    def bind_route_a(request, evidence, schema_key):
        value = copy.deepcopy(request)
        value.pop("switch_evidence")
        value[schema_key] = 2
        value["journal_schema_version"] = 2
        value.update(
            {
                "switch_backend": evidence["backend"],
                "switch_evidence_digest": evidence["evidence_digest"],
                "backend_implementation_digest": evidence[
                    "backend_implementation_digest"
                ],
                "skills_root_stable_identity": evidence[
                    "skills_root_stable_identity"
                ],
                "wsl_mount_identity": evidence["wsl_mount_identity"],
                "windows_volume_identity": evidence["windows_volume_identity"],
            }
        )
        digest = (
            INSTALLER.upgrade_request_digest
            if schema_key == "upgrade_schema_version"
            else INSTALLER.restore_request_digest
        )
        value["request_digest"] = digest(value)
        return value

    def test_valid_backend_evidence_has_exact_contract_and_digest(self):
        evidence = valid_backend_evidence_fixture(self.skills_root.resolve())

        self.assertEqual(set(evidence), INSTALLER.SWITCH_BACKEND_EVIDENCE_V1_KEYS)
        self.assertIsNone(INSTALLER.validate_switch_backend_evidence(evidence))
        self.assertEqual(
            evidence["evidence_digest"],
            INSTALLER.switch_backend_evidence_digest(evidence),
        )

    def test_backend_evidence_rejects_contract_and_semantic_drift(self):
        base = valid_backend_evidence_fixture(self.skills_root.resolve())

        cases = {
            "unknown-key": lambda value: value.update({"unexpected": None}),
            "missing-key": lambda value: value.pop("created_at_utc"),
            "bool-as-int": lambda value: value.update({"schema_version": True}),
            "bad-evidence-id": lambda value: value.update({"evidence_id": "bad"}),
            "bad-approval": lambda value: value.update({"approval_id": "bad\nvalue"}),
            "relative-skills-root": lambda value: value.update({"skills_root": "relative"}),
            "wrong-backend": lambda value: value.update({"backend": "POSIX_RENAME"}),
            "wrong-capability": lambda value: value.update({"capability": "UNKNOWN"}),
            "lowercase-drive": lambda value: value["windows_volume_identity"].update({"drive": "c:"}),
            "bad-volume-serial": lambda value: value["windows_volume_identity"].update({"volume_serial": "123"}),
            "wrong-call-count": lambda value: value["forward_move"].update({"call_count": 2}),
            "bool-call-count": lambda value: value["forward_move"].update({"call_count": True}),
            "wrong-forward-classification": lambda value: value["forward_move"].update({"return_classification": "TARGET_EXISTS"}),
            "collision-content-drift": lambda value: value["collision_guard"].update(
                {
                    "source_after": {
                        **value["collision_guard"]["source_after"],
                        "tree_digest": "f" * 64,
                    }
                }
            ),
            "cleanup-residue": lambda value: value["cleanup"].update({"residue": ["source"]}),
            "implementation-digest": lambda value: value.update({"backend_implementation_digest": "0" * 64}),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                value = copy.deepcopy(base)
                mutate(value)
                value["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(value)
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.validate_switch_backend_evidence(value)
                self.assertEqual(
                    raised.exception.reason,
                    "switch_backend_evidence_invalid",
                )

        installer_mismatch = copy.deepcopy(base)
        installer_mismatch["installer_sha256"] = "0" * 64
        installer_mismatch["backend_implementation_digest"] = (
            INSTALLER.backend_implementation_digest(
                INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                installer_mismatch["installer_sha256"],
            )
        )
        installer_mismatch["evidence_digest"] = (
            INSTALLER.switch_backend_evidence_digest(installer_mismatch)
        )
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.validate_switch_backend_evidence(installer_mismatch)

        digest_mismatch = copy.deepcopy(base)
        digest_mismatch["evidence_digest"] = "0" * 64
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.validate_switch_backend_evidence(digest_mismatch)

    def test_upgrade_and_restore_requests_dispatch_v1_and_v2_strictly(self):
        evidence = valid_backend_evidence_fixture(self.skills_root.resolve())
        upgrade_v1 = self.upgrade_request_v1()
        restore_v1 = self.restore_request_v1()
        upgrade_v2 = self.bind_route_a(
            upgrade_v1,
            evidence,
            "upgrade_schema_version",
        )
        restore_v2 = self.bind_route_a(
            restore_v1,
            evidence,
            "restore_schema_version",
        )

        self.assertIsNone(INSTALLER.validate_upgrade_request(upgrade_v1))
        self.assertIsNone(INSTALLER.validate_upgrade_request(upgrade_v2))
        self.assertIsNone(INSTALLER.validate_restore_request(restore_v1))
        self.assertIsNone(INSTALLER.validate_restore_request(restore_v2))
        self.assertEqual(upgrade_v1["upgrade_schema_version"], 1)
        self.assertNotIn("switch_backend", upgrade_v1)
        self.assertEqual(upgrade_v2["upgrade_schema_version"], 2)
        self.assertEqual(upgrade_v2["journal_schema_version"], 2)
        self.assertEqual(
            upgrade_v2["switch_backend"],
            INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
        )
        self.assertNotIn("switch_evidence", upgrade_v2)

        for request, validator, schema_key in (
            (upgrade_v1, INSTALLER.validate_upgrade_request, "upgrade_schema_version"),
            (restore_v1, INSTALLER.validate_restore_request, "restore_schema_version"),
        ):
            with self.subTest(schema_key=schema_key):
                value = copy.deepcopy(request)
                value[schema_key] = True
                value["request_digest"] = (
                    INSTALLER.upgrade_request_digest(value)
                    if schema_key == "upgrade_schema_version"
                    else INSTALLER.restore_request_digest(value)
                )
                with self.assertRaises(INSTALLER.InstallError):
                    validator(value)


class WindowsSwitchBackendAttestationTests(unittest.TestCase):
    def setUp(self):
        InstallSkillTests.setUp(self)
        self.output = self.tempdir / "switch-backend-evidence.json"
        self.filesystem = {
            "device": self.skills_root.stat().st_dev,
            "mount_target": "/mnt/c",
            "filesystem_type": "9p",
            "mount_options_sha256": "c" * 64,
        }
        self.volume = {
            "drive": "C:",
            "filesystem_name": "NTFS",
            "volume_serial": "12AB34CD",
        }

    @staticmethod
    def fake_adapter(calls):
        def move(source, destination):
            source = Path(source)
            destination = Path(destination)
            calls.append((source.name, destination.name))
            if destination.name == "collision":
                return {
                    "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                    "classification": "TARGET_EXISTS",
                    "movefileex_call_count": 1,
                    "win32_error": 183,
                }
            os.rename(source, destination)
            return {
                "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                "classification": "VERIFIED",
                "movefileex_call_count": 1,
                "win32_error": 0,
            }

        return move

    def attest(self, adapter, *, output=None):
        stdout = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"CODEX_HOME": str(self.codex_home)},
            clear=False,
        ):
            with mock.patch.object(
                INSTALLER,
                "filesystem_identity",
                return_value=self.filesystem,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "windows_volume_identity",
                    return_value=self.volume,
                ):
                    with mock.patch.object(
                        INSTALLER,
                        "windows_movefileex_noreplace",
                        side_effect=adapter,
                    ):
                        with redirect_stdout(stdout):
                            code = INSTALLER.attest_switch_backend(
                                self.skills_root,
                                INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                                "ATTEST-UNIT-001",
                                self.output if output is None else output,
                            )
        return code, stdout.getvalue()

    def probe_roots(self):
        return sorted(
            self.skills_root.glob(
                ".vibe-project-lead-zh-switch-probe-*"
            )
        )

    def test_attestation_proves_forward_collision_restore_and_cleanup(self):
        calls = []
        source_marker = b"source-marker-secret-contents"
        collision_marker = b"collision-marker-secret-contents"
        marker_values = iter((source_marker, collision_marker))

        with mock.patch.object(
            INSTALLER,
            "_new_attestation_marker",
            side_effect=lambda: next(marker_values),
        ):
            with mock.patch.object(
                INSTALLER.subprocess,
                "run",
                side_effect=AssertionError("unit attestation must use its doubles"),
            ):
                code, output = self.attest(self.fake_adapter(calls))

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [("source", "forward"), ("forward", "collision"), ("forward", "source")],
        )
        self.assertEqual(self.probe_roots(), [])
        payload = json.loads(output)
        evidence = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload),
            {"status", "evidence_id", "evidence_digest", "output"},
        )
        self.assertEqual(payload["status"], "switch_backend_attested")
        self.assertEqual(payload["output"], str(self.output))
        self.assertEqual(payload["evidence_id"], evidence["evidence_id"])
        self.assertEqual(payload["evidence_digest"], evidence["evidence_digest"])
        self.assertIsNone(INSTALLER.validate_switch_backend_evidence(evidence))
        self.assertEqual(evidence["cleanup"]["residue"], [])
        self.assertEqual(
            evidence["cleanup"]["removed_relative_paths"],
            [
                "source/source-marker.bin",
                "collision/collision-marker.bin",
                "source",
                "collision",
                ".",
            ],
        )
        self.assertEqual(evidence["forward_move"]["call_count"], 1)
        self.assertEqual(evidence["collision_guard"]["call_count"], 1)
        self.assertEqual(evidence["restore_move"]["call_count"], 1)
        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn(source_marker.decode("ascii"), serialized)
        self.assertNotIn(collision_marker.decode("ascii"), serialized)
        for forbidden in (
            "command",
            "executable",
            "stdout",
            "stderr",
            "environment",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_output_collision_symlink_hardlink_and_protected_paths_are_rejected(self):
        seed = self.tempdir / "seed.json"
        seed.write_text("seed", encoding="utf-8")
        cases = []

        collision = self.tempdir / "collision.json"
        collision.write_text("existing", encoding="utf-8")
        cases.append(("collision", collision))

        symlink = self.tempdir / "symlink.json"
        symlink.symlink_to(seed)
        cases.append(("symlink", symlink))

        hardlink = self.tempdir / "hardlink.json"
        os.link(seed, hardlink)
        cases.append(("hardlink", hardlink))

        protected = self.skills_root / "protected.json"
        cases.append(("protected", protected))

        for label, output in cases:
            with self.subTest(label=label):
                calls = []
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    self.attest(self.fake_adapter(calls), output=output)
                self.assertEqual(
                    raised.exception.reason,
                    "switch_backend_evidence_output_invalid",
                )
                self.assertEqual(calls, [])
                self.assertEqual(self.probe_roots(), [])

    def test_unknown_move_writes_no_evidence_does_not_retry_or_cleanup(self):
        for unknown_call in (1, 2, 3):
            with self.subTest(unknown_call=unknown_call):
                output = self.tempdir / f"unknown-{unknown_call}.json"
                calls = []

                def adapter(source, destination):
                    source = Path(source)
                    destination = Path(destination)
                    calls.append((source.name, destination.name))
                    if len(calls) == unknown_call:
                        return {
                            "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                            "classification": "UNKNOWN",
                            "movefileex_call_count": 1,
                            "win32_error": None,
                        }
                    if destination.name == "collision":
                        return {
                            "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                            "classification": "TARGET_EXISTS",
                            "movefileex_call_count": 1,
                            "win32_error": 183,
                        }
                    os.rename(source, destination)
                    return {
                        "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                        "classification": "VERIFIED",
                        "movefileex_call_count": 1,
                        "win32_error": 0,
                    }

                before = set(self.probe_roots())
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    self.attest(adapter, output=output)
                after = set(self.probe_roots())
                self.assertEqual(
                    raised.exception.reason,
                    "switch_backend_attestation_unknown",
                )
                self.assertEqual(raised.exception.status, "unknown")
                self.assertEqual(raised.exception.exit_code, 4)
                self.assertEqual(len(calls), unknown_call)
                self.assertFalse(output.exists())
                self.assertEqual(len(after - before), 1)

    def test_known_cleanup_failure_requires_recovery_and_preserves_probe_root(self):
        calls = []
        with mock.patch.object(
            INSTALLER.os,
            "unlink",
            side_effect=PermissionError("cleanup denied"),
        ):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                self.attest(self.fake_adapter(calls))

        self.assertEqual(
            raised.exception.reason,
            "switch_backend_attestation_cleanup_failed",
        )
        self.assertEqual(raised.exception.status, "recovery_required")
        self.assertEqual(len(calls), 3)
        self.assertFalse(self.output.exists())
        roots = self.probe_roots()
        self.assertEqual(len(roots), 1)
        self.assertTrue((roots[0] / "source" / "source-marker.bin").is_file())
        self.assertTrue((roots[0] / "collision" / "collision-marker.bin").is_file())

    def test_cli_exposes_only_the_fixed_backend_contract(self):
        argv = [
            str(SCRIPT),
            "attest-switch-backend",
            "--skills-root",
            str(self.skills_root),
            "--backend",
            INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
            "--approval-id",
            "ATTEST-UNIT-001",
            "--output",
            str(self.output),
        ]
        with mock.patch.object(sys, "argv", argv):
            args = INSTALLER.parse_args()

        self.assertEqual(args.command, "attest-switch-backend")
        self.assertEqual(args.skills_root, self.skills_root)
        self.assertEqual(args.backend, INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE)
        self.assertEqual(args.approval_id, "ATTEST-UNIT-001")
        self.assertEqual(args.output, self.output)

        argv[argv.index(INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE)] = "POSIX_RENAME"
        with mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit):
                INSTALLER.parse_args()


class RouteAUpgradePrepareTests(unittest.TestCase):
    SOURCE_HEAD = "1" * 40

    def setUp(self):
        UpgradePreflightTests.setUp(self)
        self.filesystem = {
            "device": self.skills_root.stat().st_dev,
            "mount_target": "/mnt/c",
            "filesystem_type": "9p",
            "mount_options_sha256": "c" * 64,
        }

    snapshot_tree = UpgradePreflightTests.snapshot_tree
    capability = UpgradePreflightTests.capability

    def write_valid_evidence(self, name="route-a-evidence.json"):
        evidence = valid_backend_evidence_fixture(self.skills_root.resolve())
        evidence["skills_root_stable_identity"] = INSTALLER.stable_directory_identity(
            self.skills_root,
            "test",
        )
        evidence["target_filesystem_identity"] = copy.deepcopy(self.filesystem)
        evidence["wsl_mount_identity"] = {
            key: self.filesystem[key]
            for key in INSTALLER.WSL_MOUNT_IDENTITY_KEYS
        }
        evidence["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(
            evidence
        )
        path = self.tempdir / name
        path.write_text(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def build_route_a(self, evidence_path):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=self.filesystem,
                ):
                    return INSTALLER.build_upgrade_request(
                        self.new_source,
                        self.target,
                        self.manifest,
                        approval_id="ROUTE-A-PREPARE-001",
                        switch_backend_evidence_path=evidence_path,
                    )

    def test_route_a_prepare_is_process_free_and_builds_strict_v2(self):
        evidence_path = self.write_valid_evidence()
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        traps = {
            "run": mock.patch.object(
                INSTALLER.subprocess,
                "run",
                side_effect=AssertionError("route-A prepare started a process"),
            ),
            "switch_probe": mock.patch.object(
                INSTALLER,
                "probe_switch_capability",
                side_effect=AssertionError("route-A prepare called POSIX probe"),
            ),
            "noreplace_probe": mock.patch.object(
                INSTALLER,
                "probe_noreplace_capability",
                side_effect=AssertionError("route-A prepare called noreplace probe"),
            ),
            "adapter": mock.patch.object(
                INSTALLER,
                "windows_movefileex_noreplace",
                side_effect=AssertionError("route-A prepare called Windows adapter"),
            ),
            "move": mock.patch.object(
                INSTALLER,
                "move_directory_for_backend",
                side_effect=AssertionError("route-A prepare moved a directory"),
            ),
        }
        with traps["run"], traps["switch_probe"], traps["noreplace_probe"], traps[
            "adapter"
        ], traps["move"]:
            request = self.build_route_a(evidence_path)

        self.assertEqual(request["upgrade_schema_version"], 2)
        self.assertEqual(request["journal_schema_version"], 2)
        self.assertEqual(set(request), INSTALLER.UPGRADE_REQUEST_V2_KEYS)
        self.assertEqual(
            request["switch_backend"],
            INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
        )
        self.assertEqual(request["switch_evidence_digest"], evidence["evidence_digest"])
        self.assertNotIn("switch_evidence", request)
        self.assertIsNone(INSTALLER.validate_upgrade_request(request))

    def test_absent_evidence_preserves_the_legacy_v1_branch(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(),
                ) as probe:
                    request = INSTALLER.build_upgrade_request(
                        self.new_source,
                        self.target,
                        self.manifest,
                        approval_id="LEGACY-PREPARE-001",
                    )

        self.assertEqual(request["upgrade_schema_version"], 1)
        self.assertEqual(request["journal_schema_version"], 1)
        self.assertEqual(set(request), INSTALLER.UPGRADE_REQUEST_V1_KEYS)
        self.assertIn("switch_evidence", request)
        probe.assert_called_once_with(self.target.parent)

    def test_route_a_rejects_invalid_or_drifted_evidence_without_output(self):
        base_path = self.write_valid_evidence("base-evidence.json")
        base = json.loads(base_path.read_text(encoding="utf-8"))

        def mutate_and_digest(mutator):
            value = copy.deepcopy(base)
            mutator(value)
            value["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(value)
            return value

        cases = {
            "bad-digest": {**base, "evidence_digest": "0" * 64},
            "wrong-backend": mutate_and_digest(
                lambda value: value.update({"backend": "POSIX_RENAME"})
            ),
            "wrong-capability": mutate_and_digest(
                lambda value: value.update({"capability": "UNKNOWN"})
            ),
            "wrong-root": mutate_and_digest(
                lambda value: value.update({"skills_root": str(self.tempdir)})
            ),
            "wrong-filesystem": mutate_and_digest(
                lambda value: value["target_filesystem_identity"].update(
                    {"device": value["target_filesystem_identity"]["device"] + 1}
                )
            ),
            "wrong-wsl-mount": mutate_and_digest(
                lambda value: value["wsl_mount_identity"].update(
                    {"mount_options_sha256": "d" * 64}
                )
            ),
            "wrong-derived-drive": mutate_and_digest(
                lambda value: value["windows_volume_identity"].update({"drive": "D:"})
            ),
            "wrong-installer": mutate_and_digest(
                lambda value: value.update({"installer_sha256": "0" * 64})
            ),
            "wrong-implementation": mutate_and_digest(
                lambda value: value.update({"backend_implementation_digest": "0" * 64})
            ),
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                evidence_path = self.tempdir / f"{label}.json"
                evidence_path.write_text(json.dumps(value), encoding="utf-8")
                output = self.tempdir / f"{label}-request.json"
                with self.assertRaises(INSTALLER.InstallError):
                    self.build_route_a(evidence_path)
                self.assertFalse(output.exists())

        seed = self.tempdir / "evidence-seed.json"
        seed.write_bytes(base_path.read_bytes())
        for label, evidence_path in (
            ("hardlink", self.tempdir / "evidence-hardlink.json"),
            ("symlink", self.tempdir / "evidence-symlink.json"),
        ):
            if label == "hardlink":
                os.link(seed, evidence_path)
            else:
                evidence_path.symlink_to(seed)
            with self.subTest(label=label):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    self.build_route_a(evidence_path)
                self.assertEqual(
                    raised.exception.reason,
                    "switch_backend_evidence_invalid",
                )

    def test_route_a_output_is_optional_exclusive_and_outside_protected_trees(self):
        evidence_path = self.write_valid_evidence()
        before = self.snapshot_tree(self.codex_home)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=self.filesystem,
                ):
                    with redirect_stdout(io.StringIO()):
                        code = INSTALLER.prepare_upgrade(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="ROUTE-A-PREPARE-OUTPUT-001",
                            output=None,
                            switch_backend_evidence_path=evidence_path,
                        )
        self.assertEqual(code, 0)
        self.assertEqual(before, self.snapshot_tree(self.codex_home))

        output = self.tempdir / "route-a-request.json"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(INSTALLER, "read_source_head", return_value=self.SOURCE_HEAD):
                with mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=self.filesystem,
                ):
                    with redirect_stdout(io.StringIO()):
                        code = INSTALLER.prepare_upgrade(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="ROUTE-A-PREPARE-OUTPUT-002",
                            output=output,
                            switch_backend_evidence_path=evidence_path,
                        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.read_text())["upgrade_schema_version"], 2)

        with self.assertRaises(INSTALLER.InstallError) as collision:
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                with mock.patch.object(INSTALLER, "read_source_head", return_value=self.SOURCE_HEAD):
                    with mock.patch.object(
                        INSTALLER,
                        "filesystem_identity",
                        return_value=self.filesystem,
                    ):
                        INSTALLER.prepare_upgrade(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="ROUTE-A-PREPARE-OUTPUT-003",
                            output=output,
                            switch_backend_evidence_path=evidence_path,
                        )
        self.assertEqual(collision.exception.reason, "upgrade_request_collision")

        for protected in (
            self.new_source / "request.json",
            self.skills_root / "request.json",
        ):
            with self.subTest(protected=protected):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    with mock.patch.dict(
                        os.environ,
                        {"CODEX_HOME": str(self.codex_home)},
                    ):
                        INSTALLER.prepare_upgrade(
                            self.new_source,
                            self.target,
                            self.manifest,
                            approval_id="ROUTE-A-PREPARE-PROTECTED",
                            output=protected,
                            switch_backend_evidence_path=evidence_path,
                        )
                self.assertEqual(raised.exception.reason, "unsafe_upgrade_output")
                self.assertFalse(protected.exists())

    def test_route_a_keeps_candidate_inventory_rejection(self):
        evidence_path = self.write_valid_evidence()
        duplicate = {
            "state": "DUPLICATE",
            "proof_kind": "PROCESS_FREE_SOURCE_LAYOUT",
            "discovery_id": SKILL_NAME,
            "declared_name": SKILL_NAME,
            "enabled": True,
            "duplicate_count": 2,
            "load_errors": [],
            "future_locator": str(self.target / "SKILL.md"),
            "source_skill_sha256": "2" * 64,
        }
        with mock.patch.object(
            INSTALLER,
            "candidate_inventory_identity",
            return_value=duplicate,
        ):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                self.build_route_a(evidence_path)
        self.assertEqual(raised.exception.reason, "candidate_inventory_not_unique")


class NoReplaceUpgradeJournalTests(unittest.TestCase):
    SOURCE_HEAD = "1" * 40
    PHASES = (
        "PREPARED",
        "OLD_SNAPSHOT_READY",
        "OLD_TARGET_ARCHIVED",
        "OLD_STATE_ARCHIVED",
        "NEW_TARGET_ACTIVE",
        "NEW_STATE_ACTIVE",
        "VERIFIED",
    )

    def setUp(self):
        InstallSkillTests.setUp(self)
        self.old_source = InstallSkillTests.make_versioned_source(
            self,
            "journal-old-source",
        )
        self.new_source = InstallSkillTests.make_versioned_source(
            self,
            "journal-new-source",
        )
        result = InstallSkillTests.run_cli(
            self,
            "install",
            "--source",
            self.old_source,
            "--skills-root",
            self.skills_root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def capability(self, target=None):
        target = target or self.target
        snapshot = INSTALLER.directory_identity(target, "unsafe_target_entry")
        snapshot["tree_digest"] = INSTALLER.canonical_tree_digest(
            INSTALLER.scan_tree(target)
        )
        return (
            "NOREPLACE_ONLY",
            {
                "capability": "NOREPLACE_ONLY",
                "exchange_attempted": True,
                "exchange_result": "UNSUPPORTED",
                "noreplace_attempted": True,
                "noreplace_result": "VERIFIED",
                "postconditions_verified": True,
                "restored": True,
                "left_identity_before": snapshot,
                "right_identity_before": None,
                "left_identity_after": None,
                "right_identity_after": snapshot,
                "left_identity_restored": snapshot,
                "right_identity_restored": None,
            },
        )

    def build_request(self, source=None, target=None, manifest=None):
        source = source or self.new_source
        target = target or self.target
        manifest = manifest or self.manifest
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(target.parent.parent)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(target),
                ):
                    return INSTALLER.build_upgrade_request(
                        source,
                        target,
                        manifest,
                        approval_id="UPGRADE-JOURNAL-TEST-001",
                    )

    def write_request(self, request, root=None):
        root = root or self.tempdir
        path = root / f"request-{request['operation_id']}.json"
        path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def recovery_directory(self, request, codex_home=None):
        codex_home = codex_home or self.codex_home
        return (
            codex_home
            / INSTALLER.RECOVERY_ROOT_NAME
            / request["operation_id"]
        )

    def journal_receipts(self, request, codex_home=None):
        journal = self.recovery_directory(request, codex_home) / "journal"
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(journal.glob("*.json"))
        ]

    def run_upgrade(self, request, *, phase_hook=None, codex_home=None):
        codex_home = codex_home or self.codex_home
        request_path = self.write_request(request)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(Path(request["target"])),
                ):
                    with redirect_stdout(stdout):
                        code = INSTALLER.upgrade(
                            request_path,
                            request["request_digest"],
                            phase_hook=phase_hook,
                        )
        return code, json.loads(stdout.getvalue())

    def run_upgrade_main(self, request, *, codex_home=None, hook_error=None):
        codex_home = codex_home or self.codex_home
        request_path = self.write_request(request)
        arguments = [
            str(SCRIPT),
            "upgrade",
            "--request",
            str(request_path),
            "--confirm-request",
            request["request_digest"],
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        hook = (
            mock.patch.object(INSTALLER, "_call_phase_hook", side_effect=hook_error)
            if hook_error is not None
            else mock.patch.object(
                INSTALLER,
                "_call_phase_hook",
                wraps=INSTALLER._call_phase_hook,
            )
        )
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(Path(request["target"])),
                ):
                    with mock.patch.object(sys, "argv", arguments):
                        with hook:
                            with redirect_stdout(stdout), redirect_stderr(stderr):
                                try:
                                    code = INSTALLER.main()
                                except SystemExit as error:
                                    code = int(error.code)
        payload = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        return code, payload, stderr.getvalue()

    def make_case(self, label):
        root = self.tempdir / label
        codex_home = root / "codex-home"
        skills_root = codex_home / "skills"
        skills_root.mkdir(parents=True)
        old_source = root / "old" / SKILL_NAME
        new_source = root / "new" / SKILL_NAME
        shutil.copytree(self.old_source, old_source)
        shutil.copytree(self.new_source, new_source)
        target = skills_root / SKILL_NAME
        state = skills_root / f".{SKILL_NAME}-install"
        manifest = state / INSTALLER.MANIFEST_NAME
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with redirect_stdout(io.StringIO()):
                code = INSTALLER.install(old_source, skills_root)
        self.assertEqual(code, 0)
        request = self.build_request(new_source, target, manifest)
        return codex_home, target, state, manifest, request

    def test_noreplace_phase_graph_is_exact(self):
        self.assertEqual(
            INSTALLER.upgrade_phase_graph("NOREPLACE_ONLY"),
            self.PHASES,
        )
        for previous, phase in zip(self.PHASES, self.PHASES[1:]):
            self.assertTrue(
                INSTALLER.validate_upgrade_transition(
                    "NOREPLACE_ONLY",
                    previous,
                    phase,
                )
                is None
            )

        for previous, phase in (
            ("PREPARED", "OLD_TARGET_ARCHIVED"),
            ("NEW_STATE_ACTIVE", "OLD_STATE_ARCHIVED"),
            ("VERIFIED", "UNKNOWN"),
            ("UNKNOWN", "VERIFIED"),
            ("RECOVERY_REQUIRED", "VERIFIED"),
        ):
            with self.subTest(previous=previous, phase=phase):
                with self.assertRaises(INSTALLER.InstallError):
                    INSTALLER.validate_upgrade_transition(
                        "NOREPLACE_ONLY",
                        previous,
                        phase,
                    )

    def test_invalid_phase_or_capability_is_rejected(self):
        for capability, previous, phase in (
            ("UNKNOWN", "PREPARED", "OLD_SNAPSHOT_READY"),
            ("NOREPLACE_ONLY", "OLD_SNAPSHOT_READY", "TARGET_EXCHANGED"),
            ("EXCHANGE_SUPPORTED", "OLD_SNAPSHOT_READY", "OLD_TARGET_ARCHIVED"),
            ("NOT_A_CAPABILITY", "PREPARED", "OLD_SNAPSHOT_READY"),
        ):
            with self.subTest(capability=capability, phase=phase):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.validate_upgrade_transition(
                        capability,
                        previous,
                        phase,
                    )
                self.assertEqual(raised.exception.reason, "upgrade_phase_invalid")

    def test_old_snapshot_ready_does_not_claim_old_target_archived(self):
        request = self.build_request()

        def stop_after_snapshot(phase):
            if phase == "OLD_SNAPSHOT_READY":
                raise INSTALLER.InstallError(
                    "injected_interruption",
                    4,
                    status="unknown",
                )

        with self.assertRaises(INSTALLER.InstallError) as raised:
            self.run_upgrade(request, phase_hook=stop_after_snapshot)

        self.assertEqual(raised.exception.exit_code, 4)
        snapshot = next(
            receipt
            for receipt in self.journal_receipts(request)
            if receipt["phase"] == "OLD_SNAPSHOT_READY"
        )
        observed = snapshot["observed_postconditions"]
        self.assertIsNotNone(observed["active_target"])
        self.assertIsNotNone(observed["active_state"])
        self.assertIsNone(observed["recovery_target"])
        self.assertIsNone(observed["recovery_state"])
        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.state_dir.is_dir())

    def test_target_and_state_archive_phases_are_distinct(self):
        request = self.build_request()
        code, payload = self.run_upgrade(request)
        self.assertEqual(code, 0, payload)
        receipts = {item["phase"]: item for item in self.journal_receipts(request)}

        target_phase = receipts["OLD_TARGET_ARCHIVED"]["observed_postconditions"]
        self.assertIsNone(target_phase["active_target"])
        self.assertIsNotNone(target_phase["active_state"])
        self.assertIsNotNone(target_phase["recovery_target"])
        self.assertIsNone(target_phase["recovery_state"])

        state_phase = receipts["OLD_STATE_ARCHIVED"]["observed_postconditions"]
        self.assertIsNone(state_phase["active_target"])
        self.assertIsNone(state_phase["active_state"])
        self.assertIsNotNone(state_phase["recovery_target"])
        self.assertIsNotNone(state_phase["recovery_state"])

    def test_interruption_after_each_phase_is_unknown_or_recoverable(self):
        for selected_phase in self.PHASES[:-1]:
            with self.subTest(phase=selected_phase):
                codex_home, _, _, _, request = self.make_case(
                    f"interrupt-{selected_phase.lower()}"
                )

                def interrupt(phase, selected=selected_phase):
                    if phase == selected:
                        raise INSTALLER.InstallError(
                            "injected_interruption",
                            4,
                            status="unknown",
                        )

                with self.assertRaises(INSTALLER.InstallError) as raised:
                    self.run_upgrade(
                        request,
                        phase_hook=interrupt,
                        codex_home=codex_home,
                    )

                self.assertEqual(raised.exception.exit_code, 4)
                phases = [
                    item["phase"]
                    for item in self.journal_receipts(request, codex_home)
                ]
                self.assertIn(selected_phase, phases)
                self.assertIn(phases[-1], {"UNKNOWN", "RECOVERY_REQUIRED"})
                self.assertNotIn("VERIFIED", phases)

    def test_unverified_candidate_never_replaces_active_target(self):
        request = self.build_request()
        old_target_digest = request["old_target_tree_digest"]
        real_copy = INSTALLER.copy_entries

        def copy_then_corrupt(source, destination, entries):
            real_copy(source, destination, entries)
            if Path(source) == self.new_source:
                (Path(destination) / "SKILL.md").write_text(
                    "corrupt\n",
                    encoding="utf-8",
                )

        with mock.patch.object(
            INSTALLER,
            "copy_entries",
            side_effect=copy_then_corrupt,
        ):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                self.run_upgrade(request)

        self.assertEqual(raised.exception.exit_code, 3)
        self.assertEqual(
            INSTALLER.canonical_tree_digest(INSTALLER.scan_tree(self.target)),
            old_target_digest,
        )
        self.assertTrue(self.state_dir.is_dir())
        phases = [item["phase"] for item in self.journal_receipts(request)]
        self.assertNotIn("OLD_TARGET_ARCHIVED", phases)
        self.assertNotIn("NEW_TARGET_ACTIVE", phases)

    def test_candidate_identity_replacement_stops_before_old_archive(self):
        for selected_key in ("stage_target", "stage_state"):
            with self.subTest(selected_key=selected_key):
                codex_home, target, state, _, request = self.make_case(
                    f"candidate-replacement-{selected_key}"
                )
                paths = INSTALLER._upgrade_paths(request, codex_home)

                def replace_after_snapshot(phase, selected=selected_key):
                    if phase != "OLD_SNAPSHOT_READY":
                        return
                    original = paths[selected]
                    displaced = original.with_name(original.name + "-displaced")
                    original.rename(displaced)
                    shutil.copytree(displaced, original)

                with self.assertRaises(INSTALLER.InstallError) as raised:
                    self.run_upgrade(
                        request,
                        phase_hook=replace_after_snapshot,
                        codex_home=codex_home,
                    )

                self.assertEqual(raised.exception.exit_code, 3)
                self.assertTrue(target.is_dir())
                self.assertTrue(state.is_dir())
                recovery = self.recovery_directory(request, codex_home)
                self.assertFalse((recovery / SKILL_NAME).exists())
                self.assertFalse((recovery / state.name).exists())
                phases = [
                    item["phase"]
                    for item in self.journal_receipts(request, codex_home)
                ]
                self.assertNotIn("OLD_TARGET_ARCHIVED", phases)

    def test_success_receipt_binds_old_archive_and_new_manifest(self):
        request = self.build_request()
        code, payload = self.run_upgrade(request)
        self.assertEqual(code, 0, payload)
        receipt_path = Path(payload["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = INSTALLER.load_manifest(self.manifest)
        recovery = self.recovery_directory(request)

        self.assertEqual(receipt["status"], "VERIFIED")
        self.assertEqual(receipt["request_digest"], request["request_digest"])
        self.assertEqual(
            receipt["old_archive"]["recovery_directory"],
            str(recovery),
        )
        self.assertEqual(
            receipt["old_archive"]["manifest_digest"],
            request["old_manifest_digest"],
        )
        self.assertEqual(
            receipt["new_active"]["manifest_digest"],
            manifest["manifest_digest"],
        )
        self.assertEqual(
            receipt["new_active"]["target_tree_digest"],
            INSTALLER.canonical_tree_digest(INSTALLER.scan_tree(self.target)),
        )
        self.assertTrue((recovery / SKILL_NAME).is_dir())
        self.assertTrue((recovery / self.state_dir.name).is_dir())
        receipts = self.journal_receipts(request)
        candidate_identities = {
            json.dumps(item["new_stage_identity"], sort_keys=True)
            for item in receipts
            if item["phase"] not in {"PREPARED", "UNKNOWN", "RECOVERY_REQUIRED"}
        }
        self.assertEqual(len(candidate_identities), 1)
        self.assertNotEqual(candidate_identities, {"null"})
        self.assertEqual(
            receipt["journal_final_digest"],
            next(
                item["receipt_digest"]
                for item in receipts
                if item["phase"] == "VERIFIED"
            ),
        )

    def test_upgrade_cli_requires_exact_confirmation(self):
        request = self.build_request()
        request_path = self.write_request(request)
        arguments = [
            str(SCRIPT),
            "upgrade",
            "--request",
            str(request_path),
            "--confirm-request",
            "0" * 64,
        ]
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with mock.patch.object(sys, "argv", arguments):
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    try:
                        code = INSTALLER.main()
                    except SystemExit as error:
                        code = int(error.code)

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["reason"], "confirmation_mismatch")
        self.assertFalse(self.recovery_directory(request).exists())

    def test_upgrade_rejects_path_control_operation_id_before_write(self):
        request = self.build_request()
        request["operation_id"] = "../escaped-operation"
        request["request_digest"] = INSTALLER.upgrade_request_digest(request)

        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.validate_upgrade_request(request)

        self.assertEqual(raised.exception.reason, "upgrade_request_invalid")
        self.assertFalse((self.codex_home / "escaped-operation").exists())
        self.assertFalse((self.codex_home / INSTALLER.RECOVERY_ROOT_NAME).exists())

    def test_upgrade_cli_exit_codes_are_2_3_4_and_unknown_keeps_evidence(self):
        collision_request = self.build_request()
        collision_recovery = self.recovery_directory(collision_request)
        collision_recovery.mkdir(parents=True)
        collision_code, collision, collision_stderr = self.run_upgrade_main(
            collision_request
        )
        self.assertEqual(collision_code, 2, collision_stderr)
        self.assertEqual(collision["reason"], "upgrade_recovery_collision")

        drift_home, _, _, _, drift_request = self.make_case("cli-drift")
        Path(drift_request["source"]).joinpath("SKILL.md").write_text(
            "drift\n",
            encoding="utf-8",
        )
        drift_code, drift, drift_stderr = self.run_upgrade_main(
            drift_request,
            codex_home=drift_home,
        )
        self.assertEqual(drift_code, 3, drift_stderr)
        self.assertEqual(drift["status"], "drift")

        unknown_home, _, _, _, unknown_request = self.make_case("cli-unknown")
        interruption = INSTALLER.InstallError(
            "injected_interruption",
            4,
            status="unknown",
        )
        unknown_code, unknown, unknown_stderr = self.run_upgrade_main(
            unknown_request,
            codex_home=unknown_home,
            hook_error=interruption,
        )
        self.assertEqual(unknown_code, 4, unknown_stderr)
        self.assertEqual(unknown["status"], "unknown")
        recovery = self.recovery_directory(unknown_request, unknown_home)
        self.assertTrue((recovery / "upgrade-request.json").is_file())
        phases = [
            item["phase"]
            for item in self.journal_receipts(unknown_request, unknown_home)
        ]
        self.assertEqual(phases, ["PREPARED", "UNKNOWN"])


class RouteAUpgradeCase:
    def __init__(
        self,
        owner,
        root,
        codex_home,
        new_source,
        target,
        state,
        request,
        filesystem,
        volume,
        upgrade_evidence,
    ):
        self.owner = owner
        self.root = root
        self.codex_home = codex_home
        self.new_source = new_source
        self.target = target
        self.state = state
        self.request = request
        self.filesystem = filesystem
        self.volume = volume
        self.upgrade_evidence = upgrade_evidence
        self.recovery = (
            codex_home / INSTALLER.RECOVERY_ROOT_NAME / request["operation_id"]
        )
        self.journal = self.recovery / "journal"
        self.request_path = root / "upgrade-request.json"
        self.request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.move_calls = []
        self.volume_mock = None

    def receipts(self):
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.journal.glob("*.json"))
        ]

    def default_move(self, backend, source, destination):
        source = Path(source)
        destination = Path(destination)
        self.move_calls.append((backend, source.name, destination.name))
        os.rename(source, destination)
        return {
            "backend": backend,
            "classification": "VERIFIED",
            "movefileex_call_count": 1,
            "win32_error": 0,
        }

    def run_upgrade(
        self,
        *,
        phase_hook=None,
        volume_result=None,
        dispatcher=True,
        windows_result=None,
    ):
        stdout = io.StringIO()
        volume_result = self.volume if volume_result is None else volume_result
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "read_source_head",
                    return_value=RouteAUpgradeJournalTests.SOURCE_HEAD,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=self.filesystem,
                )
            )
            if isinstance(volume_result, BaseException):
                self.volume_mock = stack.enter_context(
                    mock.patch.object(
                        INSTALLER,
                        "windows_volume_identity",
                        side_effect=volume_result,
                    )
                )
            else:
                self.volume_mock = stack.enter_context(
                    mock.patch.object(
                        INSTALLER,
                        "windows_volume_identity",
                        return_value=volume_result,
                    )
                )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "rename_noreplace",
                    side_effect=AssertionError("route A called generic rename"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "renameat2_direct",
                    side_effect=AssertionError("route A called POSIX backend"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    side_effect=AssertionError("route A called POSIX probe"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "probe_noreplace_capability",
                    side_effect=AssertionError("route A called noreplace probe"),
                )
            )
            if dispatcher:
                stack.enter_context(
                    mock.patch.object(
                        INSTALLER,
                        "move_directory_for_backend",
                        side_effect=self.default_move,
                    )
                )
            if windows_result is not None:
                stack.enter_context(
                    mock.patch.object(
                        INSTALLER,
                        "windows_movefileex_noreplace",
                        return_value=windows_result,
                    )
                )
            stack.enter_context(redirect_stdout(stdout))
            code = INSTALLER.upgrade(
                self.request_path,
                self.request["request_digest"],
                phase_hook=phase_hook,
            )
        payload = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        return code, payload

    def interrupt_after(self, phase):
        def interrupt(observed_phase):
            if observed_phase == phase:
                raise INSTALLER.InstallError(
                    "injected_route_a_interruption",
                    4,
                    status="unknown",
                )

        with self.owner.assertRaises(INSTALLER.InstallError) as raised:
            self.run_upgrade(phase_hook=interrupt)
        return raised.exception

    def inspect(self):
        return INSTALLER.inspect_upgrade(self.journal)


class RouteAUpgradeJournalTests(unittest.TestCase):
    SOURCE_HEAD = "1" * 40

    def setUp(self):
        InstallSkillTests.setUp(self)
        self.old_source = InstallSkillTests.make_versioned_source(
            self,
            "route-a-journal-old-source",
        )
        self.new_source = InstallSkillTests.make_versioned_source(
            self,
            "route-a-journal-new-source",
        )
        self.case_number = 0

    def new_route_a_case(self):
        self.case_number += 1
        root = self.tempdir / f"route-a-case-{self.case_number}"
        codex_home = root / "codex-home"
        skills_root = codex_home / "skills"
        skills_root.mkdir(parents=True)
        old_source = root / "old" / SKILL_NAME
        new_source = root / "new" / SKILL_NAME
        shutil.copytree(self.old_source, old_source)
        shutil.copytree(self.new_source, new_source)
        target = skills_root / SKILL_NAME
        state = skills_root / f".{SKILL_NAME}-install"
        manifest = state / INSTALLER.MANIFEST_NAME
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with redirect_stdout(io.StringIO()):
                code = INSTALLER.install(old_source, skills_root)
        self.assertEqual(code, 0)

        filesystem = {
            "device": skills_root.stat().st_dev,
            "mount_target": "/mnt/c",
            "filesystem_type": "9p",
            "mount_options_sha256": "c" * 64,
        }
        volume = {
            "drive": "C:",
            "filesystem_name": "NTFS",
            "volume_serial": "12AB34CD",
        }
        evidence = valid_backend_evidence_fixture(skills_root.resolve())
        evidence["skills_root_stable_identity"] = INSTALLER.stable_directory_identity(
            skills_root,
            "test",
        )
        evidence["target_filesystem_identity"] = copy.deepcopy(filesystem)
        evidence["wsl_mount_identity"] = {
            key: filesystem[key]
            for key in INSTALLER.WSL_MOUNT_IDENTITY_KEYS
        }
        evidence["windows_volume_identity"] = copy.deepcopy(volume)
        evidence["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(
            evidence
        )
        evidence_path = root / "switch-backend-evidence.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "read_source_head",
                return_value=self.SOURCE_HEAD,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=filesystem,
                ):
                    request = INSTALLER.build_upgrade_request(
                        new_source,
                        target,
                        manifest,
                        approval_id="ROUTE-A-UPGRADE-001",
                        switch_backend_evidence_path=evidence_path,
                    )
        return RouteAUpgradeCase(
            self,
            root,
            codex_home,
            new_source,
            target,
            state,
            request,
            filesystem,
            volume,
            evidence_path,
        )

    def test_route_a_upgrade_uses_only_bound_backend_and_writes_v2_receipts(self):
        case = self.new_route_a_case()
        code, payload = case.run_upgrade()

        self.assertEqual(code, 0, payload)
        self.assertEqual(
            tuple(receipt["phase"] for receipt in case.receipts()),
            INSTALLER.NOREPLACE_UPGRADE_PHASES,
        )
        expected_moves = (
            (case.target.name, case.target.name),
            (case.state.name, case.state.name),
            (
                f".{SKILL_NAME}-upgrade-{case.request['operation_id']}-target",
                case.target.name,
            ),
            (
                f".{SKILL_NAME}-upgrade-{case.request['operation_id']}-state",
                case.state.name,
            ),
        )
        self.assertEqual(
            tuple((source, destination) for _, source, destination in case.move_calls),
            expected_moves,
        )
        self.assertEqual(
            {backend for backend, _, _ in case.move_calls},
            {INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE},
        )
        case.volume_mock.assert_called_once_with(case.target.parent)

        binding_keys = (
            "switch_backend",
            "switch_evidence_digest",
            "backend_implementation_digest",
        )
        for receipt in case.receipts():
            self.assertEqual(
                receipt["journal_schema_version"],
                INSTALLER.ROUTE_A_JOURNAL_SCHEMA_VERSION,
            )
            self.assertEqual(set(receipt), INSTALLER.UPGRADE_JOURNAL_RECEIPT_V2_KEYS)
            for key in binding_keys:
                self.assertEqual(receipt[key], case.request[key])

        success = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(success["receipt_schema_version"], 2)
        self.assertEqual(set(success), INSTALLER.UPGRADE_SUCCESS_RECEIPT_V2_KEYS)
        self.assertEqual(
            success["gap_disclosure"],
            "TARGET_AND_STATE_SWITCHED_SEPARATELY",
        )
        for key in binding_keys:
            self.assertEqual(success[key], case.request[key])
        self.assertEqual(
            success["journal_final_digest"],
            case.receipts()[-1]["receipt_digest"],
        )
        self.assertTrue(Path(success["old_archive"]["target"]).is_dir())
        self.assertTrue(Path(success["old_archive"]["state"]).is_dir())

    def test_volume_identity_unknown_or_drift_stops_before_all_moves(self):
        cases = (
            (
                "unknown",
                INSTALLER.InstallError(
                    "windows_volume_identity_unknown",
                    4,
                    status="unknown",
                ),
                4,
                "UNKNOWN",
            ),
            (
                "drift",
                {
                    "drive": "D:",
                    "filesystem_name": "NTFS",
                    "volume_serial": "12AB34CD",
                },
                3,
                "RECOVERY_REQUIRED",
            ),
        )
        for label, volume_result, exit_code, terminal in cases:
            with self.subTest(label=label):
                case = self.new_route_a_case()
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    case.run_upgrade(volume_result=volume_result)
                self.assertEqual(raised.exception.exit_code, exit_code)
                self.assertEqual(case.move_calls, [])
                self.assertEqual(case.receipts()[-1]["phase"], terminal)
                case.volume_mock.assert_called_once_with(case.target.parent)

    def test_adapter_unknown_or_collision_is_not_retried_or_reversed(self):
        outcomes = (
            (
                "unknown",
                {
                    "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                    "classification": "UNKNOWN",
                    "movefileex_call_count": 1,
                    "win32_error": 5,
                },
                "UNKNOWN",
                4,
            ),
            (
                "collision",
                {
                    "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                    "classification": "TARGET_EXISTS",
                    "movefileex_call_count": 1,
                    "win32_error": 183,
                },
                "RECOVERY_REQUIRED",
                3,
            ),
        )
        for label, result, terminal, exit_code in outcomes:
            with self.subTest(label=label):
                case = self.new_route_a_case()
                with mock.patch.object(
                    INSTALLER,
                    "windows_movefileex_noreplace",
                    return_value=result,
                ) as adapter:
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        case.run_upgrade(dispatcher=False)
                self.assertEqual(raised.exception.exit_code, exit_code)
                adapter.assert_called_once()
                self.assertEqual(case.receipts()[-1]["phase"], terminal)
                self.assertTrue(case.target.is_dir())
                self.assertTrue(case.state.is_dir())

    def test_interruption_after_every_durable_phase_matches_last_receipt(self):
        for phase in INSTALLER.NOREPLACE_UPGRADE_PHASES:
            with self.subTest(phase=phase):
                case = self.new_route_a_case()
                raised = case.interrupt_after(phase)
                self.assertEqual(raised.exit_code, 4)
                receipts = case.receipts()
                self.assertIn(phase, [receipt["phase"] for receipt in receipts])
                self.assertFalse((case.recovery / "upgrade-success-receipt.json").exists())
                if phase == "VERIFIED":
                    self.assertEqual(receipts[-1]["phase"], "VERIFIED")
                else:
                    self.assertIn(
                        receipts[-1]["phase"],
                        {"UNKNOWN", "RECOVERY_REQUIRED"},
                    )
                paths = INSTALLER._upgrade_paths(case.request, case.codex_home)
                current = INSTALLER._upgrade_observed(paths)
                self.assertEqual(
                    current,
                    receipts[-1]["observed_postconditions"],
                )

    def test_route_a_request_binding_mismatch_fails_before_write(self):
        case = self.new_route_a_case()
        case.request["switch_backend"] = "POSIX_RENAME"
        case.request["request_digest"] = INSTALLER.upgrade_request_digest(case.request)
        case.request_path.write_text(json.dumps(case.request), encoding="utf-8")
        with self.assertRaises(INSTALLER.InstallError) as raised:
            case.run_upgrade()
        self.assertEqual(raised.exception.reason, "upgrade_request_invalid")
        self.assertFalse(case.recovery.exists())


class RouteAUpgradeInspectionTests(unittest.TestCase):
    SOURCE_HEAD = RouteAUpgradeJournalTests.SOURCE_HEAD
    new_route_a_case = RouteAUpgradeJournalTests.new_route_a_case

    def setUp(self):
        RouteAUpgradeJournalTests.setUp(self)

    def completed_route_a_case(self):
        case = self.new_route_a_case()
        code, payload = case.run_upgrade()
        self.assertEqual(code, 0, payload)
        return case

    @staticmethod
    def file_snapshot(root):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_v2_inspection_is_read_only_binding_strict_and_verified(self):
        case = self.completed_route_a_case()
        before = self.file_snapshot(case.recovery)
        with mock.patch.object(
            INSTALLER.subprocess,
            "run",
            side_effect=AssertionError("inspection started a process"),
        ):
            result = INSTALLER.inspect_upgrade(case.journal)
        after = self.file_snapshot(case.recovery)

        self.assertEqual(result["classification"], "VERIFIED")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["upgrade_schema_version"], 2)
        self.assertEqual(
            set(result),
            INSTALLER.UPGRADE_INSPECTION_V2_KEYS,
        )
        for key in (
            "switch_backend",
            "switch_evidence_digest",
            "backend_implementation_digest",
        ):
            self.assertEqual(result[key], case.request[key])
        self.assertEqual(before, after)

    def test_verified_phase_without_success_receipt_is_unknown(self):
        case = self.completed_route_a_case()
        (case.recovery / "upgrade-success-receipt.json").unlink()

        result = INSTALLER.inspect_upgrade(case.journal)

        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["last_phase"], "VERIFIED")
        self.assertEqual(result["reasons"], ["MISSING_VERIFIED_RECEIPT"])

    def test_request_phase_and_success_binding_mismatch_are_invalid(self):
        for location in ("request", "phase", "success"):
            with self.subTest(location=location):
                case = self.completed_route_a_case()
                if location == "request":
                    path = case.recovery / "upgrade-request.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["switch_evidence_digest"] = "0" * 64
                    value["request_digest"] = INSTALLER.upgrade_request_digest(value)
                elif location == "phase":
                    path = sorted(case.journal.glob("*.json"))[2]
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["switch_evidence_digest"] = "0" * 64
                    value["receipt_digest"] = INSTALLER.journal_receipt_digest(value)
                else:
                    path = case.recovery / "upgrade-success-receipt.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["backend_implementation_digest"] = "0" * 64
                    value["receipt_digest"] = INSTALLER.success_receipt_digest(value)
                path.write_text(json.dumps(value), encoding="utf-8")

                with self.assertRaises(INSTALLER.InstallError) as raised:
                    INSTALLER.inspect_upgrade(case.journal)
                self.assertEqual(raised.exception.reason, "upgrade_journal_invalid")

    def test_contradiction_is_unknown_and_content_only_change_is_drift(self):
        contradiction = self.completed_route_a_case()
        os.rename(
            contradiction.target,
            contradiction.target.with_name(contradiction.target.name + "-moved"),
        )
        result = INSTALLER.inspect_upgrade(contradiction.journal)
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["reasons"], ["CONTRADICTORY_POSTCONDITIONS"])

        drift = self.completed_route_a_case()
        (drift.target / "SKILL.md").write_text("drift\n", encoding="utf-8")
        result = INSTALLER.inspect_upgrade(drift.journal)
        self.assertEqual(result["classification"], "DRIFT")
        self.assertIn("ACTIVE_TARGET_CONTENT_DRIFT", result["reasons"])

    def test_v1_shape_relabelled_as_v2_cannot_verify(self):
        case = self.completed_route_a_case()
        request_path = case.recovery / "upgrade-request.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        for key in INSTALLER.ROUTE_A_BINDING_KEYS:
            request.pop(key)
        snapshot = dict(request["old_target_identity"])
        snapshot["tree_digest"] = request["old_target_tree_digest"]
        request["switch_evidence"] = {
            "capability": "NOREPLACE_ONLY",
            "exchange_attempted": True,
            "exchange_result": "UNSUPPORTED",
            "noreplace_attempted": True,
            "noreplace_result": "VERIFIED",
            "postconditions_verified": True,
            "restored": True,
            "left_identity_before": snapshot,
            "right_identity_before": None,
            "left_identity_after": None,
            "right_identity_after": snapshot,
            "left_identity_restored": snapshot,
            "right_identity_restored": None,
        }
        request["upgrade_schema_version"] = 2
        request["journal_schema_version"] = 2
        request["request_digest"] = INSTALLER.upgrade_request_digest(request)
        request_path.write_text(json.dumps(request), encoding="utf-8")

        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.inspect_upgrade(case.journal)


class ExchangeUpgradeInspectionTests(unittest.TestCase):
    SOURCE_HEAD = NoReplaceUpgradeJournalTests.SOURCE_HEAD
    PHASES = (
        "PREPARED",
        "OLD_SNAPSHOT_READY",
        "TARGET_EXCHANGED",
        "OLD_TARGET_ARCHIVED",
        "STATE_EXCHANGED",
        "OLD_STATE_ARCHIVED",
        "VERIFIED",
    )
    setUp = NoReplaceUpgradeJournalTests.setUp
    build_request = NoReplaceUpgradeJournalTests.build_request
    write_request = NoReplaceUpgradeJournalTests.write_request
    recovery_directory = NoReplaceUpgradeJournalTests.recovery_directory
    journal_receipts = NoReplaceUpgradeJournalTests.journal_receipts

    def capability(self, target=None):
        target = target or self.target
        state = target.parent / f".{SKILL_NAME}-install"
        left = INSTALLER.directory_identity(target, "unsafe_target_entry")
        left["tree_digest"] = INSTALLER.canonical_tree_digest(
            INSTALLER.scan_tree(target)
        )
        right = INSTALLER.directory_identity(state, "unsafe_install_state")
        right["tree_digest"] = INSTALLER.canonical_tree_digest(
            INSTALLER.scan_tree(state)
        )
        return (
            "EXCHANGE_SUPPORTED",
            {
                "capability": "EXCHANGE_SUPPORTED",
                "exchange_attempted": True,
                "exchange_result": "VERIFIED",
                "noreplace_attempted": False,
                "noreplace_result": "NOT_ATTEMPTED",
                "postconditions_verified": True,
                "restored": True,
                "left_identity_before": left,
                "right_identity_before": right,
                "left_identity_after": right,
                "right_identity_after": left,
                "left_identity_restored": left,
                "right_identity_restored": right,
            },
        )

    @staticmethod
    def fake_exchange(source, destination, flags):
        if flags != INSTALLER.RENAME_EXCHANGE:
            raise AssertionError("exchange tests accept only RENAME_EXCHANGE")
        source = Path(source)
        destination = Path(destination)
        temporary = source.parent / f".{source.name}-exchange-test-temp"
        if temporary.exists() or temporary.is_symlink():
            raise AssertionError("test exchange temporary path collision")
        os.rename(source, temporary)
        os.rename(destination, source)
        os.rename(temporary, destination)

    def run_upgrade(self, request, *, phase_hook=None, codex_home=None):
        with mock.patch.object(
            INSTALLER,
            "renameat2_direct",
            side_effect=self.fake_exchange,
        ):
            return NoReplaceUpgradeJournalTests.run_upgrade(
                self,
                request,
                phase_hook=phase_hook,
                codex_home=codex_home,
            )

    def byte_snapshot(self, root):
        root = Path(root)
        result = []
        for path in [root, *sorted(root.rglob("*"))]:
            metadata = path.lstat()
            relative = "." if path == root else path.relative_to(root).as_posix()
            if stat.S_ISDIR(metadata.st_mode):
                kind = "directory"
                digest = None
            elif stat.S_ISREG(metadata.st_mode):
                kind = "file"
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            elif stat.S_ISLNK(metadata.st_mode):
                kind = "symlink"
                digest = os.readlink(path)
            else:
                kind = "other"
                digest = None
            result.append(
                (
                    relative,
                    kind,
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    digest,
                )
            )
        return result

    def rewrite_receipt_and_relink(self, request, phase, mutate):
        journal = self.recovery_directory(request) / "journal"
        paths = sorted(journal.glob("*.json"))
        previous_digest = None
        for path in paths:
            value = json.loads(path.read_text(encoding="utf-8"))
            value["previous_phase_digest"] = previous_digest
            if value["phase"] == phase:
                mutate(value)
            value["receipt_digest"] = INSTALLER.journal_receipt_digest(value)
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            previous_digest = value["receipt_digest"]

    def test_exchange_phase_graph_is_exact(self):
        self.assertEqual(
            INSTALLER.upgrade_phase_graph("EXCHANGE_SUPPORTED"),
            self.PHASES,
        )
        for previous, phase in zip(self.PHASES, self.PHASES[1:]):
            self.assertTrue(
                INSTALLER.validate_upgrade_transition(
                    "EXCHANGE_SUPPORTED",
                    previous,
                    phase,
                )
                is None
            )
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.validate_upgrade_transition(
                "EXCHANGE_SUPPORTED",
                "OLD_SNAPSHOT_READY",
                "OLD_TARGET_ARCHIVED",
            )

    def test_target_exchanged_requires_old_target_identity(self):
        request = self.build_request()

        def stop(phase):
            if phase == "TARGET_EXCHANGED":
                raise INSTALLER.InstallError(
                    "injected_interruption",
                    4,
                    status="unknown",
                )

        with self.assertRaises(INSTALLER.InstallError):
            self.run_upgrade(request, phase_hook=stop)

        receipt = next(
            item
            for item in self.journal_receipts(request)
            if item["phase"] == "TARGET_EXCHANGED"
        )
        observed = receipt["observed_postconditions"]
        expected_old = dict(request["old_target_identity"])
        expected_old["tree_digest"] = request["old_target_tree_digest"]
        self.assertEqual(observed["staging_target"], expected_old)
        self.assertEqual(observed["active_target"], receipt["new_stage_identity"])
        self.assertEqual(
            observed["active_state"]["tree_digest"],
            request["old_state_tree_digest"],
        )
        self.assertIsNone(observed["recovery_target"])

        self.rewrite_receipt_and_relink(
            request,
            "TARGET_EXCHANGED",
            lambda value: value["observed_postconditions"].__setitem__(
                "staging_target",
                None,
            ),
        )
        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.inspect_upgrade(self.recovery_directory(request) / "journal")
        self.assertEqual(raised.exception.reason, "upgrade_journal_invalid")

    def test_state_exchanged_requires_old_state_identity(self):
        request = self.build_request()

        def stop(phase):
            if phase == "STATE_EXCHANGED":
                raise INSTALLER.InstallError(
                    "injected_interruption",
                    4,
                    status="unknown",
                )

        with self.assertRaises(INSTALLER.InstallError):
            self.run_upgrade(request, phase_hook=stop)

        receipt = next(
            item
            for item in self.journal_receipts(request)
            if item["phase"] == "STATE_EXCHANGED"
        )
        observed = receipt["observed_postconditions"]
        expected_old = dict(request["old_state_identity"])
        expected_old["tree_digest"] = request["old_state_tree_digest"]
        self.assertEqual(observed["staging_state"], expected_old)
        self.assertIsNotNone(observed["active_state"])
        self.assertNotEqual(
            observed["active_state"]["tree_digest"],
            request["old_state_tree_digest"],
        )
        self.assertIsNotNone(observed["recovery_target"])
        self.assertIsNone(observed["recovery_state"])

        self.rewrite_receipt_and_relink(
            request,
            "STATE_EXCHANGED",
            lambda value: value["observed_postconditions"].__setitem__(
                "staging_state",
                None,
            ),
        )
        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.inspect_upgrade(self.recovery_directory(request) / "journal")
        self.assertEqual(raised.exception.reason, "upgrade_journal_invalid")

    def test_inspect_rejects_noreplace_journal_with_exchange_phase(self):
        def stop(phase):
            if phase == "OLD_SNAPSHOT_READY":
                raise INSTALLER.InstallError(
                    "injected_interruption",
                    4,
                    status="unknown",
                )

        with mock.patch.object(
            self,
            "capability",
            side_effect=lambda target=None: NoReplaceUpgradeJournalTests.capability(
                self,
                target,
            ),
        ):
            request = NoReplaceUpgradeJournalTests.build_request(self)
            with self.assertRaises(INSTALLER.InstallError):
                NoReplaceUpgradeJournalTests.run_upgrade(
                    self,
                    request,
                    phase_hook=stop,
                )

        journal = self.recovery_directory(request) / "journal"
        terminal = next(journal.glob("002-unknown.json"))
        terminal.unlink()
        original = next(journal.glob("001-old-snapshot-ready.json"))
        value = json.loads(original.read_text(encoding="utf-8"))
        value["phase"] = "TARGET_EXCHANGED"
        value["receipt_digest"] = INSTALLER.journal_receipt_digest(value)
        replacement = journal / "001-target-exchanged.json"
        replacement.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        original.unlink()

        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.inspect_upgrade(journal)
        self.assertEqual(raised.exception.reason, "upgrade_journal_invalid")

    def test_inspect_reconstructs_unknown_after_process_crash(self):
        request = self.build_request()

        class ProcessCrash(BaseException):
            pass

        def crash(phase):
            if phase == "TARGET_EXCHANGED":
                raise ProcessCrash()

        with self.assertRaises(ProcessCrash):
            self.run_upgrade(request, phase_hook=crash)

        journal = self.recovery_directory(request) / "journal"
        result = INSTALLER.inspect_upgrade(journal)
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["last_phase"], "TARGET_EXCHANGED")
        self.assertEqual(result["exit_code"], 4)
        self.assertFalse((self.recovery_directory(request) / "upgrade-success-receipt.json").exists())

    def test_inspect_is_byte_level_read_only(self):
        request = self.build_request()
        code, payload = self.run_upgrade(request)
        self.assertEqual(code, 0, payload)
        journal = self.recovery_directory(request) / "journal"
        before = self.byte_snapshot(self.codex_home)

        first = INSTALLER.inspect_upgrade(journal)
        second = INSTALLER.inspect_upgrade(journal)

        self.assertEqual(first, second)
        self.assertEqual(first["classification"], "VERIFIED")
        self.assertEqual(self.byte_snapshot(self.codex_home), before)

    def test_contradictory_postconditions_never_become_verified(self):
        request = self.build_request()
        code, payload = self.run_upgrade(request)
        self.assertEqual(code, 0, payload)
        recovery = self.recovery_directory(request)
        displaced = self.target.with_name(f".{SKILL_NAME}-displaced-new")
        self.target.rename(displaced)
        shutil.copytree(recovery / SKILL_NAME, self.target)

        journal = recovery / "journal"
        result = INSTALLER.inspect_upgrade(journal)
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["exit_code"], 4)

        stdout = io.StringIO()
        arguments = [str(SCRIPT), "inspect-upgrade", "--journal", str(journal)]
        with mock.patch.object(sys, "argv", arguments):
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = INSTALLER.main()
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "unknown")

    def test_inspect_classifies_in_place_content_change_as_drift(self):
        request = self.build_request()
        code, payload = self.run_upgrade(request)
        self.assertEqual(code, 0, payload)
        skill_file = self.target / "SKILL.md"
        before_inode = skill_file.stat().st_ino
        skill_file.write_text("drift\n", encoding="utf-8")
        self.assertEqual(skill_file.stat().st_ino, before_inode)

        result = INSTALLER.inspect_upgrade(
            self.recovery_directory(request) / "journal"
        )

        self.assertEqual(result["classification"], "DRIFT")
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["reasons"], ["ACTIVE_TARGET_CONTENT_DRIFT"])

        stdout = io.StringIO()
        arguments = [
            str(SCRIPT),
            "inspect-upgrade",
            "--journal",
            str(self.recovery_directory(request) / "journal"),
        ]
        with mock.patch.object(sys, "argv", arguments):
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = INSTALLER.main()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "drift")


class RouteARestoreCase:
    def __init__(self, owner, upgrade_case, success_receipt):
        self.owner = owner
        self.upgrade_case = upgrade_case
        self.codex_home = upgrade_case.codex_home
        self.target = upgrade_case.target
        self.state = upgrade_case.state
        self.filesystem = upgrade_case.filesystem
        self.volume = upgrade_case.volume
        self.recovery_root = self.codex_home / INSTALLER.RECOVERY_ROOT_NAME
        self.success_receipt = success_receipt
        self.success = json.loads(success_receipt.read_text(encoding="utf-8"))
        self.restore_confirmation_digest = self.success[
            "restore_confirmation_digest"
        ]
        self.upgrade_evidence = upgrade_case.upgrade_evidence
        self.move_calls = []
        self.volume_mock = None

    def fresh_evidence(self, label="fresh"):
        evidence = json.loads(self.upgrade_evidence.read_text(encoding="utf-8"))
        evidence_id = hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]
        probe_id = hashlib.sha256((label + "-probe").encode("utf-8")).hexdigest()[:32]
        evidence["evidence_id"] = evidence_id
        evidence["approval_id"] = f"RESTORE-ATTEST-{label.upper()}"
        evidence["probe_identity"]["probe_id"] = probe_id
        evidence["probe_identity"]["probe_root"] = str(
            self.target.parent
            / f".{SKILL_NAME}-switch-probe-{probe_id}"
        )
        evidence["created_at_utc"] = "2026-09-07T01:00:00Z"
        evidence["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(
            evidence
        )
        path = self.upgrade_case.root / f"restore-evidence-{label}.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return path

    def recovery_directories(self):
        return sorted(
            path
            for path in self.recovery_root.iterdir()
            if path.name.startswith("restore-")
        )

    def build_request(self, evidence_path, approval_id="ROUTE-A-RESTORE-001"):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=self.filesystem,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER.subprocess,
                    "run",
                    side_effect=AssertionError("restore prepare started a process"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    side_effect=AssertionError("route A restore called POSIX probe"),
                )
            )
            return INSTALLER.build_restore_request(
                self.success_receipt,
                self.restore_confirmation_digest,
                approval_id,
                switch_backend_evidence_path=evidence_path,
            )

    def default_move(self, backend, source, destination):
        source = Path(source)
        destination = Path(destination)
        self.move_calls.append((backend, source.name, destination.name))
        os.rename(source, destination)
        return {
            "backend": backend,
            "classification": "VERIFIED",
            "movefileex_call_count": 1,
            "win32_error": 0,
        }

    def run_restore(
        self,
        evidence_path,
        *,
        phase_hook=None,
        dispatcher=True,
        windows_result=None,
    ):
        stdout = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "filesystem_identity",
                    return_value=self.filesystem,
                )
            )
            self.volume_mock = stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "windows_volume_identity",
                    return_value=self.volume,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "rename_noreplace",
                    side_effect=AssertionError("route A restore called generic rename"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "renameat2_direct",
                    side_effect=AssertionError("route A restore called POSIX backend"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    side_effect=AssertionError("route A restore called POSIX probe"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    INSTALLER,
                    "probe_noreplace_capability",
                    side_effect=AssertionError("route A restore called noreplace probe"),
                )
            )
            if dispatcher:
                stack.enter_context(
                    mock.patch.object(
                        INSTALLER,
                        "move_directory_for_backend",
                        side_effect=self.default_move,
                    )
                )
            if windows_result is not None:
                stack.enter_context(
                    mock.patch.object(
                        INSTALLER,
                        "windows_movefileex_noreplace",
                        return_value=windows_result,
                    )
                )
            stack.enter_context(redirect_stdout(stdout))
            code = INSTALLER.restore_version(
                self.success_receipt,
                self.restore_confirmation_digest,
                "ROUTE-A-RESTORE-001",
                switch_backend_evidence_path=evidence_path,
                phase_hook=phase_hook,
            )
        payload = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        return code, payload


class RouteARestoreTests(unittest.TestCase):
    SOURCE_HEAD = RouteAUpgradeJournalTests.SOURCE_HEAD
    new_route_a_case = RouteAUpgradeJournalTests.new_route_a_case

    def setUp(self):
        RouteAUpgradeJournalTests.setUp(self)

    def completed_route_a_upgrade(self):
        upgrade_case = self.new_route_a_case()
        code, payload = upgrade_case.run_upgrade()
        self.assertEqual(code, 0, payload)
        return RouteARestoreCase(
            self,
            upgrade_case,
            Path(payload["receipt"]),
        )

    def test_v2_restore_requires_fresh_evidence_and_new_approval(self):
        case = self.completed_route_a_upgrade()
        recovery_before = sorted(case.recovery_root.iterdir())

        with mock.patch.dict(
            os.environ,
            {"CODEX_HOME": str(case.codex_home)},
        ):
            with self.assertRaises(INSTALLER.InstallError) as missing:
                INSTALLER.build_restore_request(
                    case.success_receipt,
                    case.restore_confirmation_digest,
                    "ROUTE-A-RESTORE-001",
                )
        self.assertEqual(missing.exception.reason, "restore_evidence_required")

        with self.assertRaises(INSTALLER.InstallError) as reused:
            case.build_request(case.upgrade_evidence)
        self.assertEqual(reused.exception.reason, "restore_evidence_must_be_fresh")

        fresh = case.fresh_evidence("approval")
        with self.assertRaises(INSTALLER.InstallError) as approval:
            case.build_request(fresh, approval_id=case.success["approval_id"])
        self.assertEqual(approval.exception.reason, "restore_approval_must_be_new")
        self.assertEqual(sorted(case.recovery_root.iterdir()), recovery_before)

    def test_v2_restore_uses_only_bound_backend_and_seals_v2_chain(self):
        case = self.completed_route_a_upgrade()
        fresh = case.fresh_evidence("success")
        fresh_value = json.loads(fresh.read_text(encoding="utf-8"))

        code, payload = case.run_restore(fresh)

        self.assertEqual(code, 0, payload)
        self.assertEqual(len(case.move_calls), 4)
        self.assertEqual(
            {backend for backend, _, _ in case.move_calls},
            {INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE},
        )
        case.volume_mock.assert_called_once_with(case.target.parent)
        recovery = Path(payload["recovery_directory"])
        request = json.loads(
            (recovery / "restore-request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(request["restore_schema_version"], 2)
        self.assertEqual(set(request), INSTALLER.RESTORE_REQUEST_V2_KEYS)
        self.assertEqual(
            request["switch_evidence_digest"],
            fresh_value["evidence_digest"],
        )
        journal_receipts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((recovery / "journal").glob("*.json"))
        ]
        self.assertEqual(
            tuple(value["phase"] for value in journal_receipts),
            INSTALLER.NOREPLACE_RESTORE_PHASES,
        )
        for value in journal_receipts:
            self.assertEqual(set(value), INSTALLER.RESTORE_JOURNAL_RECEIPT_V2_KEYS)
            self.assertEqual(value["journal_schema_version"], 2)
            for key in (
                "switch_backend",
                "switch_evidence_digest",
                "backend_implementation_digest",
            ):
                self.assertEqual(value[key], request[key])
        success = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(success["receipt_schema_version"], 2)
        self.assertEqual(set(success), INSTALLER.RESTORE_SUCCESS_RECEIPT_V2_KEYS)
        self.assertEqual(success["force_reload_state"], "PENDING_CALLER_VERIFICATION")
        for key in (
            "switch_backend",
            "switch_evidence_digest",
            "backend_implementation_digest",
        ):
            self.assertEqual(success[key], request[key])

    def test_restore_evidence_or_source_drift_stops_before_recovery_write(self):
        case = self.completed_route_a_upgrade()
        evidence_path = case.fresh_evidence("drift")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["windows_volume_identity"]["drive"] = "D:"
        evidence["evidence_digest"] = INSTALLER.switch_backend_evidence_digest(
            evidence
        )
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        before = sorted(case.recovery_root.iterdir())
        with self.assertRaises(INSTALLER.InstallError):
            case.build_request(evidence_path)
        self.assertEqual(sorted(case.recovery_root.iterdir()), before)

        source_drift = self.completed_route_a_upgrade()
        fresh = source_drift.fresh_evidence("source-drift")
        (source_drift.target / "SKILL.md").write_text("drift\n", encoding="utf-8")
        before = sorted(source_drift.recovery_root.iterdir())
        with self.assertRaises(INSTALLER.InstallError) as raised:
            source_drift.build_request(fresh)
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertEqual(sorted(source_drift.recovery_root.iterdir()), before)

    def test_unknown_or_collision_move_is_single_call_and_preserves_stages(self):
        outcomes = (
            (
                "unknown",
                {
                    "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                    "classification": "UNKNOWN",
                    "movefileex_call_count": 1,
                    "win32_error": 5,
                },
                "UNKNOWN",
                4,
            ),
            (
                "collision",
                {
                    "backend": INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
                    "classification": "TARGET_EXISTS",
                    "movefileex_call_count": 1,
                    "win32_error": 183,
                },
                "RECOVERY_REQUIRED",
                3,
            ),
        )
        for label, result, terminal, exit_code in outcomes:
            with self.subTest(label=label):
                case = self.completed_route_a_upgrade()
                fresh = case.fresh_evidence(label)
                with mock.patch.object(
                    INSTALLER,
                    "windows_movefileex_noreplace",
                    return_value=result,
                ) as adapter:
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        case.run_restore(
                            fresh,
                            dispatcher=False,
                        )
                self.assertEqual(raised.exception.exit_code, exit_code)
                adapter.assert_called_once()
                recovery = case.recovery_directories()[0]
                receipts = [
                    json.loads(path.read_text(encoding="utf-8"))
                    for path in sorted((recovery / "journal").glob("*.json"))
                ]
                self.assertEqual(receipts[-1]["phase"], terminal)
                request = json.loads(
                    (recovery / "restore-request.json").read_text(encoding="utf-8")
                )
                paths = INSTALLER._restore_paths(request, case.codex_home)
                self.assertTrue(paths["stage_target"].is_dir())
                self.assertTrue(paths["stage_state"].is_dir())
                self.assertTrue(case.target.is_dir())
                self.assertTrue(case.state.is_dir())

    def test_interruption_after_every_restore_phase_matches_durable_receipt(self):
        expected_move_counts = {
            "PREPARED": 0,
            "ARCHIVE_COPY_READY": 0,
            "CURRENT_TARGET_ARCHIVED": 1,
            "CURRENT_STATE_ARCHIVED": 2,
            "RESTORED_TARGET_ACTIVE": 3,
            "RESTORED_STATE_ACTIVE": 4,
            "VERIFIED": 4,
        }
        for phase in INSTALLER.NOREPLACE_RESTORE_PHASES:
            with self.subTest(phase=phase):
                case = self.completed_route_a_upgrade()
                fresh = case.fresh_evidence("interrupt-" + phase.lower())

                def interrupt(observed_phase, selected=phase):
                    if observed_phase == selected:
                        raise INSTALLER.InstallError(
                            "injected_route_a_restore_interruption",
                            4,
                            status="unknown",
                        )

                with self.assertRaises(INSTALLER.InstallError) as raised:
                    case.run_restore(fresh, phase_hook=interrupt)
                self.assertEqual(raised.exception.exit_code, 4)
                self.assertEqual(len(case.move_calls), expected_move_counts[phase])
                recovery = case.recovery_directories()[0]
                self.assertFalse((recovery / "restore-success-receipt.json").exists())
                receipts = [
                    json.loads(path.read_text(encoding="utf-8"))
                    for path in sorted((recovery / "journal").glob("*.json"))
                ]
                self.assertIn(phase, [value["phase"] for value in receipts])
                if phase == "VERIFIED":
                    self.assertEqual(receipts[-1]["phase"], "VERIFIED")
                else:
                    self.assertIn(
                        receipts[-1]["phase"],
                        {"UNKNOWN", "RECOVERY_REQUIRED"},
                    )
                request = json.loads(
                    (recovery / "restore-request.json").read_text(encoding="utf-8")
                )
                paths = INSTALLER._restore_paths(request, case.codex_home)
                self.assertEqual(
                    INSTALLER._restore_observed(paths),
                    receipts[-1]["observed_postconditions"],
                )

    def test_restore_cli_accepts_explicit_evidence_path(self):
        case = self.completed_route_a_upgrade()
        fresh = case.fresh_evidence("cli")
        argv = [
            str(SCRIPT),
            "restore-version",
            "--receipt",
            str(case.success_receipt),
            "--approval-id",
            "ROUTE-A-RESTORE-CLI-001",
            "--confirm",
            case.restore_confirmation_digest,
            "--switch-backend-evidence",
            str(fresh),
        ]
        with mock.patch.object(sys, "argv", argv):
            args = INSTALLER.parse_args()
        self.assertEqual(args.switch_backend_evidence, fresh)


class ReceiptBoundRestoreAndToggleTests(unittest.TestCase):
    SOURCE_HEAD = NoReplaceUpgradeJournalTests.SOURCE_HEAD
    setUp = NoReplaceUpgradeJournalTests.setUp
    capability = NoReplaceUpgradeJournalTests.capability
    build_request = NoReplaceUpgradeJournalTests.build_request
    write_request = NoReplaceUpgradeJournalTests.write_request
    recovery_directory = NoReplaceUpgradeJournalTests.recovery_directory
    journal_receipts = NoReplaceUpgradeJournalTests.journal_receipts
    run_upgrade = NoReplaceUpgradeJournalTests.run_upgrade
    make_case = NoReplaceUpgradeJournalTests.make_case

    def successful_case(self, label):
        codex_home, target, state, manifest, request = self.make_case(label)
        code, payload = self.run_upgrade(request, codex_home=codex_home)
        self.assertEqual(code, 0, payload)
        receipt_path = Path(payload["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        return codex_home, target, state, manifest, request, receipt_path, receipt

    def run_restore(self, codex_home, receipt_path, receipt, *, approval_id=None):
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "probe_switch_capability",
                return_value=self.capability(Path(receipt["new_active"]["target"])),
            ):
                with redirect_stdout(stdout):
                    code = INSTALLER.restore_version(
                        receipt_path,
                        receipt["restore_confirmation_digest"],
                        approval_id or "RESTORE-VERSION-TEST-001",
                    )
        return code, json.loads(stdout.getvalue())

    @staticmethod
    def fake_exchange(source, destination, flags):
        if flags != INSTALLER.RENAME_EXCHANGE:
            raise AssertionError("restore exchange requires RENAME_EXCHANGE")
        source = Path(source)
        destination = Path(destination)
        temporary = source.parent / f".{source.name}-restore-exchange-temp"
        if temporary.exists() or temporary.is_symlink():
            raise AssertionError("restore exchange temporary path collision")
        os.rename(source, temporary)
        os.rename(destination, source)
        os.rename(temporary, destination)

    def exchange_capability(self, target=None):
        return ExchangeUpgradeInspectionTests.capability(self, target)

    def run_restore_main(
        self,
        codex_home,
        receipt_path,
        confirmation,
        approval_id,
    ):
        arguments = [
            str(SCRIPT),
            "restore-version",
            "--receipt",
            str(receipt_path),
            "--approval-id",
            approval_id,
            "--confirm",
            confirmation,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(sys, "argv", arguments):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    try:
                        code = INSTALLER.main()
                    except SystemExit as error:
                        code = int(error.code)
        payload = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        return code, payload, stderr.getvalue()

    @staticmethod
    def directory_tree_digest(path):
        return INSTALLER.canonical_tree_digest(INSTALLER.scan_tree(path))

    def toggle_inventory(self, target=None, *, enabled=True):
        target = target or self.target
        return {
            "schema_version": 2,
            "inventory_sha256": "4" * 64,
            "load_errors": [],
            "skills": [
                {
                    "name": SKILL_NAME,
                    "declared_name": SKILL_NAME,
                    "source_namespace": "standalone",
                    "path": str(target / "SKILL.md"),
                    "scope": "user",
                    "enabled": enabled,
                }
            ],
        }

    def test_restore_accepts_only_success_receipt_archive(self):
        (
            codex_home,
            _,
            _,
            _,
            request,
            receipt_path,
            receipt,
        ) = self.successful_case("restore-receipt-source")
        journal_receipt = sorted(
            (self.recovery_directory(request, codex_home) / "journal").glob("*.json")
        )[0]

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as wrong_schema:
                INSTALLER.restore_version(
                    journal_receipt,
                    receipt["restore_confirmation_digest"],
                    "RESTORE-RECEIPT-TEST-001",
                )
        self.assertEqual(wrong_schema.exception.exit_code, 2)

        copied_receipt = self.tempdir / "copied-upgrade-success-receipt.json"
        shutil.copy2(receipt_path, copied_receipt)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as unbound_copy:
                INSTALLER.restore_version(
                    copied_receipt,
                    receipt["restore_confirmation_digest"],
                    "RESTORE-RECEIPT-TEST-002",
                )
        self.assertEqual(unbound_copy.exception.reason, "restore_receipt_location_invalid")
        self.assertEqual(unbound_copy.exception.exit_code, 2)

        receipt["restore_confirmation_digest"] = "f" * 64
        receipt["receipt_digest"] = INSTALLER.success_receipt_digest(receipt)
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as tampered_confirmation:
                INSTALLER.build_restore_request(
                    receipt_path,
                    "f" * 64,
                    "RESTORE-RECEIPT-TEST-003",
                )
        self.assertEqual(
            tampered_confirmation.exception.reason,
            "restore_receipt_invalid",
        )

    def test_restore_requires_new_confirmation_and_approval_id(self):
        (
            codex_home,
            _,
            _,
            _,
            _,
            receipt_path,
            receipt,
        ) = self.successful_case("restore-approval")
        recovery_root = codex_home / INSTALLER.RECOVERY_ROOT_NAME
        before = sorted(path.name for path in recovery_root.iterdir())

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as wrong_confirmation:
                INSTALLER.restore_version(
                    receipt_path,
                    "0" * 64,
                    "RESTORE-APPROVAL-TEST-001",
                )
        self.assertEqual(wrong_confirmation.exception.reason, "confirmation_mismatch")
        self.assertEqual(wrong_confirmation.exception.exit_code, 2)

        cli_code, cli_payload, cli_stderr = self.run_restore_main(
            codex_home,
            receipt_path,
            "0" * 64,
            "RESTORE-APPROVAL-CLI-001",
        )
        self.assertEqual(cli_code, 2, cli_stderr)
        self.assertEqual(cli_payload["reason"], "confirmation_mismatch")

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as inherited_approval:
                INSTALLER.restore_version(
                    receipt_path,
                    receipt["restore_confirmation_digest"],
                    receipt["approval_id"],
                )
        self.assertEqual(
            inherited_approval.exception.reason,
            "restore_approval_must_be_new",
        )
        self.assertEqual(inherited_approval.exception.exit_code, 2)
        self.assertEqual(
            sorted(path.name for path in recovery_root.iterdir()),
            before,
        )

    def test_restore_archives_current_new_version(self):
        (
            codex_home,
            target,
            state,
            manifest,
            _,
            receipt_path,
            receipt,
        ) = self.successful_case("restore-archives-new")
        old_archive = receipt["old_archive"]
        active_new_target_digest = self.directory_tree_digest(target)
        active_new_state_digest = self.directory_tree_digest(state)
        original_old_target_digest = self.directory_tree_digest(
            Path(old_archive["target"])
        )
        original_old_state_digest = self.directory_tree_digest(
            Path(old_archive["state"])
        )
        original_receipt = receipt_path.read_bytes()

        code, payload = self.run_restore(codex_home, receipt_path, receipt)

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "restore_verified")
        new_archive = Path(payload["recovery_directory"])
        self.assertEqual(
            self.directory_tree_digest(target),
            original_old_target_digest,
        )
        self.assertEqual(
            self.directory_tree_digest(state),
            original_old_state_digest,
        )
        self.assertEqual(
            INSTALLER.load_manifest(manifest)["manifest_digest"],
            old_archive["manifest_digest"],
        )
        self.assertEqual(
            self.directory_tree_digest(new_archive / SKILL_NAME),
            active_new_target_digest,
        )
        self.assertEqual(
            self.directory_tree_digest(new_archive / state.name),
            active_new_state_digest,
        )
        self.assertEqual(
            self.directory_tree_digest(Path(old_archive["target"])),
            original_old_target_digest,
        )
        self.assertEqual(
            self.directory_tree_digest(Path(old_archive["state"])),
            original_old_state_digest,
        )
        self.assertEqual(receipt_path.read_bytes(), original_receipt)

        restore_receipt = json.loads(
            Path(payload["receipt"]).read_text(encoding="utf-8")
        )
        journal = Path(restore_receipt["journal"])
        phases = [
            json.loads(path.read_text(encoding="utf-8"))["phase"]
            for path in sorted(journal.glob("*.json"))
        ]
        self.assertEqual(phases, list(INSTALLER.NOREPLACE_RESTORE_PHASES))
        previous_digest = None
        for path in sorted(journal.glob("*.json")):
            phase_receipt = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                phase_receipt["previous_phase_digest"],
                previous_digest,
            )
            previous_digest = phase_receipt["receipt_digest"]
        self.assertEqual(restore_receipt["journal_final_digest"], previous_digest)

    def test_restore_exchange_path_has_exact_phase_chain(self):
        with mock.patch.object(
            self,
            "capability",
            side_effect=self.exchange_capability,
        ):
            with mock.patch.object(
                INSTALLER,
                "renameat2_direct",
                side_effect=self.fake_exchange,
            ):
                (
                    codex_home,
                    target,
                    state,
                    _,
                    _,
                    receipt_path,
                    receipt,
                ) = self.successful_case("restore-exchange")
                active_target_digest = self.directory_tree_digest(target)
                active_state_digest = self.directory_tree_digest(state)
                old_target_digest = self.directory_tree_digest(
                    Path(receipt["old_archive"]["target"])
                )
                old_state_digest = self.directory_tree_digest(
                    Path(receipt["old_archive"]["state"])
                )
                code, payload = self.run_restore(
                    codex_home,
                    receipt_path,
                    receipt,
                )

        self.assertEqual(code, 0, payload)
        self.assertEqual(self.directory_tree_digest(target), old_target_digest)
        self.assertEqual(self.directory_tree_digest(state), old_state_digest)
        recovery = Path(payload["recovery_directory"])
        self.assertEqual(
            self.directory_tree_digest(recovery / SKILL_NAME),
            active_target_digest,
        )
        self.assertEqual(
            self.directory_tree_digest(recovery / state.name),
            active_state_digest,
        )
        restore_receipt = json.loads(
            Path(payload["receipt"]).read_text(encoding="utf-8")
        )
        phases = [
            json.loads(path.read_text(encoding="utf-8"))["phase"]
            for path in sorted(Path(restore_receipt["journal"]).glob("*.json"))
        ]
        self.assertEqual(phases, list(INSTALLER.EXCHANGE_RESTORE_PHASES))
        self.assertEqual(
            restore_receipt["gap_disclosure"],
            "TARGET_AND_STATE_EXCHANGED_SEPARATELY",
        )

    def test_restore_interruption_records_last_durable_phase_and_unknown(self):
        (
            codex_home,
            target,
            state,
            _,
            _,
            receipt_path,
            receipt,
        ) = self.successful_case("restore-interruption")
        original_write_phase = INSTALLER._write_restore_phase

        def interrupt_after_target_archive(*args, **kwargs):
            phase_receipt = original_write_phase(*args, **kwargs)
            if phase_receipt["phase"] == "CURRENT_TARGET_ARCHIVED":
                raise INSTALLER.InstallError(
                    "injected_restore_interruption",
                    4,
                    status="unknown",
                )
            return phase_receipt

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "probe_switch_capability",
                return_value=self.capability(target),
            ):
                with mock.patch.object(
                    INSTALLER,
                    "_write_restore_phase",
                    side_effect=interrupt_after_target_archive,
                ):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.restore_version(
                            receipt_path,
                            receipt["restore_confirmation_digest"],
                            "RESTORE-INTERRUPTION-TEST-001",
                        )

        self.assertEqual(raised.exception.exit_code, 4)
        restore_directories = sorted(
            path
            for path in (codex_home / INSTALLER.RECOVERY_ROOT_NAME).iterdir()
            if path.name.startswith("restore-")
        )
        self.assertEqual(len(restore_directories), 1)
        recovery = restore_directories[0]
        phase_paths = sorted((recovery / "journal").glob("*.json"))
        self.assertEqual(
            [path.name.split("-", 1)[0] for path in phase_paths],
            ["000", "001", "002", "003"],
        )
        phase_receipts = [
            json.loads(path.read_text(encoding="utf-8")) for path in phase_paths
        ]
        self.assertEqual(
            [item["phase"] for item in phase_receipts],
            [
                "PREPARED",
                "ARCHIVE_COPY_READY",
                "CURRENT_TARGET_ARCHIVED",
                "UNKNOWN",
            ],
        )
        self.assertIsNone(phase_receipts[-1]["observed_postconditions"]["active_target"])
        self.assertIsNotNone(phase_receipts[-1]["observed_postconditions"]["active_state"])
        self.assertIsNotNone(phase_receipts[-1]["observed_postconditions"]["staging_target"])
        self.assertIsNotNone(phase_receipts[-1]["observed_postconditions"]["recovery_target"])
        for previous, current in zip(phase_receipts, phase_receipts[1:]):
            self.assertEqual(
                current["previous_phase_digest"],
                previous["receipt_digest"],
            )
        self.assertFalse(target.exists())
        self.assertTrue(state.is_dir())

    def test_restore_never_overwrites_history(self):
        (
            codex_home,
            _,
            _,
            _,
            _,
            receipt_path,
            receipt,
        ) = self.successful_case("restore-collision")
        fixed_operation = "a" * 32
        collision = (
            codex_home
            / INSTALLER.RECOVERY_ROOT_NAME
            / f"restore-{fixed_operation}"
        )
        collision.mkdir()
        marker = collision / "owned-by-history"
        marker.write_text("preserve", encoding="utf-8")
        original_receipt = receipt_path.read_bytes()

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER.uuid,
                "uuid4",
                return_value=mock.Mock(hex=fixed_operation),
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_switch_capability",
                    return_value=self.capability(
                        Path(receipt["new_active"]["target"])
                    ),
                ):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.restore_version(
                            receipt_path,
                            receipt["restore_confirmation_digest"],
                            "RESTORE-COLLISION-TEST-001",
                        )

        self.assertEqual(raised.exception.reason, "restore_recovery_collision")
        self.assertEqual(raised.exception.exit_code, 2)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(receipt_path.read_bytes(), original_receipt)

    def test_restore_rejects_active_or_archive_drift(self):
        for drift_kind in ("active", "archive"):
            with self.subTest(drift_kind=drift_kind):
                (
                    codex_home,
                    target,
                    _,
                    _,
                    _,
                    receipt_path,
                    receipt,
                ) = self.successful_case(f"restore-drift-{drift_kind}")
                drift_target = (
                    target
                    if drift_kind == "active"
                    else Path(receipt["old_archive"]["target"])
                )
                (drift_target / "references" / "SKILL_INDEX_ZH.md").write_text(
                    f"# {drift_kind} drift\n",
                    encoding="utf-8",
                )
                recovery_root = codex_home / INSTALLER.RECOVERY_ROOT_NAME
                before = sorted(path.name for path in recovery_root.iterdir())

                with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.restore_version(
                            receipt_path,
                            receipt["restore_confirmation_digest"],
                            f"RESTORE-DRIFT-{drift_kind.upper()}-001",
                        )

                self.assertEqual(raised.exception.exit_code, 3)
                self.assertEqual(
                    sorted(path.name for path in recovery_root.iterdir()),
                    before,
                )

    def test_restore_rejects_source_receipt_drift_before_active_switch(self):
        (
            codex_home,
            target,
            state,
            _,
            _,
            receipt_path,
            receipt,
        ) = self.successful_case("restore-source-receipt-drift")
        active_target_digest = self.directory_tree_digest(target)
        active_state_digest = self.directory_tree_digest(state)
        original_copy = INSTALLER._copy_restore_archive

        def copy_then_drift(request, paths):
            original_copy(request, paths)
            receipt_path.write_bytes(receipt_path.read_bytes() + b" ")

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            with mock.patch.object(
                INSTALLER,
                "probe_switch_capability",
                return_value=self.capability(target),
            ):
                with mock.patch.object(
                    INSTALLER,
                    "_copy_restore_archive",
                    side_effect=copy_then_drift,
                ):
                    with self.assertRaises(INSTALLER.InstallError) as raised:
                        INSTALLER.restore_version(
                            receipt_path,
                            receipt["restore_confirmation_digest"],
                            "RESTORE-RECEIPT-DRIFT-TEST-001",
                        )

        self.assertEqual(raised.exception.reason, "restore_source_receipt_drift")
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertEqual(self.directory_tree_digest(target), active_target_digest)
        self.assertEqual(self.directory_tree_digest(state), active_state_digest)

    def test_toggle_request_binds_exact_locator_and_config_identity(self):
        config = self.codex_home / "config.toml"
        config.write_text("preserve = true\n", encoding="utf-8")
        locator = self.target / "SKILL.md"
        inventory = self.toggle_inventory()

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            request = INSTALLER.build_toggle_request(
                config,
                inventory,
                locator=locator,
                enabled=False,
                approval_id="TOGGLE-REQUEST-TEST-001",
            )

        self.assertEqual(request["config_path"], str(config))
        self.assertEqual(request["locator"], str(locator))
        self.assertEqual(request["declared_name"], SKILL_NAME)
        self.assertTrue(request["current_enabled"])
        self.assertFalse(request["requested_enabled"])
        self.assertEqual(
            request["native_request"],
            {
                "method": "skills/config/write",
                "params": {"path": str(locator), "enabled": False},
            },
        )
        self.assertEqual(
            request["config_sha256"],
            hashlib.sha256(config.read_bytes()).hexdigest(),
        )
        self.assertEqual(request["config_identity"]["inode"], config.stat().st_ino)

        duplicate = copy.deepcopy(inventory)
        duplicate["skills"].append(
            {
                **inventory["skills"][0],
                "name": f"duplicate:{SKILL_NAME}",
                "path": str(self.tempdir / "duplicate" / "SKILL.md"),
            }
        )
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.build_toggle_request(
                    config,
                    duplicate,
                    locator=locator,
                    enabled=False,
                    approval_id="TOGGLE-REQUEST-TEST-002",
                )
        self.assertEqual(raised.exception.reason, "toggle_supervisor_not_unique")

    def test_toggle_request_does_not_edit_config(self):
        config = self.codex_home / "config.toml"
        config.write_text("preserve = true\n", encoding="utf-8")
        locator = self.target / "SKILL.md"
        inventory = self.toggle_inventory()
        before_bytes = config.read_bytes()
        before_stat = config.stat()

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            request = INSTALLER.build_toggle_request(
                config,
                inventory,
                locator=locator,
                enabled=False,
                approval_id="TOGGLE-NOWRITE-TEST-001",
            )
            self.assertTrue(
                INSTALLER.validate_toggle_request(request, config, inventory) is None
            )

        after_stat = config.stat()
        self.assertEqual(config.read_bytes(), before_bytes)
        self.assertEqual(after_stat.st_ino, before_stat.st_ino)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)

        config.write_text("preserve = false\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.validate_toggle_request(request, config, inventory)
        self.assertEqual(raised.exception.reason, "toggle_config_drift")
        self.assertEqual(raised.exception.exit_code, 3)

    def test_toggle_request_rejects_symlinked_skill_directory(self):
        config = self.codex_home / "config.toml"
        config.write_text("preserve = true\n", encoding="utf-8")
        outside_target = self.tempdir / "outside" / SKILL_NAME
        outside_target.parent.mkdir()
        self.target.rename(outside_target)
        self.target.symlink_to(outside_target, target_is_directory=True)
        locator = self.target / "SKILL.md"
        inventory = self.toggle_inventory()

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER.build_toggle_request(
                    config,
                    inventory,
                    locator=locator,
                    enabled=False,
                    approval_id="TOGGLE-SYMLINK-TEST-001",
                )

        self.assertEqual(raised.exception.reason, "toggle_locator_invalid")

    def test_toggle_request_rejects_hardlinked_config_or_locator(self):
        config = self.codex_home / "config.toml"
        config.write_text("preserve = true\n", encoding="utf-8")
        locator = self.target / "SKILL.md"
        inventory = self.toggle_inventory()

        config_alias = self.tempdir / "config-hardlink"
        os.link(config, config_alias)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as config_error:
                INSTALLER.build_toggle_request(
                    config,
                    inventory,
                    locator=locator,
                    enabled=False,
                    approval_id="TOGGLE-HARDLINK-CONFIG-001",
                )
        self.assertEqual(config_error.exception.reason, "toggle_config_invalid")
        config_alias.unlink()

        locator_alias = self.tempdir / "locator-hardlink"
        os.link(locator, locator_alias)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with self.assertRaises(INSTALLER.InstallError) as locator_error:
                INSTALLER.build_toggle_request(
                    config,
                    inventory,
                    locator=locator,
                    enabled=False,
                    approval_id="TOGGLE-HARDLINK-LOCATOR-001",
                )
        self.assertEqual(locator_error.exception.reason, "toggle_locator_invalid")


class TargetObservedInstallTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self._tempdir.cleanup)
        self.tempdir = Path(self._tempdir.name)
        self.codex_home = self.tempdir / "codex-home"
        self.skills_root = self.codex_home / "skills"
        self.skills_root.mkdir(parents=True)
        self.source = self.tempdir / "source" / SKILL_NAME
        (self.source / "agents").mkdir(parents=True)
        (self.source / "references").mkdir()
        (self.source / "SKILL.md").write_text(
            "---\nname: vibe-project-lead-zh\ndescription: test\n---\n",
            encoding="utf-8",
        )
        (self.source / "agents" / "openai.yaml").write_text(
            'interface:\n  display_name: "中文跨项目研发主管"\n',
            encoding="utf-8",
        )
        for filename in (
            "project-binding.md",
            "manager-workflow.md",
            "safety-gates.md",
            "acceptance-and-supervision.md",
            "adaptive-delegation.md",
            "portfolio.md",
            "deployment-governance.md",
            "human-delivery.md",
            "SKILL_INDEX_ZH.md",
        ):
            (self.source / "references" / filename).write_text(
                f"# {filename}\n",
                encoding="utf-8",
            )
        self.source.chmod(0o755)
        (self.source / "agents").chmod(0o755)
        (self.source / "references").chmod(0o755)
        for child in self.source.rglob("*"):
            if child.is_file():
                child.chmod(0o600 if child.name == "SKILL_INDEX_ZH.md" else 0o644)
        self.target = self.skills_root / SKILL_NAME
        self.state_dir = self.skills_root / f".{SKILL_NAME}-install"
        self.prepared_manifest = self.state_dir / "prepared-manifest.json"
        self.manifest = self.state_dir / "install-manifest.json"

    def manifest_payload(self):
        return InstallSkillTests.manifest_payload(self)

    def install_with_mode_folding(self):
        real_copy_entries = INSTALLER.copy_entries

        def copy_with_folded_modes(source, stage, entries):
            real_copy_entries(source, stage, entries)
            stage.chmod(0o777)
            for child in stage.rglob("*"):
                child.chmod(0o777)

        output = io.StringIO()
        with mock.patch.object(
            INSTALLER,
            "copy_entries",
            side_effect=copy_with_folded_modes,
        ):
            with mock.patch.object(os, "fchmod", return_value=None):
                with mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": str(self.codex_home)},
                ):
                    with redirect_stdout(output):
                        return_code = INSTALLER.install(self.source, self.skills_root)
        return return_code, output.getvalue()

    def test_install_uses_target_observed_policy_when_probe_proves_folding(self):
        return_code, output = self.install_with_mode_folding()

        self.assertEqual(return_code, 0, output)
        manifest = self.manifest_payload()
        self.assertEqual(manifest["mode_policy"], "target-observed")
        self.assertEqual(
            manifest["mode_capability"]["status"],
            "posix_mode_not_preserved",
        )
        self.assertEqual(
            manifest["source_entries"],
            INSTALLER.scan_tree(self.source),
        )
        self.assertEqual(manifest["entries"], INSTALLER.scan_tree(self.target))
        self.assertNotEqual(
            manifest["source_root_mode"],
            manifest["target_root_mode"],
        )

        with mock.patch.dict(
            os.environ,
            {"CODEX_HOME": str(self.codex_home)},
        ):
            with redirect_stdout(io.StringIO()):
                verify_code = INSTALLER.verify(self.target, self.manifest)
        self.assertEqual(verify_code, 0)

        (self.target / "SKILL.md").chmod(0o600)
        with mock.patch.dict(
            os.environ,
            {"CODEX_HOME": str(self.codex_home)},
        ):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                with redirect_stdout(io.StringIO()):
                    INSTALLER.verify(self.target, self.manifest)
        self.assertEqual(raised.exception.reason, "target_drift")
        self.assertIn("SKILL.md", raised.exception.differences)

    def test_install_reports_unknown_when_mode_probe_is_unproven(self):
        real_copy_entries = INSTALLER.copy_entries

        def make_stage_mode_different(source, stage, entries):
            real_copy_entries(source, stage, entries)
            (stage / "SKILL.md").chmod(0o600)

        output = io.StringIO()
        arguments = [
            str(SCRIPT),
            "install",
            "--source",
            str(self.source),
            "--skills-root",
            str(self.skills_root),
        ]
        with mock.patch.object(
            INSTALLER,
            "copy_entries",
            side_effect=make_stage_mode_different,
        ):
            with mock.patch.object(
                INSTALLER,
                "probe_mode_capability",
                side_effect=INSTALLER.InstallError(
                    "mode_capability_unknown",
                    4,
                    status="unknown",
                ),
            ):
                with mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": str(self.codex_home)},
                ):
                    with mock.patch.object(sys, "argv", arguments):
                        with redirect_stdout(output):
                            return_code = INSTALLER.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(return_code, 4)
        self.assertEqual(payload["status"], "unknown")
        self.assertEqual(payload["reason"], "mode_capability_unknown")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())

    def test_target_observed_rollback_rejects_mode_drift_without_reprobing(self):
        return_code, output = self.install_with_mode_folding()
        self.assertEqual(return_code, 0, output)
        manifest = self.manifest_payload()
        self.assertEqual(manifest["mode_policy"], "target-observed")
        self.target.chmod(0o700)

        with mock.patch.object(INSTALLER, "probe_mode_capability") as probe:
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(self.codex_home)},
            ):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    with redirect_stdout(io.StringIO()):
                        INSTALLER.rollback(
                            self.target,
                            self.manifest,
                            manifest["manifest_digest"],
                        )

        self.assertEqual(raised.exception.reason, "target_drift")
        self.assertIn("<root-mode>", raised.exception.differences)
        probe.assert_not_called()
        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.state_dir.is_dir())

    def test_target_content_drift_after_move_does_not_reprobe(self):
        def move_then_corrupt(source, destination):
            os.rename(source, destination)
            (destination / "SKILL.md").write_text("corrupt\n", encoding="utf-8")

        real_probe = INSTALLER.probe_mode_capability
        with mock.patch.object(
            INSTALLER,
            "probe_mode_capability",
            wraps=real_probe,
        ) as probe:
            with mock.patch.object(
                INSTALLER,
                "rename_noreplace",
                side_effect=move_then_corrupt,
            ):
                with self.assertRaises(INSTALLER.InstallError) as raised:
                    self.install_with_mode_folding()

        self.assertEqual(raised.exception.reason, "installed_hash_mismatch")
        self.assertEqual(probe.call_count, 1)
        self.assertTrue(self.target.is_dir())
        self.assertTrue(self.prepared_manifest.is_file())
        self.assertFalse(self.manifest.exists())

    def test_fake_9p_label_does_not_bypass_unproven_probe(self):
        real_copy_entries = INSTALLER.copy_entries

        def make_stage_mode_different(source, stage, entries):
            real_copy_entries(source, stage, entries)
            (stage / "SKILL.md").chmod(0o600)

        fake_filesystem = {
            "device": 94,
            "mount_target": "/mnt/c",
            "filesystem_type": "9p",
            "mount_options_sha256": "0" * 64,
        }
        with mock.patch.object(
            INSTALLER,
            "copy_entries",
            side_effect=make_stage_mode_different,
        ):
            with mock.patch.object(
                INSTALLER,
                "filesystem_identity",
                return_value=fake_filesystem,
            ):
                with mock.patch.object(
                    INSTALLER,
                    "probe_mode_capability",
                    side_effect=INSTALLER.InstallError(
                        "mode_capability_unknown",
                        4,
                        status="unknown",
                    ),
                ) as probe:
                    with mock.patch.dict(
                        os.environ,
                        {"CODEX_HOME": str(self.codex_home)},
                    ):
                        with self.assertRaises(INSTALLER.InstallError) as raised:
                            INSTALLER.install(self.source, self.skills_root)

        self.assertEqual(raised.exception.reason, "mode_capability_unknown")
        self.assertEqual(raised.exception.status, "unknown")
        self.assertEqual(probe.call_count, 1)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())

    def test_stage_content_drift_stops_before_state_or_move(self):
        real_copy_entries = INSTALLER.copy_entries

        def copy_then_corrupt(source, stage, entries):
            real_copy_entries(source, stage, entries)
            (stage / "SKILL.md").write_text("corrupt\n", encoding="utf-8")

        with mock.patch.object(
            INSTALLER,
            "copy_entries",
            side_effect=copy_then_corrupt,
        ):
            with mock.patch.object(INSTALLER, "probe_mode_capability") as probe:
                with mock.patch.object(INSTALLER, "rename_noreplace") as move:
                    with mock.patch.dict(
                        os.environ,
                        {"CODEX_HOME": str(self.codex_home)},
                    ):
                        with self.assertRaises(INSTALLER.InstallError) as raised:
                            INSTALLER.install(self.source, self.skills_root)

        self.assertEqual(raised.exception.reason, "staging_hash_mismatch")
        self.assertEqual(raised.exception.exit_code, 3)
        probe.assert_not_called()
        move.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())


class WindowsNoReplaceRouteIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if WINDOWS_INTEROP_REQUIRED and not WINDOWS_INTEROP_ENABLED:
            raise AssertionError("required Windows noreplace interop is unavailable")
        if not WINDOWS_INTEROP_ENABLED:
            raise unittest.SkipTest("Windows noreplace interop is NOT_APPLICABLE")
        cls.approved_root = windows_drive_temp_root()
        if cls.approved_root is None:
            if WINDOWS_INTEROP_REQUIRED:
                raise AssertionError(
                    "required Windows noreplace interop root is unavailable"
                )
            raise unittest.SkipTest("Windows noreplace interop is NOT_APPLICABLE")
        real_home_text = os.environ.get("CODEX_HOME")
        if not real_home_text:
            if WINDOWS_INTEROP_REQUIRED:
                raise AssertionError("required run has no real CODEX_HOME binding")
            raise unittest.SkipTest("real CODEX_HOME binding is NOT_APPLICABLE")
        cls.real_codex_home = Path(os.path.abspath(real_home_text))
        if (
            cls.real_codex_home == cls.approved_root
            or cls.real_codex_home.is_relative_to(cls.approved_root)
            or cls.approved_root.is_relative_to(cls.real_codex_home)
        ):
            raise AssertionError("approved DrvFS root overlaps real CODEX_HOME")

    def setUp(self):
        self._source_temp = tempfile.TemporaryDirectory(
            prefix="vibe-windows-route-source-"
        )
        self.addCleanup(self._source_temp.cleanup)
        self._drvfs_temp = tempfile.TemporaryDirectory(
            prefix="vibe-windows-route-",
            dir=self.approved_root,
        )
        self.disposable_root = Path(self._drvfs_temp.name)
        self.addCleanup(self._cleanup_drvfs_leaf)
        self.old_source = self._make_source(
            Path(self._source_temp.name) / "old-repo",
            "1" * 40,
            "old",
        )
        self.new_source = self._make_source(
            Path(self._source_temp.name) / "new-repo",
            "2" * 40,
            "new",
        )

    def _cleanup_drvfs_leaf(self):
        leaf = self.disposable_root
        self._drvfs_temp.cleanup()
        self.assertFalse(leaf.exists())

    @staticmethod
    def _make_source(repository, head, label):
        git = repository / ".git"
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
        (git / "refs" / "heads" / "main").write_text(head + "\n", encoding="ascii")
        source = repository / SKILL_NAME
        shutil.copytree(ROOT / "skill" / SKILL_NAME, source)
        index = source / "references" / "SKILL_INDEX_ZH.md"
        index.write_text(
            index.read_text(encoding="utf-8") + f"\n<!-- {label} -->\n",
            encoding="utf-8",
        )
        return source

    def _new_case(self, label):
        root = self.disposable_root / label
        root.mkdir()
        codex_home = root / "codex-home"
        skills_root = codex_home / "skills"
        skills_root.mkdir(parents=True)
        self.assertNotEqual(self.real_codex_home, codex_home)
        self.assertFalse(self.real_codex_home.is_relative_to(codex_home))
        self.assertFalse(codex_home.is_relative_to(self.real_codex_home))
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(codex_home)
        return {
            "root": root,
            "codex_home": codex_home,
            "skills_root": skills_root,
            "target": skills_root / SKILL_NAME,
            "state": skills_root / f".{SKILL_NAME}-install",
            "manifest": skills_root
            / f".{SKILL_NAME}-install"
            / INSTALLER.MANIFEST_NAME,
            "environment": environment,
        }

    def _invoke(self, case, *arguments):
        result = subprocess.run(
            [sys.executable, SCRIPT, *map(str, arguments)],
            check=False,
            capture_output=True,
            text=True,
            env=case["environment"],
            timeout=120,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result, json.loads(result.stdout)

    def _install_old(self, case):
        result, payload = self._invoke(
            case,
            "install", "--source", self.old_source,
            "--skills-root", case["skills_root"],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(payload["status"], "installed")
        result, _ = self._invoke(
            case,
            "verify", "--target", case["target"],
            "--manifest", case["manifest"],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def _attest(self, case, label):
        output = case["root"] / f"{label}-evidence.json"
        result, payload = self._invoke(
            case,
            "attest-switch-backend",
            "--skills-root", case["skills_root"],
            "--backend", INSTALLER.WINDOWS_MOVEFILEEX_NOREPLACE,
            "--approval-id", f"WINDOWS-INTEGRATION-ATTEST-{label.upper()}",
            "--output", output,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(payload["status"], "switch_backend_attested")
        self.assertEqual(
            list(case["skills_root"].glob(f".{SKILL_NAME}-switch-probe-*")), []
        )
        return output, json.loads(output.read_text(encoding="utf-8"))

    def _prepare_upgrade(self, case, evidence, label):
        output = case["root"] / f"{label}-upgrade-request.json"
        result, payload = self._invoke(
            case,
            "prepare-upgrade",
            "--source", self.new_source,
            "--target", case["target"],
            "--manifest", case["manifest"],
            "--approval-id", f"WINDOWS-INTEGRATION-UPGRADE-{label.upper()}",
            "--switch-backend-evidence", evidence,
            "--output", output,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        request = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["request_digest"], request["request_digest"])
        self.assertEqual(request["upgrade_schema_version"], 2)
        return output, request

    def _complete_upgrade(self, case, label):
        self._install_old(case)
        evidence_path, evidence = self._attest(case, label + "-upgrade")
        request_path, request = self._prepare_upgrade(case, evidence_path, label)
        result, payload = self._invoke(
            case,
            "upgrade", "--request", request_path,
            "--confirm-request", request["request_digest"],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        receipt_path = Path(payload["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        return evidence_path, evidence, request_path, request, receipt_path, receipt

    @staticmethod
    def _journal_values(path):
        return [
            json.loads(child.read_text(encoding="utf-8"))
            for child in sorted(path.glob("*.json"))
        ]

    def test_cli_upgrade_inspect_and_fresh_evidence_restore_round_trip(self):
        case = self._new_case("round-trip")
        (
            first_evidence_path, first_evidence, request_path, _,
            upgrade_receipt_path, upgrade_receipt,
        ) = self._complete_upgrade(case, "round-trip")
        historical = {
            first_evidence_path: first_evidence_path.read_bytes(),
            request_path: request_path.read_bytes(),
            upgrade_receipt_path: upgrade_receipt_path.read_bytes(),
        }
        result, inspection = self._invoke(
            case, "inspect-upgrade", "--journal", Path(upgrade_receipt["journal"])
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(inspection["inspection"]["classification"], "VERIFIED")
        result, _ = self._invoke(
            case, "verify", "--target", case["target"],
            "--manifest", case["manifest"],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        second_path, second = self._attest(case, "round-trip-restore")
        self.assertNotEqual(
            first_evidence["evidence_digest"], second["evidence_digest"]
        )
        result, restore_payload = self._invoke(
            case,
            "restore-version", "--receipt", upgrade_receipt_path,
            "--confirm", upgrade_receipt["restore_confirmation_digest"],
            "--approval-id", "WINDOWS-INTEGRATION-RESTORE-ROUND-TRIP",
            "--switch-backend-evidence", second_path,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        restore_receipt = json.loads(
            Path(restore_payload["receipt"]).read_text(encoding="utf-8")
        )
        result, _ = self._invoke(
            case, "verify", "--target", case["target"],
            "--manifest", case["manifest"],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            INSTALLER.compare_content_entries(
                INSTALLER.scan_tree(self.old_source), INSTALLER.scan_tree(case["target"])
            ), []
        )
        archived_new = Path(restore_receipt["archived_replaced_version"]["target"])
        self.assertEqual(
            INSTALLER.compare_content_entries(
                INSTALLER.scan_tree(self.new_source), INSTALLER.scan_tree(archived_new)
            ), []
        )
        self.assertEqual(
            tuple(v["phase"] for v in self._journal_values(Path(upgrade_receipt["journal"]))),
            INSTALLER.NOREPLACE_UPGRADE_PHASES,
        )
        self.assertEqual(
            tuple(v["phase"] for v in self._journal_values(Path(restore_receipt["journal"]))),
            INSTALLER.NOREPLACE_RESTORE_PHASES,
        )
        for path, content in historical.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(
            list(case["skills_root"].glob(f".{SKILL_NAME}-switch-probe-*")), []
        )
        self.assertFalse(self.real_codex_home == case["codex_home"])
        self.assertFalse(self.real_codex_home.is_relative_to(case["codex_home"]))
        self.assertFalse(case["codex_home"].is_relative_to(self.real_codex_home))

    def test_real_adapter_upgrade_interruption_matrix(self):
        expected_calls = dict(zip(INSTALLER.NOREPLACE_UPGRADE_PHASES, (0, 0, 1, 2, 3, 4, 4)))
        for phase in INSTALLER.NOREPLACE_UPGRADE_PHASES:
            with self.subTest(phase=phase):
                case = self._new_case("upgrade-interrupt-" + phase.lower())
                self._install_old(case)
                evidence_path, _ = self._attest(case, "upgrade-" + phase.lower())
                request_path, request = self._prepare_upgrade(
                    case, evidence_path, "interrupt-" + phase.lower()
                )
                calls = []
                real_move = INSTALLER.move_directory_for_backend

                def counted_move(backend, source, destination):
                    calls.append((backend, Path(source), Path(destination)))
                    return real_move(backend, source, destination)

                def interrupt(observed_phase, selected=phase):
                    if observed_phase == selected:
                        raise INSTALLER.InstallError(
                            "injected_windows_upgrade_interruption", 4, status="unknown"
                        )

                with mock.patch.dict(os.environ, {"CODEX_HOME": str(case["codex_home"])}):
                    with mock.patch.object(
                        INSTALLER, "move_directory_for_backend", side_effect=counted_move
                    ):
                        with mock.patch.object(
                            INSTALLER, "rename_noreplace",
                            side_effect=AssertionError("route A used fallback"),
                        ):
                            with mock.patch.object(
                                INSTALLER, "renameat2_direct",
                                side_effect=AssertionError("route A used POSIX backend"),
                            ):
                                with self.assertRaises(INSTALLER.InstallError):
                                    INSTALLER.upgrade(
                                        request_path, request["request_digest"],
                                        phase_hook=interrupt,
                                    )
                self.assertEqual(len(calls), expected_calls[phase])
                recovery = (
                    case["codex_home"] / INSTALLER.RECOVERY_ROOT_NAME
                    / request["operation_id"]
                )
                inspection = INSTALLER.inspect_upgrade(recovery / "journal")
                self.assertIn(
                    inspection["classification"], {"UNKNOWN", "RECOVERY_REQUIRED"}
                )
                receipts = self._journal_values(recovery / "journal")
                paths = INSTALLER._upgrade_paths(request, case["codex_home"])
                self.assertEqual(
                    INSTALLER._upgrade_observed(paths),
                    receipts[-1]["observed_postconditions"],
                )
                self.assertEqual(
                    list(case["skills_root"].glob(f".{SKILL_NAME}-switch-probe-*")), []
                )

    def test_real_adapter_restore_interruption_matrix(self):
        expected_calls = dict(zip(INSTALLER.NOREPLACE_RESTORE_PHASES, (0, 0, 1, 2, 3, 4, 4)))
        for phase in INSTALLER.NOREPLACE_RESTORE_PHASES:
            with self.subTest(phase=phase):
                case = self._new_case("restore-interrupt-" + phase.lower())
                _, _, _, _, receipt_path, receipt = self._complete_upgrade(
                    case, "restore-" + phase.lower()
                )
                evidence_path, _ = self._attest(
                    case, "restore-fresh-" + phase.lower()
                )
                calls = []
                real_move = INSTALLER.move_directory_for_backend

                def counted_move(backend, source, destination):
                    calls.append((backend, Path(source), Path(destination)))
                    return real_move(backend, source, destination)

                def interrupt(observed_phase, selected=phase):
                    if observed_phase == selected:
                        raise INSTALLER.InstallError(
                            "injected_windows_restore_interruption", 4, status="unknown"
                        )

                with mock.patch.dict(os.environ, {"CODEX_HOME": str(case["codex_home"])}):
                    with mock.patch.object(
                        INSTALLER, "move_directory_for_backend", side_effect=counted_move
                    ):
                        with mock.patch.object(
                            INSTALLER, "rename_noreplace",
                            side_effect=AssertionError("route A used fallback"),
                        ):
                            with mock.patch.object(
                                INSTALLER, "renameat2_direct",
                                side_effect=AssertionError("route A used POSIX backend"),
                            ):
                                with self.assertRaises(INSTALLER.InstallError):
                                    INSTALLER.restore_version(
                                        receipt_path,
                                        receipt["restore_confirmation_digest"],
                                        "WINDOWS-INTEGRATION-RESTORE-INTERRUPT",
                                        switch_backend_evidence_path=evidence_path,
                                        phase_hook=interrupt,
                                    )
                self.assertEqual(len(calls), expected_calls[phase])
                restores = sorted(
                    path for path in (
                        case["codex_home"] / INSTALLER.RECOVERY_ROOT_NAME
                    ).iterdir() if path.name.startswith("restore-")
                )
                self.assertEqual(len(restores), 1)
                recovery = restores[0]
                receipts = self._journal_values(recovery / "journal")
                request = json.loads(
                    (recovery / "restore-request.json").read_text(encoding="utf-8")
                )
                paths = INSTALLER._restore_paths(request, case["codex_home"])
                self.assertEqual(
                    INSTALLER._restore_observed(paths),
                    receipts[-1]["observed_postconditions"],
                )
                self.assertFalse((recovery / "restore-success-receipt.json").exists())
                self.assertEqual(
                    list(case["skills_root"].glob(f".{SKILL_NAME}-switch-probe-*")), []
                )


class CrossFilesystemRootGuardTests(unittest.TestCase):
    """Exercise the real cross-filesystem setup using synthetic local roots only."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vibe-b62-guard-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.disposable = self.root / "disposable"
        self.protected = self.root / "protected"
        self.fixture_root = self.root / "fixture-work"
        for path in (self.disposable, self.protected, self.fixture_root):
            path.mkdir()
        self.marker = self.protected / "keep.txt"
        self.marker.write_bytes(b"protected synthetic fixture\n")
        self.environment = {
            "VIBE_INSTALL_DRVFS_TEST_ROOT": str(self.disposable),
            "VIBE_INSTALL_PROTECTED_CODEX_HOME": str(self.protected),
            "CODEX_HOME": str(self.protected),
        }
        self.created = []

    def make_case(self, overrides=None):
        case = CrossFilesystemInstallTests(
            "test_ext4_source_installs_to_disposable_drvfs_home"
        )
        self.addCleanup(case.doCleanups)
        real_temporary_directory = tempfile.TemporaryDirectory

        def create_fixture(*args, **kwargs):
            self.assertEqual(kwargs.get("dir"), "/tmp")
            kwargs["dir"] = self.fixture_root
            temporary = real_temporary_directory(*args, **kwargs)
            self.created.append(Path(temporary.name))
            return temporary

        with mock.patch.dict(os.environ, self.environment):
            for name, value in (overrides or {}).items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            with mock.patch.object(
                tempfile, "TemporaryDirectory", side_effect=create_fixture
            ):
                case.setUp()
        return case

    def assert_rejected_without_fixture(self, overrides):
        with self.assertRaisesRegex(AssertionError, "CROSS_FILESYSTEM_ROOT_UNSAFE"):
            self.make_case(overrides)
        self.assertEqual(self.created, [])
        self.assertEqual(list(self.fixture_root.iterdir()), [])
        self.assertEqual(self.marker.read_bytes(), b"protected synthetic fixture\n")

    def test_setup_rejects_missing_protected_home_before_fixture_creation(self):
        self.assert_rejected_without_fixture(
            {"VIBE_INSTALL_PROTECTED_CODEX_HOME": None}
        )

    def test_setup_requires_existing_canonical_directory_inputs(self):
        file_path = self.root / "ordinary-file"
        file_path.write_text("not a directory", encoding="utf-8")
        for variable, valid in (
            ("VIBE_INSTALL_DRVFS_TEST_ROOT", self.disposable),
            ("VIBE_INSTALL_PROTECTED_CODEX_HOME", self.protected),
        ):
            for bad in (
                None, "", " ", "relative-test-root",
                str(self.root / "missing"), str(file_path),
                str(valid) + "/", str(valid) + "/../" + valid.name,
            ):
                with self.subTest(variable=variable, input_kind=str(bad)):
                    overrides = {variable: bad}
                    if variable == "VIBE_INSTALL_PROTECTED_CODEX_HOME":
                        overrides["CODEX_HOME"] = bad
                    self.assert_rejected_without_fixture(overrides)

    def test_setup_requires_protected_home_to_match_effective_codex_home(self):
        other = self.root / "different-effective-home"
        other.mkdir()
        for value in (None, "", str(other)):
            with self.subTest(effective_home=value):
                self.assert_rejected_without_fixture({"CODEX_HOME": value})

    def test_setup_refuses_equal_ancestor_and_descendant_roots(self):
        child = self.protected / "nested-disposable"
        child.mkdir()
        for unsafe in (self.protected, self.root, child):
            with self.subTest(relationship=unsafe.name):
                self.assert_rejected_without_fixture(
                    {"VIBE_INSTALL_DRVFS_TEST_ROOT": str(unsafe)}
                )

    def test_setup_refuses_direct_and_parent_symlink_aliases(self):
        direct_disposable = self.root / "disposable-link"
        direct_protected = self.root / "protected-link"
        parent_alias = self.root / "parent-link"
        direct_disposable.symlink_to(self.disposable, target_is_directory=True)
        direct_protected.symlink_to(self.protected, target_is_directory=True)
        parent_alias.symlink_to(self.root, target_is_directory=True)
        for variable, alias in (
            ("VIBE_INSTALL_DRVFS_TEST_ROOT", direct_disposable),
            ("VIBE_INSTALL_PROTECTED_CODEX_HOME", direct_protected),
            ("VIBE_INSTALL_DRVFS_TEST_ROOT", parent_alias / "disposable"),
            ("VIBE_INSTALL_PROTECTED_CODEX_HOME", parent_alias / "protected"),
        ):
            with self.subTest(variable=variable, alias=alias.name):
                overrides = {variable: str(alias)}
                if variable == "VIBE_INSTALL_PROTECTED_CODEX_HOME":
                    overrides["CODEX_HOME"] = str(alias)
                self.assert_rejected_without_fixture(overrides)

    def test_disjoint_roots_allow_one_real_fixture_without_touching_protected_home(self):
        case = self.make_case()
        with mock.patch.dict(os.environ, self.environment):
            self.assertEqual(case.recheck_roots(), (self.disposable, self.protected))
        self.assertEqual(len(self.created), 1)
        self.assertTrue(self.created[0].is_relative_to(self.fixture_root))
        self.assertTrue((case.source / "SKILL.md").is_file())
        self.assertEqual(self.marker.read_bytes(), b"protected synthetic fixture\n")
        self.assertEqual(list(self.disposable.iterdir()), [])

    def test_recheck_rejects_environment_root_change_before_more_work(self):
        case = self.make_case()
        other = self.root / "other-disposable"
        other.mkdir()
        environment = {
            **self.environment,
            "VIBE_INSTALL_DRVFS_TEST_ROOT": str(other),
        }
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(AssertionError, "CROSS_FILESYSTEM_ROOT_CHANGED"):
                case.recheck_roots()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(list(other.iterdir()), [])
        self.assertEqual(self.marker.read_bytes(), b"protected synthetic fixture\n")

    def test_recheck_rejects_same_path_replacement(self):
        case = self.make_case()
        archived = self.root / "original-disposable"
        self.disposable.rename(archived)
        self.disposable.mkdir()
        with mock.patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(AssertionError, "CROSS_FILESYSTEM_ROOT_CHANGED"):
                case.recheck_roots()
        self.assertEqual(len(self.created), 1)
        self.assertTrue(archived.is_dir())
        self.assertEqual(list(self.disposable.iterdir()), [])
        self.assertEqual(self.marker.read_bytes(), b"protected synthetic fixture\n")


@unittest.skipUnless(
    os.environ.get("VIBE_INSTALL_DRVFS_TEST_ROOT"),
    "set VIBE_INSTALL_DRVFS_TEST_ROOT for disposable cross-filesystem test",
)
class CrossFilesystemInstallTests(unittest.TestCase):
    """Opt-in tests require an approved disposable root and explicit protection.

    VIBE_INSTALL_PROTECTED_CODEX_HOME must match the process CODEX_HOME;
    VIBE_INSTALL_DRVFS_TEST_ROOT must be a canonical, disjoint directory.
    """

    def setUp(self):
        self.root_binding = validated_cross_filesystem_test_roots()
        TargetObservedInstallTests.setUp(self)

    def recheck_roots(self):
        current = validated_cross_filesystem_test_roots()
        self.assertTrue(current == self.root_binding, "CROSS_FILESYSTEM_ROOT_CHANGED")
        return current["disposable"][0], current["protected"][0]

    def test_ext4_old_to_new_upgrade_preserves_archive(self):
        if not os.environ.get("VIBE_INSTALL_OLD_SOURCE"):
            self.skipTest("set VIBE_INSTALL_OLD_SOURCE to the reconstructed old package")
        if not os.environ.get("VIBE_INSTALL_NEW_SOURCE"):
            self.skipTest("set VIBE_INSTALL_NEW_SOURCE to the frozen new package")
        absolute_root, real_codex_home = self.recheck_roots()
        root_metadata = os.lstat(absolute_root)
        self.assertTrue(stat.S_ISDIR(root_metadata.st_mode))
        self.assertFalse(stat.S_ISLNK(root_metadata.st_mode))
        self.assertEqual(absolute_root, absolute_root.resolve(strict=True))
        old_source = Path(os.environ["VIBE_INSTALL_OLD_SOURCE"]).resolve(strict=True)
        new_source = Path(os.environ["VIBE_INSTALL_NEW_SOURCE"]).resolve(strict=True)
        self.assertTrue(old_source.is_dir())
        self.assertTrue(new_source.is_dir())
        self.assertNotEqual(old_source.stat().st_dev, root_metadata.st_dev)
        self.assertNotEqual(new_source.stat().st_dev, root_metadata.st_dev)
        self.assertEqual(
            INSTALLER.compare_content_entries(
                INSTALLER.scan_tree(old_source),
                INSTALLER.scan_tree(new_source),
            ),
            ["references/SKILL_INDEX_ZH.md"],
        )

        self.recheck_roots()
        with tempfile.TemporaryDirectory(
            prefix="vibe-upgrade-drvfs-",
            dir=absolute_root,
        ) as temporary:
            codex_home = Path(temporary) / "codex-home"
            skills_root = codex_home / "skills"
            skills_root.mkdir(parents=True)
            target = skills_root / SKILL_NAME
            state_dir = skills_root / f".{SKILL_NAME}-install"
            manifest_path = state_dir / "install-manifest.json"
            environment = os.environ.copy()
            environment["CODEX_HOME"] = str(codex_home)
            calls = []

            def invoke(*arguments):
                calls.append(tuple(map(str, arguments)))
                return subprocess.run(
                    [sys.executable, SCRIPT, *map(str, arguments)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )

            old_install = invoke(
                "install", "--source", old_source, "--skills-root", skills_root
            )
            self.assertEqual(old_install.returncode, 0, old_install.stderr)
            old_manifest_bytes = manifest_path.read_bytes()
            old_manifest = json.loads(old_manifest_bytes)
            old_verify = invoke(
                "verify", "--target", target, "--manifest", manifest_path
            )
            self.assertEqual(old_verify.returncode, 0, old_verify.stderr)
            rollback = invoke(
                "rollback",
                "--target",
                target,
                "--manifest",
                manifest_path,
                "--confirm",
                old_manifest["manifest_digest"],
            )
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            recovery_directory = Path(json.loads(rollback.stdout)["recovery_directory"])
            archived_target = recovery_directory / SKILL_NAME
            archived_manifest = recovery_directory / state_dir.name / manifest_path.name

            new_install = invoke(
                "install", "--source", new_source, "--skills-root", skills_root
            )
            self.assertEqual(new_install.returncode, 0, new_install.stderr)
            new_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            new_verify = invoke(
                "verify", "--target", target, "--manifest", manifest_path
            )
            self.assertEqual(new_verify.returncode, 0, new_verify.stderr)

            self.assertEqual(
                [call[0] for call in calls],
                ["install", "verify", "rollback", "install", "verify"],
            )
            self.assertIn(new_manifest["mode_policy"], {"strict", "target-observed"})
            self.assertEqual(
                INSTALLER.compare_content_entries(
                    old_manifest["source_entries"],
                    new_manifest["source_entries"],
                ),
                ["references/SKILL_INDEX_ZH.md"],
            )
            self.assertEqual(archived_manifest.read_bytes(), old_manifest_bytes)
            self.assertEqual(
                INSTALLER.compare_content_entries(
                    old_manifest["entries"],
                    INSTALLER.scan_tree(archived_target),
                ),
                [],
            )
            disposable_home = codex_home.resolve()
            self.assertNotEqual(real_codex_home, disposable_home)
            self.assertFalse(real_codex_home.is_relative_to(disposable_home))
            self.assertFalse(disposable_home.is_relative_to(real_codex_home))

    def test_ext4_source_installs_to_disposable_drvfs_home(self):
        absolute_root, real_codex_home = self.recheck_roots()
        metadata = os.lstat(absolute_root)
        self.assertTrue(stat.S_ISDIR(metadata.st_mode))
        self.assertFalse(stat.S_ISLNK(metadata.st_mode))
        self.assertEqual(absolute_root, absolute_root.resolve(strict=True))

        self.assertNotEqual(absolute_root, real_codex_home)
        self.assertFalse(absolute_root.is_relative_to(real_codex_home))
        self.assertFalse(real_codex_home.is_relative_to(absolute_root))
        self.assertNotEqual(self.source.stat().st_dev, metadata.st_dev)

        temporary_path = None
        self.recheck_roots()
        with tempfile.TemporaryDirectory(
            prefix="vibe-install-drvfs-",
            dir=absolute_root,
        ) as temporary:
            temporary_path = Path(temporary)
            codex_home = temporary_path / "codex-home"
            skills_root = codex_home / "skills"
            skills_root.mkdir(parents=True)
            target = skills_root / SKILL_NAME
            manifest_path = (
                skills_root / f".{SKILL_NAME}-install" / "install-manifest.json"
            )
            source_entries = INSTALLER.scan_tree(self.source)
            source_root_mode = INSTALLER.directory_identity(
                self.source,
                "unsafe_source_entry",
            )["mode"]

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                with redirect_stdout(io.StringIO()):
                    install_code = INSTALLER.install(self.source, skills_root)
            self.assertEqual(install_code, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(
                INSTALLER.compare_content_entries(
                    source_entries,
                    INSTALLER.scan_tree(target),
                ),
                [],
            )
            if manifest["mode_policy"] == "target-observed":
                self.assertEqual(
                    manifest["mode_capability"]["status"],
                    "posix_mode_not_preserved",
                )
            else:
                self.assertEqual(manifest["mode_policy"], "strict")
                self.assertEqual(manifest["mode_capability"], {"status": "not_required"})
                self.assertEqual(
                    manifest["target_root_mode"],
                    source_root_mode,
                )
                self.assertEqual(manifest["entries"], source_entries)
            print(f"cross_filesystem_policy={manifest['mode_policy']}")

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                with redirect_stdout(io.StringIO()):
                    verify_code = INSTALLER.verify(target, manifest_path)
            self.assertEqual(verify_code, 0)
            self.assertFalse(real_codex_home == codex_home)
            self.assertFalse(codex_home.is_relative_to(real_codex_home))
            self.assertFalse(real_codex_home.is_relative_to(codex_home))

        self.assertIsNotNone(temporary_path)
        self.assertFalse(temporary_path.exists())


if __name__ == "__main__":
    unittest.main()
