# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import json
import unittest
from unittest import mock

from workbench import progress_view as governance
from workbench.safe_public_text import render_safe_public_message


SUMMARY_ZERO = {"required": 0, "pass": 0, "fail": 0}
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
EXPECTED_REPORT_STRING_PATHS = frozenset(
    {
        "$.task_id",
        "$.goal_display.safe_summary",
        "$.goal_display.original_goal_sha256",
        "$.goal_display.original_text_state",
        "$.current_phase",
        "$.milestones.completed[*]",
        "$.milestones.current[*]",
        "$.milestones.remaining[*]",
        "$.user_visible_capabilities[*].capability_id",
        "$.user_visible_capabilities[*].name",
        "$.user_visible_capabilities[*].environment",
        "$.user_visible_capabilities[*].status",
        "$.selected_strategy.route_id",
        "$.selected_strategy.mechanism",
        "$.selected_strategy.selection_reason",
        "$.implementation_logic[*]",
        "$.verification.verified[*]",
        "$.verification.warnings[*]",
        "$.verification.unverified[*]",
        "$.verification.unknown[*]",
        "$.strategy_status.route_id",
        "$.strategy_status.evidence_delta[*]",
        "$.strategy_status.state",
        "$.alternative_routes[*].route_id",
        "$.alternative_routes[*].mechanism",
        "$.alternative_routes[*].tradeoff",
        "$.user_participation.reason",
        "$.user_participation.one_next_action",
        "$.user_participation.expected_result",
        "$.user_participation.side_effects",
        "$.user_participation.failure_stop",
        "$.next_step",
        "$.stop_conditions[*]",
        "$.technical_appendix.project_path",
        "$.technical_appendix.branch",
        "$.technical_appendix.head",
        "$.technical_appendix.dirty_fingerprint",
        "$.technical_appendix.files[*]",
        "$.technical_appendix.check_ids[*]",
        "$.technical_appendix.reviewer_result.state",
        "$.technical_appendix.digests[*]",
        "$.technical_appendix.commits[*]",
        "$.technical_appendix.rollback_reference",
        "$.completion_basis.outcome.task_id",
        "$.completion_basis.outcome.state",
        "$.completion_basis.outcome.blocking_outcome_ids[*]",
        "$.completion_basis.outcome.blocking_capability_ids[*]",
        "$.completion_basis.outcome.goal_display.safe_summary",
        "$.completion_basis.outcome.goal_display.original_goal_sha256",
        "$.completion_basis.outcome.goal_display.original_text_state",
        "$.completion_basis.execution.envelope_id",
        "$.completion_basis.execution.task_id",
        "$.completion_basis.execution.reason",
        "$.completion_basis.execution.drift_fields[*]",
        "$.completion_basis.execution.remaining_actions[*]",
        "$.completion_basis.acceptance.task_id",
        "$.completion_basis.acceptance.computed_level",
        "$.completion_basis.acceptance.level",
        "$.completion_basis.acceptance.mode",
        "$.completion_basis.acceptance.acceptance_state",
        "$.completion_basis.acceptance.reason",
        "$.completion_basis.acceptance.action_state",
        "$.completion_state.task_state",
        "$.completion_state.outcome_state",
        "$.completion_state.strategy_state",
        "$.completion_state.acceptance_state",
        "$.completion_state.reason_codes[*]",
        "$.current_conclusion",
    }
)


def requirement_summary(level, *, post_pass=0, post_fail=0, pre_pass=0, pre_fail=0,
                        rollback_pass=0, rollback_fail=0):
    return {
        "PRE_ACTION_APPROVAL": {
            "required": 1 if level == "U3" else 0,
            "pass": pre_pass,
            "fail": pre_fail,
        },
        "POST_ACTION_OBSERVATION": {
            "required": 1 if level in {"U2", "U3"} else 0,
            "pass": post_pass,
            "fail": post_fail,
        },
        "ROLLBACK_VERIFICATION": {
            "required": 1 if level == "U3" else 0,
            "pass": rollback_pass,
            "fail": rollback_fail,
        },
    }


def outcome_decision(*, complete=True):
    return {
        "schema_version": 1,
        "task_id": "TASK-2.2-DEMO",
        "revision": 1,
        "state": "DELIVERY_VERIFIED" if complete else "INCOMPLETE",
        "blocking_outcome_ids": [] if complete else ["O2"],
        "blocking_capability_ids": [] if complete else ["C2"],
        "final_uat_eligible": complete,
        "completion_eligible": False,
        "evidence_references": ["test:pass"],
        "goal_display": {
            "safe_summary": "完成已绑定目标并验证用户可见结果",
            "original_goal_sha256": "9" * 64,
            "original_text_state": "RETAINED_IN_OUTCOME_CONTRACT",
        },
        "write_authorized": False,
    }


def execution_decision():
    return {
        "schema_version": 1,
        "envelope_id": "ENV-2.2-001",
        "task_id": "TASK-2.2-DEMO",
        "eligible": True,
        "reason": "STABLE_RESUME",
        "drift_fields": [],
        "remaining_actions": ["EDIT", "TEST"],
        "authority_created": False,
        "external_write_authorized": False,
        "write_authorized": False,
    }


