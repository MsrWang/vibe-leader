# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import evaluation_surface as SURFACE

from tests.test_project_identity import (
    malformed_alias_cases, malformed_runtime_surfaces, same_object_alias_fixture,
)

try:
    from workbench import project_freshness as FRESHNESS
except ImportError:
    FRESHNESS = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workbench" / "project_freshness.py"


def identity_fixture():
    return {
        "schema_version": 2,
        "status": "bound",
        "reason": None,
        "binding_kind": "GIT_WORKTREE",
        "write_eligibility": "ELIGIBLE",
        "requested_cwd": "/project",
        "pwd": "/project",
        "realpath": "/project",
        "is_git": True,
        "logical_path": "/project",
        "physical_path": "/project",
        "path_input_kind": "WSL_POSIX_ABSOLUTE",
        "resolution_traits": [],
        "filesystem_identity": {
            "st_dev": 1,
            "st_ino": 2,
            "object_type": "directory",
        },
        "git_top_level": "/project",
        "git_dir": "/repo/.git/worktrees/project",
        "git_common_dir": "/repo/.git",
        "worktree_id": "worktree-1",
        "branch": "main",
        "head": "a" * 40,
        "dirty": True,
        "dirty_fingerprint": "b" * 64,
        "dirty_fingerprint_schema": 3,
        "fingerprint_complete": True,
        "fingerprint_applicability": "REQUIRED",
        "fingerprint_reason": None,
        "remotes": [],
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
        "aliases": [],
        "captured_at_utc": "2026-08-09T00:00:00Z",
        "bound_at_utc": "2026-08-09T00:00:00Z",
    }


def runtime_fixture():
    """Caller-supplied synthetic evidence; never a capture of the installed CLI."""
    return {"surface": "wsl", "codex_version": "0.147.0", "core_sha256": "c" * 64}


def malformed_runtime_evidence():
    valid = runtime_fixture()
    cases = [None, [], {}, {"platform": "linux", "is_wsl": True, "wsl_distro_name": None}]
    for field in valid:
        missing = dict(valid)
        del missing[field]
        cases.append(missing)
    for field, values in (
        ("surface", (None, [], "", "UNKNOWN", "windows")),
        ("codex_version", (None, [], 147, "", " ", "UNKNOWN", "0.147.0\n")),
        ("core_sha256", (None, [], 12, "", "UNKNOWN", "g" * 64, "c" * 63)),
    ):
        cases.extend(dict(valid, **{field: value}) for value in values)
    cases.extend((dict(valid, applicability="OPTIONAL"), dict(valid, route_state="FRESH")))
    return cases


def inventory_fixture(applicability="REQUIRED"):
    return {
        "applicability": applicability,
        "supervisor_identity": "vibe-project-lead-zh@locator",
        "supervisor_state": "ENABLED_UNIQUE",
        "supervisor_identity_source": "ISOLATED_DISCOVERY",
        "route_state": "FRESH",
        "inventory_sha256": "d" * 64,
        "generated_at_utc": "2026-08-09T00:00:00Z",
        "warning_classes": [],
    }


def status_fixture(applicability="REQUIRED"):
    return {
        "applicability": applicability,
        "path": "docs/PROJECT_STATUS.md",
        "content_sha256": "e" * 64,
        "observed_head": "a" * 40,
    }


class RuntimeCaptureImportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(
            dir="/tmp", prefix="vibe-project-lead-eval.runtime-bridge.",
        )
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.binary = self.root / "synthetic-codex"
        self.binary.write_bytes(b"synthetic program bytes\n")
        self.identity = identity_fixture()
        fixtures = ROOT / "tests/fixtures/evaluation_surface"

        def runner(arguments, **kwargs):
            self.assertFalse(kwargs["shell"])
            if arguments == (str(self.binary), "--version"):
                stdout = b"fake-codex 1.0.0\n"
            elif arguments == (str(self.binary), "features", "list"):
                stdout = (fixtures / "features-list.txt").read_bytes()
            else:
                self.assertEqual(arguments[:5], (
                    str(self.binary), "app-server", "generate-json-schema", "--experimental", "--out",
                ))
                shutil.copytree(fixtures / "schema", arguments[5])
                stdout = b""
            return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr=b"")

        # Use the real existing capture pipeline, replacing only its process I/O.
        self.capture = SURFACE.capture_runtime_contract(
            self.binary, self.root / "runtime-capture-source", runner=runner,
        )

    def convert(self, capture=None, binary=None, identity=None):
        function = getattr(FRESHNESS, "runtime_evidence_from_capture", None)
        self.assertTrue(callable(function), "runtime capture import is not implemented")
        return function(
            self.capture if capture is None else capture,
            codex_bin=self.binary if binary is None else binary,
            identity=self.identity if identity is None else identity,
        )

    def test_existing_producer_feeds_runtime_without_executing_program_again(self):
        before = copy.deepcopy((self.capture, self.identity))
        with mock.patch("subprocess.Popen", side_effect=AssertionError("program execution")):
            runtime = self.convert()
        self.assertEqual(runtime, {
            "surface": "wsl", "codex_version": "fake-codex 1.0.0",
            "core_sha256": hashlib.sha256(b"synthetic program bytes\n").hexdigest(),
        })
        result = FRESHNESS.build_freshness_envelope(
            {"project": self.identity, "runtime": runtime}, self.identity, runtime=runtime,
        )
        self.assertEqual(result["freshness"]["state"], "FRESH")
        self.assertFalse(result["freshness"]["write_authorized"])
        self.assertEqual((self.capture, self.identity), before)

    def test_capture_tampering_and_inventory_shape_are_rejected(self):
        invalid = [[], {}, {"schema_version": 2, "runtime": runtime_fixture()}]
        for key, value in (("schema_version", True), ("schema_version", 2),
                           ("codex_sha256", "2" * 64), ("version", "forged version"),
                           ("version_sha256", "3" * 64), ("extra", "not allowed")):
            invalid.append(dict(self.capture, **{key: value}))
        for index, key, value in (
            (0, "exit_code", False), (0, "exit_code", 1),
            (0, "argv", [str(self.binary), "features", "list"]),
            (0, "stdout_sha256", "4" * 64), (0, "stderr_sha256", "5" * 64),
            (1, "argv", [str(self.binary), "--version"]),
        ):
            changed = copy.deepcopy(self.capture)
            changed["commands"][index][key] = value
            invalid.append(changed)
        for capture in invalid:
            with self.subTest(capture=capture):
                with self.assertRaises(FRESHNESS.FreshnessInputError):
                    self.convert(capture=capture)

    def test_changed_program_symlink_and_wrong_program_path_are_rejected(self):
        alternate = self.root / "different-program"
        alternate.write_bytes(self.binary.read_bytes())
        alias = self.root / "program-alias"
        alias.symlink_to(self.binary)
        for binary in (alternate, alias):
            with self.subTest(binary=binary):
                with self.assertRaises(FRESHNESS.FreshnessInputError):
                    self.convert(binary=binary)
        claimed_alias = copy.deepcopy(self.capture)
        for command in claimed_alias["commands"]:
            command["argv"][0] = str(alias)
        with self.assertRaises(FRESHNESS.FreshnessInputError):
            self.convert(capture=claimed_alias, binary=alias)
        self.binary.write_bytes(b"changed program bytes\n")
        with self.assertRaises(FRESHNESS.FreshnessInputError):
            self.convert()

    def test_incomplete_project_identity_cannot_supply_capture_surface(self):
        for surface in malformed_runtime_surfaces():
            identity = copy.deepcopy(self.identity)
            identity["runtime_surface"] = surface
            with self.subTest(surface=surface):
                with self.assertRaises(FRESHNESS.FreshnessInputError):
                    self.convert(identity=identity)
        identity = dict(self.identity, write_eligibility="READ_ONLY")
        with self.assertRaises(FRESHNESS.FreshnessInputError):
            self.convert(identity=identity)

    def test_unhashable_identity_eligibility_is_rejected_without_a_type_error(self):
        for eligibility in ([], {}):
            with self.subTest(eligibility=eligibility):
                try:
                    with self.assertRaises(FRESHNESS.FreshnessInputError):
                        self.convert(identity=dict(self.identity, write_eligibility=eligibility))
                except TypeError:
                    self.fail("malformed identity must produce a bounded input error")

    def test_public_capture_command_with_fake_cli_feeds_import_and_context(self):
        from tests.test_delegation_contract import valid_brief, result_fixture, reception_context_fixture
        from workbench import delegation_contract
        fake_cli = ROOT / "tests/fixtures/evaluation_surface/fake_codex.py"
        capture_root = self.root / "runtime-capture-process"
        captured = subprocess.run(
            [sys.executable, ROOT / "workbench/evaluation_surface.py", "inspect", "--capture-runtime",
             "--eval-root", self.root, "--codex-bin", fake_cli, "--output-root", capture_root],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(captured.returncode, 0, captured.stderr)
        identity_path = self.root / "public-identity.json"
        identity_path.write_text(json.dumps(self.identity), encoding="utf-8")
        imported = subprocess.run(
            [sys.executable, SCRIPT, "import-runtime", "--capture", capture_root / "runtime-contract.json",
             "--codex-bin", fake_cli, "--identity", identity_path],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        runtime = json.loads(imported.stdout)
        self.assertEqual(runtime["codex_version"], "fake-codex 1.0.0")
        brief = valid_brief()
        result = result_fixture(brief)
        main = brief["baseline"]["main_thread_identity"]
        context = delegation_contract.build_runtime_context(
            brief, baseline_runtime=runtime, current_runtime=runtime, current_main_identity=main,
        )
        evaluated = delegation_contract.evaluate_delegation_result(
            brief, result, current_main_identity=main,
            current_authority_identity=result["return_baseline"], runtime_context=context,
            reception_context=reception_context_fixture(brief, result),
        )
        self.assertEqual(evaluated["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW")
        self.assertFalse(evaluated["write_authorized"])

    def test_import_cli_feeds_existing_freshness_cli_and_keeps_output_exclusive(self):
        capture_path, identity_path = self.root / "capture.json", self.root / "identity.json"
        capture_path.write_text(json.dumps(self.capture), encoding="utf-8")
        identity_path.write_text(json.dumps(self.identity), encoding="utf-8")
        output = self.root / "runtime.json"
        argv = [sys.executable, SCRIPT, "import-runtime", "--capture", capture_path,
                "--codex-bin", self.binary, "--identity", identity_path, "--output", output]
        imported = subprocess.run(argv, text=True, capture_output=True, check=False)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        runtime = json.loads(imported.stdout)
        self.assertEqual(json.loads(output.read_text()), runtime)
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"project": self.identity, "runtime": runtime}), encoding="utf-8")
        compared = subprocess.run(
            [sys.executable, SCRIPT, "--baseline", baseline, "--current", identity_path,
             "--runtime", output], text=True, capture_output=True, check=False,
        )
        self.assertEqual(compared.returncode, 0, compared.stderr)
        self.assertFalse(json.loads(compared.stdout)["freshness"]["write_authorized"])
        self.assertEqual(subprocess.run(argv, capture_output=True, check=False).returncode, 2)
        self.assertEqual(json.loads(output.read_text()), runtime)

        for path in (capture_path, identity_path):
            with self.subTest(duplicate_input=path.name):
                original = path.read_text(encoding="utf-8")
                self.assertIn('"schema_version":', original)
                path.write_text('{"schema_version": 999, ' + original[1:], encoding="utf-8")
                rejected = subprocess.run(argv[:-2], text=True, capture_output=True, check=False)
                path.write_text(original, encoding="utf-8")
                self.assertEqual(rejected.returncode, 2, rejected.stdout)
                self.assertEqual(rejected.stdout, "")
                self.assertEqual(json.loads(output.read_text()), runtime)


