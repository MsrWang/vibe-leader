# Vibe Leader 3.1 RC3 macOS 验证加固设计

日期：2026-09-28

状态：用户已于 2026-09-28 确认设计方向；设计文档待用户复核后进入实施计划

基线提交：`8788a9fd84929309e1739ebf69a3d31195c2d9e0`（`v3.1.0-rc.2`）

## 1. 背景与结论

`v3.1.0-rc.2` 已修复首次真实 Mac 技术检查暴露的两个测试可移植性问题。首次检查也证明了候选资产可以完成摘要核对和安全解包，证据筛选核心路径可以在 Apple Silicon Mac 上运行；但它没有执行真实 `CODEX_HOME` 的安装、升级或恢复。

本轮剩余风险主要来自验收流程，而不是已经识别出的 Darwin 常量错误：

- 验收指南反复使用裸 `python3`，真实 Mac 的系统解释器是 3.9.6，执行者只能临时改用 Codex App 自带的 CPython 3.12.14；
- GitHub 下载和 clone 曾出现 HTTP/2 framing error，执行者临时改用 HTTP/1.1 和 `.part` 文件后才继续；
- 真实 `CODEX_HOME` 只读预检排在临时技术验证之前，与“先不触碰真实根”的执行边界冲突；
- WSL 合同测试不能证明 macOS 内核、APFS 和 Darwin 原生系统调用的真实行为；
- Mac 上仍需手工串联较长的首次安装、升级、检查和恢复命令，容易产生步骤漂移。

因此，RC3 采用“本地全面回归 + GitHub 托管 Apple Silicon macOS 技术门 + 用户 Mac 一次最终实装验收”的三层验证。GitHub 当前把标准 `macos-14` 托管运行器列为 Apple M1、`arm64`；该运行器可证明 Darwin/APFS 技术链，但不能代替用户 Mac 的 Codex App 发现和真实安装观察。

## 2. 目标

1. 在用户再次使用实体 Mac 前，尽可能发现 Python 选择、Darwin 原语、临时安装、升级、恢复、发行包和文档命令中的问题。
2. 让所有 Python 命令使用同一个已经核验的解释器，避免执行过程中从 3.12/3.14 漂移回系统 3.9。
3. 让 GitHub 传输从第一次请求开始就使用已知更稳定的 HTTP/1.1，并确保不完整文件永远不被当作已下载资产。
4. 把所有会写入的技术检查限制在测试自有临时目录；完成后才允许读取真实 `CODEX_HOME`，再经独立批准执行真实写入。
5. 让 GitHub macOS 测试与实体 Mac 技术检查执行同一组核心合同，减少“CI 通过但验收步骤不同”的偏差。
6. 形成新的 `v3.1.0-rc.3` 候选，不修改或覆盖已经公开的 RC2。

## 3. 非目标

- 不接入外部 Jev、第三方模型或真实业务数据；
- 不改变 Vibe Leader 的主管定位、权限边界或 13 文件 Skill 内容合同；
- 不扩展 Windows 原生或 Intel Mac 支持；
- 不把 GitHub Actions 结果描述为 Codex App 用户验收；
- 不自动安装或升级 Python、Homebrew、Git、Codex App；
- 不搜索、猜测、创建或修改真实 `CODEX_HOME`；
- 不在本设计批准下自动 push、触发公开 Actions、创建 RC3 Release 或更新 Hugging Face；这些外部动作继续使用各自的明确批准和回读收据；
- 不为降低失败率而加入无界重试、关闭摘要检查或绕过安全探针。

## 4. 方案选择

### 4.1 采用方案：三层验证

第一层在当前 WSL 隔离工作树完成静态审计、单元测试、模拟 Darwin 合同测试、Linux 回归和发行包确定性检查。第二层在 GitHub 托管的 `macos-14` Apple Silicon 运行器完成真实 Darwin/APFS 技术检查。第三层只在前两层通过并冻结 RC3 后，由用户的 Mac 完成一次真实 Codex App 实装、发现、调用和恢复观察。

