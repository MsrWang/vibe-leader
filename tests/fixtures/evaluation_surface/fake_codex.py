#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Deterministic fake Codex CLI and app-server used only by tests."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def emit(value: object) -> None:
    sys.stdout.write(canonical(value) + "\n")
    sys.stdout.flush()


def log_method(method: str) -> None:
    target = os.environ.get("FAKE_CODEX_LOG")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(method + "\n")


def log_request(message: dict[str, object]) -> None:
    target = os.environ.get("FAKE_CODEX_REQUEST_LOG")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(canonical(message) + "\n")


def feature_rows(enabled: bool) -> list[dict[str, object]]:
    output = []
    for line in (ROOT / "features-list.txt").read_text(encoding="utf-8").splitlines():
        name, stage, _ = line.split("\t")
        output.append(
            {
                "name": name,
                "stage": stage,
                "enabled": enabled if name in {"shell_tool", "unified_exec"} else False,
                "defaultEnabled": True,
            }
        )
    return output


def request_result(request_id: object, result: object) -> None:
    emit({"id": request_id, "result": result})


def command_item(
    command: str, status: str, cwd: str, output: str | None = None
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "item-command-1",
        "type": "commandExecution",
        "command": command,
        "commandActions": [],
        "cwd": cwd,
        "status": status,
    }
    if output is not None:
        item.update(
            {
                "aggregatedOutput": output,
                "exitCode": 0,
            }
        )
    return item


