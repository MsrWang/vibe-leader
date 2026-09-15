# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import contextlib
import importlib
import io
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

from workbench import delegation_contract as CONTRACT
from workbench import project_identity
from tests.test_project_identity import (
    malformed_alias_cases, malformed_runtime_surfaces, same_object_alias_fixture,
)
from tests.test_project_freshness import malformed_runtime_evidence, runtime_fixture


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workbench" / "delegation_contract.py"


def identity_fixture(
    *,
    path="/project/main",
    worktree_id="main-worktree",
    git_common_dir="/project/.git",
    head=None,
):
    head = head or "a" * 40
    value = {
        "schema_version": 2,
        "status": "bound",
        "reason": None,
        "requested_cwd": path,
        "pwd": path,
        "realpath": path,
        "is_git": True,
        "git_top_level": path,
        "git_dir": f"{git_common_dir}/worktrees/{worktree_id}",
        "git_common_dir": git_common_dir,
        "worktree_id": worktree_id,
        "branch": "feat/delegation",
        "head": head,
        "dirty": False,
        "dirty_fingerprint": "b" * 64,
        "fingerprint_complete": True,
        "remotes": [],
        "bound_at_utc": "2026-08-25T00:00:00Z",
        "binding_kind": "GIT_WORKTREE",
        "write_eligibility": "ELIGIBLE",
        "path_input_kind": "WSL_POSIX_ABSOLUTE",
        "resolution_traits": [],
        "logical_path": path,
        "physical_path": path,
        "filesystem_identity": {
            "st_dev": 1,
            "st_ino": 2 if worktree_id == "main-worktree" else 3,
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
        "captured_at_utc": "2026-08-25T00:00:00Z",
    }
    if frozenset(value) != project_identity.IDENTITY_SCHEMA_FIELDS[2]:
        raise AssertionError("identity fixture does not match schema 2")
    return value


def authority_summary(identity):
    return {
        field: identity[field]
        for field in (
            "logical_path",
            "physical_path",
            "git_top_level",
            "git_dir",
            "git_common_dir",
            "worktree_id",
        )
    }


def brief_fixture(*, mode="READ_ONLY"):
    main = identity_fixture()
    authority = copy.deepcopy(main)
    write_allowlist = []
    if mode == "ISOLATED_WRITER":
        authority = identity_fixture(
            path="/project/writer",
            worktree_id="writer-worktree",
        )
        write_allowlist = ["src/delegated_change.py"]
    read_scope = ["src/input.py", "tests/test_input.py"]
    return {
        "schema_id": CONTRACT.BRIEF_SCHEMA_ID,
        "reception": {"task_id": "synthetic-task-23", "resume_policy": "EXPIRE"},
        "delegation_id": "D-1.2-NATIVE-001/01",
        "parent_goal": "1.2 adaptive delegation technical acceptance",
        "objective": "Inspect the approved delegation contract.",
        "non_goals": ["Do not make the main-thread decision."],
        "authority_project": authority_summary(authority),
        "baseline": {
            "main_thread_identity": main,
            "authority_identity": authority,
            "required_file_hashes": {
                "src/input.py": "d" * 64,
                "tests/test_input.py": "e" * 64,
            },
        },
        "mode": mode,
        "read_scope": read_scope,
        "write_allowlist": write_allowlist,
        "allowed_commands": [
            ["python3", "-m", "unittest", "tests.test_input", "-v"]
        ],
        "prohibited_actions": [
            "NESTED_DELEGATION",
            "ACCOUNT_OR_CREDENTIAL_ACCESS",
            "NETWORK_OR_EXTERNAL_SERVICE",
            "INSTALL_OR_CODEX_HOME_CHANGE",
            "PUSH_PR_RELEASE",
            "DEPLOYMENT",
            "MAIN_THREAD_DECISION",
            "WRITE_OUTSIDE_ALLOWLIST",
        ],
        "budget": {
            "max_agents": 1,
            "max_turns_per_agent": 1,
            "max_wall_time_minutes": 10,
            "max_model_calls": 1,
            "model_policy": "RUNTIME_DEFAULT",
            "token_budget": None,
        },
        "stop_conditions": [
            "BASELINE_DRIFT",
            "IDENTITY_INCOMPLETE_OR_BLOCKED",
            "ALLOWLIST_EXPANSION_REQUIRED",
            "BUDGET_EXHAUSTED",
            "EXTERNAL_PERMISSION_REQUIRED",
            "NESTED_DELEGATION_REQUESTED",
            "UNKNOWN_RESULT",
        ],
        "deliverables": ["Structured findings and test evidence."],
        "acceptance": ["Main thread can reproduce every finding."],
        "expiry": [
            "TASK_ID_CHANGED", "USER_PAUSED_OR_CANCELLED", "APPROVAL_REVOKED",
            "LIFECYCLE_EVIDENCE_INCOMPLETE", "TASK_INTERRUPTED",
            "TASK_INTERRUPTED_OR_RESUMED",
            "PROJECT_OR_WORKTREE_CHANGED",
            "HEAD_CHANGED",
            "DIRTY_FINGERPRINT_CHANGED",
            "APPROVAL_SCOPE_CHANGED",
            "RUNTIME_CAPABILITY_CHANGED",
        ],
    }


def valid_brief(*, mode="READ_ONLY"):
    value = brief_fixture(mode=mode)
    if mode in {"READ_ONLY", "REVIEW"}:
        value["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")
    return value


def runtime_context_fixture(brief):
    """Main-thread evidence pinned to this brief, not an agent self-report."""
    return {
        "brief_sha256": CONTRACT.canonical_contract_digest(brief),
        "baseline": runtime_fixture(),
        "current": runtime_fixture(),
    }


def reception_context_fixture(brief, result):
    """Synthetic main-thread completeness claims, never real trace evidence."""
    return {
        "brief_sha256": CONTRACT.canonical_contract_digest(brief),
        "result_sha256": CONTRACT.canonical_contract_digest(result),
        "current_task_id": brief["reception"]["task_id"],
        "events": [],
        "events_complete": True,
        "evidence_complete": True,
    }


def result_fixture(brief):
    authority = copy.deepcopy(brief["baseline"]["authority_identity"])
    files_modified = []
    candidate = None
    if brief["mode"] == "ISOLATED_WRITER":
        authority["dirty"] = True
        authority["dirty_fingerprint"] = "f" * 64
        files_modified = list(brief["write_allowlist"])
        candidate = {
            "parent_head": brief["baseline"]["authority_identity"]["head"],
            "head": authority["head"],
            "commit": None,
            "diff_sha256": "1" * 64,
            "changed_files": list(files_modified),
        }
    command = copy.deepcopy(brief["allowed_commands"][0])
    return {
        "schema_id": CONTRACT.RESULT_SCHEMA_ID,
        "brief_sha256": CONTRACT.canonical_contract_digest(brief),
        "delegation_id": brief["delegation_id"],
        "status": "RETURNED",
        "mode": brief["mode"],
        "files_read": list(brief["read_scope"]),
        "files_modified": files_modified,
        "commands": [command],
        "source_baseline_sha256": CONTRACT.canonical_contract_digest(
            brief["baseline"]["authority_identity"]
        ),
        "return_baseline": authority,
        "findings": [],
        "candidate": candidate,
        "tests": [
            {
                "command": command,
                "exit_code": 0,
                "status": "PASS",
                "summary": "Synthetic allowlisted command passed.",
            }
        ],
        "unknowns": [],
        "warnings": [],
        "scope_requests": [],
        "usage": {
            "turns": 1,
            "wall_time_seconds": 30,
            "model_calls": 1,
            "observed_tokens": "UNKNOWN",
        },
        "nested_delegation_attempted": False,
        "external_action_attempted": False,
        "recommendation": "ACCEPT_FOR_MAIN_THREAD_REVIEW",
        "returned_at_utc": "2026-08-25T01:02:03Z",
    }


class ReviewCommandTransportTests(unittest.TestCase):
    def render(self, brief, command, transport="POSIX_SHELL_STRING"):
        renderer = getattr(CONTRACT, "render_review_command", None)
        self.assertTrue(callable(renderer), "bounded review command renderer missing")
        return renderer(brief, command, transport=transport)

    def test_native_shell_receives_literal_arguments_without_expansion(self):
        # Removing per-argument quoting would execute substitutions or split argv.
        payload = [
            "two words", "a'b", 'a"b', "$(printf INJECTED)",
            "`printf INJECTED`", "; printf INJECTED", "| cat", ">output",
            "*", "$HOME", "a\\b", "中文",
        ]
        command = [
            sys.executable, "-I", "-B", "-c",
            "import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=False))",
            *payload,
        ]
        brief = valid_brief(mode="REVIEW")
        brief["allowed_commands"] = [command]
        rendered = self.render(brief, command)
        completed = subprocess.run(
            ["/bin/sh", "-c", rendered], cwd=ROOT, capture_output=True,
            text=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), payload)
        self.assertEqual(completed.stderr, "")

    def test_rejects_added_changed_or_reordered_arguments(self):
        brief = valid_brief(mode="REVIEW")
        brief["allowed_commands"] = [["/usr/bin/printf", "%s", "approved"]]
        for command in (
            ["/usr/bin/printf", "%s", "other"],
            ["/usr/bin/printf", "%s", "approved", "extra"],
            ["/usr/bin/printf", "approved", "%s"],
        ):
            with self.subTest(command=command):
                with self.assertRaises(CONTRACT.DelegationContractError) as caught:
                    self.render(brief, command)
                self.assertEqual(caught.exception.reason, "COMMAND_NOT_ALLOWLISTED")

    def test_rejects_unknown_or_non_posix_transport(self):
        brief = valid_brief(mode="REVIEW")
        for transport in ("UNKNOWN", "POWERSHELL", "CMD", None, []):
            with self.subTest(transport=transport):
                with self.assertRaises(CONTRACT.DelegationContractError) as caught:
                    self.render(brief, brief["allowed_commands"][0], transport)
                self.assertEqual(caught.exception.reason, "COMMAND_TRANSPORT_UNSUPPORTED")

    def test_rejects_shell_program_or_shell_wrapper_even_if_listed(self):
        brief = valid_brief(mode="REVIEW")
        for program in ("sh", "/bin/bash", "pwsh", "cmd.exe", "eval", "env"):
            command = [program, "-c", "printf not-approved-shell"]
            brief["allowed_commands"] = [command]
            with self.subTest(program=program):
                with self.assertRaises(CONTRACT.DelegationContractError) as caught:
                    self.render(brief, command)
                self.assertEqual(caught.exception.reason, "SHELL_COMMAND_FORBIDDEN")

    def test_requires_valid_read_only_brief(self):
        brief = valid_brief(mode="REVIEW")
        command = brief["allowed_commands"][0]
        brief["prohibited_actions"].remove("FILESYSTEM_OR_GIT_METADATA_WRITE")
        with self.assertRaises(CONTRACT.DelegationContractError) as caught:
            self.render(brief, command)
        self.assertEqual(caught.exception.reason, "REQUIRED_PROHIBITION_MISSING")
        writer = valid_brief(mode="ISOLATED_WRITER")
        with self.assertRaises(CONTRACT.DelegationContractError) as caught:
            self.render(writer, writer["allowed_commands"][0])
        self.assertEqual(caught.exception.reason, "REVIEW_COMMAND_MODE_REQUIRED")

    def test_rendering_preserves_brief_and_command_without_authorizing_execution(self):
        brief = valid_brief(mode="READ_ONLY")
        command = ["/usr/bin/printf", "%s", "two words"]
        brief["allowed_commands"] = [command]
        before = copy.deepcopy((brief, command))
        self.assertEqual(
            self.render(brief, command), "exec /usr/bin/printf %s 'two words'"
        )
        self.assertEqual((brief, command), before)


