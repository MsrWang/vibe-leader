#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Compare project/runtime facts or explicitly import an existing program capture."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path


SCHEMA_VERSION = 2
MAX_INPUT_BYTES = 4 * 1024 * 1024
GENERATION_TIME_FIELDS = {
    "bound_at_utc",
    "captured_at_utc",
    "generated_at_utc",
}
STATE_PRIORITY = {
    "FRESH": 0,
    "STALE": 1,
    "UNKNOWN": 2,
    "AMBIGUOUS": 3,
}
IDENTITY_FIELDS = (
    "schema_version",
    "status",
    "binding_kind",
    "write_eligibility",
    "requested_cwd",
    "pwd",
    "logical_path",
    "physical_path",
    "filesystem_identity",
    "git_top_level",
    "git_dir",
    "git_common_dir",
    "worktree_id",
    "branch",
    "head",
    "dirty_fingerprint",
    "dirty_fingerprint_schema",
    "fingerprint_complete",
    "fingerprint_applicability",
    "runtime_surface",
    "git",
    "aliases",
)
INVENTORY_FIELDS = (
    "supervisor_identity",
    "supervisor_state",
    "supervisor_identity_source",
    "inventory_sha256",
)
STATUS_SOURCE_FIELDS = (
    "path",
    "content_sha256",
    "observed_head",
)
RUNTIME_FIELDS = ("surface", "codex_version", "core_sha256")


class FreshnessInputError(ValueError):
    pass


def _project_identity_module():
    if __package__:
        from workbench import project_identity
    else:
        import project_identity

    return project_identity


def _without_generation_time(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_generation_time(item)
            for key, item in value.items()
            if key not in GENERATION_TIME_FIELDS
        }
    if isinstance(value, list):
        return [_without_generation_time(item) for item in value]
    return value


def canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        _without_generation_time(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_result(
    state: str,
    reasons: list[str],
    changed_fields: list[str],
) -> dict[str, object]:
    return {
        "state": state,
        "reasons": sorted(set(reasons)),
        "changed_fields": sorted(set(changed_fields)),
    }


def _ambiguous_project_reasons(current: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    if current.get("status") == "ambiguous":
        reasons.append("AMBIGUOUS_PROJECT_STATUS")
    aliases = current.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if not isinstance(alias, dict):
                continue
            relation = alias.get("relation_to_workspace")
            if isinstance(relation, str) and relation in {"AMBIGUOUS", "DIFFERENT_OBJECT"}:
                reasons.append("AMBIGUOUS_PROJECT_ALIAS")
                break
    return reasons


def _identity_write_candidate_reasons(
    value: dict[str, object],
    *,
    label: str,
) -> list[str]:
    reasons: list[str] = []
    project_identity = _project_identity_module()
    try:
        project_identity.validate_identity_schema(
            value,
            supported_versions=(2,),
        )
        project_identity.validate_git_worktree_facts(value)
    except project_identity.IdentitySchemaError as error:
        reasons.append(f"INVALID_IDENTITY_SCHEMA:{label}:{error.reason}")

    if value.get("status") != "bound" or value.get("reason") is not None:
        reasons.append(f"IDENTITY_CAPTURE_NOT_BOUND:{label}")
    if (
        value.get("binding_kind") != "GIT_WORKTREE"
        or value.get("is_git") is not True
    ):
        reasons.append(f"IDENTITY_NOT_GIT_WORKTREE:{label}")

    eligibility = value.get("write_eligibility")
    if not isinstance(eligibility, str) or eligibility not in {
        "ELIGIBLE", "READ_ONLY", "BLOCKED", "UNKNOWN"
    }:
        reasons.append(f"INVALID_WRITE_ELIGIBILITY:{label}")
    elif eligibility != "ELIGIBLE":
        reasons.append(f"WRITE_ELIGIBILITY_NOT_ELIGIBLE:{label}")

    if value.get("fingerprint_applicability") != "REQUIRED":
        reasons.append(f"FINGERPRINT_NOT_REQUIRED:{label}")
    if (
        value.get("fingerprint_complete") is not True
        or value.get("fingerprint_reason") is not None
    ):
        reasons.append(f"INCOMPLETE_FINGERPRINT:{label}")
    return reasons


def compare_identity(
    baseline: dict[str, object],
    current: dict[str, object],
) -> dict[str, object]:
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        return _state_result("UNKNOWN", ["IDENTITY_NOT_OBJECT"], [])

    ambiguous = _ambiguous_project_reasons(current)
    reasons = _identity_write_candidate_reasons(baseline, label="baseline")
    reasons.extend(_identity_write_candidate_reasons(current, label="current"))
    changed_fields: list[str] = []
    for field in IDENTITY_FIELDS:
        if field not in baseline or field not in current:
            reasons.append(f"MISSING_REQUIRED_FIELD:project.{field}")
            continue
        if _without_generation_time(baseline[field]) != _without_generation_time(
            current[field]
        ):
            changed_fields.append(f"project.{field}")

    if ambiguous:
        return _state_result("AMBIGUOUS", ambiguous + reasons, changed_fields)
    if reasons:
        return _state_result("UNKNOWN", reasons, changed_fields)
    if changed_fields:
        return _state_result(
            "STALE",
            [f"FIELD_CHANGED:{field}" for field in changed_fields],
            changed_fields,
        )
    return _state_result("FRESH", [], [])


def _valid_runtime_evidence(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != set(RUNTIME_FIELDS):
        return False
    for field in ("surface", "codex_version"):
        text = value[field]
        if (
            not isinstance(text, str) or not text or text.strip() != text
            or text.upper() in {"UNKNOWN", "UNAVAILABLE", "NOT_APPLICABLE", "NOT_CAPTURED"}
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in text)
        ):
            return False
    digest = value["core_sha256"]
    return isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None


def compare_runtime(
    baseline: object,
    current: object,
    *,
    baseline_surface: object,
    current_surface: object,
) -> dict[str, object]:
    """Compare explicit program evidence; never discover a CLI, home or binary.

    Both sides are required. Equality of OS facts alone cannot establish program
    freshness. The caller owns evidence capture and its authorization/lifecycle.
    """
    reasons: list[str] = []
    changed: list[str] = []
    project_identity = _project_identity_module()
    for label, evidence, surface in (
        ("baseline", baseline, baseline_surface),
        ("current", current, current_surface),
    ):
        if not _valid_runtime_evidence(evidence):
            reasons.append(f"INVALID_RUNTIME_EVIDENCE:{label}")
            continue
        try:
            project_identity.validate_runtime_surface(surface)
        except project_identity.IdentitySchemaError:
            reasons.append(f"INVALID_RUNTIME_SURFACE:{label}")
            continue
        expected_surface = "wsl" if surface["is_wsl"] else surface["platform"]
        if evidence["surface"] != expected_surface:
            reasons.append(f"RUNTIME_SURFACE_MISMATCH:{label}")
    if _valid_runtime_evidence(baseline) and _valid_runtime_evidence(current):
        changed = [f"runtime.{field}" for field in RUNTIME_FIELDS
                   if baseline[field] != current[field]]
    if reasons:
        return _state_result("UNKNOWN", reasons, changed)
    if changed:
        return _state_result("STALE", [f"FIELD_CHANGED:{field}" for field in changed], changed)
    return _state_result("FRESH", [], [])


def runtime_evidence_from_capture(
    capture: object,
    *,
    codex_bin: Path,
    identity: object,
) -> dict[str, str]:
    """Import an existing evaluation-surface capture; never execute its argv.

    Recheck the explicitly selected program bytes and the recorded version-output
    binding. Capture authenticity and lifecycle still belong to the caller. This
    is not an adapter for skill-inventory or a replacement capture system.
    """
    if not isinstance(identity, dict) or _identity_write_candidate_reasons(identity, label="capture"):
        raise FreshnessInputError("RUNTIME_CAPTURE_IDENTITY_INVALID")
    if __package__:
        from workbench import evaluation_surface
    else:
        import evaluation_surface
    try:
        normalized = evaluation_surface._normalized_runtime_capture_contract(capture, codex_bin)
        if type(normalized["schema_version"]) is not int or any(
            type(command["exit_code"]) is not int for command in normalized["commands"]
        ):
            raise FreshnessInputError("RUNTIME_CAPTURE_INVALID")
        surface = identity["runtime_surface"]
        runtime = {
            "surface": "wsl" if surface["is_wsl"] else surface["platform"],
            "codex_version": normalized["version"],
            "core_sha256": normalized["codex_sha256"],
        }
        if not _valid_runtime_evidence(runtime):
            raise FreshnessInputError("RUNTIME_CAPTURE_INVALID")
        # The producer strips line endings. Accept only these explicitly supported
        # encodings, rather than treating an unrelated version string as proven.
        version_bytes = runtime["codex_version"].encode("utf-8")
        version_hashes = {hashlib.sha256(version_bytes + ending).hexdigest()
                          for ending in (b"", b"\n", b"\r\n")}
        if normalized["version_sha256"] not in version_hashes:
            raise FreshnessInputError("RUNTIME_CAPTURE_VERSION_MISMATCH")
        if evaluation_surface._sha256_runtime_binary(codex_bin) != runtime["core_sha256"]:
            raise FreshnessInputError("RUNTIME_CAPTURE_PROGRAM_CHANGED")
    except evaluation_surface.SurfaceUnproven:
        raise FreshnessInputError("RUNTIME_CAPTURE_INVALID") from None
    return runtime


def _baseline_component(
    baseline: dict[str, object] | None,
    *names: str,
) -> dict[str, object] | None:
    if not isinstance(baseline, dict):
        return None
    for name in names:
        value = baseline.get(name)
        if isinstance(value, dict):
            return value
    return None


def _component_result(
    baseline: dict[str, object] | None,
    current: dict[str, object] | None,
    *,
    prefix: str,
    fields: tuple[str, ...],
) -> tuple[dict[str, object], dict[str, object]]:
    current_missing = not isinstance(current, dict)
    current_value = copy.deepcopy(current) if isinstance(current, dict) else {
        "applicability": "NOT_APPLICABLE"
    }
    applicability = current_value.get("applicability", "NOT_APPLICABLE")
    baseline_applicability = (
        baseline.get("applicability") if isinstance(baseline, dict) else None
    )
    if baseline_applicability == "REQUIRED" and (
        current_missing or applicability != "REQUIRED"
    ):
        current_value["route_state"] = "UNKNOWN"
        return current_value, _state_result(
            "UNKNOWN",
            [f"REQUIRED_COMPONENT_OMITTED:{prefix}"],
            [],
        )
    if applicability not in {"REQUIRED", "OPTIONAL", "NOT_APPLICABLE"}:
        current_value["route_state"] = "UNKNOWN"
        return current_value, _state_result(
            "UNKNOWN",
            [f"INVALID_APPLICABILITY:{prefix}"],
            [],
        )
    if applicability == "NOT_APPLICABLE":
        current_value["route_state"] = "NOT_APPLICABLE"
        return current_value, _state_result("FRESH", [], [])

    reasons: list[str] = []
    changed: list[str] = []
    if not isinstance(baseline, dict):
        reasons.append(f"MISSING_COMPONENT_BASELINE:{prefix}")
    for field in fields:
        if field not in current_value:
            reasons.append(f"MISSING_REQUIRED_FIELD:{prefix}.{field}")
        elif isinstance(baseline, dict) and field not in baseline:
            reasons.append(f"MISSING_BASELINE_FIELD:{prefix}.{field}")
        elif isinstance(baseline, dict) and _without_generation_time(
            baseline[field]
        ) != _without_generation_time(current_value[field]):
            changed.append(f"{prefix}.{field}")

    ambiguous = (
        prefix == "skills"
        and current_value.get("supervisor_state") in {"DUPLICATE", "AMBIGUOUS"}
    )
    unavailable = (
        prefix == "skills"
        and (
            current_value.get("supervisor_identity_source") == "UNAVAILABLE"
            or current_value.get("supervisor_state") not in {None, "ENABLED_UNIQUE"}
        )
    )
    if ambiguous:
        component = _state_result(
            "AMBIGUOUS",
            ["AMBIGUOUS_SUPERVISOR_IDENTITY"] + reasons,
            changed,
        )
    elif reasons or unavailable:
        if unavailable:
            reasons.append("SUPERVISOR_IDENTITY_UNAVAILABLE")
        component = _state_result("UNKNOWN", reasons, changed)
    elif changed:
        component = _state_result(
            "STALE",
            [f"FIELD_CHANGED:{field}" for field in changed],
            changed,
        )
    else:
        component = _state_result("FRESH", [], [])
    current_value["route_state"] = component["state"]
    if applicability == "OPTIONAL":
        return current_value, _state_result("FRESH", [], [])
    return current_value, component


def _highest_state(*results: dict[str, object]) -> str:
    return max(
        (str(result["state"]) for result in results),
        key=lambda state: STATE_PRIORITY[state],
    )


def build_freshness_envelope(
    baseline: dict[str, object] | None,
    current: dict[str, object],
    *,
    inventory: dict[str, object] | None = None,
    status_source: dict[str, object] | None = None,
    runtime: dict[str, object] | None = None,
) -> dict[str, object]:
    if not isinstance(current, dict):
        raise FreshnessInputError("current identity must be an object")
    baseline_project = _baseline_component(baseline, "project")
    if baseline_project is None and isinstance(baseline, dict):
        baseline_project = baseline

    baseline_inventory = _baseline_component(baseline, "skills", "inventory")
    skills, skills_state = _component_result(
        baseline_inventory,
        inventory,
        prefix="skills",
        fields=INVENTORY_FIELDS,
    )
    baseline_status = _baseline_component(baseline, "status_source")
    status, status_state = _component_result(
        baseline_status,
        status_source,
        prefix="status_source",
        fields=STATUS_SOURCE_FIELDS,
    )

    if baseline_project is None:
        current_check = compare_identity(current, current)
        if current_check["state"] == "AMBIGUOUS":
            project_state = current_check
        else:
            project_state = _state_result(
                "UNKNOWN",
                ["NO_COMPARISON_BASELINE"],
                [],
            )
    else:
        project_state = compare_identity(baseline_project, current)

    runtime_state = compare_runtime(
        _baseline_component(baseline, "runtime"), runtime,
        baseline_surface=(baseline_project.get("runtime_surface")
                          if isinstance(baseline_project, dict) else None),
        current_surface=current.get("runtime_surface"),
    )
    overall_state = _highest_state(project_state, runtime_state, skills_state, status_state)
    relevant_results = [project_state, runtime_state]
    if skills.get("applicability") == "REQUIRED" or skills_state["state"] != "FRESH":
        relevant_results.append(skills_state)
    if status.get("applicability") == "REQUIRED" or status_state["state"] != "FRESH":
        relevant_results.append(status_state)
    reasons = sorted(
        {
            str(reason)
            for result in relevant_results
            for reason in result["reasons"]
        }
    )
    changed_fields = sorted(
        {
            str(field)
            for result in relevant_results
            for field in result["changed_fields"]
        }
    )
    freshness = {
        "state": overall_state,
        "reasons": reasons,
        "changed_fields": changed_fields,
        "write_precondition_satisfied": (
            overall_state == "FRESH"
            and isinstance(baseline_project, dict)
            and not _identity_write_candidate_reasons(
                baseline_project,
                label="baseline",
            )
            and not _identity_write_candidate_reasons(
                current,
                label="current",
            )
        ),
        "write_authorized": False,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": current.get("captured_at_utc"),
        "baseline": {
            "captured_at_utc": (
                baseline_project.get("captured_at_utc")
                if isinstance(baseline_project, dict)
                else None
            )
        },
        "runtime": copy.deepcopy(runtime),
        "project": copy.deepcopy(current),
        "skills": skills,
        "status_source": status,
        "freshness": freshness,
    }


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FreshnessInputError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise FreshnessInputError("no-follow input reads unavailable")
    descriptor = os.open(path, flags | nofollow)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT_BYTES:
            raise FreshnessInputError("input must be a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise FreshnessInputError("input exceeds byte limit")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return json.loads(b"".join(chunks).decode("utf-8", "strict"),
                      object_pairs_hook=_unique_json_object)


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise FreshnessInputError("no-follow output writes unavailable")
    descriptor = os.open(path, flags | nofollow, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short output write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "import-runtime":
        parser = argparse.ArgumentParser(description="Import an existing runtime capture without running Codex")
        parser.set_defaults(command="import-runtime")
        parser.add_argument("--capture", required=True, type=Path)
        parser.add_argument("--codex-bin", required=True, type=Path)
        parser.add_argument("--identity", required=True, type=Path)
        parser.add_argument("--output", type=Path)
        return parser.parse_args(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(command="compare")
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--status-source", type=Path)
    parser.add_argument("--runtime", type=Path, help="Explicit current program evidence JSON")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(arguments)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "import-runtime":
            result = runtime_evidence_from_capture(
                _read_json(args.capture), codex_bin=args.codex_bin,
                identity=_read_json(args.identity),
            )
            exit_code = 0
        else:
            baseline = _read_json(args.baseline)
            current = _read_json(args.current)
            inventory = _read_json(args.inventory) if args.inventory else None
            status_source = _read_json(args.status_source) if args.status_source else None
            runtime = _read_json(args.runtime) if args.runtime else None
            if baseline is not None and not isinstance(baseline, dict):
                raise FreshnessInputError("baseline must be an object or null")
            if not isinstance(current, dict):
                raise FreshnessInputError("current must be an object")
            if inventory is not None and not isinstance(inventory, dict):
                raise FreshnessInputError("inventory must be an object")
            if status_source is not None and not isinstance(status_source, dict):
                raise FreshnessInputError("status source must be an object")
            result = build_freshness_envelope(
                baseline, current, inventory=inventory, status_source=status_source, runtime=runtime,
            )
            exit_code = 0 if result["freshness"]["state"] == "FRESH" else 3
        output = (
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if args.output is not None:
            _write_exclusive(args.output, output)
        sys.stdout.buffer.write(output)
    except (FreshnessInputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"freshness input rejected: {type(exc).__name__}\n")
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