def emit_turn_events(
    command: str, scenario: str, thread_id: str, turn_id: str, cwd: str
) -> None:
    if scenario == "approval-request":
        emit(
            {
                "id": "server-approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {"threadId": thread_id, "turnId": turn_id},
            }
        )
        return
    if scenario == "malformed-json":
        sys.stdout.write("{malformed\n")
        sys.stdout.flush()
        return

    if scenario == "extra-command":
        command = "cat synthetic-unapproved.txt"
    behavior = scenario.startswith("behavior-")
    if scenario in {
        "behavior-pass",
        "behavior-exit-nonzero",
        "behavior-stderr",
        "behavior-post-completion",
        "behavior-forbidden-output",
        "behavior-multi-command",
        "behavior-oversize",
        "behavior-extra-item-field",
        "behavior-mutates-synthetic",
        "behavior-reused-id",
        "behavior-candidate-read",
    }:
        command = "pwd"
    if scenario == "behavior-first-not-pwd":
        command = "git status --short --branch"
    if scenario == "behavior-forbidden-target":
        command = "cat /synthetic-forbidden-target"

    event_thread_id = "thread-fake-other" if scenario == "wrong-event-thread" else thread_id
    event_turn_id = "turn-fake-other" if scenario == "wrong-event-turn" else turn_id
    started_item = command_item(command, "inProgress", cwd)
    if scenario == "started-wrong-status":
        started_item["status"] = "completed"

    emit(
        {
            "method": "item/started",
            "params": {
                "threadId": event_thread_id,
                "turnId": event_turn_id,
                "startedAtMs": 1,
                "item": started_item,
            },
        }
    )

    if behavior:
        output = cwd + "\n"
    else:
        output = (ROOT / "probe-pass.json").read_text(encoding="utf-8").strip()
    if scenario in {"probe-id-changed", "probe-errno-bool"}:
        probe_value = json.loads(output)
        if scenario == "probe-id-changed":
            probe_value["probe_id"] = "synthetic-other"
        else:
            probe_value["results"][1]["errno"] = True
        output = canonical(probe_value)
    if scenario in {"forbidden-output", "behavior-forbidden-output"}:
        output = "SYNTHETIC_FORBIDDEN_BODY_MARKER"
    if scenario == "behavior-oversize":
        output = "x" * 70000
    completed_item = command_item(command, "completed", cwd, output)
    if scenario == "completed-wrong-status":
        completed_item["status"] = "failed"
    if scenario == "nonzero-exit":
        completed_item["exitCode"] = 7
    if scenario == "changed-item-id":
        completed_item["id"] = "item-command-2"
    if scenario == "behavior-extra-item-field":
        completed_item["unapprovedField"] = "synthetic"
    emit(
        {
            "method": "item/completed",
            "params": {
                "threadId": event_thread_id,
                "turnId": event_turn_id,
                "completedAtMs": 2,
                "item": completed_item,
            },
        }
    )
    if scenario == "behavior-mutates-synthetic":
        (Path(cwd) / "unexpected-write.txt").write_text(
            "synthetic drift\n", encoding="utf-8"
        )

    if scenario in {"behavior-multi-command", "behavior-candidate-read"}:
        second_command = "git status --short --branch"
        if scenario == "behavior-candidate-read":
            eval_root = Path(cwd).parents[1]
            second_command = "cat " + str(
                eval_root / "candidate/vibe-project-lead-zh/SKILL.md"
            )
        second_started = command_item(second_command, "inProgress", cwd)
        second_started["id"] = "item-command-2"
        emit(
            {
                "method": "item/started",
                "params": {
                    "threadId": event_thread_id,
                    "turnId": event_turn_id,
                    "startedAtMs": 3,
                    "item": second_started,
                },
            }
        )
        second_completed = command_item(
            second_command, "completed", cwd, "## synthetic\n"
        )
        second_completed["id"] = "item-command-2"
        emit(
            {
                "method": "item/completed",
                "params": {
                    "threadId": event_thread_id,
                    "turnId": event_turn_id,
                    "completedAtMs": 4,
                    "item": second_completed,
                },
            }
        )

    if behavior:
        assistant_text = "已完成合成项目的只读检查，并在信息不足处停止。"
        if scenario == "behavior-oversize":
            assistant_text = "文" * 70000
        emit(
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "completedAtMs": 3,
                    "item": {
                        "id": "item-message-1",
                        "type": "agentMessage",
                        "status": "completed",
                        "text": assistant_text,
                    },
                },
            }
        )

    if scenario != "missing-usage":
        usage_event = {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": event_thread_id,
                "turnId": event_turn_id,
                "tokenUsage": {
                    "last": {
                        "totalTokens": 12,
                        "inputTokens": 8,
                        "cachedInputTokens": 2,
                        "outputTokens": 4,
                        "reasoningOutputTokens": 1,
                    },
                    "total": {
                        "totalTokens": 12,
                        "inputTokens": 8,
                        "cachedInputTokens": 2,
                        "outputTokens": 4,
                        "reasoningOutputTokens": 1,
                    },
                },
            },
        }
        emit(usage_event)
        if scenario == "duplicate-usage":
            emit(usage_event)
    emit(
        {
            "method": "turn/completed",
            "params": {
                "threadId": event_thread_id,
                "turn": {
                    "id": event_turn_id,
                    "status": "completed",
                    "items": [],
                },
            },
        }
    )
    if scenario == "behavior-post-completion":
        emit(
            {
                "method": "item/completed",
                "params": {
                    "threadId": event_thread_id,
                    "turnId": event_turn_id,
                    "completedAtMs": 99,
                    "item": {
                        "id": "item-after-completion",
                        "type": "agentMessage",
                        "status": "completed",
                        "text": "synthetic extra output",
                    },
                },
            }
        )


