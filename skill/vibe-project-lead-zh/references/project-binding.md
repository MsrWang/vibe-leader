# 项目绑定

## 目录

- [首命令不变量](#首命令不变量)
- [只读身份字段](#只读身份字段)
- [只读 Git 原语](#只读-git-原语)
- [绑定状态](#绑定状态)
- [dirty_fingerprint](#dirty_fingerprint)
- [两层路径身份](#两层路径身份)
- [两次完整捕获](#两次完整捕获)
- [写入前复核](#写入前复核)
- [用户修改](#用户修改)
- [项目切换](#项目切换)
- [恢复](#恢复)

## 首命令不变量

先从用户最新明确指令取得唯一目标目录。不要从聊天标题、IDE 标签、上个任务、memory、checkpoint、Git remote 或相邻目录猜测项目。

把工具 cwd 直接设为该目标目录。在任何项目 Git 命令、文件读取、目录枚举、测试或服务命令之前，第一条 shell 命令必须独立执行 `pwd`：

```bash
pwd
```

只比较这条命令的完整输出与用户目标目录。输出为空、命令失败、目标不存在、路径不相等或存在无法裁决的符号链接/挂载映射时，立即返回 `STOP_PATH_MISMATCH` 或 `STOP_IDENTITY_AMBIGUOUS`。停止后不得尝试“找一个看起来像”的仓库，也不得先读 Git 或文件来帮助猜测。

只有首命令通过后，才可用 `pwd -P` 取得物理路径并继续只读身份调查。逻辑路径与物理路径的关系不唯一、跨 Windows/WSL 映射无法证明或目录在调查期间变化时，返回 `STOP_IDENTITY_AMBIGUOUS`。

首条 `pwd` 建立 `workspace binding`。路径解析、`wslpath` 或其他转换器只产生候选路径；路径转换成功不等于 workspace binding 成功，也不是 filesystem identity 或 Git identity 的证明。首条输出与请求工作区不是同一对象时返回 `STOP_WRONG_WORKSPACE`，不得自动 `cd`、改用另一个表示或搜索同名仓库。

受保护或高敏感项目由本次用户或上层规则明确标记；目标必须由用户明确指定，且首命令输出与该目标精确匹配，才可继续绑定。不得从旧会话或相邻目录猜测目标。未标记为受保护不代表获准访问或写入，所有项目仍须满足本文件的绑定和相应动作批准；本条本身不授予访问权限。

不得继承其他任务的绑定或审批。一次普通任务只允许一个写入权威项目。

## 只读身份字段

首命令通过后，从当前事实建立临时绑定记录。至少报告下列字段；无法可靠取得时不要编造值。

| 字段 | 要求 |
|---|---|
| 用户目标路径 | 用户最新明确指定的原始路径 |
| logical path | 独立 `pwd` 的完整输出 |
| physical path | `pwd -P` 的完整输出及其与 logical path 的关系 |
| 项目类型 | Git 或 non-Git |
| Git top-level | Git 项目必须取得绝对 top-level；non-Git 标记不适用 |
| Git dir / common dir | 都使用 Git 返回的绝对路径，不手工拼接 |
| branch state | 分支名、detached 或 unborn 三者之一 |
| HEAD | 完整提交 ID；unborn 使用字面值 `<unborn>` |
| worktree ID | 根据 Git dir/common dir 关系记录 main 或 linked worktree 身份 |
| dirty summary | tracked/index/worktree/untracked 的计数与存在性，不输出原始 diff |
| dirty_fingerprint | 当前临时摘要值 |
| fingerprint_complete | `true` 或 `false`，并在 false 时列原因 |
| remotes | 只记录经过净化的 remote 名称和数量，不输出 URL、用户名或凭据 |
| 项目指令 | 当前路径层级实际适用的 `AGENTS.md` 等项目规则 |
| 项目状态源 | 已存在且可验证的规格、计划、状态或交接入口；它们只是证据，不覆盖现场事实 |

先判断 Git/non-Git。Git 命令明确表示“不是仓库”时可进入 `BOUND_NON_GIT` 候选；权限错误、超时、输出矛盾或部分成功不能降级成 non-Git，必须返回 `STOP_IDENTITY_AMBIGUOUS` 或 `UNKNOWN`。

不要读取或展示 remote URL。不要把工作树原始 diff、未跟踪文件内容、环境变量、凭据或秘密放进绑定输出。

## 只读 Git 原语

独立 `pwd` 通过后，先固定实际目录对象，再采集身份。只设置 `GIT_OPTIONAL_LOCKS=0` 不足以保证只读：文件监视钩子、textconv 和 clean/process filter 都可能执行外部程序。身份采集禁止使用 `git status` 或 `git diff`，即使追加 `--no-ext-diff`、`--no-textconv` 也不能保证不会启动过滤器。

对每个 Git 子进程在内存中构造环境：清除继承的 `GIT_*`，设置 `GIT_OPTIONAL_LOCKS=0`、`GIT_CONFIG_NOSYSTEM=1`，将 `GIT_CONFIG_GLOBAL` 与 `GIT_CONFIG_SYSTEM` 指向系统空设备。不要修改宿主环境或真实 Git 配置，不读取或输出原环境集合。

所有查询必须带 `--no-pager --no-lazy-fetch`，并用命令级配置禁用 `core.fsmonitor=false`、`core.untrackedCache=false`、`submodule.recurse=false`，将 `core.hooksPath` 指向系统空设备。使用可信的本地 Git/Python；不支持该能力、对象缺失、命令失败或权限不明时停止，不以去掉保护参数或运行下载、钩子来恢复。全局/系统排除配置不参与本采集；仓库内排除规则仍生效。

以下仅列查询的逻辑形式，实际执行必须同时具备上述环境、命令级保护及目录对象锚定，不能当作未经保护的 shell 清单复制运行：

```text
git rev-parse --show-toplevel
git rev-parse --path-format=absolute --git-dir
git rev-parse --path-format=absolute --git-common-dir
git symbolic-ref --short -q HEAD
git rev-parse --verify --quiet HEAD
git symbolic-ref -q HEAD
git show-ref --exists <本次HEAD指向的完整ref>
git ls-files --stage -z
git ls-files --others --exclude-standard -z
git ls-tree -rz --full-tree <本次已核实的HEAD>
git remote
```

`ls-tree` 仅对本地已存在的 HEAD 执行，unborn 使用空树记录。不能只取 stdout 丢弃退出码：`symbolic-ref --short -q HEAD` 的 0 必须有分支名，1 必须无输出才是 detached 候选，其余失败或矛盾均停止。`rev-parse --verify --quiet HEAD` 成功须给出完整提交 ID；退出 1 且无输出时，只有已取得 symbolic HEAD，再用 `symbolic-ref -q HEAD` 取得完整 ref、由 `show-ref --exists` 返回 2（引用确实缺失），才可判为 unborn。引用存在（0）、查询错误（1）、不支持此能力或其他矛盾均停止；detached 候选必须有有效 HEAD。不得把权限/I/O 失败解释成空仓库。`remote` 只取得名称，不追加 `-v` 或读取 URL。禁止 fetch、submodule 更新、LFS 下载、maintenance、内容转换或其他外部动作。

查询期间保留已验证的目录描述符；子进程的工作树必须指向同一目录对象，前后核验对象未替换。只有路径字符串相同或前后存在不能证明对象相同。执行面不能保持目录锚定/no-follow 时，报告不完整并停止写入。

原生主线程可按本节和下节在本地采集；如任务明确使用完整公开发行包的 `workbench/project_identity.py --stable`，先核实该公开工具的实际位置和版本，再以业务项目为工具 cwd/显式目标执行。不能猜维护者路径、要求业务项目自带该文件或把建设仓库脚本复制进去。工程接口只接受其支持的真实采集格式，不能把临时摘要改标签伪装成工具输出。

## 绑定状态

只使用以下绑定状态，不创建近义状态：

| 状态 | 含义 | 是否允许正常主循环 |
|---|---|---|
| `BOUND_GIT` | 路径、Git 身份和临时指纹均可解释 | 是 |
| `BOUND_NON_GIT` | 路径明确，且已可靠证明不是 Git 项目 | 是 |
| `STOP_PATH_MISMATCH` | 首命令路径与用户目标不一致 | 否 |
| `STOP_IDENTITY_AMBIGUOUS` | 物理路径、仓库、worktree 或身份事实矛盾 | 否 |
| `STOP_PROJECT_SWITCH` | 当前任务试图从已绑定项目切换到另一个项目 | 否 |
| `UNKNOWN` | 中断、超时、权限或工具结果无法裁决 | 否 |

Git 绑定另需明确区分 `GIT_WORKTREE`、`BARE_GIT` 和非 Git 目录。`BARE_GIT` 只能只读调查，不能当普通工作树进入实现；非 Git 目录没有 Git fingerprint，不能用 filesystem identity 冒充内容 freshness。无法证明是 Git 还是 non-Git 时保持 `UNKNOWN`。

只有 `BOUND_GIT` 与 `BOUND_NON_GIT` 可以进入 INTAKE。任何停止状态都要给出已验证事实、缺口、风险和一个下一安全步骤。`UNKNOWN` 不自动重试，不换工具或 Provider 猜答案。

## dirty_fingerprint

当前工具的内容格式是 `dirty_fingerprint_schema=3`，identity 外层仍为 schema 2。原生采集也必须覆盖 HEAD、索引及实际文件内容；不能用文件名、mtime 或 Git 对象 ID 单独替代工作树字节。旧格式失效，重新采集，不得仅修改版本字段。

与公开工具使用同一格式时，以 SHA-256 编码域分离的字节段：每段依次为 label、NUL、值的字节长度（ASCII 十进制）、NUL、值、NUL。内容顺序为：

1. `schema` 字段值 `dirty-fingerprint-v3-raw`；`head` 字段是完整 HEAD 或 `<unborn>`。
2. `index-entries` 是 `ls-files --stage -z` 原始字节；`head-tree` 是上述 `ls-tree` 原始字节，unborn 为空。
3. 按索引的相对路径、模式、对象 ID、stage 排序，每个路径只读一次。写入 `tracked-path`；缺失路径写入 `tracked-state=missing`。普通文件/已跟踪链接写入实际 `tracked-mode` 和原始内容的 `tracked-content-sha256` 二进制摘要；链接只读链接文本，不跟随目标。
4. `ls-files --others --exclude-standard -z` 的普通未跟踪文件按路径字节排序，逐项写入 `untracked-path`、`untracked-type=regular`、`untracked-size`（ASCII 十进制）及 `untracked-content-sha256` 二进制摘要。

所有文件读取通过锚定目录逐层 no-follow，核对父目录和文件对象、类型、size/mtime，已跟踪文件还核对 ctime。未跟踪列表和索引读取前后须一致，完整捕获仍做两次。遇到路径逃逸、目录/文件替换、特殊文件、未跟踪符号链接、不可读、读取中变化、超过任务内上限或截断时，指纹只用于诊断，`fingerprint_complete=false`，列不含内容的原因，禁止取得写入或接收资格。

dirty 按索引与 HEAD、工作树原始字节/模式及未跟踪内容保守判断；CRLF、过滤器或可执行位配置可能使它与普通 Git 的干净状态不同。子模块 gitlink 不证明嵌套内容；已有子模块目录无法完整采集时返回 `tracked_submodule_not_fingerprinted` 并停止写入，不能以父仓库对象 ID 冒充完整证明。

不把时间戳、remote URL、绝对 checkout 路径或中文摘要写入内容 digest，不输出或持久化文件原文。对 non-Git 项目明确标记 Git 指纹不适用；写入仍依据已授权的文件范围与现场复核，不假装具备 Git 完整性证明。

## 两层路径身份

绑定同时保留两层事实：

1. `workspace binding`：当前 Codex 工具实际运行的独立 `pwd`。
2. `project identity`：用户原始输入、转换后的逻辑路径、物理路径、filesystem identity 与 Git 拓扑。

两层都成立才可绑定。Windows drive、WSL POSIX、`\\wsl.localhost` 或 `\\wsl$` 形式只有在解析到相同 filesystem identity，并且适用时具有相同 Git common dir/top-level，才可标为同一对象。只凭字符串、remote 或转换器成功不能裁决；不支持时返回 `STOP_PATH_UNSUPPORTED`，多个对象或别名冲突时返回 `STOP_PATH_AMBIGUOUS`。

## 两次完整捕获

绑定、恢复和写入前的稳定快照都要求两次完整捕获。每次都要独立取得路径身份、Git 拓扑、HEAD、dirty 摘要、完整 fingerprint 和适用项目规则；两次结果一致且 `fingerprint_complete=true` 才能作为当前 baseline。

任一捕获不完整时返回 `STOP_DIRTY_INCOMPLETE`。两次结果不同返回 `UNKNOWN / STATE_CHANGED_DURING_CAPTURE`；不自动第三次重试，也不扩大读取范围猜测稳定值。路径或 Git 身份无法证明时返回 `STOP_GIT_IDENTITY_UNKNOWN`。

## 写入前复核

在任何 C-F 级执行、计划批准后的项目写入、额外模型调用、外部操作或发布动作之前，重新执行两次完整捕获，并与当前任务 baseline 比较。至少比较：

- logical/physical path；
- Git top-level、git/common dir 与 worktree ID；
- branch state 和完整 HEAD；
- dirty summary、`dirty_fingerprint` 与 `fingerprint_complete`；
- 当前适用项目指令；
- 审批绑定的项目、环境、动作、文件、次数和有效期。

任一身份字段、digest 或 completeness 变化都使原确认失效。停止并重新说明差异；不得把“只是用户又改了一点”当成自动继承授权的理由。

复核过程中若出现 `UNKNOWN`，消费本次受限调用或操作次数，不重试，不执行写入。

## 用户修改

把任务开始前和任务进行中由其他来源产生的全部改动都视为用户工作。先记录 dirty 基线，再单独声明本次允许修改的文件和行为范围。

不得执行 reset、checkout、restore、clean、覆盖、删除、批量格式化或无关重构来获得干净工作树。发现重叠修改时先理解并协作；无法安全合并时停止请求用户决定。

测试、生成器和格式化命令也可能写文件。运行前判断其副作用，只允许写入已批准范围；运行后重新复核 dirty 状态。

## 项目切换

当前任务已经绑定后，用户或工具若把 cwd、Git top-level、物理路径或写入目标指向另一个项目，立即返回 `STOP_PROJECT_SWITCH`。不同项目之间不得在同一任务内重新绑定。

停止当前任务的全部写入和外部审批消费。要求开启新任务，在新任务中重新显式进入中文跨项目研发主管、独立执行 `pwd`、重新绑定，并清除旧项目的审批、HEAD、dirty 指纹、环境和状态假设。

不得在一个普通任务内同时维护两个写入 worktree，也不得用 Portfolio 或主管摘要反向覆盖项目事实。

## 恢复

上下文压缩、中断、重新打开或从摘要恢复后，把 memory、checkpoint、聊天摘要和旧状态当作定位提示，而不是真相。

重新执行本文件的首命令和完整绑定流程，从当前 cwd、Git、HEAD、worktree、dirty 指纹和项目指令重建状态。审批是否仍有效，以 safety-gates.md 的“审批绑定与生命周期”为唯一详细真源；本文件只证明项目身份，不另行撤销或恢复权限。双捕获证明是 `stable same-task resume` 时，按该生命周期核对未消费的本地包络；已消费的一次性审批永不复用，E/F 外部审批永不恢复。按不可变提交批准的设计或计划不能替代本轮项目绑定、两次完整捕获或新的外部操作审批。

恢复事实与旧摘要冲突时，以用户最新指令、当前项目规则和现场证据为准，记录冲突并停止受影响动作。无法重建为 `BOUND_GIT` 或 `BOUND_NON_GIT` 时，不继续主循环。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
