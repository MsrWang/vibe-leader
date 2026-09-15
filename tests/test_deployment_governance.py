# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import socket
import unittest
from datetime import datetime, timezone
from pathlib import Path

from workbench import deployment_governance as governance


EVIDENCE_REQUIRED_FIELDS = {
    "authentication": "verified",
    "tls": "verified",
    "migration": "plan",
    "backup_recovery": "verified",
    "observability": "logs",
    "rollback": "executable",
    "sqlite": "filesystem_semantics",
}


def applicable_profile_evidence(profile_id):
    profile = governance.PROFILE_CATALOG[profile_id]
    return {
        "profile_id": profile_id,
        "applicability": {
            condition: True for condition in profile["applicability"]
        },
        "non_applicability": {
            condition: False for condition in profile["non_applicability"]
        },
        "conflict": False,
    }


def valid_assessment(**overrides):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    profile_id = overrides.get("profile_id", "VERCEL_WEB")
    value = {
        "schema_version": 1,
        "project_id": "web-pilot",
        "profile_id": "VERCEL_WEB",
        "environment": "local",
        "observed_at_utc": now,
        "identity": {
            "status": "bound",
            "head": "a" * 40,
            "fingerprint_complete": True,
            "write_eligibility": "READ_ONLY",
        },
        "freshness": {"state": "FRESH", "reasons": []},
        "evidence": {
            "valid_until_utc": "2099-01-01T00:00:00Z",
            "conflict": False,
            "profile_applicability": applicable_profile_evidence(profile_id),
            "authentication": {"verified": True, "conflict": False},
            "tls": {"verified": True, "conflict": False},
            "migration": {"plan": True, "isolated": True, "conflict": False},
            "backup_recovery": {"verified": True, "conflict": False},
            "observability": {
                "logs": True,
                "metrics": True,
                "health": True,
                "alerts": True,
                "capacity": True,
                "conflict": False,
            },
            "rollback": {"executable": True, "conflict": False},
            "sqlite": {
                "filesystem_semantics": True,
                "concurrency": True,
                "locking": True,
                "backup_consistency": True,
                "restore_verified": True,
                "conflict": False,
            },
        },
        "notes": [],
        "write_authorized": False,
    }
    value.update(overrides)
    return value


class AssessmentContractTests(unittest.TestCase):
    def test_accepts_exact_safe_assessment_and_is_deterministic(self):
        first = governance.validate_assessment_input(valid_assessment())
        second = governance.validate_assessment_input(valid_assessment())
        self.assertEqual(governance.canonical_assessment_digest(first), governance.canonical_assessment_digest(second))
        self.assertFalse(first["write_authorized"])

    def test_rejects_unknown_top_level_field(self):
        value = valid_assessment(extra="no")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.validate_assessment_input(value)
        self.assertEqual(caught.exception.reason, "SCHEMA_FIELDS_CHANGED")

    def test_rejects_unsupported_schema_and_invalid_enums(self):
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.validate_assessment_input(valid_assessment(schema_version=99))
        self.assertEqual(caught.exception.reason, "SCHEMA_UNSUPPORTED")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.validate_assessment_input(valid_assessment(environment="qa"))
        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_rejects_duplicate_keys_and_bom_json(self):
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.parse_assessment_json('{"schema_version":1,"schema_version":1}')
        self.assertEqual(caught.exception.reason, "SCHEMA_FIELDS_CHANGED")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.parse_assessment_json("\ufeff{}")
        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_rejects_secret_shaped_keys_anywhere(self):
        for key in ("token", "secret", "password", "cookie", "authorization", "private_key", "remote_url"):
            value = valid_assessment(notes=[{key: "redacted"}])
            with self.subTest(key=key):
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    governance.validate_assessment_input(value)
                self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_rejects_incomplete_identity_and_non_boolean_write_authorized(self):
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.validate_assessment_input(valid_assessment(identity={"status": "incomplete"}))
        self.assertEqual(caught.exception.reason, "PROJECT_IDENTITY_INCOMPLETE")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.validate_assessment_input(valid_assessment(write_authorized=True))
        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")

    def test_missing_or_invalid_evidence_expiry_fails_closed(self):
        for valid_until in (None, "not-a-timestamp"):
            assessment = valid_assessment()
            if valid_until is None:
                assessment["evidence"].pop("valid_until_utc")
            else:
                assessment["evidence"]["valid_until_utc"] = valid_until
            with self.subTest(valid_until=valid_until):
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    governance.validate_assessment_input(assessment)
                self.assertEqual(caught.exception.reason, "EVIDENCE_EXPIRED")

    def test_future_observation_is_rejected_at_every_public_boundary(self):
        assessment = valid_assessment(
            observed_at_utc="2098-01-01T00:00:00Z",
            environment="production",
        )
        production_assessment = dict(assessment)
        production_assessment["action_approvals"] = {"production": True}
        boundaries = (
            ("input", lambda: governance.validate_assessment_input(assessment)),
            (
                "readiness",
                lambda: governance.evaluate_readiness(
                    assessment,
                    profile=governance.PROFILE_CATALOG["VERCEL_WEB"],
                ),
            ),
            ("json", lambda: governance.render_assessment_json(assessment)),
            ("markdown", lambda: governance.render_assessment_markdown(assessment)),
            (
                "production_action",
                lambda: governance.build_read_only_action_request(
                    production_assessment,
                    action="production",
                ),
            ),
        )

        for boundary, invoke in boundaries:
            with self.subTest(boundary=boundary):
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    invoke()
                self.assertEqual(caught.exception.reason, "EVIDENCE_FUTURE")