这个方案增加一个受控 CI 门，但能在占用用户 Mac 前执行真实 Darwin 系统调用，是当前成本与覆盖率最合适的组合。

### 4.2 未采用：只做本地模拟后直接用用户 Mac

该方案不需要 GitHub Actions，但用户 Mac 会继续承担第一次真实 Darwin 安装器执行。模拟无法证明 `statfs`、`renameatx_np`、APFS 交换语义及 GitHub runner 上的 arm64 路径，较容易需要第二轮实体机复验。

### 4.3 未采用：把全部验收封装成新的大型安装程序

单一程序可以减少复制命令，但会新增一套安装编排、状态管理和恢复实现，反而扩大 RC3 的代码面。RC3 复用现有安装器和发行工具，只增加聚焦的原生集成测试、CI 编排及验收指南加固。

## 5. Python 解释器绑定

验收开始时只绑定一次 `VIBE_PYTHON`：

1. 调用者可以显式提供绝对可执行路径；未提供时只使用 `command -v python3` 的结果；
2. 将路径规范化并确认其为可执行普通文件；
3. 使用该解释器一次性输出并验证 `sys.executable`、实现、完整版本、release level 和 `platform.machine()`；
4. 只接受稳定版 CPython、`3.11 <= version < 3.15` 和 `arm64`；
5. 验证通过后，指南中的 JSON 读取、测试、发行工具和安装器全部使用 `"$VIBE_PYTHON" -B`；不得再次调用裸 `python3`；
6. 解释器不合格时返回 `PYTHON_UPDATE_REQUIRED` 或 `RUNTIME_UNVERIFIED`，由用户更新 Python 或显式提供另一个合格解释器后重新开始新的验收，不在脚本内安装依赖。

设计不硬编码某个用户名、Home 目录或 Codex App 内部缓存路径。这样既允许用户 Mac 显式使用已经发现的 Codex CPython 3.12.14，也适用于其他安装了合格 CPython 的 Apple Silicon Mac。

## 6. 网络与资产落盘

所有 GitHub HTTP 请求从第一次尝试开始使用 `curl --http1.1`。每个响应先写入同目录、唯一命名的 `.part` 文件；只有满足以下条件后才以排他方式成为最终文件：

- curl 成功退出；
- Release API 的仓库、标签、资产名称、URL 和 `sha256:` digest 全部匹配；
- 下载字节的 SHA-256 与 API digest 一致；
- 最终路径不存在。

Git 操作统一使用 `git -c http.version=HTTP/1.1`。clone 先写入临时目录，核对唯一 origin、标签提交和冻结提交后再作为可信仓库使用。失败时保留 `.part` 文件或临时 clone 供诊断，不自动重试，也不把残留解释为成功。

这一规则解决已观察到的 HTTP/2 framing error，同时保持“单次、可审计、失败关闭”的传输合同。HTTP/1.1 只能减少已观察到的传输兼容问题，不能替代 GitHub API digest、提交身份和发行工具验证。

## 7. 验收顺序

### 7.1 阶段 A：完全隔离的技术验证

1. 记录系统摘要并绑定 `VIBE_PYTHON`；
2. 创建权限为 `0700` 的验收根和私有证据目录；
3. 绑定公共仓库、候选标签、冻结提交和 v3.0.3 基线提交；
4. 下载、核对并安全解包 RC3 三个发行资产；
5. 运行证据筛选完整测试；
6. 在临时 `CODEX_HOME` 完成首次安装和 verify；
7. 在第二个临时 `CODEX_HOME` 完成 v3.0.3 安装、3.1 升级、inspect、verify、恢复及原状态比较；
8. 生成不含用户名、绝对路径、Token 或业务文本的技术摘要。

阶段 A 不读取或写入真实 `CODEX_HOME`。任一步失败时保留现场并停止。

### 7.2 阶段 B：真实根只读预检

只有阶段 A 全部通过后，用户才从 Codex App 当前配置和唯一 locator 观察中显式提供 `VIBE_REAL_CODEX_HOME`。安装器只执行 `preflight`，并验证九字段结果、`read_only=true` 和预期安装模式。该阶段不创建目录、不运行写探针、不修改真实 Skill。

