#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Summarize local Skill checks without changing an installation or querying Codex."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

# Keep imported helpers from creating bytecode, including when -B was omitted.
sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    print("只读自检需要 Python 3.11 或更高版本。", file=sys.stderr)
    raise SystemExit(2)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import install_skill as installer
from workbench.skill_inventory import ProtocolError, assess_skill_route


# File-content fingerprint of public 3.0.3, commit d0e17544584fb6c68254c49b87f4275fa19a71f4.
# This identifies payload contents, not release provenance or the installed package version.
V303_PAYLOAD = "d7fccfa6e0a2292d4c2ac6345221e6e3fe10554145b2b3a8d56e459448f1fa6b"


def payload_digest(entries: dict[str, Any]) -> str:
    files = {name: entry["sha256"] for name, entry in entries.items()
             if entry["type"] == "file"}
    material = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def item(status: str, detail: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **values}


def check_installation(skills_root: Path | None) -> tuple[dict, dict | None, Path | None]:
    try:
        if skills_root is None:
            skills_root = installer.validate_codex_home() / "skills"
        root = installer.validate_skills_root(skills_root)
        target = installer.validate_skill_target(root / installer.SKILL_NAME)
        manifest_path = installer.expected_manifest_path(target)
        try:
            target.lstat()
        except FileNotFoundError:
            try:
                manifest_path.parent.lstat()
            except FileNotFoundError:
                return item("NOT_FOUND", "所选目录没有安装；尚未检查其他宿主目录。"), None, target
            return item("UNKNOWN", "安装目标缺失，但安装状态目录仍在；需检查中断记录。",
                        reason="incomplete_installation"), None, target
        manifest, differences = installer.verify_internal(target, manifest_path)
        if differences:
            return item("DRIFT", "文件或属性与安装清单不一致。",
                        difference_count=len(differences)), None, target
        return item("VERIFIED", "文件与安装清单一致；这不表示宿主已加载。",
                    file_count=sum(e["type"] == "file" for e in manifest["entries"].values())), manifest, target
    except installer.InstallError as error:
        return item("UNKNOWN", "目录或安装清单无法可靠核验。", reason=error.reason), None, None
    except (OSError, ValueError, RuntimeError):
        return item("UNKNOWN", "无法读取目录或安装清单。", reason="local_read_failed"), None, None


def check_version(manifest: dict | None) -> dict:
    if manifest is None:
        return item("UNKNOWN", "先完成安装核验，再判断版本文件内容。")
    installed_digest = payload_digest(manifest["entries"])
    result = item(
        "KNOWN_CONTENT" if installed_digest == V303_PAYLOAD else "UNKNOWN_CONTENT",
        "文件内容匹配 3.0.3；清单未记录发行版本。" if installed_digest == V303_PAYLOAD
        else "未匹配内置版本指纹；不代表安装损坏。",
        payload_sha256=installed_digest,
        matches_3_0_3_payload=installed_digest == V303_PAYLOAD,
        release_provenance="NOT_CHECKED",
        source_comparison="UNKNOWN",
    )
    try:
        source = installer.strict_existing_directory(
            ROOT / "skill" / installer.SKILL_NAME, "unsafe_source_directory")
        entries = installer.scan_tree(source)
        installer.validate_runtime_layout(entries)
        differences = installer.compare_content_entries(entries, manifest["entries"])
        result["source_comparison"] = "DIFFERENT" if differences else "MATCH"
        result["source_difference_count"] = len(differences)
    except (installer.InstallError, OSError, ValueError, RuntimeError):
        result["source_comparison"] = "UNKNOWN"
    return result


def check_discovery(path: Path | None, project: Path | None, target: Path | None) -> dict:
    if path is None:
        return item("NOT_CHECKED", "未提供宿主发现记录；文件存在不能证明已加载。",
                    current_host_status="NOT_CHECKED")
    if target is None or project is None:
        return item("UNKNOWN", "需先确认安装目录和发现记录对应的项目。",
                    current_host_status="NOT_CHECKED")
    try:
        project = installer.strict_existing_directory(project, "unsafe_project_directory")
        path = Path(os.path.abspath(path))
        installer.strict_existing_directory(path.parent, "unsafe_discovery_directory")
        content, _, digest = installer.read_bounded_regular_file(path, "discovery_unreadable")
        response = json.loads(content.decode("utf-8"))
        assessment = assess_skill_route(
            response,
            expected_cwd=str(project),
            expected_locators={installer.SKILL_NAME: str(target / "SKILL.md")},
        )
        if assessment["status"] == "VERIFIED":
            return item("SNAPSHOT_MATCH", "提供的快照与所选 Skill 一致；当前宿主状态仍待确认。",
                        current_host_status="NOT_CHECKED", snapshot_sha256=digest)
        reasons = sorted({entry["reason"] for entry in assessment["blocked"]})
        return item("UNKNOWN" if assessment["status"] == "UNKNOWN" else "SNAPSHOT_BLOCKED",
                    "提供的发现记录存在缺口或阻断；请在原宿主核对。",
                    current_host_status="NOT_CHECKED", reasons=reasons,
                    unattributed_error_count=assessment["unattributed_error_count"],
                    snapshot_sha256=digest)
    except (installer.InstallError, ProtocolError, OSError, ValueError, RuntimeError):
        return item("UNKNOWN", "发现记录无法解析或与项目对应；请使用原始 skills/list 响应。",
                    current_host_status="NOT_CHECKED", reason="discovery_unverified")