class ProfileTests(unittest.TestCase):
    def test_catalog_contains_six_profiles_with_required_fields(self):
        self.assertEqual(
            set(governance.PROFILE_CATALOG),
            {
                "VERCEL_WEB",
                "LINUX_HOST",
                "DOCKER_SERVICE",
                "PYTHON_SQLITE",
                "FRONTEND_BACKEND_SPLIT",
                "STATIC_SITE",
            },
        )
        for profile in governance.PROFILE_CATALOG.values():
            record = governance.validate_profile_record(profile)
            self.assertIn("required_evidence", record)
            self.assertIn("persistence_model", record)
            self.assertIn("rollback_inputs", record)
            self.assertNotIn("token", json.dumps(record).lower())

    def test_all_profiles_require_complete_positive_and_negative_applicability(self):
        for profile_id, profile in governance.PROFILE_CATALOG.items():
            with self.subTest(profile_id=profile_id, case="applicable"):
                result = governance.evaluate_readiness(
                    valid_assessment(profile_id=profile_id),
                    profile=profile,
                )
                self.assertEqual(result["profile_state"], "SELECTED")
                self.assertNotIn("PROFILE_NOT_APPLICABLE", result["reasons"])

            negative = valid_assessment(profile_id=profile_id)
            condition = next(iter(profile["applicability"]))
            negative["evidence"]["profile_applicability"]["applicability"][condition] = False
            with self.subTest(profile_id=profile_id, case="positive_condition_false"):
                result = governance.evaluate_readiness(negative, profile=profile)
                self.assertEqual(result["profile_state"], "NOT_APPLICABLE")
                self.assertEqual(result["readiness"], "NOT_READY")
                self.assertEqual(result["decision"], "No-Go")
                self.assertIn("PROFILE_NOT_APPLICABLE", result["reasons"])

            contradicted = valid_assessment(profile_id=profile_id)
            condition = next(iter(profile["non_applicability"]))
            contradicted["evidence"]["profile_applicability"]["non_applicability"][condition] = True
            with self.subTest(profile_id=profile_id, case="negative_condition_true"):
                result = governance.evaluate_readiness(contradicted, profile=profile)
                self.assertEqual(result["profile_state"], "NOT_APPLICABLE")
                self.assertEqual(result["readiness"], "NOT_READY")
                self.assertEqual(result["decision"], "No-Go")
                self.assertIn("PROFILE_NOT_APPLICABLE", result["reasons"])

    def test_all_profiles_fail_closed_on_missing_unknown_conflicting_or_mismatched_facts(self):
        for profile_id, profile in governance.PROFILE_CATALOG.items():
            missing = valid_assessment(profile_id=profile_id)
            missing["evidence"].pop("profile_applicability")

            unknown = valid_assessment(profile_id=profile_id)
            condition = next(iter(profile["applicability"]))
            unknown["evidence"]["profile_applicability"]["applicability"][condition] = "UNKNOWN"

            conflicting = valid_assessment(profile_id=profile_id)
            conflicting["evidence"]["profile_applicability"]["conflict"] = True

            mismatched = valid_assessment(profile_id=profile_id)
            mismatched["evidence"]["profile_applicability"]["profile_id"] = next(
                candidate for candidate in governance.PROFILE_CATALOG if candidate != profile_id
            )

            for case, assessment in (
                ("missing", missing),
                ("unknown", unknown),
                ("conflicting", conflicting),
                ("mismatched", mismatched),
            ):
                with self.subTest(profile_id=profile_id, case=case):
                    result = governance.evaluate_readiness(assessment, profile=profile)
                    self.assertEqual(result["profile_state"], "NOT_APPLICABLE")
                    self.assertEqual(result["readiness"], "NOT_READY")
                    self.assertEqual(result["decision"], "No-Go")
                    self.assertIn("PROFILE_NOT_APPLICABLE", result["reasons"])

    def test_missing_auth_and_unknown_freshness_are_not_ready(self):
        assessment = valid_assessment(
            freshness={"state": "UNKNOWN", "reasons": ["PROJECT_FRESHNESS_UNKNOWN"]},
            evidence={
                "valid_until_utc": "2099-01-01T00:00:00Z",
                "authentication": {"verified": False},
            },
        )
        result = governance.evaluate_readiness(assessment, profile=governance.PROFILE_CATALOG["VERCEL_WEB"])
        self.assertEqual(result["readiness"], "NOT_READY")
        self.assertIn("PROJECT_FRESHNESS_UNKNOWN", result["reasons"])
        self.assertIn("AUTHENTICATION_UNVERIFIED", result["reasons"])

    def test_readiness_rejects_weakened_same_id_profile(self):
        weakened = dict(governance.PROFILE_CATALOG["VERCEL_WEB"])
        weakened["required_evidence"] = ["authentication"]

        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.evaluate_readiness(
                valid_assessment(),
                profile=weakened,
            )

        self.assertEqual(caught.exception.reason, "PROFILE_NOT_APPLICABLE")