### 7.3 阶段 C：真实安装与用户观察

真实安装或受控升级仍是独立外部动作。获得对应批准后，只执行 preflight 指定的一条路径，然后完成 manifest 核验、Codex App 唯一发现、新任务显式调用、合成本地证据筛选和恢复演练。用户观察是 U3 证据，命令退出 0 或 CI 成功不能替代。

## 8. GitHub macOS 原生技术门

仓库新增 `.github/workflows/macos-native.yml`。首次真实运行明确绑定公开仓库 `MsrWang/vibe-leader`，不得从当前开发工作树的 `origin` 推断目标。工作流遵循以下合同：

- 只使用 GitHub 托管的 `macos-14` 标准运行器；根据 GitHub 当前官方运行器表，该标签对应 Apple M1、`arm64`；
- Python 矩阵覆盖 3.11 和 3.14，均通过同一运行时门；
- 权限固定为 `contents: read`，不读取仓库 secret，不发布 Release，不 push，不写外部系统；
- `push` 入口只匹配公开仓库中的专用候选分支 `codex/vibe-leader-3.1-macos`；同时保留 pull request 和 `workflow_dispatch` 入口，避免其他分支的普通提交无条件消耗 macOS 分钟；
- checkout 取得完整历史，以便从固定基线提交 `d0e17544584fb6c68254c49b87f4275fa19a71f4` 读取 v3.0.3 Skill；
- 成功日志只打印系统版本、架构、Python 版本、提交、状态码和摘要，不主动打印完整环境、完整 JSON 或用户路径；失败诊断允许包含 GitHub 临时运行器路径，但不得包含用户 Mac 路径、凭据或业务数据；
- 每个 job 使用新的临时目录，不依赖缓存；结束时清理测试自有目录，失败时由 GitHub job 日志保留有界诊断；
- 工作流 action 依赖使用 GitHub 官方 action，并在实施时固定到经过核对的不可变提交 SHA；
- 并发组对同一分支只保留最新一次运行，避免重复占用。

GitHub Actions 的公开资料说明标准 macOS arm64 运行器为临时虚拟机；因此它可以提供真实 Darwin/APFS 技术证据，但无法证明用户 Mac 上的 Codex App 版本、实际 `CODEX_HOME`、本机已有 Skill 状态或用户可见发现结果。

把候选提交 push 到上述公开分支属于独立外部动作，必须在本地实现、测试和审查完成后取得明确批准。push 的成功回读与 macOS job 的最终成功结论分别记录；其中任何一个都不能代替 RC3 Release 的发布批准。

## 9. macOS 原生集成测试

新增一个仅在 `sys.platform == "darwin"` 时执行的原生集成测试模块。非 Darwin 环境明确 skip，不能通过 mock 冒充原生成功。模块只使用标准库和测试自有临时目录，覆盖：

1. 运行时、系统和 `arm64` 门；
2. 真实 `_darwin_statfs` 读取，并核对 mount point、文件系统类型和 device；
3. 真实 `renameatx_np` 的 `RENAME_EXCL` 与 `RENAME_SWAP` 探针、反向交换和内容回读；
4. Darwin 证据筛选的目录锚定打开、普通文件读取、符号链接拒绝、特殊文件拒绝和来源变化拒绝；
5. 从当前冻结提交构建确定性发行 ZIP，执行 verify、extract，并核对提交和清单；
6. 在临时根执行首次安装和 verify；
7. 从固定 v3.0.3 提交构造旧版源，在第二个临时根执行安装、prepare-upgrade、upgrade、inspect-upgrade、verify、restore-version 和原状态比较；
8. 核对测试没有打开真实 `CODEX_HOME`，目标路径始终位于测试临时根内。

集成测试调用现有公开 CLI 或稳定内部边界，不复制安装、升级或恢复算法。需要批准 ID 和 confirmation digest 的步骤使用清晰的测试专用值，并保持现有摘要验证，不增加绕过入口。

