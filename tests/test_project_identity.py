# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import builtins
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from workbench import project_identity as IDENTITY
from workbench import project_freshness as FRESHNESS


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workbench" / "project_identity.py"
WSLPATH = Path("/usr/bin/wslpath")
WINDOWS_POWERSHELL = Path(
    "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"
)


def same_object_alias_fixture(identity):
    return {
        "input": "/fixture/project-alias",
        "kind": "WSL_POSIX_ABSOLUTE",
        "resolution_traits": [],
        "converted_path": None,
        "logical_path": "/fixture/project-alias",
        "physical_path": identity["physical_path"],
        "filesystem_identity": copy.deepcopy(identity["filesystem_identity"]),
        "relation_to_workspace": "SAME_OBJECT",
        "reason": None,
    }


def malformed_alias_cases(identity):
    valid = same_object_alias_fixture(identity)
    cases = [("null entry", [None]), ("empty entry", [{}]),
             ("unknown partial", [{"relation_to_workspace": "UNKNOWN"}]),
             ("same-object partial", [{"relation_to_workspace": "SAME_OBJECT"}])]
    for field in valid:
        alias = copy.deepcopy(valid)
        del alias[field]
        cases.append(("missing " + field, [alias]))
    for field, value in (
        ("relation_to_workspace", "UNKNOWN"), ("relation_to_workspace", []),
        ("reason", "PATH_CONVERSION_FAILED"), ("input", "relative"),
        ("kind", "WINDOWS_DRIVE_ABSOLUTE"), ("logical_path", None),
        ("physical_path", "relative"), ("converted_path", "/unexplained"),
        ("resolution_traits", [None]), ("resolution_traits", ["UNKNOWN"]),
        ("resolution_traits", ["CONVERTED"]),
        ("filesystem_identity", None), ("filesystem_identity", {}),
        ("filesystem_identity", dict(valid["filesystem_identity"], st_ino=True)),
        ("filesystem_identity", dict(valid["filesystem_identity"], st_dev=-1)),
        ("filesystem_identity", dict(valid["filesystem_identity"], object_type="file")),
        ("filesystem_identity", dict(valid["filesystem_identity"], st_ino=identity["filesystem_identity"]["st_ino"] + 1)),
    ):
        alias = copy.deepcopy(valid)
        alias[field] = value
        cases.append(("invalid " + field + repr(value), [alias]))
    cases.append(("valid then unknown", [copy.deepcopy(valid), {"relation_to_workspace": "UNKNOWN"}]))
    return cases


def windows_drive_temp_root() -> Path | None:
    if not WSLPATH.is_file() or not WINDOWS_POWERSHELL.is_file():
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


def malformed_runtime_surfaces():
    valid = {"platform": "linux", "is_wsl": True, "wsl_distro_name": "Ubuntu"}
    cases = [None, [], {}, {"unknown": None},
             {"surface": "wsl", "codex_version": "0.147.0", "core_sha256": "c" * 64}]
    for field in valid:
        missing = dict(valid)
        del missing[field]
        cases.append(missing)
    for field, values in (
        ("platform", (None, False, 1, [], {}, "", " ", "linux\n")),
        ("is_wsl", (None, "true", 0, 1, [], {})),
        ("wsl_distro_name", (False, 1, [], {}, "Ubuntu\x00")),
    ):
        cases.extend(dict(valid, **{field: value}) for value in values)
    cases.append(dict(valid, core_sha256="c" * 64))
    return cases


class PathSyntaxTests(unittest.TestCase):
    def classify(self, value):
        self.assertTrue(
            hasattr(IDENTITY, "classify_path_input"),
            "classify_path_input is not implemented",
        )
        return IDENTITY.classify_path_input(value)

    def resolve(self, requested_path, **kwargs):
        self.assertTrue(
            hasattr(IDENTITY, "resolve_path_alias"),
            "resolve_path_alias is not implemented",
        )
        return IDENTITY.resolve_path_alias(requested_path, **kwargs)

    def test_classify_accepts_wsl_posix_and_drive_forms(self):
        self.assertEqual(
            self.classify("/srv/fixture/projects/demo"),
            "WSL_POSIX_ABSOLUTE",
        )
        self.assertEqual(
            self.classify("/mnt/c/fixture/projects/demo"),
            "WSL_POSIX_ABSOLUTE",
        )
        self.assertEqual(
            self.classify(r"C:\fixture\projects\demo"),
            "WINDOWS_DRIVE_ABSOLUTE",
        )
        self.assertEqual(
            self.classify("d:/projects/demo"),
            "WINDOWS_DRIVE_ABSOLUTE",
        )

    def test_classify_accepts_both_wsl_unc_forms(self):
        self.assertEqual(
            self.classify(r"\\wsl.localhost\Ubuntu\srv\fixture\projects\demo"),
            "WSL_UNC_LOCALHOST",
        )
        self.assertEqual(
            self.classify(r"\\wsl$\Ubuntu\srv\fixture\projects\demo"),
            "WSL_UNC_DOLLAR",
        )

    def test_classify_rejects_relative_network_and_device_paths(self):
        rejected = (
            "relative/path",
            r"C:relative\path",
            r"\\server\share\demo",
            r"\\?\C:\device\path",
            r"\\.\PhysicalDrive0",
            "/tmp/line\nbreak",
            "/tmp/nul\0byte",
            "/tmp/invalid-\udcff",
        )

        for value in rejected:
            with self.subTest(value=repr(value)):
                self.assertEqual(self.classify(value), "UNSUPPORTED_OR_RELATIVE")

    def test_parser_preserves_requested_bytes_and_does_not_prelabel_symlink(self):
        with tempfile.TemporaryDirectory() as tempdir:
            requested = str(Path(tempdir) / "plain")
            Path(requested).mkdir()

            record = self.resolve(
                requested,
                runtime_surface={"platform": "linux", "is_wsl": True},
                workspace_pwd=requested,
            )

        self.assertEqual(record["input"], requested)
        self.assertEqual(record["kind"], "WSL_POSIX_ABSOLUTE")
        self.assertEqual(record["resolution_traits"], [])
        self.assertEqual(record["relation_to_workspace"], "SAME_OBJECT")

    def test_wsl_converter_is_argv_safe(self):
        calls = []
        with tempfile.TemporaryDirectory() as tempdir:
            requested = r"C:\tmp\$(touch should-not-run);demo"

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=f"{tempdir}\n".encode(),
                    stderr=b"",
                )

            self.assertTrue(
                hasattr(IDENTITY, "make_wslpath_converter"),
                "make_wslpath_converter is not implemented",
            )
            converter = IDENTITY.make_wslpath_converter("wslpath", runner=runner)
            record = self.resolve(
                requested,
                runtime_surface={"platform": "linux", "is_wsl": True},
                workspace_pwd=tempdir,
                converter=converter,
            )

        self.assertEqual(
            calls[0][0],
            ["wslpath", "-u", "--", requested],
        )
        self.assertFalse(calls[0][1]["shell"])
        self.assertEqual(record["converted_path"], tempdir)
        self.assertEqual(record["relation_to_workspace"], "SAME_OBJECT")
        self.assertEqual(record["resolution_traits"], ["CONVERTED"])

    def test_cross_surface_identity_without_bridge_is_unknown(self):
        requested = r"C:\fixture\projects\demo"
        record = self.resolve(
            requested,
            runtime_surface={"platform": "windows", "is_wsl": False},
            workspace_pwd="/srv/fixture/projects/demo",
            native_resolver=lambda value: value,
        )

        self.assertEqual(record["input"], requested)
        self.assertEqual(record["relation_to_workspace"], "UNKNOWN")
        self.assertEqual(record["reason"], "CROSS_SURFACE_IDENTITY_UNPROVEN")

    def test_windows_native_same_surface_resolves_workspace_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            requested = r"C:\workspace\project"
            paths = {requested: tempdir}

            record = self.resolve(
                requested,
                runtime_surface={"platform": "windows", "is_wsl": False},
                workspace_pwd=requested,
                native_resolver=lambda value: paths[value],
            )

        self.assertEqual(record["relation_to_workspace"], "SAME_OBJECT")
        self.assertEqual(record["physical_path"], str(Path(tempdir).resolve()))
        self.assertEqual(record["filesystem_identity"]["object_type"], "directory")
        self.assertIsNone(record["reason"])