def build_report(skills_root: Path | None = None, discovery: Path | None = None,
                 project: Path | None = None) -> tuple[dict, int]:
    report = {
        "schema_version": 1,
        "kind": "vibe-leader-local-check",
        "checked_at_utc": installer.utc_now(),
        "status": "NEEDS_ATTENTION",
        "environment": item("SUPPORTED" if sys.platform == "linux" else "UNVERIFIED",
                            "此入口面向 Linux/WSL；宿主安装根需由使用者确认。"),
        "installation": item("NOT_CHECKED", "尚未核验安装。"),
        "version": item("UNKNOWN", "尚未核对版本文件内容。"),
        "discovery": item("NOT_CHECKED", "尚未核对宿主发现记录。",
                          current_host_status="NOT_CHECKED"),
        "invocation": item("NOT_CHECKED", "需在当前宿主显式调用并观察；本工具不启动模型。"),
        "next_action": "在受支持的 Linux/WSL 环境中使用本入口。",
    }
    if sys.platform != "linux":
        return report, 2
    if (discovery is None) != (project is None):
        report["next_action"] = "同时提供 --discovery 和 --project，或同时省略。"
        return report, 2
    installation, manifest, target = check_installation(skills_root)
    report["installation"] = installation
    report["version"] = check_version(manifest)
    report["discovery"] = check_discovery(discovery, project, target)
    if installation["status"] == "DRIFT":
        report["next_action"] = "保留当前安装；按自检说明核对变动原因和恢复记录。"
        return report, 3
    if installation["status"] != "VERIFIED":
        report["next_action"] = "按安装指南确认当前宿主的真实 CODEX_HOME、安装目录和清单。"
        return report, 2
    if report["version"]["source_comparison"] == "UNKNOWN":
        report["next_action"] = "从可信完整源码运行本入口，核对源码目录可读且结构完整。"
        return report, 2
    if report["discovery"]["status"] in {"UNKNOWN", "SNAPSHOT_BLOCKED"}:
        report["next_action"] = "在生成记录的宿主检查同名冲突、启用状态、路径或加载错误。"
        return report, 3 if report["discovery"]["status"] == "SNAPSHOT_BLOCKED" else 2
    report["status"] = "LOCAL_CHECKS_COMPLETE"
    report["next_action"] = (
        "先确认所用源码与安装是否有意属于不同版本，再决定是否需要升级。"
        if report["version"]["source_comparison"] == "DIFFERENT"
        else "在当前项目选择该 Skill 并进行一次只读调用，观察实际加载和回复。"
    )
    return report, 0


def render_text(report: dict) -> str:
    lines = ["Vibe Leader 只读自检"]
    for key, label in (("environment", "环境"), ("installation", "安装"),
                       ("version", "版本内容"), ("discovery", "宿主发现"),
                       ("invocation", "实际调用")):
        section = report[key]
        lines.append(f"{label}：[{section['status']}] {section['detail']}")
        if section.get("reason"):
            lines.append(f"  原因：{section['reason']}")
        if section.get("reasons"):
            lines.append(f"  原因：{', '.join(section['reasons'])}")
    comparison = report["version"].get("source_comparison")
    if comparison:
        lines.append(f"与本次源码比较：{comparison}")
    lines.extend([f"下一步：{report['next_action']}",
                  "本地核验完成不等于 App 加载或用户验收通过；本工具不自动保存或上传报告。"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vibe Leader 只读自检（Linux/WSL，完整源码入口）")
    parser.add_argument("--skills-root", type=Path,
                        help="已确认的绝对安装根；必须与 CODEX_HOME/skills 一致")
    parser.add_argument("--discovery", type=Path, help="已有 skills/list 原始 JSON 响应（最多 1 MiB）")
    parser.add_argument("--project", type=Path, help="该发现记录对应的绝对项目目录")
    parser.add_argument("--json", action="store_true", help="输出不含原始日志和本机路径的 JSON 摘要")
    args = parser.parse_args(argv)
    if (args.discovery is None) != (args.project is None):
        parser.error("--discovery 与 --project 必须同时提供")
    report, exit_code = build_report(args.skills_root, args.discovery, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
