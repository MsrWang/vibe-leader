# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Synthetic reception evidence; no model call or historical review is consumed."""
import copy
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import delegation_contract as contract
from tests.test_delegation_contract import (
    valid_brief, result_fixture, runtime_context_fixture,
)

BRIEF_V2 = "vibe-project-lead-zh-delegation-brief-v2"
RESULT_V2 = "vibe-project-lead-zh-delegation-result-v2"
LIFECYCLE_EXPIRY = {
    "TASK_ID_CHANGED", "USER_PAUSED_OR_CANCELLED", "APPROVAL_REVOKED",
    "LIFECYCLE_EVIDENCE_INCOMPLETE", "TASK_INTERRUPTED",
}


def reception_brief(*, mode="REVIEW", policy="REVALIDATE_READ_ONLY"):
    brief = valid_brief(mode=mode)
    brief["schema_id"] = BRIEF_V2
    brief["reception"] = {"task_id": "synthetic-task-23", "resume_policy": policy}
    expiry = set(brief["expiry"]) | LIFECYCLE_EXPIRY
    if policy == "REVALIDATE_READ_ONLY":
        expiry.discard("TASK_INTERRUPTED_OR_RESUMED")
    brief["expiry"] = sorted(expiry)
    return brief


def reception_result(brief):
    result = result_fixture(brief)
    result["schema_id"] = RESULT_V2
    result["brief_sha256"] = contract.canonical_contract_digest(brief)
    return result


def reception_context(brief, result, events=()):
    return {
        "brief_sha256": contract.canonical_contract_digest(brief),
        "result_sha256": contract.canonical_contract_digest(result),
        "current_task_id": brief["reception"]["task_id"],
        "events": list(events),
        "events_complete": True,
        "evidence_complete": True,
    }


