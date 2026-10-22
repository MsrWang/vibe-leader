# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Exercise the real local-check CLI against disposable installer outputs."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import doctor


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "doctor.py"
NAME = "vibe-project-lead-zh"


@unittest.skipUnless(sys.platform == "linux", "doctor supports Linux/WSL only")
class DoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vibe-doctor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codex_root = self.root / "codex"
        self.skills_root = self.codex_root / "skills"
        self.skills_root.mkdir(parents=True)
        self.project = self.root / "project"
        self.project.mkdir()
        self.target = self.skills_root / NAME
        self.manifest = self.skills_root / f".{NAME}-install" / "install-manifest.json"
        self.source = self.root / "source" / NAME
        shutil.copytree(ROOT / "skill" / NAME, self.source)
        self.env = dict(os.environ, CODEX_HOME=str(self.codex_root))
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONSTARTUP", None)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def install(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "install_skill.py"),
             "install", "--source", str(self.source), "--skills-root", str(self.skills_root)],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "installed")

    def snapshot(self):
        # Access times may change on reads; contents, modes and modification times may not.
        result = {}
        for path in [self.root, *sorted(self.root.rglob("*"))]:
            info = path.lstat()
            content = (os.readlink(path) if path.is_symlink() else
                       hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            result[str(path.relative_to(self.root))] = (info.st_mode, info.st_mtime_ns, content)
        return result

    def run_cli(self, *args, env=None, script=SCRIPT, bytecode_disabled=True):
        before = self.snapshot()
        command = [sys.executable]
        if bytecode_disabled:
            command.append("-B")
        result = subprocess.run(command + [str(script), *map(str, args)],
                                cwd=self.root, env=self.env if env is None else env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(self.snapshot(), before, "doctor changed disposable input files")
        self.assertNotIn("Traceback", result.stderr)
        return result

    def report(self, *args, env=None):
        result = self.run_cli("--json", *args, env=env)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertNotIn(str(self.root), result.stdout)
        self.assertNotIn(str(ROOT), result.stdout)
        self.assertEqual(value["invocation"]["status"], "NOT_CHECKED")
        self.assertEqual(value["discovery"]["current_host_status"], "NOT_CHECKED")
        return result.returncode, value

    def response(self):
        return {"result": {"data": [{"cwd": str(self.project), "skills": [{
            "name": NAME, "path": str(self.target / "SKILL.md"),
            "enabled": True, "scope": "user", "description": "synthetic record",
        }], "errors": []}]}}

    def discovery_file(self, response):
        path = self.root / "discovery.json"
        path.write_text(json.dumps(response), encoding="utf-8")
        return path

    def test_healthy_install_separates_local_success_from_runtime_acceptance(self):
        self.install()
        code, value = self.report()
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "LOCAL_CHECKS_COMPLETE")
        self.assertEqual(value["installation"]["status"], "VERIFIED")
        self.assertEqual(value["installation"]["file_count"], 13)
        self.assertEqual(value["version"]["status"], "KNOWN_CONTENT")
        self.assertTrue(value["version"]["matches_3_0_3_payload"])
        self.assertEqual(value["version"]["release_provenance"], "NOT_CHECKED")
        self.assertEqual(value["version"]["source_comparison"], "MATCH")
        self.assertEqual(value["discovery"]["status"], "NOT_CHECKED")

    def test_text_cli_preserves_separate_statuses_and_next_action(self):
        self.install()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        for text in ("安装：[VERIFIED]", "版本内容：[KNOWN_CONTENT]",
                     "宿主发现：[NOT_CHECKED]", "实际调用：[NOT_CHECKED]", "下一步："):
            self.assertIn(text, result.stdout)
        self.assertEqual(result.stderr, "")

    def test_missing_home_does_not_guess_or_scan_default_location(self):
        self.install()
        environment = dict(self.env)
        environment.pop("CODEX_HOME")
        code, value = self.report(env=environment)
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["reason"], "codex_home_not_set")

    def test_other_skills_root_is_rejected_even_when_it_exists(self):
        other = self.root / "other-skills"
        other.mkdir()
        code, value = self.report("--skills-root", other)
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["reason"], "skills_root_mismatch")

    def test_missing_installation_is_not_global_absence(self):
        code, value = self.report()
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["status"], "NOT_FOUND")
        self.assertEqual(value["version"]["status"], "UNKNOWN")

    def test_orphaned_install_state_is_unknown_not_clean_absence(self):
        self.manifest.parent.mkdir()
        code, value = self.report()
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["reason"], "incomplete_installation")

    def test_changed_installed_content_is_drift(self):
        self.install()
        (self.target / "SKILL.md").write_text("changed", encoding="utf-8")
        code, value = self.report()
        self.assertEqual(code, 3)
        self.assertEqual(value["installation"]["status"], "DRIFT")
        self.assertEqual(value["installation"]["difference_count"], 1)
        self.assertEqual(value["version"]["status"], "UNKNOWN")

    def test_changed_installed_mode_is_drift(self):
        self.install()
        path = self.target / "SKILL.md"
        path.chmod(stat.S_IMODE(path.stat().st_mode) ^ stat.S_IXUSR)
        code, value = self.report()
        self.assertEqual(code, 3)
        self.assertEqual(value["installation"]["status"], "DRIFT")

    def test_symlink_install_target_is_not_verified(self):
        self.install()
        moved = self.root / "moved-target"
        self.target.rename(moved)
        self.target.symlink_to(moved, target_is_directory=True)
        code, value = self.report()
        self.assertEqual(code, 3)
        self.assertEqual(value["installation"]["status"], "DRIFT")

    def test_corrupt_or_missing_manifest_never_passes(self):
        self.install()
        self.manifest.write_text("{}", encoding="utf-8")
        code, value = self.report()
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["reason"], "manifest_digest_invalid")
        self.manifest.unlink()
        code, value = self.report()
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["reason"], "manifest_not_found")

    def test_valid_other_payload_is_not_misreported_as_damage_or_known_release(self):
        path = self.source / "SKILL.md"
        path.write_text(path.read_text() + "\nSynthetic alternate version.\n", encoding="utf-8")
        self.install()
        code, value = self.report()
        self.assertEqual(code, 0)
        self.assertEqual(value["installation"]["status"], "VERIFIED")
        self.assertEqual(value["version"]["status"], "UNKNOWN_CONTENT")
        self.assertEqual(value["version"]["source_comparison"], "DIFFERENT")

    def test_valid_discovery_is_only_a_snapshot_match(self):
        self.install()
        path = self.discovery_file(self.response())
        code, value = self.report("--discovery", path, "--project", self.project)
        self.assertEqual(code, 0)
        self.assertEqual(value["discovery"]["status"], "SNAPSHOT_MATCH")

    def test_discovery_identity_failures_remain_blocking(self):
        self.install()
        for condition, expected in (("missing", "MISSING"), ("disabled", "DISABLED"),
                                    ("duplicate", "DUPLICATE"), ("load", "LOAD_ERROR"),
                                    ("locator", "LOCATOR_CHANGED")):
            with self.subTest(condition=condition):
                response = self.response()
                data = response["result"]["data"][0]
                if condition == "missing":
                    data["skills"] = []
                elif condition == "disabled":
                    data["skills"][0]["enabled"] = False
                elif condition == "duplicate":
                    data["skills"].append(copy.deepcopy(data["skills"][0]))
                elif condition == "load":
                    data["errors"] = [{"path": str(self.target / "SKILL.md"), "message": "private error"}]
                else:
                    data["skills"][0]["path"] = str(self.source / "SKILL.md")
                path = self.discovery_file(response)
                code, value = self.report("--discovery", path, "--project", self.project)
                self.assertEqual(code, 3)
                self.assertEqual(value["discovery"]["status"], "SNAPSHOT_BLOCKED")
                self.assertEqual(value["discovery"]["reasons"], [expected])
                self.assertNotIn("private error", json.dumps(value))

    def test_unattributed_discovery_error_is_unknown(self):
        self.install()
        response = self.response()
        response["result"]["data"][0]["errors"] = [{"path": "unknown", "message": "private"}]
        path = self.discovery_file(response)
        code, value = self.report("--discovery", path, "--project", self.project)
        self.assertEqual(code, 2)
        self.assertEqual(value["discovery"]["status"], "UNKNOWN")
        self.assertEqual(value["discovery"]["unattributed_error_count"], 1)

    def test_wrong_project_or_malformed_record_is_unknown(self):
        self.install()
        wrong = self.response()
        wrong["result"]["data"][0]["cwd"] = str(self.root)
        invalid_enabled = self.response()
        invalid_enabled["result"]["data"][0]["skills"][0]["enabled"] = "true"
        for response in (wrong, invalid_enabled, [], {}, {"result": None}):
            with self.subTest(response=response):
                path = self.discovery_file(response)
                code, value = self.report("--discovery", path, "--project", self.project)
                self.assertEqual(code, 2)
                self.assertEqual(value["discovery"]["status"], "UNKNOWN")

    def test_invalid_encoding_oversize_and_symlink_record_are_rejected(self):
        self.install()
        path = self.root / "bad.json"
        for content in (b"\xff", b"{", b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(content)):
                path.write_bytes(content)
                code, value = self.report("--discovery", path, "--project", self.project)
                self.assertEqual(code, 2)
                self.assertEqual(value["discovery"]["status"], "UNKNOWN")
        path.unlink()
        path.symlink_to(self.discovery_file(self.response()))
        code, value = self.report("--discovery", path, "--project", self.project)
        self.assertEqual(code, 2)
        self.assertEqual(value["discovery"]["status"], "UNKNOWN")

    def test_discovery_cli_arguments_must_be_paired(self):
        for option in ("--discovery", "--project"):
            result = self.run_cli(option, self.project)
            self.assertEqual(result.returncode, 2)
            self.assertIn("必须同时提供", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_missing_source_returns_attention_instead_of_local_success(self):
        self.install()
        with patch.dict(os.environ, self.env), patch.object(doctor, "ROOT", self.root / "missing"):
            value, code = doctor.build_report()
        self.assertEqual(code, 2)
        self.assertEqual(value["installation"]["status"], "VERIFIED")
        self.assertEqual(value["version"]["source_comparison"], "UNKNOWN")
        self.assertEqual(value["status"], "NEEDS_ATTENTION")

    def test_unsupported_platform_does_not_use_linux_install_result(self):
        self.install()
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform), patch.object(doctor.sys, "platform", platform):
                value, code = doctor.build_report(self.skills_root)
            self.assertEqual(code, 2)
            self.assertEqual(value["environment"]["status"], "UNVERIFIED")
            self.assertEqual(value["installation"]["status"], "NOT_CHECKED")

    def test_running_without_B_does_not_create_source_bytecode(self):
        self.install()
        source_root = self.root / "tool-source"
        for relative in ("scripts/doctor.py", "scripts/install_skill.py", "workbench/skill_inventory.py"):
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        shutil.copytree(self.source, source_root / "skill" / NAME)
        environment = dict(self.env)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        result = self.run_cli("--json", env=environment,
                              script=source_root / "scripts" / "doctor.py", bytecode_disabled=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["installation"]["status"], "VERIFIED")
        self.assertEqual(list(source_root.rglob("__pycache__")), [])

    def test_real_cli_does_not_attempt_writes_network_or_subprocesses(self):
        self.install()
        path = self.discovery_file(self.response())
        guard = self.root / "guard.py"
        guard.write_text('''import os, runpy, sys
def audit(event, args):
    if event == "open":
        _, mode, flags = args
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise RuntimeError("unexpected write")
    if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.chmod", "os.link", "os.symlink", "os.truncate", "os.utime", "os.system", "subprocess.Popen", "socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise RuntimeError("unexpected side effect: " + event)
sys.addaudithook(audit)
script = sys.argv.pop(1)
runpy.run_path(script, run_name="__main__")
''', encoding="utf-8")
        result = self.run_cli(SCRIPT, "--json", "--discovery", path, "--project", self.project,
                              script=guard)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["discovery"]["status"], "SNAPSHOT_MATCH")


if __name__ == "__main__":
    unittest.main()
