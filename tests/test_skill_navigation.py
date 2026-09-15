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

from workbench import skill_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
CORE = "vibe-project-lead-zh"
TARGET = "fixture:helper"


class SkillNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vibe-b22-route-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = {
            "schema_version": 2, "skills": {},
            "primary_router": "legacy-router",
            "mandatory": {"before_any_action": {"ids": ["legacy-required"]}},
            "routing": {
                "core_ids": [CORE],
                "capabilities": [{
                    "label_zh": "合成能力", "purpose_zh": "可选扩展示例",
                    "example_ids": [TARGET],
                }],
            },
        }

    def response(self):
        skills = []
        for name in (CORE, TARGET):
            folder = self.root / name.replace(":", "-")
            folder.mkdir(exist_ok=True)
            locator = folder / "SKILL.md"
            locator.write_text(
                "---\nname: " + name.rsplit(":", 1)[-1]
                + "\ndescription: synthetic fixture\n---\nFixture.\n",
                encoding="utf-8",
            )
            skills.append({"name": name, "path": str(locator), "scope": "repo", "enabled": True})
        return {"result": {"data": [{"cwd": str(self.root), "skills": skills, "errors": []}]}}

    def command(self, *args):
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = subprocess.run(
            [sys.executable, "-B", "-m", "workbench.skill_route", *args],
            cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        return result

    def test_core_only_does_not_import_full_audit_requirements(self):
        selected = inventory.select_route_dependencies(self.policy)
        self.assertEqual(selected["target_ids"], [])
        self.assertEqual(selected["required_ids"], [CORE])
        self.assertEqual(selected["selection_basis"], {
            "product_core": [CORE], "user_targets": [], "current_requirements": [],
        })
        self.assertEqual(selected["requirement_completeness"], "CALLER_DECLARED")
        self.assertEqual(inventory.required_skill_ids(self.policy),
                         {"legacy-router", "legacy-required"})

    def test_targets_and_current_requirements_are_never_demoted_to_examples(self):
        selected = inventory.select_route_dependencies(
            self.policy, target_ids=[TARGET], required_ids=["host:gate"],
        )
        self.assertEqual(selected["target_ids"], [TARGET])
        self.assertEqual(selected["required_ids"], ["host:gate", CORE])
        self.assertEqual(selected["selection_basis"]["current_requirements"], ["host:gate"])
        report = inventory.assess_skill_route(
            self.response(), expected_cwd=str(self.root),
            target_ids=selected["target_ids"], required_ids=selected["required_ids"],
        )
        self.assertEqual(report["status"], "BLOCKED_ROUTE")
        self.assertIn({"skill_id": "host:gate", "reason": "MISSING"}, report["blocked"])
        self.assertFalse(report["write_authorized"])

    def test_current_route_policy_cannot_remove_or_replace_supervisor(self):
        for core in ([], ["other"], [CORE, "optional:plugin"]):
            with self.subTest(core=core):
                policy = copy.deepcopy(self.policy)
                policy["routing"]["core_ids"] = core
                with self.assertRaises(inventory.ProtocolError):
                    inventory.select_route_dependencies(policy)
        with self.assertRaises(inventory.ProtocolError):
            inventory.select_route_dependencies({})

    def test_selection_preserves_input_and_rejects_malformed_ids(self):
        before = copy.deepcopy(self.policy)
        inventory.select_route_dependencies(self.policy, target_ids=[TARGET, TARGET])
        self.assertEqual(self.policy, before)
        for bad in (None, "single-id", [""], ["bad::name"], ["with space"], [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(inventory.ProtocolError):
                    inventory.select_route_dependencies(self.policy, required_ids=bad)

    def test_navigation_output_ignores_legacy_inventory_and_locator_fields(self):
        expected = inventory.render_stable_navigation(self.policy)
        altered = copy.deepcopy(self.policy)
        altered["skills"] = {"private-skill": {"locator": "/private/sentinel/SKILL.md"}}
        altered["generated_at_utc"] = "SECRET-TIMESTAMP"
        altered["cwd"] = "/private/sentinel"
        altered["routing"]["capabilities"][0]["locator"] = "/private/sentinel/SKILL.md"
        self.assertEqual(inventory.render_stable_navigation(altered), expected)
        self.assertNotIn("private-skill", expected)
        self.assertNotIn("/private/sentinel", expected)
        self.assertIn(TARGET, expected)
        self.assertNotIn("legacy-required", expected)

    def test_navigation_rejects_unusable_capability_records(self):
        for bad in ([], None, ["text"], [{}], [{
            "label_zh": "name", "purpose_zh": "purpose", "example_ids": [],
        }]):
            with self.subTest(bad=bad):
                policy = copy.deepcopy(self.policy)
                policy["routing"]["capabilities"] = bad
                with self.assertRaises(inventory.ProtocolError):
                    inventory.render_stable_navigation(policy)

    def test_cli_routes_report_requirement_provenance_and_keep_missing_gate_blocked(self):
        response = self.response()
        path = self.root / "response.json"
        path.write_text(json.dumps(response), encoding="utf-8")
        result = self.command("--input", str(path), "--cwd", str(self.root),
                              "--target", TARGET, "--require", "host:gate")
        self.assertEqual(result.returncode, 3, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["selection_basis"], {
            "product_core": [CORE], "user_targets": [TARGET],
            "current_requirements": ["host:gate"],
        })
        self.assertEqual(output["requirement_completeness"], "CALLER_DECLARED")
        self.assertEqual(output["freshness"], "NOT_VERIFIED")
        self.assertEqual(output["inventory_completeness"], "NOT_CAPTURED")
        self.assertFalse(output["instructions_loaded"])
        self.assertFalse(output["write_authorized"])

    def test_cli_navigation_needs_no_discovery_and_cannot_mix_route_or_query(self):
        result = self.command("--navigation")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, inventory.render_stable_navigation(
            inventory.load_policy(ROOT / "workbench" / "policy.toml"),
        ))
        for extra in (["--cwd", str(self.root)], ["--target", TARGET],
                      ["--require", "host:gate"], ["--query"]):
            with self.subTest(extra=extra):
                result = self.command("--navigation", *extra)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_packaged_navigation_matches_static_renderer_without_private_snapshot(self):
        actual = (ROOT / "skill" / CORE / "references" / "SKILL_INDEX_ZH.md").read_text()
        expected = inventory.render_stable_navigation(
            inventory.load_policy(ROOT / "workbench" / "policy.toml"),
        )
        self.assertEqual(actual, expected)
        self.assertNotIn("/mnt/c/Users/", actual)
        self.assertNotIn("/home/pc/", actual)
        self.assertNotIn("generated_at_utc:", actual)
        self.assertNotIn("70 / 70", actual)

    def test_full_audit_required_set_is_unchanged_by_routing_configuration(self):
        original = inventory.required_skill_ids(self.policy)
        altered = copy.deepcopy(self.policy)
        altered["routing"]["core_ids"] = ["irrelevant:other"]
        altered["routing"]["required"] = {"ids": ["not-a-full-audit-requirement"]}
        self.assertEqual(inventory.required_skill_ids(altered), original)