def acceptance_decision(level="U2", state=None, *, outcome_ready=True):
    state = state or ("USER_OBSERVATION_PENDING" if level == "U2" else "WAIVER_PENDING")
    accepted = state in {"WAIVED_WITH_EVIDENCE", "USER_OBSERVED"}
    action_state = "NOT_REQUIRED"
    summary = requirement_summary(level)
    reason = {
        "BLOCKED_BY_OUTCOME": "CRITICAL_CAPABILITY_UNVERIFIED",
        "WAIVER_PENDING": "WAIVER_EVIDENCE_REQUIRED",
        "WAIVED_WITH_EVIDENCE": "ACCEPTED",
        "USER_OBSERVATION_PENDING": "USER_EVENT_RECEIPT_REQUIRED",
        "USER_OBSERVATION_FAILED": "OBSERVATION_FAILED",
        "U3_PRE_ACTION_PENDING": "U3_PRE_ACTION_REQUIRED",
        "U3_PRE_ACTION_FAILED": "U3_PRE_ACTION_FAILED",
        "U3_ACTION_PENDING": "U3_ACTION_RESULT_REQUIRED",
        "U3_ACTION_FAILED": "U3_ACTION_FAILED",
        "U3_ACTION_UNKNOWN": "U3_ACTION_UNKNOWN",
        "U3_POST_ACTION_PENDING": "USER_EVENT_RECEIPT_REQUIRED",
        "U3_POST_ACTION_FAILED": "U3_POST_ACTION_FAILED",
        "U3_ROLLBACK_VERIFICATION_PENDING": "U3_ROLLBACK_VERIFICATION_REQUIRED",
        "U3_ROLLBACK_VERIFICATION_FAILED": "U3_ROLLBACK_VERIFICATION_FAILED",
        "USER_OBSERVED": "ACCEPTED",
    }[state]
    if state == "USER_OBSERVATION_FAILED":
        summary = requirement_summary(level, post_fail=1)
    elif state == "USER_OBSERVED":
        if level == "U2":
            summary = requirement_summary(level, post_pass=1)
        elif level == "U3":
            summary = requirement_summary(
                level, pre_pass=1, post_pass=1, rollback_pass=1
            )
            action_state = "SUCCEEDED"
    elif level == "U3":
        action_state = "PENDING"
        if state == "U3_PRE_ACTION_FAILED":
            summary = requirement_summary(level, pre_fail=1)
        elif state in {"U3_ACTION_PENDING", "U3_ACTION_FAILED", "U3_ACTION_UNKNOWN"}:
            summary = requirement_summary(level, pre_pass=1)
            action_state = {
                "U3_ACTION_PENDING": "PENDING",
                "U3_ACTION_FAILED": "FAILED",
                "U3_ACTION_UNKNOWN": "UNKNOWN",
            }[state]
        elif state == "U3_POST_ACTION_PENDING":
            summary = requirement_summary(level, pre_pass=1)
            action_state = "SUCCEEDED"
        elif state == "U3_POST_ACTION_FAILED":
            summary = requirement_summary(level, pre_pass=1, post_fail=1)
            action_state = "SUCCEEDED"
        elif state == "U3_ROLLBACK_VERIFICATION_PENDING":
            summary = requirement_summary(level, pre_pass=1, post_pass=1)
            action_state = "SUCCEEDED"
        elif state == "U3_ROLLBACK_VERIFICATION_FAILED":
            summary = requirement_summary(
                level, pre_pass=1, post_pass=1, rollback_fail=1
            )
            action_state = "SUCCEEDED"
    if state == "BLOCKED_BY_OUTCOME":
        accepted = False
        outcome_ready = False
        action_state = "PENDING" if level == "U3" else "NOT_REQUIRED"
    return {
        "schema_version": 1,
        "task_id": "TASK-2.2-DEMO",
        "computed_level": level,
        "level": level,
        "mode": {
            "U0": "WAIVER_REQUIRED",
            "U1": "WAIVER_REQUIRED",
            "U2": "GUIDED_OBSERVATION_REQUIRED",
            "U3": "PRE_ACTION_AND_POST_ACTION_HUMAN_GATE",
        }[level],
        "human_required": level in {"U2", "U3"},
        "reasons": [],
        "outcome_ready": outcome_ready,
        "write_authorized": False,
        "accepted": accepted,
        "acceptance_state": state,
        "reason": reason,
        "requirement_summary": summary,
        "action_state": action_state,
        "completion_eligible": accepted,
    }


def technical_appendix(*, reviewer_state="PASS"):
    return {
        "project_path": "/projects/lead",
        "branch": "feat/vibe-project-lead-zh-2.2",
        "head": "a" * 40,
        "dirty_fingerprint": "b" * 64,
        "files": ["workbench/progress_view.py"],
        "check_ids": ["UNIT:PROGRESS"],
        "test_counts": {
            "total": 10,
            "passed": 10,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        },
        "reviewer_result": {
            "state": reviewer_state,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        },
        "digests": ["sha256:" + "c" * 64],
        "commits": ["d" * 40],
        "rollback_reference": "git-parent:" + "d" * 40,
    }


def valid_progress_input():
    return {
        "schema_version": 1,
        "task_id": "TASK-2.2-DEMO",
        "current_phase": "真人验收准备",
        "milestones": {
            "completed": ["结果合同", "确定性验证"],
            "current": ["真人观察"],
            "remaining": ["用户接受", "合并"],
        },
        "user_visible_capabilities": [
            {
                "capability_id": "C1",
                "name": "分层中文进度卡",
                "environment": "local",
                "status": "VERIFIED",
                "critical": True,
            }
        ],
        "selected_strategy": {
            "route_id": "e" * 64,
            "mechanism": "确定性合同加引导式观察",
            "selection_reason": "兼顾可验证性和真人体验",
        },
        "implementation_logic": ["先验证关键能力", "再决定真人验收等级"],
        "verification": {
            "verified": ["聚焦测试通过"],
            "warnings": ["真实安装尚未执行"],
            "unverified": ["用户观察尚未填写"],
            "unknown": [],
        },
        "strategy_status": {
            "route_id": "e" * 64,
            "failure_count": 0,
            "evidence_delta": [],
            "state": "READY",
        },
        "alternative_routes": [],
        "user_participation": {
            "required": True,
            "reason": "用户可见工作流属于 U2",
            "one_next_action": "观察一次代表场景",
            "expected_result": "能看懂功能、问题和下一步",
            "side_effects": "只读，无外部动作",
            "failure_stop": "任何结果不清楚就停止并记录 FAIL",
        },
        "next_step": "执行一项引导式真人观察",
        "stop_conditions": ["关键事实漂移", "发现 Critical/High"],
        "technical_appendix": technical_appendix(),
        "outcome_decision": outcome_decision(),
        "execution_decision": execution_decision(),
        "acceptance_decision": acceptance_decision(),
    }


def nested_value(depth):
    value = "leaf"
    for _ in range(depth):
        value = {"layer": value}
    return value