class GateTests(unittest.TestCase):
    def test_state_machine_happy_path_and_invalid_transition(self):
        self.assertEqual(
            governance.transition_state("NOT_CONFIGURED", "staging_ready", evidence_complete=True, approved=False),
            "READY_FOR_STAGING",
        )
        self.assertEqual(
            governance.transition_state("READY_FOR_STAGING", "staging_verified", evidence_complete=True, approved=True),
            "STAGING_VERIFIED",
        )
        self.assertEqual(
            governance.transition_state("STAGING_VERIFIED", "production_ready", evidence_complete=True, approved=True),
            "READY_FOR_PRODUCTION",
        )
        with self.assertRaises(governance.DeploymentContractError):
            governance.transition_state("NOT_CONFIGURED", "production_ready", evidence_complete=True, approved=True)

    def test_unknown_or_incomplete_evidence_fails_closed(self):
        for state in ("BLOCKED", "UNKNOWN"):
            with self.subTest(state=state):
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    governance.transition_state(state, "production_ready", evidence_complete=True, approved=True)
                self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")

    def test_each_external_transition_needs_its_own_approval(self):
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.transition_state(
                "READY_FOR_STAGING",
                "staging_verified",
                evidence_complete=True,
                approved=False,
            )
        self.assertEqual(caught.exception.reason, "ACTION_NOT_APPROVED")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.transition_state(
                "STAGING_VERIFIED",
                "production_ready",
                evidence_complete=True,
                approved=False,
            )
        self.assertEqual(caught.exception.reason, "ACTION_NOT_APPROVED")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.transition_state(
                "READY_FOR_PRODUCTION",
                "rollback_requested",
                evidence_complete=True,
                approved=False,
            )
        self.assertEqual(caught.exception.reason, "ACTION_NOT_APPROVED")

    def test_production_security_hardening_gap_is_conditional(self):
        assessment = valid_assessment(environment="production")
        result = governance.evaluate_readiness(
            assessment,
            profile=governance.PROFILE_CATALOG["VERCEL_WEB"],
        )
        self.assertEqual(result["readiness"], "CONDITIONAL")
        self.assertEqual(result["decision"], "Conditional Go")
        self.assertIn("TRUSTED_PROXY_UNVERIFIED", result["warnings"])
        self.assertIn("SECURITY_HEADERS_UNVERIFIED", result["warnings"])
        self.assertIn("RATE_LIMIT_UNVERIFIED", result["warnings"])

    def test_security_data_observability_and_rollback_gates(self):
        assessment = valid_assessment(
            evidence={
                "valid_until_utc": "2099-01-01T00:00:00Z",
                "conflict": False,
                "profile_applicability": applicable_profile_evidence("VERCEL_WEB"),
                "authentication": {"verified": True, "trusted_proxy": True, "conflict": False},
                "tls": {"verified": True, "secure_headers": True, "rate_limit": True, "conflict": False},
                "migration": {"plan": True, "isolated": True, "conflict": False},
                "backup_recovery": {"verified": True, "conflict": False},
                "observability": {
                    "logs": True,
                    "metrics": True,
                    "health": True,
                    "alerts": True,
                    "capacity": True,
                    "conflict": False,
                },
                "rollback": {"executable": True, "conflict": False},
            }
        )
        result = governance.evaluate_readiness(assessment, profile=governance.PROFILE_CATALOG["VERCEL_WEB"])
        self.assertEqual(result["decision"], "Go")
        assessment["evidence"]["rollback"] = {"executable": False}
        result = governance.evaluate_readiness(assessment, profile=governance.PROFILE_CATALOG["VERCEL_WEB"])
        self.assertEqual(result["decision"], "No-Go")
        self.assertIn("ROLLBACK_NOT_EXECUTABLE", result["reasons"])

    def test_all_profiles_accept_closed_conflict_free_evidence_categories(self):
        for profile_id, profile in governance.PROFILE_CATALOG.items():
            assessment = valid_assessment(
                profile_id=profile_id,
                environment="production",
            )
            with self.subTest(profile_id=profile_id, stage="readiness"):
                result = governance.evaluate_readiness(assessment, profile=profile)
                self.assertNotEqual(result["readiness"], "NOT_READY")
                self.assertNotEqual(result["decision"], "No-Go")
                self.assertNotIn("EVIDENCE_CONFLICT", result["reasons"])

            assessment["action_approvals"] = {"production": True}
            with self.subTest(profile_id=profile_id, stage="production_action"):
                request = governance.build_read_only_action_request(
                    assessment,
                    action="production",
                )
                self.assertEqual(request["target"]["profile_id"], profile_id)
                self.assertFalse(request["write_authorized"])

    def test_all_profiles_fail_closed_on_invalid_evidence_category_contracts(self):
        for profile_id, profile in governance.PROFILE_CATALOG.items():
            for category, required_field in EVIDENCE_REQUIRED_FIELDS.items():
                cases = []

                missing = valid_assessment(profile_id=profile_id, environment="production")
                missing["evidence"][category].pop(required_field)
                cases.append(("missing_required_field", missing))

                unexpected = valid_assessment(profile_id=profile_id, environment="production")
                unexpected["evidence"][category]["unexpected"] = True
                cases.append(("unexpected_field", unexpected))

                unknown = valid_assessment(profile_id=profile_id, environment="production")
                unknown["evidence"][category][required_field] = "UNKNOWN"
                cases.append(("unknown_value", unknown))

                non_boolean = valid_assessment(profile_id=profile_id, environment="production")
                non_boolean["evidence"][category][required_field] = 1
                cases.append(("non_boolean_value", non_boolean))

                conflicted = valid_assessment(profile_id=profile_id, environment="production")
                conflicted["evidence"][category]["conflict"] = True
                cases.append(("nested_conflict", conflicted))

                for case, assessment in cases:
                    with self.subTest(
                        profile_id=profile_id,
                        category=category,
                        case=case,
                        stage="readiness",
                    ):
                        result = governance.evaluate_readiness(
                            assessment,
                            profile=profile,
                        )
                        self.assertEqual(result["readiness"], "NOT_READY")
                        self.assertEqual(result["decision"], "No-Go")
                        self.assertIn("EVIDENCE_CONFLICT", result["reasons"])

                    assessment["action_approvals"] = {"production": True}
                    with self.subTest(
                        profile_id=profile_id,
                        category=category,
                        case=case,
                        stage="production_action",
                    ):
                        with self.assertRaises(governance.DeploymentContractError) as caught:
                            governance.build_read_only_action_request(
                                assessment,
                                action="production",
                            )
                        self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")

    def test_all_profiles_fail_closed_on_missing_required_category(self):
        for profile_id, profile in governance.PROFILE_CATALOG.items():
            for category in profile["required_evidence"]:
                assessment = valid_assessment(
                    profile_id=profile_id,
                    environment="production",
                )
                assessment["evidence"].pop(category)
                with self.subTest(profile_id=profile_id, category=category):
                    result = governance.evaluate_readiness(assessment, profile=profile)
                    self.assertEqual(result["readiness"], "NOT_READY")
                    self.assertEqual(result["decision"], "No-Go")

                assessment["action_approvals"] = {"production": True}
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    governance.build_read_only_action_request(
                        assessment,
                        action="production",
                    )
                self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")

    def test_unknown_top_level_evidence_field_fails_closed(self):
        assessment = valid_assessment(environment="production")
        assessment["evidence"]["unexpected_category"] = {"verified": True}

        result = governance.evaluate_readiness(
            assessment,
            profile=governance.PROFILE_CATALOG["VERCEL_WEB"],
        )

        self.assertEqual(result["readiness"], "NOT_READY")
        self.assertEqual(result["decision"], "No-Go")
        self.assertIn("EVIDENCE_CONFLICT", result["reasons"])
        assessment["action_approvals"] = {"production": True}
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.build_read_only_action_request(assessment, action="production")
        self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")

    def test_general_evidence_conflict_must_be_explicitly_false(self):
        accepted = valid_assessment(environment="production")
        accepted_result = governance.evaluate_readiness(
            accepted,
            profile=governance.PROFILE_CATALOG["VERCEL_WEB"],
        )
        self.assertNotIn("EVIDENCE_CONFLICT", accepted_result["reasons"])
        accepted["action_approvals"] = {"production": True}
        request = governance.build_read_only_action_request(
            accepted,
            action="production",
        )
        self.assertEqual(request["target"]["environment"], "production")

        invalid_values = (
            ("missing", None, True),
            ("true", True, False),
            ("unknown", "UNKNOWN", False),
            ("zero", 0, False),
            ("one", 1, False),
            ("string", "false", False),
            ("null", None, False),
            ("mapping", {}, False),
        )
        for case, conflict, remove in invalid_values:
            assessment = valid_assessment(environment="production")
            if remove:
                assessment["evidence"].pop("conflict")
            else:
                assessment["evidence"]["conflict"] = conflict
            with self.subTest(case=case, stage="readiness"):
                result = governance.evaluate_readiness(
                    assessment,
                    profile=governance.PROFILE_CATALOG["VERCEL_WEB"],
                )
                self.assertEqual(result["readiness"], "NOT_READY")
                self.assertEqual(result["decision"], "No-Go")
                self.assertIn("EVIDENCE_CONFLICT", result["reasons"])

            assessment["action_approvals"] = {"production": True}
            with self.subTest(case=case, stage="production_action"):
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    governance.build_read_only_action_request(
                        assessment,
                        action="production",
                    )
                self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")

    def test_expired_evidence_is_not_ready_and_cannot_produce_go(self):
        assessment = valid_assessment(observed_at_utc="2000-01-01T00:00:00Z")
        assessment["evidence"]["valid_until_utc"] = "2000-01-02T00:00:00Z"

        result = governance.evaluate_readiness(
            assessment,
            profile=governance.PROFILE_CATALOG["VERCEL_WEB"],
        )

        self.assertEqual(result["readiness"], "NOT_READY")
        self.assertEqual(result["decision"], "No-Go")
        self.assertIn("EVIDENCE_EXPIRED", result["reasons"])

        assessment["action_approvals"] = {"production": True}
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.build_read_only_action_request(assessment, action="production")
        self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")


