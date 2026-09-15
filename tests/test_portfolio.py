# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from workbench import portfolio as PORTFOLIO
except ImportError:
    PORTFOLIO = None


ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_SCRIPT = ROOT / "workbench" / "portfolio.py"


def registry_fixture():
    return {
        "schema_version": 1,
        "portfolio_id": "product-portfolio",
        "display_name": "产品组合",
        "projects": [
            {
                "id": "project-a",
                "display_name": "项目 A",
                "path": "/projects/project-a",
                "priority": 90,
                "lifecycle": "ACTIVE",
                "depends_on": [],
                "status_source_schema": "JSON_STATUS_V1",
                "status_source_path": "docs/project-status.json",
            },
            {
                "id": "project-b",
                "display_name": "项目 B",
                "path": "/projects/project-b",
                "priority": 70,
                "lifecycle": "WAITING_APPROVAL",
                "depends_on": ["project-a"],
                "status_source_schema": "NONE",
            },
        ],
    }


def registry_toml():
    return """\
schema_version = 1
portfolio_id = "product-portfolio"
display_name = "产品组合"

[[projects]]
id = "project-a"
display_name = "项目 A"
path = "/projects/project-a"
priority = 90
lifecycle = "ACTIVE"
depends_on = []
status_source_schema = "JSON_STATUS_V1"
status_source_path = "docs/project-status.json"

[[projects]]
id = "project-b"
display_name = "项目 B"
path = "/projects/project-b"
priority = 70
lifecycle = "WAITING_APPROVAL"
depends_on = ["project-a"]
status_source_schema = "NONE"
"""


def status_fixture():
    return {
        "schema_version": 1,
        "milestone": "完成只读 Portfolio",
        "milestone_state": "IN_PROGRESS",
        "state": "ACTIVE",
        "blockers": ["等待独立审查"],
        "next_step": "运行确定性回归",
        "milestone_value": 80,
        "completion_confidence": 70,
        "blocker_cost": 20,
        "task_size": 30,
        "approval_wait": 10,
        "release_window": 60,
        "risk_level": 25,
        "updated_at_utc": "2026-08-31T06:00:00Z",
    }


def filesystem_identity(path):
    metadata = os.stat(path, follow_symlinks=False)
    return {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "object_type": "directory",
    }


def status_project(path, *, schema="JSON_STATUS_V1", source="status.json"):
    project = {
        "id": "project-a",
        "display_name": "项目 A",
        "path": os.fspath(path),
        "priority": 90,
        "lifecycle": "ACTIVE",
        "depends_on": [],
        "status_source_schema": schema,
    }
    if schema == "JSON_STATUS_V1":
        project["status_source_path"] = source
    return project


def identity_fixture(*, path="/projects/project-a", st_dev=1, st_ino=2):
    return {
        "schema_version": 2,
        "status": "bound",
        "reason": None,
        "requested_cwd": path,
        "pwd": path,
        "realpath": path,
        "is_git": True,
        "git_top_level": path,
        "git_dir": "/projects/.git/worktrees/project-a",
        "git_common_dir": "/projects/.git",
        "worktree_id": "project-a",
        "branch": "main",
        "head": "a" * 40,
        "dirty": False,
        "dirty_fingerprint": "b" * 64,
        "fingerprint_complete": True,
        "remotes": [],
        "bound_at_utc": "2026-08-31T06:00:00Z",
        "binding_kind": "GIT_WORKTREE",
        "write_eligibility": "ELIGIBLE",
        "path_input_kind": "WSL_POSIX_ABSOLUTE",
        "resolution_traits": [],
        "logical_path": path,
        "physical_path": path,
        "filesystem_identity": {
            "st_dev": st_dev,
            "st_ino": st_ino,
            "object_type": "directory",
        },
        "aliases": [],
        "runtime_surface": {
            "platform": "linux",
            "is_wsl": True,
            "wsl_distro_name": "Ubuntu",
        },
        "git": {
            "is_inside_worktree": True,
            "is_inside_git_dir": False,
            "is_bare": False,
            "is_detached": False,
            "is_unborn": False,
            "remote_authority": "NOT_APPLICABLE",
            "fork_relation": "NOT_APPLICABLE",
            "fork_authority_source": None,
        },
        "dirty_fingerprint_schema": 3,
        "fingerprint_applicability": "REQUIRED",
        "fingerprint_reason": None,
        "captured_at_utc": "2026-08-31T06:00:00Z",
    }


def non_git_identity(*, path="/projects/project-a", bare=False):
    value = identity_fixture(path=path)
    value.update(
        binding_kind="BARE_GIT" if bare else "NON_GIT_DIRECTORY",
        write_eligibility="READ_ONLY",
        is_git=bare,
        git_top_level=None,
        git_dir="/projects/project-a" if bare else None,
        git_common_dir="/projects/project-a" if bare else None,
        worktree_id=None,
        branch="main" if bare else None,
        head="a" * 40 if bare else None,
        dirty=False,
        dirty_fingerprint=None if bare else "d" * 64,
        fingerprint_complete=False,
        fingerprint_applicability="NOT_APPLICABLE",
        fingerprint_reason="NOT_APPLICABLE_BARE" if bare else "LEGACY_NON_GIT_MARKER",
    )
    value["git"] = {
        "is_inside_worktree": False,
        "is_inside_git_dir": False,
        "is_bare": bare,
        "is_detached": False,
        "is_unborn": False,
        "remote_authority": "NOT_APPLICABLE",
        "fork_relation": "NOT_APPLICABLE",
        "fork_authority_source": None,
    }
    return value