def string_leaf_paths(value, actual=(), normalized="$"):
    if isinstance(value, str):
        return [(actual, normalized)]
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            result.extend(string_leaf_paths(item, actual + (key,), f"{normalized}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(string_leaf_paths(item, actual + (index,), f"{normalized}[*]"))
    return result


def set_at_path(value, path, replacement):
    target = value
    for item in path[:-1]:
        target = target[item]
    target[path[-1]] = replacement


def report_inventory_variants():
    variants = [governance.build_progress_report(valid_progress_input())]

    blocked = valid_progress_input()
    blocked["user_visible_capabilities"][0]["status"] = "MISSING"
    blocked["outcome_decision"].update(
        state="INCOMPLETE",
        blocking_outcome_ids=["O1"],
        blocking_capability_ids=["C1"],
        final_uat_eligible=False,
    )
    blocked["acceptance_decision"] = acceptance_decision(
        "U2", "BLOCKED_BY_OUTCOME", outcome_ready=False
    )
    variants.append(governance.build_progress_report(blocked))

    populated = valid_progress_input()
    populated["verification"]["unknown"] = ["结果仍为 UNKNOWN"]
    populated["strategy_status"]["evidence_delta"] = ["新增确定性证据"]
    populated["alternative_routes"] = [
        {"route_id": "1" * 64, "mechanism": "备用路线", "tradeoff": "需要更多时间"}
    ]
    populated["acceptance_decision"]["reasons"] = ["BUSINESS_VISIBLE"]
    variants.append(governance.build_progress_report(populated))

    drifted = valid_progress_input()
    drifted["execution_decision"].update(
        eligible=False,
        reason="RESUME_ANCHOR_DRIFT",
        drift_fields=["authority_project.head"],
        remaining_actions=[],
    )
    variants.append(governance.build_progress_report(drifted))
    return variants


class ProgressContractTests(unittest.TestCase):
    def assert_rejected(self, value, reason="PROGRESS_INPUT_INCONSISTENT"):
        with self.assertRaises(governance.ProgressContractError) as caught:
            governance.build_progress_report(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_builder_final_scan_checks_returned_report(self):
        value = valid_progress_input()
        before = copy.deepcopy(value)
        with mock.patch.object(
            governance, "_validate_public_strings",
            wraps=governance._validate_public_strings,
        ) as scanner:
            report = governance.build_progress_report(value)
        scanner.assert_called_once_with(report)
        self.assertEqual(value, before)
        self.assertIs(report["write_authorized"], False)

    def test_builder_rejects_unsafe_text_added_during_final_assembly(self):
        original_order = governance._deterministic_report
        placements = (
            ("implementation_logic", 0),
            ("completion_basis", "outcome", "goal_display", "safe_summary"),
            ("technical_appendix", "branch"),
        )
        payloads = (
            "FEATURE_FLAG=local-test-value",
            "ＦＥＡＴＵＲＥ＿ＦＬＡＧ＝local-test-value",
        )
        for path in placements:
            for payload in payloads:
                value = valid_progress_input()
                before = copy.deepcopy(value)

                def inject(report):
                    result = original_order(report)
                    target = result
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = payload
                    return result

                with self.subTest(path=path, payload=payload):
                    with mock.patch.object(
                        governance, "_deterministic_report", side_effect=inject,
                    ):
                        with self.assertRaises(governance.ProgressContractError) as caught:
                            governance.build_progress_report(value)
                    self.assertEqual(caught.exception.reason, "SECRET_VALUE_REJECTED")
                    self.assertNotIn(payload, str(caught.exception))
                    self.assertEqual(value, before)

    def test_builds_exact_report_without_mutating_input(self):
        value = valid_progress_input()
        before = copy.deepcopy(value)
        report = governance.build_progress_report(value)
        self.assertEqual(value, before)
        self.assertEqual(set(report), governance.REPORT_FIELDS)
        self.assertIs(report["write_authorized"], False)
        self.assertNotIn("outcome_decision", report)
        self.assertNotIn("original_goal", report)
        self.assertEqual(report["goal_display"], outcome_decision()["goal_display"])
        self.assertEqual(
            report["goal_display"], report["completion_basis"]["outcome"]["goal_display"]
        )
        self.assertIn("completion_basis", report)

    def test_rejects_legacy_goal_and_caller_supplied_goal_display(self):
        for field, value in (
            ("original_goal", "不应进入 Progress 的原始目标"),
            ("goal_display", copy.deepcopy(outcome_decision()["goal_display"])),
        ):
            progress = valid_progress_input()
            progress[field] = value
            with self.subTest(field=field):
                self.assert_rejected(progress, "SCHEMA_FIELDS_CHANGED")

    def test_public_renderers_contain_only_derived_goal_display(self):
        raw_goal = "RAW-GOAL-UNIQUE-MUST-NOT-APPEAR"
        report = governance.build_progress_report(valid_progress_input())
        json_text = governance.render_progress_json(report)
        markdown = governance.render_progress_markdown(report)
        for rendered in (json_text, markdown):
            self.assertNotIn(raw_goal, rendered)
            self.assertNotIn("original_goal\"", rendered)
            self.assertIn("9" * 64, rendered)
        self.assertIn("RETAINED_IN_OUTCOME_CONTRACT", json_text)
        self.assertIn(r"RETAINED\_IN\_OUTCOME\_CONTRACT", markdown)

    def test_closed_authentication_message_is_safe_but_does_not_complete_the_task(self):
        message = render_safe_public_message("AUTHORIZATION_VALUE_HIDDEN")
        value = valid_progress_input()
        value["implementation_logic"][0] = message

        report = governance.build_progress_report(value)
        json_text = governance.render_progress_json(report)
        markdown = governance.render_progress_markdown(report)

        self.assertIn(message, json_text)
        self.assertIn(message, markdown)
        self.assertEqual(report["completion_state"]["task_state"], "WAITING_USER")
        self.assertIs(report["completion_state"]["completion_eligible"], False)
        self.assertIs(report["write_authorized"], False)

    def test_builder_derives_complete_waiting_and_incomplete_states(self):
        waiting = governance.build_progress_report(valid_progress_input())
        self.assertEqual(waiting["completion_state"]["task_state"], "WAITING_USER")
        self.assertEqual(
            waiting["current_conclusion"],
            "技术前置条件已满足，正在等待所需用户操作或观察",
        )

        complete_input = valid_progress_input()
        complete_input["acceptance_decision"] = acceptance_decision(
            "U2", "USER_OBSERVED"
        )
        complete = governance.build_progress_report(complete_input)
        self.assertEqual(complete["completion_state"]["task_state"], "COMPLETE")

        incomplete_input = valid_progress_input()
        incomplete_input["technical_appendix"] = technical_appendix(
            reviewer_state="PENDING"
        )
        incomplete = governance.build_progress_report(incomplete_input)
        self.assertEqual(incomplete["completion_state"]["task_state"], "INCOMPLETE")

    def test_only_human_wait_states_can_be_waiting_user(self):
        for state in (
            "USER_OBSERVATION_PENDING",
            "U3_PRE_ACTION_PENDING",
            "U3_POST_ACTION_PENDING",
            "U3_ROLLBACK_VERIFICATION_PENDING",
        ):
            value = valid_progress_input()
            level = "U2" if state == "USER_OBSERVATION_PENDING" else "U3"
            value["acceptance_decision"] = acceptance_decision(level, state)
            value["user_participation"]["required"] = True
            with self.subTest(state=state):
                report = governance.build_progress_report(value)
                self.assertEqual(report["completion_state"]["task_state"], "WAITING_USER")

        value = valid_progress_input()
        value["acceptance_decision"] = acceptance_decision("U3", "U3_ACTION_PENDING")
        self.assertEqual(
            governance.build_progress_report(value)["completion_state"]["task_state"],
            "INCOMPLETE",
        )

    def test_current_conclusion_is_not_caller_controlled(self):
        value = valid_progress_input()
        value["current_conclusion"] = "任务已完成"
        self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

    def test_synonyms_cannot_bypass_structured_completion(self):
        value = valid_progress_input()
        value["outcome_decision"]["goal_display"]["safe_summary"] = "宣布全部完成并交付完成"
        value["technical_appendix"] = technical_appendix(reviewer_state="PENDING")
        report = governance.build_progress_report(value)
        self.assertEqual(report["completion_state"]["task_state"], "INCOMPLETE")
        self.assertEqual(report["current_conclusion"], "用户目标尚未完成")

    def test_all_execution_authority_flags_must_be_false(self):
        for field in ("authority_created", "external_write_authorized", "write_authorized"):
            value = valid_progress_input()
            value["execution_decision"][field] = True
            with self.subTest(field=field):
                self.assert_rejected(value)

    def test_execution_resume_truth_table_is_closed(self):
        legal_rows = (
            ("STABLE_RESUME", True, [], ["EDIT", "TEST"], "WAITING_USER"),
            ("TASK_OR_ENVELOPE_CHANGED", False, ["resume_event"], [], "INCOMPLETE"),
            ("RESUME_ANCHOR_DRIFT", False, ["authority_project.head"], [], "INCOMPLETE"),
            ("ENVELOPE_INACTIVE", False, [], [], "INCOMPLETE"),
        )
        for reason, eligible, drift, remaining, expected_state in legal_rows:
            value = valid_progress_input()
            value["execution_decision"].update(
                reason=reason,
                eligible=eligible,
                drift_fields=drift,
                remaining_actions=remaining,
            )
            with self.subTest(reason=reason):
                report = governance.build_progress_report(value)
                self.assertEqual(report["completion_state"]["task_state"], expected_state)
                self.assertEqual(report["completion_basis"]["execution"]["drift_fields"], drift)
                self.assertEqual(
                    report["completion_basis"]["execution"]["remaining_actions"], remaining
                )

    def test_execution_resume_truth_table_rejects_contradictions(self):
        invalid_rows = (
            ("UNKNOWN_REASON", True, [], ["EDIT"]),
            ("TASK_OR_ENVELOPE_CHANGED", True, ["resume_event"], []),
            ("STABLE_RESUME", False, [], []),
            ("STABLE_RESUME", True, ["resume_event"], ["EDIT"]),
            ("ENVELOPE_INACTIVE", False, [], ["EDIT"]),
            ("TASK_OR_ENVELOPE_CHANGED", False, [], []),
            ("TASK_OR_ENVELOPE_CHANGED", False, ["resume_event", "resume_event"], []),
            ("RESUME_ANCHOR_DRIFT", False, ["task_id", "authority_project.head"], []),
            ("STABLE_RESUME", True, [], ["UNKNOWN_ACTION"]),
        )
        for reason, eligible, drift, remaining in invalid_rows:
            value = valid_progress_input()
            value["execution_decision"].update(
                reason=reason,
                eligible=eligible,
                drift_fields=drift,
                remaining_actions=remaining,
            )
            with self.subTest(reason=reason, eligible=eligible, drift=drift):
                self.assert_rejected(value, "EXECUTION_DECISION_INCONSISTENT")

    def test_renderers_reject_false_complete_execution_mutations(self):
        value = valid_progress_input()
        value["acceptance_decision"] = acceptance_decision("U2", "USER_OBSERVED")
        base = governance.build_progress_report(value)
        mutations = (
            ("reason", "ENVELOPE_INACTIVE"),
            ("eligible", False),
            ("drift_fields", ["authority_project.head"]),
            ("remaining_actions", ["UNKNOWN_ACTION"]),
        )
        for field, changed in mutations:
            report = copy.deepcopy(base)
            report["completion_basis"]["execution"][field] = changed
            with self.subTest(field=field):
                for renderer in (
                    governance.render_progress_json,
                    governance.render_progress_markdown,
                ):
                    with self.assertRaises(governance.ProgressContractError) as caught:
                        renderer(copy.deepcopy(report))
                    self.assertEqual(
                        caught.exception.reason, "EXECUTION_DECISION_INCONSISTENT"
                    )

    def test_rejects_unknown_fields_boolean_integer_confusion_and_bad_counts(self):
        value = valid_progress_input()
        value["extra"] = "no"
        self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

        value = valid_progress_input()
        value["strategy_status"]["failure_count"] = True
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

        value = valid_progress_input()
        value["technical_appendix"]["test_counts"]["passed"] = 9
        self.assert_rejected(value)

    def test_technical_test_and_reviewer_counts_are_not_limited_to_128(self):
        value = valid_progress_input()
        value["technical_appendix"]["test_counts"].update(
            total=751,
            passed=749,
            failed=0,
            errors=0,
            skipped=2,
        )
        value["technical_appendix"]["reviewer_result"]["medium"] = 129

        report = governance.build_progress_report(value)

        self.assertEqual(report["technical_appendix"]["test_counts"]["total"], 751)
        self.assertEqual(
            report["technical_appendix"]["reviewer_result"]["medium"], 129
        )

    def test_depth_cycle_alias_nodes_and_surrogate_fail_closed(self):
        value = valid_progress_input()
        value["implementation_logic"] = [nested_value(1500)]
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

        governance._validate_bounded_tree(nested_value(16))
        cycle = {}
        cycle["self"] = cycle
        shared = ["value"]
        cases = (nested_value(17), ["item"] * 129, cycle,
                 {"first": shared, "second": shared},
                 [list(range(32)) for _ in range(128)], "\ud800")
        for item in cases:
            with self.subTest(value_type=type(item).__name__):
                with self.assertRaises(governance.ProgressContractError) as caught:
                    governance._validate_bounded_tree(item)
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_cross_contract_task_route_capabilities_and_uat_must_agree(self):
        mutations = (
            ("outcome_decision", "task_id", "OTHER"),
            ("execution_decision", "task_id", "OTHER"),
            ("acceptance_decision", "task_id", "OTHER"),
            ("selected_strategy", "route_id", "f" * 64),
            ("acceptance_decision", "outcome_ready", False),
        )
        for section, field, changed in mutations:
            value = valid_progress_input()
            value[section][field] = changed
            with self.subTest(section=section, field=field):
                self.assert_rejected(value)

    def test_acceptance_semantics_are_closed_for_u0_u1_u2_and_u3(self):
        valid_states = {
            "U0": ("WAIVER_PENDING", "WAIVED_WITH_EVIDENCE"),
            "U1": ("WAIVER_PENDING", "WAIVED_WITH_EVIDENCE"),
            "U2": (
                "USER_OBSERVATION_PENDING",
                "USER_OBSERVATION_FAILED",
                "USER_OBSERVED",
            ),
            "U3": (
                "U3_PRE_ACTION_PENDING",
                "U3_PRE_ACTION_FAILED",
                "U3_ACTION_PENDING",
                "U3_ACTION_FAILED",
                "U3_ACTION_UNKNOWN",
                "U3_POST_ACTION_PENDING",
                "U3_POST_ACTION_FAILED",
                "U3_ROLLBACK_VERIFICATION_PENDING",
                "U3_ROLLBACK_VERIFICATION_FAILED",
                "USER_OBSERVED",
            ),
        }
        for level, states in valid_states.items():
            for state in states:
                value = valid_progress_input()
                value["acceptance_decision"] = acceptance_decision(level, state)
                value["user_participation"]["required"] = level in {"U2", "U3"}
                with self.subTest(level=level, state=state):
                    governance.build_progress_report(value)

    def test_invalid_acceptance_counters_states_and_reasons_are_rejected(self):
        mutations = []
        zero_u2 = valid_progress_input()
        zero_u2["acceptance_decision"]["requirement_summary"][
            "POST_ACTION_OBSERVATION"
        ]["required"] = 0
        mutations.append(zero_u2)
        over_pass = valid_progress_input()
        over_pass["acceptance_decision"]["requirement_summary"][
            "POST_ACTION_OBSERVATION"
        ]["pass"] = 2
        mutations.append(over_pass)
        wrong_action = valid_progress_input()
        wrong_action["acceptance_decision"]["action_state"] = "SUCCEEDED"
        mutations.append(wrong_action)
        wrong_reason = valid_progress_input()
        wrong_reason["acceptance_decision"]["reason"] = "ACCEPTED"
        mutations.append(wrong_reason)
        for value in mutations:
            self.assert_rejected(value)

    def test_u3_rejects_rollback_counts_before_post_observations_pass(self):
        cases = (
            ("U3_ACTION_PENDING", "pass"),
            ("U3_POST_ACTION_PENDING", "pass"),
            ("U3_POST_ACTION_FAILED", "fail"),
        )
        for state, rollback_result in cases:
            value = valid_progress_input()
            value["acceptance_decision"] = acceptance_decision("U3", state)
            value["acceptance_decision"]["requirement_summary"][
                "ROLLBACK_VERIFICATION"
            ][rollback_result] = 1
            value["user_participation"]["required"] = True
            with self.subTest(state=state, rollback_result=rollback_result):
                self.assert_rejected(value, "PROGRESS_INPUT_INCONSISTENT")

    def test_u3_early_states_reject_all_future_observation_counts(self):
        future_counts = {
            "U3_PRE_ACTION_PENDING": (
                ("POST_ACTION_OBSERVATION", "pass"),
                ("POST_ACTION_OBSERVATION", "fail"),
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
            "U3_PRE_ACTION_FAILED": (
                ("POST_ACTION_OBSERVATION", "pass"),
                ("POST_ACTION_OBSERVATION", "fail"),
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
            "U3_ACTION_PENDING": (
                ("POST_ACTION_OBSERVATION", "pass"),
                ("POST_ACTION_OBSERVATION", "fail"),
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
            "U3_ACTION_FAILED": (
                ("POST_ACTION_OBSERVATION", "pass"),
                ("POST_ACTION_OBSERVATION", "fail"),
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
            "U3_ACTION_UNKNOWN": (
                ("POST_ACTION_OBSERVATION", "pass"),
                ("POST_ACTION_OBSERVATION", "fail"),
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
            "U3_POST_ACTION_PENDING": (
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
            "U3_POST_ACTION_FAILED": (
                ("ROLLBACK_VERIFICATION", "pass"),
                ("ROLLBACK_VERIFICATION", "fail"),
            ),
        }
        for state, mutations in future_counts.items():
            for requirement, result in mutations:
                value = valid_progress_input()
                value["acceptance_decision"] = acceptance_decision("U3", state)
                value["acceptance_decision"]["requirement_summary"][requirement][
                    result
                ] = 1
                value["user_participation"]["required"] = True
                with self.subTest(
                    state=state, requirement=requirement, result=result
                ):
                    self.assert_rejected(value, "PROGRESS_INPUT_INCONSISTENT")

    def test_fourth_failure_requires_two_alternative_routes(self):
        value = valid_progress_input()
        value["strategy_status"].update(
            failure_count=4, state="ROUTE_REASSESSMENT_REQUIRED"
        )
        self.assert_rejected(value)
        value["alternative_routes"] = [
            {"route_id": "1" * 64, "mechanism": "路线 A", "tradeoff": "更慢但简单"},
            {"route_id": "2" * 64, "mechanism": "路线 B", "tradeoff": "更快但复杂"},
        ]
        self.assertEqual(
            governance.build_progress_report(value)["strategy_status"]["failure_count"],
            4,
        )

    def test_rejects_sensitive_values_inside_allowed_text_fields(self):
        sensitive = (
            "https://example.com/private",
            "ssh://host/path",
            "git@example.com:repo.git",
            "Authorization: Bearer abc",
            "Authorization: Basic abc",
            "Cookie: session=abc",
            "-----BEGIN PRIVATE KEY-----",
            "API_KEY=value",
            "PASSWORD=value",
            "diff --git a/a b/a",
            "?? secret.txt",
            "（API_KEY=value）",
            '"TOKEN=value"',
            "说明中泄漏 API_KEY=value 不允许",
            "> diff --git a/a b/a",
            ">   @@ -1 +1 @@",
            "    ?? secret.txt",
            "> \t?? secret.txt",
            "DATABASE_URL=postgresql://user:password@db/private",
            "FEATURE_FLAG=true",
            "export ORDINARY_NAME=value",
            "（DATABASE_URL=local-value）",
            "- DATABASE_URL=local-value",
            "* diff --git a/private b/private",
            "- >   @@ -1 +1 @@",
            "> 1. ?? secret.txt",
            "  * > ?? secret.txt",
            *PROJECTION_EQUIVALENT_DANGERS,
        )
        placements = (
            lambda value, text: value["outcome_decision"]["goal_display"].__setitem__("safe_summary", text),
            lambda value, text: value["implementation_logic"].__setitem__(0, text),
            lambda value, text: value["verification"]["warnings"].__setitem__(0, text),
            lambda value, text: value["user_participation"].__setitem__("reason", text),
        )
        for text in sensitive:
            for place in placements:
                value = valid_progress_input()
                place(value, text)
                with self.subTest(text=text, place=place):
                    self.assert_rejected(value, "SECRET_VALUE_REJECTED")

        baseline = governance.build_progress_report(valid_progress_input())
        control = valid_progress_input()
        safe_controls = [
            "中文标点：可以。",
            "/projects/lead",
            "UNIT:PROGRESS",
            "a" * 64,
            "环境变量名称 DATABASE_URL（未展示值）",
            "使用结构化字段说明配置状态",
            "列表项 - 普通说明，不包含原始差异",
        ]
        control["implementation_logic"] = safe_controls
        report = governance.build_progress_report(control)
        self.assertEqual(report["implementation_logic"], safe_controls)
        self.assertEqual(report["completion_state"], baseline["completion_state"])
        self.assertEqual(
            report["completion_basis"]["execution"],
            baseline["completion_basis"]["execution"],
        )
        self.assertEqual(report["strategy_status"], baseline["strategy_status"])
        self.assertEqual(
            report["completion_basis"]["acceptance"]["requirement_summary"],
            baseline["completion_basis"]["acceptance"]["requirement_summary"],
        )
        self.assertIs(report["write_authorized"], False)

    def test_renderers_reject_new_sensitive_equivalent_forms(self):
        sensitive = (
            "DATABASE_URL=local-value",
            "- diff --git a/private b/private",
            "* ?? secret.txt",
            *PROJECTION_EQUIVALENT_DANGERS,
        )
        for text in sensitive:
            for renderer in (
                governance.render_progress_json,
                governance.render_progress_markdown,
            ):
                report = governance.build_progress_report(valid_progress_input())
                report["implementation_logic"][0] = text
                with self.subTest(text=text, renderer=renderer.__name__):
                    with self.assertRaises(governance.ProgressContractError) as caught:
                        renderer(report)
                    self.assertEqual(caught.exception.reason, "SECRET_VALUE_REJECTED")


class ProgressRenderingTests(unittest.TestCase):
    def assert_render_rejected(self, report):
        for renderer in (governance.render_progress_json, governance.render_progress_markdown):
            with self.subTest(renderer=renderer.__name__):
                with self.assertRaises(governance.ProgressContractError) as caught:
                    renderer(copy.deepcopy(report))
                self.assertEqual(caught.exception.reason, "PROGRESS_INPUT_INCONSISTENT")

    def test_renderer_rejects_mutated_completion_or_basis(self):
        base = governance.build_progress_report(valid_progress_input())
        mutations = []
        changed_completion = copy.deepcopy(base)
        changed_completion["completion_state"]["task_state"] = "COMPLETE"
        mutations.append(changed_completion)
        changed_outcome = copy.deepcopy(base)
        changed_outcome["completion_basis"]["outcome"]["state"] = "INCOMPLETE"
        mutations.append(changed_outcome)
        changed_execution = copy.deepcopy(base)
        changed_execution["completion_basis"]["execution"].update(
            eligible=False,
            reason="ENVELOPE_INACTIVE",
            drift_fields=[],
            remaining_actions=[],
        )
        mutations.append(changed_execution)
        changed_acceptance = copy.deepcopy(base)
        changed_acceptance["completion_basis"]["acceptance"]["accepted"] = True
        mutations.append(changed_acceptance)
        changed_summary = copy.deepcopy(base)
        changed_summary["completion_basis"]["acceptance"]["requirement_summary"][
            "POST_ACTION_OBSERVATION"
        ]["pass"] = 1
        mutations.append(changed_summary)
        changed_conclusion = copy.deepcopy(base)
        changed_conclusion["current_conclusion"] = "用户目标已完成并通过所需验收"
        mutations.append(changed_conclusion)
        for report in mutations:
            self.assert_render_rejected(report)

    def test_renderer_rederives_capability_strategy_verification_and_technical_gates(self):
        base = governance.build_progress_report(valid_progress_input())
        mutations = []
        capability = copy.deepcopy(base)
        capability["user_visible_capabilities"][0]["status"] = "MISSING"
        mutations.append(capability)
        strategy = copy.deepcopy(base)
        strategy["strategy_status"]["state"] = "EXTERNAL_STOP"
        mutations.append(strategy)
        verification = copy.deepcopy(base)
        verification["verification"]["unknown"] = ["审查 UNKNOWN"]
        mutations.append(verification)
        tests = copy.deepcopy(base)
        tests["technical_appendix"]["test_counts"].update(passed=9, failed=1)
        mutations.append(tests)
        reviewer = copy.deepcopy(base)
        reviewer["technical_appendix"]["reviewer_result"].update(
            state="STOP_CRITICAL_HIGH", high=1
        )
        mutations.append(reviewer)
        for report in mutations:
            self.assert_render_rejected(report)

    def test_markdown_leads_with_management_summary_and_technical_appendix_last(self):
        report = governance.build_progress_report(valid_progress_input())
        text = governance.render_progress_markdown(report)
        headings = [
            "## 当前结论",
            "## 当前阶段与总体进度",
            "## 用户可见能力与状态",
            "## 当前方案",
            "## 实现逻辑",
            "## 证据与未知",
            "## 问题与路线尝试",
            "## 备选路线",
            "## 是否需要你参与",
            "## 下一步",
            "## 技术附录",
        ]
        positions = [text.index(item) for item in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("WAITING\\_USER", text)

    def test_first_section_explains_current_action_before_machine_evidence(self):
        report = governance.build_progress_report(valid_progress_input())
        text = governance.render_progress_markdown(report)
        front = text.split("## 当前阶段与总体进度", 1)[0]
        for expected in (
            "完成已绑定目标并验证用户可见结果",
            "技术前置条件已满足，正在等待所需用户操作或观察",
            "执行一项引导式真人观察",
            "选择当前方案的理由是：兼顾可验证性和真人体验。",
            "观察一次代表场景",
            "预期结果是：能看懂功能、问题和下一步",
            "操作影响：只读，无外部动作",
            "停止条件：任何结果不清楚就停止并记录 FAIL",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, front)
        conclusion_paragraph = next(
            paragraph for paragraph in front.split("\n\n")
            if report["current_conclusion"] in paragraph
        )
        self.assertIn(report["selected_strategy"]["selection_reason"], conclusion_paragraph)
        self.assertIn(report["next_step"], conclusion_paragraph)
        for technical_value in (
            "9" * 64,
            "RETAINED\\_IN\\_OUTCOME\\_CONTRACT",
            "WAITING\\_USER",
            "原因代码",
            "用户可见工作流属于 U2",
        ):
            with self.subTest(technical_value=technical_value):
                self.assertNotIn(technical_value, front)

    def test_summary_distinguishes_future_participation_from_current_action(self):
        report = report_inventory_variants()[1]
        report["user_participation"].update(
            required=True,
            reason="功能补齐后需要用户观察",
            one_next_action="现在不用操作，先由主管补齐功能",
            expected_result="功能验证后再提供观察步骤",
        )
        text = governance.render_progress_markdown(report)
        front, details = text.split("## 当前阶段与总体进度", 1)
        self.assertIn("用户目标尚未完成", front)
        self.assertIn("现在不用操作，先由主管补齐功能", front)
        self.assertNotIn("用户参与要求：需要", front)
        self.assertIn("功能补齐后需要用户观察", details)

    def test_summary_keeps_warnings_and_unknown_visible_before_details(self):
        report = report_inventory_variants()[2]
        text = governance.render_progress_markdown(report)
        front = text.split("## 当前阶段与总体进度", 1)[0]
        for field in ("warnings", "unknown"):
            for item in report["verification"][field]:
                with self.subTest(field=field, item=item):
                    self.assertIn(governance._markdown_text(item), front)
        self.assertIn("## 需要注意", front)
        self.assertIn("## 尚不能确认", front)

    def test_details_remain_readable_without_html_disclosure_support(self):
        report = governance.build_progress_report(valid_progress_input())
        report["selected_strategy"]["selection_reason"] = "说明含有 </details> 也只是文字"
        before = governance.render_progress_json(report)
        text = governance.render_progress_markdown(report)
        self.assertNotIn("<details>", text)
        self.assertNotIn("<summary>", text)
        self.assertNotIn("</details>", text)
        self.assertIn(r"\</details\>", text)
        self.assertLess(text.index("## 你的参与"), text.index("## 当前阶段与总体进度"))
        self.assertLess(text.index("## 当前阶段与总体进度"), text.index("## 技术附录"))
        self.assertIn(report["verification"]["unverified"][0], text)
        self.assertIn("```json\n", text)
        self.assertEqual(governance.render_progress_json(report), before)

    def test_report_documents_keep_summary_short_and_preserve_full_details(self):
        self.assertTrue(callable(getattr(governance, "render_progress_documents", None)))
        report = governance.build_progress_report(valid_progress_input())
        before = governance.render_progress_json(report)
        full = governance.render_progress_markdown(report)
        documents = governance.render_progress_documents(report)
        self.assertEqual(set(documents), {"progress.md", "progress-details.md"})
        summary, details = documents["progress.md"], documents["progress-details.md"]
        for expected in ("技术前置条件已满足", "兼顾可验证性和真人体验", "执行一项引导式真人观察", "## 你的参与", "真实安装尚未执行"):
            self.assertIn(expected, summary)
        self.assertNotIn("## 当前阶段与总体进度", summary)
        self.assertNotIn("## 技术附录", summary)
        self.assertNotIn("9" * 64, summary)
        self.assertIn("[查看详细进度与依据](progress-details.md)", summary)
        self.assertIn("[返回进度摘要](progress.md)", details)
        full_summary, full_details = full.split("## 当前阶段与总体进度\n\n", 1)
        self.assertTrue(summary.startswith(full_summary.rstrip()))
        self.assertTrue(details.endswith("## 当前阶段与总体进度\n\n" + full_details))
        self.assertNotIn("<details>", summary + details)
        self.assertEqual(governance.render_progress_json(report), before)

    def test_report_documents_keep_unknown_visible_and_reject_invalid_report(self):
        self.assertTrue(callable(getattr(governance, "render_progress_documents", None)))
        report = report_inventory_variants()[2]
        documents = governance.render_progress_documents(report)
        for field in ("warnings", "unknown"):
            for item in report["verification"][field]:
                self.assertIn(item, documents["progress.md"])
        invalid = copy.deepcopy(report)
        invalid["current_conclusion"] = "用户目标已完成并通过所需验收"
        with self.assertRaises(governance.ProgressContractError):
            governance.render_progress_documents(invalid)

    def test_markdown_keeps_goal_and_completion_evidence_in_appendix(self):
        report = governance.build_progress_report(valid_progress_input())
        before = governance.render_progress_json(report)
        text = governance.render_progress_markdown(report)
        summary, appendix = text.split("## 技术附录", 1)
        self.assertNotIn("9" * 64, summary)
        self.assertIn("9" * 64, appendix)
        self.assertIn("RETAINED\\_IN\\_OUTCOME\\_CONTRACT", appendix)
        self.assertIn("WAITING\\_USER", appendix)
        self.assertIn("原因代码", appendix)
        for reason in report["completion_state"]["reason_codes"]:
            self.assertIn(reason.replace("_", "\\_"), appendix)
        technical_json = appendix.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(technical_json), report["technical_appendix"])
        self.assertEqual(governance.render_progress_json(report), before)

    def test_report_string_inventory_is_explicit_and_complete(self):
        observed = {
            normalized
            for report in report_inventory_variants()
            for _, normalized in string_leaf_paths(report)
        }
        self.assertEqual(observed, EXPECTED_REPORT_STRING_PATHS)

    def test_every_exported_string_rejects_each_prohibited_payload(self):
        payloads = (
            "> - [ ] FEATURE_FLAG=true",
            "- `FEATURE_FLAG=true`",
            "- [x] diff --git a/private b/private",
            "custom://host/private",
            "Authorization: Bearer private-value",
            "Authorizationopaque",
            "Authorization私密值",
            "Ａｕｔｈｏｒｉｚａｔｉｏｎopaque",
            "Auth\u200borizationopaque",
            "FEATURE_FLAG=true",
            "@@ -1 +1 @@",
            "?? private.txt",
            *PROJECTION_EQUIVALENT_DANGERS,
        )
        reports = report_inventory_variants()
        leaves = {}
        for report_index, report in enumerate(reports):
            for path, normalized in string_leaf_paths(report):
                leaves.setdefault(normalized, (report_index, path))
        self.assertEqual(set(leaves), EXPECTED_REPORT_STRING_PATHS)
        for normalized, (report_index, path) in leaves.items():
            for payload in payloads:
                mutated = copy.deepcopy(reports[report_index])
                set_at_path(mutated, path, payload)
                for renderer in (
                    governance.render_progress_json,
                    governance.render_progress_markdown,
                ):
                    with self.subTest(path=normalized, payload=payload, renderer=renderer.__name__):
                        with self.assertRaises(governance.ProgressContractError) as caught:
                            renderer(mutated)
                        self.assertEqual(caught.exception.reason, "SECRET_VALUE_REJECTED")

    def test_renderers_revalidate_goal_basis_free_text_and_technical_strings(self):
        base = governance.build_progress_report(valid_progress_input())
        mutations = []
        top_goal = copy.deepcopy(base)
        top_goal["goal_display"]["safe_summary"] = "另一个安全摘要"
        mutations.append((top_goal, "PROGRESS_INPUT_INCONSISTENT"))
        basis_goal = copy.deepcopy(base)
        basis_goal["completion_basis"]["outcome"]["goal_display"][
            "safe_summary"
        ] = "另一个安全摘要"
        mutations.append((basis_goal, "PROGRESS_INPUT_INCONSISTENT"))
        conclusion = copy.deepcopy(base)
        conclusion["current_conclusion"] = "尚未形成固定裁决"
        mutations.append((conclusion, "PROGRESS_INPUT_INCONSISTENT"))
        free_text = copy.deepcopy(base)
        free_text["verification"]["warnings"][0] = "- [ ] FEATURE_FLAG=true"
        mutations.append((free_text, "SECRET_VALUE_REJECTED"))
        technical = copy.deepcopy(base)
        technical["technical_appendix"]["project_path"] = "custom://private"
        mutations.append((technical, "SECRET_VALUE_REJECTED"))
        for report, reason in mutations:
            for renderer in (
                governance.render_progress_json,
                governance.render_progress_markdown,
            ):
                with self.subTest(reason=reason, renderer=renderer.__name__):
                    with self.assertRaises(governance.ProgressContractError) as caught:
                        renderer(copy.deepcopy(report))
                    self.assertEqual(caught.exception.reason, reason)

    def test_markdown_escapes_safe_caller_structure(self):
        value = valid_progress_input()
        value["current_phase"] = "阶段 #1 [只读] *通过* | 保持原义"
        text = governance.render_progress_markdown(
            governance.build_progress_report(value)
        )
        self.assertIn(r"阶段 \#1 \[只读\] \*通过\* \| 保持原义", text)

    def test_json_is_canonical_and_round_trips(self):
        report = governance.build_progress_report(valid_progress_input())
        first = governance.render_progress_json(report)
        second = governance.render_progress_json(copy.deepcopy(report))
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), report)


if __name__ == "__main__":
    unittest.main()
