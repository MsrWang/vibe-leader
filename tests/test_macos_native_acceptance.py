# SPDX-License-Identifier: MPL-2.0
"""Native Apple Silicon acceptance checks for the macOS release lifecycle."""

import importlib.util
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = ROOT / "scripts" / "install_skill.py"
RELEASE_SCRIPT = ROOT / "scripts" / "release_archive.py"
SKILL_NAME = "vibe-project-lead-zh"
BASELINE_COMMIT = "d0e17544584fb6c68254c49b87f4275fa19a71f4"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = load_module("mac_native_installer", INSTALLER_SCRIPT)


@unittest.skipUnless(
    sys.platform == "darwin" and platform.machine() == "arm64",
    "Darwin native evidence only",
)
class MacNativeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vibe-macos-native-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def run_json(self, script: Path, *arguments: object, codex_home: Path | None = None):
        environment = os.environ.copy()
        environment.pop("CODEX_HOME", None)
        if codex_home is not None:
            self.assertTrue(codex_home.is_relative_to(self.root))
            environment["CODEX_HOME"] = str(codex_home)
        result = subprocess.run(
            [sys.executable, "-B", str(script), *map(str, arguments)],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result, json.loads(result.stdout)

    def git(self, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), *arguments],
            stderr=subprocess.PIPE,
            text=True,
        ).strip()

    def test_native_statfs_and_exchange_probe(self):
        skills_root = self.root / "skills"
        skills_root.mkdir()
        observed = INSTALLER._darwin_statfs(skills_root)
        self.assertEqual(observed["device"], os.lstat(skills_root).st_dev)
        self.assertTrue(Path(observed["mount_target"]).is_absolute())
        self.assertTrue(observed["filesystem_type"])
        capability, evidence = INSTALLER.probe_switch_capability(skills_root)
        self.assertEqual(capability, "EXCHANGE_SUPPORTED")
        self.assertEqual(evidence["exchange_result"], "VERIFIED")
        self.assertTrue(evidence["postconditions_verified"])
        self.assertTrue(evidence["restored"])
        self.assertEqual(list(skills_root.iterdir()), [])

    def test_release_archive_build_verify_extract_is_native(self):
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), "")
        commit = self.git("rev-parse", "HEAD^{commit}")
        first, second = self.root / "assets-a", self.root / "assets-b"
        first.mkdir()
        second.mkdir()
        for destination in (first, second):
            result, payload = self.run_json(
                RELEASE_SCRIPT,
                "build", "--repo", ROOT,
                "--commit", commit, "--output-dir", destination,
            )
            self.assertEqual(result.returncode, 0, payload)
        for name in (
            "Vibe-Leader-3.1.0-GitHub.zip",
            "release_archive.py",
            "SHA256SUMS.txt",
        ):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        downloaded = first / "release_archive.py"
        archive = first / "Vibe-Leader-3.1.0-GitHub.zip"
        checksums = first / "SHA256SUMS.txt"
        result, payload = self.run_json(
            downloaded,
            "verify", "--archive", archive,
            "--checksums", checksums, "--expected-commit", commit,
        )
        self.assertEqual(result.returncode, 0, payload)
        extracted = self.root / "extracted"
        result, payload = self.run_json(
            downloaded,
            "extract", "--archive", archive,
            "--checksums", checksums, "--expected-commit", commit,
            "--destination", extracted,
        )
        self.assertEqual(result.returncode, 0, payload)
        skill = extracted / "Vibe-Leader-3.1.0" / "skill" / SKILL_NAME
        self.assertEqual(len([path for path in skill.rglob("*") if path.is_file()]), 13)

    def test_fresh_install_upgrade_and_restore_are_native(self):
        current_source = ROOT / "skill" / SKILL_NAME
        fresh_home = self.root / "fresh-codex-home"
        fresh_skills = fresh_home / "skills"
        fresh_skills.mkdir(parents=True)
        self.assertTrue(fresh_home.is_relative_to(self.root))
        result, payload = self.run_json(
            INSTALLER_SCRIPT,
            "install", "--source", current_source, "--skills-root", fresh_skills,
            codex_home=fresh_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["status"], "installed")
        fresh_target = fresh_skills / SKILL_NAME
        fresh_manifest = fresh_skills / f".{SKILL_NAME}-install" / INSTALLER.MANIFEST_NAME
        result, payload = self.run_json(
            INSTALLER_SCRIPT,
            "verify", "--target", fresh_target, "--manifest", fresh_manifest,
            codex_home=fresh_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(len([path for path in fresh_target.rglob("*") if path.is_file()]), 13)

        baseline_repo = self.root / "baseline-repo"
        self.assertTrue(baseline_repo.is_relative_to(self.root))
        subprocess.run(
            ["git", "clone", "--local", "--no-hardlinks", str(ROOT), str(baseline_repo)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        subprocess.run(
            ["git", "-C", str(baseline_repo), "checkout", "--detach", BASELINE_COMMIT],
            check=True, capture_output=True, text=True, timeout=180,
        )
        baseline_source = baseline_repo / "skill" / SKILL_NAME
        upgrade_home = self.root / "upgrade-codex-home"
        upgrade_skills = upgrade_home / "skills"
        upgrade_skills.mkdir(parents=True)
        self.assertTrue(upgrade_home.is_relative_to(self.root))
        target = upgrade_skills / SKILL_NAME
        manifest = upgrade_skills / f".{SKILL_NAME}-install" / INSTALLER.MANIFEST_NAME
        result, payload = self.run_json(
            INSTALLER_SCRIPT,
            "install", "--source", baseline_source, "--skills-root", upgrade_skills,
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["status"], "installed")
        result, payload = self.run_json(
            INSTALLER_SCRIPT,
            "verify", "--target", target, "--manifest", manifest,
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        original_manifest = manifest.read_bytes()
        original_digest = INSTALLER.canonical_tree_digest(INSTALLER.scan_tree(target))

        request = self.root / "upgrade-request.json"
        result, payload = self.run_json(
            INSTALLER_SCRIPT, "prepare-upgrade",
            "--source", current_source, "--target", target, "--manifest", manifest,
            "--approval-id", "MAC-NATIVE-CI-UPGRADE", "--output", request,
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        request_payload = json.loads(request.read_text(encoding="utf-8"))
        result, payload = self.run_json(
            INSTALLER_SCRIPT, "upgrade", "--request", request,
            "--confirm-request", request_payload["request_digest"],
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["status"], "upgrade_verified")
        receipt = Path(payload["receipt"])
        self.assertTrue(receipt.is_relative_to(self.root))
        receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        journal = Path(receipt_payload["journal"])
        self.assertTrue(journal.is_relative_to(self.root))
        result, inspection = self.run_json(
            INSTALLER_SCRIPT, "inspect-upgrade", "--journal", journal,
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, inspection)
        self.assertEqual(inspection["inspection"]["classification"], "VERIFIED")
        result, payload = self.run_json(
            INSTALLER_SCRIPT, "verify", "--target", target, "--manifest", manifest,
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["status"], "verified")

        result, restored = self.run_json(
            INSTALLER_SCRIPT, "restore-version", "--receipt", receipt,
            "--approval-id", "MAC-NATIVE-CI-RESTORE",
            "--confirm", receipt_payload["restore_confirmation_digest"],
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, restored)
        self.assertEqual(restored["status"], "restore_verified")
        restore_receipt = Path(restored["receipt"])
        self.assertTrue(restore_receipt.is_relative_to(self.root))
        result, payload = self.run_json(
            INSTALLER_SCRIPT, "verify", "--target", target, "--manifest", manifest,
            codex_home=upgrade_home,
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(manifest.read_bytes(), original_manifest)
        self.assertEqual(
            INSTALLER.canonical_tree_digest(INSTALLER.scan_tree(target)),
            original_digest,
        )


if __name__ == "__main__":
    unittest.main()
