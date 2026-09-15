# Changelog

只记录公开产品变化。实际验证范围见[2.3 验证说明](docs/release-2.3.md)；公开发布状态以平台版本记录为准。

## 2.3 — 首发候选

### 面向新用户

- 对外名称为 Vibe Leader；保留显示名“中文跨项目研发主管”和技术标识 `vibe-project-lead-zh`。
- 能力导航改为稳定说明与当前发现；基础流程不要求个人插件库存。主管或当前目标能力无法证明时仍失败关闭。
- 新增入门、三组合成使用示例、数据流与兼容限制说明。
- 增加适用文件的 MPL-2.0 通知，并保留固定协议参考的 Apache-2.0 归属。运行时仍为 11 文件。

### 安装与评测工具

- 私人事故恢复锚点从公开安装 CLI 分离；保留通用安装、核验、受控升级、可恢复卸载与版本恢复边界。`archive-staging` 不作为公开 CLI 提供。
- 合成验收与私人历史证据分离；输出分区与拒绝覆盖约束保留。
- 评测路径要求显式绑定受保护项目、候选、评测根及必要 Git 身份；输入只取合成场景的用户输入，不附评分答案。
- probe v2 与旧配方、旧批准不兼容；不能靠补默认值复用旧授权。固定 schema 仍是最小测试参考，不是当前 Codex 全协议的兼容保证。

### 保留限制

- instruction-only 指令不是 OS 沙箱，源码校验器不会自动成为每次对话的强制执行层。
- Windows Codex Desktop + Ubuntu/WSL 已验证发现及实际使用；其他宿主、Windows-native 与所有跨文件系统组合不作全面兼容承诺。
- 实际安装、升级、旧版恢复及返回 2.3、停用和重新启用已在限定环境验证；非原子切换与权限属性限制保留。
- 三个受影响的绑定行为已有原生观察，但存在先执行 pwd 后完整读取绑定说明的顺序偏差，不声称全流程合规。

### 首发资料

- 同步已完成的 2.3 验收状态，补充安全报告、贡献指南和公开验证范围。
- 新增三个固定合成案例的静态演示；不调用模型，不收集用户代码或 Token。
- 本次仅修改公开文档和演示，126 文件原源码中的运行时、工具和测试保持原样。

详见[入门指南](docs/getting-started.md)与[限制说明](docs/limitations.md)。历史私人审批和迭代日志不作为公开变更记录分发。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
