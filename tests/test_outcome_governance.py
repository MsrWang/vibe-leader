# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import hashlib
import json
import unittest

from workbench import outcome_governance as governance
from workbench.safe_public_text import render_safe_public_message


PROJECTION_EQUIVALENT_DANGERS = (
    "ｈｔｔｐｓ：／／example.com/private",
    "git＠example.com：private/repo.git",
    "Ｂｅａｒｅｒ private-token",
    "Co\u200bokie: private-token",
    "ＦＥＡＴＵＲＥ＿ＦＬＡＧ＝private-value",
    "di\u200bff --git a/private b/private",
    "＠＠ -1 +1 ＠＠",
    "？？ private.txt",
    "［ｘ］ private task",
    "｀private inline value｀",
)


def valid_contract():
    return {
        "schema_version": 1,
        "task_id": "TASK-2.2-DEMO",
        "revision": 1,
        "original_goal": "把库存同步功能完整迁移到目标环境",
        "safe_goal_summary": "完成库存同步迁移并验证目标环境结果",
        "observable_outcomes": [
            {
                "outcome_id": "O1",
                "description": "目标环境能同步库存并显示成功结果",
                "critical": True,
                "capability_ids": ["C1"],
            }
        ],
        "capabilities": [
            {
                "capability_id": "C1",
                "description": "目标环境执行真实库存同步",
                "critical": True,
                "status": "VERIFIED",
                "evidence_requirement_ids": ["E1"],
                "evidence_references": ["uat:inventory-sync:pass"],
            }
        ],
        "non_goals": ["自动发布到生产"],
        "evidence_requirements": [
            {
                "evidence_id": "E1",
                "category": "DELIVERY",
                "description": "目标环境真实场景观察",
            }
        ],
        "accepted_limitations": [],
    }


def nested_value(depth):
    value = "leaf"
    for _ in range(depth):
        value = {"layer": value}
    return value