class ProjectIdentityTests(unittest.TestCase):
    def test_head_query_failure_cannot_become_unborn(self):
        repo = self.make_repo()
        real_git = IDENTITY.git
        for code, stdout in ((1, b""), (128, b""), (1, b"a" * 40 + b"\n")):
            with self.subTest(code=code, stdout=stdout):
                def failing_head(path, *args, **kwargs):
                    if args[0] == "rev-parse" and "--verify" in args and args[-1] == "HEAD":
                        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=b"")
                    return real_git(path, *args, **kwargs)

                with mock.patch.object(IDENTITY, "git", side_effect=failing_head):
                    result = IDENTITY.capture_stable_identity(
                        str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
                    )
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["write_eligibility"], "BLOCKED")
                self.assertIsNone(result["identity"])

    def test_symbolic_ref_errors_cannot_become_detached(self):
        repo = self.make_repo()
        real_git = IDENTITY.git
        for code, stdout in ((128, b""), (1, b"main\n"), (0, b""), (128, b"main\n")):
            with self.subTest(code=code, stdout=stdout):
                def failing_symbolic(path, *args, **kwargs):
                    if args[0] == "symbolic-ref":
                        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=b"")
                    return real_git(path, *args, **kwargs)

                with mock.patch.object(IDENTITY, "git", side_effect=failing_symbolic):
                    result = IDENTITY.capture_stable_identity(
                        str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
                    )
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertIsNone(result["identity"])

    def test_unborn_requires_proven_missing_reference(self):
        repo = self.make_repo(commit=False)
        real_git = IDENTITY.git
        for code in (0, 1, 129):
            with self.subTest(code=code):
                def failed_lookup(path, *args, **kwargs):
                    if args[0] == "show-ref":
                        return subprocess.CompletedProcess(args, code, stdout=b"", stderr=b"")
                    return real_git(path, *args, **kwargs)

                with mock.patch.object(IDENTITY, "git", side_effect=failed_lookup):
                    result = IDENTITY.capture_stable_identity(
                        str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
                    )
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertIsNone(result["identity"])

    def test_stable_unborn_detached_and_packed_branch_remain_supported(self):
        for kind in ("unborn", "detached", "packed"):
            with self.subTest(kind=kind):
                repo = self.make_repo(kind, commit=kind != "unborn")
                if kind == "detached":
                    self.git(repo, "checkout", "--detach")
                elif kind == "packed":
                    self.git(repo, "pack-refs", "--all", "--prune")
                result = IDENTITY.capture_stable_identity(
                    str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
                )
                self.assertEqual(result["status"], "STABLE")
                self.assertEqual(result["identity"]["git"]["is_unborn"], kind == "unborn")
                self.assertEqual(result["identity"]["git"]["is_detached"], kind == "detached")

    def test_stable_cli_produces_direct_comparable_identity(self):
        repo = self.make_repo()
        result = self.run_identity(repo, "--stable", "--workspace-pwd", str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        identity = self.schema_two_payload(result)
        self.assertEqual(identity["write_eligibility"], "ELIGIBLE")
        self.assertEqual(FRESHNESS.compare_identity(identity, identity)["state"], "FRESH")

    def test_stable_cli_without_workspace_binding_cannot_emit_eligible_identity(self):
        repo = self.make_repo()
        result = self.run_identity(repo, "--stable")
        self.assertEqual(result.returncode, 3, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["status"], "UNKNOWN")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")
        self.assertIsNone(payload["identity"])

    def test_stable_capture_rejects_identical_malformed_runtime_surfaces(self):
        repo = self.make_repo()
        capture = IDENTITY.collect_identity(
            str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
        )
        for surface in malformed_runtime_surfaces():
            with self.subTest(surface=surface):
                invalid = copy.deepcopy(capture)
                invalid["runtime_surface"] = surface
                before = copy.deepcopy(invalid)
                with mock.patch.object(IDENTITY, "collect_identity", return_value=invalid):
                    result = IDENTITY.capture_stable_identity(
                        str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                        workspace_pwd=str(repo),
                    )
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["write_eligibility"], "BLOCKED")
                self.assertIsNone(result["identity"])
                self.assertEqual(invalid, before)

    def test_stable_capture_rejects_identical_invalid_alias_records(self):
        repo = self.make_repo()
        capture = IDENTITY.collect_identity(
            str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
        )
        for label, aliases in malformed_alias_cases(capture):
            with self.subTest(label=label):
                invalid = copy.deepcopy(capture)
                invalid["aliases"] = aliases
                before = copy.deepcopy(invalid)
                # Inject malformed capture data at the I/O boundary; the stable
                # capture and shared validation themselves remain real.
                with mock.patch.object(IDENTITY, "collect_identity", return_value=invalid):
                    result = IDENTITY.capture_stable_identity(
                        str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                        workspace_pwd=str(repo),
                    )
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["write_eligibility"], "BLOCKED")
                self.assertIsNone(result["identity"])
                self.assertEqual(invalid, before)

    def test_real_same_object_aliases_remain_valid_stable_candidates(self):
        repo = self.make_repo()
        alias_path = self.tempdir / "repo-alias"
        alias_path.symlink_to(repo, target_is_directory=True)
        result = IDENTITY.capture_stable_identity(
            str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo), aliases=(str(repo), str(alias_path)),
        )
        self.assertEqual(result["status"], "STABLE")
        identity = result["identity"]
        converted = IDENTITY.resolve_path_alias(
            r"C:\fixture\project", runtime_surface={"platform": "linux", "is_wsl": True},
            workspace_pwd=str(repo), converter=lambda path: str(repo),
        )
        self.assertEqual(converted["relation_to_workspace"], "SAME_OBJECT")
        self.assertEqual(converted["resolution_traits"], ["CONVERTED"])
        identity["aliases"].append(converted)
        before = copy.deepcopy(identity)
        IDENTITY.validate_git_worktree_facts(identity)
        self.assertEqual(FRESHNESS.compare_identity(identity, before)["state"], "FRESH")
        self.assertEqual(identity, before)

    def test_stable_capture_rejects_identical_malformed_facts(self):
        repo = self.make_repo()
        capture = IDENTITY.collect_identity(
            str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
        )
        self.assertEqual(capture["status"], "bound")
        for changes in (
            {"head": None}, {"dirty_fingerprint": None},
            {"filesystem_identity": None}, {"git": None},
        ):
            with self.subTest(changes=changes):
                invalid = copy.deepcopy(capture)
                invalid.update(changes)
                with mock.patch.object(IDENTITY, "collect_identity", return_value=invalid):
                    result = IDENTITY.capture_stable_identity(
                        str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                        workspace_pwd=str(repo),
                    )
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["write_eligibility"], "BLOCKED")
                self.assertIsNone(result["identity"])

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.tempdir = Path(self._tempdir.name)

    def run_command(self, *args, cwd=None, check=True):
        return subprocess.run(
            [*map(str, args)],
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
        )

    def git(self, repo, *args, check=True):
        return self.run_command("git", "-C", repo, *args, check=check)

    def make_repo(self, name="repo", commit=True, *, root=None):
        repo = (root or self.tempdir) / name
        repo.mkdir()
        self.run_command("git", "init", "-b", "main", repo)
        self.git(repo, "config", "user.name", "Test User")
        self.git(repo, "config", "user.email", "test@example.invalid")
        if commit:
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            self.git(repo, "add", "tracked.txt")
            self.git(repo, "commit", "-m", "initial")
        return repo

    def make_windows_drive_tempdir(self) -> Path:
        root = windows_drive_temp_root()
        if root is None:
            self.skipTest("Windows drive-backed temporary root is NOT_APPLICABLE")
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="vibe-project-identity-drvfs-",
                dir=root,
            )
        except OSError:
            self.skipTest("Windows drive-backed temporary root is NOT_APPLICABLE")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def run_identity(self, cwd, *extra):
        return self.run_command(
            sys.executable,
            SCRIPT,
            "--cwd",
            cwd,
            *extra,
            check=False,
        )

    def payload(self, result):
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def schema_two_payload(self, result):
        payload = self.payload(result)
        self.assertEqual(
            payload.get("schema_version"),
            2,
            "project identity schema 2 is not implemented",
        )
        return payload

    def test_collect_identity_windows_native_same_surface_uses_filesystem_identity(self):
        repo = self.make_repo()
        windows_path = r"C:\workspace\repo"
        runtime_surface = {
            "platform": "windows",
            "is_wsl": False,
            "wsl_distro_name": None,
        }
        native_resolver = lambda value: str(repo) if value == windows_path else value

        with (
            mock.patch.object(
                IDENTITY,
                "detect_runtime_surface",
                return_value=runtime_surface,
            ),
            mock.patch.object(
                IDENTITY,
                "_default_path_adapters",
                return_value=(None, native_resolver),
            ),
        ):
            payload = IDENTITY.collect_identity(
                windows_path,
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=windows_path,
            )

        self.assertEqual(payload["status"], "bound")
        self.assertEqual(payload["binding_kind"], "GIT_WORKTREE")
        self.assertEqual(
            payload["filesystem_identity"],
            IDENTITY._filesystem_identity(str(repo)),
        )
        self.assertTrue(payload["fingerprint_complete"])
        self.assertEqual(payload["write_eligibility"], "READ_ONLY")

    def test_collect_identity_cross_surface_without_bridge_is_unknown_and_blocked(self):
        repo = self.make_repo()
        windows_path = r"C:\workspace\repo"
        runtime_surface = {
            "platform": "windows",
            "is_wsl": False,
            "wsl_distro_name": None,
        }

        with (
            mock.patch.object(
                IDENTITY,
                "detect_runtime_surface",
                return_value=runtime_surface,
            ),
            mock.patch.object(
                IDENTITY,
                "_default_path_adapters",
                return_value=(None, lambda _value: str(repo)),
            ),
        ):
            payload = IDENTITY.collect_identity(
                windows_path,
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["reason"], "CROSS_SURFACE_IDENTITY_UNPROVEN")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_collect_identity_without_descriptor_support_is_blocked(self):
        repo = self.make_repo()

        with mock.patch.object(IDENTITY.os, "O_NOFOLLOW", None):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_directory_anchor_unavailable")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_collect_identity_without_dir_fd_support_is_blocked(self):
        repo = self.make_repo()

        with mock.patch.object(IDENTITY.os, "supports_dir_fd", set()):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_directory_anchor_unavailable")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_reports_git_identity_and_remote_names_only(self):
        repo = self.make_repo()
        self.git(
            repo,
            "remote",
            "add",
            "origin",
            "https://user:secret@example.test/team/app.git?token=x#frag",
        )

        result = self.run_identity(repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["status"], "bound")
        self.assertEqual(payload["git_top_level"], str(repo.resolve()))
        self.assertEqual(payload["branch"], "main")
        self.assertEqual(len(payload["head"]), 40)
        self.assertEqual(payload["remotes"], {"origin": None})
        self.assertNotIn("example.test", result.stdout)
        self.assertNotIn("secret", result.stdout)
        self.assertNotIn("token=x", result.stdout)

    def test_scp_style_remote_location_is_not_returned(self):
        repo = self.make_repo()
        self.git(repo, "remote", "add", "origin", "git@example.test:team/app.git")

        result = self.run_identity(repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.payload(result)["remotes"], {"origin": None})
        self.assertNotIn("example.test", result.stdout)

    def test_remote_collection_never_queries_urls(self):
        repo = self.make_repo()
        self.git(repo, "remote", "add", "origin", "https://example.test/private.git")
        original_git = IDENTITY.git
        commands = []

        def names_only(path, *args, **kwargs):
            commands.append(args)
            self.assertEqual(args, ("remote",), "remote URL query is forbidden")
            return original_git(path, *args, **kwargs)

        with mock.patch.object(IDENTITY, "git", side_effect=names_only):
            remotes = IDENTITY.collect_remotes(str(repo))

        self.assertEqual(remotes, {"origin": None})
        self.assertEqual(commands, [("remote",)])

    def test_non_git_directory_is_explicit(self):
        result = self.run_identity(self.tempdir)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["status"], "bound")
        self.assertFalse(payload["is_git"])
        self.assertIsNone(payload["git_top_level"])
        self.assertFalse(payload["dirty"])

    def test_expected_realpath_mismatch_fails_closed(self):
        repo = self.make_repo()

        result = self.run_identity(repo, "--expect-realpath", self.tempdir / "wrong")

        self.assertEqual(result.returncode, 2, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["reason"], "expected_realpath_mismatch")

    def test_missing_directory_fails_closed(self):
        result = self.run_identity(self.tempdir / "missing")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.payload(result)["reason"], "cwd_not_directory")

    def test_untracked_content_changes_fingerprint(self):
        repo = self.make_repo()
        path = repo / "new.txt"
        path.write_text("one", encoding="utf-8")
        first = self.payload(self.run_identity(repo))["dirty_fingerprint"]
        path.write_text("two", encoding="utf-8")
        second = self.payload(self.run_identity(repo))["dirty_fingerprint"]

        self.assertNotEqual(first, second)

    def test_tracked_content_changes_fingerprint(self):
        repo = self.make_repo()
        path = repo / "tracked.txt"
        path.write_text("first change\n", encoding="utf-8")
        first = self.payload(self.run_identity(repo))["dirty_fingerprint"]
        path.write_text("second change\n", encoding="utf-8")
        second = self.payload(self.run_identity(repo))["dirty_fingerprint"]

        self.assertNotEqual(first, second)

    def test_head_change_changes_fingerprint_without_file_changes(self):
        repo = self.make_repo()
        first = self.payload(self.run_identity(repo))["dirty_fingerprint"]
        self.git(repo, "commit", "--allow-empty", "-m", "new head")
        second = self.payload(self.run_identity(repo))["dirty_fingerprint"]

        self.assertNotEqual(first, second)

    def test_staging_same_content_changes_fingerprint(self):
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        unstaged = self.payload(self.run_identity(repo))["dirty_fingerprint"]
        self.git(repo, "add", "tracked.txt")
        staged = self.payload(self.run_identity(repo))["dirty_fingerprint"]

        self.assertNotEqual(unstaged, staged)

    def test_untracked_symlink_makes_fingerprint_incomplete(self):
        repo = self.make_repo()
        (repo / "target.txt").write_text("target\n", encoding="utf-8")
        (repo / "linked.txt").symlink_to("target.txt")

        result = self.run_identity(repo)
        payload = self.payload(result)

        self.assertEqual(result.returncode, 3)
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_file_type_unsupported")

    def test_untracked_hash_limit_is_incomplete(self):
        repo = self.make_repo()
        (repo / "large.bin").write_bytes(b"abcd")

        result = self.run_identity(repo, "--max-untracked-bytes", "2")

        self.assertEqual(result.returncode, 3, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_hash_limit_exceeded")

    def test_fingerprint_schema_is_explicit(self):
        repo = self.make_repo()

        payload = self.schema_two_payload(self.run_identity(repo))

        self.assertEqual(payload["dirty_fingerprint_schema"], 3)
        self.assertEqual(payload["fingerprint_applicability"], "REQUIRED")
        self.assertEqual(len(payload["dirty_fingerprint"]), 64)

    def test_head_tracked_staged_untracked_and_unmerged_changes_each_change_digest(self):
        clean_repo = self.make_repo("clean")
        clean = self.payload(self.run_identity(clean_repo))["dirty_fingerprint"]

        head_repo = self.make_repo("head")
        self.git(head_repo, "commit", "--allow-empty", "-m", "new head")
        tracked_repo = self.make_repo("tracked")
        (tracked_repo / "tracked.txt").write_text("tracked change\n", encoding="utf-8")
        staged_repo = self.make_repo("staged")
        (staged_repo / "tracked.txt").write_text("staged change\n", encoding="utf-8")
        self.git(staged_repo, "add", "tracked.txt")
        untracked_repo = self.make_repo("untracked")
        (untracked_repo / "new.txt").write_text("new\n", encoding="utf-8")
        unmerged_repo = self.make_repo("unmerged")
        self.git(unmerged_repo, "checkout", "-b", "other")
        (unmerged_repo / "tracked.txt").write_text("other\n", encoding="utf-8")
        self.git(unmerged_repo, "commit", "-am", "other")
        self.git(unmerged_repo, "checkout", "main")
        (unmerged_repo / "tracked.txt").write_text("main\n", encoding="utf-8")
        self.git(unmerged_repo, "commit", "-am", "main")
        merge = self.git(unmerged_repo, "merge", "other", check=False)
        self.assertNotEqual(merge.returncode, 0)

        changed = {
            "head": self.payload(self.run_identity(head_repo))["dirty_fingerprint"],
            "tracked": self.payload(self.run_identity(tracked_repo))["dirty_fingerprint"],
            "staged": self.payload(self.run_identity(staged_repo))["dirty_fingerprint"],
            "untracked": self.payload(self.run_identity(untracked_repo))["dirty_fingerprint"],
            "unmerged": self.payload(self.run_identity(unmerged_repo))["dirty_fingerprint"],
        }
        for name, fingerprint in changed.items():
            with self.subTest(name=name):
                self.assertNotEqual(fingerprint, clean)

    def test_untracked_special_file_is_incomplete(self):
        repo = self.make_repo()
        (repo / "target.txt").write_text("target\n", encoding="utf-8")
        (repo / "special-link").symlink_to("target.txt")

        result = self.run_identity(repo)
        payload = self.payload(result)

        self.assertEqual(result.returncode, 3)
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_file_type_unsupported")

    def test_read_replacement_is_incomplete(self):
        repo = self.make_repo()
        (repo / "new.txt").write_text("content\n", encoding="utf-8")
        real_fstat = IDENTITY.os.fstat
        observations = 0
        target_inode = (repo / "new.txt").stat().st_ino

        def changing_fstat(descriptor):
            nonlocal observations
            metadata = real_fstat(descriptor)
            if not IDENTITY.stat.S_ISREG(metadata.st_mode) or metadata.st_ino != target_inode:
                return metadata
            observations += 1
            if observations < 2:
                return metadata
            values = list(metadata)
            values[1] += 1
            return IDENTITY.os.stat_result(values)

        with mock.patch.object(IDENTITY.os, "fstat", side_effect=changing_fstat):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_file_replaced_during_binding")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_nested_parent_replacement_is_incomplete(self):
        repo = self.make_repo()
        nested = repo / "nested"
        nested.mkdir()
        (nested / "new.txt").write_text("original\n", encoding="utf-8")
        outside = self.tempdir / "outside"
        outside.mkdir()
        replaced = {"done": False}
        real_open = IDENTITY.os.open

        def replacing_open(path, flags, *args, **kwargs):
            if (
                not replaced["done"]
                and isinstance(path, (str, bytes))
                and os.fsdecode(path) == "nested"
                and kwargs.get("dir_fd") is not None
            ):
                replaced["done"] = True
                moved_nested = repo / "nested-old"
                nested.rename(moved_nested)
                (moved_nested / "new.txt").rename(outside / "new.txt")
                (repo / "nested").symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(IDENTITY.os, "open", side_effect=replacing_open):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertTrue(replaced["done"])
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_parent_replaced_during_binding")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_nested_parent_replacement_with_ordinary_directory_is_incomplete(self):
        repo = self.make_repo()
        original_root_identity = IDENTITY._filesystem_identity(str(repo))
        nested = repo / "nested"
        nested.mkdir()
        (nested / "new.txt").write_text("original\n", encoding="utf-8")
        replacement = self.tempdir / "replacement"
        replacement.mkdir()
        (replacement / "new.txt").write_text("outside\n", encoding="utf-8")
        moved_nested = repo / "nested-old"
        replaced = {"done": False}
        read_chunks = []
        real_git = IDENTITY.git
        real_read = IDENTITY.os.read

        def replacing_git(repo_path, *args, **kwargs):
            result = real_git(repo_path, *args, **kwargs)
            if not replaced["done"] and args[:2] == ("ls-files", "--stage"):
                replaced["done"] = True
                nested.rename(moved_nested)
                replacement.rename(repo / "nested")
            return result

        def recording_read(descriptor, size):
            chunk = real_read(descriptor, size)
            read_chunks.append(chunk)
            return chunk

        with (
            mock.patch.object(IDENTITY, "git", side_effect=replacing_git),
            mock.patch.object(IDENTITY.os, "read", side_effect=recording_read),
        ):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertTrue(replaced["done"])
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["write_eligibility"], "BLOCKED")
        self.assertEqual(payload["reason"], "untracked_paths_changed_during_binding")
        self.assertEqual(
            IDENTITY._filesystem_identity(str(repo)),
            original_root_identity,
        )
        self.assertNotIn(b"outside\n", b"".join(read_chunks))

    def test_unrelated_sibling_namespace_change_does_not_replace_project_root(self):
        repo = self.make_repo()
        original_root_identity = IDENTITY._filesystem_identity(str(repo))
        sibling = self.tempdir / "unrelated-sibling"
        sibling.mkdir()
        moved_sibling = self.tempdir / "unrelated-sibling-moved"
        changed = {"done": False}
        real_git = IDENTITY.git

        def moving_sibling_git(repo_path, *args, **kwargs):
            result = real_git(repo_path, *args, **kwargs)
            if not changed["done"] and args[:2] == ("ls-files", "--stage"):
                changed["done"] = True
                sibling.rename(moved_sibling)
            return result

        with mock.patch.object(IDENTITY, "git", side_effect=moving_sibling_git):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertTrue(changed["done"])
        self.assertEqual(payload["status"], "bound")
        self.assertTrue(payload["fingerprint_complete"])
        self.assertIsNone(payload["reason"])
        self.assertEqual(payload["write_eligibility"], "READ_ONLY")
        self.assertEqual(
            IDENTITY._filesystem_identity(str(repo)),
            original_root_identity,
        )

    def test_project_root_replacement_is_incomplete(self):
        repo = self.make_repo()
        (repo / "new.txt").write_text("original\n", encoding="utf-8")
        replacement = self.tempdir / "replacement"
        replacement.mkdir()
        (replacement / "new.txt").write_text("outside\n", encoding="utf-8")
        moved_repo = self.tempdir / "repo-old"
        replaced = {"done": False}
        real_git = IDENTITY.git

        def replacing_git(repo_path, *args, **kwargs):
            result = real_git(repo_path, *args, **kwargs)
            if not replaced["done"] and args[:1] == ("ls-tree",):
                replaced["done"] = True
                repo.rename(moved_repo)
                repo.symlink_to(replacement, target_is_directory=True)
            return result

        with mock.patch.object(IDENTITY, "git", side_effect=replacing_git):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertTrue(replaced["done"])
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "project_root_replaced_during_git_command")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_initial_root_replaced_before_dirty_descriptor_is_incomplete(self):
        repo = self.make_repo()
        replacement = self.make_repo("replacement")
        marker = b"replacement-repository-content\n"
        (replacement / "replacement-only.txt").write_bytes(marker)
        moved_repo = self.tempdir / "repo-old"
        replaced = {"done": False}
        read_chunks = []
        real_open_root = IDENTITY._open_project_root
        real_read = IDENTITY.os.read

        def replacing_open_root(repo_path, *args, **kwargs):
            if not replaced["done"]:
                replaced["done"] = True
                repo.rename(moved_repo)
                replacement.rename(repo)
            return real_open_root(repo_path, *args, **kwargs)

        def recording_read(descriptor, size):
            chunk = real_read(descriptor, size)
            read_chunks.append(chunk)
            return chunk

        with (
            mock.patch.object(
                IDENTITY,
                "_open_project_root",
                side_effect=replacing_open_root,
            ),
            mock.patch.object(IDENTITY.os, "read", side_effect=recording_read),
        ):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        self.assertTrue(replaced["done"])
        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "project_root_replaced_during_binding")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")
        self.assertEqual(
            payload["filesystem_identity"],
            IDENTITY._filesystem_identity(str(moved_repo)),
        )
        self.assertNotIn(marker, b"".join(read_chunks))

    def _capture_temporary_git_root_replacement(
        self,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the identity payload and recorded internal observations."""
        drvfs_root = self.make_windows_drive_tempdir()
        repo = self.make_repo(root=drvfs_root)
        replacement = self.make_repo("replacement", root=drvfs_root)
        marker_name = b"replacement-marker-only.txt"
        marker_content = b"replacement-marker-only-content\n"
        (replacement / os.fsdecode(marker_name)).write_bytes(marker_content)
        displaced = drvfs_root / "repo-displaced"
        replaced = {"done": False}
        git_commands = []
        command_outputs = []
        status_paths = []
        read_chunks = []
        fingerprint_fields = []
        real_run = IDENTITY.subprocess.run
        real_read = IDENTITY.os.read
        real_update_digest_field = IDENTITY._update_digest_field

        untracked_reads = 0

        def replacing_run(argv, *args, **kwargs):
            nonlocal untracked_reads
            if "ls-files" in argv and "--others" in argv:
                untracked_reads += 1
            if argv[:1] == ["git"]:
                git_commands.append(list(argv))
            if (
                not replaced["done"]
                and argv[:1] == ["git"]
                and "-C" in argv
                and "ls-files" in argv and "--others" in argv and untracked_reads == 2
            ):
                replaced["done"] = True
                repo.rename(displaced)
                replacement.rename(repo)
                try:
                    result = real_run(argv, *args, **kwargs)
                finally:
                    repo.rename(replacement)
                    displaced.rename(repo)
            else:
                result = real_run(argv, *args, **kwargs)
            if argv[:1] == ["git"]:
                command_outputs.append(result.stdout + result.stderr)
                if "ls-files" in argv and "--others" in argv:
                    status_paths.extend([path for path in result.stdout.split(b"\0") if path])
            return result

        def recording_read(descriptor, size):
            chunk = real_read(descriptor, size)
            read_chunks.append(chunk)
            return chunk

        def recording_update_digest_field(digest, label, value):
            fingerprint_fields.append(label + b"\0" + value)
            return real_update_digest_field(digest, label, value)

        original_identity = IDENTITY._filesystem_identity(str(repo))
        with (
            mock.patch.object(IDENTITY.subprocess, "run", side_effect=replacing_run),
            mock.patch.object(IDENTITY.os, "read", side_effect=recording_read),
            mock.patch.object(
                IDENTITY,
                "_update_digest_field",
                side_effect=recording_update_digest_field,
            ),
        ):
            payload = IDENTITY.collect_identity(
                str(repo),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(repo),
            )

        return payload, {
            "marker_name": marker_name,
            "marker_content": marker_content,
            "git_commands": git_commands,
            "command_outputs": command_outputs,
            "status_paths": status_paths,
            "read_chunks": read_chunks,
            "fingerprint_fields": fingerprint_fields,
            "original_identity": original_identity,
            "replaced": replaced["done"],
        }

    def test_temporary_git_root_replacement_is_publicly_fail_closed(self):
        payload, observations = self._capture_temporary_git_root_replacement()
        serialized = json.dumps(payload, sort_keys=True).encode("utf-8")

        self.assertNotIn(observations["marker_name"], serialized)
        self.assertNotIn(observations["marker_content"], serialized)
        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["write_eligibility"], "BLOCKED")
        self.assertIsNotNone(payload["reason"])

    def test_temporary_git_root_path_replacement_keeps_public_result_fail_closed(self):
        payload, observations = self._capture_temporary_git_root_replacement()
        marker_name = observations["marker_name"]
        marker_content = observations["marker_content"]
        git_commands = observations["git_commands"]
        command_outputs = observations["command_outputs"]
        status_paths = observations["status_paths"]
        read_chunks = observations["read_chunks"]
        fingerprint_fields = observations["fingerprint_fields"]

        self.assertTrue(observations["replaced"])
        self.assertTrue(git_commands)
        self.assertTrue(
            all(
                argv[argv.index("-C") + 1].startswith("/proc/self/fd/")
                for argv in git_commands
            )
        )
        dirty_commands = [
            argv
            for argv in git_commands
            if any(command in argv for command in ("ls-files", "ls-tree"))
        ]
        self.assertTrue(dirty_commands)
        for argv in dirty_commands:
            worktrees = [
                argument.split("=", 1)[1]
                for argument in argv
                if argument.startswith("--work-tree=")
            ]
            self.assertEqual(len(worktrees), 1)
            self.assertTrue(worktrees[0].startswith("/proc/self/fd/"))
            self.assertLess(
                next(
                    index
                    for index, argument in enumerate(argv)
                    if argument.startswith("--work-tree=")
                ),
                argv.index("-C"),
            )

        serialized_payload = json.dumps(payload, sort_keys=True).encode("utf-8")
        for marker in (marker_name, marker_content):
            self.assertNotIn(marker, serialized_payload)
        self.assertNotIn(marker_content, b"".join(command_outputs))
        self.assertNotIn(marker_content, b"\0".join(status_paths))
        self.assertNotIn(marker_content, b"".join(read_chunks))
        self.assertNotIn(marker_content, b"\0".join(fingerprint_fields))

        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "untracked_paths_changed_during_binding")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")
        self.assertEqual(
            payload["filesystem_identity"],
            observations["original_identity"],
        )

    def test_persistent_git_root_replacement_during_command_is_incomplete(self):
        repo = self.make_repo()
        replacement = self.make_repo("replacement")
        marker = b"replacement-repository-content\n"
        (replacement / "replacement-only.txt").write_bytes(marker)
        displaced = self.tempdir / "repo-displaced"
        replaced = {"done": False}
        real_run = IDENTITY.subprocess.run
        command_outputs = []

        def replacing_run(argv, *args, **kwargs):
            result = real_run(argv, *args, **kwargs)
            if (
                not replaced["done"]
                and argv[:1] == ["git"]
                and "-C" in argv
                and "ls-files" in argv and "--stage" in argv
            ):
                replaced["done"] = True
                command_outputs.append(result.stdout + result.stderr)
                repo.rename(displaced)
                replacement.rename(repo)
            return result

        try:
            with mock.patch.object(IDENTITY.subprocess, "run", side_effect=replacing_run):
                payload = IDENTITY.collect_identity(
                    str(repo),
                    IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                    workspace_pwd=str(repo),
                )
        finally:
            if displaced.exists():
                if repo.exists():
                    shutil.rmtree(repo)
                displaced.rename(repo)
            if replacement.exists():
                shutil.rmtree(replacement)

        self.assertTrue(replaced["done"])
        self.assertTrue(command_outputs)
        self.assertNotIn(marker, b"".join(command_outputs))
        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "project_root_replaced_during_git_command")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_repository_subdirectory_binds_to_verified_git_top_level(self):
        repo = self.make_repo()
        requested = repo / "src" / "nested"
        requested.mkdir(parents=True)

        payload = IDENTITY.collect_identity(
            str(requested),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(requested),
        )

        self.assertEqual(payload["status"], "bound")
        self.assertEqual(payload["binding_kind"], "GIT_WORKTREE")
        self.assertEqual(payload["git_top_level"], str(repo))
        self.assertEqual(
            payload["filesystem_identity"],
            IDENTITY._filesystem_identity(str(requested)),
        )
        self.assertTrue(payload["fingerprint_complete"])
        self.assertEqual(payload["write_eligibility"], "READ_ONLY")

    def test_repository_subdirectory_rebinding_is_incomplete(self):
        repo = self.make_repo()
        requested = repo / "src"
        requested.mkdir()
        replacement = repo / "src-replacement"
        replacement.mkdir()
        displaced = repo / "src-displaced"
        replaced = {"done": False}
        real_open_anchor = IDENTITY._open_directory_anchor

        def replacing_open_anchor(path, *args, **kwargs):
            if not replaced["done"] and os.path.realpath(path) == str(repo):
                replaced["done"] = True
                requested.rename(displaced)
                replacement.rename(requested)
            return real_open_anchor(path, *args, **kwargs)

        with mock.patch.object(
            IDENTITY,
            "_open_directory_anchor",
            side_effect=replacing_open_anchor,
        ):
            payload = IDENTITY.collect_identity(
                str(requested),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(requested),
            )

        self.assertTrue(replaced["done"])
        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "requested_workspace_replaced_during_binding")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_repository_root_rebinding_from_subdirectory_is_incomplete(self):
        repo = self.make_repo()
        requested = repo / "src"
        requested.mkdir()
        replacement = self.make_repo("replacement")
        marker = b"replacement-repository-content\n"
        (replacement / "replacement-only.txt").write_bytes(marker)
        replaced = {"done": False}
        read_chunks = []
        real_open_anchor = IDENTITY._open_directory_anchor
        real_read = IDENTITY.os.read

        def replacing_open_anchor(path, *args, **kwargs):
            if not replaced["done"] and os.path.realpath(path) == str(repo):
                replaced["done"] = True
                return real_open_anchor(str(replacement), *args, **kwargs)
            return real_open_anchor(path, *args, **kwargs)

        def recording_read(descriptor, size):
            chunk = real_read(descriptor, size)
            read_chunks.append(chunk)
            return chunk

        with (
            mock.patch.object(
                IDENTITY,
                "_open_directory_anchor",
                side_effect=replacing_open_anchor,
            ),
            mock.patch.object(IDENTITY.os, "read", side_effect=recording_read),
        ):
            payload = IDENTITY.collect_identity(
                str(requested),
                IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
                workspace_pwd=str(requested),
            )

        self.assertTrue(replaced["done"])
        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["reason"], "git_worktree_root_mismatch")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")
        self.assertNotIn(marker, b"".join(read_chunks))

    def test_untracked_read_chunking_does_not_change_fingerprint(self):
        repo = self.make_repo()
        (repo / "new.txt").write_text("chunked-content\n", encoding="utf-8")
        default_digest = hashlib.sha256()
        default_result = IDENTITY.hash_untracked(
            default_digest,
            str(repo),
            [b"new.txt"],
            1024,
        )
        real_read = IDENTITY.os.read

        def one_byte_read(descriptor, _size):
            return real_read(descriptor, 1)

        chunked_digest = hashlib.sha256()
        with mock.patch.object(IDENTITY.os, "read", side_effect=one_byte_read):
            chunked_result = IDENTITY.hash_untracked(
                chunked_digest,
                str(repo),
                [b"new.txt"],
                1024,
            )

        self.assertEqual(default_result, (True, None))
        self.assertEqual(chunked_result, (True, None))
        self.assertEqual(default_digest.hexdigest(), chunked_digest.hexdigest())

    def test_two_equal_incomplete_captures_are_unknown(self):
        first = {
            "status": "bound",
            "head": "a" * 40,
            "dirty_fingerprint": "b" * 64,
            "fingerprint_complete": True,
            "captured_at_utc": "2026-08-09T00:00:00Z",
            "bound_at_utc": "2026-08-09T00:00:00Z",
        }
        second = dict(
            first,
            captured_at_utc="2026-08-09T00:00:01Z",
            bound_at_utc="2026-08-09T00:00:01Z",
        )
        self.assertTrue(
            hasattr(IDENTITY, "capture_stable_identity"),
            "capture_stable_identity is not implemented",
        )
        with mock.patch.object(
            IDENTITY,
            "collect_identity",
            side_effect=(first, second),
        ) as collector:
            result = IDENTITY.capture_stable_identity("/project", 1024)

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "INCOMPLETE_IDENTITY_CAPTURE")
        self.assertEqual(result["capture_count"], 2)
        self.assertEqual(result["write_eligibility"], "BLOCKED")
        self.assertIsNone(result["identity"])
        self.assertEqual(collector.call_count, 2)

    def test_two_changed_captures_return_unknown_without_third_read(self):
        repo = self.make_repo()
        first = IDENTITY.collect_identity(
            str(repo), IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES, workspace_pwd=str(repo)
        )
        second = dict(first, head="c" * 40)
        self.assertTrue(
            hasattr(IDENTITY, "capture_stable_identity"),
            "capture_stable_identity is not implemented",
        )
        with mock.patch.object(
            IDENTITY,
            "collect_identity",
            side_effect=(first, second),
        ) as collector:
            result = IDENTITY.capture_stable_identity(str(repo), 1024, workspace_pwd=str(repo))

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "STATE_CHANGED_DURING_CAPTURE")
        self.assertIsNone(result["identity"])
        self.assertEqual(result["write_eligibility"], "BLOCKED")
        self.assertEqual(collector.call_count, 2)

    def test_single_capture_is_read_only_and_stable_pair_is_eligible(self):
        repo = self.make_repo()

        single = IDENTITY.collect_identity(
            str(repo),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo),
        )
        stable = IDENTITY.capture_stable_identity(
            str(repo),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo),
        )

        self.assertEqual(single["status"], "bound")
        self.assertTrue(single["fingerprint_complete"])
        self.assertEqual(single["write_eligibility"], "READ_ONLY")
        self.assertEqual(stable["status"], "STABLE")
        self.assertEqual(stable["capture_count"], 2)
        self.assertEqual(stable["write_eligibility"], "ELIGIBLE")
        self.assertIsNotNone(stable["identity"])
        self.assertEqual(stable["identity"]["write_eligibility"], "ELIGIBLE")

    def test_incomplete_baseline_cannot_be_write_eligible(self):
        repo = self.make_repo()
        (repo / "target.txt").write_text("target\n", encoding="utf-8")
        (repo / "linked.txt").symlink_to("target.txt")

        payload = self.schema_two_payload(
            self.run_identity(repo, "--workspace-pwd", repo)
        )

        self.assertEqual(payload["status"], "incomplete")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_detached_head_is_explicit(self):
        repo = self.make_repo()
        self.git(repo, "checkout", "--detach")

        result = self.run_identity(repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertIsNone(payload["branch"])
        self.assertEqual(len(payload["head"]), 40)

    def test_repository_without_commits_is_explicit(self):
        repo = self.make_repo(commit=False)

        result = self.run_identity(repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["branch"], "main")
        self.assertIsNone(payload["head"])

    def test_linked_worktree_has_distinct_git_and_common_dirs(self):
        repo = self.make_repo()
        linked = self.tempdir / "linked"
        self.git(repo, "worktree", "add", "-b", "feature/test", linked)

        result = self.run_identity(linked)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertNotEqual(payload["git_dir"], payload["git_common_dir"])
        self.assertEqual(payload["branch"], "feature/test")
        self.assertTrue(payload["worktree_id"])

    def test_schema_two_retains_schema_one_fields(self):
        repo = self.make_repo()

        payload = self.payload(self.run_identity(repo))

        legacy_fields = {
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
        schema_two_fields = {
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
        self.assertEqual(payload["schema_version"], 2)
        self.assertTrue(legacy_fields <= payload.keys())
        self.assertTrue(schema_two_fields <= payload.keys())

    def test_schema_validator_accepts_only_explicit_exact_versions(self):
        repo = self.make_repo()
        current = self.schema_two_payload(self.run_identity(repo))
        schema_one_fields = {
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
        historical = {
            key: copy.deepcopy(current[key])
            for key in schema_one_fields
        }
        historical["schema_version"] = 1

        self.assertTrue(
            hasattr(IDENTITY, "validate_identity_schema"),
            "validate_identity_schema is not implemented",
        )
        self.assertEqual(IDENTITY.validate_identity_schema(current), 2)
        with self.assertRaises(IDENTITY.IdentitySchemaError) as rejected:
            IDENTITY.validate_identity_schema(historical)
        self.assertEqual(rejected.exception.reason, "SCHEMA_UNSUPPORTED")
        self.assertEqual(
            IDENTITY.validate_identity_schema(
                historical,
                supported_versions=(1, 2),
            ),
            1,
        )

    def test_schema_validator_rejects_unknown_boolean_missing_and_extra_shapes(self):
        repo = self.make_repo()
        current = self.schema_two_payload(self.run_identity(repo))

        self.assertTrue(
            hasattr(IDENTITY, "validate_identity_schema"),
            "validate_identity_schema is not implemented",
        )
        cases = (
            ("not-object", [], "IDENTITY_NOT_OBJECT"),
            ("unknown-version", dict(current, schema_version=3), "SCHEMA_UNSUPPORTED"),
            ("boolean-version", dict(current, schema_version=True), "SCHEMA_UNSUPPORTED"),
            ("missing-field", {key: value for key, value in current.items() if key != "head"}, "SCHEMA_FIELDS_CHANGED"),
            ("extra-field", dict(current, unexpected=True), "SCHEMA_FIELDS_CHANGED"),
        )
        for name, value, reason in cases:
            with self.subTest(name=name):
                with self.assertRaises(IDENTITY.IdentitySchemaError) as rejected:
                    IDENTITY.validate_identity_schema(value)
                self.assertEqual(rejected.exception.reason, reason)

    def test_schema_validator_is_process_and_filesystem_free(self):
        repo = self.make_repo()
        current = self.schema_two_payload(self.run_identity(repo))

        self.assertTrue(
            hasattr(IDENTITY, "validate_identity_schema"),
            "validate_identity_schema is not implemented",
        )
        with (
            mock.patch.object(builtins, "open", side_effect=AssertionError("open called")) as builtin_open,
            mock.patch.object(IDENTITY.os, "open", side_effect=AssertionError("os.open called")) as os_open,
            mock.patch.object(IDENTITY.os, "stat", side_effect=AssertionError("stat called")) as os_stat,
            mock.patch.object(IDENTITY.os, "lstat", side_effect=AssertionError("lstat called")) as os_lstat,
            mock.patch.object(IDENTITY.os, "listdir", side_effect=AssertionError("listdir called")) as os_listdir,
            mock.patch.object(IDENTITY.os, "scandir", side_effect=AssertionError("scandir called")) as os_scandir,
            mock.patch.object(IDENTITY.subprocess, "run", side_effect=AssertionError("subprocess called")) as run,
            mock.patch.dict(IDENTITY.os.environ, {"SCHEMA_VALIDATOR_SENTINEL": "unchanged"}, clear=True),
        ):
            environment_before = dict(IDENTITY.os.environ)
            self.assertEqual(IDENTITY.validate_identity_schema(current), 2)
            self.assertEqual(dict(IDENTITY.os.environ), environment_before)

        for guarded in (
            builtin_open,
            os_open,
            os_stat,
            os_lstat,
            os_listdir,
            os_scandir,
            run,
        ):
            guarded.assert_not_called()

    def test_schema_two_identity_feeds_freshness_without_path_reads(self):
        repo = self.make_repo()
        single_identity = self.schema_two_payload(self.run_identity(repo))
        stable_capture = IDENTITY.capture_stable_identity(
            str(repo),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo),
        )
        identity = stable_capture["identity"]
        self.assertIsNotNone(identity)
        # Explicit synthetic program evidence, separate from the real collector.
        surface = identity["runtime_surface"]
        runtime = {
            "surface": "wsl" if surface["is_wsl"] else surface["platform"],
            "codex_version": "0.147.0",
            "core_sha256": "c" * 64,
        }

        with mock.patch.object(
            FRESHNESS.os,
            "open",
            side_effect=AssertionError("pure freshness comparison attempted a path read"),
        ):
            single_result = FRESHNESS.build_freshness_envelope(
                {"project": copy.deepcopy(single_identity), "runtime": runtime},
                copy.deepcopy(single_identity),
                runtime=runtime,
            )
            stable_result = FRESHNESS.build_freshness_envelope(
                {"project": copy.deepcopy(identity), "runtime": runtime},
                copy.deepcopy(identity),
                runtime=runtime,
            )

        self.assertEqual(single_result["freshness"]["state"], "UNKNOWN")
        self.assertFalse(
            single_result["freshness"]["write_precondition_satisfied"]
        )
        self.assertEqual(stable_result["project"], identity)
        self.assertEqual(stable_result["freshness"]["state"], "FRESH")
        self.assertTrue(
            stable_result["freshness"]["write_precondition_satisfied"]
        )
        self.assertFalse(stable_result["freshness"]["write_authorized"])

    def test_main_and_linked_worktree_have_distinct_worktree_ids(self):
        repo = self.make_repo()
        linked = self.tempdir / "linked-schema-two"
        self.git(repo, "worktree", "add", "-b", "feature/schema-two", linked)

        main = self.schema_two_payload(self.run_identity(repo))
        worktree = self.schema_two_payload(self.run_identity(linked))

        self.assertEqual(main["binding_kind"], "GIT_WORKTREE")
        self.assertEqual(worktree["binding_kind"], "GIT_WORKTREE")
        self.assertEqual(main["git_common_dir"], worktree["git_common_dir"])
        self.assertNotEqual(main["worktree_id"], worktree["worktree_id"])
        self.assertTrue(worktree["git"]["is_inside_worktree"])

    def test_detached_and_unborn_are_explicit(self):
        detached = self.make_repo("detached")
        self.git(detached, "checkout", "--detach")
        unborn = self.make_repo("unborn", commit=False)

        detached_payload = self.schema_two_payload(self.run_identity(detached))
        unborn_payload = self.schema_two_payload(self.run_identity(unborn))

        self.assertTrue(detached_payload["git"]["is_detached"])
        self.assertFalse(detached_payload["git"]["is_unborn"])
        self.assertIsNone(detached_payload["branch"])
        self.assertFalse(unborn_payload["git"]["is_detached"])
        self.assertTrue(unborn_payload["git"]["is_unborn"])
        self.assertIsNone(unborn_payload["head"])

    def test_bare_repository_is_read_only_not_worktree(self):
        bare = self.tempdir / "bare.git"
        self.run_command("git", "init", "--bare", "-b", "main", bare)

        result = self.run_identity(bare)
        payload = self.schema_two_payload(result)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["binding_kind"], "BARE_GIT")
        self.assertEqual(payload["write_eligibility"], "READ_ONLY")
        self.assertTrue(payload["is_git"])
        self.assertTrue(payload["git"]["is_bare"])
        self.assertFalse(payload["git"]["is_inside_worktree"])
        self.assertIsNone(payload["git_top_level"])
        self.assertFalse(payload["dirty"])
        self.assertIsNone(payload["dirty_fingerprint"])
        self.assertFalse(payload["fingerprint_complete"])
        self.assertEqual(payload["fingerprint_applicability"], "NOT_APPLICABLE")
        self.assertEqual(payload["fingerprint_reason"], "NOT_APPLICABLE_BARE")

    def test_non_git_directory_is_not_content_fresh(self):
        result = self.run_identity(self.tempdir)
        payload = self.schema_two_payload(result)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["binding_kind"], "NON_GIT_DIRECTORY")
        self.assertEqual(payload["write_eligibility"], "READ_ONLY")
        self.assertEqual(payload["fingerprint_applicability"], "NOT_APPLICABLE")
        self.assertEqual(payload["fingerprint_reason"], "LEGACY_NON_GIT_MARKER")
        self.assertFalse(payload["fingerprint_complete"])
        self.assertIsNotNone(payload["filesystem_identity"])

    def test_no_remote_and_multiple_remotes_are_valid(self):
        repo = self.make_repo()
        no_remote = self.schema_two_payload(self.run_identity(repo))
        self.git(repo, "remote", "add", "origin", "https://example.test/team/app.git")
        self.git(repo, "remote", "add", "upstream", "https://example.test/base/app.git")
        self.git(
            repo,
            "remote",
            "set-url",
            "--add",
            "origin",
            "ssh://git@example.test/team/app.git",
        )
        multiple = self.schema_two_payload(self.run_identity(repo))

        self.assertEqual(no_remote["status"], "bound")
        self.assertEqual(no_remote["remotes"], {})
        self.assertEqual(no_remote["git"]["remote_authority"], "NOT_APPLICABLE")
        self.assertEqual(set(multiple["remotes"]), {"origin", "upstream"})
        self.assertEqual(multiple["remotes"], {"origin": None, "upstream": None})
        self.assertEqual(multiple["git"]["remote_authority"], "UNDETERMINED")

    def test_remote_authority_is_undetermined_without_declaration(self):
        repo = self.make_repo()
        self.git(repo, "remote", "add", "origin", "https://example.test/team/app.git")

        payload = self.schema_two_payload(self.run_identity(repo))

        self.assertEqual(payload["git"]["remote_authority"], "UNDETERMINED")
        self.assertIsNone(payload["git"]["fork_authority_source"])

    def test_fork_relation_is_not_inferred(self):
        repo = self.make_repo()
        self.git(repo, "remote", "add", "origin", "https://example.test/team/app.git")
        self.git(repo, "remote", "add", "upstream", "https://example.test/base/app.git")

        payload = self.schema_two_payload(self.run_identity(repo))

        self.assertEqual(payload["git"]["fork_relation"], "UNDETERMINED")
        self.assertIsNone(payload["git"]["fork_authority_source"])

    def test_workspace_pwd_mismatch_stops(self):
        repo = self.make_repo()
        wrong = self.tempdir / "wrong-workspace"
        wrong.mkdir()

        result = self.run_identity(repo, "--workspace-pwd", wrong)
        payload = self.payload(result)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["reason"], "STOP_WRONG_WORKSPACE")

    def test_write_eligibility_requires_workspace_binding(self):
        repo = self.make_repo()

        diagnostic = self.schema_two_payload(self.run_identity(repo))
        single_bound = self.schema_two_payload(
            self.run_identity(repo, "--workspace-pwd", repo)
        )
        stable_bound = IDENTITY.capture_stable_identity(
            str(repo),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo),
        )

        self.assertEqual(diagnostic["write_eligibility"], "READ_ONLY")
        self.assertEqual(single_bound["write_eligibility"], "READ_ONLY")
        self.assertEqual(stable_bound["status"], "STABLE")
        self.assertEqual(stable_bound["identity"]["write_eligibility"], "ELIGIBLE")

    def test_alias_records_are_stable_and_sorted(self):
        repo = self.make_repo()
        alias_a = self.tempdir / "alias-a"
        alias_z = self.tempdir / "alias-z"
        alias_a.symlink_to(repo, target_is_directory=True)
        alias_z.symlink_to(repo, target_is_directory=True)

        first = self.payload(
            self.run_identity(repo, "--alias", alias_z, "--alias", alias_a)
        )["aliases"]
        second = self.payload(
            self.run_identity(repo, "--alias", alias_a, "--alias", alias_z)
        )["aliases"]

        self.assertEqual(first, second)
        self.assertEqual([item["input"] for item in first], sorted(map(str, (alias_a, alias_z))))
        self.assertTrue(all(item["relation_to_workspace"] == "SAME_OBJECT" for item in first))
        self.assertTrue(all("SYMLINK_ALIAS" in item["resolution_traits"] for item in first))

    def test_different_object_alias_blocks_write_eligibility(self):
        repo = self.make_repo()
        other = self.tempdir / "other"
        other.mkdir()

        payload = IDENTITY.collect_identity(
            str(repo),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo),
            aliases=(str(other),),
        )

        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["reason"], "AMBIGUOUS_PROJECT_ALIAS")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")

    def test_unknown_alias_blocks_write_eligibility(self):
        repo = self.make_repo()

        payload = IDENTITY.collect_identity(
            str(repo),
            IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES,
            workspace_pwd=str(repo),
            aliases=("relative-alias",),
        )

        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["reason"], "UNKNOWN_PROJECT_ALIAS")
        self.assertEqual(payload["write_eligibility"], "BLOCKED")


class GitCallbackSafetyTests(unittest.TestCase):
    """Real Git repositories; callbacks only write markers in our temporary root."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="identity-callback-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull, GIT_OPTIONAL_LOCKS="0")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "--template=", "-b", "main")
        (self.repo / ".git/info").mkdir()
        self.git("config", "user.name", "Synthetic Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "tracked.txt").write_bytes(b"base\n")
        self.git("add", "tracked.txt")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "initial")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True).stdout

    def capture(self):
        return IDENTITY.collect_identity(str(self.repo), 1024 * 1024,
                                         workspace_pwd=str(self.repo))

    def callback(self, kind):
        marker = self.root / (kind + "-executed")
        program = self.root / (kind + ".py")
        output = {
            "fsmonitor": "sys.stdout.buffer.write(b'token\\0/\\0')",
            "textconv": "sys.stdout.buffer.write(Path(sys.argv[-1]).read_bytes())",
            "clean": "sys.stdout.buffer.write(sys.stdin.buffer.read())",
            "process": "sys.exit(1)",
        }[kind]
        program.write_text("#!" + sys.executable + "\nfrom pathlib import Path\nimport sys\n"
                           + "Path(" + repr(str(marker)) + ").write_bytes(b'executed')\n"
                           + output + "\n")
        program.chmod(0o700)
        if kind == "fsmonitor":
            self.git("config", "core.fsmonitor", str(program))
        elif kind == "textconv":
            self.git("config", "diff.synthetic.textconv", str(program))
            (self.repo / ".git/info/attributes").write_text("tracked.txt diff=synthetic\n")
        else:
            self.git("config", "filter.synthetic." + kind, str(program))
            (self.repo / ".git/info/attributes").write_text("tracked.txt filter=synthetic\n")
        return marker

    def assert_callback_not_run(self, kind):
        marker = self.callback(kind)
        (self.repo / "tracked.txt").write_bytes(b"same\n")
        before_index = (self.repo / ".git/index").read_bytes()
        result = self.capture()
        self.assertFalse(marker.exists(), kind + " executed during read-only binding")
        self.assertEqual(result["status"], "bound", result)
        self.assertTrue(result["dirty"])
        self.assertEqual((self.repo / ".git/index").read_bytes(), before_index)

    def test_fsmonitor_never_executes(self):
        self.assert_callback_not_run("fsmonitor")

    def test_textconv_never_executes(self):
        self.assert_callback_not_run("textconv")

    def test_clean_filter_never_executes_even_for_same_size_change(self):
        self.assert_callback_not_run("clean")

    def test_process_filter_never_executes(self):
        self.assert_callback_not_run("process")

    def test_inherited_git_trace_cannot_create_files(self):
        trace = self.root / "inherited-trace"
        with mock.patch.dict(os.environ, {"GIT_TRACE": str(trace)}):
            result = self.capture()
        self.assertFalse(trace.exists())
        self.assertEqual(result["status"], "bound")

    def test_inherited_git_dir_cannot_redirect_identity(self):
        foreign = self.root / "foreign"
        subprocess.run(["git", "init", "--bare", "--template=", str(foreign)],
                       check=True, capture_output=True)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(foreign)}):
            result = self.capture()
        self.assertEqual(result["git_top_level"], str(self.repo))
        self.assertEqual(result["status"], "bound")

    def test_raw_content_change_with_restored_mtime_changes_fingerprint(self):
        path = self.repo / "tracked.txt"
        before = self.capture()
        metadata = path.stat()
        path.write_bytes(b"same\n")
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        after = self.capture()
        self.assertTrue(after["fingerprint_complete"])
        self.assertNotEqual(before["dirty_fingerprint"], after["dirty_fingerprint"])

    def test_tracked_symlink_hashes_link_without_reading_target(self):
        target = self.root / "outside"
        target.write_bytes(b"private")
        link = self.repo / "link"
        link.symlink_to(target)
        self.git("add", "link")
        first = self.capture()
        target.write_bytes(b"different private bytes")
        second = self.capture()
        self.assertTrue(first["fingerprint_complete"], first)
        self.assertEqual(first["dirty_fingerprint"], second["dirty_fingerprint"])
        link.unlink()
        link.symlink_to(self.root / "other-outside")
        third = self.capture()
        self.assertNotEqual(second["dirty_fingerprint"], third["dirty_fingerprint"])

    def test_deleted_tracked_directory_is_complete_and_dirty(self):
        directory = self.repo / "nested"
        directory.mkdir()
        (directory / "file").write_bytes(b"tracked")
        self.git("add", "nested/file")
        shutil.rmtree(directory)
        result = self.capture()
        self.assertTrue(result["fingerprint_complete"], result)
        self.assertTrue(result["dirty"])


    def test_gitlink_never_claims_nested_content_is_fingerprinted(self):
        head = self.git("rev-parse", "HEAD").strip().decode()
        self.git("update-index", "--add", "--cacheinfo", "160000," + head + ",nested")
        (self.repo / "nested").mkdir()
        result = self.capture()
        self.assertFalse(result["fingerprint_complete"])
        self.assertEqual(result["write_eligibility"], "BLOCKED")
        self.assertEqual(result["reason"], "tracked_submodule_not_fingerprinted")

    def test_tracked_parent_symlink_cannot_read_outside_content(self):
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / "file").write_bytes(b"local")
        self.git("add", "nested/file")
        shutil.rmtree(nested)
        nested.symlink_to(self.root, target_is_directory=True)
        (self.root / "file").write_bytes(b"outside-marker")
        chunks = []
        original_read = IDENTITY.os.read

        def record_read(fd, size):
            data = original_read(fd, size)
            chunks.append(data)
            return data

        with mock.patch.object(IDENTITY.os, "read", side_effect=record_read):
            result = self.capture()
        self.assertFalse(result["fingerprint_complete"])
        self.assertNotIn(b"outside-marker", b"".join(chunks))

    def test_missing_promisor_tree_cannot_start_remote_helper(self):
        marker = self.root / "remote-executed"
        helper = self.root / "remote-helper.py"
        helper.write_text("#!" + sys.executable + "\nfrom pathlib import Path\n"
                          + "Path(" + repr(str(marker)) + ").write_bytes(b'executed')\n")
        helper.chmod(0o700)
        tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        self.git("config", "remote.origin.url", "ext::" + str(helper))
        self.git("config", "remote.origin.promisor", "true")
        self.git("config", "protocol.ext.allow", "always")
        (self.repo / ".git/objects" / tree[:2] / tree[2:]).unlink()
        result = self.capture()
        self.assertFalse(marker.exists(), "identity attempted a lazy fetch")
        self.assertFalse(result["fingerprint_complete"])
        self.assertEqual(result["write_eligibility"], "BLOCKED")

    def test_sha256_repository_tracks_raw_content(self):
        self.repo = self.root / "sha256-repo"
        self.repo.mkdir()
        self.git("init", "--object-format=sha256", "--template=", "-b", "main")
        (self.repo / "tracked.txt").write_bytes(b"base\n")
        self.git("add", "tracked.txt")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=test@example.invalid",
                 "-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "initial")
        first = self.capture()
        self.assertTrue(first["fingerprint_complete"], first)
        self.assertFalse(first["dirty"])
        (self.repo / "tracked.txt").write_bytes(b"same\n")
        second = self.capture()
        self.assertTrue(second["dirty"])
        self.assertNotEqual(first["dirty_fingerprint"], second["dirty_fingerprint"])

    def test_crlf_conversion_uses_conservative_raw_dirty_state(self):
        self.git("config", "core.autocrlf", "true")
        (self.repo / "tracked.txt").write_bytes(b"base\r\n")
        result = self.capture()
        self.assertTrue(result["fingerprint_complete"], result)
        self.assertTrue(result["dirty"])


if __name__ == "__main__":
    unittest.main()
