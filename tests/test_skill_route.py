# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import skill_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
CORE = "vibe-project-lead-zh"
TARGET = "plugin:fixture-helper"


class SkillRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vibe-b21-route-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = str(self.root / "project")
        self.sequence = 0
        self.core = self.skill(CORE)
        self.target = self.skill(TARGET)
        self.response = self.response_for([self.core, self.target])

    def skill(self, name):
        self.sequence += 1
        path = self.root / str(self.sequence) / "SKILL.md"
        path.parent.mkdir()
        path.write_text(
            "---\nname: " + name.rsplit(":", 1)[-1] + "\n"
            "description: synthetic fixture\n---\nFixture instructions.\n",
            encoding="utf-8",
        )
        return {"name": name, "path": str(path), "enabled": True,
                "scope": "repo", "description": "synthetic"}

    def response_for(self, skills, errors=None):
        return {"result": {"data": [{
            "cwd": self.cwd, "skills": copy.deepcopy(skills),
            "errors": copy.deepcopy(errors or []),
        }]}}

    def check(self, response=None, **kwargs):
        return inventory.assess_skill_route(
            self.response if response is None else response,
            expected_cwd=self.cwd, **kwargs,
        )

    def test_core_only_requires_no_optional_plugin(self):
        result = self.check(self.response_for([self.core]))
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual([item["skill_id"] for item in result["verified"]], [CORE])
        self.assertFalse(result["write_authorized"])
        self.assertFalse(result["instructions_loaded"])
        self.assertEqual(result["freshness"], "NOT_VERIFIED")
        self.assertEqual(result["inventory_completeness"], "NOT_CAPTURED")
        self.assertNotIn("inventory_sha256", result)

    def test_unindexed_target_is_verified_by_current_identity(self):
        result = self.check(target_ids=[TARGET])
        self.assertEqual(result["status"], "VERIFIED")
        actual = next(item for item in result["verified"] if item["skill_id"] == TARGET)
        self.assertEqual(actual["source_namespace"], "plugin")
        self.assertEqual(actual["declared_name"], "fixture-helper")
        self.assertEqual(actual["locator"], self.target["path"])
        self.assertEqual(actual["locator_sha256"],
                         hashlib.sha256(Path(self.target["path"]).read_bytes()).hexdigest())

    def test_unrelated_unreadable_locator_is_not_opened(self):
        other = self.skill("optional:fixture-other")
        Path(other["path"]).unlink()
        other["enabled"] = False
        response = self.response_for(
            [self.core, self.target, other],
            [{"path": other["path"], "message": "synthetic load failure"}],
        )
        reads = []
        original = inventory.read_locator_snapshot
        def observed_read(path):
            reads.append(path)
            return original(path)
        with patch.object(inventory, "read_locator_snapshot", side_effect=observed_read):
            result = self.check(response, target_ids=[TARGET])
        self.assertEqual(result["status"], "VERIFIED")
        self.assertCountEqual(reads, [self.core["path"], self.target["path"]])
        self.assertEqual(result["unrelated_findings"], [{
            "skill_id": other["name"], "reasons": ["DISABLED", "LOAD_ERROR"],
        }])

    def test_missing_required_and_explicit_targets_are_blocked(self):
        for kwargs, missing in (
            ({"target_ids": ["missing-target"]}, "missing-target"),
            ({"required_ids": ["host-required"]}, "host-required"),
        ):
            with self.subTest(kwargs=kwargs):
                result = self.check(**kwargs)
                self.assertEqual(result["status"], "BLOCKED_ROUTE")
                self.assertIn({"skill_id": missing, "reason": "MISSING"}, result["blocked"])

    def test_core_anomalies_block_supervisor(self):
        for condition in ("missing", "disabled", "duplicate", "load-error"):
            with self.subTest(condition=condition):
                skills = copy.deepcopy([self.core, self.target])
                errors = []
                if condition == "missing":
                    skills.pop(0)
                elif condition == "disabled":
                    skills[0]["enabled"] = False
                elif condition == "duplicate":
                    skills.append(copy.deepcopy(skills[0]))
                else:
                    errors.append({"path": self.core["path"], "message": "synthetic"})
                result = self.check(self.response_for(skills, errors), target_ids=[TARGET])
                self.assertEqual(result["status"], "BLOCKED_SUPERVISOR")

    def test_target_anomalies_block_only_route(self):
        for condition in ("missing", "disabled", "duplicate", "load-error"):
            with self.subTest(condition=condition):
                skills = copy.deepcopy([self.core, self.target])
                errors = []
                if condition == "missing":
                    skills.pop()
                elif condition == "disabled":
                    skills[1]["enabled"] = False
                elif condition == "duplicate":
                    skills.append(copy.deepcopy(skills[1]))
                else:
                    errors.append({"path": self.target["path"], "message": "synthetic"})
                result = self.check(self.response_for(skills, errors), target_ids=[TARGET])
                self.assertEqual(result["status"], "BLOCKED_ROUTE")
                self.assertIn(CORE, [item["skill_id"] for item in result["verified"]])

    def test_bad_current_cwd_or_enabled_never_passes(self):
        wrong_cwd = copy.deepcopy(self.response)
        wrong_cwd["result"]["data"][0]["cwd"] = self.cwd + "-other"
        duplicate_cwd = copy.deepcopy(self.response)
        duplicate_cwd["result"]["data"].append(copy.deepcopy(duplicate_cwd["result"]["data"][0]))
        cases = [wrong_cwd, duplicate_cwd, {"result": {"data": []}}]
        for bad in ("false", 1, None):
            value = copy.deepcopy(self.response)
            value["result"]["data"][0]["skills"][1]["enabled"] = bad
            cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(inventory.ProtocolError):
                    self.check(value, target_ids=[TARGET])

    def test_unknown_error_owner_never_becomes_nonblocking(self):
        result = self.check(self.response_for(
            [self.core, self.target],
            [{"path": str(self.root / "unknown" / "SKILL.md"), "message": "synthetic"}],
        ), target_ids=[TARGET])
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["unattributed_error_count"], 1)
        self.assertFalse(result["write_authorized"])

    def test_selected_locator_pin_and_declared_identity_are_enforced(self):
        result = self.check(target_ids=[TARGET],
                            expected_locators={TARGET: str(self.root / "old" / "SKILL.md")})
        self.assertEqual(result["status"], "BLOCKED_ROUTE")
        self.assertIn({"skill_id": TARGET, "reason": "LOCATOR_CHANGED"}, result["blocked"])
        Path(self.target["path"]).write_text(
            "---\nname: wrong-name\ndescription: synthetic\n---\n", encoding="utf-8")
        result = self.check(target_ids=[TARGET])
        self.assertEqual(result["status"], "BLOCKED_ROUTE")
        self.assertIn({"skill_id": TARGET, "reason": "LOCATOR_UNVERIFIED"}, result["blocked"])

    def test_equal_declared_names_in_distinct_namespaces_remain_distinct(self):
        other = self.skill("another:fixture-helper")
        result = self.check(self.response_for([self.core, self.target, other]),
                            target_ids=[TARGET, other["name"]])
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(len(result["verified"]), 3)

    def test_namespace_rename_cannot_satisfy_selected_old_id(self):
        response = copy.deepcopy(self.response)
        response["result"]["data"][0]["skills"][1]["name"] = "renamed:fixture-helper"
        result = self.check(response, target_ids=[TARGET])
        self.assertEqual(result["status"], "BLOCKED_ROUTE")
        self.assertIn({"skill_id": TARGET, "reason": "MISSING"}, result["blocked"])

    def test_response_and_file_bytes_are_not_mutated(self):
        before = copy.deepcopy(self.response)
        files = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.check(target_ids=[TARGET])
        self.assertEqual(self.response, before)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, files)

    def test_selected_nul_locator_is_blocked_without_crashing(self):
        response = copy.deepcopy(self.response)
        response["result"]["data"][0]["skills"][1]["path"] = str(self.root / "bad\x00" / "SKILL.md")
        result = self.check(response, target_ids=[TARGET])
        self.assertEqual(result["status"], "BLOCKED_ROUTE")
        self.assertIn({"skill_id": TARGET, "reason": "LOCATOR_UNVERIFIED"}, result["blocked"])

    def run_cli(self, response, *extra):
        path = self.root / "input.json"
        if isinstance(response, bytes):
            path.write_bytes(response)
        else:
            path.write_text(json.dumps(response), encoding="utf-8")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = subprocess.run(
            [sys.executable, "-B", "-m", "workbench.skill_route", "--input", str(path),
             "--cwd", self.cwd, *extra],
            cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, check=False, timeout=15,
        )
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        return result

    def test_cli_invalid_utf8_returns_unknown_json_without_traceback(self):
        result = self.run_cli(b"\xff")
        self.assertEqual(result.returncode, 4, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "UNKNOWN")
        self.assertFalse(output["write_authorized"])
        self.assertFalse(output["instructions_loaded"])
        self.assertEqual(result.stderr, "")

    def test_cli_exit_codes_and_json_boundary(self):
        for response, extra, expected in (
            (self.response, ["--target", TARGET], (0, "VERIFIED")),
            (self.response, ["--require", "missing"], (3, "BLOCKED_ROUTE")),
            (self.response_for([self.target]), [], (3, "BLOCKED_SUPERVISOR")),
            (self.response_for([self.core], [{"path": "unknown", "message": "synthetic"}]),
             [], (4, "UNKNOWN")),
        ):
            with self.subTest(expected=expected):
                result = self.run_cli(response, *extra)
                self.assertEqual(result.returncode, expected[0], result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["status"], expected[1])
                self.assertFalse(output["write_authorized"])
                self.assertFalse(output["instructions_loaded"])

    def test_cli_rejects_malformed_input_pins_and_query_mode(self):
        cases = (
            ({}, [], 4),
            (self.response, ["--expect-locator", "bad"], 4),
            (self.response, ["--expect-locator", "unknown=/fixture/SKILL.md"], 4),
            (self.response, ["--query"], 2),
        )
        for response, extra, expected in cases:
            with self.subTest(extra=extra):
                result = self.run_cli(response, *extra)
                self.assertEqual(result.returncode, expected)