class OutcomeContractTests(unittest.TestCase):
    def assert_rejected(self, value, reason):
        with self.assertRaises(governance.OutcomeContractError) as caught:
            governance.validate_outcome_contract(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_accepts_exact_contract_without_mutating_input(self):
        value = valid_contract()
        before = copy.deepcopy(value)

        result = governance.validate_outcome_contract(value)

        self.assertEqual(result, before)
        self.assertIsNot(result, value)
        result["capabilities"][0]["status"] = "MISSING"
        self.assertEqual(value, before)

    def test_parse_rejects_duplicate_keys_bom_and_invalid_json(self):
        cases = (
            ('{"schema_version":1,"schema_version":1}', "SCHEMA_FIELDS_CHANGED"),
            ("\ufeff{}", "JSON_INVALID"),
            ("not-json", "JSON_INVALID"),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(governance.OutcomeContractError) as caught:
                    governance.parse_outcome_json(payload)
                self.assertEqual(caught.exception.reason, reason)

    def test_rejects_non_object_unsupported_schema_and_unknown_fields(self):
        self.assert_rejected([], "INPUT_NOT_OBJECT")
        value = valid_contract()
        value["schema_version"] = 2
        self.assert_rejected(value, "SCHEMA_UNSUPPORTED")
        value = valid_contract()
        value["extra"] = "no"
        self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

    def test_rejects_bool_revision_and_invalid_bounds(self):
        value = valid_contract()
        value["revision"] = True
        self.assert_rejected(value, "GOAL_REVISION_INVALID")

        value = valid_contract()
        value["original_goal"] = "x" * 4097
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

        value = valid_contract()
        value["accepted_limitations"] = ["x"] * 129
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_safe_summary_is_required_bounded_and_distinct_from_raw_goal(self):
        cases = []
        missing = valid_contract()
        del missing["safe_goal_summary"]
        cases.append((missing, "SAFE_GOAL_SUMMARY_REQUIRED"))
        for summary in (
            "第一行\n第二行",
            "x" * 257,
            valid_contract()["original_goal"],
            "摘要：" + valid_contract()["original_goal"],
        ):
            value = valid_contract()
            value["safe_goal_summary"] = summary
            cases.append((value, "SAFE_GOAL_SUMMARY_REQUIRED"))
        unsafe = valid_contract()
        unsafe["safe_goal_summary"] = "FEATURE_FLAG=true"
        cases.append((unsafe, "SECRET_VALUE_REJECTED"))
        for value, reason in cases:
            with self.subTest(reason=reason, summary=value.get("safe_goal_summary")):
                self.assert_rejected(value, reason)

    def test_raw_goal_is_preserved_even_when_not_safe_for_public_output(self):
        raw_goals = (
            "DATABASE_URL=private-value",
            "https://example.com/private",
            "Authorization: Bearer private-value",
            "Authorization私密值",
            "Ａｕｔｈｏｒｉｚａｔｉｏｎopaque",
            "diff --git a/private b/private",
            "- [ ] private task",
            "`private inline value`",
            *PROJECTION_EQUIVALENT_DANGERS,
        )
        for raw_goal in raw_goals:
            value = valid_contract()
            value["original_goal"] = raw_goal
            with self.subTest(raw_goal=raw_goal):
                validated = governance.validate_outcome_contract(value)
                self.assertEqual(validated["original_goal"], raw_goal)
                decision = governance.evaluate_outcome_contract(value)
                self.assertNotIn("original_goal", decision)
                self.assertEqual(
                    decision["goal_display"]["original_goal_sha256"],
                    hashlib.sha256(raw_goal.encode("utf-8", "strict")).hexdigest(),
                )

    def test_projection_equivalent_dangers_are_private_only_and_hash_raw_utf8(self):
        for raw_goal in PROJECTION_EQUIVALENT_DANGERS:
            value = valid_contract()
            value["original_goal"] = raw_goal
            with self.subTest(location="original_goal", raw_goal=raw_goal):
                decision = governance.evaluate_outcome_contract(value)
                self.assertEqual(
                    decision["goal_display"]["original_goal_sha256"],
                    hashlib.sha256(raw_goal.encode("utf-8", "strict")).hexdigest(),
                )
                self.assertNotIn("original_goal", decision)

            public_value = valid_contract()
            public_value["safe_goal_summary"] = raw_goal
            with self.subTest(location="safe_goal_summary", raw_goal=raw_goal):
                self.assert_rejected(public_value, "SECRET_VALUE_REJECTED")

    def test_safe_goal_summary_rejects_every_authorization_marker_form(self):
        summaries = (
            "Authorization",
            "Authorizationopaque",
            "Authorization私密值",
            "Ａｕｔｈｏｒｉｚａｔｉｏｎopaque",
            "Auth\u200borizationopaque",
        )
        for summary in summaries:
            value = valid_contract()
            value["safe_goal_summary"] = summary
            with self.subTest(summary=summary):
                self.assert_rejected(value, "SECRET_VALUE_REJECTED")

    def test_closed_authentication_message_does_not_change_outcome_evaluation(self):
        baseline = governance.evaluate_outcome_contract(valid_contract())
        value = valid_contract()
        value["safe_goal_summary"] = render_safe_public_message(
            "AUTHORIZATION_VALUE_HIDDEN"
        )

        decision = governance.evaluate_outcome_contract(value)

        self.assertEqual(
            decision["goal_display"]["safe_summary"],
            "认证请求头的值不得进入公开进度。",
        )
        for field in (
            "state",
            "blocking_outcome_ids",
            "blocking_capability_ids",
            "final_uat_eligible",
            "completion_eligible",
            "evidence_references",
            "write_authorized",
        ):
            with self.subTest(field=field):
                self.assertEqual(decision[field], baseline[field])

    def test_goal_binding_hashes_exact_raw_utf8_without_normalization(self):
        raw_goals = ("目标", " 目标", "目标 ", "目标\n", "caf\u00e9", "cafe\u0301")
        bindings = []
        for raw_goal in raw_goals:
            value = valid_contract()
            value["original_goal"] = raw_goal
            value["safe_goal_summary"] = "完成库存同步并验证交付结果"
            decision = governance.evaluate_outcome_contract(value)
            expected = hashlib.sha256(raw_goal.encode("utf-8", "strict")).hexdigest()
            self.assertEqual(decision["goal_display"]["original_goal_sha256"], expected)
            bindings.append(expected)
        self.assertEqual(len(bindings), len(set(bindings)))

    def test_revision_pair_requires_exact_next_revision_and_previous_digest(self):
        previous = valid_contract()
        current = copy.deepcopy(previous)
        current.update(
            revision=2,
            previous_goal_digest=governance.canonical_outcome_digest(previous),
            original_goal="把库存同步功能迁移到新的目标环境",
            safe_goal_summary="完成新的库存同步迁移并验证结果",
        )
        result = governance.validate_outcome_revision(previous, current)
        self.assertEqual(result["revision"], 2)

        invalid_pairs = []
        wrong_task = copy.deepcopy(current)
        wrong_task["task_id"] = "OTHER"
        invalid_pairs.append((previous, wrong_task))
        skipped = copy.deepcopy(current)
        skipped["revision"] = 3
        invalid_pairs.append((previous, skipped))
        wrong_digest = copy.deepcopy(current)
        wrong_digest["previous_goal_digest"] = "f" * 64
        invalid_pairs.append((previous, wrong_digest))
        for before, after in invalid_pairs:
            with self.subTest(task=after["task_id"], revision=after["revision"]):
                with self.assertRaises(governance.OutcomeContractError) as caught:
                    governance.validate_outcome_revision(before, after)
                self.assertEqual(caught.exception.reason, "GOAL_REVISION_INVALID")

    def test_depth_overflow_is_a_controlled_contract_error(self):
        value = valid_contract()
        value["accepted_limitations"] = [nested_value(1500)]

        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_bounded_tree_accepts_depth_16_and_rejects_depth_17_and_large_container(self):
        governance._validate_bounded_tree(nested_value(16))
        for value in (nested_value(17), ["item"] * 129):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(governance.OutcomeContractError) as caught:
                    governance._validate_bounded_tree(value)
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_cycle_alias_total_nodes_and_surrogate_fail_closed(self):
        cycle = {}
        cycle["self"] = cycle
        shared = ["value"]
        alias = {"first": shared, "second": shared}
        over_total_nodes = [list(range(32)) for _ in range(128)]
        cases = (
            ("cycle", cycle),
            ("alias", alias),
            ("total_nodes", over_total_nodes),
            ("surrogate", "\ud800"),
        )
        for name, value in cases:
            with self.subTest(name=name):
                with self.assertRaises(governance.OutcomeContractError) as caught:
                    governance._validate_bounded_tree(value)
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_rejects_secret_shaped_keys_recursively(self):
        for key in ("token", "secret", "password", "cookie", "authorization", "private_key", "remote_url"):
            value = valid_contract()
            value["accepted_limitations"] = [{key: "redacted"}]
            with self.subTest(key=key):
                self.assert_rejected(value, "SECRET_SHAPED_FIELD")

    def test_rejects_duplicate_ids_and_broken_links(self):
        duplicate = valid_contract()
        duplicate["capabilities"].append(copy.deepcopy(duplicate["capabilities"][0]))
        self.assert_rejected(duplicate, "FIELD_VALUE_INVALID")

        missing_capability = valid_contract()
        missing_capability["observable_outcomes"][0]["capability_ids"] = ["C404"]
        self.assert_rejected(missing_capability, "OUTCOME_MAPPING_INCOMPLETE")

        missing_evidence = valid_contract()
        missing_evidence["capabilities"][0]["evidence_requirement_ids"] = ["E404"]
        self.assert_rejected(missing_evidence, "OUTCOME_MAPPING_INCOMPLETE")

    def test_critical_outcome_requires_critical_capability(self):
        value = valid_contract()
        value["capabilities"][0]["critical"] = False
        self.assert_rejected(value, "OUTCOME_MAPPING_INCOMPLETE")

    def test_verified_capability_requires_current_evidence_reference(self):
        value = valid_contract()
        value["capabilities"][0]["evidence_references"] = []
        self.assert_rejected(value, "OUTCOME_MAPPING_INCOMPLETE")

    def test_critical_capability_cannot_be_not_applicable(self):
        value = valid_contract()
        value["capabilities"][0]["status"] = "NOT_APPLICABLE"
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_revision_requires_previous_goal_digest_only_after_revision_one(self):
        initial = valid_contract()
        initial["previous_goal_digest"] = "a" * 64
        self.assert_rejected(initial, "GOAL_REVISION_INVALID")

        revised = valid_contract()
        revised["revision"] = 2
        self.assert_rejected(revised, "GOAL_REVISION_INVALID")
        revised["previous_goal_digest"] = "a" * 64
        self.assertEqual(governance.validate_outcome_contract(revised), revised)

    def test_canonical_digest_is_repeatable_and_validated(self):
        value = valid_contract()
        first = governance.canonical_outcome_digest(value)
        second = governance.canonical_outcome_digest(copy.deepcopy(value))
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

        invalid = json.loads(json.dumps(value))
        invalid["schema_version"] = 7
        with self.assertRaises(governance.OutcomeContractError):
            governance.canonical_outcome_digest(invalid)


class OutcomeDecisionTests(unittest.TestCase):
    def test_every_public_outcome_free_text_rejects_prohibited_payloads(self):
        payloads = (
            "https://example.com/private",
            "Authorization: Bearer private-value",
            "FEATURE_FLAG=true",
            "diff --git a/private b/private",
            "- [ ] private task",
            "`private inline value`",
            *PROJECTION_EQUIVALENT_DANGERS,
        )
        placements = (
            (
                "goal_display.safe_summary",
                lambda value, text: value.__setitem__("safe_goal_summary", text),
            ),
            (
                "evidence_references[]",
                lambda value, text: value["capabilities"][0].__setitem__(
                    "evidence_references", [text]
                ),
            ),
        )
        for location, place in placements:
            for payload in payloads:
                value = valid_contract()
                place(value, payload)
                with self.subTest(location=location, payload=payload):
                    with self.assertRaises(governance.OutcomeContractError) as caught:
                        governance.evaluate_outcome_contract(value)
                    self.assertEqual(caught.exception.reason, "SECRET_VALUE_REJECTED")

    def test_safe_public_outcome_free_text_is_preserved_without_semantic_change(self):
        baseline = governance.evaluate_outcome_contract(valid_contract())
        value = valid_contract()
        value["safe_goal_summary"] = "安全中文说明"
        safe_references = [
            "安全中文证据",
            "/projects/lead",
            "a" * 64,
            "UNIT:OUTCOME",
        ]
        value["capabilities"][0]["evidence_references"] = safe_references

        decision = governance.evaluate_outcome_contract(value)

        self.assertEqual(decision["goal_display"]["safe_summary"], "安全中文说明")
        self.assertEqual(decision["evidence_references"], sorted(safe_references))
        for field in (
            "state",
            "blocking_outcome_ids",
            "blocking_capability_ids",
            "final_uat_eligible",
            "completion_eligible",
            "write_authorized",
        ):
            with self.subTest(field=field):
                self.assertEqual(decision[field], baseline[field])

    def test_verified_critical_capability_is_delivery_verified(self):
        result = governance.evaluate_outcome_contract(valid_contract())
        self.assertEqual(result["state"], "DELIVERY_VERIFIED")
        self.assertIs(result["final_uat_eligible"], True)
        self.assertIs(result["completion_eligible"], False)
        self.assertIs(result["write_authorized"], False)
        self.assertEqual(
            result["goal_display"],
            {
                "safe_summary": "完成库存同步迁移并验证目标环境结果",
                "original_goal_sha256": hashlib.sha256(
                    "把库存同步功能完整迁移到目标环境".encode("utf-8")
                ).hexdigest(),
                "original_text_state": "RETAINED_IN_OUTCOME_CONTRACT",
            },
        )

    def test_every_blocking_status_prevents_final_uat(self):
        for status in ("PLANNED", "IMPLEMENTED", "PARTIAL", "MISSING", "UNKNOWN"):
            value = valid_contract()
            value["capabilities"][0]["status"] = status
            value["capabilities"][0]["evidence_references"] = ["commit:abc"]
            with self.subTest(status=status):
                result = governance.evaluate_outcome_contract(value)
                self.assertEqual(result["state"], "INCOMPLETE")
                self.assertEqual(result["blocking_capability_ids"], ["C1"])
                self.assertEqual(result["blocking_outcome_ids"], ["O1"])
                self.assertIs(result["final_uat_eligible"], False)

    def test_technical_reference_does_not_upgrade_implemented_status(self):
        value = valid_contract()
        value["capabilities"][0]["status"] = "IMPLEMENTED"
        value["capabilities"][0]["evidence_references"] = [
            "test:pass",
            "commit:abc",
            "build:ready",
        ]
        self.assertEqual(governance.evaluate_outcome_contract(value)["state"], "INCOMPLETE")

    def test_noncritical_not_applicable_does_not_block_delivery(self):
        value = valid_contract()
        value["observable_outcomes"].append(
            {
                "outcome_id": "O2",
                "description": "可选报告",
                "critical": False,
                "capability_ids": ["C2"],
            }
        )
        value["capabilities"].append(
            {
                "capability_id": "C2",
                "description": "生成可选报告",
                "critical": False,
                "status": "NOT_APPLICABLE",
                "evidence_requirement_ids": ["E2"],
                "evidence_references": [],
            }
        )
        value["evidence_requirements"].append(
            {"evidence_id": "E2", "category": "OPTIONAL", "description": "可选证据"}
        )
        self.assertEqual(governance.evaluate_outcome_contract(value)["state"], "DELIVERY_VERIFIED")

    def test_decision_lists_are_sorted_and_deduplicated(self):
        value = valid_contract()
        value["capabilities"][0]["evidence_references"] = ["z:last", "a:first", "z:last"]
        result = governance.evaluate_outcome_contract(value)
        self.assertEqual(result["evidence_references"], ["a:first", "z:last"])


if __name__ == "__main__":
    unittest.main()
