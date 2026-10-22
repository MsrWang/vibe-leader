# Vibe Leader

**让 Codex 帮你把想法变成计划，按计划推进，最后检查做出来的东西是否能用。**

Vibe Leader 是一套给 Codex 使用的中文项目推进方法，以 **Skill（可加载的工作说明）** 的形式提供。适合已经在用 Codex 做项目，希望把“做什么、做到哪了、接下来做什么”说清楚的人。

| 你现在想做什么 | 直接从这里开始 |
|---|---|
| 第一次使用 | [快速开始](#快速开始)；也可以先看 [在线示例](https://huggingface.co/spaces/MsrWang0112/vibe-leader) |
| 已经装好，继续项目 | [复制接续请求](docs/project-workflow.md#常用入口) |
| 不确定装了什么、为什么没生效 | [只读自检](docs/self-check.md)（需包含新脚本的完整源码） |
| 想反馈问题或使用感受 | [反馈方式](CONTRIBUTING.md#报告问题) |

稳定版使用说明以 3.0.3 为基础。Mac 的 3.1 候选及其状态见[发行页](https://github.com/MsrWang/vibe-leader/releases)；各环境的实际加载与使用需要分别确认。

## 快速开始

### 1. 安装并确认可见

从[版本下载页](https://github.com/MsrWang/vibe-leader/releases)选择所需版本的完整源码 ZIP，保留包内文件，按[安装指南第 1–2 节](docs/getting-started.md#1-准备与兼容检查)完成安装。3.0.3 增加可选的本地证据筛选器；3.0.2 或更早安装需要按受控升级流程更新实际安装才能获得这两个新文件。首次安装或升级后，在 Codex 的 Skills 中确认“中文跨项目研发主管”可见且已启用。

**已验证环境：Windows Codex Desktop 配合 Ubuntu/WSL。** 安装所需的 Python、Git、目录确认，以及已有版本的升级步骤，都在技术指南中说明；其他环境需先核对兼容性。

### 2. 在你的项目里调用

在要处理的项目任务中选择该 Skill，并发送：

```text
使用 $vibe-project-lead-zh。先确认当前打开的是我要处理的项目。
我想做一个仅在本地使用的待办清单。
请先查看现有内容，告诉我准备做什么、这次不做什么、完成后怎么检查，暂不修改文件。
```

它应先解释准备怎么做。你确认范围后，再让 Codex 继续；完成时亲自打开产物试用，确认结果符合需要。

以后继续工作可直接使用[接续请求](docs/project-workflow.md#常用入口)。每次使用需要选择该 Skill，或写出 `$vibe-project-lead-zh`。找不到或不确定是否生效时，看[只读自检](docs/self-check.md)。

## 使用示例

下面用四个预先编写的例子说明用法；它们不是在线运行结果，实际处理需要结合你的项目。

| 你提出的请求 | 预期会怎么做 |
|---|---|
| “做一个本地待办，先看看现有内容。” | 先说明准备做什么、这次不做什么、完成后怎么检查。 |
| 打开的是 A 项目，却要求“直接去改 B 项目”。 | 暂停修改，提醒你在正确的项目里重新开始任务。 |
| “测试通过了，就当我已经验收。” | 给出可试用的结果和检查步骤，等你实际确认。 |
| “我感觉当前推进有偏差，但说不清技术原因。” | 对齐目标、行动和证据，给出三行最小纠偏；仍在已有授权内时继续。 |

可以在 [HF 在线示例](https://huggingface.co/spaces/MsrWang0112/vibe-leader)切换案例、复制提示词；页面是静态示例，不运行模型或本地筛选器。感觉方向不对时，可直接使用[独立纠偏卡](docs/deviation-correction.md)，不需要先说清技术原因。

## 能做什么

- **先确认做的是哪个项目**：核对当前内容和进度，发现项目不符时停止。
- **说清这次做什么**：把目标拆成可检查的步骤，说明哪些事情暂时不做。
- **持续推进已确认的工作**：完成后报告结果和未解决的问题，需要你决定时说明原因。
- **帮助接续进度**：暂停或恢复时，核对已完成的内容和剩余事项。
- **发现跑偏后最小修正**：保留有效成果，停止扩大偏差，在已有授权内继续主线。

## 源码结构

<details>
<summary>开发者可展开查看目录与工具分工</summary>

```text
skill/vibe-project-lead-zh/   可安装的 Skill，13 个文件；含可选本地证据筛选器
scripts/                    只读自检、安装、核验、升级与恢复入口
workbench/                  显式运行的治理校验工具
tests/                      确定性测试与合成案例
docs/                       入门、限制与版本验证说明
demo/                       HF 静态使用示例
```

普通使用先完成 Skill 安装和显式调用。`workbench/` 是高级源码工具，不会因 Skill 被安装就自动执行，也不要求首次使用者接通评测系统。
本地证据筛选器随 Skill 一起安装，但只在大文件、多文件或长日志调查中被显式调用；它只做本机相关性排序，没有 Jev API 或后台服务。

</details>

## 验证与限制

已有安装与使用证据覆盖 Windows Codex Desktop 配合 Ubuntu/WSL 的限定环境。源码候选、文件安装、宿主加载和用户实际使用分别计证据，不能相互替代。新增自检入口已通过 WSL2 上的 21 项功能测试，具体范围见[自检说明](docs/self-check.md#验证范围)。

这套方法提供工作规则，**不能隔离文件或代替权限设置**，模型仍可能偏离规则。Codex 使用的模型服务可能接收项目上下文；在本地操作不等于离线。具体兼容性、安装与数据边界见[已知限制](docs/limitations.md)。

<details>
<summary>开发者可展开查看源码自检命令</summary>

```bash
python3 -B -m unittest -q tests.test_behavior_scenarios tests.test_skill_route tests.test_acceptance_contract
```

运行完整测试前先阅读[测试边界](docs/limitations.md#测试与证据边界)，不要使用真实安装或业务项目作为测试夹具。

</details>

## 版本变化

| 版本 | 主要变化与证据范围 |
|---|---|
| [3.0.3](docs/release-3.0.3.md) | 大文件、多文件和长日志的可选本地证据筛选，Skill 共 13 个文件。 |
| [3.0.2](docs/release-3.0.2.md) | 感觉跑偏时进行轻量纠偏，并在已有授权内继续。 |
| [3.0.1](docs/release-3.0.1.md) | 稳定条件下连续推进到有意义检查点。 |
| [3.0.0](docs/release-3.0.md) | 继续项目、质量检查、Skill 维护及变更影响的日常入口。 |
| [2.3](docs/release-2.3.md) | 首发安装、代表任务、暂停恢复及限定环境验收。 |

完整记录见 [CHANGELOG](CHANGELOG.md)，发行状态以 [GitHub Releases](https://github.com/MsrWang/vibe-leader/releases) 为准。

## 帮助、贡献与许可

普通问题或使用感受可从[反馈模板](CONTRIBUTING.md#报告问题)开始；代码和文档改进也见贡献指南。安全问题按[安全报告说明](SECURITY.md)使用私密渠道，避免公开敏感材料。

维护者：Jason Wong（GitHub：MsrWang）。个人维护，不承诺固定响应时间或兼容所有环境。

项目自有文件采用 [MPL-2.0](LICENSE)，固定 Codex 协议参考保留 Apache-2.0 及通知；详见[第三方与许可范围](THIRD_PARTY_NOTICES.md)。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