def run_app_server() -> int:
    scenario = os.environ.get("FAKE_CODEX_SCENARIO", "pass")
    instance = os.environ.get("FAKE_CODEX_INSTANCE")
    thread_id = f"thread-fake-{instance}" if instance is not None else "thread-fake-1"
    turn_id = f"turn-fake-{instance}" if instance is not None else "turn-fake-1"
    if scenario == "behavior-reused-id":
        thread_id = "thread-fake-reused"
        turn_id = "turn-fake-reused"
    active_profile = "eval-control"

    for raw_line in sys.stdin:
        if scenario == "malformed-request-input":
            return 9
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            return 10
        method = message.get("method")
        if not isinstance(method, str):
            return 11
        log_method(method)
        log_request(message)
        if "id" not in message:
            continue
        request_id = message["id"]
        if scenario == "wrong-id" and method == "initialize":
            request_result(999999, {})
            continue
        if scenario == "timeout" and method == "initialize":
            time.sleep(30)
            continue
        if scenario == "early-exit" and method == "initialize":
            return 12

        params = message.get("params") or {}
        if scenario == "initialize-contract-missing" and method == "initialize":
            request_result(request_id, {"platformFamily": "unix"})
            continue
        if scenario == "thread-contract-invalid" and method == "thread/start":
            request_result(request_id, {"thread": {"id": thread_id}, "sandbox": {}})
            continue
        if method == "initialize":
            request_result(
                request_id,
                {
                    "codexHome": "/synthetic/codex-home",
                    "platformFamily": "unix",
                    "platformOs": "linux",
                    "userAgent": "fake-codex/1.0.0",
                },
            )
        elif method == "experimentalFeature/list":
            rows = feature_rows(enabled=True)
            if scenario == "extra-feature":
                rows.append(
                    {
                        "name": "synthetic_unapproved_feature",
                        "stage": "experimental",
                        "enabled": True,
                        "defaultEnabled": False,
                    }
                )
            request_result(
                request_id,
                {"data": rows, "nextCursor": None},
            )
        elif method == "permissionProfile/list":
            profiles = [
                {"id": "eval-control", "allowed": True},
                {"id": "eval-candidate", "allowed": True},
            ]
            if scenario == "extra-profile":
                profiles.append({"id": "synthetic-unapproved", "allowed": True})
            request_result(
                request_id,
                {
                    "data": profiles,
                    "nextCursor": None,
                },
            )
        elif method == "thread/start":
            active_profile = params.get("permissions", "eval-control")
            model_provider = params.get("modelProvider", "openai")
            request_result(
                request_id,
                {
                    "thread": {
                        "id": thread_id,
                        "preview": "",
                        "ephemeral": True,
                        "modelProvider": model_provider,
                        "createdAt": 1,
                        "updatedAt": 1,
                        "status": {"type": "idle"},
                        "cwd": params.get("cwd"),
                        "cliVersion": "fake-codex 1.0.0",
                        "source": "appServer",
                        "sessionId": f"session-{thread_id}",
                        "turns": [],
                    },
                    "activePermissionProfile": {"id": active_profile},
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "cwd": params.get("cwd"),
                    "instructionSources": [],
                    "runtimeWorkspaceRoots": params.get("runtimeWorkspaceRoots", []),
                    "model": params.get("model", "gpt-test"),
                    "modelProvider": model_provider,
                    "sandbox": {
                        "type": "externalSandbox",
                        "networkAccess": "restricted",
                    },
                },
            )
        elif method == "mcpServerStatus/list":
            request_result(request_id, {"data": [], "nextCursor": None})
        elif method == "skills/list":
            request_result(
                request_id,
                {
                    "data": [
                        {
                            "cwd": params.get("cwds", [""])[0],
                            "skills": [],
                            "errors": [],
                        }
                    ]
                },
            )
        elif method == "thread/memoryMode/set":
            request_result(request_id, {})
        elif method == "turn/start":
            request_result(
                request_id,
                {
                    "turn": {
                        "id": turn_id,
                        "status": "inProgress",
                        "items": [],
                    }
                },
            )
            text_items = [
                item.get("text", "")
                for item in params.get("input", [])
                if item.get("type") == "text"
            ]
            prompt_lines = text_items[0].splitlines() if text_items else []
            command = prompt_lines[-1] if prompt_lines else "missing-probe-command"
            emit_turn_events(
                command,
                scenario,
                thread_id,
                turn_id,
                str(params.get("cwd", "")),
            )
            if scenario == "behavior-stderr":
                sys.stderr.write("synthetic stderr\n")
                sys.stderr.flush()
        elif method == "turn/interrupt":
            request_result(request_id, {})
        else:
            emit(
                {
                    "id": request_id,
                    "error": {"code": -32601, "message": "unknown fake method"},
                }
            )
    return 13 if scenario == "behavior-exit-nonzero" else 0


def generate_schema(arguments: list[str]) -> int:
    try:
        output = Path(arguments[arguments.index("--out") + 1])
    except (ValueError, IndexError):
        return 2
    if output.exists() or output.is_symlink():
        return 3
    shutil.copytree(ROOT / "schema", output, symlinks=False)
    return 0


def main(arguments: list[str]) -> int:
    if arguments == ["--version"]:
        print("fake-codex 1.0.0")
        return 0
    if arguments == ["features", "list"]:
        sys.stdout.write((ROOT / "features-list.txt").read_text(encoding="utf-8"))
        return 0
    if arguments[:2] == ["app-server", "generate-json-schema"]:
        return generate_schema(arguments[2:])
    if arguments and arguments[0] == "app-server":
        return run_app_server()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