class OutputTests(unittest.TestCase):
    def test_renderers_are_sanitized_and_read_only(self):
        assessment = valid_assessment()
        assessment["status_source"] = {"path": "/secret/status.json", "content": "raw"}
        rendered_json = governance.render_assessment_json(assessment)
        self.assertNotIn("status_source", rendered_json)
        self.assertNotIn("secret", rendered_json.lower())
        self.assertIn('"write_authorized":false', rendered_json)
        payload = json.loads(rendered_json)
        for field in (
            "project_id",
            "environment",
            "readiness",
            "missing_conditions",
            "evidence_time",
            "staging_plan",
            "production_architecture",
            "security",
            "data",
            "monitoring",
            "rollback",
            "decision",
            "write_authorized",
        ):
            self.assertIn(field, payload)
        rendered_md = governance.render_assessment_markdown(assessment)
        self.assertIn("准备度", rendered_md)
        self.assertIn("只读", rendered_md)
        self.assertIn("Go/No-Go", rendered_md)
        self.assertIn("回滚", rendered_md)

    def test_markdown_includes_assumptions_unknowns_and_security_warnings(self):
        assessment = valid_assessment(
            environment="production",
            notes=["发布窗口由负责人确认"],
        )
        assessment["evidence"]["profile_applicability"]["conflict"] = True
        assessment["evidence"]["non_blocking_gaps"] = ["MANUAL_CAPACITY_REVIEW"]

        rendered = governance.render_assessment_markdown(assessment)

        for heading in ("## 假设", "## 未知项", "## 安全警告"):
            self.assertIn(heading, rendered)
        for value in (
            "发布窗口由负责人确认",
            "PROFILE_NOT_APPLICABLE",
            "MANUAL_CAPACITY_REVIEW",
            "TRUSTED_PROXY_UNVERIFIED",
        ):
            self.assertIn(value, rendered)

    def test_renderers_include_deterministic_evidence_references(self):
        assessment = valid_assessment()
        expected = [
            "authentication",
            "backup_recovery",
            "migration",
            "observability",
            "profile_applicability",
            "rollback",
            "tls",
        ]

        payload = json.loads(governance.render_assessment_json(assessment))
        rendered = governance.render_assessment_markdown(assessment)

        self.assertEqual(payload["evidence_references"], expected)
        self.assertIn("## 证据引用", rendered)
        positions = [rendered.index(f"- {reference}") for reference in expected]
        self.assertEqual(positions, sorted(positions))

    def test_action_requests_need_independent_approval_and_have_no_side_effect(self):
        assessment = valid_assessment(environment="production")
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.build_read_only_action_request(assessment, action="production")
        self.assertEqual(caught.exception.reason, "ACTION_NOT_APPROVED")
        assessment["action_approvals"] = {"production": True}
        before = set()
        result = governance.build_read_only_action_request(assessment, action="production")
        self.assertEqual(result["write_authorized"], False)
        self.assertEqual(result["action"], "production")
        self.assertEqual(result["target"]["environment"], "production")
        for field in (
            "target",
            "scope",
            "data",
            "credentials_category",
            "side_effects",
            "success_criteria",
            "failure_criteria",
            "unknown_criteria",
            "rollback",
        ):
            self.assertIn(field, result)
        self.assertEqual(before, set())

    def test_action_request_rejects_cross_environment_and_keeps_approvals_independent(self):
        for environment in ("local", "staging"):
            assessment = valid_assessment(environment=environment)
            assessment["action_approvals"] = {"production": True}
            with self.subTest(environment=environment):
                with self.assertRaises(governance.DeploymentContractError) as caught:
                    governance.build_read_only_action_request(
                        assessment,
                        action="production",
                    )
                self.assertEqual(caught.exception.reason, "PRODUCTION_NOT_READY")

        production = valid_assessment(environment="production")
        production["action_approvals"] = {"staging": True}
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.build_read_only_action_request(production, action="production")
        self.assertEqual(caught.exception.reason, "ACTION_NOT_APPROVED")

        staging = valid_assessment(environment="staging")
        staging["action_approvals"] = {"rollback": True}
        rollback = governance.build_read_only_action_request(staging, action="rollback")
        self.assertEqual(rollback["action"], "rollback")
        self.assertEqual(rollback["target"]["environment"], "staging")

    def test_invalid_action_and_no_socket_process_or_file(self):
        with self.assertRaises(governance.DeploymentContractError) as caught:
            governance.build_read_only_action_request(valid_assessment(), action="deploy")
        self.assertEqual(caught.exception.reason, "FIELD_VALUE_INVALID")


if __name__ == "__main__":
    unittest.main()