## 10. 本地测试与文档验证

WSL 实施按 TDD 顺序增加失败测试，再修改实现或文档。验证集合包括：

- Python 绑定片段在空值、旧版、预发布版、非 CPython、3.15+、非 arm64 和合格运行时下的结果；
- 所有执行命令都使用 `VIBE_PYTHON`，阶段 A 中不出现真实 `CODEX_HOME` 访问；
- curl 全部使用 HTTP/1.1 和 `.part`，GitHub git 操作全部固定 HTTP/1.1；
- `.part` 摘要不符、最终路径已存在、tag/commit 不符时失败关闭；
- macOS 指南所有 Bash 代码块通过 `bash -n`；
- 原有证据筛选、安装器、发行包、治理和 Skill 合同回归；
- 发行包连续构建字节一致，安全解包和 13 文件 Skill 清单保持不变。

本地通过只说明 Linux/WSL 与模拟合同通过。只有 GitHub macOS job 通过后，RC3 才满足实体 Mac 最终验收的前置条件。

## 11. 文件范围

计划修改范围限于：

- `docs/macos-acceptance-3.1.md`：解释器绑定、HTTP/1.1、临时文件、阶段重排和一次性验收说明；
- `.github/workflows/macos-native.yml`：Apple Silicon macOS 技术门；
- `tests/test_macos_native_acceptance.py`：Darwin 原生集成测试；
- 现有聚焦测试文件：验证指南合同、工作流安全边界和必要的测试辅助接口；
- `docs/release-3.1.0.md` 及必要的版本资料：记录 RC3 的修复范围和证据状态。

只有测试暴露现有 `scripts/install_skill.py`、`scripts/release_archive.py` 或 `evidence_filter.py` 的真实缺陷时，才对相应实现做最小修复，并为缺陷先增加回归测试。不得借机重构无关代码或修改 `SKILL.md` 的角色行为。

## 12. 失败处理与证据边界

- WSL、本地合同、GitHub macOS 和实体 Mac 是不同证据层，后者不能被前者替代；
- CI failure 或 cancelled 都不满足候选门，不能通过 rerun 直接忽略；先读取首次失败证据并修复确定原因；
- 网络失败、运行器不可用和代码失败分别记录，网络或平台故障不自动归因于候选代码；
- GitHub Actions 不接触真实 `CODEX_HOME`，也不产生真实安装授权；
- 实体 Mac 失败时保留私有原始证据，不清理后盲目重试；
- 公开收据只含去标识字段，真实路径、用户名、完整日志和平台 JSON 留在私有证据目录；
- RC3 必须使用新提交、新标签和新资产摘要；RC1、RC2 继续作为历史 prerelease 保留。

## 13. 完成条件

进入实体 Mac 最终验收前，必须同时满足：

1. 设计对应的实施计划已完成；
2. 本地聚焦测试和完整回归通过；
3. macOS 指南命令静态验证通过；
4. RC3 候选资产确定性构建、verify 和 extract 通过；
5. GitHub `macos-14` 的 Python 3.11 与 3.14 job 均通过；
6. 运行记录确认 `arm64`、Darwin 原生 `statfs`/交换探针、临时首次安装及临时升级恢复均通过；
7. 没有真实 `CODEX_HOME` 写入，没有未说明的 skip，没有公开敏感路径；
8. 用户单独批准 RC3 发布和实体 Mac 真实安装或升级。

实体 Mac 一次验收只有在技术验证、真实 preflight、对应安装路径、Codex App 唯一发现、显式调用、合成筛选和恢复观察全部完成后，才能记为 3.1 macOS 实装通过。任何一项为 `UNKNOWN` 都保持候选状态。

## 14. 参考资料

- [GitHub：Choosing the runner for a job](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)
- [GitHub：Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Apple：APFS Tools and APIs](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/APFS_Guide/ToolsandAPIs/ToolsandAPIs.html)
- [Apple：statfs(2)](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/statfs.2.html)

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
