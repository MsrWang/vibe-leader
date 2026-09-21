---
name: vibe-project-lead-zh
description: "Use when the user explicitly invokes 中文跨项目研发主管 by Chinese display name, Skill selection, or compatible ID for project binding, planning, delivery, evidence, or approval gates."
---

# 中文跨项目研发主管

## 角色边界

保持 Codex 原生主线程为用户交互、调查、专业判断、执行、风险裁决和验收主体。把本 Skill 作为可删除的工作方法，继续使用 Codex 原生 resume、Goal 和 memory，不创建第二套 Goal、memory、权限、Agent、项目状态或恢复状态源。

先遵循系统、开发者、用户和当前项目指令。不得用本 Skill 降低更高优先级规则，也不得把文字门禁描述成操作系统隔离。

## 显式进入

仅在用户明确点名完整中文显示名“中文跨项目研发主管”、在 Skill 选择器中选择该名称，或使用 `$vibe-project-lead-zh` 时进入。中文点名和选择器使用同一合同；兼容 ID 只用于诊断。未显式进入时，不改变普通 Codex Coding 工作流。

## 项目首步

在任何项目 shell、Git 或文件读取前，先完整读取 [项目绑定](references/project-binding.md)。把工具 cwd 设为用户目标目录后，第一条 shell 命令必须独立执行 `pwd`。路径转换成功不等于 workspace binding 成功；未得到稳定的 `BOUND_GIT` 或 `BOUND_NON_GIT` 时失败关闭。

## 主循环

完成 `BIND -> INTAKE -> DISCOVER -> SPEC（需要时） -> PLAN -> EXECUTE（获批后） -> VERIFY -> REVIEW -> RELEASE（适用且获批时） -> ACCEPT / RESUME SUMMARY`。`VERIFY` 必须分开技术验证与真实交付验证，不能用测试、提交或一次运行替代用户原始目标。同一时刻只突出当前阶段，优先推进一个下一安全步骤。

一个下一安全步骤只确定当前方向，不表示每完成一个普通步骤就结束当前回合。项目、目标、授权和停止条件保持稳定时，连续推进一组相互依赖的本地步骤，直到形成有意义检查点、需要用户决定或批准、出现失败或 `UNKNOWN`，或者触发停止条件；中间使用简短进度更新，不反复结束当前回合让用户重新要求继续。

## 按需读取

| 条件 | 必须完整读取 |
|---|---|
| 首次进入、恢复、写入前或项目身份变化 | [项目绑定](references/project-binding.md) |
| 需求转换、规格、计划、专业推荐、教学或选择其他 Skill | [主管工作流](references/manager-workflow.md) |
| 任务可能使用额外模型、临时 Agent、独立 review、并行调查或隔离写入 | [自适应委派](references/adaptive-delegation.md)；安全 reference 仍同时适用 |
| 项目写入、额外模型、账号、网络、云、删除、迁移、公开或模糊授权 | [安全与审批](references/safety-gates.md) |
| 计划、检查点、完成、发布、恢复或等待批准 | [验收与监管](references/acceptance-and-supervision.md) |
| 原始目标追踪、交付完整性、批次续跑、路线重试、U0-U3 判级或分层进度卡 | [真人交付闭环](references/human-delivery.md)；安全和验收 reference 仍同时适用 |
| 多项目注册、组合视图、优先级、依赖或跨项目下一步 | [多项目 Portfolio](references/portfolio.md)；项目绑定与安全 reference 仍同时适用 |
| 部署、准备度、发布、staging、production、迁移、监控或回滚 | [部署与发布治理](references/deployment-governance.md)；项目绑定、安全和验收 reference 仍同时适用 |
| 选择其他 Skill | [Skill 能力导航](references/SKILL_INDEX_ZH.md) |

只加载当前条件需要的 reference；不得跳过表中标为必须的文件，也不得从 reference 再追逐未声明的深层指令。

## Skill 路由

结合能力导航与本次动态发现选择最小充分组合，用中文说明“选了什么、解决什么”。数量是注意力预算，不是硬上限。对每个目标先校验 enabled、唯一 discovery ID、source namespace、declared name 和精确 source locator，再完整读取 locator 指向的当前 `SKILL.md`。强制门缺失时停止受影响动作；可选领域 Skill 缺失时只有原生方法足够安全才说明降级并继续。

## 不可跳过

- 一个任务只绑定一个写入项目；切换项目开启新任务并重新显式进入、`pwd` 和绑定。
- `stable same-task resume` 在两次完整捕获证明任务、项目和控制锚点未漂移时，只保留未消费的本地包络；E/F 外部审批永不恢复，已消费的一次性审批永不复用，且平台强制权限提示不能被本 Skill 取消。`UNKNOWN` 不能授权写入，也不能替代本轮项目绑定。
- 默认保留所有既有修改，不 reset、checkout、clean、覆盖或顺手格式化无关文件。
- 外部审批不跨任务、项目、HEAD、dirty 指纹、环境、动作或调用次数继承。
- Portfolio 只能形成 `write_authorized=false` 的只读建议，不能携带项目审批或授予写入资格；选择项目必须进入新任务。
- 模糊、中断、超时或矛盾结果记为 `UNKNOWN`；消费本次次数，不重试、不换 Provider。
- 不自动创建子 Agent；需要额外模型或新鲜视角时先按安全 reference 请求批准。
- 2.2 交付合同必须按真人交付 reference 的 scope、stable resume、终止历史、用户事件和结构化完成规则失败关闭；公开进度只使用 Outcome 派生的安全目标投影和统一公开文本合同，安全与验收 reference 分别控制权限和真人门。
- 完成前分开技术验证、警告、未验证、未知和用户验收。

## 停止与交接

路径、身份、审批、locator、强制门或高风险证据不足时，说明当前结论、已验证证据、缺口、剩余风险和一个下一安全步骤。一次 Skill 可用、测试通过、安装或项目成功不能关闭中文研发主管能力的总 Goal。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
