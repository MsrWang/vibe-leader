#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build deterministic, read-only deployment governance assessments."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 1024 * 1024
MAX_TEXT_LENGTH = 4096
MAX_LIST_LENGTH = 128

Environment = Literal["local", "staging", "production"]
Readiness = Literal["READY", "CONDITIONAL", "NOT_READY"]
DeploymentState = Literal[
    "NOT_CONFIGURED",
    "READY_FOR_STAGING",
    "STAGING_VERIFIED",
    "READY_FOR_PRODUCTION",
    "BLOCKED",
    "UNKNOWN",
]
Decision = Literal["Go", "Conditional Go", "No-Go"]

ENVIRONMENTS = frozenset({"local", "staging", "production"})
PROFILE_IDS = frozenset(
    {
        "VERCEL_WEB",
        "LINUX_HOST",
        "DOCKER_SERVICE",
        "PYTHON_SQLITE",
        "FRONTEND_BACKEND_SPLIT",
        "STATIC_SITE",
    }
)
ASSESSMENT_FIELDS = frozenset(
    {
        "schema_version",
        "project_id",
        "profile_id",
        "environment",
        "observed_at_utc",
        "identity",
        "freshness",
        "evidence",
        "notes",
        "write_authorized",
    }
)
IDENTITY_FIELDS = frozenset(
    {"status", "head", "fingerprint_complete", "write_eligibility"}
)
FRESHNESS_FIELDS = frozenset({"state", "reasons"})
FORBIDDEN_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "cookie",
        "authorization",
        "private_key",
        "remote_url",
    }
)
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HEX_HEAD = re.compile(r"^[0-9a-f]{40,64}$")
PROFILE_FIELDS = frozenset(
    {
        "profile_id",
        "display_name_zh",
        "applicability",
        "non_applicability",
        "required_evidence",
        "persistence_model",
        "security_boundary",
        "staging_checks",
        "monitoring_checks",
        "rollback_inputs",
    }
)
EVIDENCE_CATEGORY_SCHEMAS = MappingProxyType(
    {
        "authentication": MappingProxyType(
            {
                "required": frozenset({"verified", "conflict"}),
                "optional": frozenset({"trusted_proxy", "secure_session"}),
                "verification": frozenset({"verified"}),
            }
        ),
        "tls": MappingProxyType(
            {
                "required": frozenset({"verified", "conflict"}),
                "optional": frozenset({"secure_headers", "rate_limit"}),
                "verification": frozenset({"verified"}),
            }
        ),
        "migration": MappingProxyType(
            {
                "required": frozenset({"plan", "isolated", "conflict"}),
                "optional": frozenset(),
                "verification": frozenset({"plan", "isolated"}),
            }
        ),
        "backup_recovery": MappingProxyType(
            {
                "required": frozenset({"verified", "conflict"}),
                "optional": frozenset(),
                "verification": frozenset({"verified"}),
            }
        ),
        "observability": MappingProxyType(
            {
                "required": frozenset(
                    {"logs", "metrics", "health", "alerts", "capacity", "conflict"}
                ),
                "optional": frozenset(),
                "verification": frozenset(
                    {"logs", "metrics", "health", "alerts", "capacity"}
                ),
            }
        ),
        "rollback": MappingProxyType(
            {
                "required": frozenset({"executable", "conflict"}),
                "optional": frozenset(),
                "verification": frozenset({"executable"}),
            }
        ),
        "sqlite": MappingProxyType(
            {
                "required": frozenset(
                    {
                        "filesystem_semantics",
                        "concurrency",
                        "locking",
                        "backup_consistency",
                        "restore_verified",
                        "conflict",
                    }
                ),
                "optional": frozenset(),
                "verification": frozenset(
                    {
                        "filesystem_semantics",
                        "concurrency",
                        "locking",
                        "backup_consistency",
                        "restore_verified",
                    }
                ),
            }
        ),
    }
)
EVIDENCE_FIELDS = frozenset(
    {
        "valid_until_utc",
        "conflict",
        "profile_applicability",
        "non_blocking_gaps",
        *EVIDENCE_CATEGORY_SCHEMAS,
    }
)