def capture_fixture(
    project,
    *,
    head=None,
    status_hash=None,
    milestone_state="IN_PROGRESS",
    status_state="ACTIVE",
    capture_state="CURRENT_COMPLETE",
    reason=None,
    object_identity=None,
    binding_kind="GIT_WORKTREE",
):
    project_id = project["id"]
    identity = {
        "status": "bound",
        "binding_kind": binding_kind,
        "physical_path": project["path"],
        "git_top_level": project["path"] if binding_kind == "GIT_WORKTREE" else None,
        "git_common_dir": "/projects/.git",
        "worktree_id": project_id if binding_kind == "GIT_WORKTREE" else None,
        "branch": "main",
        "head": head or ("a" * 40),
        "dirty": False,
        "dirty_fingerprint": "b" * 64 if binding_kind == "GIT_WORKTREE" else None,
        "fingerprint_complete": binding_kind == "GIT_WORKTREE",
    }
    if project.get("status_source_schema") == "NONE":
        status = {
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
    else:
        status = {
            "schema": "JSON_STATUS_V1",
            "path": project.get("status_source_path"),
            "content_sha256": status_hash
            or (("c" if project_id == "project-a" else "d") * 64),
            "state": status_state,
            "reason": None,
            "milestone": f"{project_id} milestone",
            "milestone_state": milestone_state,
            "blockers": [],
            "next_step": f"continue {project_id}",
            "milestone_value": 80,
            "completion_confidence": 70,
            "blocker_cost": 20,
            "task_size": 30,
            "approval_wait": 10,
            "release_window": 60,
            "risk_level": 25,
            "updated_at_utc": "2026-08-31T06:00:00Z",
        }
    return {
        "id": project_id,
        "display_name": project["display_name"],
        "configured_path": project["path"],
        "lifecycle": project["lifecycle"],
        "priority": project["priority"],
        "depends_on": copy.deepcopy(project["depends_on"]),
        "capture_state": capture_state,
        "reason": reason,
        "identity": identity,
        "filesystem_identity": object_identity
        or {
            "st_dev": 1,
            "st_ino": 10 if project_id == "project-a" else 20,
            "object_type": "directory",
        },
        "status_source": status,
        "write_authorized": False,
    }


class PortfolioRegistryTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(PORTFOLIO, "workbench.portfolio is not implemented")
        return PORTFOLIO

    def assert_rejected(self, value, reason):
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.module().validate_registry(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_valid_registry_is_deep_copied(self):
        value = registry_fixture()

        result = self.module().validate_registry(value)

        self.assertEqual(result, value)
        self.assertIsNot(result, value)
        self.assertIsNot(result["projects"], value["projects"])

    def test_empty_project_list_is_rejected(self):
        value = registry_fixture()
        value["projects"] = []
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_unknown_top_or_project_field_is_rejected(self):
        for label, change in (
            ("top", lambda value: value.update({"head": "a" * 40})),
            ("project", lambda value: value["projects"][0].update({"dirty": False})),
        ):
            with self.subTest(label=label):
                value = registry_fixture()
                change(value)
                self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

    def test_bool_priority_is_rejected(self):
        value = registry_fixture()
        value["projects"][0]["priority"] = True
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

    def test_invalid_or_duplicate_ids_are_rejected(self):
        for label, change in (
            ("portfolio", lambda value: value.update({"portfolio_id": "A"})),
            ("project", lambda value: value["projects"][0].update({"id": "x"})),
            (
                "duplicate",
                lambda value: value["projects"][1].update({"id": "project-a"}),
            ),
        ):
            with self.subTest(label=label):
                value = registry_fixture()
                change(value)
                self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_relative_control_or_duplicate_normalized_paths_are_rejected(self):
        cases = (
            ("relative", "projects/project-a", "FIELD_VALUE_INVALID"),
            ("control", "/projects/project-a\nother", "FIELD_VALUE_INVALID"),
            ("duplicate", "/projects/other/../project-a", "REGISTRY_PATH_DUPLICATE"),
        )
        for label, path, reason in cases:
            with self.subTest(label=label):
                value = registry_fixture()
                value["projects"][1]["path"] = path
                self.assert_rejected(value, reason)

    def test_invalid_lifecycle_is_rejected(self):
        value = registry_fixture()
        value["projects"][0]["lifecycle"] = "RUNNING"
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_invalid_dependencies_are_rejected(self):
        cases = (
            ("unknown", ["missing"], "REGISTRY_DEPENDENCY_UNKNOWN"),
            ("self", ["project-a"], "FIELD_VALUE_INVALID"),
            ("duplicate", ["project-b", "project-b"], "FIELD_VALUE_INVALID"),
        )
        for label, dependencies, reason in cases:
            with self.subTest(label=label):
                value = registry_fixture()
                value["projects"][0]["depends_on"] = dependencies
                self.assert_rejected(value, reason)

    def test_dependency_cycle_is_rejected(self):
        value = registry_fixture()
        value["projects"][0]["depends_on"] = ["project-b"]
        value["projects"][1]["depends_on"] = ["project-a"]
        self.assert_rejected(value, "REGISTRY_DEPENDENCY_CYCLE")

    def test_status_schema_and_path_must_match(self):
        cases = (
            (
                "none-with-path",
                lambda project: project.update({"status_source_path": "status.json"}),
                1,
            ),
            (
                "json-without-path",
                lambda project: project.pop("status_source_path"),
                0,
            ),
            (
                "invalid-schema",
                lambda project: project.update({"status_source_schema": "YAML"}),
                0,
            ),
            (
                "parent-segment",
                lambda project: project.update({"status_source_path": "../status.json"}),
                0,
            ),
        )
        for label, change, index in cases:
            with self.subTest(label=label):
                value = registry_fixture()
                change(value["projects"][index])
                self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_load_registry_reads_regular_toml_without_project_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "registry.toml").write_text(registry_toml(), encoding="utf-8")
            with mock.patch.object(
                self.module().project_identity,
                "collect_identity",
            ) as identity_collector:
                result = self.module().load_registry(root)

        self.assertEqual(result, registry_fixture())
        identity_collector.assert_not_called()

    def test_registry_symlink_fifo_directory_and_oversize_are_rejected(self):
        module = self.module()
        for label in ("symlink", "fifo", "directory", "oversize"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "registry.toml"
                metadata_patch = mock.patch.object(module, "_entry_metadata_at")
                if label == "symlink":
                    (root / "actual.toml").write_text(registry_toml(), encoding="utf-8")
                    target.symlink_to("actual.toml")
                elif label == "fifo":
                    target.write_text(registry_toml(), encoding="utf-8")
                    metadata_patch = mock.patch.object(
                        module,
                        "_entry_metadata_at",
                        return_value=SimpleNamespace(
                            st_mode=stat.S_IFIFO,
                            st_size=0,
                        ),
                    )
                elif label == "directory":
                    target.mkdir()
                else:
                    target.write_bytes(b"x" * (module.MAX_REGISTRY_BYTES + 1))
                reason = (
                    "REGISTRY_TOO_LARGE" if label == "oversize" else "REGISTRY_NOT_REGULAR"
                )
                context = metadata_patch if label == "fifo" else mock.patch.object(
                    module,
                    "_entry_metadata_at",
                    wraps=module._entry_metadata_at,
                )
                with context, self.assertRaises(module.PortfolioContractError) as caught:
                    module.load_registry(root)
                self.assertEqual(caught.exception.reason, reason)

    def test_duplicate_toml_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "registry.toml").write_text(
                'schema_version = 1\nschema_version = 1\n',
                encoding="utf-8",
            )
            with self.assertRaises(self.module().PortfolioContractError) as caught:
                self.module().load_registry(root)
        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_root_identity_change_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "registry.toml").write_text(registry_toml(), encoding="utf-8")
            original = self.module()._path_directory_identity
            calls = 0

            def changed(path):
                nonlocal calls
                calls += 1
                value = original(path)
                if calls > 1:
                    value = (*value[:-1], value[-1] + 1)
                return value

            with mock.patch.object(
                self.module(),
                "_path_directory_identity",
                side_effect=changed,
            ):
                with self.assertRaises(self.module().PortfolioContractError) as caught:
                    self.module().load_registry(root)

        self.assertEqual(caught.exception.reason, "PORTFOLIO_ROOT_CHANGED")

    def test_file_mutation_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "registry.toml"
            path.write_text(registry_toml(), encoding="utf-8")
            original = self.module()._read_fd_bytes

            def mutate(fd, limit):
                data = original(fd, limit)
                path.write_text(registry_toml() + "\n", encoding="utf-8")
                return data

            with mock.patch.object(
                self.module(),
                "_read_fd_bytes",
                side_effect=mutate,
            ):
                with self.assertRaises(self.module().PortfolioContractError) as caught:
                    self.module().load_registry(root)

        self.assertEqual(caught.exception.reason, "REGISTRY_CHANGED_DURING_READ")


class PortfolioStatusSourceTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(PORTFOLIO, "workbench.portfolio is not implemented")
        return PORTFOLIO

    def write_status(self, root, value=None):
        path = Path(root) / "status.json"
        path.write_text(
            json.dumps(value or status_fixture(), ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def read_status(self, root, **kwargs):
        project = kwargs.pop("project", status_project(root))
        expected = kwargs.pop("expected", filesystem_identity(root))
        return self.module().read_status_source(
            project,
            expected_root_identity=expected,
            **kwargs,
        )

    def assert_rejected(self, root, reason, **kwargs):
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.read_status(root, **kwargs)
        self.assertEqual(caught.exception.reason, reason)

    def test_valid_status_is_normalized_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_status(directory)
            result = self.read_status(directory)

        self.assertEqual(result["schema"], "JSON_STATUS_V1")
        self.assertEqual(result["path"], "status.json")
        self.assertEqual(result["milestone"], "完成只读 Portfolio")
        self.assertEqual(result["blockers"], ["等待独立审查"])
        self.assertIsNone(result["reason"])
        self.assertRegex(result["content_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("schema_version", result)

    def test_none_status_source_is_explicit_without_filesystem_read(self):
        project = status_project("/not/read", schema="NONE")
        with mock.patch("os.open") as opener:
            result = self.module().read_status_source(
                project,
                expected_root_identity={
                    "st_dev": 1,
                    "st_ino": 2,
                    "object_type": "directory",
                },
            )

        opener.assert_not_called()
        self.assertEqual(result["schema"], "NONE")
        self.assertEqual(result["reason"], "STATUS_SOURCE_NOT_CONFIGURED")
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["blockers"], [])

    def test_duplicate_json_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "status.json").write_bytes(
                b'{"schema_version":1,"schema_version":1}'
            )
            self.assert_rejected(directory, "STATUS_SOURCE_SCHEMA_INVALID")

    def test_missing_unknown_and_invalid_status_fields_are_rejected(self):
        cases = (
            ("missing", lambda value: value.pop("milestone")),
            ("unknown", lambda value: value.update({"head": "a" * 40})),
            ("bool-metric", lambda value: value.update({"risk_level": True})),
            ("bad-state", lambda value: value.update({"state": "RUNNING"})),
            (
                "bad-milestone-state",
                lambda value: value.update({"milestone_state": "READY"}),
            ),
            (
                "bad-time",
                lambda value: value.update({"updated_at_utc": "2026-08-31T06:00:00+00:00"}),
            ),
            ("too-many-blockers", lambda value: value.update({"blockers": ["x"] * 51})),
            ("control-text", lambda value: value.update({"next_step": "bad\nstep"})),
        )
        for label, change in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                value = status_fixture()
                change(value)
                self.write_status(directory, value)
                self.assert_rejected(directory, "STATUS_SOURCE_SCHEMA_INVALID")

    def test_bom_and_invalid_utf8_are_rejected(self):
        for label, data in (
            ("bom", b"\xef\xbb\xbf{}"),
            ("invalid-utf8", b"\xff"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "status.json").write_bytes(data)
                self.assert_rejected(directory, "STATUS_SOURCE_SCHEMA_INVALID")

    def test_symlink_intermediate_symlink_and_fifo_are_rejected(self):
        module = self.module()
        for label in ("final-symlink", "intermediate-symlink", "fifo"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = status_project(root)
                context = mock.patch.object(
                    module,
                    "_entry_metadata_at",
                    wraps=module._entry_metadata_at,
                )
                if label == "final-symlink":
                    (root / "actual.json").write_text("{}", encoding="utf-8")
                    (root / "status.json").symlink_to("actual.json")
                elif label == "intermediate-symlink":
                    (root / "actual").mkdir()
                    (root / "actual" / "status.json").write_text("{}", encoding="utf-8")
                    (root / "docs").symlink_to("actual", target_is_directory=True)
                    project = status_project(root, source="docs/status.json")
                else:
                    self.write_status(root)
                    context = mock.patch.object(
                        module,
                        "_entry_metadata_at",
                        return_value=SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0),
                    )
                with context:
                    self.assert_rejected(
                        root,
                        "STATUS_SOURCE_NOT_REGULAR",
                        project=project,
                    )

    def test_oversized_and_changed_during_read_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_bytes(b"x" * (self.module().MAX_STATUS_SOURCE_BYTES + 1))
            self.assert_rejected(directory, "STATUS_SOURCE_TOO_LARGE")

        with tempfile.TemporaryDirectory() as directory:
            path = self.write_status(directory)
            original = self.module()._read_fd_bytes

            def mutate(fd, limit):
                data = original(fd, limit)
                path.write_bytes(data + b" ")
                return data

            with mock.patch.object(self.module(), "_read_fd_bytes", side_effect=mutate):
                self.assert_rejected(directory, "STATUS_SOURCE_CHANGED_DURING_READ")

    def test_intermediate_directory_replacement_after_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            self.write_status(nested)
            project = status_project(root, source="nested/status.json")
            original = self.module()._entry_metadata_at
            intermediate_reads = 0

            def replace_on_second_lookup(directory_fd, name):
                nonlocal intermediate_reads
                metadata = original(directory_fd, name)
                if name != "nested":
                    return metadata
                intermediate_reads += 1
                if intermediate_reads == 1:
                    return metadata
                return SimpleNamespace(
                    st_dev=metadata.st_dev,
                    st_ino=metadata.st_ino + 1,
                    st_mode=metadata.st_mode,
                    st_size=metadata.st_size,
                    st_mtime_ns=metadata.st_mtime_ns,
                    st_ctime_ns=metadata.st_ctime_ns,
                )

            with mock.patch.object(
                self.module(),
                "_entry_metadata_at",
                side_effect=replace_on_second_lookup,
            ):
                self.assert_rejected(
                    root,
                    "STATUS_SOURCE_CHANGED_DURING_READ",
                    project=project,
                )

        self.assertEqual(intermediate_reads, 2)

    def test_root_mismatch_rejects_replacement_marker_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = "replacement-marker-only.txt"
            value = status_fixture()
            value["milestone"] = marker
            self.write_status(root, value)
            expected = filesystem_identity(root)
            expected["st_ino"] += 1

            with self.assertRaises(self.module().PortfolioContractError) as caught:
                self.read_status(root, expected=expected)

        self.assertEqual(caught.exception.reason, "STATUS_SOURCE_OUTSIDE_PROJECT")
        self.assertNotIn(marker, str(caught.exception))


class PortfolioCaptureTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(PORTFOLIO, "workbench.portfolio is not implemented")
        return PORTFOLIO

    def test_git_capture_requires_two_equal_complete_identities(self):
        first = identity_fixture()
        result = self.module().classify_capture_pair(first, copy.deepcopy(first))

        self.assertEqual(result["capture_state"], "CURRENT_COMPLETE")
        self.assertIsNone(result["reason"])
        self.assertFalse(result["write_authorized"])
        self.assertEqual(result["identity"]["head"], "a" * 40)
        self.assertNotIn("remotes", result["identity"])

    def test_changed_head_or_fingerprint_is_incomplete(self):
        for field, value in (("head", "f" * 40), ("dirty_fingerprint", "e" * 64)):
            with self.subTest(field=field):
                first = identity_fixture()
                second = copy.deepcopy(first)
                second[field] = value
                result = self.module().classify_capture_pair(first, second)
                self.assertEqual(result["capture_state"], "CURRENT_INCOMPLETE")
                self.assertEqual(result["reason"], "STATE_CHANGED_DURING_CAPTURE")
                self.assertFalse(result["write_authorized"])

    def test_incomplete_and_ambiguous_identity_fail_closed(self):
        incomplete = identity_fixture()
        incomplete.update(status="incomplete", reason="DIRTY_FINGERPRINT_INCOMPLETE")
        result = self.module().classify_capture_pair(incomplete, copy.deepcopy(incomplete))
        self.assertEqual(result["capture_state"], "CURRENT_INCOMPLETE")
        self.assertEqual(result["reason"], "IDENTITY_CAPTURE_INCOMPLETE")

        ambiguous = identity_fixture()
        ambiguous["aliases"] = [
            {"relation_to_workspace": "DIFFERENT_OBJECT"},
        ]
        result = self.module().classify_capture_pair(ambiguous, copy.deepcopy(ambiguous))
        self.assertEqual(result["capture_state"], "AMBIGUOUS")
        self.assertEqual(result["reason"], "IDENTITY_CHANGED_DURING_CAPTURE")

    def test_invalid_identity_schema_is_rejected(self):
        invalid = identity_fixture()
        invalid["unexpected"] = True
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.module().classify_capture_pair(invalid, identity_fixture())
        self.assertEqual(caught.exception.reason, "IDENTITY_SCHEMA_INVALID")

    def test_stable_non_git_and_bare_are_complete_but_not_write_authorized(self):
        for label, value in (
            ("non-git", non_git_identity()),
            ("bare", non_git_identity(bare=True)),
        ):
            with self.subTest(label=label):
                result = self.module().classify_capture_pair(value, copy.deepcopy(value))
                self.assertEqual(result["capture_state"], "CURRENT_COMPLETE")
                self.assertIsNone(result["reason"])
                self.assertFalse(result["write_authorized"])

    def test_capture_project_calls_collector_twice_with_exact_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status.json").write_text(
                json.dumps(status_fixture(), ensure_ascii=False),
                encoding="utf-8",
            )
            metadata = os.stat(root)
            identity = identity_fixture(
                path=os.fspath(root),
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
            )
            collector = mock.Mock(side_effect=[identity, copy.deepcopy(identity)])

            result = self.module().capture_project(
                status_project(root),
                baseline_project=None,
                identity_collector=collector,
            )

        self.assertEqual(collector.call_count, 2)
        collector.assert_has_calls(
            [
                mock.call(
                    os.fspath(root),
                    self.module().MAX_UNTRACKED_BYTES,
                    workspace_pwd=os.fspath(root),
                    aliases=(),
                ),
                mock.call(
                    os.fspath(root),
                    self.module().MAX_UNTRACKED_BYTES,
                    workspace_pwd=os.fspath(root),
                    aliases=(),
                ),
            ]
        )
        self.assertEqual(result["capture_state"], "CURRENT_COMPLETE")
        self.assertEqual(result["status_source"]["milestone"], "完成只读 Portfolio")
        self.assertFalse(result["write_authorized"])

    def test_status_source_error_is_project_local_and_redacted(self):
        marker = "replacement-marker-only.txt"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = status_fixture()
            value["milestone"] = marker
            (root / "status.json").write_text(
                json.dumps(value, ensure_ascii=False),
                encoding="utf-8",
            )
            metadata = os.stat(root)
            first = identity_fixture(
                path=os.fspath(root),
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino + 1,
            )
            collector = mock.Mock(side_effect=[first, copy.deepcopy(first)])

            result = self.module().capture_project(
                status_project(root),
                baseline_project=None,
                identity_collector=collector,
            )

        self.assertEqual(result["capture_state"], "CURRENT_INCOMPLETE")
        self.assertEqual(result["reason"], "STATUS_SOURCE_OUTSIDE_PROJECT")
        self.assertNotIn(marker, json.dumps(result, ensure_ascii=False))


class PortfolioSnapshotTests(unittest.TestCase):
    TOP_FIELDS = {
        "schema_version",
        "portfolio_id",
        "registry_sha256",
        "generated_at_utc",
        "portfolio_state",
        "write_authorized",
        "projects",
        "snapshot_sha256",
    }
    PROJECT_FIELDS = {
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

    def module(self):
        self.assertIsNotNone(PORTFOLIO, "workbench.portfolio is not implemented")
        return PORTFOLIO

    def build(self, registry=None, captures=None, baseline=None, generated_at_utc=None):
        registry = copy.deepcopy(registry or registry_fixture())
        captures = captures or {
            project["id"]: capture_fixture(project)
            for project in registry["projects"]
        }

        def capture(project, **_kwargs):
            return copy.deepcopy(captures[project["id"]])

        with mock.patch.object(self.module(), "capture_project", side_effect=capture):
            return self.module().build_portfolio_snapshot(
                registry,
                baseline=baseline,
                identity_collector=mock.sentinel.identity_collector,
                generated_at_utc=generated_at_utc,
            )

    def test_snapshot_has_exact_closed_fields_and_no_write_authority(self):
        result = self.build(generated_at_utc="2026-08-31T06:00:00Z")

        self.assertEqual(set(result), self.TOP_FIELDS)
        self.assertEqual(set(result["projects"][0]), self.PROJECT_FIELDS)
        self.assertFalse(result["write_authorized"])
        self.assertRegex(result["snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("reason", result["projects"][0])
        self.assertNotIn("filesystem_identity", result["projects"][0])

    def test_generated_time_does_not_change_snapshot_digest(self):
        first = self.build(generated_at_utc="2026-08-31T06:00:00Z")
        second = self.build(generated_at_utc="2026-08-31T06:01:00Z")
        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])

    def test_config_identity_and_status_changes_change_digest(self):
        original = self.build(generated_at_utc="2026-08-31T06:00:00Z")

        registry = registry_fixture()
        registry["projects"][0]["priority"] = 89
        config_changed = self.build(registry=registry, generated_at_utc="2026-08-31T06:00:00Z")

        captures = {
            project["id"]: capture_fixture(project)
            for project in registry_fixture()["projects"]
        }
        captures["project-a"]["identity"]["head"] = "f" * 40
        identity_changed = self.build(captures=captures, generated_at_utc="2026-08-31T06:00:00Z")

        captures["project-a"]["status_source"]["content_sha256"] = "e" * 64
        status_changed = self.build(captures=captures, generated_at_utc="2026-08-31T06:00:00Z")

        self.assertNotEqual(original["snapshot_sha256"], config_changed["snapshot_sha256"])
        self.assertNotEqual(original["snapshot_sha256"], identity_changed["snapshot_sha256"])
        self.assertNotEqual(identity_changed["snapshot_sha256"], status_changed["snapshot_sha256"])

    def test_first_capture_unknown_then_equal_baseline_is_fresh(self):
        first = self.build(generated_at_utc="2026-08-31T06:00:00Z")
        self.assertEqual(first["projects"][0]["freshness"]["state"], "UNKNOWN")
        self.assertEqual(
            first["projects"][0]["freshness"]["reasons"],
            ["NO_PORTFOLIO_BASELINE"],
        )

        second = self.build(
            baseline=first,
            generated_at_utc="2026-08-31T06:01:00Z",
        )
        self.assertEqual(second["projects"][0]["freshness"]["state"], "FRESH")
        self.assertEqual(second["projects"][0]["freshness"]["changed_fields"], [])

    def test_changed_current_facts_are_stale_without_baseline_copyback(self):
        baseline = self.build(generated_at_utc="2026-08-31T06:00:00Z")
        registry = registry_fixture()
        captures = {
            project["id"]: capture_fixture(project)
            for project in registry["projects"]
        }
        captures["project-a"]["identity"]["head"] = "f" * 40
        captures["project-a"]["status_source"]["content_sha256"] = "e" * 64

        current = self.build(
            registry=registry,
            captures=captures,
            baseline=baseline,
            generated_at_utc="2026-08-31T06:01:00Z",
        )

        freshness = current["projects"][0]["freshness"]
        self.assertEqual(freshness["state"], "STALE")
        self.assertEqual(freshness["changed_fields"], ["identity", "status_source"])
        self.assertEqual(current["projects"][0]["identity"]["head"], "f" * 40)

    def test_non_git_freshness_remains_unknown(self):
        registry = registry_fixture()
        captures = {
            project["id"]: capture_fixture(project)
            for project in registry["projects"]
        }
        captures["project-a"]["identity"]["binding_kind"] = "NON_GIT_DIRECTORY"
        captures["project-a"]["identity"]["git_top_level"] = None
        captures["project-a"]["identity"]["worktree_id"] = None
        captures["project-a"]["identity"]["fingerprint_complete"] = False
        baseline = self.build(registry=registry, captures=captures)
        current = self.build(registry=registry, captures=captures, baseline=baseline)

        self.assertEqual(current["projects"][0]["freshness"]["state"], "UNKNOWN")
        self.assertIn(
            "UNSUPPORTED_IDENTITY_CLASS",
            current["projects"][0]["freshness"]["reasons"],
        )

    def test_snapshot_unknown_field_and_digest_mismatch_are_rejected(self):
        value = self.build()
        invalid = copy.deepcopy(value)
        invalid["approval"] = "old"
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.module().validate_snapshot(invalid)
        self.assertEqual(caught.exception.reason, "SNAPSHOT_SCHEMA_INVALID")

        invalid = copy.deepcopy(value)
        invalid["snapshot_sha256"] = "0" * 64
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.module().validate_snapshot(invalid)
        self.assertEqual(caught.exception.reason, "SNAPSHOT_DIGEST_MISMATCH")

    def test_snapshot_rejects_invalid_nested_values_and_cross_field_duplicates(self):
        value = self.build(generated_at_utc="2026-08-31T06:00:00Z")
        cases = (
            ("project-id-type", lambda item: item["projects"][0].update(id=["project-a"])),
            ("relative-path", lambda item: item["projects"][0].update(configured_path="relative")),
            ("identity-type", lambda item: item["projects"][0]["identity"].update(dirty="false")),
            ("status-type", lambda item: item["projects"][0]["status_source"].update(blockers="none")),
            ("freshness-type", lambda item: item["projects"][0]["freshness"].update(reasons="none")),
            (
                "recommendation-type",
                lambda item: item["projects"][0]["recommendation"].update(rationale="none"),
            ),
            (
                "duplicate-project-id",
                lambda item: item["projects"][1].update(id=item["projects"][0]["id"]),
            ),
            (
                "duplicate-rank",
                lambda item: item["projects"][1]["recommendation"].update(
                    rank=item["projects"][0]["recommendation"]["rank"]
                ),
            ),
            ("invalid-generated-time", lambda item: item.update(generated_at_utc="2026-02-31T00:00:00Z")),
        )

        for label, mutate in cases:
            with self.subTest(label=label):
                invalid = copy.deepcopy(value)
                mutate(invalid)
                invalid["snapshot_sha256"] = self.module()._snapshot_digest(invalid)
                with self.assertRaises(self.module().PortfolioContractError) as caught:
                    self.module().validate_snapshot(invalid)
                self.assertEqual(caught.exception.reason, "SNAPSHOT_SCHEMA_INVALID")

    def test_invalid_baseline_digest_and_registry_are_rejected(self):
        baseline = self.build()
        invalid = copy.deepcopy(baseline)
        invalid["snapshot_sha256"] = "0" * 64
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.build(baseline=invalid)
        self.assertEqual(caught.exception.reason, "BASELINE_DIGEST_MISMATCH")

        other = copy.deepcopy(baseline)
        other["registry_sha256"] = "0" * 64
        other["snapshot_sha256"] = self.module()._snapshot_digest(other)
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.build(baseline=other)
        self.assertEqual(caught.exception.reason, "BASELINE_REGISTRY_MISMATCH")

    def test_registry_mismatch_accepts_only_declared_comparable_config_changes(self):
        baseline = self.build(generated_at_utc="2026-08-31T06:00:00Z")

        priority_changed = registry_fixture()
        priority_changed["projects"][0]["priority"] = 89
        stale = self.build(
            registry=priority_changed,
            baseline=baseline,
            generated_at_utc="2026-08-31T06:01:00Z",
        )
        self.assertIn(
            "config.priority",
            stale["projects"][0]["freshness"]["changed_fields"],
        )

        display_name_changed = registry_fixture()
        display_name_changed["projects"][0]["display_name"] = "另一个项目"
        project_set_changed = registry_fixture()
        extra = copy.deepcopy(project_set_changed["projects"][0])
        extra.update(id="project-c", display_name="项目 C", path="/projects/project-c")
        project_set_changed["projects"].append(extra)

        for label, registry in (
            ("display-name", display_name_changed),
            ("project-set", project_set_changed),
        ):
            with self.subTest(label=label):
                with self.assertRaises(self.module().PortfolioContractError) as caught:
                    self.build(registry=registry, baseline=baseline)
                self.assertEqual(caught.exception.reason, "BASELINE_REGISTRY_MISMATCH")


class PortfolioRankingTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(PORTFOLIO, "workbench.portfolio is not implemented")
        return PORTFOLIO

    def build(self, registry, captures, *, baseline=None):
        def capture(project, **_kwargs):
            return copy.deepcopy(captures[project["id"]])

        with mock.patch.object(self.module(), "capture_project", side_effect=capture):
            return self.module().build_portfolio_snapshot(
                copy.deepcopy(registry),
                baseline=baseline,
                identity_collector=mock.sentinel.identity_collector,
                generated_at_utc="2026-08-31T06:00:00Z",
            )

    def registry_with_projects(self, definitions):
        registry = registry_fixture()
        registry["projects"] = []
        for project_id, lifecycle, priority, depends_on in definitions:
            registry["projects"].append(
                {
                    "id": project_id,
                    "display_name": project_id,
                    "path": f"/projects/{project_id}",
                    "priority": priority,
                    "lifecycle": lifecycle,
                    "depends_on": list(depends_on),
                    "status_source_schema": "JSON_STATUS_V1",
                    "status_source_path": "status.json",
                }
            )
        return registry

    def captures_for(self, registry):
        return {
            project["id"]: capture_fixture(
                project,
                object_identity={
                    "st_dev": 1,
                    "st_ino": index + 10,
                    "object_type": "directory",
                },
            )
            for index, project in enumerate(registry["projects"])
        }

    def test_bucket_precedence_is_deterministic(self):
        registry = self.registry_with_projects(
            (
                ("archived", "ARCHIVED", 100, ()),
                ("paused", "PAUSED", 100, ()),
                ("waiting", "WAITING_APPROVAL", 100, ()),
                ("blocked", "ACTIVE", 100, ()),
                ("actionable", "ACTIVE", 1, ()),
            )
        )
        captures = self.captures_for(registry)
        captures["blocked"]["status_source"]["state"] = "BLOCKED"
        captures["blocked"]["status_source"]["milestone_state"] = "BLOCKED"

        snapshot = self.build(registry, captures)

        self.assertEqual(
            [project["id"] for project in snapshot["projects"]],
            ["actionable", "blocked", "waiting", "paused", "archived"],
        )
        self.assertEqual(
            [project["recommendation"]["bucket"] for project in snapshot["projects"]],
            ["ACTIONABLE", "BLOCKED", "WAITING_APPROVAL", "PAUSED", "ARCHIVED"],
        )

    def test_fail_closed_capture_precedes_lifecycle_and_status_routing(self):
        registry = self.registry_with_projects(
            (
                ("ambiguous-waiting", "WAITING_APPROVAL", 90, ()),
                ("status-paused", "ACTIVE", 80, ()),
                ("status-archived", "ACTIVE", 70, ()),
                ("status-waiting", "ACTIVE", 60, ()),
            )
        )
        captures = self.captures_for(registry)
        captures["ambiguous-waiting"].update(
            capture_state="AMBIGUOUS",
            reason="IDENTITY_CHANGED_DURING_CAPTURE",
        )
        captures["status-paused"]["status_source"]["state"] = "PAUSED"
        captures["status-archived"]["status_source"]["state"] = "ARCHIVED"
        captures["status-waiting"]["status_source"]["state"] = "WAITING_APPROVAL"

        snapshot = self.build(registry, captures)
        buckets = {
            project["id"]: project["recommendation"]["bucket"]
            for project in snapshot["projects"]
        }

        self.assertEqual(buckets["ambiguous-waiting"], "BLOCKED")
        self.assertEqual(buckets["status-paused"], "PAUSED")
        self.assertEqual(buckets["status-archived"], "ARCHIVED")
        self.assertEqual(buckets["status-waiting"], "WAITING_APPROVAL")

    def test_unmet_dependency_blocks_until_current_complete_and_fresh(self):
        registry = self.registry_with_projects(
            (
                ("project-a", "ACTIVE", 50, ("project-b",)),
                ("project-b", "ACTIVE", 40, ()),
            )
        )
        captures = self.captures_for(registry)
        captures["project-b"]["status_source"]["milestone_state"] = "COMPLETE"
        first = self.build(registry, captures)
        self.assertEqual(
            next(p for p in first["projects"] if p["id"] == "project-a")["recommendation"]["bucket"],
            "BLOCKED",
        )

        second = self.build(registry, captures, baseline=first)
        project_a = next(p for p in second["projects"] if p["id"] == "project-a")
        self.assertEqual(project_a["recommendation"]["bucket"], "ACTIONABLE")
        self.assertIn("DEPENDENCIES_SATISFIED", project_a["recommendation"]["rationale"])

    def test_priority_freshness_numeric_and_id_sort_directions(self):
        registry = self.registry_with_projects(
            (
                ("project-b", "ACTIVE", 50, ()),
                ("project-a", "ACTIVE", 50, ()),
                ("project-c", "ACTIVE", 80, ()),
            )
        )
        captures = self.captures_for(registry)
        baseline = self.build(registry, captures)
        current = self.build(registry, captures, baseline=baseline)
        self.assertEqual(
            [project["id"] for project in current["projects"]],
            ["project-c", "project-a", "project-b"],
        )

        captures["project-a"]["status_source"].update(
            release_window=90,
            milestone_value=90,
            blocker_cost=90,
            completion_confidence=90,
            risk_level=1,
            approval_wait=1,
            task_size=1,
        )
        captures["project-b"]["status_source"].update(
            release_window=10,
            milestone_value=10,
            blocker_cost=10,
            completion_confidence=10,
            risk_level=99,
            approval_wait=99,
            task_size=99,
        )
        changed = self.build(registry, captures, baseline=baseline)
        ids = [project["id"] for project in changed["projects"]]
        self.assertLess(ids.index("project-a"), ids.index("project-b"))

    def test_confidence_and_rationale_follow_freshness(self):
        registry = self.registry_with_projects((("project-a", "ACTIVE", 50, ()),))
        captures = self.captures_for(registry)
        first = self.build(registry, captures)
        self.assertEqual(first["projects"][0]["recommendation"]["confidence"], "UNKNOWN")
        self.assertIn("FRESHNESS:UNKNOWN", first["projects"][0]["recommendation"]["rationale"])

        second = self.build(registry, captures, baseline=first)
        self.assertEqual(second["projects"][0]["recommendation"]["confidence"], "HIGH")
        self.assertIn("PRIORITY:50", second["projects"][0]["recommendation"]["rationale"])

    def test_rationale_explains_rank_fields_milestone_blockers_and_dependency_ids(self):
        registry = self.registry_with_projects(
            (
                ("project-a", "ACTIVE", 50, ("project-b",)),
                ("project-b", "ACTIVE", 40, ()),
            )
        )
        captures = self.captures_for(registry)
        captures["project-a"]["status_source"].update(
            milestone="完成只读 Portfolio",
            blockers=["等待独立审查"],
        )
        project_a = next(
            project
            for project in self.build(registry, captures)["projects"]
            if project["id"] == "project-a"
        )
        rationale = project_a["recommendation"]["rationale"]

        for expected in (
            "MILESTONE:完成只读 Portfolio",
            "BLOCKERS:等待独立审查",
            "DEPENDENCIES_UNMET:project-b",
            "RELEASE_WINDOW:60",
            "MILESTONE_VALUE:80",
            "BLOCKER_COST:20",
            "COMPLETION_CONFIDENCE:70",
            "RISK_LEVEL:25",
            "APPROVAL_WAIT:10",
            "TASK_SIZE:30",
        ):
            self.assertIn(expected, rationale)

    def test_duplicate_filesystem_or_git_worktree_identity_blocks_snapshot(self):
        for label in ("filesystem", "git"):
            with self.subTest(label=label):
                registry = self.registry_with_projects(
                    (
                        ("project-a", "ACTIVE", 50, ()),
                        ("project-b", "ACTIVE", 40, ()),
                    )
                )
                captures = self.captures_for(registry)
                if label == "filesystem":
                    captures["project-b"]["filesystem_identity"] = copy.deepcopy(
                        captures["project-a"]["filesystem_identity"]
                    )
                else:
                    captures["project-b"]["identity"]["worktree_id"] = "project-a"
                snapshot = self.build(registry, captures)
                self.assertEqual(snapshot["portfolio_state"], "BLOCKED")
                self.assertTrue(
                    all(
                        "PROJECT_OBJECT_DUPLICATE" in project["freshness"]["reasons"]
                        for project in snapshot["projects"]
                    )
                )
                self.assertTrue(
                    all(
                        project["recommendation"]["bucket"] == "BLOCKED"
                        for project in snapshot["projects"]
                    )
                )


class PortfolioViewTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(PORTFOLIO, "workbench.portfolio is not implemented")
        return PORTFOLIO

    def snapshot(self):
        registry = registry_fixture()
        registry["projects"][0]["display_name"] = "项目 | A"
        captures = {
            project["id"]: capture_fixture(project)
            for project in registry["projects"]
        }

        def capture(project, **_kwargs):
            return copy.deepcopy(captures[project["id"]])

        with mock.patch.object(self.module(), "capture_project", side_effect=capture):
            return self.module().build_portfolio_snapshot(
                registry,
                generated_at_utc="2026-08-31T06:00:00Z",
            )

    def test_markdown_has_fixed_chinese_columns_and_escapes_tables(self):
        snapshot = self.snapshot()
        result = self.module().render_portfolio_markdown(snapshot)

        self.assertIn("# 多项目 Portfolio", result)
        self.assertIn(
            "| 顺序 | 项目 | 当前阶段 | 里程碑 | 阻塞/依赖 | 下一步 | freshness | 验证时间 | 理由 |",
            result,
        )
        self.assertIn("项目 \\| A", result)
        self.assertIn("NO_PORTFOLIO_BASELINE", result)

    def test_markdown_omits_absolute_paths_and_unapproved_material(self):
        snapshot = self.snapshot()
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertIn("/projects/project-a", encoded)

        result = self.module().render_portfolio_markdown(snapshot)

        self.assertNotIn("/projects/project-a", result)
        self.assertNotIn("git_common_dir", result)
        self.assertNotIn("dirty_fingerprint", result)
        self.assertNotIn("remote", result.lower())

    def test_handoff_never_inherits_approval_or_authorizes_write(self):
        handoff = self.module().build_project_handoff(self.snapshot(), "project-a")

        self.assertEqual(handoff["target_path"], "/projects/project-a")
        self.assertTrue(handoff["required_new_task"])
        self.assertEqual(handoff["first_command"], "pwd")
        self.assertEqual(
            handoff["required_rechecks"],
            ["path", "git", "head", "dirty", "freshness", "project_rules"],
        )
        self.assertEqual(handoff["inherited_approvals"], [])
        self.assertFalse(handoff["write_authorized"])

    def test_missing_or_incomplete_handoff_is_rejected_exactly(self):
        snapshot = self.snapshot()
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.module().build_project_handoff(snapshot, "missing")
        self.assertEqual(caught.exception.reason, "PROJECT_NOT_FOUND")

        snapshot["projects"][0]["capture_state"] = "AMBIGUOUS"
        snapshot["snapshot_sha256"] = self.module()._snapshot_digest(snapshot)
        with self.assertRaises(self.module().PortfolioContractError) as caught:
            self.module().build_project_handoff(snapshot, "project-a")
        self.assertEqual(caught.exception.reason, "PROJECT_NOT_HANDOFF_ELIGIBLE")


class PortfolioCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "source"
        self.project_a = self.root / "project-a"
        self.project_b = self.root / "project-b"
        self.source.mkdir()
        self.git(self.source, "init", "-b", "main")
        (self.source / "status.json").write_text(
            json.dumps(status_fixture(), ensure_ascii=False),
            encoding="utf-8",
        )
        (self.source / "README.md").write_text("portfolio fixture", encoding="utf-8")
        self.git(self.source, "add", "status.json", "README.md")
        self.git(
            self.source,
            "-c",
            "user.name=Portfolio Tests",
            "-c",
            "user.email=portfolio@example.test",
            "commit",
            "-m",
            "fixture",
        )
        self.git(self.source, "worktree", "add", "-b", "portfolio-a", self.project_a)
        self.git(self.source, "worktree", "add", "-b", "portfolio-b", self.project_b)
        self.portfolio_root = self.root / "portfolio"
        self.portfolio_root.mkdir()
        (self.portfolio_root / "registry.toml").write_text(
            self.registry_text(),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", *map(os.fspath, args)],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def registry_text(self):
        return f'''\
schema_version = 1
portfolio_id = "test-portfolio"
display_name = "测试组合"

[[projects]]
id = "project-a"
display_name = "项目 A"
path = "{self.project_a.as_posix()}"
priority = 90
lifecycle = "ACTIVE"
depends_on = []
status_source_schema = "JSON_STATUS_V1"
status_source_path = "status.json"

[[projects]]
id = "project-b"
display_name = "项目 B"
path = "{self.project_b.as_posix()}"
priority = 70
lifecycle = "ACTIVE"
depends_on = []
status_source_schema = "JSON_STATUS_V1"
status_source_path = "status.json"
'''

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.fspath(PORTFOLIO_SCRIPT), *map(os.fspath, args)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def git_state(self, project):
        return (
            self.git(project, "rev-parse", "HEAD").stdout.strip(),
            self.git(project, "status", "--porcelain=v1", "--untracked-files=all").stdout,
        )

    def test_json_markdown_and_handoff_are_stdout_only_and_read_only(self):
        before_a = self.git_state(self.project_a)
        before_b = self.git_state(self.project_b)
        files_before = sorted(path.relative_to(self.root) for path in self.root.rglob("*") if path.is_file())

        json_result = self.run_cli("--portfolio-root", self.portfolio_root)
        markdown_result = self.run_cli(
            "--portfolio-root", self.portfolio_root, "--view", "markdown"
        )
        handoff_result = self.run_cli(
            "--portfolio-root", self.portfolio_root, "--handoff-project", "project-a"
        )

        self.assertEqual(json_result.returncode, 3, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual({p["id"] for p in payload["projects"]}, {"project-a", "project-b"})
        self.assertFalse(payload["write_authorized"])
        self.assertEqual(markdown_result.returncode, 3, markdown_result.stderr)
        self.assertIn("# 多项目 Portfolio", markdown_result.stdout)
        self.assertEqual(handoff_result.returncode, 0, handoff_result.stderr)
        self.assertTrue(json.loads(handoff_result.stdout)["required_new_task"])
        self.assertEqual(json_result.stderr, "")
        self.assertEqual(markdown_result.stderr, "")
        self.assertEqual(handoff_result.stderr, "")
        self.assertEqual(self.git_state(self.project_a), before_a)
        self.assertEqual(self.git_state(self.project_b), before_b)
        files_after = sorted(path.relative_to(self.root) for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(files_after, files_before)

    def test_baseline_can_make_second_capture_complete(self):
        first = self.run_cli("--portfolio-root", self.portfolio_root)
        baseline = self.portfolio_root / "baseline.json"
        baseline.write_text(first.stdout, encoding="utf-8")

        second = self.run_cli(
            "--portfolio-root", self.portfolio_root,
            "--baseline", baseline,
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["portfolio_state"], "COMPLETE")

    def test_invalid_options_and_baselines_are_sanitized(self):
        invalid_combination = self.run_cli(
            "--portfolio-root", self.portfolio_root,
            "--view", "markdown",
            "--handoff-project", "project-a",
        )
        output_file = self.run_cli(
            "--portfolio-root", self.portfolio_root,
            "--output-file", self.root / "out.json",
        )
        target = self.portfolio_root / "real-baseline.json"
        target.write_text("{}", encoding="utf-8")
        symlink = self.portfolio_root / "baseline.json"
        symlink.symlink_to(target.name)
        symlink_result = self.run_cli(
            "--portfolio-root", self.portfolio_root,
            "--baseline", symlink,
        )

        for result in (invalid_combination, output_file, symlink_result):
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertRegex(result.stderr, r"^portfolio rejected: [A-Z_]+\n$")

    def test_self_digested_malformed_baseline_is_sanitized(self):
        first = self.run_cli("--portfolio-root", self.portfolio_root)
        self.assertEqual(first.returncode, 3, first.stderr)
        malformed = json.loads(first.stdout)
        malformed["projects"][0]["id"] = ["unhashable"]
        self.assertIsNotNone(PORTFOLIO)
        malformed["snapshot_sha256"] = PORTFOLIO._snapshot_digest(malformed)
        baseline = self.portfolio_root / "malformed-baseline.json"
        baseline.write_text(json.dumps(malformed, ensure_ascii=False), encoding="utf-8")

        result = self.run_cli(
            "--portfolio-root",
            self.portfolio_root,
            "--baseline",
            baseline,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "portfolio rejected: BASELINE_SCHEMA_INVALID\n")

    def test_blocked_snapshot_uses_exit_four(self):
        alias = self.root / "project-a-alias"
        alias.symlink_to(self.project_a, target_is_directory=True)
        text = self.registry_text().replace(
            f'path = "{self.project_b.as_posix()}"',
            f'path = "{alias.as_posix()}"',
        )
        (self.portfolio_root / "registry.toml").write_text(text, encoding="utf-8")
        result = self.run_cli("--portfolio-root", self.portfolio_root)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stdout)["portfolio_state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
