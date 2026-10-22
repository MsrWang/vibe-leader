# 参与改进

Vibe Leader 面向中文 Codex 用户，由 Jason Wong 个人维护。欢迎有明确复现的问题报告、小范围修复和文档改进；维护者决定是否采纳，不承诺响应时间。

## 报告问题

普通问题使用本仓库 Issues，可从两份模板中选一份：

- [使用问题](.github/ISSUE_TEMPLATE/usage-problem.md)：版本与环境、预期、实际表现和最小复现。
- [体验反馈](.github/ISSUE_TEMPLATE/experience-feedback.md)：这次任务、顺畅或费力的地方、希望改善的结果。

不知道版本或无法稳定重现时直接写“未知”，不影响提交。已经运行过[只读自检](docs/self-check.md)的用户可附摘要；它是可选材料，不需要先跑全量测试。将失败、未检查和无法判断分别说明，不把静态示例、文件核验或保存的发现记录当成真实调用结果。

在 [GitHub 新建 Issue](https://github.com/MsrWang/vibe-leader/issues/new/choose) 页面选择模板；模板合入默认分支后才会在那里出现。也可以先复制模板内容填写。

安全问题先阅读 [SECURITY.md](SECURITY.md)。公开反馈不要包含 Token、Cookie、密码、私人项目、真实配置、安装备份或完整会话；使用虚构目录与最小样例。

## 提交修改

1. 大功能、安装路径变化、运行时文件增减或行为范围变化，先用 Issue 说明实际用户问题和最小方案。
2. 在自己的分支中完成单一目的的修改，保留既有用户改动、许可通知和第三方来源。不要修改历史证据来证明新行为成功。
3. PR 说明触发问题、修改后的行为、验证命令与结果、影响范围和限制。仅文档排版不必新增测试；功能修复应验证受影响行为。
4. 不附私人开发目录、缓存、密钥、日志或生成的安装状态。新数据文件保留适用的旁置许可；第三方代码说明固定来源及修改。

不要求签署额外 CLA；提交时应确认你有权贡献该内容，并接受文件所适用的现有许可。项目自有文件采用 MPL-2.0，既有第三方文件保留各自许可；参见[许可范围](THIRD_PARTY_NOTICES.md)。

## 验证

源码工具要求 Python 3.11+。普通确定性自检可从以下命令开始：

```bash
python3 -B -m unittest -q tests.test_behavior_scenarios tests.test_skill_route tests.test_acceptance_contract
```

完整测试会创建和清理自己的临时文件、合成 Git 仓库和安装夹具，部分测试涉及 Windows/WSL；先阅读[测试边界](docs/limitations.md#测试与证据边界)。不要将测试指向现有安装或业务项目。真实模型评测、额外费用、真实安装与公开发布分别取得本次授权。

静态演示位于 `demo/`，直接使用 HTML、CSS 和 JavaScript，无 npm 依赖。检查四个案例的切换、文档页签、提示词复制及拒绝回退、键盘操作及窄屏显示；确保没有网络 API、用户输入、持久化或追踪。演示说明的是预期流程，不是在线主管。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
