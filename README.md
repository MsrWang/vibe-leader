# Vibe Leader

**在 Codex 项目中使用的中文研发主管 Skill。** 帮你把需求拆成有范围的计划，推进实现与验证，最后检查实际交付。

使用方式：在自己的 Codex 项目任务中显式调用 `$vibe-project-lead-zh`，或选择“中文跨项目研发主管”。本仓库提供 Skill、安装工具和使用文档；HF 提供合成使用示例，实际项目操作在 Codex 中完成。

[快速开始](#快速开始) · [使用示例](#使用示例) · [安装与恢复](docs/getting-started.md) · [已知限制](docs/limitations.md) · [贡献](CONTRIBUTING.md)

## 快速开始

### 1. 准备环境

- 一个能够发现并显式调用此 Skill 的 Codex 宿主。
- 使用源码安装器时需要 Python 3.11+；处理 Git 项目时需要 Git。
- **已验证环境：Windows Codex Desktop 配合 Ubuntu/WSL。** 其他宿主、版本与安装目录需单独核对；当前未承诺 Claude Code、Cursor 等兼容。

### 2. 获取并安装

下载本仓库 Releases 中的完整源码 ZIP，保留 LICENSE、第三方通知和旁置许可文件；解压后在源码根查看安装入口：

```bash
python3 -B scripts/install_skill.py install --help
```

本版安装器使用已核对的 `CODEX_HOME/skills`。先按[安装指南第 1–2 节](docs/getting-started.md#1-准备与兼容检查)确认当前宿主目录，再执行其中的安装与核验步骤。已经存在同名安装时走升级流程，不覆盖重装。文件核验成功后，还要确认 Codex 的 Skills 中能发现并启用“中文跨项目研发主管”。

### 3. 在你的项目里调用

在要处理的项目任务中选择该 Skill，并发送：

```text
使用 $vibe-project-lead-zh。先绑定当前项目。
目标：做一个仅本地使用的待办清单。
先只读调查，给我目标、非目标、验收标准和最小计划，暂不修改。
```

预期先得到项目核对、最小计划和检查方法。确认具体范围后，再让 Codex 完成那一批本地工作。最终需要打开实际产物并检查，测试通过不自动代表用户已接受。

本 Skill 采用显式调用；仅提出普通编程需求不会自动触发它。停用、暂停、恢复和升级方法见[入门指南](docs/getting-started.md)。

## 使用示例

以下内容均为编写的合成示例，不是实际会话记录或实时模型输出。

| 在 Codex 中提出的请求 | 主管应怎样处理 |
|---|---|
| “做一个本地待办，先只读调查。” | 核对项目，列目标、非目标、验收方法和最小计划。 |
| 当前在 demo-notes，却要求“直接改 demo-calendar”。 | 停止当前项目的写入，说明需要在新项目任务重新绑定。 |
| “测试通过了，就当我已经验收。” | 报告技术结果，给出实际检查步骤，等待真实用户接受。 |

完整文字步骤见[三个合成示例](docs/getting-started.md#4-三个合成示例)。[HF 演示源码及说明](demo/README.md)提供案例选择与提示词复制；浏览演示不需要安装，执行项目任务需要自己的 Codex 环境。

## 能做什么

- **核对项目**：确认目录、Git/worktree 身份和当前状态，发现项目不符或证据不足时停止。
- **控制范围**：明确目标、非目标、计划、检查点以及需要确认的操作。
- **推进交付**：连续完成已授权的工作，分开技术验证、实际使用与用户接受。
- **选择能力**：结合稳定导航和当前发现使用需要的 Skill；基础流程不依赖维护者的私人插件库存。
- **协作与恢复**：在适用时组织有界委派、交接与恢复；多项目和部署评估各有边界。

## 源码结构

```text
skill/vibe-project-lead-zh/   可安装的 Skill，11 个文件
scripts/                    安装、核验、升级与恢复入口
workbench/                  显式运行的治理校验工具
tests/                      确定性测试与合成案例
docs/                       入门、限制与版本验证说明
demo/                       HF 静态使用示例
```

普通使用先完成 Skill 安装和显式调用。`workbench/` 是高级源码工具，不会因 Skill 被安装就自动执行，也不要求首次使用者接通评测系统。

## 验证与限制

2.3 已完成独立源码交付及限定环境的用户接受；真实安装、代表任务、暂停/只读恢复、两项目绑定、停用/启用及旧版恢复有各自范围证据。详细结论和保留警告见[2.3 验证说明](docs/release-2.3.md)。

Skill 是指令与参考，**不是 OS 沙箱**。模型可能偏离流程；实际权限取决于 Codex 宿主配置。上下文可能发送给配置的模型服务，运行在本地不等于离线。旧 Git、已有子模块、可选工程接口和非原子安装等限制见[限制与数据流](docs/limitations.md)。

源码确定性自检示例：

```bash
python3 -B -m unittest -q tests.test_behavior_scenarios tests.test_skill_route tests.test_acceptance_contract
```

运行完整测试前先阅读[测试边界](docs/limitations.md#测试与证据边界)，不要使用真实安装或业务项目作为测试夹具。

## 帮助、贡献与许可

普通问题使用本仓库 Issues；代码和文档改进见[贡献指南](CONTRIBUTING.md)。安全问题按[安全报告说明](SECURITY.md)使用私密渠道，避免公开敏感材料。

维护者：Jason Wong（GitHub：MsrWang）。个人维护，不承诺固定响应时间或兼容所有环境。

项目自有文件采用 [MPL-2.0](LICENSE)，固定 Codex 协议参考保留 Apache-2.0 及通知；详见[第三方与许可范围](THIRD_PARTY_NOTICES.md)。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
