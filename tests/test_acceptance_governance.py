# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import hashlib
import json
import unittest
import unicodedata

from workbench import acceptance_governance as governance


ACTION_REQUEST_DIGEST = "9" * 64


def canonical_digest(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def human_requirements(level, action_request_digest=ACTION_REQUEST_DIGEST):
    if level in {"U0", "U1"}:
        return []
    if level == "U2":
        return [
            {
                "requirement_id": "REQ-01-POST",
                "kind": "POST_ACTION_OBSERVATION",
                "description": "用户确认可见结果符合预期",
                "action_request_digest": None,
            }
        ]
    return [
        {
            "requirement_id": "REQ-01-PRE",
            "kind": "PRE_ACTION_APPROVAL",
            "description": "用户确认可以开始外部动作",
            "action_request_digest": action_request_digest,
        },
        {
            "requirement_id": "REQ-02-POST",
            "kind": "POST_ACTION_OBSERVATION",
            "description": "用户确认外部结果符合预期",
            "action_request_digest": action_request_digest,
        },
        {
            "requirement_id": "REQ-03-ROLLBACK",
            "kind": "ROLLBACK_VERIFICATION",
            "description": "用户确认回滚路径可以验证",
            "action_request_digest": action_request_digest,
        },
    ]


def acceptance_case(level="U0", *, requirements=None, **factor_overrides):
    factors = {
        "user_visibility": "NONE",
        "business_impact": "NONE",
        "data_effect": "NONE",
        "data_sensitivity": "NONE",
        "external_effect": "NONE",
        "reversibility": "IMMEDIATE",
        "automated_coverage": "COMPLETE",
        "environment_gap": "NONE",
        "novelty": "KNOWN",
    }
    if level == "U1":
        factors["user_visibility"] = "LOW"
    elif level == "U2":
        factors["user_visibility"] = "HIGH"
    elif level == "U3":
        factors["external_effect"] = "PRODUCTION"
    factors.update(factor_overrides)
    return {
        "schema_version": 1,
        "task_id": "TASK-2.2-DEMO",
        "outcome_decision": {
            "state": "DELIVERY_VERIFIED",
            "final_uat_eligible": True,
            "blocking_capability_ids": [],
            "critical_unknown": False,
        },
        "factors": factors,
        "safety_unknown": False,
        "waiver": None,
        "human_requirements": (
            copy.deepcopy(human_requirements(level))
            if requirements is None
            else copy.deepcopy(requirements)
        ),
    }


def case_digest(value, effective_level):
    return canonical_digest({"case": value, "effective_level": effective_level})


def receipt(
    value,
    requirement_id,
    *,
    effective_level,
    result="PASS",
    source_event_id=None,
    message="用户确认通过",
    action_result_digest=None,
):
    requirement = next(
        item
        for item in value["human_requirements"]
        if item["requirement_id"] == requirement_id
    )
    kind = requirement["kind"]
    return {
        "schema_version": 1,
        "receipt_id": f"UER-{requirement_id}",
        "task_id": value["task_id"],
        "acceptance_case_digest": case_digest(value, effective_level),
        "requirement_id": requirement_id,
        "kind": kind,
        "result": result,
        "source_event_id": source_event_id or f"USER-EVENT-{requirement_id}",
        "source_event_digest": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "source_event_role": "USER",
        "action_request_digest": requirement["action_request_digest"],
        "action_result_digest": (
            None if kind == "PRE_ACTION_APPROVAL" else action_result_digest
        ),
    }


def action_result(value, pre_receipt, *, state="SUCCEEDED"):
    result = {
        "schema_version": 1,
        "task_id": value["task_id"],
        "action_request_digest": ACTION_REQUEST_DIGEST,
        "pre_action_receipt_digest": canonical_digest(pre_receipt),
        "state": state,
        "action_result_digest": "0" * 64,
        "evidence_references": ["artifact:deployment-result", "check:exit-zero"],
    }
    result["action_result_digest"] = canonical_digest(
        {key: item for key, item in result.items() if key != "action_result_digest"}
    )
    return result


def nested_value(depth):
    value = "leaf"
    for _ in range(depth):
        value = {"layer": value}
    return value


class AcceptanceContractTests(unittest.TestCase):
    def assert_rejected(self, value, reason):
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.validate_acceptance_case(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_validates_without_mutating_input(self):
        value = acceptance_case()
        before = copy.deepcopy(value)
        result = governance.validate_acceptance_case(value)
        self.assertEqual(result, before)
        self.assertIsNot(result, value)

    def test_rejects_unknown_fields_bool_and_factor_values(self):
        value = acceptance_case()
        value["extra"] = "no"
        self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

        value = acceptance_case()
        value["safety_unknown"] = 0
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

        value = acceptance_case()
        value["factors"]["data_sensitivity"] = "PRIVATEISH"
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

        value = acceptance_case()
        value["outcome_decision"]["state"] = []
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

    def test_waiver_has_exact_nonempty_evidence_contract(self):
        value = acceptance_case()
        value["waiver"] = {"reason": "内部重构", "evidence_references": []}
        self.assert_rejected(value, "WAIVER_EVIDENCE_REQUIRED")

        value["waiver"] = {
            "reason": "内部重构",
            "evidence_references": ["test:pass"],
        }
        self.assertEqual(governance.validate_acceptance_case(value), value)

    def test_human_requirements_follow_effective_level_contract(self):
        for level in ("U0", "U1", "U2", "U3"):
            with self.subTest(level=level):
                value = acceptance_case(level)
                self.assertEqual(governance.validate_acceptance_case(value), value)

        low_with_requirement = acceptance_case(requirements=human_requirements("U2"))
        self.assert_rejected(low_with_requirement, "HUMAN_REQUIREMENTS_INVALID")

        missing_u2 = acceptance_case("U2", requirements=[])
        self.assert_rejected(missing_u2, "HUMAN_REQUIREMENTS_INVALID")

        missing_u3 = acceptance_case("U3", requirements=human_requirements("U2"))
        self.assert_rejected(missing_u3, "HUMAN_REQUIREMENTS_INVALID")

    def test_human_requirement_ids_fields_cardinality_and_digests_are_strict(self):
        mutations = []
        duplicate = acceptance_case("U3")
        duplicate["human_requirements"][1]["requirement_id"] = "REQ-01-PRE"
        mutations.append(duplicate)
        extra = acceptance_case("U3")
        extra["human_requirements"][0]["extra"] = "no"
        mutations.append(extra)
        invalid_kind = acceptance_case("U3")
        invalid_kind["human_requirements"][0]["kind"] = "CLICK_OK"
        mutations.append(invalid_kind)
        duplicate_pre = acceptance_case("U3")
        duplicate_pre["human_requirements"][1]["kind"] = "PRE_ACTION_APPROVAL"
        mutations.append(duplicate_pre)
        mismatched_digest = acceptance_case("U3")
        mismatched_digest["human_requirements"][2]["action_request_digest"] = "8" * 64
        mutations.append(mismatched_digest)
        for value in mutations:
            with self.subTest(value=value):
                self.assert_rejected(value, "HUMAN_REQUIREMENTS_INVALID")

    def test_depth_overflow_is_a_controlled_contract_error(self):
        value = acceptance_case()
        value["waiver"] = nested_value(1500)
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_bounded_tree_accepts_depth_16_and_rejects_depth_17_and_large_container(self):
        governance._validate_bounded_tree(nested_value(16))
        for value in (nested_value(17), ["item"] * 129):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(governance.AcceptanceContractError) as caught:
                    governance._validate_bounded_tree(value)
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_cycle_alias_total_nodes_and_surrogate_fail_closed(self):
        cycle = {}
        cycle["self"] = cycle
        shared = ["value"]
        alias = {"first": shared, "second": shared}
        over_total_nodes = [list(range(32)) for _ in range(128)]
        for value in (cycle, alias, over_total_nodes, "\ud800"):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(governance.AcceptanceContractError) as caught:
                    governance._validate_bounded_tree(value)
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")


class AcceptanceClassificationTests(unittest.TestCase):
    def test_each_factor_value_maps_to_its_declared_level(self):
        for factor, mapping in governance.FACTOR_LEVELS.items():
            for factor_value, expected in mapping.items():
                value = acceptance_case(
                    requirements=human_requirements(expected),
                    **{factor: factor_value},
                )
                with self.subTest(factor=factor, value=factor_value):
                    self.assertEqual(
                        governance.classify_acceptance(value)["computed_level"],
                        expected,
                    )

    def test_data_sensitivity_uses_the_closed_level_matrix(self):
        expected = {
            "NONE": "U0",
            "PUBLIC": "U1",
            "INTERNAL": "U2",
            "SENSITIVE": "U3",
            "UNKNOWN": "U3",
        }
        for sensitivity, level in expected.items():
            value = acceptance_case(
                requirements=human_requirements(level),
                data_sensitivity=sensitivity,
            )
            with self.subTest(sensitivity=sensitivity):
                self.assertEqual(governance.classify_acceptance(value)["level"], level)

    def test_read_only_sensitive_data_is_u3(self):
        value = acceptance_case(
            requirements=human_requirements("U3"),
            data_effect="READ_ONLY",
            data_sensitivity="SENSITIVE",
        )
        self.assertEqual(governance.classify_acceptance(value)["level"], "U3")

    def test_critical_unknown_and_safety_unknown_raise_level(self):
        critical = acceptance_case(requirements=human_requirements("U2"))
        critical["outcome_decision"].update(
            state="INCOMPLETE",
            final_uat_eligible=False,
            blocking_capability_ids=["C1"],
            critical_unknown=True,
        )
        self.assertEqual(governance.classify_acceptance(critical)["level"], "U2")

        safety = acceptance_case(requirements=human_requirements("U3"))
        safety["safety_unknown"] = True
        self.assertEqual(governance.classify_acceptance(safety)["level"], "U3")

    def test_upward_override_validates_target_requirements_and_changes_case_digest(self):
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.classify_acceptance(acceptance_case(), user_override="U2")
        self.assertEqual(caught.exception.reason, "HUMAN_REQUIREMENTS_INVALID")

        value = acceptance_case(requirements=human_requirements("U2"))
        raised = governance.classify_acceptance(value, user_override="U2")
        self.assertEqual(raised["computed_level"], "U0")
        self.assertEqual(raised["level"], "U2")
        self.assertNotEqual(
            canonical_digest({"case": value, "effective_level": "U0"}),
            governance.canonical_acceptance_case_digest(value, user_override="U2"),
        )

    def test_user_override_cannot_be_invalid_or_lower(self):
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.classify_acceptance(acceptance_case(), user_override="U4")
        self.assertEqual(caught.exception.reason, "ACCEPTANCE_LEVEL_INVALID")

        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.classify_acceptance(acceptance_case("U2"), user_override="U1")
        self.assertEqual(caught.exception.reason, "USER_OVERRIDE_CANNOT_LOWER")


class UserEventReceiptTests(unittest.TestCase):
    def assert_receipt_rejected(self, value, receipts, reason="USER_EVENT_RECEIPT_INVALID"):
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.evaluate_acceptance(value, receipts)
        self.assertEqual(caught.exception.reason, reason)

    def test_recorded_by_literal_is_not_a_user_receipt(self):
        value = acceptance_case("U2")
        legacy = {
            "requested_observation_ids": ["REQ-01-POST"],
            "results": [
                {
                    "observation_id": "REQ-01-POST",
                    "result": "PASS",
                    "recorded_by": "USER",
                }
            ],
        }
        self.assert_receipt_rejected(value, legacy)

    def test_receipt_must_match_task_case_requirement_and_kind(self):
        value = acceptance_case("U2")
        base = receipt(value, "REQ-01-POST", effective_level="U2")
        mutations = []
        for field, changed in (
            ("task_id", "TASK-OTHER"),
            ("acceptance_case_digest", "f" * 64),
            ("requirement_id", "REQ-UNKNOWN"),
            ("kind", "PRE_ACTION_APPROVAL"),
            ("source_event_role", "AGENT"),
        ):
            changed_receipt = copy.deepcopy(base)
            changed_receipt[field] = changed
            mutations.append(changed_receipt)
        for receipts in ([item] for item in mutations):
            self.assert_receipt_rejected(value, receipts)
        self.assert_receipt_rejected(value, [base, copy.deepcopy(base)])

    def test_receipt_requires_source_event_id_and_digest(self):
        value = acceptance_case("U2")
        for field, changed in (("source_event_id", ""), ("source_event_digest", None)):
            item = receipt(value, "REQ-01-POST", effective_level="U2")
            item[field] = changed
            with self.subTest(field=field):
                self.assert_receipt_rejected(value, [item])

    def test_unknown_receipts_are_rejected_and_missing_bound_receipts_stay_pending(self):
        value = acceptance_case("U2")
        pending = governance.evaluate_acceptance(value, [])
        self.assertEqual(pending["acceptance_state"], "USER_OBSERVATION_PENDING")
        self.assertEqual(pending["reason"], "USER_EVENT_RECEIPT_REQUIRED")

        unknown = receipt(value, "REQ-01-POST", effective_level="U2")
        unknown["requirement_id"] = "REQ-UNKNOWN"
        self.assert_receipt_rejected(value, [unknown])

    def test_receipt_stage_digest_rules_are_strict(self):
        u2 = acceptance_case("U2")
        u2_receipt = receipt(u2, "REQ-01-POST", effective_level="U2")
        u2_receipt["action_request_digest"] = ACTION_REQUEST_DIGEST
        self.assert_receipt_rejected(u2, [u2_receipt])

        u3 = acceptance_case("U3")
        pre = receipt(u3, "REQ-01-PRE", effective_level="U3")
        pre["action_result_digest"] = "a" * 64
        self.assert_receipt_rejected(u3, [pre])

    def test_source_event_digest_preserves_exact_bytes(self):
        composed = "用户确认 Caf\u00e9\n"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(
            governance.canonical_user_event_source_digest(composed),
            governance.canonical_user_event_source_digest(composed.rstrip()),
        )
        self.assertNotEqual(
            governance.canonical_user_event_source_digest(composed),
            governance.canonical_user_event_source_digest(composed.replace("\n", "\r\n")),
        )
        self.assertNotEqual(
            governance.canonical_user_event_source_digest(composed),
            governance.canonical_user_event_source_digest(decomposed),
        )


class AcceptanceEvaluationTests(unittest.TestCase):
    def assert_state(self, result, state, reason, *, action_state="PENDING"):
        self.assertEqual(result["acceptance_state"], state)
        self.assertEqual(result["reason"], reason)
        self.assertEqual(result["action_state"], action_state)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["completion_eligible"], False)
        self.assertIs(result["write_authorized"], False)

    def test_blocked_outcome_cannot_be_accepted(self):
        value = acceptance_case("U2")
        value["outcome_decision"].update(
            state="INCOMPLETE",
            final_uat_eligible=False,
            blocking_capability_ids=["C1"],
        )
        result = governance.evaluate_acceptance(value)
        self.assertEqual(result["acceptance_state"], "BLOCKED_BY_OUTCOME")
        self.assertEqual(result["reason"], "CRITICAL_CAPABILITY_UNVERIFIED")
        self.assertIs(result["accepted"], False)

    def test_u0_and_u1_require_explicit_waiver_evidence(self):
        for level in ("U0", "U1"):
            value = acceptance_case(level)
            with self.subTest(level=level):
                pending = governance.evaluate_acceptance(value)
                self.assertEqual(pending["acceptance_state"], "WAIVER_PENDING")
                value["waiver"] = {
                    "reason": "替代证据完整",
                    "evidence_references": ["test:pass", "review:pass"],
                }
                accepted = governance.evaluate_acceptance(value)
                self.assertEqual(accepted["acceptance_state"], "WAIVED_WITH_EVIDENCE")
                self.assertIs(accepted["accepted"], True)
                self.assertEqual(accepted["action_state"], "NOT_REQUIRED")

    def test_u2_missing_failed_and_passing_user_receipts(self):
        value = acceptance_case("U2")
        pending = governance.evaluate_acceptance(value)
        self.assert_state(
            pending,
            "USER_OBSERVATION_PENDING",
            "USER_EVENT_RECEIPT_REQUIRED",
            action_state="NOT_REQUIRED",
        )

        failed_receipt = receipt(
            value, "REQ-01-POST", effective_level="U2", result="FAIL"
        )
        failed = governance.evaluate_acceptance(value, [failed_receipt])
        self.assert_state(
            failed,
            "USER_OBSERVATION_FAILED",
            "OBSERVATION_FAILED",
            action_state="NOT_REQUIRED",
        )

        passed_receipt = receipt(value, "REQ-01-POST", effective_level="U2")
        accepted = governance.evaluate_acceptance(value, [passed_receipt])
        self.assertEqual(accepted["acceptance_state"], "USER_OBSERVED")
        self.assertIs(accepted["accepted"], True)
        self.assertIs(accepted["completion_eligible"], True)

    def test_u3_complete_state_machine(self):
        value = acceptance_case("U3")
        pre_pass = receipt(value, "REQ-01-PRE", effective_level="U3")
        pre_fail = receipt(
            value, "REQ-01-PRE", effective_level="U3", result="FAIL"
        )

        self.assert_state(
            governance.evaluate_acceptance(value),
            "U3_PRE_ACTION_PENDING",
            "U3_PRE_ACTION_REQUIRED",
        )
        self.assert_state(
            governance.evaluate_acceptance(value, [pre_fail]),
            "U3_PRE_ACTION_FAILED",
            "U3_PRE_ACTION_FAILED",
        )
        self.assert_state(
            governance.evaluate_acceptance(value, [pre_pass]),
            "U3_ACTION_PENDING",
            "U3_ACTION_RESULT_REQUIRED",
        )

        for action_state, expected_state in (
            ("FAILED", "U3_ACTION_FAILED"),
            ("UNKNOWN", "U3_ACTION_UNKNOWN"),
        ):
            result = governance.evaluate_acceptance(
                value,
                [pre_pass],
                u3_action_result=action_result(value, pre_pass, state=action_state),
            )
            self.assert_state(
                result, expected_state, expected_state, action_state=action_state
            )

        succeeded = action_result(value, pre_pass)
        self.assert_state(
            governance.evaluate_acceptance(
                value, [pre_pass], u3_action_result=succeeded
            ),
            "U3_POST_ACTION_PENDING",
            "USER_EVENT_RECEIPT_REQUIRED",
            action_state="SUCCEEDED",
        )

        post_fail = receipt(
            value,
            "REQ-02-POST",
            effective_level="U3",
            result="FAIL",
            action_result_digest=succeeded["action_result_digest"],
        )
        self.assert_state(
            governance.evaluate_acceptance(
                value, [pre_pass, post_fail], u3_action_result=succeeded
            ),
            "U3_POST_ACTION_FAILED",
            "U3_POST_ACTION_FAILED",
            action_state="SUCCEEDED",
        )

        post_pass = receipt(
            value,
            "REQ-02-POST",
            effective_level="U3",
            action_result_digest=succeeded["action_result_digest"],
        )
        self.assert_state(
            governance.evaluate_acceptance(
                value, [pre_pass, post_pass], u3_action_result=succeeded
            ),
            "U3_ROLLBACK_VERIFICATION_PENDING",
            "U3_ROLLBACK_VERIFICATION_REQUIRED",
            action_state="SUCCEEDED",
        )

        rollback_fail = receipt(
            value,
            "REQ-03-ROLLBACK",
            effective_level="U3",
            result="FAIL",
            action_result_digest=succeeded["action_result_digest"],
        )
        self.assert_state(
            governance.evaluate_acceptance(
                value,
                [pre_pass, post_pass, rollback_fail],
                u3_action_result=succeeded,
            ),
            "U3_ROLLBACK_VERIFICATION_FAILED",
            "U3_ROLLBACK_VERIFICATION_FAILED",
            action_state="SUCCEEDED",
        )

        rollback_pass = receipt(
            value,
            "REQ-03-ROLLBACK",
            effective_level="U3",
            action_result_digest=succeeded["action_result_digest"],
        )
        accepted = governance.evaluate_acceptance(
            value,
            [pre_pass, post_pass, rollback_pass],
            u3_action_result=succeeded,
        )
        self.assertEqual(accepted["acceptance_state"], "USER_OBSERVED")
        self.assertEqual(accepted["action_state"], "SUCCEEDED")
        self.assertIs(accepted["accepted"], True)
        self.assertIs(accepted["completion_eligible"], True)

    def test_u3_rollback_before_all_post_observations_is_rejected(self):
        requirements = human_requirements("U3")
        requirements.insert(
            2,
            {
                "requirement_id": "REQ-03-POST",
                "kind": "POST_ACTION_OBSERVATION",
                "description": "用户确认第二项外部结果符合预期",
                "action_request_digest": ACTION_REQUEST_DIGEST,
            },
        )
        value = acceptance_case("U3", requirements=requirements)
        pre = receipt(value, "REQ-01-PRE", effective_level="U3")
        succeeded = action_result(value, pre)
        post_pass = receipt(
            value,
            "REQ-02-POST",
            effective_level="U3",
            action_result_digest=succeeded["action_result_digest"],
        )
        post_fail = receipt(
            value,
            "REQ-02-POST",
            effective_level="U3",
            result="FAIL",
            action_result_digest=succeeded["action_result_digest"],
        )
        second_post_pass = receipt(
            value,
            "REQ-03-POST",
            effective_level="U3",
            action_result_digest=succeeded["action_result_digest"],
        )
        rollback = receipt(
            value,
            "REQ-03-ROLLBACK",
            effective_level="U3",
            action_result_digest=succeeded["action_result_digest"],
        )

        for observed in (
            [pre, rollback],
            [pre, post_pass, rollback],
            [pre, post_fail, rollback],
            [pre, post_pass, rollback, second_post_pass],
        ):
            with self.subTest(receipt_ids=[item["receipt_id"] for item in observed]):
                with self.assertRaises(governance.AcceptanceContractError) as caught:
                    governance.evaluate_acceptance(
                        value, observed, u3_action_result=succeeded
                    )
                self.assertEqual(caught.exception.reason, "USER_EVENT_RECEIPT_INVALID")

    def test_u3_action_result_and_receipt_chain_must_match(self):
        value = acceptance_case("U3")
        pre = receipt(value, "REQ-01-PRE", effective_level="U3")
        bad_pre = action_result(value, pre)
        bad_pre["pre_action_receipt_digest"] = "f" * 64
        bad_pre["action_result_digest"] = canonical_digest(
            {key: item for key, item in bad_pre.items() if key != "action_result_digest"}
        )
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.evaluate_acceptance(value, [pre], u3_action_result=bad_pre)
        self.assertEqual(caught.exception.reason, "U3_ACTION_RESULT_INVALID")

        malformed = action_result(value, pre)
        malformed["action_result_digest"] = "f" * 64
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.evaluate_acceptance(value, [pre], u3_action_result=malformed)
        self.assertEqual(caught.exception.reason, "U3_ACTION_RESULT_INVALID")

        succeeded = action_result(value, pre)
        wrong_post = receipt(
            value,
            "REQ-02-POST",
            effective_level="U3",
            action_result_digest="f" * 64,
        )
        with self.assertRaises(governance.AcceptanceContractError) as caught:
            governance.evaluate_acceptance(
                value, [pre, wrong_post], u3_action_result=succeeded
            )
        self.assertEqual(caught.exception.reason, "USER_EVENT_RECEIPT_INVALID")

    def test_requirement_summary_is_derived_from_bound_requirements(self):
        value = acceptance_case("U3")
        pre = receipt(value, "REQ-01-PRE", effective_level="U3")
        result = governance.evaluate_acceptance(value, [pre])
        self.assertEqual(
            result["requirement_summary"],
            {
                "PRE_ACTION_APPROVAL": {"required": 1, "pass": 1, "fail": 0},
                "POST_ACTION_OBSERVATION": {"required": 1, "pass": 0, "fail": 0},
                "ROLLBACK_VERIFICATION": {"required": 1, "pass": 0, "fail": 0},
            },
        )

    def test_evaluation_does_not_mutate_case_receipts_or_action_result(self):
        value = acceptance_case("U3")
        pre = receipt(value, "REQ-01-PRE", effective_level="U3")
        result = action_result(value, pre)
        before = copy.deepcopy((value, [pre], result))
        governance.evaluate_acceptance(value, [pre], u3_action_result=result)
        self.assertEqual((value, [pre], result), before)


if __name__ == "__main__":
    unittest.main()
