---
title: Vibe Leader · Codex Skill Examples
emoji: 🧭
colorFrom: gray
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: mpl-2.0
short_description: Codex 中文研发主管 Skill 的合成使用示例与调用指引
---

# Vibe Leader — Codex Skill 使用示例

本 Space 展示 Vibe Leader 3.0.2 在 Codex 项目任务中的预期工作方式。Vibe Leader 是可安装的中文研发主管 Skill，技术标识为 `vibe-project-lead-zh`。3.0.2 增加轻量纠偏：用户只需表达“感觉当前推进有偏差”，主管默认用三行说明最小修正，并在已有授权内继续。

## 如何查看

1. 在 App 中选择“新项目启动”“感觉项目跑偏”“错误项目处理”或“验收交付”。
2. 阅读示例提示词、预期处理和需要用户确认的下一步。
3. 使用“复制提示词”，在自己的 Codex 项目中按实际目标调整后发送。

这些是预先编写的合成案例，不是实际会话回放，也不是在线 Codex。点击不会运行模型、读取项目或形成用户验收。

## 在 Codex 中使用

从 Vibe Leader 的 GitHub 仓库取得完整源码，按入门指南核对宿主与安装目录。3.0.2 修改了 Skill 的纠偏工作流；使用 3.0.1 或更早版本时，需要按受控升级步骤更新实际安装。安装或升级并确认“中文跨项目研发主管”已启用后，在实际项目任务中显式调用：

```text
使用 $vibe-project-lead-zh。先绑定当前项目，只读调查后告诉我目标、风险和最小下一步，暂不修改。
```

已验证 Windows Codex Desktop 配合 Ubuntu/WSL；其他宿主和版本需另验。Skill 不是 OS 沙箱，模型可能偏离流程。静态案例说明预期规则，不证明新任务已经加载 3.0.2，也不证明长期行为。版本与源码以 [GitHub Releases](https://github.com/MsrWang/vibe-leader/releases) 为准。

## 数据与运行边界

此 Space 使用静态 HTML。没有自由输入、代码上传、Token、模型 API、后台任务、持久化或自建分析追踪。复制按钮只复制选中的合成文本，不读取剪贴板。平台自身的访问数据处理适用其政策；实际 Codex 的上下文可能发送给配置的模型服务。

## 源码与许可

`index.html`、`styles.css`、`app.js`、本 README 与完整 MPL-2.0 LICENSE 构成本 Space 的上传范围，无构建依赖。

维护者 Jason Wong。示例与界面代码采用 MPL-2.0，完整产品的固定协议参考另保留其来源许可。HF 的 App 只负责示例阅读，GitHub 承载源码、安装说明、版本与问题反馈。

平台配置：[Static HTML Spaces](https://huggingface.co/docs/hub/spaces-sdks-static)。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
