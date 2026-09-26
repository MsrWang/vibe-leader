# Vibe Leader 3.1.0 macOS 说明

本文件描述 3.1.0 的固定字节合同、兼容边界和晋升流程。生命周期状态由外部 Release 元数据以及经签名或平台回读的收据建立；包内文字不声明资产当前处于候选、正式发布或 latest 状态。

## 本版改变什么

- 为证据安全读取、文件系统身份和原子目录切换增加 Darwin 平台原语，同时保留既有 Linux/WSL 行为。
- 增加只读 `preflight`，在写入前报告平台、运行时、显式安装根、安装模式和仍待写入阶段确认的能力探针。
- 增加确定性发行工具，从固定提交生成并验证三个资产：`Vibe-Leader-3.1.0-GitHub.zip`、`release_archive.py` 和 `SHA256SUMS.txt`。
- 可安装 Skill 仍为精确 **13 个文件**。3.1 只替换平台相关实现，不改变“中文跨项目研发主管”的定位、项目绑定、授权、交付和用户验收合同。
- 本版不接入外部 Jev；Jev 范围仍是显式调用的本地证据筛选，不调用外部 API、不读取凭据、不选择模型，也不承担项目级模块调度。

## 兼容设计和参考验收目标

通用设计目标是 macOS 14+、Apple Silicon M1+、`arm64` 和稳定版 CPython 3.11–3.14（最低 3.11；新安装推荐 3.14.7）。低于 3.11 要求先更新；非 CPython、预发布版、无法解析的版本和 3.15+ 保持 `RUNTIME_UNVERIFIED`。

2026 Mac mini（M6）仅为参考验收目标，不是已验证结论；固定参考配置还包括验收当日的 macOS 27 稳定补丁版本和 CPython 3.14.7。每次验收都要重新绑定 Codex App 版本、真实 `CODEX_HOME`、目标文件系统和安装前状态。

## 四类证据分开记录

1. Linux/WSL 回归只证明现有行为和可模拟的平台合同；**WSL 测试不是 Mac 原生验证**。
2. Mac 技术检查证明固定候选字节能在当次 Apple Silicon、macOS、Python 和文件系统组合上验证、解包、预检、安装、核验和恢复。
3. Codex App 用户观察证明当次安装能被发现、唯一定位、显式调用，并能在合成材料上运行本地证据筛选。**用户观察前不得声称 Codex App 验收完成**。
4. GitHub 或 Hugging Face 的平台回读只证明对应资产或页面已发布，不能替代本地技术检查和用户接受。

任何步骤要求改变代码、文档、安装步骤或资产字节时，应停止晋升并生成新的提交和候选编号。

## 资产、信任和收据边界

验收同时取得上述三个下载资产，并按 [macOS 3.1 验收指南](macos-acceptance-3.1.md)绑定公共仓库 `https://github.com/MsrWang/vibe-leader.git`、标签提交和 GitHub Release API 的三个资产 digest。`SHA256SUMS.txt` 只在自身字节先通过平台回读 digest 后用于核对内部配对；ZIP 内 `RELEASE-MANIFEST.json` 也不是签名或独立信任根。

私有原始证据可以保存真实绝对路径、完整 API JSON 和安装/恢复收据；公开去标识收据只公开摘要、状态和有界 locator 证据。原始绝对路径收据不得作为公开发布材料。

只读 `preflight` 不创建目录、不写能力探针、不修改 `CODEX_HOME`。真实安装、受控升级、候选回滚、正式版重装和平台发布各自需要当时明确的范围、状态和批准，外部动作分别批准。

## 六个独立晋升门

晋升顺序固定为六个独立门：

1. 本地候选冻结；
2. GitHub 候选 prerelease；
3. Mac 候选实装验收；
4. GitHub 正式发布；
5. Mac 稳定版实装；
6. Hugging Face 同步。

候选与正式版流程根据外部发布状态选择适用门。每个门重新绑定提交、资产、目标和当时批准；包内文档既不授权后续门，也不证明后续门已经完成。

与已接受候选相比，正式标签的提交或三个资产摘要任一不同，必须停止晋升并生成新的候选；不同字节不能沿用已接受候选的证据。只有提交与三个资产字节完全一致时，才可进入稳定版实装；此时若环境或安装前状态发生变化，仍要重新执行真实恢复演练。

## 未由本版证明

- Intel Mac、Windows 原生、网络盘、外接盘和大小写敏感 APFS 的完整组合没有由通用设计证明。
- WSL 合成测试没有执行 Darwin 内核、APFS 或 `renameatx_np` 原生路径。
- 真实 Mac 单机通过后，只能证明该次绑定组合；不会自动证明所有 Apple Silicon 机型、未来 macOS 或未来 Python 系列。
- 本版不增加外部 Jev、第三方模型路由、凭据、后台服务或项目级模块编排。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
