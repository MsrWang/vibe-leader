# 安全问题报告

Vibe Leader 由 Jason Wong（GitHub：MsrWang）个人维护。当前优先处理 2.3 中可复现的安全问题；不承诺固定响应时间，也不保证所有旧版本会收到修复。

## 私下报告

首发仓库采用 GitHub 的私密漏洞报告渠道。进入本仓库的 **Security → Report a vulnerability**，提交私密报告；请先确认页面标明为私密。该入口需维护者启用，公开首发时必须检查其实际可用性。

若看不到该入口，请只提交一句不含细节的普通 Issue，说明“需要安全报告渠道”；等待维护者提供私密路径后再发送详情。不要在公开 Issue、PR、讨论或截图中披露漏洞利用步骤、凭据、私人代码、真实安装清单或完整会话。

报告请包含：受影响的源码版本、系统与 Codex 版本、最小合成复现、预期/实际行为、影响范围。用虚构目录和无敏感内容的文件复现；确有必要提供敏感材料时，先与维护者确认接收方式和范围。

## 范围与边界

优先报告错误项目写入、未经授权执行、路径逃逸、安装或恢复破坏、私人数据进入公开产物、凭据泄露及来源许可问题。不要为验证问题而操作他人的账号、项目或生产数据。

Skill 是指令与参考资料，不是操作系统沙箱。模型遵从、宿主权限和安装恢复限制见[限制说明](docs/limitations.md)。收到报告不表示问题已被证实或修复；确认与修复范围以维护者实际核验为准。

GitHub 渠道说明：[配置私密漏洞报告](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
