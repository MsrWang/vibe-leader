# Skill 能力导航

## 稳定中文映射

本页描述能力与示例 discovery ID，不是本机已安装、已启用或完整库存的证明。
主管自身是产品必需项；扩展示例不是启动依赖。系统、用户和当前已加载规则要求的强制 Skill 仍按当前动作生效。
本次动态发现决定目标是否可用；导航未列出的目标记为 UNINDEXED_PROJECT_SKILL 覆盖提示，其当前身份仍按主管工作流逐 locator 校验。

| 能力 | 用途 | 示例 discovery ID（不代表当前可用） |
|---|---|---|
| 项目主管 | 绑定项目、澄清目标、规划执行、核验交付；主管自身必需 | vibe-project-lead-zh |
| 需求与计划 | 需要时澄清方案、拆解任务；扩展示例 | brainstorming、writing-plans |
| 代码实现与调试 | 实现、测试、复现并定位问题；扩展示例 | systematic-debugging、test-driven-development |
| 验证与审查 | 核对完成证据、审查变更；扩展示例 | requesting-code-review、verification-before-completion |
| 网页与界面 | 实现界面并检查可访问性、响应式与交互；扩展示例 | web-design-guidelines |
| 文件与视觉产物 | 按当前发现选择文档、表格、演示或图像工具；扩展示例 | imagegen、visualize:visualize |
| 安装与外部工具 | 当前要求触发时执行第三方安全审查；扩展示例，不代表安装批准 | skillspector-security-gate |
| 发布与回退 | 按目标环境选择发布工具并保留验证、审批及回退；扩展示例 | finishing-a-development-branch |

## 与本地库存报告的边界

本页不保存个人 discovery 快照。显式库存工具生成的本地报告另存到操作者指定的位置，不写回安装目录。
inventory cwd 是动态报告的来源信息，不是当前业务项目绑定。完整 inventory_sha256 仅比较相同规范化 inventory cwd、相同 runtime 与同一 Skill 根的完整发现；不同 cwd 的完整哈希不可直接比较。
目标身份可用不等于正文已读、指令已执行或写入已获批准。具体读取顺序、异常分类和降级边界见主管工作流。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
