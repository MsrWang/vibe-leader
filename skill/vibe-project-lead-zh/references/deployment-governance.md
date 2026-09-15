# 部署与发布治理

## 适用范围

用户询问项目能否上线、还缺什么、如何设计 staging/production、认证、域名/TLS、数据迁移、备份恢复、监控、发布顺序、Go/No-Go 或回滚时，使用本 reference。Codex 原生主线程继续负责调查、解释、风险裁决和验收；本 reference 只是默认只读、可删除的治理合同。

所有输出固定 `write_authorized=false`。`READY`、`STAGING_VERIFIED`、`READY_FOR_PRODUCTION` 或 `Go` 都只是技术结论，不是业务项目写入、平台登录、push、发布或部署许可。不得回写业务项目，不得产生外部副作用。

## 入口与证据

1. 新任务绑定一个用户明确指定的项目，先独立执行 `pwd`，再捕获两次 identity，并核验 HEAD、dirty fingerprint 和 freshness。
2. 用户明确选择一个档案和目标环境 `local`、`staging` 或 `production`。缺项不得猜测，也不得读取业务项目。
3. 只读取部署判断所需的结构化事实；不得读取 raw diff、未跟踪文件原文、remote URL、环境变量集合、生产数据，也不得读取或保存凭据。
4. 证据必须标注时间、来源类别、完整性和冲突；`observed_at_utc` 不得晚于实际评估时间，未来时间不设置时钟偏差豁免，并按 `EVIDENCE_FUTURE` 失败关闭。`evidence.valid_until_utc` 必须提供严格 UTC 有效期；该字段缺失、非法、早于采集时间或评估时已经到期，统一按 `EVIDENCE_EXPIRED` 失败关闭并输出 `NOT_READY / No-Go`；身份不完整、freshness 非 `FRESH` 或证据冲突同样失败关闭。
5. 本地启动、单次测试或 fixture 只能证明对应技术条件，不能证明真实平台、TLS、认证、迁移、恢复或生产运营已验证。
6. 所选档案必须来自不可变的六项 `PROFILE_CATALOG`，调用方提供的同 ID 弱化或改写记录不得使用；并提供独立的 `evidence.profile_applicability`：档案 ID 必须一致，全部 `applicability` 条件为 `true`，全部 `non_applicability` 条件为 `false`，且冲突标志为 `false`。缺失、UNKNOWN、条件不完整、真假相反、档案不匹配或事实冲突时，统一返回 `PROFILE_NOT_APPLICABLE / NOT_READY / No-Go`，不得继续用该档案产生 Go 结论。
7. `evidence` 根和七类证据采用封闭字段合同：`authentication`、`tls`、`migration`、`backup_recovery`、`observability`、`rollback`、`sqlite` 只能包含各自声明的字段。每类都必须显式提供严格布尔值 `conflict=false`；验证、迁移、监控、回滚和 SQLite 检查字段也只接受严格布尔值。必需字段缺失、未知字段、`UNKNOWN`、数字/字符串伪布尔值、嵌套冲突或自相矛盾证据统一加入 `EVIDENCE_CONFLICT` 并返回 `NOT_READY / No-Go`；production action 返回 `PRODUCTION_NOT_READY`。可信代理、会话安全、安全头和限流是唯一允许省略的已知强化字段，省略或为 `false` 只能形成明确 Warning 和 `Conditional Go`，不能被解释为已验证。

## 六类档案

| ID | 适用重点 | 必须解释 |
|---|---|---|
| `VERCEL_WEB` | Web、静态或 serverless 交付 | 构建、预览隔离、公开入口、运行时配置类别、回滚 |
| `LINUX_HOST` | 单主机常驻服务 | 反向代理、服务账号、进程恢复、主机持久化、回滚 |
| `DOCKER_SERVICE` | 容器镜像和声明式运行时 | 镜像来源、运行用户、网络、volume、健康检查 |
| `PYTHON_SQLITE` | Python 服务与 SQLite | 文件系统语义、并发锁、备份一致性、完整恢复 |
| `FRONTEND_BACKEND_SPLIT` | 独立前端和 API | origin、CORS、会话、API 兼容、两端版本回滚 |
| `STATIC_SITE` | 无服务端持久化的静态产物 | 产物完整性、链接、缓存、TLS、上一版本恢复 |

