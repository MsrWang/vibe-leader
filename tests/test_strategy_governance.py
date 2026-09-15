# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import unittest

from workbench import strategy_governance as governance


def route(mechanism="strict result agent protocol", side_effect_class="LOCAL_REVERSIBLE"):
    return {
        "goal_digest": "a" * 64,
        "mechanism": mechanism,
        "key_assumptions": ["reviewer follows output contract"],
        "target_environment": "local Codex runtime",
        "side_effect_class": side_effect_class,
    }


def failed_attempt(number, delta):
    return {
        "attempt_number": number,
        "outcome": "FAILED",
        "failure_summary": "result contract rejected",
        "evidence_references": [f"result:{number}"],
        "evidence_delta": delta,
        "correction": f"correction-{number}",
    }


def attempt(number, outcome, delta):
    value = failed_attempt(number, delta)
    value["outcome"] = outcome
    if outcome == "SUCCEEDED":
        value["failure_summary"] = "completed"
    elif outcome == "UNKNOWN":
        value["failure_summary"] = "result remains unknown"
    return value


def history(attempts=None, selected_route=None):
    return {
        "schema_version": 1,
        "route": selected_route or route(),
        "attempts": attempts or [],
    }


def alternative(mechanism, **changes):
    value = {
        "route": route(mechanism=mechanism),
        "expected_effect": f"effect-{mechanism}",
        "risk": "LOW",
        "maintenance_cost": "LOW",
        "evidence_gaps": ["fresh review"],
        "changes_user_outcome": False,
        "changes_data_model": False,
        "changes_cost": False,
        "changes_external_effect": False,
        "changes_maintenance_owner": False,
    }
    value.update(changes)
    return value


