# 多项目只读 Portfolio

## 适用范围

用户需要登记多个独立项目、查看中文组合状态、比较优先级/里程碑/阻塞/依赖，或选择下一个项目时，使用本 reference。Portfolio 只汇总本轮只读事实和用户提供的配置，不是多项目写入器、后台监控器或第二套项目状态。

所有 Portfolio 结果固定 `write_authorized=false`。排序是可解释建议，不是项目审批、写入资格、发布决定或长期 Goal 完成结论。

## 输入与捕获

1. 任务只绑定用户批准的 Portfolio 根；先独立执行 `pwd`，再读取唯一 `registry.toml`。
2. 注册表只保存项目 ID、显示名、绝对路径、生命周期、优先级、依赖和状态源位置。不得保存 HEAD、dirty、fingerprint、审批、remote、Token 或凭据。
3. 每个项目执行两次当前 identity 捕获。状态源只通过第一次捕获所证明的项目根 descriptor 读取；第二次捕获验证期间没有漂移。
4. 只接受闭合的 `JSON_STATUS_V1`；不读取 raw diff、未跟踪文件内容、环境变量或 remote URL。缺少状态源保持 `STATUS_SOURCE_NOT_CONFIGURED`。
5. 不自动创建或修改 `registry.toml`、状态源、baseline、业务项目、测试、提交或部署；Portfolio 对任何项目都不回写。

## Freshness 与失败关闭

| 状态 | 含义 | 处理 |
|---|---|---|
| `FRESH` | 当前完整事实与已验证 baseline 一致 | 可用于只读组合判断，仍不授权写入 |
| `STALE` | 当前捕获完整，但配置、identity 或状态源已变化 | 显示变化字段，重新调查 |
| `UNKNOWN` | 首次运行、无状态源、不支持的 identity 类或捕获不完整 | 保留原因，不用历史值补齐 |
| `AMBIGUOUS` | 路径、对象或 Git identity 冲突 | 失败关闭并停止受影响选择 |

首次运行固定为 `NO_PORTFOLIO_BASELINE`，不是错误也不是 PASS。baseline 只能比较，不能覆盖当前事实；digest、registry 或 schema 不匹配时整体拒绝。重复 filesystem identity 或重复 `(git_common_dir, worktree_id)` 记为 `PROJECT_OBJECT_DUPLICATE / BLOCKED`。

## 中文组合视图与排序

视图按固定桶展示 `ACTIONABLE -> BLOCKED -> WAITING_APPROVAL -> PAUSED -> ARCHIVED`，桶内再使用声明优先级、freshness、发布窗口、里程碑价值、阻塞成本、完成信心、风险、审批等待、任务规模和稳定 ID 顺序。理由必须标出来源字段，不合成不透明总分。

依赖项目只有在当前捕获完整、状态源有效、里程碑 `COMPLETE` 且 freshness 非 `UNKNOWN/AMBIGUOUS` 时才算满足。任何缺失、冲突或旧 baseline 都不能把项目升级为可行动。

## 选择项目

选择只生成交接数据，不在当前任务重新绑定：

```text
required_new_task=true
first_command=pwd
required_rechecks=[path, git, head, dirty, freshness, project_rules]
inherited_approvals=[]
write_authorized=false
```

随后使用 `STOP_PROJECT_SWITCH` 结束当前 Portfolio 任务，在新任务中重新选择主管、独立执行 `pwd`、读取项目规则、捕获 identity/freshness 并取得新的项目级审批。不得继承 Portfolio 任务或另一个项目的路径、HEAD、dirty fingerprint、技术假设、外部权限和 C-F 审批。
交接合同固定为不继承任何项目审批或外部权限。

## 验收与边界

确定性测试只证明 registry、descriptor 读取、snapshot、排序、中文视图和交接合同。真实验收还要由用户指定 Portfolio 根和两个项目，逐项确认：组合看板、单项目变化隔离、失败解释、排序理由、零写入/零外部动作、新任务切换。

Private 工程仓库继续是发布前权威；Portfolio 结果不得自动上传。转为 Public 必须另行完成隐私、许可证、安装体验、兼容矩阵和发布审查。回滚只移除 Portfolio 工具、测试和本 reference/路由，不删除真实注册表、baseline 或业务项目。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
