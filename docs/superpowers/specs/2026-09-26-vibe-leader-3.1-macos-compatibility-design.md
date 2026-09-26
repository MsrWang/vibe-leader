# Vibe Leader 3.1 macOS 兼容设计

日期：2026-09-26

状态：用户已于 2026-09-26 确认原设计；Task 1–5 已实施；用户于 2026-09-27 确认通用 macOS 与 Python 版本合同修订

代码基线：Vibe Leader 3.0.3，提交 `d0e17544584fb6c68254c49b87f4275fa19a71f4`

公开发布基线：[GitHub `MsrWang/vibe-leader` 的 `v3.0.3`](https://github.com/MsrWang/vibe-leader/releases/tag/v3.0.3)，标签与公开 `main` 均指向上述提交；Hugging Face 入口为 [`MsrWang0112/vibe-leader`](https://huggingface.co/spaces/MsrWang0112/vibe-leader)。

## 1. 目标

让 Vibe Leader 在 Apple Silicon Mac 的 Codex App 中完成一条可验证的用户路径：

1. 从 GitHub 下载固定候选包并核对摘要；
2. 对本机 Python、源码、实际 Codex Skill 目录和文件系统能力做预检；
3. 安装或从旧版受控升级；
4. 在 Codex App 新任务中发现并显式调用“中文跨项目研发主管”；
5. 在本地测试项目中使用 3.0.3 新增的证据筛选能力；
6. 保留安装清单、旧版本归档、恢复入口和验收记录；
7. 完成 Apple Silicon Mac 实装验收后，再把同一候选提交晋升为 3.1.0 正式版。

本次兼容工作的核心是替换 Linux 专属的文件系统能力依赖。项目管理规则、授权边界、Jev 的本地证据筛选定位和用户交互方式保持现有合同。

## 2. 非目标

本轮不包含以下工作：

- 不接入外部 Jev API、第三方模型路由或凭据；
- 不把项目级模块编排加入 Vibe Leader 或 Jev；
- 不实现 Windows 原生适配；
- 不为 Intel Mac 宣称已验收支持；
- 不把全部 `workbench/` 高级评测工具一次性迁移到 macOS；
- 不自动安装 Homebrew、Python、Git、Codex App 或其他系统依赖；
- 不自动寻找、创建或猜测 `CODEX_HOME`；
- 不在 Mac 验收通过前替换 3.0.3 的稳定发布地位；
- 不在本地实现阶段自动 push、创建 GitHub Release、更新 Hugging Face 或写入真实 Mac 的 Codex 配置目录。

## 3. 支持矩阵与版本边界

| 环境 | 3.1 目标状态 | 证据要求 |
| --- | --- | --- |
| Windows Codex Desktop + Ubuntu/WSL | 保持 3.0.3 已有行为 | 当前回归与受影响用例通过 |
| macOS 14+ + Apple Silicon M1+ + Codex App | 本轮通用支持目标 | 用户的真实 Mac 完成下载、安装、发现、调用、筛选和恢复观察；公开材料区分设计支持范围与实际验收机型 |
| macOS + Intel | 不在本轮支持范围，预检停止 | 后续需要独立设计、合同测试和真实机器证据 |
| macOS 外接盘、网络盘或不支持安全重命名的文件系统 | 依据能力探针决定 | 能力不完整时失败关闭，不降级到不安全复制或覆盖 |
| Windows 原生 | 不在本轮范围 | 后续独立设计与验收 |

Python 合同分为三个层次：最低兼容版本为稳定版 CPython 3.11，当前支持区间为 `3.11 <= Python < 3.15`，新安装推荐版本和本轮参考验收版本固定为 CPython 3.14.7。候选包只提供标准库实现；低于 3.11 时预检返回 `PYTHON_UPDATE_REQUIRED`，非 CPython、预发布版、无法解析的版本或尚未验证的 3.15+ 返回 `RUNTIME_UNVERIFIED`，并且都停止后续安装。安装器不代用户安装或升级 Python。

版本下限代表代码兼容范围，不代表推荐新装旧版本。Python 3.11 已进入仅安全修复阶段；新用户按文档安装 CPython 3.14.7。3.12、3.13 和稳定版 3.14 继续处于兼容范围，但只有实际进入验证矩阵的组合才能写成“已验证”。

Darwin 后端按 `sys.platform == "darwin"` 和运行时能力选择；本轮预检同时要求 `arm64`，使公开支持目标与 Apple Silicon 验收范围一致。未来扩展 Intel Mac 时需单独修订支持合同，不能仅绕过预检。

本轮参考验收配置固定为 2026 Mac mini（M6）、验收当日的 macOS 27 稳定补丁版本、`arm64` 与 CPython 3.14.7。该配置在真实验收完成前只能称为“参考验收目标”；验收完成后，3.1.0 的公开说明记录实际 macOS 完整版本、构建号、架构和 Python 版本。没有其他机器证据时，只能写“已在该组合验证”，不能把 M6 单机结果外推为所有 macOS 版本或全部 Apple Silicon 设备均已验证。

## 4. 当前阻点

3.0.3 的两个用户路径依赖 Linux 专属能力：

1. `skill/vibe-project-lead-zh/scripts/evidence_filter.py` 要求 `O_PATH`、`/proc/self/fd`、目录相对打开和禁止跟随符号链接。macOS 没有 Linux 的 `/proc/self/fd` 与 `O_PATH` 路径。
2. `scripts/install_skill.py` 通过 `/proc/self/mountinfo` 建立文件系统身份，并以 Linux `renameat2` 实现排他重命名和交换。Darwin 需要不同的文件系统身份与重命名后端。

Python 官方文档把 `O_NOFOLLOW_ANY` 标为 macOS 专属常量，并提供 `os.supports_dir_fd` 供运行时检查；Apple 的 `open(2)` 文档说明 `O_NOFOLLOW` 会拒绝符号链接。Apple 还公开了卷是否支持 `RENAME_EXCL` 与 `RENAME_SWAP` 的能力信息。这些能力可作为 Darwin 后端的基础，但最终可用性必须由目标 Mac 上的实际探针确认。

## 5. 架构

### 5.1 平台选择原则

共享的输入合同、状态码、清单格式、日志结构和安全判定保持平台无关。平台差异集中在两个内部边界：

- 证据筛选器的安全文件打开后端；
- 安装器的文件系统身份与目录切换后端。

不在业务流程中散布 macOS 条件分支。每个入口先取得一份能力记录，再选择 Linux 或 Darwin 后端。未知平台、缺少必要常量、探针结果矛盾或运行中能力漂移，都返回现有失败关闭语义。

Linux/WSL 后端保留当前实现与状态码。Darwin 后端只替换操作系统原语，不改变筛选算法、隐私门、权限门、安装清单或审批合同。

### 5.2 Darwin 安全读取后端

Darwin 后端继续只接受严格 UTF-8 的相对路径，并拒绝绝对路径、空段、`.`、`..`、反斜杠、冒号、控制字符、重复路径、符号链接和特殊文件。

安全读取顺序固定为：

1. 以禁止跟随链接的标志打开项目根目录，记录根目录文件描述符及 `fstat` 身份；
2. 对相对路径逐段使用 `dir_fd` 打开目录，每次只处理一个路径组件；
3. 直接从已锚定父目录打开叶子文件，使用 `O_NOFOLLOW_ANY`，并在缺少该能力时停止；
4. 用 `fstat` 确认叶子是普通文件，读取前后比较 device、inode、mode、size、mtime 和 ctime；
5. 重新打开来源并比较身份与完整摘要后，才按字节区间回读入选片段；
6. 每次批次结束前再次核对根目录身份。

Darwin 后端不通过字符串 `realpath` 重新证明已打开文件，也不依赖 `/proc/self/fd` 或 `O_PATH`。根目录文件描述符、逐段 `dir_fd` 遍历、禁止跟随链接和读取前后身份比较共同构成边界证明。

筛选输出继续隐藏真实路径，只返回 `source_ref`、候选 ID、字节范围和已核验文本。安全能力不足时保持 `status=UNKNOWN`、`mode=UNKNOWN` 和 `fallback_reason=SECURE_READ_UNAVAILABLE`；不能退回普通 `open()` 或路径字符串检查。

### 5.3 Darwin 安装器后端

安装器增加 Darwin 文件系统身份实现，通过本机 `statfs` 能力取得并规范化：

- 目标目录的 device；
- 挂载点；
- 文件系统类型；
- 挂载标志摘要。

取得的 device 必须与 `lstat` 一致。字段缺失、结构不兼容或前后漂移时返回 `filesystem_identity_unavailable`，不调用外部 `mount`、`diskutil` 或未审计脚本猜测结果。

Darwin 目录切换后端优先使用 `renameatx_np` 或等价的 Darwin 原生接口，并把能力限定为现有三态：

- `EXCHANGE_SUPPORTED`：同一目标文件系统上的专用目录对完成交换、内容核对、反向交换和再次核对；
- `NOREPLACE_ONLY`：排他重命名探针通过，但交换能力未通过；升级使用现有的归档后激活流程，并保留非原子窗口；
- `UNKNOWN`：任何原语、文件系统或回读结果不能确定，停止升级。

能力探针必须在目标 `skills` 文件系统的专用临时目录中进行，不接触活动 Skill。探针结束时核对两侧身份和内容；清理不完整时保持失败现场并停止，不自动重试。网络盘、外接盘和未来文件系统不能仅凭 `sys.platform` 获得支持。

首次安装、核验、升级、检查升级和版本恢复继续使用现有 manifest、journal、receipt 与摘要合同。Darwin 后端不得用覆盖式 `rename`、先删除目标再移动或普通复制替代排他操作。

### 5.4 预检与 Codex App 发现

安装器增加只读 `preflight` 入口，输出结构化结果并检查：

- 平台、CPU 架构和 Python 版本；
- 源 Skill 的精确文件清单与摘要；
- 用户明确提供的 `skills-root` 是否存在、规范化且不含符号链接；
- 目标 Skill 与安装状态目录是否已存在；
- 文件系统身份是否可取得；
- 本次应走首次安装还是受控升级；
- 仍需在写入阶段执行的能力探针。

预检不创建目录、不写探针、不修改真实 `CODEX_HOME`，也不把“常见位置”当成当前 Codex App 的实际发现根。OpenAI 官方资料把 `~/.codex/skills` 定义为用户级 Skill 位置；本项目只把它作为标准候选。执行者仍须显式提供本次 `CODEX_HOME` 或 `skills-root`，预检记录选择来源，安装器不搜索相邻目录或自动改用另一份配置。

安装前只能证明“用户选择了一个符合官方用户级规则或显式覆盖规则的目标目录”。Codex App 是否实际使用该目录，需要安装后的唯一 locator 和新任务显式调用来证明；设计不要求用尚未安装的 Skill 反向证明安装前路径。

安装后验证分成四个结论：

1. 文件已安装；
2. manifest 与候选一致；
3. Codex App 发现唯一且正确的 Skill locator；
4. 新任务显式调用后行为正确。

前一个结论不能替代后一个结论。

### 5.5 候选包与安全解包

仓库增加一个标准库发行工具 `scripts/release_archive.py`，提供 `build`、`verify` 和 `extract` 三个子命令。它负责生成、核验并安全解包单一顶层目录的确定性 ZIP，避免把人工压缩或 GitHub 自动生成的源码包当成受控候选。发行资产沿用现有命名习惯，固定为 `Vibe-Leader-3.1.0-GitHub.zip`、`release_archive.py` 和 `SHA256SUMS.txt`。

`build` 只能读取调用者指定的冻结提交对象，不能从当前工作树取文件。包内容固定为该提交中 `git ls-tree` 列出的普通跟踪文件，再加一份生成的 `RELEASE-MANIFEST.json`；符号链接、子模块、特殊模式和未跟踪材料一律拒绝。它使用 `ZIP_STORED`，并固定文件顺序、顶层目录名、时间戳、权限位和影响 ZIP 字节的其余头字段。生成的清单绑定源提交、文件路径、文件摘要和构建格式版本。`verify` 只接受该存储方法和格式版本，并在解包前检查：

- 归档摘要与 `SHA256SUMS.txt` 一致；
- 没有绝对路径、`..`、反斜杠、NUL 或控制字符；
- 没有符号链接、特殊文件、重复条目或覆盖已有目标；
- 没有 Unicode 规范化冲突、大小写折叠冲突或单一 `SKILL.md` 规则冲突；
- 文件数、单文件大小和总解压大小均在固定上限内；
- `RELEASE-MANIFEST.json`、归档内源码树、安装器、许可证和可安装 Skill 清单与冻结提交一致。

`extract` 复用同一验证结果，逐项使用排他创建写入新建空目录，不调用未经约束的 `extractall`，也不覆盖已有条目。候选 prerelease 与正式版必须上传同一个已冻结 ZIP 文件；正式发布不得重新打包。两个发布入口分别回读资产 SHA-256，并与 Mac 实际下载文件比较。

GitHub Release 同时上传从冻结提交复制的独立 `release_archive.py` 和 `SHA256SUMS.txt`。该摘要文件列出 ZIP 与独立脚本的 SHA-256。Mac 先用系统工具计算下载文件摘要，并同时核对 `SHA256SUMS.txt` 与 GitHub Release 回读的资产摘要，再执行独立脚本的 `verify` 和 `extract`。该流程防止损坏、不一致资产或不安全归档条目；它不声称能抵御 GitHub 账号或发布渠道整体失陷。

GitHub ZIP 不包含 `.git` 目录。安装器需要候选提交时，先使用现有 Git 身份路径；解包树没有 Git 身份时，只接受固定顶层位置的 `RELEASE-MANIFEST.json`。清单中的源提交、Skill 前缀、文件 mode、size 和 SHA-256 必须与当前候选树完全一致，否则返回来源不可用，不进入升级准备。安装器不向其他祖先搜索清单，也不把未经过发行工具验证的提交字符串当作来源证明。

### 5.6 发布目标绑定

开发工作树的 `origin` 不是公开发布目标。所有候选和正式 GitHub 操作都必须显式绑定 `MsrWang/vibe-leader`，不得根据当前工作树 remote 自动推断。发布前回读公开仓库、标签解析提交和资产摘要；冻结提交必须已经以本次批准覆盖的方式进入该公开仓库。

Hugging Face 同步只绑定 `MsrWang0112/vibe-leader`。GitHub 候选、GitHub 正式版和 Hugging Face 同步分别记录目标、动作、提交、资产摘要与回读结果，任何一个入口的成功都不能替代另一个入口。

## 6. 文件与兼容范围

实施计划应把修改限制在下列类别：

- `skill/vibe-project-lead-zh/scripts/evidence_filter.py`；
- `scripts/install_skill.py`；
- `scripts/release_archive.py`；
- 对应的聚焦测试；
- macOS 安装、限制、验收和 3.1 发布说明；
- 发行包清单、版本号和校验资料中确实受影响的文件。

优先在现有两个运行脚本内部形成清晰的平台后端边界，避免引入新的运行时依赖。如果实施调查证明必须增加辅助模块，先修订本设计和实施计划，再更新安装清单、恢复合同、包文件计数和升级测试；不能在实施中静默扩大文件范围。

本轮原则上不改 `SKILL.md` 的角色、权限和主循环内容。只有 macOS 实际验收发现现有文字会导致错误使用时，才单独提出最小 Skill 规则变更；该变化需要重新走 writing-skills 的行为验证。

## 7. 错误处理与安全规则

下列情况统一失败关闭：

- Darwin 能力不完整或调用返回矛盾结果；
- 源码、候选、目标 Skill、manifest 或旧归档发生漂移；
- `skills-root`、目标或状态目录包含符号链接或越过预期文件系统；
- 读取中来源、父目录或项目根发生变化；
- 切换能力为 `UNKNOWN`；
- Codex App 发现不到目标、发现重复项、locator 不符或新任务未加载候选；
- GitHub 候选摘要与 Mac 下载文件不一致；
- ZIP 包含不安全条目、规范化冲突、大小超限或发行清单外文件；

失败时保留 journal、staging、归档和收据等可恢复证据。不能通过删除现场、放宽符号链接检查、普通覆盖、换路径或再次执行来制造成功。

## 8. 验证策略

### 8.0 固定参考配置门

冻结候选前不再要求读取用户个人 Mac 的环境事实。候选构建使用以下固定、去个人化的参考配置合同：

- 通用支持目标：macOS 14+、Apple Silicon M1+、`arm64`；
- 参考验收机：2026 Mac mini（M6）；
- 参考验收系统：macOS 27 的验收当日稳定补丁版本；
- 最低兼容运行时：稳定版 CPython 3.11；
- 新安装推荐与参考验收运行时：CPython 3.14.7；
- 目标位置：用户在真实验收时明确提供的 `CODEX_HOME` 或 `skills-root`，预检不得提前搜索、创建或猜测。

该门只固定候选设计和文档基线，不证明用户的 Mac 已就绪，也不证明安装、发现或兼容成功。真实 macOS 版本、构建号、架构、Python、Codex App 版本、目标目录和文件系统事实全部推迟到候选资产下载后的 Mac 技术验证与实装验收中读取。

### 8.1 WSL 本地技术验证

实施时先为缺失的 Darwin 行为建立失败测试，再写最小实现。WSL 可完成：

- 后端选择和能力记录的单元测试；
- 注入 Darwin 能力后的安全读取合同测试；
- 安装器的 Darwin `statfs`、排他重命名、交换、失败和清理合同测试；
- Linux/WSL 现有证据筛选与安装路径回归；
- 精确包清单、升级、恢复、隐私扫描和文档一致性检查。

模拟 Darwin 原语只能证明合同与分支逻辑，不能证明 macOS 内核、APFS、Codex App 或 Apple Silicon 真实兼容。

### 8.2 Apple Silicon Mac 技术验证

候选包在用户的 Mac 本地驱动器上完成。所有技术检查必须针对下载并通过 `release_archive.py verify` 的 ZIP 解包结果，不能用另一个 Git checkout 代替候选资产：

1. 记录 macOS 版本、`arm64` 架构、Python 版本和候选 SHA-256；
2. 运行只读预检；
3. 在隔离临时目录运行证据筛选的正常文件、符号链接拒绝、特殊文件拒绝和读取变化测试；
4. 在隔离临时 `CODEX_HOME` 运行首次安装、核验、升级探针、检查升级和恢复测试；
5. 核对所有临时目录清理结果，任何残留或未知单列报告。

Mac 任务生成一份不含用户名、绝对路径、凭据或业务数据的验收收据。收据至少绑定候选提交、ZIP SHA-256、macOS/Python/架构、预检结果、聚焦检查结果、安装或升级收据摘要、Codex App 发现结果和恢复收据摘要。技术工具不能代填用户观察；当前 Mac 任务中的真实用户消息单独形成观察证据并与收据摘要绑定。收据只是发布证据，不创建第二套项目状态或授权。

### 8.3 Codex App 实装验收

隔离临时目录中的 macOS 兼容验证为 U2。真实 Codex App 的 `CODEX_HOME` 写入会改变当前宿主环境，本项目固定按 E 级动作和 U3 验收处理：动作前批准、动作后用户观察、候选回滚验证三者缺一不可。

验收场景固定为：

1. 从 `MsrWang/vibe-leader` 的 GitHub 候选 Release 下载 `Vibe-Leader-3.1.0-GitHub.zip`、独立 `release_archive.py` 和 `SHA256SUMS.txt`；
2. 核对下载摘要与发布摘要一致，并在新空目录中完成安全解包验证；
3. 显式选择本次 `CODEX_HOME` 或 `skills-root`，并核对当前真实事实符合固定参考配置合同；
4. 取得新的精确安装或升级批准后执行一次；
5. 核对安装 manifest 与候选树一致；
6. 重启或刷新 Codex App，并在新任务中确认唯一 locator、显示名和显式调用；
7. 在不含业务数据的本地测试项目中完成项目绑定和一次证据筛选；
8. 由用户确认界面中能发现、能调用、没有 Linux 专属错误，结果符合预期；
9. 使用新的恢复批准把候选回滚到安装前状态：首次安装使用 `rollback` 返回未安装状态，旧版升级使用 `restore-version` 恢复原活动版本；
10. 刷新 Codex App 并在新任务中确认原版本或未安装状态已经恢复。

任何一步失败都停止晋升正式版。修复后生成新的提交与 `rc.2`，不修改已发布 `rc.1` 的资产或摘要。

## 9. 发布流程

发布按六个独立门处理：

1. **本地候选**：在当前隔离分支完成实现、测试、两遍审查、确定性打包和摘要；不联网发布。
2. **GitHub 候选**：取得新的 E 级批准后，在 `MsrWang/vibe-leader` 创建公开 prerelease `v3.1.0-rc.1`。标签绑定固定提交，上传 `Vibe-Leader-3.1.0-GitHub.zip`、独立 `release_archive.py` 与 `SHA256SUMS.txt`。3.0.3 继续是稳定版。
3. **Mac 实装验收**：用户在 Apple Silicon Mac 下载候选并按第 8.3 节完成真实观察。Mac 上的实际安装或恢复使用该 Mac 任务中的独立 E 级批准。
4. **GitHub 正式发布**：Mac 候选验收通过后，取得新的 E 级批准，让 `MsrWang/vibe-leader` 的 `v3.1.0` 标签指向通过验收的同一提交，并逐字节复用候选阶段冻结的 ZIP、独立脚本与摘要文件；不得重新打包。
5. **Mac 稳定版实装**：使用新的 E 级批准，从正式 Release 下载同一 SHA-256 资产并安装。完成 manifest、Codex App 新任务显式调用和恢复材料复核后保留稳定版活动状态。若环境、安装前状态或资产摘要与候选验收不同，必须重新执行实际恢复演练。
6. **Hugging Face 同步**：Mac 稳定版实装通过后，再以独立批准更新 Hugging Face 说明并完成发布后回读。

如果候选验收要求改代码、文档、包内容或安装步骤，必须进入新的候选提交与 prerelease。不能把“变化很小”当作继续使用旧 Mac 验收结果的理由。

## 10. 回滚

- 开发阶段：3.0.3 的发布标签、资产、GitHub 最新稳定版和 Hugging Face 页面保持不变；本地候选分支可单独停止。
- Mac 临时测试：只清理测试自己创建且已核对的临时目录；不删除未知残留。
- Mac 真实安装：使用安装器成功收据和原版本归档准备新的恢复动作。恢复需要新的精确批准，不能复用安装批准。
- GitHub 候选：保留 prerelease 作为不可变审计记录；发现问题时发布 `rc.2`，不覆盖 `rc.1`。
- 正式发布：只有通过候选的同一提交和已冻结资产才能晋升。若正式发布后发现新问题，发布后续修订版，不改写历史标签或资产。
- Mac 稳定版：候选阶段必须实际完成一次恢复演练。稳定版安装后重新绑定当前状态和恢复材料；环境或摘要漂移时，旧恢复证据失效。

## 11. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 当前 WSL 无法证明 Darwin 原语真实行为 | WSL 只做合同测试；Apple Silicon Mac 完成真实技术验证 |
| Codex App 的实际 Skill 根与常见路径不同 | 预检要求显式 `skills-root`，安装后按真实 locator 验证 |
| APFS 以外文件系统不支持安全交换 | 在目标文件系统探针；降为 `NOREPLACE_ONLY` 或停止 |
| Mac 的 Python 低于 3.11 | 预检返回 `PYTHON_UPDATE_REQUIRED` 并停止；文档要求升级，不自动安装依赖 |
| Mac 使用非 CPython、预发布版或尚未验证的 3.15+ | 预检返回 `RUNTIME_UNVERIFIED`；先完成独立验证再扩大支持范围 |
| macOS 读取实现引入安全退化 | 保持逐段 `dir_fd`、禁止跟随链接、身份复核和失败关闭 |
| ZIP 解包覆盖文件或产生路径逃逸 | 使用确定性发行工具，解包前验证所有条目，只写新空目录 |
| macOS 默认大小写不敏感导致条目碰撞 | 构建与验证都拒绝 Unicode 规范化和大小写折叠冲突 |
| 平台适配意外改变主管定位 | 不改项目管理主循环，不接外部 Jev，不增加模块调度 |
| 候选发布被误当稳定版 | 使用 GitHub prerelease，3.0.3 保持最新稳定版直到 Mac 验收通过 |
| 候选与正式版字节漂移 | 两个标签指向同一通过提交，正式资产复用同一确定性包摘要 |
| 开发 remote 被误当作公开发布仓库 | 所有发布命令显式绑定 `MsrWang/vibe-leader`，并回读仓库、标签提交和资产摘要 |

## 12. 完成定义

3.1.0 只有在以下条件全部满足时才可标为正式完成：

- Linux/WSL 受影响回归通过；
- Darwin 合同测试与 Apple Silicon Mac 原生技术检查通过；
- 候选 ZIP 通过安全归档验证，并能从冻结提交确定性重建为同一摘要；
- `MsrWang/vibe-leader` 中的候选包、独立验证脚本、摘要、Git 标签和 Mac 下载文件一致；
- Mac Codex App 发现并显式调用唯一候选 Skill；
- 本地证据筛选在 Mac 上正常工作，并拒绝符号链接与特殊文件；
- 安装、升级或首次安装的 manifest、journal、归档与恢复证据完整；
- 用户完成真实发现、调用和筛选观察，并确认候选已经回滚到安装前状态；
- 正式 Release 的同一资产已在 Mac 安装为活动稳定版，并完成新任务显式调用与恢复材料复核；
- 没有未裁决的 Critical/High、外部状态 `UNKNOWN` 或候选字节漂移；
- 正式 GitHub 发布和 Hugging Face 更新分别取得当时的新批准并完成发布后回读。

实现完成、WSL 测试通过、GitHub 候选上传和 Mac 安装成功分别是不同检查点，不能互相替代。

## 13. 参考资料

- [Python `os` 文档](https://docs.python.org/3/library/os.html)：平台能力常量与 `os.supports_dir_fd`。
- [Python 3.14.7 发布说明](https://www.python.org/downloads/release/python-3147/)：本轮新安装推荐与参考验收运行时。
- [Python 3.11.16 发布说明](https://www.python.org/downloads/release/python-31116/)：3.11 的安全维护阶段和支持边界。
- [OpenAI macOS App 系统要求](https://help.openai.com/en/articles/9395554-what-are-the-system-requirements-for-the-chatgpt-macos-app)：包含 Codex 的当前桌面 App 要求 macOS 14，并支持 Apple Silicon M1+。
- [Apple Mac mini (2026) 技术规格](https://support.apple.com/en-us/128108)：本轮 M6 参考验收机型。
- [Apple `open(2)` 文档](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/open.2.html)：禁止跟随符号链接的系统语义。
- [Apple 排他重命名能力](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming)：`RENAME_EXCL` 的卷能力。
- [Apple 交换重命名能力](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsswaprenaming)：`RENAME_SWAP` 的卷能力。
- [OpenAI Skills 文档](https://developers.openai.com/api/docs/guides/tools-skills)：Skill 目录、单一 `SKILL.md`、ZIP 顶层目录和安全边界。
- [OpenAI Skill 评测指南](https://developers.openai.com/blog/eval-skills)：仓库级与用户级 Skill 位置、显式调用和真实目录评测。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