def _profile(
    profile_id: str,
    display_name_zh: str,
    *,
    applicability: tuple[str, ...],
    non_applicability: tuple[str, ...],
    required_evidence: tuple[str, ...],
    persistence_model: str,
    security_boundary: tuple[str, ...],
    staging_checks: tuple[str, ...],
    monitoring_checks: tuple[str, ...],
    rollback_inputs: tuple[str, ...],
) -> MappingProxyType:
    return MappingProxyType(
        {
            "profile_id": profile_id,
            "display_name_zh": display_name_zh,
            "applicability": applicability,
            "non_applicability": non_applicability,
            "required_evidence": required_evidence,
            "persistence_model": persistence_model,
            "security_boundary": security_boundary,
            "staging_checks": staging_checks,
            "monitoring_checks": monitoring_checks,
            "rollback_inputs": rollback_inputs,
        }
    )


PROFILE_CATALOG = MappingProxyType(
    {
        "VERCEL_WEB": _profile(
            "VERCEL_WEB",
            "Vercel Web",
            applicability=("web build", "serverless or static delivery"),
            non_applicability=("stateful single-host runtime",),
            required_evidence=(
                "authentication",
                "tls",
                "migration",
                "backup_recovery",
                "observability",
                "rollback",
            ),
            persistence_model="external or explicitly stateless",
            security_boundary=("public entry", "trusted proxy", "session boundary"),
            staging_checks=("preview isolation", "build reproducibility", "runtime configuration categories"),
            monitoring_checks=("logs", "metrics", "health", "alerts", "capacity"),
            rollback_inputs=("immutable release reference", "configuration rollback", "data compatibility"),
        ),
        "LINUX_HOST": _profile(
            "LINUX_HOST",
            "单 Linux 主机",
            applicability=("long-running service", "host-managed process"),
            non_applicability=("provider-managed static delivery",),
            required_evidence=("authentication", "tls", "migration", "backup_recovery", "observability", "rollback"),
            persistence_model="host filesystem or external service",
            security_boundary=("reverse proxy", "service account", "host firewall"),
            staging_checks=("process isolation", "port ownership", "restart behavior"),
            monitoring_checks=("logs", "metrics", "health", "alerts", "capacity"),
            rollback_inputs=("previous artifact", "service configuration", "data compatibility"),
        ),
        "DOCKER_SERVICE": _profile(
            "DOCKER_SERVICE",
            "Docker/容器服务",
            applicability=("container image", "declared runtime dependencies"),
            non_applicability=("host-only undeclared runtime",),
            required_evidence=("authentication", "tls", "migration", "backup_recovery", "observability", "rollback"),
            persistence_model="declared volume or external service",
            security_boundary=("image provenance", "runtime user", "network boundary"),
            staging_checks=("image immutability", "health check", "volume isolation"),
            monitoring_checks=("logs", "metrics", "health", "alerts", "capacity"),
            rollback_inputs=("previous image digest", "configuration rollback", "volume compatibility"),
        ),
        "PYTHON_SQLITE": _profile(
            "PYTHON_SQLITE",
            "Python/SQLite 服务",
            applicability=("Python service", "SQLite persistence"),
            non_applicability=("multi-writer distributed database",),
            required_evidence=("authentication", "tls", "migration", "backup_recovery", "observability", "rollback", "sqlite"),
            persistence_model="single SQLite database with explicit filesystem semantics",
            security_boundary=("application auth", "database file permissions", "trusted proxy"),
            staging_checks=("copied database", "lock behavior", "recovery rehearsal"),
            monitoring_checks=("logs", "metrics", "health", "alerts", "capacity"),
            rollback_inputs=("consistent backup", "schema compatibility", "restore verification"),
        ),
        "FRONTEND_BACKEND_SPLIT": _profile(
            "FRONTEND_BACKEND_SPLIT",
            "前后端分离项目",
            applicability=("separate public frontend", "separate API boundary"),
            non_applicability=("single static artifact without API",),
            required_evidence=("authentication", "tls", "migration", "backup_recovery", "observability", "rollback"),
            persistence_model="backend-owned or external persistence",
            security_boundary=("browser origin", "API origin", "CORS and session boundary"),
            staging_checks=("origin isolation", "API compatibility", "frontend-backend version matrix"),
            monitoring_checks=("logs", "metrics", "health", "alerts", "capacity"),
            rollback_inputs=("frontend artifact", "backend artifact", "API and data compatibility"),
        ),
        "STATIC_SITE": _profile(
            "STATIC_SITE",
            "静态网站",
            applicability=("static artifact", "no server-side persistence"),
            non_applicability=("server-side session or mutable application data",),
            required_evidence=("tls", "observability", "rollback"),
            persistence_model="none",
            security_boundary=("public origin", "content security policy"),
            staging_checks=("artifact integrity", "link validation", "cache policy"),
            monitoring_checks=("logs", "health", "alerts", "capacity"),
            rollback_inputs=("previous immutable artifact", "cache invalidation plan"),
        ),
    }
)
DEPLOYMENT_STATES = frozenset(
    {
        "NOT_CONFIGURED",
        "READY_FOR_STAGING",
        "STAGING_VERIFIED",
        "READY_FOR_PRODUCTION",
        "BLOCKED",
        "UNKNOWN",
    }
)
TRANSITIONS = MappingProxyType(
    {
        ("NOT_CONFIGURED", "staging_ready"): ("READY_FOR_STAGING", False),
        ("READY_FOR_STAGING", "staging_verified"): ("STAGING_VERIFIED", True),
        ("STAGING_VERIFIED", "production_ready"): ("READY_FOR_PRODUCTION", True),
        ("READY_FOR_PRODUCTION", "rollback_requested"): ("STAGING_VERIFIED", True),
        ("STAGING_VERIFIED", "rollback_requested"): ("READY_FOR_STAGING", True),
    }
)


