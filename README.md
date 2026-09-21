# Vibe Leader

> **3.0.1 维护更新：连续推进到有意义检查点**
> 本版本在 3.0.0 的主循环中补充一条规则：条件稳定时，同一检查点内的相关本地步骤应连续推进，中间用简短进度说明保持用户知情。日常用法同时明确，未来新任务的加载观察不是暂停当前项目的前置条件。
> 规则候选、静态合同、完整回归、安全检查和限定环境的实际安装已有对应证据；新任务是否加载新版及长期行为仍需自然观察。[查看 3.0.1 变化与限制](docs/release-3.0.1.md)。

> **3.0.0 有限范围**：加入继续项目、质量检查、Skill 维护及变更影响的[日常使用入口](docs/project-workflow.md)。范围与历史限制见[3.0.0 说明](docs/release-3.0.md)。

**让 Codex 帮你把想法变成计划，按计划推进，最后检查做出来的东西是否能用。**

Vibe Leader 是一套给 Codex 使用的中文项目推进方法，以 **Skill（可加载的工作说明）** 的形式提供。适合已经在用 Codex 做项目，希望把“做什么、做到哪了、接下来做什么”说清楚的人。

比如，你想做一个待办清单：它会先核对当前项目，说明这次准备做哪些功能；经你确认后推进工作，最后给出可以实际检查的结果。

**这页先帮你了解用途和使用方式。** 负责安装、升级或维护的人，请看[安装与使用指南（技术向）](docs/getting-started.md)。

[先看在线示例](https://huggingface.co/spaces/MsrWang0112/vibe-leader) · [快速开始](#快速开始) · [查看版本下载页](https://github.com/MsrWang/vibe-leader/releases) · [3.0 范围与限制](docs/release-3.0.md) · [安装与使用指南](docs/getting-started.md)

## 快速开始

### 1. 先看看怎么用

打开 [HF 在线示例](https://huggingface.co/spaces/MsrWang0112/vibe-leader)，选择一个场景，看看可以怎么提需求、Codex 应怎样处理。无需安装即可浏览；页面展示预先编写的示例，实际任务仍要在你自己的 Codex 中完成。

### 2. 获取并安装

从[版本下载页](https://github.com/MsrWang/vibe-leader/releases)选择所需版本的完整源码 ZIP，保留包内文件，按[安装指南第 1–2 节](docs/getting-started.md#1-准备与兼容检查)完成安装。3.0.1 修改了 Skill 主循环；从 3.0.0 或更早版本获得这项行为需要按受控升级流程更新实际安装。首次安装或升级后，在 Codex 的 Skills 中确认“中文跨项目研发主管”可见且已启用。

**已验证环境：Windows Codex Desktop 配合 Ubuntu/WSL。** 安装所需的 Python、Git、目录确认，以及已有版本的升级步骤，都在技术指南中说明；其他环境需先核对兼容性。

### 3. 在你的项目里调用

在要处理的项目任务中选择该 Skill，并发送：

```text
使用 $vibe-project-lead-zh。先确认当前打开的是我要处理的项目。
我想做一个仅在本地使用的待办清单。
请先查看现有内容，告诉我准备做什么、这次不做什么、完成后怎么检查，暂不修改文件。
```

它应先解释准备怎么做。你确认范围后，再让 Codex 继续；完成时亲自打开产物试用，确认结果符合需要。

每次要使用这套方法时，需要选择该 Skill，或在消息中写出 `$vibe-project-lead-zh`。暂停、恢复和升级方法见[技术指南](docs/getting-started.md)。

## 使用示例

下面用三个预先编写的例子说明用法；它们不是在线运行结果，实际处理需要结合你的项目。

| 你提出的请求 | 预期会怎么做 |
|---|---|
| “做一个本地待办，先看看现有内容。” | 先说明准备做什么、这次不做什么、完成后怎么检查。 |
| 打开的是 A 项目，却要求“直接去改 B 项目”。 | 暂停修改，提醒你在正确的项目里重新开始任务。 |
| “测试通过了，就当我已经验收。” | 给出可试用的结果和检查步骤，等你实际确认。 |

可以直接在 [HF 在线示例](https://huggingface.co/spaces/MsrWang0112/vibe-leader)切换案例、复制提示词；详细操作说明见[技术指南中的三个示例](docs/getting-started.md#4-三个合成示例)。

## 能做什么

- **先确认做的是哪个项目**：核对当前内容和进度，发现项目不符时停止。
- **说清这次做什么**：把目标拆成可检查的步骤，说明哪些事情暂时不做。
- **持续推进已确认的工作**：完成后报告结果和未解决的问题，需要你决定时说明原因。
- **帮助接续进度**：暂停或恢复时，核对已完成的内容和剩余事项。

## 源码结构

<details>
<summary>开发者可展开查看目录与工具分工</summary>

```text
skill/vibe-project-lead-zh/   可安装的 Skill，11 个文件
scripts/                    安装、核验、升级与恢复入口
workbench/                  显式运行的治理校验工具
tests/                      确定性测试与合成案例
docs/                       入门、限制与版本验证说明
demo/                       HF 静态使用示例
```

普通使用先完成 Skill 安装和显式调用。`workbench/` 是高级源码工具，不会因 Skill 被安装就自动执行，也不要求首次使用者接通评测系统。

</details>

## 验证与限制

2.3 已完成独立源码交付及限定环境的用户接受；真实安装、代表任务、暂停/只读恢复、两项目绑定、停用/启用及旧版恢复有各自范围证据。详细结论和保留警告见[2.3 验证说明](docs/release-2.3.md)。3.0.0 新增用法的范围见[3.0.0 说明](docs/release-3.0.md)；连续推进规则的证据与未覆盖场景见[3.0.1 说明](docs/release-3.0.1.md)。

这套方法提供工作规则，**不能隔离文件或代替权限设置**，模型仍可能偏离规则。Codex 使用的模型服务可能接收项目上下文；在本地操作不等于离线。具体兼容性、安装与数据边界见[已知限制](docs/limitations.md)。

<details>
<summary>开发者可展开查看源码自检命令</summary>

```bash
python3 -B -m unittest -q tests.test_behavior_scenarios tests.test_skill_route tests.test_acceptance_contract
```

运行完整测试前先阅读[测试边界](docs/limitations.md#测试与证据边界)，不要使用真实安装或业务项目作为测试夹具。

</details>

## 帮助、贡献与许可

普通问题使用本仓库 Issues；代码和文档改进见[贡献指南](CONTRIBUTING.md)。安全问题按[安全报告说明](SECURITY.md)使用私密渠道，避免公开敏感材料。

维护者：Jason Wong（GitHub：MsrWang）。个人维护，不承诺固定响应时间或兼容所有环境。

项目自有文件采用 [MPL-2.0](LICENSE)，固定 Codex 协议参考保留 Apache-2.0 及通知；详见[第三方与许可范围](THIRD_PARTY_NOTICES.md)。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
