# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Synthetic recovery contracts; no historical incident data or private imports."""

import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_skill.py"
SKILL_NAME = "vibe-project-lead-zh"
SPEC = importlib.util.spec_from_file_location("public_recovery_installer", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load public installer")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)

def make_synthetic_source(self):
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

class SyntheticRecoveryFixture:
    STAGE_BASENAME = ".vibe-project-lead-zh-stage-synthetic"

    def setUp(self):
        make_synthetic_source(self)
        self.failed_stage = self.skills_root / self.STAGE_BASENAME
        shutil.copytree(self.source, self.failed_stage)
        self.failed_stage.chmod(0o777)
        for child in self.failed_stage.rglob("*"):
            child.chmod(0o777)
        self.neighbor = self.skills_root / "existing-skill"
        self.neighbor.mkdir()
        (self.neighbor / "owned-by-user").write_text("keep", encoding="utf-8")
        self.archive_root = self.codex_home / ".skill-rollbacks"
        self.source_entries = INSTALLER.scan_tree(self.source)
        self.stage_entries = INSTALLER.scan_tree(self.failed_stage)
        self.frozen_stage_identity = INSTALLER.directory_identity(
            self.failed_stage, "unsafe_failed_stage"
        )
        self.anchors = {
            "stage_basename": self.STAGE_BASENAME,
            "runtime_entries": {
                "SKILL.md": "file", "agents": "directory",
                "agents/openai.yaml": "file", "references": "directory",
                "references/project-binding.md": "file",
                "references/manager-workflow.md": "file",
                "references/safety-gates.md": "file",
                "references/acceptance-and-supervision.md": "file",
                "references/SKILL_INDEX_ZH.md": "file",
            },
            "stage_root_identity": copy.deepcopy(self.frozen_stage_identity),
            "source_root_mode": 0o755,
            "file_anchors": {
                relative: (entry["size"], entry["sha256"])
                for relative, entry in self.source_entries.items()
                if entry["type"] == "file"
            },
            "source_entry_modes": {
                relative: entry["mode"] for relative, entry in self.source_entries.items()
            },
            "stage_entry_modes": {
                relative: entry["mode"] for relative, entry in self.stage_entries.items()
            },
        }

    def build_request(self, source=None, stage=None, skills_root=None):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            return INSTALLER.build_staging_recovery_request(
                source or self.source, stage or self.failed_stage,
                skills_root or self.skills_root, anchors=self.anchors,
            )

    def run_recovery(self, *, source=None, stage=None, skills_root=None,
                     check_only=False, confirmation=None):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            with redirect_stdout(output):
                try:
                    return_code = INSTALLER.archive_staging(
                        source or self.source, stage or self.failed_stage,
                        skills_root or self.skills_root, check_only, confirmation,
                        anchors=self.anchors,
                    )
                except INSTALLER.InstallError as error:
                    status = error.status or (
                        "unknown" if error.reason.endswith("_outcome_unknown")
                        else "drift" if error.exit_code == 3 else "refused"
                    )
                    return_code = INSTALLER.emit(
                        status, error.exit_code, reason=error.reason,
                        differences=error.differences,
                    )
        return subprocess.CompletedProcess([], return_code, output.getvalue(), "")

    def snapshot_tree(self, root):
        paths = [root, *sorted(root.rglob('*'))]
        snapshot = []
        for path in paths:
            metadata = os.lstat(path)
            if stat.S_ISDIR(metadata.st_mode):
                entry_type = 'directory'
                digest = None
            elif stat.S_ISREG(metadata.st_mode):
                entry_type = 'file'
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            elif stat.S_ISLNK(metadata.st_mode):
                entry_type = 'symlink'
                digest = os.readlink(path)
            else:
                entry_type = 'other'
                digest = None
            snapshot.append({'path': path.relative_to(root).as_posix() if path != root else '.', 'type': entry_type, 'mode': stat.S_IMODE(metadata.st_mode), 'device': metadata.st_dev, 'inode': metadata.st_ino, 'size': metadata.st_size, 'nlink': metadata.st_nlink, 'mtime_ns': metadata.st_mtime_ns, 'digest': digest})
        return snapshot

    def assert_not_ready(self, callable_value):
        before = self.snapshot_tree(self.codex_home)
        with self.assertRaises(INSTALLER.InstallError) as raised:
            callable_value()
        self.assertEqual(raised.exception.reason, 'staging_recovery_not_ready')
        self.assertEqual(raised.exception.exit_code, 2)
        self.assertEqual(raised.exception.status, 'error')
        self.assertTrue(self.failed_stage.is_dir())
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

class PublicInstallerBoundaryTests(SyntheticRecoveryFixture, unittest.TestCase):
    def run_public_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args],
            env={**os.environ, "CODEX_HOME": str(self.codex_home)},
            capture_output=True, text=True, check=False,
        )

    def test_public_cli_rejects_incident_command_without_writes(self):
        before = self.snapshot_tree(self.codex_home)
        result = self.run_public_cli(
            "archive-staging", "--source", str(self.source),
            "--stage", str(self.failed_stage),
            "--skills-root", str(self.skills_root), "--check-only",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

    def test_all_eight_general_commands_remain_available(self):
        before = self.snapshot_tree(self.codex_home)
        for command in (
            "install", "verify", "rollback", "attest-switch-backend",
            "prepare-upgrade", "upgrade", "inspect-upgrade", "restore-version",
        ):
            with self.subTest(command=command):
                result = self.run_public_cli(command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot_tree(self.codex_home), before)


class RecoveryAnchorContractTests(SyntheticRecoveryFixture, unittest.TestCase):
    def test_explicit_synthetic_anchors_work_without_global_patch(self):
        before = self.snapshot_tree(self.codex_home)
        try:
            request = self.build_request()
        except TypeError as error:
            self.fail(f"explicit recovery anchors are unsupported: {error}")
        self.assertEqual(request["source_stage"], str(self.failed_stage))
        self.assertEqual(len(request["runtime_layout"]["expected_entries"]), 9)
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

    def test_incomplete_or_unsafe_anchors_are_refused_before_tree_scan(self):
        mutations = []
        for field in self.anchors:
            changed = copy.deepcopy(self.anchors)
            del changed[field]
            mutations.append(changed)
        changed = copy.deepcopy(self.anchors)
        changed["unexpected"] = True
        mutations.append(changed)
        for field, value in (
            ("stage_basename", "../stage"),
            ("stage_basename", ".vibe-project-lead-zh-stage-"),
            ("stage_basename", ".vibe-project-lead-zh-stage-a/b"),
            ("stage_basename", 9),
            ("source_root_mode", True),
            ("source_root_mode", 0o10000),
            ("runtime_entries", {}),
            ("runtime_entries", {"../SKILL.md": "file"}),
            ("runtime_entries", {"SKILL.md": "symlink"}),
            ("runtime_entries", {"SKILL.md": "file", "missing/file": "file"}),
            ("stage_root_identity", {}),
            ("file_anchors", {}),
            ("source_entry_modes", {}),
            ("stage_entry_modes", {}),
        ):
            changed = copy.deepcopy(self.anchors)
            changed[field] = value
            mutations.append(changed)
        for field in ("device", "inode", "size", "nlink", "mtime_ns", "mode"):
            changed = copy.deepcopy(self.anchors)
            changed["stage_root_identity"][field] = True
            mutations.append(changed)
        for mode_field in ("source_entry_modes", "stage_entry_modes"):
            for value in (-1, True, 0o10000):
                changed = copy.deepcopy(self.anchors)
                changed[mode_field]["SKILL.md"] = value
                mutations.append(changed)
        for value in ((-1, "a" * 64), (True, "a" * 64), (2, "A" * 64),
                      (2, "g" * 64), [2, "a" * 64]):
            changed = copy.deepcopy(self.anchors)
            changed["file_anchors"]["SKILL.md"] = value
            mutations.append(changed)
        for changed in mutations:
            with self.subTest(anchors=repr(changed)[:80]):
                before = self.snapshot_tree(self.codex_home)
                with mock.patch.object(INSTALLER, "scan_tree") as scan:
                    with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
                        with self.assertRaises(INSTALLER.InstallError) as raised:
                            INSTALLER.build_staging_recovery_request(
                                self.source, self.failed_stage, self.skills_root,
                                anchors=changed,
                            )
                self.assertEqual(raised.exception.reason, "staging_recovery_not_ready")
                self.assertEqual(raised.exception.exit_code, 2)
                self.assertEqual(raised.exception.differences, ["<recovery-anchors>"])
                scan.assert_not_called()
                self.assertEqual(self.snapshot_tree(self.codex_home), before)

    def test_later_caller_mutation_cannot_change_checked_anchor_snapshot(self):
        # A caller mutates its original object during the first filesystem read.
        # The request must remain bound to the already-validated deep snapshot.
        original = copy.deepcopy(self.anchors)
        real_root = INSTALLER.validate_skills_root

        def mutate_caller(root):
            self.anchors["runtime_entries"].clear()
            self.anchors["file_anchors"].clear()
            return real_root(root)

        with mock.patch.object(INSTALLER, "validate_skills_root", side_effect=mutate_caller):
            request = self.build_request()
        self.assertEqual(request["runtime_layout"]["expected_entries"],
                         sorted(original["runtime_entries"]))
        self.assertEqual(set(request["source_entries"]), set(original["runtime_entries"]))

    def test_unhashable_layout_kind_is_refused_without_filesystem_reads(self):
        before = self.snapshot_tree(self.codex_home)
        self.anchors["runtime_entries"]["agents"] = []
        with mock.patch.object(INSTALLER, "validate_skills_root") as read_root:
            with self.assertRaises(Exception) as raised:
                self.build_request()
        self.assertIsInstance(raised.exception, INSTALLER.InstallError)
        self.assertEqual(raised.exception.reason, "staging_recovery_not_ready")
        self.assertEqual(raised.exception.differences, ["<recovery-anchors>"])
        read_root.assert_not_called()
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

    def test_source_layout_is_not_tied_to_current_thirteen_file_package(self):
        # A second, independent synthetic seven-file source has its own binding.
        self.assertEqual(len(self.anchors["file_anchors"]), 7)
        self.assertEqual(sum(v == "file" for v in INSTALLER.EXPECTED_RUNTIME_ENTRIES.values()), 13)
        request = self.build_request()
        self.assertEqual(len(request["stage_entries"]), 9)

class StagingRecoveryCheckTests(SyntheticRecoveryFixture, unittest.TestCase):

    def test_check_only_is_ready_and_byte_for_byte_read_only(self):
        before = self.snapshot_tree(self.codex_home)
        result = self.run_recovery(source=self.source, stage=self.failed_stage, skills_root=self.skills_root, check_only=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['status'], 'staging_recovery_ready')
        self.assertRegex(payload['recovery_request_digest'], '^[0-9a-f]{64}$')
        self.assertEqual(payload['entry_count'], len(self.stage_entries))
        self.assertTrue(payload['target_absent'])
        self.assertTrue(payload['install_state_absent'])
        self.assertNotIn('source_entries', payload)
        self.assertNotIn('stage_entries', payload)
        self.assertFalse(self.archive_root.exists())
        self.assertTrue(self.failed_stage.is_dir())
        self.assertEqual(self.snapshot_tree(self.codex_home), before)

    def test_recovery_requires_exactly_one_execution_mode(self):
        common = dict(source=self.source, stage=self.failed_stage, skills_root=self.skills_root)
        missing = self.run_recovery(**common)
        both = self.run_recovery(**common, check_only=True, confirmation='0' * 64)
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(both.returncode, 2)
        self.assertFalse(self.archive_root.exists())
        self.assertTrue(self.failed_stage.is_dir())

    def test_recovery_request_has_exact_stable_canonical_schema(self):
        first = self.build_request()
        second = self.build_request()
        self.assertEqual(set(first), INSTALLER.RECOVERY_REQUEST_KEYS)
        self.assertEqual(first, second)
        self.assertFalse(any((key.endswith('_at_utc') for key in first)))
        self.assertNotIn('timestamp', first)
        digest = INSTALLER.recovery_request_digest(first)
        self.assertRegex(digest, '^[0-9a-f]{64}$')
        self.assertEqual(digest, INSTALLER.recovery_request_digest(second))
        self.assertEqual(first['runtime_layout'], {'valid': True, 'expected_entries': sorted(self.anchors['runtime_entries'])})

    def test_recovery_rejects_wrong_stage_basename(self):
        wrong_stage = self.skills_root / '.wrong-stage'
        shutil.copytree(self.failed_stage, wrong_stage)
        self.assert_not_ready(lambda: self.build_request(stage=wrong_stage))

    def test_check_only_reports_not_ready_as_error(self):
        wrong_stage = self.skills_root / '.wrong-stage'
        shutil.copytree(self.failed_stage, wrong_stage)
        result = self.run_recovery(source=self.source, stage=wrong_stage, skills_root=self.skills_root, check_only=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['reason'], 'staging_recovery_not_ready')
        self.assertTrue(self.failed_stage.is_dir())
        self.assertFalse(self.archive_root.exists())

    def test_recovery_rejects_stage_outside_direct_child_boundary(self):
        nested_parent = self.skills_root / 'nested'
        nested_parent.mkdir()
        nested_stage = nested_parent / self.STAGE_BASENAME
        shutil.copytree(self.failed_stage, nested_stage)
        self.assert_not_ready(lambda: self.build_request(stage=nested_stage))

    def test_recovery_rejects_symlink_stage(self):
        backing = self.tempdir / 'failed-stage-backing'
        self.failed_stage.rename(backing)
        os.symlink(backing, self.failed_stage)
        self.assert_not_ready(self.build_request)

    def test_recovery_rejects_every_frozen_stage_identity_drift(self):
        real_directory_identity = INSTALLER.directory_identity
        stage_absolute = Path(os.path.abspath(self.failed_stage))
        mutations = {'mode': lambda value: value ^ 1, 'device': lambda value: value + 1, 'inode': lambda value: value + 1, 'size': lambda value: value + 1, 'nlink': lambda value: value + 1, 'mtime_ns': lambda value: value + 1}
        for field, mutate in mutations.items():
            with self.subTest(field=field):

                def identity_with_drift(path, reason, selected=field, change=mutate):
                    value = real_directory_identity(path, reason)
                    if Path(os.path.abspath(path)) == stage_absolute:
                        value = dict(value)
                        value[selected] = change(value[selected])
                    return value
                with mock.patch.object(INSTALLER, 'directory_identity', side_effect=identity_with_drift):
                    self.assert_not_ready(self.build_request)

    def test_recovery_rejects_layout_mismatch(self):
        changed = copy.deepcopy(self.stage_entries)
        changed.pop('references/safety-gates.md')
        self._assert_stage_scan_override_is_rejected(changed)

    def test_recovery_rejects_content_mismatch(self):
        changed = copy.deepcopy(self.stage_entries)
        changed['SKILL.md']['sha256'] = '0' * 64
        self._assert_stage_scan_override_is_rejected(changed)

    def test_recovery_rejects_non_mode_type_difference(self):
        changed = copy.deepcopy(self.stage_entries)
        changed['SKILL.md'] = {'type': 'directory', 'mode': 511}
        self._assert_stage_scan_override_is_rejected(changed)

    def _assert_stage_scan_override_is_rejected(self, changed):
        real_scan_tree = INSTALLER.scan_tree
        stage_absolute = Path(os.path.abspath(self.failed_stage))

        def scan_with_changed_stage(root, *args, **kwargs):
            if Path(os.path.abspath(root)) == stage_absolute:
                return copy.deepcopy(changed)
            return real_scan_tree(root, *args, **kwargs)
        with mock.patch.object(INSTALLER, 'scan_tree', side_effect=scan_with_changed_stage):
            self.assert_not_ready(self.build_request)

    def test_recovery_rejects_unstable_stage_scan(self):
        real_scan_tree = INSTALLER.scan_tree
        stage_absolute = Path(os.path.abspath(self.failed_stage))
        changed = copy.deepcopy(self.stage_entries)
        changed['SKILL.md']['sha256'] = 'f' * 64
        stage_calls = 0

        def unstable_scan(root, *args, **kwargs):
            nonlocal stage_calls
            if Path(os.path.abspath(root)) == stage_absolute:
                stage_calls += 1
                return copy.deepcopy(self.stage_entries if stage_calls == 1 else changed)
            return real_scan_tree(root, *args, **kwargs)
        with mock.patch.object(INSTALLER, 'scan_tree', side_effect=unstable_scan):
            self.assert_not_ready(self.build_request)

    def test_recovery_rejects_unstable_source_scan(self):
        real_scan_tree = INSTALLER.scan_tree
        source_absolute = Path(os.path.abspath(self.source))
        changed = copy.deepcopy(self.source_entries)
        changed['SKILL.md']['sha256'] = 'f' * 64
        source_calls = 0

        def unstable_scan(root, *args, **kwargs):
            nonlocal source_calls
            if Path(os.path.abspath(root)) == source_absolute:
                source_calls += 1
                return copy.deepcopy(self.source_entries if source_calls == 1 else changed)
            return real_scan_tree(root, *args, **kwargs)
        with mock.patch.object(INSTALLER, 'scan_tree', side_effect=unstable_scan):
            self.assert_not_ready(self.build_request)

    def test_recovery_rejects_active_target(self):
        self.target.mkdir()
        self.assert_not_ready(self.build_request)

    def test_recovery_rejects_active_install_state(self):
        self.state_dir.mkdir()
        self.assert_not_ready(self.build_request)

    def test_recovery_rejects_symlinked_codex_home(self):
        alias = self.tempdir / 'codex-home-alias'
        os.symlink(self.codex_home, alias)
        with mock.patch.dict(os.environ, {'CODEX_HOME': str(alias)}):
            self.assert_not_ready(lambda: INSTALLER.build_staging_recovery_request(self.source, self.failed_stage, self.skills_root, anchors=self.anchors))

    def test_recovery_rejects_skills_root_outside_codex_home(self):
        other_root = self.tempdir / 'other-skills'
        other_root.mkdir()
        self.assert_not_ready(lambda: self.build_request(skills_root=other_root))

    def test_recovery_rejects_symlinked_archive_root(self):
        backing = self.tempdir / 'archive-backing'
        backing.mkdir()
        os.symlink(backing, self.archive_root)
        self.assert_not_ready(self.build_request)

    def test_recovery_accepts_safe_existing_archive_root(self):
        self.archive_root.mkdir()
        request = self.build_request()
        self.assertEqual(request['archive_root_condition'], {'status': 'existing', 'identity': INSTALLER.directory_identity(self.archive_root, 'unsafe_rollback_root')})

    def test_recovery_rejects_neighbor_scan_failure(self):
        with mock.patch.object(INSTALLER, 'scan_neighbor_roots', side_effect=OSError('simulated neighbor scan failure'), create=True):
            self.assert_not_ready(self.build_request)

class StagingRecoveryArchiveTests(SyntheticRecoveryFixture, unittest.TestCase):
    UUID_HEX = 'a' * 32

    def request_digest(self):
        return INSTALLER.recovery_request_digest(self.build_request())

    def run_confirm(self, digest):
        return self.run_recovery(source=self.source, stage=self.failed_stage, skills_root=self.skills_root, confirmation=digest)

    def result_payload(self, result):
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def recovery_directory(self):
        return self.archive_root / self.UUID_HEX

    def archive_destination(self):
        return self.recovery_directory() / self.failed_stage.name

    def test_confirmation_mismatch_is_drift_before_any_write(self):
        before = self.snapshot_tree(self.codex_home)
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            result = self.run_confirm('0' * 64)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(payload['status'], 'drift')
        self.assertEqual(payload['reason'], 'staging_recovery_drift')
        self.assertEqual(self.snapshot_tree(self.codex_home), before)
        self.assertTrue(self.failed_stage.is_dir())
        self.assertFalse(self.archive_root.exists())

    def test_recovery_directory_collision_never_moves_stage(self):
        self.archive_root.mkdir()
        self.recovery_directory().mkdir()
        digest = self.request_digest()
        before = self.snapshot_tree(self.codex_home)
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'rename_noreplace') as move:
                result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['reason'], 'install_state_collision')
        move.assert_not_called()
        self.assertEqual(self.snapshot_tree(self.codex_home), before)
        self.assertTrue(self.failed_stage.is_dir())

    def test_prepared_manifest_collision_never_moves_stage(self):
        digest = self.request_digest()
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'write_json_exclusive', side_effect=INSTALLER.InstallError('install_state_collision')):
                with mock.patch.object(INSTALLER, 'rename_noreplace') as move:
                    result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['reason'], 'install_state_collision')
        move.assert_not_called()
        self.assertTrue(self.failed_stage.is_dir())

    def test_exclusive_json_write_honors_custom_collision_reason(self):
        path = self.tempdir / 'owned-manifest.json'
        path.write_text('owned\n', encoding='utf-8')
        with self.assertRaises(INSTALLER.InstallError) as raised:
            INSTALLER.write_json_exclusive(path, {'test': True}, 'prepared_manifest_collision')
        self.assertEqual(raised.exception.reason, 'prepared_manifest_collision')
        self.assertEqual(path.read_text(encoding='utf-8'), 'owned\n')

    def test_recovery_manifest_builders_reject_schema_drift(self):
        request = self.build_request()
        digest = INSTALLER.recovery_request_digest(request)
        prepared = INSTALLER.build_recovery_prepared_manifest(request, digest, self.archive_destination())
        changed_request = dict(request, unexpected=True)
        with self.assertRaises(INSTALLER.InstallError) as request_error:
            INSTALLER.build_recovery_prepared_manifest(changed_request, digest, self.archive_destination())
        self.assertEqual(request_error.exception.reason, 'recovery_manifest_invalid')
        changed_prepared = dict(prepared, unexpected=True)
        with self.assertRaises(INSTALLER.InstallError) as prepared_error:
            INSTALLER.build_recovery_final_manifest(changed_prepared, self.frozen_stage_identity, request['neighbor_digest'])
        self.assertEqual(prepared_error.exception.reason, 'recovery_manifest_invalid')

    def test_successful_archive_moves_once_and_writes_bound_manifests(self):
        digest = self.request_digest()
        old_identity = INSTALLER.directory_identity(self.failed_stage, 'unsafe_failed_stage')
        real_move = INSTALLER.rename_noreplace
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'rename_noreplace', wraps=real_move) as move:
                result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload['status'], 'staging_archived')
        self.assertTrue(payload['recoverable'])
        self.assertEqual(payload['archive_destination'], str(self.archive_destination()))
        self.assertEqual(payload['entry_count'], len(self.stage_entries))
        self.assertEqual(move.call_count, 1)
        self.assertFalse(self.failed_stage.exists())
        self.assertTrue(self.archive_destination().is_dir())
        archived_identity = INSTALLER.directory_identity(self.archive_destination(), 'unsafe_archived_stage')
        self.assertEqual((archived_identity['device'], archived_identity['inode']), (old_identity['device'], old_identity['inode']))
        self.assertEqual(INSTALLER.scan_tree(self.archive_destination()), self.stage_entries)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state_dir.exists())
        prepared_path = self.recovery_directory() / INSTALLER.RECOVERY_PREPARED_MANIFEST_NAME
        final_path = self.recovery_directory() / INSTALLER.RECOVERY_FINAL_MANIFEST_NAME
        prepared = json.loads(prepared_path.read_text(encoding='utf-8'))
        final = json.loads(final_path.read_text(encoding='utf-8'))
        self.assertEqual(set(prepared), INSTALLER.RECOVERY_PREPARED_MANIFEST_KEYS)
        self.assertEqual(set(final), INSTALLER.RECOVERY_FINAL_MANIFEST_KEYS)
        self.assertEqual(prepared['phase'], 'prepared')
        self.assertEqual(final['phase'], 'archived')
        self.assertEqual(prepared['recovery_request_digest'], digest)
        self.assertEqual(prepared['manifest_digest'], INSTALLER.canonical_digest(prepared))
        self.assertEqual(final['prepared_manifest_digest'], prepared['manifest_digest'])
        self.assertTrue(final['source_stage_absent'])
        self.assertEqual(final['post_neighbor_digest'], prepared['neighbor_digest'])
        self.assertEqual(final['manifest_digest'], INSTALLER.canonical_digest(final))
        self.assertEqual(payload['prepared_manifest_digest'], prepared['manifest_digest'])
        self.assertEqual(payload['manifest_digest'], final['manifest_digest'])

    def test_move_unknown_is_classified_once_without_retry_or_reverse(self):
        digest = self.request_digest()
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'rename_noreplace', side_effect=INSTALLER.InstallError('atomic_move_outcome_unknown', 4)) as move:
                result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(payload['status'], 'unknown')
        self.assertEqual(payload['reason'], 'staging_archive_outcome_unknown')
        self.assertEqual(move.call_count, 1)
        self.assertTrue(self.failed_stage.is_dir())
        self.assertTrue((self.recovery_directory() / INSTALLER.RECOVERY_PREPARED_MANIFEST_NAME).is_file())
        self.assertFalse(self.archive_destination().exists())

    def test_moved_tree_mismatch_preserves_payload_and_prepared(self):
        digest = self.request_digest()

        def move_then_corrupt(source, destination):
            os.rename(source, destination)
            (destination / 'SKILL.md').write_text('corrupt\n', encoding='utf-8')
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'rename_noreplace', side_effect=move_then_corrupt) as move:
                result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(payload['status'], 'drift')
        self.assertEqual(payload['reason'], 'archived_staging_mismatch')
        self.assertEqual(move.call_count, 1)
        self.assertFalse(self.failed_stage.exists())
        self.assertTrue(self.archive_destination().is_dir())
        self.assertTrue((self.recovery_directory() / INSTALLER.RECOVERY_PREPARED_MANIFEST_NAME).is_file())
        self.assertFalse((self.recovery_directory() / INSTALLER.RECOVERY_FINAL_MANIFEST_NAME).exists())

    def test_post_move_identity_uncertainty_preserves_evidence(self):
        digest = self.request_digest()
        real_scan_tree = INSTALLER.scan_tree

        def scan_with_archived_failure(root, *args, **kwargs):
            if Path(os.path.abspath(root)) == self.archive_destination():
                raise INSTALLER.InstallError('unsafe_archived_stage')
            return real_scan_tree(root, *args, **kwargs)
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'scan_tree', side_effect=scan_with_archived_failure):
                result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(payload['status'], 'unknown')
        self.assertEqual(payload['reason'], 'staging_archive_outcome_unknown')
        self.assertFalse(self.failed_stage.exists())
        self.assertTrue(self.archive_destination().is_dir())
        self.assertTrue((self.recovery_directory() / INSTALLER.RECOVERY_PREPARED_MANIFEST_NAME).is_file())

    def test_final_manifest_failure_preserves_payload_and_prepared(self):
        digest = self.request_digest()
        real_write = INSTALLER.write_json_exclusive

        def fail_final(path, value, collision_reason='install_state_collision'):
            if path.name == INSTALLER.RECOVERY_FINAL_MANIFEST_NAME:
                raise OSError('simulated final write failure')
            return real_write(path, value, collision_reason)
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'write_json_exclusive', side_effect=fail_final):
                with mock.patch.object(INSTALLER, 'rename_noreplace', wraps=INSTALLER.rename_noreplace) as move:
                    result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(payload['status'], 'unknown')
        self.assertEqual(payload['reason'], 'recovery_manifest_incomplete')
        self.assertEqual(move.call_count, 1)
        self.assertFalse(self.failed_stage.exists())
        self.assertTrue(self.archive_destination().is_dir())
        self.assertTrue((self.recovery_directory() / INSTALLER.RECOVERY_PREPARED_MANIFEST_NAME).is_file())
        self.assertFalse((self.recovery_directory() / INSTALLER.RECOVERY_FINAL_MANIFEST_NAME).exists())

    def _assert_post_move_reappearance_is_unknown(self, kind):
        digest = self.request_digest()

        def move_then_reappear(source, destination):
            os.rename(source, destination)
            if kind == 'target':
                self.target.mkdir()
            elif kind == 'state':
                self.state_dir.mkdir()
            else:
                (self.skills_root / 'concurrent-neighbor').mkdir()
        with mock.patch.object(INSTALLER.uuid, 'uuid4', return_value=mock.Mock(hex=self.UUID_HEX)):
            with mock.patch.object(INSTALLER, 'rename_noreplace', side_effect=move_then_reappear) as move:
                result = self.run_confirm(digest)
        payload = self.result_payload(result)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(payload['status'], 'unknown')
        self.assertEqual(payload['reason'], 'staging_archive_outcome_unknown')
        self.assertEqual(move.call_count, 1)
        self.assertFalse(self.failed_stage.exists())
        self.assertTrue(self.archive_destination().is_dir())
        self.assertTrue((self.recovery_directory() / INSTALLER.RECOVERY_PREPARED_MANIFEST_NAME).is_file())

    def test_target_reappearance_after_move_is_unknown(self):
        self._assert_post_move_reappearance_is_unknown('target')

    def test_state_reappearance_after_move_is_unknown(self):
        self._assert_post_move_reappearance_is_unknown('state')

    def test_neighbor_reappearance_after_move_is_unknown(self):
        self._assert_post_move_reappearance_is_unknown('neighbor')