class DeploymentContractError(ValueError):
    reason: str

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise DeploymentContractError(reason)


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("FIELD_TYPE_INVALID")
            if key.casefold() in FORBIDDEN_KEYS:
                _fail("FIELD_VALUE_INVALID")
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)


def _validate_bounded_tree(value: object, *, depth: int = 0) -> None:
    if depth > 16:
        _fail("FIELD_VALUE_INVALID")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if not value or len(value) > MAX_TEXT_LENGTH or "\x00" in value:
            _fail("FIELD_VALUE_INVALID")
        try:
            value.encode("utf-8", "strict")
        except UnicodeEncodeError:
            _fail("FIELD_VALUE_INVALID")
        return
    if isinstance(value, list):
        if len(value) > MAX_LIST_LENGTH:
            _fail("FIELD_VALUE_INVALID")
        for item in value:
            _validate_bounded_tree(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_LIST_LENGTH:
            _fail("FIELD_VALUE_INVALID")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                _fail("FIELD_TYPE_INVALID")
            _validate_bounded_tree(item, depth=depth + 1)
        return
    _fail("FIELD_TYPE_INVALID")


def _validate_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("FIELD_VALUE_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("FIELD_VALUE_INVALID")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("FIELD_VALUE_INVALID")
    return parsed


def _validate_evidence_expiry(value: object, *, observed_at: datetime) -> datetime:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    try:
        valid_until = _validate_utc_timestamp(value.get("valid_until_utc"))
    except DeploymentContractError:
        _fail("EVIDENCE_EXPIRED")
    if valid_until <= observed_at:
        _fail("EVIDENCE_EXPIRED")
    return valid_until


def _validate_identity(value: object) -> None:
    if not isinstance(value, dict):
        _fail("FIELD_TYPE_INVALID")
    if frozenset(value) != IDENTITY_FIELDS:
        _fail("PROJECT_IDENTITY_INCOMPLETE")
    if (
        value.get("status") != "bound"
        or value.get("fingerprint_complete") is not True
        or value.get("write_eligibility") not in {"READ_ONLY", "ELIGIBLE"}
        or not isinstance(value.get("head"), str)
        or HEX_HEAD.fullmatch(str(value["head"])) is None
    ):
        _fail("PROJECT_IDENTITY_INCOMPLETE")


def _validate_freshness(value: object) -> None:
    if not isinstance(value, dict) or frozenset(value) != FRESHNESS_FIELDS:
        _fail("FIELD_TYPE_INVALID")
    if value.get("state") not in {"FRESH", "STALE", "UNKNOWN", "AMBIGUOUS"}:
        _fail("FIELD_VALUE_INVALID")
    reasons = value.get("reasons")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        _fail("FIELD_TYPE_INVALID")


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("SCHEMA_FIELDS_CHANGED")
        result[key] = value
    return result


def parse_assessment_json(value: str | bytes) -> dict[str, object]:
    if isinstance(value, bytes):
        if len(value) > MAX_JSON_BYTES:
            _fail("FIELD_VALUE_INVALID")
        try:
            text = value.decode("utf-8", "strict")
        except UnicodeDecodeError:
            _fail("FIELD_VALUE_INVALID")
    elif isinstance(value, str):
        if len(value.encode("utf-8", "strict")) > MAX_JSON_BYTES:
            _fail("FIELD_VALUE_INVALID")
        text = value
    else:
        _fail("FIELD_TYPE_INVALID")
    if text.startswith("\ufeff"):
        _fail("FIELD_VALUE_INVALID")
    try:
        parsed = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except DeploymentContractError:
        raise
    except (UnicodeError, ValueError, TypeError):
        _fail("FIELD_VALUE_INVALID")
    return validate_assessment_input(parsed)


def validate_assessment_input(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    _reject_forbidden_keys(value)
    _validate_bounded_tree(value)
    version = value.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        _fail("SCHEMA_UNSUPPORTED")
    if frozenset(value) != ASSESSMENT_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    project_id = value.get("project_id")
    if not isinstance(project_id, str) or PROJECT_ID.fullmatch(project_id) is None:
        _fail("FIELD_VALUE_INVALID")
    if value.get("profile_id") not in PROFILE_IDS or value.get("environment") not in ENVIRONMENTS:
        _fail("FIELD_VALUE_INVALID")
    observed_at = _validate_utc_timestamp(value.get("observed_at_utc"))
    if observed_at > datetime.now(timezone.utc):
        _fail("EVIDENCE_FUTURE")
    _validate_identity(value.get("identity"))
    _validate_freshness(value.get("freshness"))
    if not isinstance(value.get("evidence"), dict) or not isinstance(value.get("notes"), list):
        _fail("FIELD_TYPE_INVALID")
    _validate_evidence_expiry(value["evidence"], observed_at=observed_at)
    if any(not isinstance(item, str) for item in value["notes"]):
        _fail("FIELD_TYPE_INVALID")
    if value.get("write_authorized") is not False:
        _fail("FIELD_VALUE_INVALID")
    return copy.deepcopy(value)


def canonical_assessment_digest(value: object) -> str:
    validated = validate_assessment_input(value)
    digest_value = {key: item for key, item in validated.items() if key != "observed_at_utc"}
    encoded = json.dumps(
        digest_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_profile_record(value: object) -> dict[str, object]:
    if not isinstance(value, (dict, MappingProxyType)):
        _fail("FIELD_TYPE_INVALID")
    record = {
        key: list(item) if isinstance(item, tuple) else item
        for key, item in dict(value).items()
    }
    _reject_forbidden_keys(record)
    _validate_bounded_tree(record)
    if frozenset(record) != PROFILE_FIELDS:
        _fail("SCHEMA_FIELDS_CHANGED")
    if record.get("profile_id") not in PROFILE_IDS:
        _fail("FIELD_VALUE_INVALID")
    if not isinstance(record.get("display_name_zh"), str):
        _fail("FIELD_TYPE_INVALID")
    for field in (
        "applicability",
        "non_applicability",
        "required_evidence",
        "security_boundary",
        "staging_checks",
        "monitoring_checks",
        "rollback_inputs",
    ):
        items = record.get(field)
        if not isinstance(items, (list, tuple)) or not items:
            _fail("FIELD_TYPE_INVALID")
        if any(not isinstance(item, str) or not item for item in items):
            _fail("FIELD_TYPE_INVALID")
        record[field] = list(items)
    if not isinstance(record.get("persistence_model"), str):
        _fail("FIELD_TYPE_INVALID")
    canonical = PROFILE_CATALOG.get(str(record["profile_id"]))
    if canonical is None:
        _fail("FIELD_VALUE_INVALID")
    canonical_record = {
        key: list(item) if isinstance(item, tuple) else item
        for key, item in dict(canonical).items()
    }
    if record != canonical_record:
        _fail("PROFILE_NOT_APPLICABLE")
    return copy.deepcopy(record)


def _evidence_category_contract_valid(value: object, category: str) -> bool:
    schema = EVIDENCE_CATEGORY_SCHEMAS.get(category)
    if not isinstance(value, dict) or schema is None:
        return False
    required = schema["required"]
    optional = schema["optional"]
    assert isinstance(required, frozenset) and isinstance(optional, frozenset)
    fields = frozenset(value)
    if not required.issubset(fields) or not fields.issubset(required | optional):
        return False
    if any(type(item) is not bool for item in value.values()):
        return False
    return value["conflict"] is False


def _evidence_contract_valid(evidence: dict[str, object]) -> bool:
    if not frozenset(evidence).issubset(EVIDENCE_FIELDS):
        return False
    non_blocking = evidence.get("non_blocking_gaps", [])
    if not isinstance(non_blocking, list) or any(
        not isinstance(item, str) or not item or item == "UNKNOWN"
        for item in non_blocking
    ):
        return False
    return all(
        category not in evidence
        or _evidence_category_contract_valid(evidence[category], category)
        for category in EVIDENCE_CATEGORY_SCHEMAS
    )


def _evidence_verified(evidence: dict[str, object], category: str) -> bool:
    value = evidence.get(category)
    if not _evidence_category_contract_valid(value, category):
        return False
    assert isinstance(value, dict)
    verification = EVIDENCE_CATEGORY_SCHEMAS[category]["verification"]
    assert isinstance(verification, frozenset)
    return all(value[field] is True for field in verification)


def _reason_for_evidence(category: str) -> str:
    return {
        "authentication": "AUTHENTICATION_UNVERIFIED",
        "tls": "TLS_UNVERIFIED",
        "migration": "MIGRATION_PLAN_MISSING",
        "backup_recovery": "BACKUP_RECOVERY_UNVERIFIED",
        "observability": "OBSERVABILITY_INCOMPLETE",
        "rollback": "ROLLBACK_NOT_EXECUTABLE",
        "sqlite": "BACKUP_RECOVERY_UNVERIFIED",
    }.get(category, "EVIDENCE_CONFLICT")


def _profile_applicability_proven(
    evidence: dict[str, object],
    profile: dict[str, object],
) -> bool:
    proof = evidence.get("profile_applicability")
    if not isinstance(proof, dict) or frozenset(proof) != {
        "profile_id",
        "applicability",
        "non_applicability",
        "conflict",
    }:
        return False
    if proof.get("profile_id") != profile["profile_id"] or proof.get("conflict") is not False:
        return False

    applicable = proof.get("applicability")
    non_applicable = proof.get("non_applicability")
    if not isinstance(applicable, dict) or not isinstance(non_applicable, dict):
        return False

    required_positive = set(profile["applicability"])
    required_negative = set(profile["non_applicability"])
    return (
        set(applicable) == required_positive
        and set(non_applicable) == required_negative
        and all(value is True for value in applicable.values())
        and all(value is False for value in non_applicable.values())
    )


def evaluate_readiness(
    assessment: dict[str, object],
    *,
    profile: dict[str, object] | MappingProxyType,
) -> dict[str, object]:
    validated = validate_assessment_input(assessment)
    profile_record = validate_profile_record(profile)
    reasons: list[str] = []
    warnings: list[str] = []

    evidence = validated["evidence"]
    assert isinstance(evidence, dict)
    if (
        validated["profile_id"] != profile_record["profile_id"]
        or not _profile_applicability_proven(evidence, profile_record)
    ):
        reasons.append("PROFILE_NOT_APPLICABLE")
        profile_state = "NOT_APPLICABLE"
    else:
        profile_state = "SELECTED"

    freshness = validated["freshness"]
    if not isinstance(freshness, dict) or freshness.get("state") != "FRESH":
        reasons.append("PROJECT_FRESHNESS_UNKNOWN")
    identity = validated["identity"]
    if not isinstance(identity, dict) or identity.get("status") != "bound":
        reasons.append("PROJECT_IDENTITY_INCOMPLETE")

    valid_until = _validate_utc_timestamp(evidence["valid_until_utc"])
    if valid_until <= datetime.now(timezone.utc):
        reasons.append("EVIDENCE_EXPIRED")
    if evidence.get("conflict") is not False:
        reasons.append("EVIDENCE_CONFLICT")
    if not _evidence_contract_valid(evidence):
        reasons.append("EVIDENCE_CONFLICT")
    for category in profile_record["required_evidence"]:
        if not _evidence_verified(evidence, str(category)):
            reasons.append(_reason_for_evidence(str(category)))

    non_blocking = evidence.get("non_blocking_gaps", [])
    if isinstance(non_blocking, list):
        warnings.extend(
            item for item in non_blocking if isinstance(item, str) and item
        )
    if validated["environment"] in {"staging", "production"}:
        authentication = evidence.get("authentication")
        tls = evidence.get("tls")
        if isinstance(authentication, dict):
            if authentication.get("trusted_proxy") is not True:
                warnings.append("TRUSTED_PROXY_UNVERIFIED")
            if authentication.get("secure_session") is not True:
                warnings.append("SESSION_SECURITY_UNVERIFIED")
        if isinstance(tls, dict):
            if tls.get("secure_headers") is not True:
                warnings.append("SECURITY_HEADERS_UNVERIFIED")
            if tls.get("rate_limit") is not True:
                warnings.append("RATE_LIMIT_UNVERIFIED")

    reasons = sorted(set(reasons))
    warnings = sorted(set(warnings))
    if reasons:
        readiness: Readiness = "NOT_READY"
        decision: Decision = "No-Go"
    elif warnings:
        readiness = "CONDITIONAL"
        decision = "Conditional Go"
    else:
        readiness = "READY"
        decision = "Go"
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": validated["project_id"],
        "profile_id": profile_record["profile_id"],
        "profile_state": profile_state,
        "environment": validated["environment"],
        "readiness": readiness,
        "decision": decision,
        "reasons": reasons,
        "warnings": warnings,
        "evidence_references": sorted(
            {
                category
                for category in profile_record["required_evidence"]
                if category in evidence
            }
            | ({"profile_applicability"} if "profile_applicability" in evidence else set())
        ),
        "write_authorized": False,
    }


def transition_state(
    current: DeploymentState,
    event: str,
    *,
    evidence_complete: bool,
    approved: bool,
) -> DeploymentState:
    if current not in DEPLOYMENT_STATES or not isinstance(event, str):
        _fail("FIELD_VALUE_INVALID")
    if type(evidence_complete) is not bool or type(approved) is not bool:
        _fail("FIELD_TYPE_INVALID")
    if current in {"BLOCKED", "UNKNOWN"}:
        _fail("PRODUCTION_NOT_READY")
    transition = TRANSITIONS.get((current, event))
    if transition is None:
        if event == "production_ready":
            _fail("PRODUCTION_NOT_READY")
        _fail("FIELD_VALUE_INVALID")
    target, approval_required = transition
    if not evidence_complete:
        return "UNKNOWN"
    if approval_required and not approved:
        _fail("ACTION_NOT_APPROVED")
    return target


def _core_assessment(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("INPUT_NOT_OBJECT")
    _reject_forbidden_keys(value)
    return validate_assessment_input(
        {key: value[key] for key in ASSESSMENT_FIELDS if key in value}
    )


def _assessment_output(value: object) -> dict[str, object]:
    assessment = _core_assessment(value)
    profile = validate_profile_record(PROFILE_CATALOG[str(assessment["profile_id"])])
    result = evaluate_readiness(assessment, profile=profile)
    evidence = assessment["evidence"]
    assert isinstance(evidence, dict)
    migration = evidence.get("migration")
    observability = evidence.get("observability")
    rollback = evidence.get("rollback")
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": assessment["project_id"],
        "profile_id": assessment["profile_id"],
        "profile_state": result["profile_state"],
        "environment": assessment["environment"],
        "evidence_time": assessment["observed_at_utc"],
        "evidence_references": result["evidence_references"],
        "readiness": result["readiness"],
        "missing_conditions": result["reasons"],
        "assumptions": list(assessment["notes"]),
        "unknowns": result["reasons"] if result["readiness"] == "NOT_READY" else [],
        "staging_plan": list(profile["staging_checks"]),
        "production_architecture": {
            "persistence_model": profile["persistence_model"],
            "security_boundary": list(profile["security_boundary"]),
        },
        "security": {
            "authentication_required": "authentication" in profile["required_evidence"],
            "tls_required": "tls" in profile["required_evidence"],
            "warnings": result["warnings"],
        },
        "data": {
            "migration_planned": isinstance(migration, dict) and migration.get("plan") is True,
            "migration_isolated": isinstance(migration, dict) and migration.get("isolated") is True,
            "persistence_model": profile["persistence_model"],
        },
        "monitoring": {
            field: isinstance(observability, dict) and observability.get(field) is True
            for field in ("logs", "metrics", "health", "alerts", "capacity")
        },
        "rollback": {
            "executable": isinstance(rollback, dict) and rollback.get("executable") is True,
            "required_inputs": list(profile["rollback_inputs"]),
        },
        "decision": result["decision"],
        "write_authorized": False,
    }


def render_assessment_json(assessment: object) -> str:
    return json.dumps(
        _assessment_output(assessment),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _markdown_list(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "- 无"
    lines: list[str] = []
    for item in items:
        text = str(item).replace("\r", " ").replace("\n", " ").replace("`", "'")
        lines.append(f"- {text}")
    return "\n".join(lines)


def render_assessment_markdown(assessment: object) -> str:
    output = _assessment_output(assessment)
    security = output["security"]
    assert isinstance(security, dict)
    monitoring = output["monitoring"]
    assert isinstance(monitoring, dict)
    rollback = output["rollback"]
    assert isinstance(rollback, dict)
    return "\n".join(
        (
            f"# {output['project_id']} 部署治理评估",
            "",
            f"- 档案：`{output['profile_id']}`",
            f"- 目标环境：`{output['environment']}`",
            f"- 准备度：`{output['readiness']}`",
            f"- Go/No-Go：`{output['decision']}`",
            "- 执行权限：只读（`write_authorized=false`）",
            f"- 证据时间：`{output['evidence_time']}`",
            "",
            "## 缺失条件",
            "",
            _markdown_list(output["missing_conditions"]),
            "",
            "## 证据引用",
            "",
            _markdown_list(output["evidence_references"]),
            "",
            "## 假设",
            "",
            _markdown_list(output["assumptions"]),
            "",
            "## 未知项",
            "",
            _markdown_list(output["unknowns"]),
            "",
            "## 安全警告",
            "",
            _markdown_list(security["warnings"]),
            "",
            "## 最小 staging 计划",
            "",
            _markdown_list(output["staging_plan"]),
            "",
            "## 生产架构与安全",
            "",
            _markdown_list(output["production_architecture"]["security_boundary"]),
            "",
            "## 数据与监控",
            "",
            f"- 迁移计划：{str(output['data']['migration_planned']).lower()}",
            f"- 迁移隔离：{str(output['data']['migration_isolated']).lower()}",
            "- 监控：" + ", ".join(
                f"{name}={str(value).lower()}" for name, value in sorted(monitoring.items())
            ),
            "",
            "## 回滚",
            "",
            f"- 可执行：{str(rollback['executable']).lower()}",
            _markdown_list(rollback["required_inputs"]),
        )
    ) + "\n"


def build_read_only_action_request(
    assessment: dict[str, object],
    *,
    action: Literal["staging", "production", "rollback"],
) -> dict[str, object]:
    if action not in {"staging", "production", "rollback"}:
        _fail("FIELD_VALUE_INVALID")
    core = _core_assessment(assessment)
    approvals = assessment.get("action_approvals")
    if not isinstance(approvals, dict) or approvals.get(action) is not True:
        _fail("ACTION_NOT_APPROVED")
    if any(key not in {"staging", "production", "rollback"} for key in approvals):
        _fail("SCHEMA_FIELDS_CHANGED")
    if action == "production" and core["environment"] != "production":
        _fail("PRODUCTION_NOT_READY")
    if action == "staging" and core["environment"] != "staging":
        _fail("FIELD_VALUE_INVALID")
    output = _assessment_output(core)
    if action == "production" and output["readiness"] == "NOT_READY":
        _fail("PRODUCTION_NOT_READY")
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "target": {
            "project_id": core["project_id"],
            "profile_id": core["profile_id"],
            "environment": core["environment"],
        },
        "scope": ["plan", "preconditions", "verification", "rollback"],
        "data": ["category-only metadata", "no production records"],
        "credentials_category": ["provider account", "runtime configuration"],
        "side_effects": ["external state change if separately executed"],
        "success_criteria": ["target state verified", "health evidence complete"],
        "failure_criteria": ["verification failed", "identity or evidence drift"],
        "unknown_criteria": ["platform result unavailable", "cleanup incomplete"],
        "rollback": output["rollback"],
        "approval_recorded": True,
        "write_authorized": False,
    }
