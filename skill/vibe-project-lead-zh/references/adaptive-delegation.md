# 自适应委派

本 reference 只增加中文决策和验收规则，不创建第二套运行时、Goal、审批、项目状态或
memory。Codex 原生主线程始终负责用户交互、项目绑定、批准消费、风险裁决、结果消费、
技术验收和用户验收。

工程子任务使用本 reference 的完整 brief/result 合同。纯文本样本只用于经相应 D 级批准的行为观察，由主线程核对材料、实际工具活动、原始回答和逐项结果；不伪造工程身份、测试或写入资格，也不放宽工程校验器来接纳文本实验。宿主强制规则仍适用；准备要求互相冲突时先停止受影响采样，不把未读取材料的停止归因于被测规则。

## 目录

- [何时保持 SOLO](#何时保持-solo)
- [何时成为委派候选](#何时成为委派候选)
- [原生能力发现与 SOLO_FALLBACK](#原生能力发现与-solo_fallback)
- [中文 brief V2](#中文-brief-v2)
- [结果 result V2](#结果-result-v2)
- [预算与一个 D 级批次](#预算与一个-d-级批次)
- [只读、Review 与隔离写入](#只读review-与隔离写入)
- [source、return 与主线程 freshness](#sourcereturn-与主线程-freshness)
- [审批不继承与安全停止](#审批不继承与安全停止)
- [结果冲突、一次消费与主线程裁决](#结果冲突一次消费与主线程裁决)
- [中断恢复](#中断恢复)
- [1.2 技术与用户验收](#12-技术与用户验收)

## 何时保持 SOLO

小任务默认 `SOLO`。局部、低风险、强顺序依赖、需要连续主线程上下文或委派收益不足
时，主线程直接完成，不创建 brief，不创建 Agent。一个能由主线程安全完成的任务不因
“可能并行”而变成委派候选。

## 何时成为委派候选

只有当任务边界清楚、收益可解释、范围可绑定且结果能独立复核时，才进入以下状态机：

```text
SOLO -> DELEGATION_CANDIDATE -> APPROVED_NATIVE_CALL -> MAIN_THREAD_VERIFY
```

候选说明目标、非目标、读取范围、写入范围、预算、停止条件和验收证据。并行调查、
独立 review 或隔离写入必须说明为什么能减少总风险或时间；说明不了收益就保持 SOLO。

## 原生能力发现与 SOLO_FALLBACK

委派候选只使用 Codex 原生临时 Agent 能力；不预设固定 Agent 阵容（如
explorer/worker/reviewer），
不创建 Custom Agent、后台 Agent、Agent 池、多层 Agent 树或私有编排器。能力发现只能
读取当前运行时暴露的能力，不能猜测、安装或修改真实 `CODEX_HOME`。

原生能力缺失、能力状态 UNKNOWN、预算无法约束或派发接口不符合合同，都进入
`SOLO_FALLBACK`：主线程继续做安全的工作，或停止依赖委派的动作。SOLO_FALLBACK 不是
隐式批准，也不把失败结果伪装成 Agent PASS。

## 中文 brief V2

委派 brief 的 schema ID 是 `vibe-project-lead-zh-delegation-brief-v2`，顶层字段必须
精确包含：

```text
schema_id, delegation_id, parent_goal, objective, non_goals,
authority_project, baseline, mode, read_scope, write_allowlist,
allowed_commands, prohibited_actions, budget, stop_conditions,
deliverables, acceptance, expiry, reception
```

`delegation_id` 使用当前 D 批次派生的唯一值
`D-1.2-NATIVE-<001-999>/<01-99>`。每次新批准使用未占用的批次 ID；历史批次 ID 不得复用。
`mode` 只能是 `READ_ONLY`、`REVIEW` 或 `ISOLATED_WRITER`。`authority_project`
必须绑定 logical path、physical path、Git
top-level、git dir、git common dir 和 worktree ID；`baseline` 必须同时包含
`main_thread_identity`、`authority_identity` 与 `required_file_hashes`。

在本实现的安全合同中，`read_scope` 是逐文件的仓库相对 POSIX 路径，不接受绝对路径，
也不能用目录前缀扩大范围；`files_read`、`files_modified` 和候选的 `changed_files`
同样使用仓库相对路径。`authority_project` 中的项目身份字段才使用绝对路径。每个读取
文件都必须有对应的 SHA-256。`allowed_commands` 始终是完整 argv 数组，不以自由 shell 脚本代替批准清单。
校验器只验证调用方显式提供的 JSON，不读取 Goal、memory、session、环境变量集合、业务
项目、Git 元数据或真实 `CODEX_HOME`。

原生命令工具支持直接 argv 时按数组传递；只接受命令字符串时，先只读验证实际传输为 POSIX shell，并用合成参数回显确认逐项不变。仅可把精确匹配白名单的 argv 逐参数按 POSIX 规则引用，形成固定 `exec ` 前缀加一个简单命令；不能再拼接管道、重定向、命令替换、环境赋值或其他语句，不能指定 shell 程序、eval 或 env 包装器。引号内的特殊字符只是字面参数；批准解释器参数中的程序内容仍需单独核对只读范围和副作用，引用正确不等于程序安全。未知传输、回显不一致或无法证明唯一映射时停止。

源码中的 `render_review_command` 是上述只读转换的可选纯函数，不执行命令、不探测宿主、不产生批准；业务项目不需要复制 workbench 工具。主线程保留原 argv、实际工具调用字符串及结果的对应证据，关闭可配置的登录 shell；不能用工具包装器藏匿新增动作。工具实际运行命令才算执行，生成字符串不算测试通过。此适配不扩大 read_scope、write_allowlist、调用预算或其他权限，也不改变身份、新鲜度、结果证据与过期规则；切换到未经验证的 shell 必须重新停止核验。

两份 identity 必须是 schema 2、`status=bound`、`binding_kind=GIT_WORKTREE`、
`fingerprint_complete=true`、`write_eligibility=ELIGIBLE` 的完整稳定候选；不完整、
UNKNOWN 或 BLOCKED 直接拒绝。

`reception` 精确包含 `task_id` 与 `resume_policy`。task_id 绑定本次实际原生任务，
为 1–128 个 ASCII 字母/数字及 `._:/-`，首位必须字母/数字。保守策略为 `EXPIRE`；
仅 READ_ONLY / REVIEW 可在新的已批准派发前选 `REVALIDATE_READ_ONLY`。
ISOLATED_WRITER 只能使用 EXPIRE。过期事件与接收证据见“中断恢复”。
V1 仅供历史诊断，不是新派发模板，不得追加字段或摘要迁移旧结果。

## 结果 result V2

委派 result 的 schema ID 是 `vibe-project-lead-zh-delegation-result-v2`，顶层字段必须
精确包含：

```text
schema_id, delegation_id, status, mode, files_read, files_modified, commands,
source_baseline_sha256, brief_sha256, return_baseline, findings, candidate,
tests, unknowns, warnings, scope_requests, usage, nested_delegation_attempted,
external_action_attempted, recommendation, returned_at_utc
```

`brief_sha256` 在返回时绑定实际收到的完整 brief；与 `source_baseline_sha256` 不同，
后者只绑定派发时 authority identity。两者使用可选源码工具的
`canonical_contract_digest` 规范摘要：去除其声明的采集时间字段后，按稳定 JSON 编码
计算 SHA-256；不是原始文件字节摘要。材料完整性另按实际字节核对，不互相替代。

结果不认证自身来源；命令、测试、用量、未知项和原始返回均由主线程核对。
接收器允许解析 V1 用于诊断，但含 V1 的接收返回
`INCOMPLETE / LEGACY_CONTRACT_REQUIRES_NEW_DISPATCH`，不产生消费记录。

## 预算与一个 D 级批次

一个边界完整的 1.2 委派批次对应一个精确 D 级请求，用户批准的是批次范围，而不是隐式
批准未来所有调用。预算字段必须精确包含：

```text
max_agents, max_turns_per_agent, max_wall_time_minutes,
max_model_calls, model_policy, token_budget
```

四个最大值为正整数；`model_policy` 只能是 `USER_APPROVED` 或 `RUNTIME_DEFAULT`；未获
明确 token 上限时 `token_budget` 必须是 `null`。主线程在派发前把 `max_agents` 与当前
运行时上限比较，并为批次内每名 Agent 生成派生 `delegation_id`，不复用旧 ID。

## 只读、Review 与隔离写入

`READ_ONLY` 和 `REVIEW` 的 `write_allowlist` 必须为空，结果的 `candidate` 必须为
`null`，且不得写项目、Git 元数据或状态。`ISOLATED_WRITER` 才能产生候选，并且必须
绑定独立 linked worktree：同一 `git_common_dir`、不同 `worktree_id`、不同物理路径、
相同 parent HEAD。一个 worktree 同时只有一个写入者；`write_allowlist` 为非空且路径
不互相形成父子重叠。

workbench 校验器只是可选验证工具，`write_authorized` 永远是 `false`。它不创建 Agent、
不 merge/cherry-pick/rebase，也不把候选变成主线程写入批准。

## source、return 与主线程 freshness

结果必须同时经过 source freshness、return freshness 和 main-thread freshness 检查。
brief 的 authority baseline、结果的 `source_baseline_sha256`、`return_baseline`、
当前 authority identity 与当前 main identity 必须按模式匹配；至少绑定完整项目路径、
worktree、HEAD、dirty fingerprint 和必要文件哈希。`source_baseline_sha256` 必须是
派发时 authority identity 的 canonical digest。

`status` 只能是 `RETURNED`、`STOPPED`、`FAILED` 或 `UNKNOWN`；unknown、未运行或失败的
测试、scope request、identity 不完整和 freshness 不可判定都属于 incomplete。任一
`write_eligibility=BLOCKED` 或 fingerprint 不完整的身份永远不能形成写入资格。

## 审批不继承与安全停止

Agent 不继承用户账号、凭据、Provider、网络、安装、真实 `CODEX_HOME`、push、PR、release、
部署、删除、迁移或业务项目写入审批。每份 brief 必须包含以下禁止项：

```text
NESTED_DELEGATION
ACCOUNT_OR_CREDENTIAL_ACCESS
NETWORK_OR_EXTERNAL_SERVICE
INSTALL_OR_CODEX_HOME_CHANGE
PUSH_PR_RELEASE
DEPLOYMENT
MAIN_THREAD_DECISION
WRITE_OUTSIDE_ALLOWLIST
```

`READ_ONLY` 和 `REVIEW` 还必须包含 `FILESYSTEM_OR_GIT_METADATA_WRITE`。每份 brief 必须
包含以下停止条件：

```text
BASELINE_DRIFT
IDENTITY_INCOMPLETE_OR_BLOCKED
ALLOWLIST_EXPANSION_REQUIRED
BUDGET_EXHAUSTED
EXTERNAL_PERMISSION_REQUIRED
NESTED_DELEGATION_REQUESTED
UNKNOWN_RESULT
```

出现任一停止条件立即停止并返回证据，不自动重试、不更换 Provider、不扩大范围。

## 结果冲突、一次消费与主线程裁决

主线程先做合同、scope、预算和 freshness 检查，再按需求符合性审查和质量与安全审查
两遍核对结果。`RETURNED`、`STOPPED`、`FAILED`、`UNKNOWN` 不是用户裁决；推荐值只能是
`ACCEPT_FOR_MAIN_THREAD_REVIEW`、`REJECT` 或 `REINVESTIGATE`。

评估 freshness 按 `CONTRACT_VIOLATION > INCOMPLETE > STALE > FRESH` 裁决；冲突、unknown、
不完整或过期结果拒绝消费。只有全部检查通过时才生成一次消费记录；同一
`delegation_id` 再次出现返回 `ALREADY_CONSUMED`。主线程记录消费 ID，但校验器不保存第二
状态源，也永远不把结果变成写入授权。

## 中断恢复

重新执行项目绑定、两次完整身份捕获、HEAD/dirty fingerprint 和能力发现。批次剩余批准
以 safety-gates.md 的“审批绑定与生命周期”为唯一详细真源；稳定恢复不恢复旧 brief/result，
也不恢复已失效或已消费结果。批次剩余动作与某份工程结果的有效性分别裁决。

V2 的 EXPIRE 策略仍须包含 `TASK_INTERRUPTED_OR_RESUMED`，压缩或恢复后结果过期。
REVALIDATE_READ_ONLY 必须移除这一矛盾项，并保留其余所有漂移和生命周期过期事件。
以下是只读重验的字段片段（合成示例，不是完整 brief 或真实派发证据）：

```json
{
  "reception": {
    "task_id": "synthetic-task-23",
    "resume_policy": "REVALIDATE_READ_ONLY"
  },
  "expiry": [
    "PROJECT_OR_WORKTREE_CHANGED", "HEAD_CHANGED", "DIRTY_FINGERPRINT_CHANGED",
    "APPROVAL_SCOPE_CHANGED", "RUNTIME_CAPABILITY_CHANGED", "TASK_ID_CHANGED",
    "USER_PAUSED_OR_CANCELLED", "APPROVAL_REVOKED",
    "LIFECYCLE_EVIDENCE_INCOMPLETE", "TASK_INTERRUPTED"
  ],
  "reception_context_fields": [
    "brief_sha256", "result_sha256", "current_task_id",
    "events", "events_complete", "evidence_complete"
  ]
}
```

`reception_context_fields` 只列出主线程接收上下文的六个键，不是 brief/result 字段。
Python 参数 `reception_context` / CLI `--reception-context` 必须提供完整对象：两个
摘要分别绑定实际批准送达的 brief 和实际返回的 result；current_task_id 来自当前任务。
events 是派发至接收的完整有序事件，最多 256 项；只接受 CONTEXT_COMPACTION、
SAME_TASK_RESUME、USER_PAUSED、USER_CANCELLED、APPROVAL_REVOKED、TASK_SWITCH、
PROJECT_SWITCH、TASK_INTERRUPTED。已确认无事件才为空列表；不能截断、猜测或只列恢复后事件。

前两种事件仅在重验策略下允许继续完整身份与运行新鲜度检查。暂停、取消、撤销、切换或
执行被打断会令该结果失效；后续 SAME_TASK_RESUME 不抹除这些事件。批次允许暂停后
重新核验剩余动作，不等于暂停前结果仍有效。V1 继续保留原过期事件，且始终只供诊断。

events_complete / evidence_complete 必须是主线程根据完整真实轨迹、材料、命令、原始返回
和当前有效批准核实的布尔值；Agent 自报、fixture、哈希相等或两个 true 都不能认证来源。
接收器不读取宿主事件、不保存消费历史、不创建批准。新 v2 结果只有尚未消费、未失效，
且项目、版本、程序指纹和完整证据均通过时，才可进入主线程两遍审查；不是审查 PASS、
写入资格或用户验收。接收判断后出现新事件或状态变化，必须重新核对。

没有当前完整证据时返回 UNKNOWN / BLOCKED；旧结果不能跨任务、项目、worktree、Provider
或批准范围复用。

## 1.2 技术与用户验收

确定性合同测试、相关 1.0/1.1 回归、py_compile、diff hygiene 和主线程两遍审查共同形成
技术证据，但不代表原生 Agent 行为评测或用户验收。1.2 还必须分别记录原生能力、预算、
新鲜度、单写入者、审批隔离、SOLO_FALLBACK 和恢复场景；任何 warning、UNKNOWN 或 skip
都单独列出。

用户验收由用户观察并标记，不由主线程代填：中文 brief 是否可理解、SOLO 默认是否自然、
委派收益是否清楚、结果过期是否拒绝、写入是否隔离、冲突是否由主线程裁决。Task 4-5
完成只表示实现候选和确定性证据完成；不创建 D 请求、不授予写入资格、不写 `USER_ACCEPTED`，
也不关闭长期 Goal。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