class FreshnessTests(unittest.TestCase):
    def test_malformed_runtime_surface_never_becomes_fresh(self):
        for surface in malformed_runtime_surfaces():
            for position in ("baseline", "current", "both"):
                with self.subTest(surface=surface, position=position):
                    baseline, current = identity_fixture(), identity_fixture()
                    if position in {"baseline", "both"}:
                        baseline["runtime_surface"] = copy.deepcopy(surface)
                    if position in {"current", "both"}:
                        current["runtime_surface"] = copy.deepcopy(surface)
                    comparison = self.module().compare_identity(baseline, current)
                    self.assertEqual(comparison["state"], "UNKNOWN")

    def test_program_evidence_is_required_even_when_project_identity_is_fresh(self):
        identity = identity_fixture()
        self.assertEqual(self.module().compare_identity(identity, identity)["state"], "FRESH")
        result = self.module().build_freshness_envelope(identity, identity)
        self.assertEqual(result["freshness"]["state"], "UNKNOWN")
        self.assertFalse(result["freshness"]["write_precondition_satisfied"])
        self.assertFalse(result["freshness"]["write_authorized"])

    def runtime_envelope(self, baseline, runtime):
        try:
            return self.module().build_freshness_envelope(
                baseline, identity_fixture(), runtime=runtime,
            )
        except TypeError as error:
            self.fail(f"Runtime evidence must be evaluated, not raise: {error}")

    def test_version_and_program_fingerprint_drift_are_stale(self):
        baseline = {"project": identity_fixture(), "runtime": runtime_fixture()}
        for field, value in (("codex_version", "0.148.0"), ("core_sha256", "2" * 64)):
            with self.subTest(field=field):
                current = dict(runtime_fixture(), **{field: value})
                before = copy.deepcopy((baseline, current))
                result = self.runtime_envelope(baseline, current)
                self.assertEqual(result["freshness"]["state"], "STALE")
                self.assertIn(f"runtime.{field}", result["freshness"]["changed_fields"])
                self.assertFalse(result["freshness"]["write_precondition_satisfied"])
                self.assertFalse(result["freshness"]["write_authorized"])
                self.assertEqual((baseline, current), before)

    def test_invalid_or_downgraded_program_evidence_is_unknown(self):
        for invalid in malformed_runtime_evidence():
            for position in ("baseline", "current", "both"):
                with self.subTest(invalid=invalid, position=position):
                    baseline = {"project": identity_fixture(), "runtime": runtime_fixture()}
                    current = runtime_fixture()
                    if position in {"baseline", "both"}:
                        baseline["runtime"] = copy.deepcopy(invalid)
                    if position in {"current", "both"}:
                        current = copy.deepcopy(invalid)
                    result = self.runtime_envelope(baseline, current)
                    self.assertEqual(result["freshness"]["state"], "UNKNOWN")
                    self.assertFalse(result["freshness"]["write_precondition_satisfied"])
                    self.assertFalse(result["freshness"]["write_authorized"])

    def test_runtime_envelope_round_trip_retains_program_evidence(self):
        baseline = {"project": identity_fixture(), "runtime": runtime_fixture()}
        first = self.runtime_envelope(baseline, runtime_fixture())
        second = self.runtime_envelope(first, runtime_fixture())
        self.assertEqual(first["runtime"], runtime_fixture())
        self.assertEqual(second["freshness"]["state"], "FRESH")
        self.assertTrue(second["freshness"]["write_precondition_satisfied"])
        self.assertFalse(second["freshness"]["write_authorized"])

    def test_real_surface_shapes_are_fresh_and_platform_changes_are_stale(self):
        for platform, is_wsl, distro in (
            ("linux", True, "Ubuntu"), ("linux", True, None),
            ("linux", False, None), ("windows", False, None), ("darwin", False, None),
        ):
            with self.subTest(platform=platform, is_wsl=is_wsl, distro=distro):
                project = identity_fixture()
                project["runtime_surface"] = {
                    "platform": platform, "is_wsl": is_wsl, "wsl_distro_name": distro,
                }
                runtime = dict(runtime_fixture(), surface="wsl" if is_wsl else platform)
                result = self.module().build_freshness_envelope(
                    {"project": project, "runtime": runtime}, project, runtime=runtime,
                )
                self.assertEqual(result["freshness"]["state"], "FRESH")
                if platform != "linux" or not is_wsl:
                    changed = self.module().build_freshness_envelope(
                        {"project": identity_fixture(), "runtime": runtime_fixture()},
                        project, runtime=runtime,
                    )
                    self.assertEqual(changed["freshness"]["state"], "STALE")
                    self.assertIn("project.runtime_surface", changed["freshness"]["changed_fields"])
                    self.assertIn("runtime.surface", changed["freshness"]["changed_fields"])
                    self.assertFalse(changed["freshness"]["write_authorized"])

    def test_invalid_alias_records_never_satisfy_freshness_preconditions(self):
        for label, aliases in malformed_alias_cases(identity_fixture()):
            for position in ("baseline", "current", "both"):
                with self.subTest(label=label, position=position):
                    baseline, current = identity_fixture(), identity_fixture()
                    if position in {"baseline", "both"}:
                        baseline["aliases"] = copy.deepcopy(aliases)
                    if position in {"current", "both"}:
                        current["aliases"] = copy.deepcopy(aliases)
                    before = copy.deepcopy((baseline, current))
                    try:
                        result = self.envelope(baseline, current)
                    except TypeError as error:
                        self.fail(f"Malformed alias must return UNKNOWN, not raise: {error}")
                    self.assertEqual(result["freshness"]["state"], "UNKNOWN")
                    self.assertFalse(result["freshness"]["write_precondition_satisfied"])
                    self.assertFalse(result["freshness"]["write_authorized"])
                    self.assertEqual((baseline, current), before)

    def test_complete_same_object_alias_remains_fresh(self):
        identity = identity_fixture()
        identity["aliases"] = [same_object_alias_fixture(identity)]
        result = self.envelope(identity, copy.deepcopy(identity))
        self.assertEqual(result["freshness"]["state"], "FRESH")
        self.assertFalse(result["freshness"]["write_authorized"])

    def test_invalid_identity_facts_never_become_fresh(self):
        invalid = [
            {"head": None}, {"head": "not-a-commit"}, {"head": "a" * 41},
            {"dirty_fingerprint": None}, {"dirty_fingerprint": ""},
            {"dirty_fingerprint": "g" * 64}, {"dirty_fingerprint_schema": True}, {"dirty_fingerprint_schema": 2},
            {"filesystem_identity": None}, {"filesystem_identity": {}},
            {"filesystem_identity": {"st_dev": True, "st_ino": 2, "object_type": "directory"}},
            {"filesystem_identity": {"st_dev": 1, "st_ino": -1, "object_type": "directory"}},
            {"filesystem_identity": {"st_dev": 1, "st_ino": 2, "object_type": "file"}},
            {"logical_path": None}, {"physical_path": "relative"},
            {"git_dir": ""}, {"worktree_id": None}, {"dirty": "false"},
            {"runtime_surface": None}, {"aliases": None}, {"git": None},
            {"git": dict(identity_fixture()["git"], is_bare=True)},
            {"git": dict(identity_fixture()["git"], is_unborn=True)},
            {"git": dict(identity_fixture()["git"], is_detached=True)},
        ]
        for changes in invalid:
            for position in ("baseline", "current", "both"):
                with self.subTest(changes=changes, position=position):
                    baseline, current = identity_fixture(), identity_fixture()
                    if position in {"baseline", "both"}:
                        baseline.update(copy.deepcopy(changes))
                    if position in {"current", "both"}:
                        current.update(copy.deepcopy(changes))
                    result = self.envelope(baseline, current)
                    self.assertEqual(result["freshness"]["state"], "UNKNOWN")
                    self.assertFalse(result["freshness"]["write_precondition_satisfied"])
                    self.assertFalse(result["freshness"]["write_authorized"])

    def test_valid_detached_unborn_and_sha256_heads_remain_comparable(self):
        variants = [identity_fixture() for _ in range(3)]
        variants[0]["head"] = "a" * 64
        variants[1]["head"] = None
        variants[1]["git"]["is_unborn"] = True
        variants[2]["branch"] = None
        variants[2]["git"]["is_detached"] = True
        for identity in variants:
            with self.subTest(head=identity["head"], branch=identity["branch"]):
                before = copy.deepcopy(identity)
                result = self.envelope(identity, copy.deepcopy(identity))
                self.assertEqual(result["freshness"]["state"], "FRESH")
                self.assertFalse(result["freshness"]["write_authorized"])
                self.assertEqual(identity, before)

    def module(self):
        self.assertIsNotNone(FRESHNESS, "project_freshness is not implemented")
        return FRESHNESS

    def envelope(self, baseline, current, **kwargs):
        # Existing identity/source tests supply explicit, unchanged program facts.
        if isinstance(baseline, dict):
            baseline = copy.deepcopy(baseline)
            if "project" not in baseline:
                baseline = {"project": baseline}
            baseline.setdefault("runtime", runtime_fixture())
        return self.module().build_freshness_envelope(
            baseline,
            current,
            runtime=runtime_fixture(),
            **kwargs,
        )

    def test_no_baseline_is_unknown(self):
        result = self.envelope(None, identity_fixture())

        self.assertEqual(result["freshness"]["state"], "UNKNOWN")
        self.assertEqual(
            result["freshness"]["reasons"],
            ["INVALID_RUNTIME_EVIDENCE:baseline", "NO_COMPARISON_BASELINE"],
        )
        self.assertFalse(result["freshness"]["write_authorized"])

    def test_equal_required_fields_are_fresh(self):
        baseline = {
            "project": identity_fixture(),
            "skills": inventory_fixture(),
            "status_source": status_fixture(),
        }

        result = self.envelope(
            baseline,
            copy.deepcopy(baseline["project"]),
            inventory=copy.deepcopy(baseline["skills"]),
            status_source=copy.deepcopy(baseline["status_source"]),
        )

        self.assertEqual(result["freshness"]["state"], "FRESH")
        self.assertEqual(result["freshness"]["reasons"], [])
        self.assertEqual(result["freshness"]["changed_fields"], [])
        self.assertTrue(result["freshness"]["write_precondition_satisfied"])
        self.assertFalse(result["freshness"]["write_authorized"])

    def test_equal_read_only_or_blocked_identity_is_not_write_ready(self):
        for eligibility in ("READ_ONLY", "BLOCKED"):
            with self.subTest(eligibility=eligibility):
                baseline = identity_fixture()
                baseline["write_eligibility"] = eligibility

                result = self.envelope(baseline, copy.deepcopy(baseline))

                self.assertEqual(result["freshness"]["state"], "UNKNOWN")
                self.assertIn(
                    "WRITE_ELIGIBILITY_NOT_ELIGIBLE:baseline",
                    result["freshness"]["reasons"],
                )
                self.assertIn(
                    "WRITE_ELIGIBILITY_NOT_ELIGIBLE:current",
                    result["freshness"]["reasons"],
                )
                self.assertFalse(
                    result["freshness"]["write_precondition_satisfied"]
                )
                self.assertFalse(result["freshness"]["write_authorized"])

    def test_current_blocked_identity_cannot_reuse_eligible_baseline(self):
        baseline = identity_fixture()
        current = copy.deepcopy(baseline)
        current["write_eligibility"] = "BLOCKED"

        result = self.envelope(baseline, current)

        self.assertEqual(result["freshness"]["state"], "UNKNOWN")
        self.assertIn(
            "WRITE_ELIGIBILITY_NOT_ELIGIBLE:current",
            result["freshness"]["reasons"],
        )
        self.assertIn(
            "project.write_eligibility",
            result["freshness"]["changed_fields"],
        )
        self.assertFalse(result["freshness"]["write_precondition_satisfied"])

    def test_invalid_write_eligibility_is_unknown(self):
        current = identity_fixture()
        current["write_eligibility"] = "UNRECOGNIZED"

        result = self.envelope(identity_fixture(), current)

        self.assertEqual(result["freshness"]["state"], "UNKNOWN")
        self.assertIn(
            "INVALID_WRITE_ELIGIBILITY:current",
            result["freshness"]["reasons"],
        )
        self.assertFalse(result["freshness"]["write_precondition_satisfied"])

    def test_non_git_or_non_required_fingerprint_is_not_write_ready(self):
        cases = (
            (
                "non_git",
                {"binding_kind": "BOUND_NON_GIT", "is_git": False},
                "IDENTITY_NOT_GIT_WORKTREE:current",
            ),
            (
                "fingerprint_not_required",
                {"fingerprint_applicability": "NOT_APPLICABLE"},
                "FINGERPRINT_NOT_REQUIRED:current",
            ),
            (
                "fingerprint_incomplete",
                {"fingerprint_complete": False},
                "INCOMPLETE_FINGERPRINT:current",
            ),
        )

        for label, changes, expected_reason in cases:
            with self.subTest(label=label):
                current = identity_fixture()
                current.update(changes)

                result = self.envelope(identity_fixture(), current)

                self.assertEqual(result["freshness"]["state"], "UNKNOWN")
                self.assertIn(expected_reason, result["freshness"]["reasons"])
                self.assertFalse(
                    result["freshness"]["write_precondition_satisfied"]
                )

    def test_each_required_identity_head_dirty_runtime_inventory_and_status_change_is_stale(self):
        baseline = {
            "project": identity_fixture(),
            "skills": inventory_fixture(),
            "status_source": status_fixture(),
        }
        cases = []

        filesystem = copy.deepcopy(baseline["project"])
        filesystem["filesystem_identity"]["st_ino"] = 99
        cases.append(("project.filesystem_identity", filesystem, baseline["skills"], baseline["status_source"]))
        head = copy.deepcopy(baseline["project"])
        head["head"] = "f" * 40
        cases.append(("project.head", head, baseline["skills"], baseline["status_source"]))
        dirty = copy.deepcopy(baseline["project"])
        dirty["dirty_fingerprint"] = "1" * 64
        cases.append(("project.dirty_fingerprint", dirty, baseline["skills"], baseline["status_source"]))
        runtime = copy.deepcopy(baseline["project"])
        runtime["runtime_surface"]["wsl_distro_name"] = "Debian"
        cases.append(("project.runtime_surface", runtime, baseline["skills"], baseline["status_source"]))
        inventory = copy.deepcopy(baseline["skills"])
        inventory["inventory_sha256"] = "3" * 64
        cases.append(("skills.inventory_sha256", baseline["project"], inventory, baseline["status_source"]))
        status = copy.deepcopy(baseline["status_source"])
        status["content_sha256"] = "4" * 64
        cases.append(("status_source.content_sha256", baseline["project"], baseline["skills"], status))

        for expected_field, project, inventory, status in cases:
            with self.subTest(expected_field=expected_field):
                result = self.envelope(
                    baseline,
                    copy.deepcopy(project),
                    inventory=copy.deepcopy(inventory),
                    status_source=copy.deepcopy(status),
                )
                self.assertEqual(result["freshness"]["state"], "STALE")
                self.assertIn(expected_field, result["freshness"]["changed_fields"])

    def test_missing_required_field_is_unknown(self):
        current = identity_fixture()
        del current["head"]

        result = self.envelope(identity_fixture(), current)

        self.assertEqual(result["freshness"]["state"], "UNKNOWN")
        self.assertIn("MISSING_REQUIRED_FIELD:project.head", result["freshness"]["reasons"])

    def test_ambiguous_alias_has_highest_priority(self):
        current = identity_fixture()
        del current["head"]
        current["aliases"] = [
            {
                "input": "/alias",
                "relation_to_workspace": "AMBIGUOUS",
                "reason": "AMBIGUOUS_CASE_ALIAS",
            }
        ]

        result = self.envelope(None, current)

        self.assertEqual(result["freshness"]["state"], "AMBIGUOUS")
        self.assertIn("AMBIGUOUS_PROJECT_ALIAS", result["freshness"]["reasons"])

    def test_optional_and_not_applicable_sources_do_not_create_unknown(self):
        optional = inventory_fixture("OPTIONAL")
        baseline = {
            "project": identity_fixture(),
            "skills": optional,
            "status_source": status_fixture("NOT_APPLICABLE"),
        }
        changed_optional = copy.deepcopy(optional)
        changed_optional["inventory_sha256"] = "9" * 64

        result = self.envelope(
            baseline,
            identity_fixture(),
            inventory=changed_optional,
            status_source=status_fixture("NOT_APPLICABLE"),
        )

        self.assertEqual(result["freshness"]["state"], "FRESH")
        self.assertEqual(result["skills"]["route_state"], "STALE")
        self.assertEqual(result["status_source"]["applicability"], "NOT_APPLICABLE")

    def test_required_sources_cannot_be_omitted_or_downgraded(self):
        baseline = {
            "project": identity_fixture(),
            "skills": inventory_fixture("REQUIRED"),
            "status_source": status_fixture("REQUIRED"),
        }

        omitted = self.envelope(baseline, identity_fixture())
        downgraded = self.envelope(
            baseline,
            identity_fixture(),
            inventory={"applicability": "NOT_APPLICABLE"},
            status_source={"applicability": "OPTIONAL"},
        )

        self.assertEqual(omitted["freshness"]["state"], "UNKNOWN")
        self.assertIn(
            "REQUIRED_COMPONENT_OMITTED:skills",
            omitted["freshness"]["reasons"],
        )
        self.assertIn(
            "REQUIRED_COMPONENT_OMITTED:status_source",
            omitted["freshness"]["reasons"],
        )
        self.assertEqual(downgraded["freshness"]["state"], "UNKNOWN")

    def test_timestamp_only_change_stays_fresh(self):
        baseline = {
            "project": identity_fixture(),
            "skills": inventory_fixture(),
            "status_source": status_fixture(),
        }
        current = copy.deepcopy(baseline["project"])
        current["captured_at_utc"] = "2026-08-10T00:00:00Z"
        current["bound_at_utc"] = "2026-08-10T00:00:00Z"
        inventory = copy.deepcopy(baseline["skills"])
        inventory["generated_at_utc"] = "2026-08-10T00:00:00Z"

        result = self.envelope(
            baseline,
            current,
            inventory=inventory,
            status_source=status_fixture(),
        )

        self.assertEqual(result["freshness"]["state"], "FRESH")

    def test_changed_fields_are_sorted_and_secret_free(self):
        current = identity_fixture()
        current["head"] = "secret-head-value"
        current["branch"] = "secret-branch-value"

        result = self.envelope(identity_fixture(), current)
        freshness_json = json.dumps(result["freshness"], sort_keys=True)

        self.assertEqual(
            result["freshness"]["changed_fields"],
            sorted(result["freshness"]["changed_fields"]),
        )
        self.assertNotIn("secret-head-value", freshness_json)
        self.assertNotIn("secret-branch-value", freshness_json)

    def test_canonical_digest_ignores_only_generation_time_where_specified(self):
        module = self.module()
        first = {
            "value": "same",
            "captured_at_utc": "one",
            "bound_at_utc": "one",
            "generated_at_utc": "one",
            "updated_at_utc": "one",
        }
        generation_changed = dict(
            first,
            captured_at_utc="two",
            bound_at_utc="two",
            generated_at_utc="two",
        )
        semantic_time_changed = dict(generation_changed, updated_at_utc="two")

        self.assertEqual(
            module.canonical_json_digest(first),
            module.canonical_json_digest(generation_changed),
        )
        self.assertNotEqual(
            module.canonical_json_digest(generation_changed),
            module.canonical_json_digest(semantic_time_changed),
        )

    def test_cli_rejects_duplicate_keys_in_all_comparison_inputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            inputs = {
                "baseline": {"project": identity_fixture(), "runtime": runtime_fixture(),
                             "skills": inventory_fixture(), "status_source": status_fixture()},
                "current": identity_fixture(), "runtime": runtime_fixture(),
                "inventory": inventory_fixture(), "status-source": status_fixture(),
            }
            originals = {name: json.dumps(value) for name, value in inputs.items()}
            command = [sys.executable, SCRIPT]
            for name, content in originals.items():
                path = root / (name + ".json")
                path.write_text(content, encoding="utf-8")
                command += ["--" + name, path]
            valid = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertTrue(json.loads(valid.stdout)["freshness"]["write_precondition_satisfied"])
            cases = [
                ("baseline", '"fingerprint_complete": true',
                 '"fingerprint_complete": false, "fingerprint_complete": true'),
                ("current", '"fingerprint_complete": true',
                 '"fingerprint_complete": false, "fingerprint_complete": true'),
                ("current", '"is_wsl": true', '"is_wsl": false, "is_wsl": true'),
                ("runtime", '"surface": "wsl"', '"surface": "UNKNOWN", "surface": "wsl"'),
                ("inventory", '"applicability": "REQUIRED"',
                 '"applicability": "OPTIONAL", "applicability": "REQUIRED"'),
                ("status-source", '"applicability": "REQUIRED"',
                 '"applicability": "OPTIONAL", "applicability": "REQUIRED"'),
                ("current", '"fingerprint_complete": true',
                 '"fingerprint_complete": false, "fingerprint_\\u0063omplete": true'),
            ]
            for index, (name, old, new) in enumerate(cases):
                with self.subTest(name=name, replacement=new):
                    path = root / (name + ".json")
                    self.assertIn(old, originals[name])
                    path.write_text(originals[name].replace(old, new, 1), encoding="utf-8")
                    output = root / f"result-{index}.json"
                    result = subprocess.run(command + ["--output", output],
                                            capture_output=True, text=True, check=False)
                    path.write_text(originals[name], encoding="utf-8")
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(output.exists())

    def test_cli_writes_only_explicit_non_symlink_output(self):
        self.assertTrue(SCRIPT.exists(), "project_freshness CLI is not implemented")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            baseline = root / "baseline.json"
            current = root / "current.json"
            runtime = root / "runtime.json"
            baseline.write_text(json.dumps({
                "project": identity_fixture(), "runtime": runtime_fixture(),
            }), encoding="utf-8")
            current.write_text(json.dumps(identity_fixture()), encoding="utf-8")
            runtime.write_text(json.dumps(runtime_fixture()), encoding="utf-8")

            without_output = subprocess.run(
                [sys.executable, SCRIPT, "--baseline", baseline, "--current", current,
                 "--runtime", runtime],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(without_output.returncode, 0, without_output.stderr)
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["baseline.json", "current.json", "runtime.json"],
            )

            output = root / "result.json"
            with_output = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--baseline",
                    baseline,
                    "--current",
                    current,
                    "--runtime",
                    runtime,
                    "--output",
                    output,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(with_output.returncode, 0, with_output.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), json.loads(with_output.stdout))

            symlink = root / "symlink-output.json"
            symlink.symlink_to(output)
            rejected = subprocess.run(
                [
                    sys.executable,
                    SCRIPT,
                    "--baseline",
                    baseline,
                    "--current",
                    current,
                    "--runtime",
                    runtime,
                    "--output",
                    symlink,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 2)

    def test_cli_missing_invalid_or_drifted_program_evidence_never_exits_zero(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            baseline, current, runtime = [root / name for name in (
                "baseline.json", "current.json", "runtime.json",
            )]
            baseline.write_text(json.dumps({
                "project": identity_fixture(), "runtime": runtime_fixture(),
            }), encoding="utf-8")
            current.write_text(json.dumps(identity_fixture()), encoding="utf-8")
            command = [sys.executable, SCRIPT, "--baseline", baseline, "--current", current]
            for evidence, state in (
                (None, "UNKNOWN"), ({}, "UNKNOWN"),
                (dict(runtime_fixture(), codex_version="0.148.0"), "STALE"),
                (dict(runtime_fixture(), core_sha256="2" * 64), "STALE"),
            ):
                with self.subTest(evidence=evidence):
                    runtime.write_text(json.dumps(evidence), encoding="utf-8")
                    argv = command if evidence is None else command + ["--runtime", runtime]
                    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
                    self.assertEqual(completed.returncode, 3, completed.stderr)
                    result = json.loads(completed.stdout)
                    self.assertEqual(result["freshness"]["state"], state)
                    self.assertFalse(result["freshness"]["write_precondition_satisfied"])
                    self.assertFalse(result["freshness"]["write_authorized"])
            alias = root / "runtime-alias.json"
            alias.symlink_to(runtime)
            rejected = subprocess.run(command + ["--runtime", alias],
                                      capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 2)


if __name__ == "__main__":
    unittest.main()