class ReceptionLifecycleTests(unittest.TestCase):
    def evaluate(self, brief, result, context, *, runtime=None, main=None, consumed=()):
        self.assertIn("reception_context", inspect.signature(
            contract.evaluate_delegation_result).parameters,
            "receiver has no main-thread lifecycle evidence input")
        return contract.evaluate_delegation_result(
            brief, result,
            current_main_identity=(main if main is not None
                                   else brief["baseline"]["main_thread_identity"]),
            current_authority_identity=result["return_baseline"],
            runtime_context=(runtime if runtime is not None
                             else runtime_context_fixture(brief)),
            reception_context=context,
            consumed_delegation_ids=frozenset(consumed),
        )

    def assert_rejected(self, value, freshness, reason):
        self.assertEqual(value["freshness"], freshness, value)
        self.assertEqual(value["decision"], "REJECTED")
        self.assertIn(reason, value["reasons"])
        self.assertFalse(value["consumable"])
        self.assertIsNone(value["consumption_record"])
        self.assertFalse(value["write_authorized"])

    def test_prechange_legacy_receiver_must_not_accept_without_lifecycle(self):
        brief = valid_brief()
        brief["schema_id"] = "vibe-project-lead-zh-delegation-brief-v1"
        brief.pop("reception", None)
        result = result_fixture(brief)
        result["schema_id"] = "vibe-project-lead-zh-delegation-result-v1"
        result.pop("brief_sha256", None)
        evaluated = contract.evaluate_delegation_result(
            brief, result, current_main_identity=brief["baseline"]["main_thread_identity"],
            current_authority_identity=result["return_baseline"],
            runtime_context=runtime_context_fixture(brief),
        )
        self.assert_rejected(evaluated, "INCOMPLETE",
                             "LEGACY_CONTRACT_REQUIRES_NEW_DISPATCH")
        self.assertEqual(contract.validate_delegation_brief(brief), brief)
        self.assertEqual(contract.validate_delegation_result(result), result)

    def test_fresh_and_stable_resumed_read_only_evidence_enters_review_only(self):
        for mode in ("READ_ONLY", "REVIEW"):
            for events in ((), ("CONTEXT_COMPACTION",), ("SAME_TASK_RESUME",),
                           ("CONTEXT_COMPACTION", "SAME_TASK_RESUME", "SAME_TASK_RESUME")):
                with self.subTest(mode=mode, events=events):
                    brief = reception_brief(mode=mode)
                    result = reception_result(brief)
                    context = reception_context(brief, result, events)
                    evaluated = self.evaluate(brief, result, context)
                    self.assertEqual(evaluated["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW")
                    self.assertTrue(evaluated["consumable"])
                    self.assertFalse(evaluated["write_authorized"])
                    self.assertEqual(evaluated["schema_version"], 3)
                    self.assertEqual(evaluated["consumption_record"]["reception_context_sha256"],
                                     contract.canonical_contract_digest(context))

    def test_pass_requires_zero_exit_code_before_reception(self):
        for events in ((), ("CONTEXT_COMPACTION",)):
            brief = reception_brief()
            result = reception_result(brief)
            control = self.evaluate(brief, result,
                                    reception_context(brief, result, events))
            self.assertEqual(control["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW")
            for exit_code in (1, 2):
                with self.subTest(events=events, exit_code=exit_code):
                    invalid = copy.deepcopy(result)
                    invalid["tests"][0]["exit_code"] = exit_code
                    with self.assertRaises(contract.DelegationContractError) as caught:
                        contract.validate_delegation_result(invalid)
                    self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")
                    evaluated = self.evaluate(
                        brief, invalid, reception_context(brief, invalid, events))
                    self.assert_rejected(evaluated, "CONTRACT_VIOLATION",
                                         "FIELD_VALUE_INVALID")

    def test_unhashable_model_policy_is_structured_contract_rejection(self):
        for model_policy in ([], {}):
            with self.subTest(model_policy=model_policy):
                brief = reception_brief()
                runtime = runtime_context_fixture(brief)
                brief["budget"]["model_policy"] = model_policy
                result = reception_result(brief)
                with self.assertRaises(contract.DelegationContractError) as caught:
                    contract.validate_delegation_brief(brief)
                self.assertEqual(caught.exception.reason, "BUDGET_INVALID")
                self.assert_rejected(
                    self.evaluate(brief, result, reception_context(brief, result),
                                  runtime=runtime),
                    "CONTRACT_VIOLATION", "BUDGET_INVALID")

    def test_missing_malformed_or_incomplete_context_cannot_receive(self):
        brief = reception_brief()
        result = reception_result(brief)
        valid = reception_context(brief, result)
        invalid = [None, [], {}, dict(valid, extra=True)]
        for field in valid:
            changed = copy.deepcopy(valid)
            del changed[field]
            invalid.append(changed)
        for field in ("events_complete", "evidence_complete"):
            for value in (False, 1, "true", None, [], {}):
                invalid.append(dict(valid, **{field: value}))
        for value in (None, {}, "SAME_TASK_RESUME", [None], [{}], [1],
                      ["UNKNOWN"], ["NEW_EVENT"], ["SAME_TASK_RESUME"] * 257):
            invalid.append(dict(valid, events=value))
        for context in invalid:
            with self.subTest(context=context):
                self.assert_rejected(self.evaluate(brief, result, context),
                                     "INCOMPLETE", "RECEPTION_CONTEXT_INVALID")

    def test_material_hash_or_result_brief_mismatch_is_not_receivable(self):
        brief = reception_brief()
        result = reception_result(brief)
        context = reception_context(brief, result)
        for field in ("brief_sha256", "result_sha256"):
            self.assert_rejected(self.evaluate(brief, result, dict(context, **{field: "0" * 64})),
                                 "INCOMPLETE", "RECEPTION_CONTEXT_INVALID")
        altered = copy.deepcopy(result)
        altered["warnings"].append("new warning absent from checked material")
        self.assert_rejected(self.evaluate(brief, altered, context), "INCOMPLETE",
                             "RECEPTION_CONTEXT_INVALID")
        changed_brief = copy.deepcopy(brief)
        changed_brief["objective"] = "Different approved review"
        self.assert_rejected(self.evaluate(changed_brief, result,
                                           reception_context(changed_brief, result)),
                             "CONTRACT_VIOLATION", "RESULT_BRIEF_MISMATCH")

    def test_old_result_cannot_be_attached_to_new_brief(self):
        brief = reception_brief()
        result = reception_result(brief)
        result["schema_id"] = "vibe-project-lead-zh-delegation-result-v1"
        del result["brief_sha256"]
        self.assert_rejected(self.evaluate(brief, result, reception_context(brief, result)),
                             "INCOMPLETE", "LEGACY_CONTRACT_REQUIRES_NEW_DISPATCH")

    def test_user_stop_or_scope_event_cannot_be_erased_by_later_resume(self):
        brief = reception_brief()
        result = reception_result(brief)
        for event in ("USER_PAUSED", "USER_CANCELLED", "APPROVAL_REVOKED",
                      "TASK_SWITCH", "PROJECT_SWITCH", "TASK_INTERRUPTED"):
            with self.subTest(event=event):
                value = self.evaluate(brief, result, reception_context(
                    brief, result, (event, "SAME_TASK_RESUME")))
                self.assert_rejected(value, "STALE", "RECEPTION_INVALIDATED:" + event)

    def test_different_task_is_stale_even_with_matching_hashes(self):
        brief = reception_brief()
        result = reception_result(brief)
        context = reception_context(brief, result)
        context["current_task_id"] = "different-task"
        self.assert_rejected(self.evaluate(brief, result, context),
                             "STALE", "RECEPTION_TASK_CHANGED")

    def test_task_identifier_must_be_bounded_and_well_formed(self):
        for task_id in ("", "x" * 129, "task\n", {}, None, 1, " task"):
            brief = reception_brief()
            brief["reception"]["task_id"] = task_id
            with self.subTest(task_id=task_id), self.assertRaises(contract.DelegationContractError):
                contract.validate_delegation_brief(brief)
        brief = reception_brief()
        result = reception_result(brief)
        for task_id in ("", "x" * 129, {}, None, 1):
            context = dict(reception_context(brief, result), current_task_id=task_id)
            self.assert_rejected(self.evaluate(brief, result, context),
                                 "INCOMPLETE", "RECEPTION_CONTEXT_INVALID")

    def test_expire_policy_and_writer_do_not_recover_unconsumed_result(self):
        for mode in ("READ_ONLY", "REVIEW", "ISOLATED_WRITER"):
            brief = reception_brief(mode=mode, policy="EXPIRE")
            result = reception_result(brief)
            self.assertTrue(self.evaluate(brief, result, reception_context(brief, result))["consumable"])
            for event in ("CONTEXT_COMPACTION", "SAME_TASK_RESUME"):
                self.assert_rejected(self.evaluate(brief, result,
                    reception_context(brief, result, (event,))),
                    "STALE", "RECEPTION_RESUME_NOT_ALLOWED")

    def test_contradictory_or_incomplete_brief_policies_are_rejected(self):
        cases = [reception_brief(mode="ISOLATED_WRITER"),
                 reception_brief(policy="SILENT_REUSE")]
        contradictory = reception_brief()
        contradictory["expiry"].append("TASK_INTERRUPTED_OR_RESUMED")
        cases.append(contradictory)
        for field in LIFECYCLE_EXPIRY:
            changed = reception_brief()
            changed["expiry"].remove(field)
            cases.append(changed)
        for reception in (None, {}, {"task_id": "x", "resume_policy": []},
                          {"task_id": "x", "resume_policy": "EXPIRE", "extra": True}):
            changed = reception_brief()
            changed["reception"] = reception
            cases.append(changed)
        for brief in cases:
            with self.subTest(brief=brief), self.assertRaises(contract.DelegationContractError):
                contract.validate_delegation_brief(brief)

    def test_resumed_result_still_rejects_project_version_and_binary_drift(self):
        brief = reception_brief()
        result = reception_result(brief)
        context = reception_context(brief, result, ("SAME_TASK_RESUME",))
        for field, value in (("codex_version", "0.148.0"), ("core_sha256", "2" * 64)):
            runtime = runtime_context_fixture(brief)
            runtime["current"][field] = value
            evaluated = self.evaluate(brief, result, context, runtime=runtime)
            self.assertEqual(evaluated["freshness"], "STALE", evaluated)
            self.assertIsNone(evaluated["consumption_record"])
        main = copy.deepcopy(brief["baseline"]["main_thread_identity"])
        main["dirty_fingerprint"] = "9" * 64
        self.assert_rejected(self.evaluate(brief, result, context, main=main),
                             "STALE", "MAIN_BASELINE_STALE")

    def test_consumed_id_never_regains_authority_after_stable_resume(self):
        brief = reception_brief()
        result = reception_result(brief)
        context = reception_context(brief, result, ("SAME_TASK_RESUME",))
        value = self.evaluate(brief, result, context, consumed=(brief["delegation_id"],))
        self.assertEqual(value["decision"], "ALREADY_CONSUMED")
        self.assertFalse(value["consumable"])
        self.assertIsNone(value["consumption_record"])
        self.assertFalse(value["write_authorized"])

    def test_reception_is_pure_and_does_not_run_commands_or_persist_authority(self):
        brief = reception_brief()
        result = reception_result(brief)
        context = reception_context(brief, result, ("CONTEXT_COMPACTION",))
        before = copy.deepcopy((brief, result, context))
        with mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
             mock.patch("os.open", side_effect=AssertionError("filesystem")):
            first = self.evaluate(brief, result, context)
            second = self.evaluate(brief, result, context)
        self.assertTrue(first["consumable"])
        self.assertEqual(first, second)
        self.assertEqual((brief, result, context), before)


class ReceptionCliTests(unittest.TestCase):
    def run_cli(self, *argv):
        script = Path(contract.__file__)
        return subprocess.run([sys.executable, "-B", script, *map(str, argv)],
                              capture_output=True, text=True, check=False)

    def inputs(self, root):
        brief = reception_brief()
        result = reception_result(brief)
        values = {"brief": brief, "result": result,
                  "main": brief["baseline"]["main_thread_identity"],
                  "authority": result["return_baseline"],
                  "runtime": runtime_context_fixture(brief)}
        for name, value in values.items():
            (root / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
        args = ["evaluate-result"]
        for option, name in (("brief", "brief"), ("result", "result"),
                             ("current-main-identity", "main"),
                             ("current-authority-identity", "authority"),
                             ("runtime-context", "runtime")):
            args += ["--" + option, root / (name + ".json")]
        return brief, result, args

    def test_cli_requires_context_accepts_stable_resume_and_refuses_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief, result, args = self.inputs(root)
            missing = self.run_cli(*args)
            self.assertEqual(missing.returncode, 3, missing.stderr)
            self.assertIn("RECEPTION_CONTEXT_INVALID", json.loads(missing.stdout)["reasons"])
            context = root / "reception.json"
            context.write_text(json.dumps(reception_context(
                brief, result, ("CONTEXT_COMPACTION", "SAME_TASK_RESUME"))), encoding="utf-8")
            output = root / "receipt.json"
            accepted = self.run_cli(*args, "--reception-context", context, "--output", output)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            receipt = json.loads(accepted.stdout)
            self.assertTrue(receipt["consumable"])
            self.assertFalse(receipt["write_authorized"])
            self.assertEqual(json.loads(output.read_text()), receipt)
            repeated = self.run_cli(*args, "--reception-context", context,
                                    "--consumed-id", brief["delegation_id"])
            self.assertEqual(repeated.returncode, 3, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["decision"], "ALREADY_CONSUMED")
            self.assertEqual(self.run_cli(*args, "--reception-context", context,
                                         "--output", output).returncode, 2)
            self.assertEqual(json.loads(output.read_text()), receipt)

    def test_cli_rejects_stopped_or_incomplete_reception_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief, result, args = self.inputs(root)
            context = root / "reception.json"
            for events, complete, expected in ((["USER_PAUSED", "SAME_TASK_RESUME"], True, "STALE"),
                                               ([], False, "INCOMPLETE"),
                                               (["UNKNOWN"], True, "INCOMPLETE")):
                value = reception_context(brief, result, events)
                value["events_complete"] = complete
                context.write_text(json.dumps(value), encoding="utf-8")
                actual = self.run_cli(*args, "--reception-context", context)
                self.assertEqual(actual.returncode, 3, actual.stderr)
                receipt = json.loads(actual.stdout)
                self.assertEqual(receipt["freshness"], expected)
                self.assertIsNone(receipt["consumption_record"])

    def test_cli_uses_strict_regular_json_reader_for_reception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief, result, args = self.inputs(root)
            context = root / "reception.json"
            context.write_text(json.dumps(reception_context(brief, result)), encoding="utf-8")
            alias = root / "alias.json"
            alias.symlink_to(context)
            self.assertEqual(self.run_cli(*args, "--reception-context", alias).returncode, 2)
            for raw in ('{"events_complete":true,"events_complete":false}', 'not-json'):
                context.write_text(raw, encoding="utf-8")
                self.assertEqual(self.run_cli(*args, "--reception-context", context).returncode, 2)
            self.assertEqual(self.run_cli(*args, "--reception-context", root).returncode, 2)

    def test_cli_cannot_override_incomplete_material_with_duplicate_true(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief, result, args = self.inputs(root)
            context = root / "duplicate.json"
            valid = json.dumps(reception_context(brief, result))
            for field in ("events_complete", "evidence_complete"):
                with self.subTest(field=field):
                    context.write_text('{"' + field + '":false,' + valid[1:], encoding="utf-8")
                    actual = self.run_cli(*args, "--reception-context", context)
                    self.assertEqual(actual.returncode, 2, actual.stdout)
                    self.assertEqual(actual.stdout, "")
                    self.assertNotIn(str(context), actual.stderr)


if __name__ == "__main__":
    unittest.main()