class StrategyContractTests(unittest.TestCase):
    def assert_rejected(self, value, reason):
        with self.assertRaises(governance.StrategyContractError) as caught:
            governance.validate_strategy_history(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_validates_without_mutating_input(self):
        value = history([failed_attempt(1, ["new schema evidence"])])
        before = copy.deepcopy(value)
        result = governance.validate_strategy_history(value)
        self.assertEqual(result, before)
        self.assertIsNot(result, value)

    def test_route_id_ignores_surface_fields(self):
        first = route()
        second = dict(
            first,
            request_id="D-999",
            temp_dir="/tmp/new",
            command="retry --json",
            output_format="markdown",
        )
        self.assertEqual(
            governance.canonical_route_id(first), governance.canonical_route_id(second)
        )

    def test_changed_root_mechanism_changes_route_id(self):
        self.assertNotEqual(
            governance.canonical_route_id(route("strict json")),
            governance.canonical_route_id(route("markdown ranges")),
        )

    def test_reordered_key_assumptions_keep_the_same_route_id(self):
        first = route()
        first["key_assumptions"] = ["beta assumption", "alpha assumption"]
        second = route()
        second["key_assumptions"] = ["alpha assumption", "beta assumption"]

        self.assertEqual(
            governance.canonical_route_id(first),
            governance.canonical_route_id(second),
        )

    def test_validated_route_uses_canonical_assumption_order(self):
        selected = route()
        selected["key_assumptions"] = ["beta assumption", "alpha assumption"]

        validated = governance.validate_strategy_history(history(selected_route=selected))

        self.assertEqual(
            validated["route"]["key_assumptions"],
            ["alpha assumption", "beta assumption"],
        )

    def test_declared_route_id_must_match_identity(self):
        value = history()
        value["route"]["route_id"] = "f" * 64
        self.assert_rejected(value, "ROUTE_ID_MISMATCH")

    def test_rejects_noncontiguous_and_duplicate_attempts(self):
        cases = (
            [failed_attempt(2, ["delta"])],
            [failed_attempt(1, ["a"]), failed_attempt(1, ["b"])],
        )
        for attempts in cases:
            with self.subTest(attempts=attempts):
                self.assert_rejected(history(attempts), "ATTEMPT_SEQUENCE_INVALID")

    def test_rejects_attempt_after_every_terminal_state(self):
        terminal_prefixes = {
            "succeeded": [attempt(1, "SUCCEEDED", ["done"])],
            "unknown": [attempt(1, "UNKNOWN", ["ambiguous"])],
            "external_first_failure": [failed_attempt(1, ["external failure"])],
            "local_empty_delta": [failed_attempt(1, [])],
            "local_repeated_delta": [
                failed_attempt(1, ["same"]),
                failed_attempt(2, ["same"]),
            ],
            "four_local_failures": [
                failed_attempt(index, [f"delta-{index}"])
                for index in range(1, 5)
            ],
        }
        for name, prefix in terminal_prefixes.items():
            selected_route = (
                route(side_effect_class="DEPLOY")
                if name == "external_first_failure"
                else route()
            )
            next_attempt = failed_attempt(len(prefix) + 1, ["later evidence"])
            with self.subTest(name=name):
                self.assert_rejected(
                    history(prefix + [next_attempt], selected_route),
                    "ATTEMPT_AFTER_TERMINAL",
                )

    def test_four_failures_are_valid_but_five_are_not(self):
        four = [
            failed_attempt(index, [f"delta-{index}"])
            for index in range(1, 5)
        ]
        self.assertEqual(len(governance.validate_strategy_history(history(four))["attempts"]), 4)
        self.assert_rejected(
            history(four + [failed_attempt(5, ["delta-5"])]),
            "ATTEMPT_AFTER_TERMINAL",
        )

    def test_rejects_unknown_fields_bool_number_and_invalid_outcome(self):
        value = history()
        value["extra"] = "no"
        self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

        value = history([failed_attempt(True, ["delta"])])
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

        value = history([failed_attempt(1, ["delta"])])
        value["attempts"][0]["outcome"] = "MAYBE"
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_all_public_entries_reject_unencodable_unicode_with_contract_error(self):
        invalid_route = route(mechanism="\ud800")
        invalid_history = history(selected_route=invalid_route)
        comparison = {
            "schema_version": 1,
            "alternatives": [
                alternative("range markdown"),
                alternative("deterministic analyzer", expected_effect="\ud800"),
            ],
        }
        calls = (
            lambda: governance.canonical_route_id(invalid_route),
            lambda: governance.validate_strategy_history(invalid_history),
            lambda: governance.evaluate_strategy(invalid_history),
            lambda: governance.compare_routes(comparison),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(governance.StrategyContractError) as caught:
                    call()
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")


class StrategyDecisionTests(unittest.TestCase):
    def test_zero_to_three_failures_have_expected_retry_state(self):
        ready = governance.evaluate_strategy(history())
        self.assertEqual(ready["state"], "READY")
        self.assertIs(ready["next_attempt_allowed"], True)

        for count in range(1, 4):
            attempts = [failed_attempt(index, [f"delta-{index}"]) for index in range(1, count + 1)]
            with self.subTest(count=count):
                result = governance.evaluate_strategy(history(attempts))
                self.assertEqual(result["state"], "RETRY_ALLOWED")
                self.assertEqual(result["failure_count"], count)
                self.assertIs(result["next_attempt_allowed"], True)

    def test_fourth_substantive_failure_forces_route_reassessment(self):
        attempts = [failed_attempt(index, [f"delta-{index}"]) for index in range(1, 5)]
        result = governance.evaluate_strategy(history(attempts))
        self.assertEqual(result["failure_count"], 4)
        self.assertEqual(result["state"], "ROUTE_REASSESSMENT_REQUIRED")
        self.assertIs(result["next_attempt_allowed"], False)

    def test_missing_or_repeated_evidence_delta_counts_failure_but_blocks_retry(self):
        missing = governance.evaluate_strategy(history([failed_attempt(1, [])]))
        self.assertEqual(missing["failure_count"], 1)
        self.assertEqual(missing["state"], "RETRY_NOT_JUSTIFIED")

        repeated = [failed_attempt(1, ["same"]), failed_attempt(2, ["same"])]
        result = governance.evaluate_strategy(history(repeated))
        self.assertEqual(result["failure_count"], 2)
        self.assertEqual(result["state"], "RETRY_NOT_JUSTIFIED")

    def test_success_stops_further_attempts(self):
        success = failed_attempt(2, ["verified result"])
        success["outcome"] = "SUCCEEDED"
        success["failure_summary"] = "completed"
        result = governance.evaluate_strategy(
            history([failed_attempt(1, ["failure evidence"]), success])
        )
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["failure_count"], 1)
        self.assertIs(result["next_attempt_allowed"], False)

    def test_successful_external_attempt_is_ready_without_retry_authority(self):
        success = failed_attempt(1, ["external result verified"])
        success["outcome"] = "SUCCEEDED"
        success["failure_summary"] = "completed"

        result = governance.evaluate_strategy(
            history([success], route(side_effect_class="DEPLOY"))
        )

        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["failure_count"], 0)
        self.assertIs(result["next_attempt_allowed"], False)
        self.assertIs(result["write_authorized"], False)

    def test_unknown_result_is_not_automatic_retry_authority(self):
        unknown = failed_attempt(1, ["ambiguous output"])
        unknown["outcome"] = "UNKNOWN"
        result = governance.evaluate_strategy(history([unknown]))
        self.assertEqual(result["state"], "RETRY_NOT_JUSTIFIED")
        self.assertIs(result["next_attempt_allowed"], False)

    def test_every_external_side_effect_stops_after_first_non_success(self):
        for side_effect in (
            "NETWORK",
            "ACCOUNT",
            "CREDENTIAL",
            "PAID",
            "BUSINESS_WRITE",
            "PUSH",
            "PUBLISH",
            "DEPLOY",
            "DELETE",
            "PRODUCTION_MIGRATION",
        ):
            with self.subTest(side_effect=side_effect):
                result = governance.evaluate_strategy(
                    history([failed_attempt(1, ["failure evidence"])], route(side_effect_class=side_effect))
                )
                self.assertEqual(result["state"], "EXTERNAL_STOP")
                self.assertIs(result["next_attempt_allowed"], False)


class RouteComparisonTests(unittest.TestCase):
    def test_requires_two_mechanism_distinct_routes(self):
        with self.assertRaises(governance.StrategyContractError) as caught:
            governance.compare_routes(
                {"schema_version": 1, "alternatives": [alternative("same"), alternative("same")]}
            )
        self.assertEqual(caught.exception.reason, "ROUTE_REASSESSMENT_REQUIRED")

    def test_returns_deterministic_comparison_and_user_decision_boundary(self):
        value = {
            "schema_version": 1,
            "alternatives": [
                alternative("range markdown"),
                alternative("deterministic local analyzer", changes_cost=True),
            ],
        }
        result = governance.compare_routes(value)
        self.assertEqual(result["state"], "ROUTE_COMPARISON_COMPLETE")
        self.assertEqual(len(result["route_ids"]), 2)
        self.assertIs(result["user_decision_required"], True)
        self.assertIs(result["write_authorized"], False)


if __name__ == "__main__":
    unittest.main()