class DelegationBriefTests(unittest.TestCase):
    def test_brief_rejects_malformed_runtime_surfaces_in_either_baseline(self):
        for surface in malformed_runtime_surfaces():
            for position in ("main_thread_identity", "authority_identity"):
                with self.subTest(surface=surface, position=position):
                    brief = valid_brief()
                    brief["baseline"][position]["runtime_surface"] = copy.deepcopy(surface)
                    self.assert_rejected(brief, "IDENTITY_SCHEMA_INVALID")

    def test_brief_rejects_invalid_alias_records_in_either_baseline(self):
        for label, aliases in malformed_alias_cases(identity_fixture()):
            for name in ("main_thread_identity", "authority_identity"):
                with self.subTest(label=label, name=name):
                    value = valid_brief()
                    value["baseline"][name]["aliases"] = copy.deepcopy(aliases)
                    before = copy.deepcopy(value)
                    self.assert_rejected(value, "IDENTITY_SCHEMA_INVALID")
                    self.assertEqual(value, before)

    def test_brief_rejects_malformed_identity_facts_in_either_baseline(self):
        for changes in (
            {"head": None}, {"head": "not-a-commit"},
            {"dirty_fingerprint": None}, {"dirty_fingerprint": ""},
            {"filesystem_identity": None}, {"filesystem_identity": {}},
            {"git": None}, {"runtime_surface": None},
        ):
            for name in ("main_thread_identity", "authority_identity"):
                with self.subTest(changes=changes, name=name):
                    value = valid_brief()
                    value["baseline"][name].update(copy.deepcopy(changes))
                    self.assert_rejected(value, "IDENTITY_SCHEMA_INVALID")

    def assert_rejected(self, value, reason):
        with self.assertRaises(CONTRACT.DelegationContractError) as caught:
            CONTRACT.validate_delegation_brief(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_read_only_brief_accepts_complete_same_worktree_baselines(self):
        value = brief_fixture()
        value["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")

        validated = CONTRACT.validate_delegation_brief(value)

        self.assertEqual(validated, value)
        self.assertIsNot(validated, value)
        self.assertEqual(
            CONTRACT.canonical_contract_digest(validated),
            CONTRACT.canonical_contract_digest(value),
        )

    def test_brief_accepts_nonzero_three_digit_batch_and_two_digit_agent_ids(self):
        for delegation_id in (
            "D-1.2-NATIVE-002/01",
            "D-1.2-NATIVE-999/99",
        ):
            with self.subTest(delegation_id=delegation_id):
                value = valid_brief()
                value["delegation_id"] = delegation_id

                validated = CONTRACT.validate_delegation_brief(value)

                self.assertEqual(validated["delegation_id"], delegation_id)

    def test_brief_rejects_zero_wrong_width_and_out_of_range_delegation_ids(self):
        for delegation_id in (
            "D-1.2-NATIVE-000/01",
            "D-1.2-NATIVE-01/01",
            "D-1.2-NATIVE-0001/01",
            "D-1.2-NATIVE-1000/01",
            "D-1.2-NATIVE-002/00",
            "D-1.2-NATIVE-002/1",
            "D-1.2-NATIVE-002/100",
            "D-1.2-NATIVE-ABC/01",
        ):
            with self.subTest(delegation_id=delegation_id):
                value = valid_brief()
                value["delegation_id"] = delegation_id

                self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_review_brief_requires_empty_write_allowlist(self):
        value = brief_fixture(mode="REVIEW")
        value["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")
        value["write_allowlist"] = ["src/review.py"]

        self.assert_rejected(value, "READ_ONLY_WRITE_ALLOWLIST_NOT_EMPTY")

    def test_writer_requires_different_worktree_in_same_repository(self):
        valid = brief_fixture(mode="ISOLATED_WRITER")
        self.assertEqual(CONTRACT.validate_delegation_brief(valid), valid)

        same_worktree = copy.deepcopy(valid)
        same_worktree["baseline"]["authority_identity"] = copy.deepcopy(
            same_worktree["baseline"]["main_thread_identity"]
        )
        same_worktree["authority_project"] = authority_summary(
            same_worktree["baseline"]["authority_identity"]
        )
        self.assert_rejected(same_worktree, "WRITER_WORKTREE_NOT_ISOLATED")

        other_repository = copy.deepcopy(valid)
        authority = other_repository["baseline"]["authority_identity"]
        authority["git_common_dir"] = "/other/.git"
        authority["git_dir"] = "/other/.git/worktrees/writer-worktree"
        other_repository["authority_project"] = authority_summary(authority)
        self.assert_rejected(other_repository, "WRITER_REPOSITORY_MISMATCH")

        other_head = copy.deepcopy(valid)
        other_head["baseline"]["authority_identity"]["head"] = "f" * 40
        self.assert_rejected(other_head, "WRITER_PARENT_HEAD_MISMATCH")

    def test_writer_requires_nonempty_nonoverlapping_allowlist(self):
        empty = brief_fixture(mode="ISOLATED_WRITER")
        empty["write_allowlist"] = []
        self.assert_rejected(empty, "WRITER_ALLOWLIST_EMPTY")

        overlapping = brief_fixture(mode="ISOLATED_WRITER")
        overlapping["write_allowlist"] = ["src", "src/delegated_change.py"]
        self.assert_rejected(overlapping, "FIELD_VALUE_INVALID")

    def test_incomplete_or_blocked_identity_is_rejected(self):
        for changes in (
            {"write_eligibility": "BLOCKED"},
            {"fingerprint_complete": False},
            {"status": "incomplete", "reason": "identity_changed"},
        ):
            with self.subTest(changes=changes):
                value = brief_fixture()
                value["prohibited_actions"].append(
                    "FILESYSTEM_OR_GIT_METADATA_WRITE"
                )
                value["baseline"]["authority_identity"].update(changes)
                self.assert_rejected(
                    value,
                    "IDENTITY_NOT_STABLE_WRITABLE_CANDIDATE",
                )

    def test_authority_summary_must_equal_authority_identity(self):
        value = brief_fixture()
        value["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")
        value["authority_project"]["logical_path"] = "/other"

        self.assert_rejected(value, "AUTHORITY_IDENTITY_MISMATCH")

    def test_budget_is_positive_and_token_budget_is_explicit(self):
        valid = brief_fixture(mode="ISOLATED_WRITER")
        valid["budget"]["token_budget"] = 1000
        self.assertEqual(CONTRACT.validate_delegation_brief(valid), valid)

        cases = (
            ("max_agents", 0),
            ("max_turns_per_agent", True),
            ("max_wall_time_minutes", -1),
            ("max_model_calls", "1"),
            ("token_budget", 0),
            ("token_budget", True),
        )
        for field, invalid in cases:
            with self.subTest(field=field, invalid=invalid):
                value = brief_fixture(mode="ISOLATED_WRITER")
                value["budget"][field] = invalid
                self.assert_rejected(value, "BUDGET_INVALID")

    def test_required_prohibitions_stops_and_expiry_are_enforced(self):
        base = brief_fixture()
        base["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")
        cases = (
            (
                "prohibited_actions",
                "NETWORK_OR_EXTERNAL_SERVICE",
                "REQUIRED_PROHIBITION_MISSING",
            ),
            ("stop_conditions", "BASELINE_DRIFT", "REQUIRED_STOP_CONDITION_MISSING"),
            ("expiry", "HEAD_CHANGED", "REQUIRED_EXPIRY_MISSING"),
            ("expiry", "TASK_INTERRUPTED_OR_RESUMED", "REQUIRED_EXPIRY_MISSING"),
        )
        for field, item, reason in cases:
            with self.subTest(field=field, item=item):
                value = copy.deepcopy(base)
                value[field].remove(item)
                self.assert_rejected(value, reason)

    def test_schema_rejects_missing_extra_and_unknown_fields(self):
        base = brief_fixture()
        base["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")

        missing = copy.deepcopy(base)
        del missing["acceptance"]
        self.assert_rejected(missing, "SCHEMA_FIELDS_CHANGED")

        extra = copy.deepcopy(base)
        extra["approval"] = "implicit"
        self.assert_rejected(extra, "SCHEMA_FIELDS_CHANGED")

        unknown_schema = copy.deepcopy(base)
        unknown_schema["schema_id"] = "unknown"
        self.assert_rejected(unknown_schema, "SCHEMA_UNSUPPORTED")

        wrong_type = copy.deepcopy(base)
        wrong_type["non_goals"] = "none"
        self.assert_rejected(wrong_type, "FIELD_TYPE_INVALID")

    def test_validation_does_not_mutate_input(self):
        value = brief_fixture()
        value["prohibited_actions"].append("FILESYSTEM_OR_GIT_METADATA_WRITE")
        before = copy.deepcopy(value)

        validated = CONTRACT.validate_delegation_brief(value)
        validated["baseline"]["authority_identity"]["head"] = "f" * 40
        validated["read_scope"].append("src/other.py")

        self.assertEqual(value, before)


class RuntimeContextPreparationTests(unittest.TestCase):
    def prepare(self, brief, baseline_runtime, current_runtime, current_main):
        function = getattr(CONTRACT, "build_runtime_context", None)
        self.assertTrue(callable(function), "runtime context preparation is not implemented")
        return function(brief, baseline_runtime=baseline_runtime, current_runtime=current_runtime,
                        current_main_identity=current_main)

    def test_preparation_pins_brief_and_does_not_hide_drift_or_grant_authority(self):
        brief = valid_brief()
        result = result_fixture(brief)
        main = brief["baseline"]["main_thread_identity"]
        baseline = runtime_fixture()
        for current, expected in ((runtime_fixture(), "FRESH"),
                                  (dict(runtime_fixture(), codex_version="0.148.0"), "STALE"),
                                  (dict(runtime_fixture(), core_sha256="2" * 64), "STALE")):
            with self.subTest(current=current):
                before = copy.deepcopy((brief, baseline, current, main))
                with mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
                     mock.patch("os.open", side_effect=AssertionError("filesystem")):
                    context = self.prepare(brief, baseline, current, main)
                    evaluated = CONTRACT.evaluate_delegation_result(
                        brief, result, current_main_identity=main,
                        current_authority_identity=result["return_baseline"], runtime_context=context,
                        reception_context=reception_context_fixture(brief, result),
                    )
                self.assertEqual(context, {"brief_sha256": CONTRACT.canonical_contract_digest(brief),
                                           "baseline": baseline, "current": current})
                self.assertEqual(evaluated["freshness"], expected)
                self.assertFalse(evaluated["write_authorized"])
                if expected == "STALE":
                    self.assertIsNone(evaluated["consumption_record"])
                self.assertEqual((brief, baseline, current, main), before)

    def test_preparation_rejects_missing_malformed_or_mismatched_runtime_evidence(self):
        brief = valid_brief()
        main = brief["baseline"]["main_thread_identity"]
        for invalid in malformed_runtime_evidence():
            for position in ("baseline", "current"):
                with self.subTest(invalid=invalid, position=position):
                    baseline = invalid if position == "baseline" else runtime_fixture()
                    current = invalid if position == "current" else runtime_fixture()
                    with self.assertRaises(CONTRACT.DelegationContractError):
                        self.prepare(brief, baseline, current, main)

    def test_prepare_cli_output_can_be_evaluated_and_stale_evidence_is_not_relabelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief = valid_brief()
            result = result_fixture(brief)
            values = {"brief": brief, "result": result,
                      "main": brief["baseline"]["main_thread_identity"],
                      "authority": result["return_baseline"], "baseline-runtime": runtime_fixture(),
                      "current-runtime": dict(runtime_fixture(), core_sha256="2" * 64),
                      "reception": reception_context_fixture(brief, result)}
            paths = {}
            for name, value in values.items():
                paths[name] = root / (name + ".json")
                paths[name].write_text(json.dumps(value), encoding="utf-8")
            output = root / "runtime-context.json"
            command = [sys.executable, SCRIPT, "prepare-runtime-context", "--brief", paths["brief"],
                       "--baseline-runtime", paths["baseline-runtime"],
                       "--current-runtime", paths["current-runtime"],
                       "--current-main-identity", paths["main"], "--output", output]
            prepared = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            context = json.loads(prepared.stdout)
            self.assertEqual(context["current"]["core_sha256"], "2" * 64)
            evaluated = subprocess.run(
                [sys.executable, SCRIPT, "evaluate-result", "--brief", paths["brief"],
                 "--result", paths["result"], "--current-main-identity", paths["main"],
                 "--current-authority-identity", paths["authority"], "--runtime-context", output,
                 "--reception-context", paths["reception"]],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(evaluated.returncode, 3, evaluated.stderr)
            receipt = json.loads(evaluated.stdout)
            self.assertEqual(receipt["freshness"], "STALE")
            self.assertIsNone(receipt["consumption_record"])
            self.assertFalse(receipt["write_authorized"])
            self.assertEqual(subprocess.run(command, capture_output=True, check=False).returncode, 2)
            self.assertEqual(json.loads(output.read_text()), context)


class DelegationResultTests(unittest.TestCase):
    def test_invalid_alias_records_cannot_produce_consumption_records(self):
        for label, aliases in malformed_alias_cases(identity_fixture()):
            for position in ("return", "main", "authority", "all"):
                with self.subTest(label=label, position=position):
                    brief = valid_brief()
                    result = result_fixture(brief)
                    main = copy.deepcopy(brief["baseline"]["main_thread_identity"])
                    authority = copy.deepcopy(result["return_baseline"])
                    if position in {"return", "all"}:
                        result["return_baseline"]["aliases"] = copy.deepcopy(aliases)
                    if position in {"main", "all"}:
                        main["aliases"] = copy.deepcopy(aliases)
                    if position in {"authority", "all"}:
                        authority["aliases"] = copy.deepcopy(aliases)
                    if position == "all":
                        for identity in brief["baseline"].values():
                            if "aliases" in identity:
                                identity["aliases"] = copy.deepcopy(aliases)
                        result["source_baseline_sha256"] = CONTRACT.canonical_contract_digest(
                            brief["baseline"]["authority_identity"]
                        )
                    before = copy.deepcopy((brief, result, main, authority))
                    try:
                        evaluation = self.evaluate(brief, result, current_main=main, current_authority=authority)
                    except TypeError as error:
                        self.fail(f"Malformed alias must be rejected, not raise: {error}")
                    self.assertEqual(evaluation["decision"], "REJECTED")
                    self.assertIn(evaluation["freshness"], {"INCOMPLETE", "CONTRACT_VIOLATION"})
                    self.assertFalse(evaluation["consumable"])
                    self.assertIsNone(evaluation["consumption_record"])
                    self.assertFalse(evaluation["write_authorized"])
                    self.assertEqual((brief, result, main, authority), before)

    def test_complete_same_object_alias_can_enter_main_thread_review(self):
        brief = valid_brief()
        for name in ("main_thread_identity", "authority_identity"):
            identity = brief["baseline"][name]
            identity["aliases"] = [same_object_alias_fixture(identity)]
        result = result_fixture(brief)
        evaluation = self.evaluate(brief, result)
        self.assertTrue(evaluation["consumable"])
        self.assertFalse(evaluation["write_authorized"])

    def evaluate(
        self,
        brief,
        result,
        *,
        current_main=None,
        current_authority=None,
        consumed=frozenset(),
        runtime_context=None,
    ):
        current_main = current_main or copy.deepcopy(
            brief["baseline"]["main_thread_identity"]
        )
        current_authority = current_authority or copy.deepcopy(
            result["return_baseline"]
        )
        return CONTRACT.evaluate_delegation_result(
            brief,
            result,
            current_main_identity=current_main,
            current_authority_identity=current_authority,
            consumed_delegation_ids=consumed,
            runtime_context=(runtime_context_fixture(brief)
                             if runtime_context is None else runtime_context),
            reception_context=reception_context_fixture(brief, result),
        )

    def assert_decision(self, evaluation, freshness, decision, reasons):
        self.assertEqual(evaluation["freshness"], freshness)
        self.assertEqual(evaluation["decision"], decision)
        self.assertEqual(evaluation["reasons"], reasons)
        self.assertFalse(evaluation["write_authorized"])

    def test_fresh_read_only_result_enters_main_thread_review(self):
        brief = valid_brief()
        result = result_fixture(brief)

        evaluation = self.evaluate(brief, result)

        self.assert_decision(
            evaluation,
            "FRESH",
            "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
            [],
        )
        self.assertTrue(evaluation["consumable"])
        self.assertEqual(
            evaluation["consumption_record"],
            {
                "delegation_id": brief["delegation_id"],
                "brief_sha256": CONTRACT.canonical_contract_digest(brief),
                "result_sha256": CONTRACT.canonical_contract_digest(result),
                "return_baseline_sha256": CONTRACT.canonical_contract_digest(
                    result["return_baseline"]
                ),
                "runtime_context_sha256": CONTRACT.canonical_contract_digest(
                    runtime_context_fixture(brief)
                ),
                "reception_context_sha256": CONTRACT.canonical_contract_digest(
                    reception_context_fixture(brief, result)
                ),
            },
        )

    def test_missing_program_context_cannot_produce_consumption_record(self):
        brief = valid_brief()
        result = result_fixture(brief)
        evaluation = CONTRACT.evaluate_delegation_result(
            brief, result,
            current_main_identity=brief["baseline"]["main_thread_identity"],
            current_authority_identity=result["return_baseline"],
        )
        self.assertEqual(evaluation["freshness"], "INCOMPLETE")
        self.assertFalse(evaluation["consumable"])
        self.assertIsNone(evaluation["consumption_record"])
        self.assertFalse(evaluation["write_authorized"])

    def test_program_context_evaluation_is_pure_and_never_inherits_consumption(self):
        brief = valid_brief()
        result = result_fixture(brief)
        context = runtime_context_fixture(brief)
        before = copy.deepcopy((brief, result, context))
        with (
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("os.open", side_effect=AssertionError("filesystem read")),
            mock.patch("os.getenv", side_effect=AssertionError("environment probe")),
            mock.patch.object(project_identity, "collect_identity", side_effect=AssertionError("capture")),
        ):
            fresh = self.evaluate(brief, result, runtime_context=context)
            consumed = self.evaluate(brief, result, runtime_context=context,
                                     consumed=frozenset({brief["delegation_id"]}))
            changed = copy.deepcopy(context)
            changed["current"]["core_sha256"] = "2" * 64
            stale = self.evaluate(brief, result, runtime_context=changed,
                                  consumed=frozenset({brief["delegation_id"]}))
        self.assertEqual(fresh["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW")
        self.assertEqual(consumed["decision"], "ALREADY_CONSUMED")
        self.assertEqual(stale["freshness"], "STALE")
        for evaluation in (consumed, stale):
            self.assertFalse(evaluation["consumable"])
            self.assertIsNone(evaluation["consumption_record"])
        for evaluation in (fresh, consumed, stale):
            self.assertFalse(evaluation["write_authorized"])
        self.assertEqual((brief, result, context), before)

    def runtime_evaluation(self, brief, result, context):
        try:
            return self.evaluate(brief, result, runtime_context=context)
        except TypeError as error:
            self.fail(f"Runtime context must be evaluated, not raise: {error}")

    def test_program_version_and_fingerprint_drift_reject_consumption_in_all_modes(self):
        for mode in ("READ_ONLY", "REVIEW", "ISOLATED_WRITER"):
            for field, value in (("codex_version", "0.148.0"), ("core_sha256", "2" * 64)):
                with self.subTest(mode=mode, field=field):
                    brief = valid_brief(mode=mode)
                    result = result_fixture(brief)
                    context = runtime_context_fixture(brief)
                    context["current"][field] = value
                    before = copy.deepcopy((brief, result, context))
                    evaluation = self.runtime_evaluation(brief, result, context)
                    self.assertEqual(evaluation["freshness"], "STALE")
                    self.assertIn(f"FIELD_CHANGED:runtime.{field}", evaluation["reasons"])
                    self.assertFalse(evaluation["consumable"])
                    self.assertIsNone(evaluation["consumption_record"])
                    self.assertFalse(evaluation["write_authorized"])
                    self.assertEqual((brief, result, context), before)

    def test_invalid_runtime_evidence_and_wrong_brief_pin_reject_consumption(self):
        brief = valid_brief()
        result = result_fixture(brief)
        contexts = [[], {}, dict(runtime_context_fixture(brief), brief_sha256="0" * 64),
                    dict(runtime_context_fixture(brief), route_state="FRESH")]
        for invalid in malformed_runtime_evidence():
            for position in ("baseline", "current"):
                contexts.append(dict(runtime_context_fixture(brief), **{position: invalid}))
        for context in contexts:
            with self.subTest(context=context):
                evaluation = self.runtime_evaluation(brief, result, context)
                self.assertEqual(evaluation["freshness"], "INCOMPLETE")
                self.assertFalse(evaluation["consumable"])
                self.assertIsNone(evaluation["consumption_record"])
                self.assertFalse(evaluation["write_authorized"])

    def test_malformed_return_or_current_runtime_surface_rejects_consumption(self):
        for surface in malformed_runtime_surfaces():
            for position in ("return", "main", "authority"):
                with self.subTest(surface=surface, position=position):
                    brief = valid_brief()
                    result = result_fixture(brief)
                    main = copy.deepcopy(brief["baseline"]["main_thread_identity"])
                    authority = copy.deepcopy(result["return_baseline"])
                    target = {"return": result["return_baseline"], "main": main,
                              "authority": authority}[position]
                    target["runtime_surface"] = copy.deepcopy(surface)
                    try:
                        evaluation = self.evaluate(
                            brief, result, current_main=main, current_authority=authority,
                        )
                    except TypeError as error:
                        self.fail(f"Malformed runtime must fail closed, not raise: {error}")
                    self.assertFalse(evaluation["consumable"])
                    self.assertIsNone(evaluation["consumption_record"])
                    self.assertFalse(evaluation["write_authorized"])

    def test_result_accepts_matching_fresh_batch_delegation_id(self):
        brief = valid_brief()
        brief["delegation_id"] = "D-1.2-NATIVE-002/01"
        result = result_fixture(brief)

        validated = CONTRACT.validate_delegation_result(result)
        evaluation = self.evaluate(brief, result)

        self.assertEqual(validated["delegation_id"], brief["delegation_id"])
        self.assert_decision(
            evaluation,
            "FRESH",
            "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
            [],
        )

    def test_result_rejects_invalid_delegation_id_independently(self):
        brief = valid_brief()
        result = result_fixture(brief)
        result["delegation_id"] = "D-1.2-NATIVE-002/00"

        with self.assertRaises(CONTRACT.DelegationContractError) as caught:
            CONTRACT.validate_delegation_result(result)

        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_fresh_review_result_never_authorizes_write(self):
        brief = valid_brief(mode="REVIEW")
        result = result_fixture(brief)

        evaluation = self.evaluate(brief, result)

        self.assert_decision(
            evaluation,
            "FRESH",
            "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
            [],
        )
        self.assertTrue(evaluation["consumable"])

    def test_head_dirty_project_or_worktree_drift_is_rejected(self):
        brief = valid_brief()
        result = result_fixture(brief)
        cases = (
            (
                "main_head",
                "main",
                "head",
                "f" * 40,
                ["FIELD_CHANGED:project.head", "MAIN_BASELINE_STALE"],
            ),
            (
                "authority_dirty",
                "authority",
                "dirty_fingerprint",
                "1" * 64,
                [
                    "AUTHORITY_BASELINE_STALE",
                    "FIELD_CHANGED:project.dirty_fingerprint",
                ],
            ),
            (
                "main_project",
                "main",
                "physical_path",
                "/project/replaced",
                ["FIELD_CHANGED:project.physical_path", "MAIN_BASELINE_STALE"],
            ),
            (
                "authority_worktree",
                "authority",
                "worktree_id",
                "other-worktree",
                [
                    "AUTHORITY_BASELINE_STALE",
                    "FIELD_CHANGED:project.worktree_id",
                ],
            ),
        )
        for label, target, field, changed, reasons in cases:
            with self.subTest(label=label):
                current_main = copy.deepcopy(
                    brief["baseline"]["main_thread_identity"]
                )
                current_authority = copy.deepcopy(result["return_baseline"])
                if target == "main":
                    current_main[field] = changed
                else:
                    current_authority[field] = changed
                evaluation = self.evaluate(
                    brief,
                    result,
                    current_main=current_main,
                    current_authority=current_authority,
                )
                self.assert_decision(
                    evaluation,
                    "STALE",
                    "REJECTED",
                    reasons,
                )

    def test_incomplete_or_blocked_return_identity_is_rejected(self):
        brief = valid_brief()
        result = result_fixture(brief)
        result["return_baseline"]["write_eligibility"] = "BLOCKED"
        current = copy.deepcopy(result["return_baseline"])

        evaluation = self.evaluate(
            brief,
            result,
            current_authority=current,
        )

        self.assert_decision(
            evaluation,
            "INCOMPLETE",
            "REJECTED",
            [
                "AUTHORITY_IDENTITY_INCOMPLETE",
                "WRITE_ELIGIBILITY_NOT_ELIGIBLE:baseline",
                "WRITE_ELIGIBILITY_NOT_ELIGIBLE:current",
            ],
        )

    def test_source_digest_mismatch_is_contract_violation(self):
        brief = valid_brief()
        result = result_fixture(brief)
        result["source_baseline_sha256"] = "0" * 64

        evaluation = self.evaluate(brief, result)

        self.assert_decision(
            evaluation,
            "CONTRACT_VIOLATION",
            "REJECTED",
            ["RESULT_BRIEF_MISMATCH"],
        )

    def test_budget_overrun_is_contract_violation(self):
        brief = valid_brief()
        result = result_fixture(brief)
        result["usage"]["model_calls"] = 2

        evaluation = self.evaluate(brief, result)

        self.assert_decision(
            evaluation,
            "CONTRACT_VIOLATION",
            "REJECTED",
            ["RESULT_BUDGET_EXCEEDED"],
        )

    def test_unknown_tokens_remain_literal_unknown(self):
        brief = valid_brief()
        result = result_fixture(brief)
        self.assertEqual(result["usage"]["observed_tokens"], "UNKNOWN")

        evaluation = self.evaluate(brief, result)

        self.assert_decision(
            evaluation,
            "FRESH",
            "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
            [],
        )

    def test_nested_delegation_or_external_action_is_contract_violation(self):
        brief = valid_brief()
        cases = (
            ("nested_delegation_attempted", "RESULT_NESTED_DELEGATION"),
            ("external_action_attempted", "RESULT_EXTERNAL_ACTION"),
        )
        for field, reason in cases:
            with self.subTest(field=field):
                result = result_fixture(brief)
                result[field] = True
                evaluation = self.evaluate(brief, result)
                self.assert_decision(
                    evaluation,
                    "CONTRACT_VIOLATION",
                    "REJECTED",
                    [reason],
                )

    def test_read_only_modified_files_or_candidate_is_scope_violation(self):
        brief = valid_brief()
        cases = (
            ("files_modified", ["src/input.py"]),
            (
                "candidate",
                {
                    "parent_head": "a" * 40,
                    "head": "a" * 40,
                    "commit": None,
                    "diff_sha256": "1" * 64,
                    "changed_files": [],
                },
            ),
        )
        for field, changed in cases:
            with self.subTest(field=field):
                result = result_fixture(brief)
                result[field] = changed
                evaluation = self.evaluate(brief, result)
                self.assert_decision(
                    evaluation,
                    "CONTRACT_VIOLATION",
                    "REJECTED",
                    ["RESULT_SCOPE_VIOLATION"],
                )

    def test_result_rejects_unlisted_read_or_command(self):
        brief = valid_brief()
        cases = (
            ("files_read", ["src/not-allowed.py"]),
            ("commands", [["python3", "unlisted.py"]]),
        )
        for field, changed in cases:
            with self.subTest(field=field):
                result = result_fixture(brief)
                result[field] = changed
                evaluation = self.evaluate(brief, result)
                self.assert_decision(
                    evaluation,
                    "CONTRACT_VIOLATION",
                    "REJECTED",
                    ["RESULT_SCOPE_VIOLATION"],
                )

    def test_finding_test_usage_and_timestamp_shapes_are_strict(self):
        brief = valid_brief()
        cases = (
            (
                "finding_line",
                lambda value: value.__setitem__(
                    "findings",
                    [
                        {
                            "finding_id": "F-01",
                            "severity": "HIGH",
                            "summary": "Invalid line.",
                            "evidence": ["Synthetic evidence."],
                            "file": "src/input.py",
                            "line": 0,
                        }
                    ],
                ),
                "FIELD_VALUE_INVALID",
            ),
            (
                "test_exit_code",
                lambda value: value["tests"][0].__setitem__("exit_code", True),
                "FIELD_TYPE_INVALID",
            ),
            (
                "usage_bool",
                lambda value: value["usage"].__setitem__("turns", False),
                "FIELD_TYPE_INVALID",
            ),
            (
                "timestamp",
                lambda value: value.__setitem__(
                    "returned_at_utc", "2026-08-25T01:02:03+00:00"
                ),
                "FIELD_VALUE_INVALID",
            ),
        )
        for label, mutate, reason in cases:
            with self.subTest(label=label):
                result = result_fixture(brief)
                mutate(result)
                evaluation = self.evaluate(brief, result)
                self.assert_decision(
                    evaluation,
                    "CONTRACT_VIOLATION",
                    "REJECTED",
                    [reason],
                )

    def test_writer_candidate_accepts_only_isolated_allowlisted_changes(self):
        brief = valid_brief(mode="ISOLATED_WRITER")
        result = result_fixture(brief)

        evaluation = self.evaluate(brief, result)

        self.assert_decision(
            evaluation,
            "FRESH",
            "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
            [],
        )
        self.assertTrue(evaluation["consumable"])

    def test_writer_parent_or_main_baseline_drift_is_rejected(self):
        brief = valid_brief(mode="ISOLATED_WRITER")

        wrong_parent = result_fixture(brief)
        wrong_parent["candidate"]["parent_head"] = "f" * 40
        evaluation = self.evaluate(brief, wrong_parent)
        self.assert_decision(
            evaluation,
            "CONTRACT_VIOLATION",
            "REJECTED",
            ["RESULT_BRIEF_MISMATCH"],
        )

        result = result_fixture(brief)
        current_main = copy.deepcopy(brief["baseline"]["main_thread_identity"])
        current_main["head"] = "e" * 40
        evaluation = self.evaluate(
            brief,
            result,
            current_main=current_main,
        )
        self.assert_decision(
            evaluation,
            "STALE",
            "REJECTED",
            ["FIELD_CHANGED:project.head", "MAIN_BASELINE_STALE"],
        )

    def test_consumed_delegation_id_cannot_be_consumed_twice(self):
        brief = valid_brief()
        result = result_fixture(brief)

        evaluation = self.evaluate(
            brief,
            result,
            consumed=frozenset({brief["delegation_id"]}),
        )

        self.assert_decision(
            evaluation,
            "FRESH",
            "ALREADY_CONSUMED",
            ["DELEGATION_ALREADY_CONSUMED"],
        )
        self.assertFalse(evaluation["consumable"])
        self.assertIsNone(evaluation["consumption_record"])


class DelegationCliTests(unittest.TestCase):
    def write_json(self, directory, name, value):
        path = Path(directory) / name
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def run_cli(self, *arguments, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def canonical_text(self, value):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"

    def evaluation_paths(self, directory, *, stale=False):
        brief = valid_brief()
        result = result_fixture(brief)
        main = copy.deepcopy(brief["baseline"]["main_thread_identity"])
        authority = copy.deepcopy(result["return_baseline"])
        if stale:
            main["head"] = "f" * 40
        return (
            brief,
            result,
            self.write_json(directory, "brief.json", brief),
            self.write_json(directory, "result.json", result),
            self.write_json(directory, "main.json", main),
            self.write_json(directory, "authority.json", authority),
        )

    def test_validate_brief_cli_emits_canonical_json(self):
        brief = valid_brief()
        with tempfile.TemporaryDirectory() as directory:
            brief_path = self.write_json(directory, "brief.json", brief)
            output_path = Path(directory) / "receipt.json"

            completed = self.run_cli(
                "validate-brief",
                "--brief",
                brief_path,
                "--output",
                output_path,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, self.canonical_text(brief))
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                completed.stdout,
            )
            self.assertEqual(completed.stderr, "")

    def test_evaluate_result_cli_returns_zero_only_for_fresh_reviewable_result(self):
        with tempfile.TemporaryDirectory() as directory:
            brief, result, brief_path, result_path, main_path, authority_path = (
                self.evaluation_paths(directory)
            )
            runtime_path = self.write_json(directory, "runtime.json", runtime_context_fixture(brief))
            reception_path = self.write_json(directory, "reception.json", reception_context_fixture(brief, result))

            completed = self.run_cli(
                "evaluate-result",
                "--brief",
                brief_path,
                "--result",
                result_path,
                "--current-main-identity",
                main_path,
                "--current-authority-identity",
                authority_path,
                "--runtime-context",
                runtime_path,
                "--reception-context",
                reception_path,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["freshness"], "FRESH")
            self.assertEqual(
                receipt["decision"],
                "ACCEPTED_FOR_MAIN_THREAD_REVIEW",
            )
            self.assertFalse(receipt["write_authorized"])

    def test_evaluate_result_cli_returns_three_for_stale_result(self):
        with tempfile.TemporaryDirectory() as directory:
            brief, result, brief_path, result_path, main_path, authority_path = (
                self.evaluation_paths(directory, stale=True)
            )
            runtime_path = self.write_json(directory, "runtime.json", runtime_context_fixture(brief))
            reception_path = self.write_json(directory, "reception.json", reception_context_fixture(brief, result))

            completed = self.run_cli(
                "evaluate-result",
                "--brief",
                brief_path,
                "--result",
                result_path,
                "--current-main-identity",
                main_path,
                "--current-authority-identity",
                authority_path,
                "--runtime-context",
                runtime_path,
                "--reception-context",
                reception_path,
            )

            self.assertEqual(completed.returncode, 3, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["freshness"], "STALE")
            self.assertEqual(receipt["decision"], "REJECTED")
            self.assertEqual(
                receipt["reasons"],
                ["FIELD_CHANGED:project.head", "MAIN_BASELINE_STALE"],
            )

    def test_cli_rejects_missing_invalid_or_drifted_runtime_context(self):
        with tempfile.TemporaryDirectory() as directory:
            brief, result, brief_path, result_path, main_path, authority_path = self.evaluation_paths(directory)
            reception_path = self.write_json(directory, "reception.json", reception_context_fixture(brief, result))
            arguments = ["evaluate-result", "--brief", brief_path, "--result", result_path,
                         "--current-main-identity", main_path, "--current-authority-identity", authority_path,
                         "--reception-context", reception_path]
            version = runtime_context_fixture(brief)
            version["current"]["codex_version"] = "0.148.0"
            binary = runtime_context_fixture(brief)
            binary["current"]["core_sha256"] = "2" * 64
            for context, expected in (
                (None, "INCOMPLETE"), ({}, "INCOMPLETE"),
                (version, "STALE"), (binary, "STALE"),
                (dict(runtime_context_fixture(brief), brief_sha256="0" * 64), "INCOMPLETE"),
            ):
                with self.subTest(context=context):
                    runtime_path = self.write_json(directory, "runtime.json", context)
                    extra = [] if context is None else ["--runtime-context", runtime_path]
                    completed = self.run_cli(*arguments, *extra)
                    self.assertEqual(completed.returncode, 3, completed.stderr)
                    receipt = json.loads(completed.stdout)
                    self.assertEqual(receipt["freshness"], expected)
                    self.assertFalse(receipt["consumable"])
                    self.assertIsNone(receipt["consumption_record"])
                    self.assertFalse(receipt["write_authorized"])
            alias = Path(directory) / "runtime-alias.json"
            alias.symlink_to(runtime_path)
            self.assertEqual(self.run_cli(*arguments, "--runtime-context", alias).returncode, 2)

    def test_cli_rejects_symlink_nonregular_oversize_and_replaced_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular.json"
            regular.write_text("{}", encoding="utf-8")
            symlink = root / "symlink.json"
            symlink.symlink_to(regular)
            oversize = root / "oversize.json"
            oversize.write_bytes(b"x" * (CONTRACT.MAX_INPUT_BYTES + 1))

            for label, path in (
                ("symlink", symlink),
                ("nonregular", root),
                ("oversize", oversize),
            ):
                with self.subTest(label=label):
                    with self.assertRaises(
                        CONTRACT.DelegationContractError
                    ) as caught:
                        CONTRACT._read_json_regular(path)
                    self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

            before = SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_size=2,
                st_dev=1,
                st_ino=2,
                st_mtime_ns=3,
                st_ctime_ns=4,
            )
            after = SimpleNamespace(
                st_mode=before.st_mode,
                st_size=before.st_size,
                st_dev=before.st_dev,
                st_ino=9,
                st_mtime_ns=before.st_mtime_ns,
                st_ctime_ns=before.st_ctime_ns,
            )
            with mock.patch.object(CONTRACT.os, "fstat", side_effect=[before, after]):
                with self.assertRaises(CONTRACT.DelegationContractError) as caught:
                    CONTRACT._read_json_regular(regular)
            self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_cli_output_is_exclusive_and_rejects_symlink_or_existing_path(self):
        brief = valid_brief()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief_path = self.write_json(root, "brief.json", brief)
            existing = root / "existing.json"
            existing.write_text("do-not-overwrite", encoding="utf-8")
            target = root / "target.json"
            target.write_text("do-not-follow", encoding="utf-8")
            symlink = root / "output-link.json"
            symlink.symlink_to(target)

            for label, output in (("existing", existing), ("symlink", symlink)):
                with self.subTest(label=label):
                    completed = self.run_cli(
                        "validate-brief",
                        "--brief",
                        brief_path,
                        "--output",
                        output,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(
                        completed.stderr,
                        "delegation contract rejected: FIELD_VALUE_INVALID\n",
                    )
                    self.assertEqual(completed.stdout, "")
            self.assertEqual(existing.read_text(encoding="utf-8"), "do-not-overwrite")
            self.assertEqual(target.read_text(encoding="utf-8"), "do-not-follow")

    def test_cli_stderr_contains_reason_without_input_content(self):
        secret_marker = "TOP-SECRET-CONTENT"
        with tempfile.TemporaryDirectory() as directory:
            invalid = self.write_json(
                directory,
                "private-input.json",
                {"secret": secret_marker},
            )

            completed = self.run_cli("validate-brief", "--brief", invalid)

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                "delegation contract rejected: SCHEMA_FIELDS_CHANGED\n",
            )
            self.assertNotIn(secret_marker, completed.stderr)
            self.assertNotIn(str(invalid), completed.stderr)
            self.assertEqual(completed.stdout, "")

            private_path = Path(directory) / "PRIVATE-PATH-MARKER.json"
            completed = self.run_cli(
                "validate-brief",
                "--brief",
                invalid,
                "--unexpected-private-path",
                private_path,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                "delegation contract rejected: FIELD_VALUE_INVALID\n",
            )
            self.assertNotIn(str(private_path), completed.stderr)
            self.assertNotIn(str(invalid), completed.stderr)

    def test_cli_does_not_create_process_or_read_project_or_codex_home(self):
        brief = valid_brief()
        sentinel = "/sentinel/codex-home-must-not-be-read"
        original_getenv = os.getenv

        def guarded_getenv(key, default=None):
            if key == "CODEX_HOME":
                raise AssertionError("CODEX_HOME was read")
            return original_getenv(key, default)

        with tempfile.TemporaryDirectory() as directory:
            brief_path = self.write_json(directory, "brief.json", brief)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": sentinel}),
                mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
                mock.patch("os.system", side_effect=AssertionError("system")),
                mock.patch("os.getenv", side_effect=guarded_getenv),
                mock.patch.object(
                    project_identity,
                    "collect_identity",
                    side_effect=AssertionError("project discovery"),
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = CONTRACT.main(
                    ["validate-brief", "--brief", str(brief_path)]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue(), self.canonical_text(brief))
            self.assertEqual(stderr.getvalue(), "")
            self.assertNotIn(sentinel, stdout.getvalue())

    def test_import_is_filesystem_and_process_free(self):
        with (
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("os.system", side_effect=AssertionError("system")),
            mock.patch.object(
                project_identity,
                "collect_identity",
                side_effect=AssertionError("project discovery"),
            ),
        ):
            reloaded = importlib.reload(CONTRACT)

        self.assertEqual(reloaded.BRIEF_SCHEMA_ID, CONTRACT.BRIEF_SCHEMA_ID)


if __name__ == "__main__":
    unittest.main()
