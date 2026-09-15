# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import hashlib
import json
import unittest

from workbench import execution_governance as governance


def canonical_digest(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def refresh_digests(envelope):
    project = envelope["authority_project"]
    project["worktree_identity"] = canonical_digest(
        {
            "git_common_dir": project["git_common_dir"],
            "git_dir": project["git_dir"],
            "physical_path": project["physical_path"],
        }
    )
    envelope["allowlist_digest"] = canonical_digest(
        {
            "behavior_allowlist": sorted(
                envelope["behavior_allowlist"], key=lambda item: item.encode("utf-8")
            ),
            "file_allowlist": sorted(
                envelope["allowlist"], key=lambda item: item.encode("utf-8")
            ),
        }
    )
    envelope["permission_scope_digest"] = canonical_digest(
        {
            "allowed_actions": sorted(
                envelope["allowed_actions"], key=lambda item: item.encode("utf-8")
            ),
            "authority_source": envelope["authority_source"],
            "reviewer_budget": envelope["reviewer_budget"],
            "same_task_resume_allowed": envelope["lifecycle"][
                "same_task_resume_allowed"
            ],
        }
    )
    envelope["side_effect_scope_digest"] = canonical_digest(
        {
            "environment": envelope["environment"],
            "expected_artifacts": sorted(
                envelope["expected_artifacts"], key=lambda item: item.encode("utf-8")
            ),
            "forbidden_actions": sorted(
                envelope["forbidden_actions"], key=lambda item: item.encode("utf-8")
            ),
            "stop_conditions": sorted(
                envelope["stop_conditions"], key=lambda item: item.encode("utf-8")
            ),
        }
    )
    envelope["resume_state_digest"] = canonical_digest(
        {
            "consumed_actions": sorted(
                envelope["consumed_actions"], key=lambda item: item.encode("utf-8")
            ),
            "lifecycle_state": envelope["lifecycle"]["state"],
        }
    )
    return envelope


def valid_envelope():
    return refresh_digests(
        {
            "schema_version": 1,
            "envelope_id": "ENV-2.2-001",
            "task_id": "TASK-2.2-DEMO",
            "environment": "test",
            "authority_source": "APPROVED_PLAN",
            "authority_project": {
                "logical_path": "/projects/lead",
                "physical_path": "/projects/lead",
                "git_root": "/projects/lead",
                "git_dir": "/projects/lead/.git/worktrees/feature",
                "git_common_dir": "/projects/lead/.git",
                "worktree_identity": "0" * 64,
                "branch": "feat/vibe-project-lead-zh-2.2",
                "head": "a" * 40,
                "dirty_fingerprint": "b" * 64,
            },
            "outcome_digest": "c" * 64,
            "design_digest": "d" * 64,
            "plan_digest": "e" * 64,
            "allowlist": ["workbench/outcome_governance.py"],
            "behavior_allowlist": [
                "EXTERNAL:APPROVAL",
                "TEST:2.2-SAFETY-FOCUSED",
            ],
            "allowed_actions": ["READ", "EDIT", "TEST", "LOCAL_COMMIT", "REVIEW"],
            "forbidden_actions": ["NETWORK", "INSTALL", "PUSH", "DEPLOY", "DELETE"],
            "expected_artifacts": ["LOCAL_COMMIT"],
            "stop_conditions": ["ANCHOR_DRIFT", "CRITICAL_HIGH", "UNKNOWN"],
            "reviewer_budget": {"max_reviewers": 1, "max_rounds": 1, "max_calls": 1},
            "lifecycle": {"state": "ACTIVE", "same_task_resume_allowed": True},
            "consumed_actions": [],
            "allowlist_digest": "0" * 64,
            "permission_scope_digest": "0" * 64,
            "side_effect_scope_digest": "0" * 64,
            "resume_state_digest": "0" * 64,
        }
    )


def current_binding(envelope=None, **overrides):
    envelope = envelope or valid_envelope()
    value = {
        "task_id": envelope["task_id"],
        "envelope_id": envelope["envelope_id"],
        "environment": envelope["environment"],
        "authority_project": copy.deepcopy(envelope["authority_project"]),
        "outcome_digest": envelope["outcome_digest"],
        "design_digest": envelope["design_digest"],
        "plan_digest": envelope["plan_digest"],
        "allowlist_digest": envelope["allowlist_digest"],
        "permission_scope_digest": envelope["permission_scope_digest"],
        "side_effect_scope_digest": envelope["side_effect_scope_digest"],
        "resume_state_digest": envelope["resume_state_digest"],
        "captured_at_utc": "2026-09-03T00:00:00Z",
    }
    value.update(overrides)
    return value


def file_action(name="EDIT", paths=None, action_class="LOCAL_REVERSIBLE"):
    return {
        "class": action_class,
        "name": name,
        "scope": {
            "kind": "FILE_SET",
            "paths": paths or ["workbench/outcome_governance.py"],
        },
    }


def behavior_action(name="TEST", behavior_id="TEST:2.2-SAFETY-FOCUSED"):
    return {
        "class": "LOCAL_REVERSIBLE",
        "name": name,
        "scope": {"kind": "BEHAVIOR", "behavior_id": behavior_id},
    }


class ExecutionEnvelopeTests(unittest.TestCase):
    def assert_rejected(self, value, reason):
        with self.assertRaises(governance.ExecutionContractError) as caught:
            governance.validate_execution_envelope(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_accepts_exact_envelope_without_mutation(self):
        value = valid_envelope()
        before = copy.deepcopy(value)
        result = governance.validate_execution_envelope(value)
        expected = copy.deepcopy(before)
        for field in (
            "allowlist",
            "behavior_allowlist",
            "allowed_actions",
            "forbidden_actions",
            "expected_artifacts",
            "stop_conditions",
            "consumed_actions",
        ):
            expected[field] = sorted(expected[field], key=lambda item: item.encode("utf-8"))
        self.assertEqual(result, expected)
        self.assertIsNot(result, value)
        self.assertEqual(value, before)

    def test_missing_authority_source_fails_closed(self):
        value = valid_envelope()
        value["authority_source"] = ""
        self.assert_rejected(value, "AUTHORITY_SOURCE_MISSING")

    def test_rejects_unknown_fields_bool_budget_and_invalid_hash(self):
        value = valid_envelope()
        value["extra"] = "no"
        self.assert_rejected(value, "SCHEMA_FIELDS_CHANGED")

        value = valid_envelope()
        value["reviewer_budget"]["max_calls"] = True
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

        value = valid_envelope()
        value["authority_project"]["dirty_fingerprint"] = "short"
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_rejects_unhashable_lifecycle_state_with_contract_error(self):
        value = valid_envelope()
        value["lifecycle"]["state"] = []
        self.assert_rejected(value, "FIELD_TYPE_INVALID")

    def test_rejects_absolute_parent_and_overlapping_allowlist_paths(self):
        for allowlist in (
            ["/etc/passwd"],
            ["../outside"],
            ["workbench", "workbench/outcome_governance.py"],
        ):
            value = valid_envelope()
            value["allowlist"] = allowlist
            with self.subTest(allowlist=allowlist):
                self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_rejects_external_action_in_local_allowance(self):
        value = valid_envelope()
        value["allowed_actions"].append("PUSH")
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_envelope_recomputes_all_scope_and_state_digests(self):
        fields = (
            "allowlist_digest",
            "permission_scope_digest",
            "side_effect_scope_digest",
            "resume_state_digest",
        )
        for field in fields:
            value = valid_envelope()
            value[field] = "f" * 64
            with self.subTest(field=field):
                self.assert_rejected(value, "DIGEST_MISMATCH")

        value = valid_envelope()
        value["authority_project"]["worktree_identity"] = "f" * 64
        self.assert_rejected(value, "DIGEST_MISMATCH")

    def test_canonical_digest_is_repeatable(self):
        first = governance.canonical_envelope_digest(valid_envelope())
        second = governance.canonical_envelope_digest(valid_envelope())
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_set_semantic_lists_are_canonicalized_without_mutating_input(self):
        variants = {
            "allowlist": ["workbench/二.py", "workbench/outcome_governance.py"],
            "behavior_allowlist": ["TEST:SECOND", "EXTERNAL:APPROVAL"],
            "allowed_actions": ["TEST", "READ"],
            "forbidden_actions": ["PUSH", "NETWORK"],
            "expected_artifacts": ["验收记录", "LOCAL_COMMIT"],
            "stop_conditions": ["范围漂移", "ANCHOR_DRIFT"],
            "consumed_actions": ["TEST", "READ"],
        }
        digest_fields = (
            "allowlist_digest",
            "permission_scope_digest",
            "side_effect_scope_digest",
            "resume_state_digest",
        )
        for field, values in variants.items():
            first = valid_envelope()
            first[field] = list(values)
            refresh_digests(first)
            second = copy.deepcopy(first)
            second[field] = list(reversed(values))
            refresh_digests(second)
            first_before = copy.deepcopy(first)
            second_before = copy.deepcopy(second)
            with self.subTest(field=field):
                validated_first = governance.validate_execution_envelope(first)
                validated_second = governance.validate_execution_envelope(second)
                self.assertEqual(validated_first, validated_second)
                self.assertEqual(
                    [validated_first[item] for item in digest_fields],
                    [validated_second[item] for item in digest_fields],
                )
                self.assertEqual(
                    governance.canonical_envelope_digest(first),
                    governance.canonical_envelope_digest(second),
                )
                self.assertEqual(first, first_before)
                self.assertEqual(second, second_before)

    def test_set_semantic_lists_still_reject_duplicates(self):
        for field in (
            "allowlist",
            "behavior_allowlist",
            "allowed_actions",
            "forbidden_actions",
            "expected_artifacts",
            "stop_conditions",
            "consumed_actions",
        ):
            value = valid_envelope()
            existing = value[field][0] if value[field] else "READ"
            value[field] = [existing, existing]
            refresh_digests(value)
            with self.subTest(field=field):
                self.assert_rejected(value, "FIELD_VALUE_INVALID")

    def test_environment_is_closed_for_envelope_and_current_binding(self):
        for environment in ("local", "test", "staging", "production"):
            value = valid_envelope()
            value["environment"] = environment
            refresh_digests(value)
            with self.subTest(environment=environment):
                self.assertEqual(
                    governance.validate_execution_envelope(value)["environment"],
                    environment,
                )

        value = valid_envelope()
        value["environment"] = "prod-like"
        refresh_digests(value)
        self.assert_rejected(value, "FIELD_VALUE_INVALID")

        with self.assertRaises(governance.ExecutionContractError) as caught:
            governance.evaluate_resume(
                valid_envelope(),
                current_binding(environment="prod-like"),
                event="SAME_TASK_RESUME",
            )
        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_all_public_entries_reject_unencodable_unicode_with_contract_error(self):
        envelope = valid_envelope()
        envelope["authority_project"]["logical_path"] = "/projects/\ud800"
        calls = (
            lambda: governance.validate_execution_envelope(envelope),
            lambda: governance.canonical_envelope_digest(envelope),
            lambda: governance.evaluate_action(envelope, file_action()),
            lambda: governance.evaluate_action(
                valid_envelope(), file_action(paths=["workbench/\ud800.py"])
            ),
            lambda: governance.evaluate_resume(
                valid_envelope(),
                current_binding(
                    authority_project={
                        **current_binding()["authority_project"],
                        "logical_path": "/projects/\ud800",
                    }
                ),
                event="SAME_TASK_RESUME",
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(governance.ExecutionContractError) as caught:
                    call()
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")


class ExecutionDecisionTests(unittest.TestCase):
    def assert_action_error(self, action, reason):
        with self.assertRaises(governance.ExecutionContractError) as caught:
            governance.evaluate_action(valid_envelope(), action)
        self.assertEqual(caught.exception.reason, reason)

    def test_local_allowlisted_edit_is_eligible_but_creates_no_authority(self):
        result = governance.evaluate_action(valid_envelope(), file_action())
        self.assertIs(result["eligible"], True)
        self.assertEqual(result["reason"], "ACTION_ALLOWED")
        self.assertIs(result["authority_created"], False)
        self.assertIs(result["external_write_authorized"], False)
        self.assertIs(result["write_authorized"], False)

    def test_every_local_action_requires_an_explicit_scope(self):
        for name in governance.LOCAL_ACTIONS:
            action_class = "LOCAL_READ" if name == "READ" else "LOCAL_REVERSIBLE"
            for missing in ("missing", "null", "empty"):
                action = file_action(name=name, action_class=action_class)
                if missing == "missing":
                    del action["scope"]
                elif missing == "null":
                    action["scope"] = None
                else:
                    action["scope"] = {}
                with self.subTest(name=name, missing=missing):
                    self.assert_action_error(action, "ACTION_SCOPE_REQUIRED")

    def test_file_set_requires_nonempty_allowlisted_paths(self):
        empty = file_action()
        empty["scope"]["paths"] = []
        self.assert_action_error(empty, "ACTION_SCOPE_REQUIRED")

        result = governance.evaluate_action(
            valid_envelope(),
            file_action(paths=["workbench/outcome_governance.py", "README.md"]),
        )
        self.assertIs(result["eligible"], False)
        self.assertEqual(result["reason"], "ACTION_OUT_OF_SCOPE")

    def test_behavior_scope_requires_allowlisted_behavior_id(self):
        result = governance.evaluate_action(
            valid_envelope(), behavior_action(behavior_id="TEST:UNKNOWN")
        )
        self.assertIs(result["eligible"], False)
        self.assertEqual(result["reason"], "ACTION_OUT_OF_SCOPE")

    def test_mixed_or_unknown_scope_shape_is_rejected(self):
        mixed = behavior_action()
        mixed["scope"]["paths"] = ["workbench/outcome_governance.py"]
        unknown = behavior_action()
        unknown["scope"] = {"kind": "UNKNOWN", "behavior_id": "TEST:UNKNOWN"}
        wrong_matrix = file_action(name="LOCAL_COMMIT")
        wrong_matrix["scope"] = {
            "kind": "BEHAVIOR",
            "behavior_id": "TEST:2.2-SAFETY-FOCUSED",
        }
        for name, action in (
            ("mixed", mixed),
            ("unknown", unknown),
            ("wrong_matrix", wrong_matrix),
        ):
            with self.subTest(name=name):
                self.assert_action_error(action, "FIELD_VALUE_INVALID")

    def test_external_action_with_scope_still_requires_external_approval(self):
        for name in (
            "NETWORK",
            "INSTALL",
            "ACCOUNT",
            "PUSH",
            "PUBLISH",
            "DEPLOY",
            "DELETE",
            "BUSINESS_WRITE",
        ):
            action = {
                "class": "EXTERNAL_OR_IRREVERSIBLE",
                "name": name,
                "scope": {
                    "kind": "BEHAVIOR",
                    "behavior_id": "EXTERNAL:APPROVAL",
                },
            }
            with self.subTest(name=name):
                result = governance.evaluate_action(valid_envelope(), action)
                self.assertIs(result["eligible"], False)
                self.assertEqual(result["reason"], "EXTERNAL_APPROVAL_REQUIRED")

    def test_out_of_scope_and_unallowed_local_actions_are_denied(self):
        outside = governance.evaluate_action(
            valid_envelope(), file_action(paths=["README.md"])
        )
        self.assertEqual(outside["reason"], "ACTION_OUT_OF_SCOPE")

        value = valid_envelope()
        value["allowed_actions"].remove("TEST")
        refresh_digests(value)
        denied = governance.evaluate_action(value, behavior_action())
        self.assertEqual(denied["reason"], "ACTION_OUT_OF_SCOPE")

    def test_inactive_or_consumed_action_is_denied(self):
        inactive = valid_envelope()
        inactive["lifecycle"]["state"] = "CONSUMED"
        refresh_digests(inactive)
        result = governance.evaluate_action(inactive, behavior_action())
        self.assertEqual(result["reason"], "ENVELOPE_INACTIVE")

        consumed = valid_envelope()
        consumed["consumed_actions"] = ["REVIEW"]
        refresh_digests(consumed)
        result = governance.evaluate_action(consumed, behavior_action(name="REVIEW"))
        self.assertEqual(result["reason"], "ENVELOPE_INACTIVE")

    def test_unknown_action_is_malformed_not_implicitly_denied(self):
        self.assert_action_error(behavior_action(name="MAGIC"), "FIELD_VALUE_INVALID")

    def test_unhashable_action_class_is_a_contract_error(self):
        self.assert_action_error(
            file_action(name="READ", action_class=[]), "FIELD_TYPE_INVALID"
        )


class ResumeTests(unittest.TestCase):
    def test_unhashable_resume_event_is_a_contract_error(self):
        with self.assertRaises(governance.ExecutionContractError) as caught:
            governance.evaluate_resume(valid_envelope(), current_binding(), event=[])
        self.assertEqual(caught.exception.reason, "FIELD_TYPE_INVALID")

    def test_same_task_and_context_compaction_keep_unconsumed_local_actions(self):
        for event in ("SAME_TASK_RESUME", "CONTEXT_COMPACTION"):
            with self.subTest(event=event):
                result = governance.evaluate_resume(
                    valid_envelope(), current_binding(), event=event
                )
                self.assertIs(result["eligible"], True)
                self.assertEqual(result["reason"], "STABLE_RESUME")
                self.assertIn("EDIT", result["remaining_actions"])
                self.assertIs(result["external_write_authorized"], False)

    def test_timestamp_only_change_does_not_invalidate_resume(self):
        current = current_binding(captured_at_utc="2026-09-03T01:00:00Z")
        result = governance.evaluate_resume(
            valid_envelope(), current, event="SAME_TASK_RESUME"
        )
        self.assertIs(result["eligible"], True)

    def test_task_or_project_switch_never_inherits_envelope(self):
        for event in ("TASK_SWITCH", "PROJECT_SWITCH"):
            result = governance.evaluate_resume(
                valid_envelope(), current_binding(), event=event
            )
            self.assertIs(result["eligible"], False)
            self.assertEqual(result["reason"], "TASK_OR_ENVELOPE_CHANGED")
            self.assertEqual(result["drift_fields"], ["resume_event"])
            self.assertEqual(result["remaining_actions"], [])

    def test_resume_rejects_task_or_envelope_change(self):
        for field in ("task_id", "envelope_id"):
            result = governance.evaluate_resume(
                valid_envelope(),
                current_binding(**{field: "OTHER"}),
                event="SAME_TASK_RESUME",
            )
            with self.subTest(field=field):
                self.assertIs(result["eligible"], False)
                self.assertEqual(result["reason"], "TASK_OR_ENVELOPE_CHANGED")
                self.assertEqual(result["drift_fields"], [field])

    def test_resume_rejects_worktree_environment_and_scope_drift(self):
        simple_changes = {
            "environment": "staging",
            "outcome_digest": "f" * 64,
            "design_digest": "f" * 64,
            "plan_digest": "f" * 64,
            "allowlist_digest": "f" * 64,
            "permission_scope_digest": "f" * 64,
            "side_effect_scope_digest": "f" * 64,
            "resume_state_digest": "f" * 64,
        }
        for field, changed in simple_changes.items():
            result = governance.evaluate_resume(
                valid_envelope(),
                current_binding(**{field: changed}),
                event="SAME_TASK_RESUME",
            )
            with self.subTest(field=field):
                self.assertIs(result["eligible"], False)
                self.assertEqual(result["reason"], "RESUME_ANCHOR_DRIFT")
                self.assertEqual(result["drift_fields"], [field])

        for field, changed in (
            ("logical_path", "/projects/alias"),
            ("physical_path", "/projects/other"),
            ("git_root", "/projects/other"),
            ("git_dir", "/projects/other/.git/worktrees/feature"),
            ("git_common_dir", "/projects/other/.git"),
            ("branch", "other"),
            ("head", "f" * 40),
            ("dirty_fingerprint", "f" * 64),
        ):
            current = current_binding()
            current["authority_project"][field] = changed
            project = current["authority_project"]
            project["worktree_identity"] = canonical_digest(
                {
                    "git_common_dir": project["git_common_dir"],
                    "git_dir": project["git_dir"],
                    "physical_path": project["physical_path"],
                }
            )
            result = governance.evaluate_resume(
                valid_envelope(), current, event="SAME_TASK_RESUME"
            )
            expected = [f"authority_project.{field}"]
            if field in {"physical_path", "git_dir", "git_common_dir"}:
                expected.append("authority_project.worktree_identity")
            with self.subTest(field=field):
                self.assertIs(result["eligible"], False)
                self.assertEqual(result["reason"], "RESUME_ANCHOR_DRIFT")
                self.assertEqual(result["drift_fields"], sorted(expected))

    def test_stable_resume_does_not_restore_consumed_review(self):
        value = valid_envelope()
        value["consumed_actions"] = ["REVIEW"]
        refresh_digests(value)
        result = governance.evaluate_resume(
            value, current_binding(value), event="SAME_TASK_RESUME"
        )
        self.assertNotIn("REVIEW", result["remaining_actions"])
        self.assertNotIn("PUSH", result["remaining_actions"])

    def test_stale_or_forged_consumed_state_cannot_restore_authority(self):
        value = valid_envelope()
        current = current_binding(value)
        value["consumed_actions"] = ["REVIEW"]
        refresh_digests(value)

        stale = governance.evaluate_resume(value, current, event="SAME_TASK_RESUME")

        self.assertIs(stale["eligible"], False)
        self.assertEqual(stale["reason"], "RESUME_ANCHOR_DRIFT")
        self.assertEqual(stale["drift_fields"], ["resume_state_digest"])
        self.assertEqual(stale["remaining_actions"], [])


if __name__ == "__main__":
    unittest.main()
