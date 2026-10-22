# Self-use Improvements Implementation Plan

> Execute inline with executing-plans; the user has selected the scope. Track the three deliverables below.

**Goal:** 让已有用户更快接续项目、定位安装与加载问题、留下可用反馈。

**Architecture:** 在完整源码中增加只读汇总脚本，复用既有安装核验和路由核验函数。文档提供直接入口，反馈模板收集最少必要信息。

**Tech Stack:** Python 3.11+ 标准库、Markdown、GitHub Markdown Issue templates。

## Global Constraints

- 基线：公开稳定版 3.0.3，提交 `d0e17544584fb6c68254c49b87f4275fa19a71f4`。
- 自检仅支持 Linux/WSL；不修改 13 文件 Skill、安装器或 Mac 候选。
- 不联网、不调用模型、不启动宿主、不写配置或安装目录。
- 现有安装清单不含发行版本；内容匹配、来源、宿主发现、实际调用分别报告。
- 初次实现只进行静态检查；后续已追加功能测试、全量默认回归与通过后的 GitHub 源码更新。

### Task 1: 只读自检

**Files:** Create `scripts/doctor.py`, `docs/self-check.md`.

**Interfaces:** `build_report(skills_root: Path | None, discovery: Path | None, project: Path | None) -> tuple[dict, int]`；`main(argv=None) -> int`。

- [x] 使用 argparse 提供 `--skills-root`、成对的 `--discovery/--project`、`--json`。
- [x] 复用 `validate_skills_root`、`verify_internal`、`scan_tree`、`compare_content_entries`；安装不完整时返回已知状态。
- [x] 对完整核验后的清单 entries 计算文件摘要映射指纹；与固定 3.0.3 指纹和当前源码分别比较。
- [x] 可选发现记录用 `read_bounded_regular_file` 和 `assess_skill_route` 核对，限制为固定 Skill locator；只呈现状态，不回显原始输入。
- [x] 根据首个需处理问题给一个下一步；调用状态始终未检查。
- [x] 写清命令、输入与输出含义、退出码和手动观察。

### Task 2: 精简入口

**Files:** Modify `README.md`, `docs/getting-started.md`, `docs/project-workflow.md`, `docs/limitations.md`, `CHANGELOG.md`.

- [x] 首页先放用途、开始、继续、自检入口，将历史更新移到后部。
- [x] 安装指南和日常用法链接自检说明；补一条可复制的简短接续请求。
- [x] 限制与未发布变更记录说明新增源码工具及未运行的验证范围。

### Task 3: 反馈模板与收尾

**Files:** Create `.github/ISSUE_TEMPLATE/usage-problem.md`, `.github/ISSUE_TEMPLATE/experience-feedback.md`; modify `CONTRIBUTING.md`.

- [x] 增加两份简短模板，提供“未知/未检查”选项，摘要可选。
- [x] 链接模板并提示最小脱敏片段与私密安全报告入口。
- [x] 用 `ast.parse` 检查 Python 语法，查看 diff 与文档相对链接，运行 `git diff --check`。
- [x] 交付源码位置与使用命令，报告实际完成范围和未实测限制。

## 完成记录

初次实现完成时，Python 语法、65 处相对文档链接、差异格式及新文件许可标识的静态检查未发现问题；独立只读审查未提出需修复项。当时未运行测试。

### 追加功能验证与 GitHub 更新

- [x] 新增 `tests/test_doctor.py`，21 项功能测试全部通过；测试仅使用临时安装。
- [x] 补回首页精简时遗漏的“纠偏不需要先说清技术原因”说明，保留原回归断言。
- [x] 同步文档中的实测范围；独立审查未发现需修复项。
- [x] 全量默认回归：`Ran 1070 tests; OK (skipped=6)`。跳过为现有跨文件系统与 Windows 互操作环境项。
- [x] 准备公开仓库 `MsrWang/vibe-leader` 的 `main` 更新：核对远端基线与待提交文件，检查隐私模式和链接。推送完成状态以 GitHub 提交记录及平台回读为准。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