本次用户未选择的档案只能记录为 `PLANNED` 或 `NOT_APPLICABLE`，不得写成已验证。新增档案按独立证据和验收扩展，不复制其他平台结论。

## 准备度、状态与结论

- `READY`：所选档案的阻断证据完整、当前且不冲突。
- `CONDITIONAL`：只有明确、非阻断的缺口，且负责人和下一证据步骤已记录。
- `NOT_READY`：身份/freshness、认证、TLS、迁移、备份恢复、监控或回滚存在阻断或未知。

状态只允许在 `NOT_CONFIGURED`、`READY_FOR_STAGING`、`STAGING_VERIFIED`、`READY_FOR_PRODUCTION`、`BLOCKED` 和 `UNKNOWN` 之间按显式证据转换。结论分别为 `Go`、`Conditional Go` 和 `No-Go`；任何 UNKNOWN、冲突或不可执行回滚必须是 `No-Go`。

## 安全、数据和运维门

- 认证必须解释身份来源、授权边界、会话/Cookie、可信代理和不可信输入；安全头与限流未验证时最多 `Conditional Go`。
- 公网入口必须解释域名/TLS 责任和证据；不自动注册域名、申请证书或创建公开地址。
- 迁移必须先在隔离副本演练；生产迁移另需备份、恢复验证、维护窗口和精确批准。
- SQLite 必须确认文件系统语义、并发、锁、备份一致性和从备份完整恢复；缺一项不得生产 Go。
- 监控至少覆盖日志、指标、健康检查、告警、容量和响应责任；不得把“能启动”写成“可运营”。
- 回滚必须说明触发条件、执行者、上一产物/配置、数据兼容、验证和失败时的停止办法。

## 权限和动作请求

staging、production 和 rollback 是三组独立权限，必须分别批准；批准 staging 不包含 production，批准发布也不自动包含 rollback。每个动作请求只描述：目标、范围、数据类别、凭据类别、潜在副作用、成功/失败/未知标准和回滚办法。

动作请求的目标环境必须与本轮评估的 `environment` 一致：production 请求只能来自 production 评估，staging 请求只能来自 staging 评估。rollback 是独立动作，不是环境名；它沿用获准回滚之评估的环境，不能把 local 或 staging 评估改写成 production 目标。

未获对应批准时只返回计划或 `ACTION_NOT_APPROVED`。即使请求已获批准，当前只读评估也仍保持 `write_authorized=false`；真正的联网、登录、安装、业务项目写入、迁移、push、发布和部署必须进入独立精确授权的执行任务。

## 中文输出与验收

每次评估用中文回答：项目还缺什么、为什么不能直接上线、最小 staging 怎么做、生产架构和信任边界是什么、上线前谁决定、如何发现失败、如何回滚。输出同时列出档案、环境、证据时间、确定性排序的证据引用、准备度、缺失条件、假设、未知项、安全、数据、监控、回滚和 Go/No-Go。

确定性测试只证明合同、状态、档案和零副作用。真实项目验收还需要用户确认：缺失条件可理解；staging/production 分离清楚；认证与安全风险明确；迁移/备份/恢复风险明确；监控和回滚责任可执行；未发生外部动作。技术结果不得自动代填用户 PASS。

## 回滚边界

代码回滚只移除部署治理模块、测试、本 reference 和 Skill 路由，不删除业务项目、状态源、凭据、备份或外部资源。已经发生的真实部署、迁移或平台变更必须使用该动作自己的恢复记录，不能用仓库回滚代替。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
