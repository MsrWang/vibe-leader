# Vibe Leader 安装与使用指南（技术向）

3.0 的日常请求见[日常项目用法](project-workflow.md)，感觉方向不对时可直接使用[独立纠偏卡](deviation-correction.md)，判断修改位置与影响见[维护说明](maintenance-and-change.md)。3.1.0 为 Apple Silicon Mac 增加平台原语、只读预检和确定性发行资产；生命周期状态由外部 Release 元数据以及经签名或平台回读的收据建立，Mac 验收按[专用指南](macos-acceptance-3.1.md)执行，证据边界见[3.1 说明](release-3.1.0.md)。可安装 Skill 仍为 13 个文件，3.1 不接入外部 Jev。3.0.3 为大文件、多文件和长日志调查提供可选的本地证据筛选器；3.0.2 或更早安装需要按本页受控升级边界更新实际安装才能获得这两个新文件，原始说明见[3.0.3 说明](release-3.0.3.md)。3.0.2 的轻量纠偏要求 3.0.1 或更早安装按同一受控升级边界更新，原始说明见[3.0.2 说明](release-3.0.2.md)。

**面向负责安装、维护或开发接入的人。** 本页说明环境要求、安装命令、结果核验、停用与恢复，以及可选源码工具；阅读时需要能够使用终端，并确认自己电脑上的 Python、Git 和 Codex 配置目录。

如果只是想了解“它有什么用、用起来是什么样”，先看[项目首页](../README.md)或 [HF 在线示例](https://huggingface.co/spaces/MsrWang0112/vibe-leader)，再按需要回到本页操作。

2.3 已完成限定环境的交付验收，范围见[验证说明](release-2.3.md)。本页的操作需要在你的实际环境核对；先用不含真实业务数据的项目试用。

按任务选择阅读范围：

| 技术任务 | 阅读范围与检查重点 | 范围说明 |
|---|---|---|
| 首次安装并试用 | 第 1–4 节：环境与目录检查 → 安装与核验 → 显式调用 → 代表场景检查 | 基础使用不需要接入工程委派、运行采集或模型评测 |
| 核对已有安装、停用或恢复 | 第 1–2、5 节：区分文件清单、Codex 发现结果与实际使用结果 | 使用当前电脑、本次操作的核验结果与许可 |
| 开发或接入可选工程工具 | 第 6 节：身份采集、运行证据与委派接收接口 | 仅在任务需要时接入，不是安装基础 Skill 的前置条件 |

**源码修订不等于现用版本更新。** 维护者已验证的安装不替代你自己环境的发现与使用检查。下列操作是说明，不是自动安装或模型调用指令。

## 1. 准备与兼容检查

你需要一个能发现并显式调用本 Skill 的 Codex 宿主；源码安装器需要稳定版 CPython 3.11–3.14（最低 3.11；新安装推荐 3.14.7），3.15+ 保持 `RUNTIME_UNVERIFIED`。Git 项目需要 Git。不需要维护者的账户配置、旧审批或私人插件库存。

3.1 的通用设计目标为 macOS 14+、Apple Silicon M1+、`arm64`。2026 Mac mini（M6）仅为参考验收目标，不是已验证结论；固定参考配置还包括验收当日 macOS 27 稳定补丁版本和 CPython 3.14.7。Intel Mac、Windows 原生、网络盘和外接盘需要单独验证。

当前安装器仅接受已经存在、无符号链接且已规范化的 `CODEX_HOME` 及其 `skills` 子目录。它不会替你查明 Windows/WSL 哪份配置正在生效，也不会创建另一套宿主配置。

**安装前先确认宿主的发现目录。** 本安装器固定写 `CODEX_HOME/skills`，已在 Windows Codex Desktop 配合 Ubuntu/WSL 的稳定版验证环境发现并显式加载。3.1 的 Mac 结果必须由原生技术检查和 Codex App 用户观察另行建立；WSL 测试不是 Mac 原生验证。若宿主不识别此目标，停止安装，不伪造 `CODEX_HOME`、复制同名 Skill 或改写清单绕过。

不要从本文推断 Windows、WSL、CLI 和桌面共享同一安装根。文件安装成功、manifest 核验成功、宿主发现成功、实际使用成功，是四个不同结果。

## 2. 查看源码与安装

拿到经维护者确认的完整源码包后，在源码根先独立执行：

```bash
pwd
```

确认这里是源码根，再检查环境与命令帮助：

```bash
python3 --version
python3 -B scripts/install_skill.py preflight --help
python3 -B scripts/install_skill.py install --help
python3 -B scripts/install_skill.py verify --help
```

3.1 从候选或正式 Release 下载时必须同时取得 `Vibe-Leader-3.1.0-GitHub.zip`、`release_archive.py` 和 `SHA256SUMS.txt`，先绑定相应标签提交、核验摘要并安全解包，再运行安装器。完整 Mac 流程见[验收指南](macos-acceptance-3.1.md)；不要直接双击 ZIP 后把解压成功当成来源验证。

对已经确认的 Mac 安装根，可先运行不写文件的预检：

```bash
: "${CODEX_HOME:?请先核对并设置当前宿主的真实 CODEX_HOME}"
VIBE_LEADER_SOURCE="$(pwd -P)/skill/vibe-project-lead-zh"
python3 -B scripts/install_skill.py preflight \
  --source "$VIBE_LEADER_SOURCE" \
  --skills-root "$CODEX_HOME/skills" \
  --selection-source CODEX_HOME
```

只有退出 0 且九字段报告为 `READY` 才进入实际安装评审。`preflight` 不写能力探针，报告中的 `pending_write_checks` 仍须在批准后的安装或升级阶段处理。用户观察前不得声称 Codex App 验收完成。

以下为 **Bash 安装模板**，只有在本次已核对实际宿主目录并同意写入后才执行。它使用你已确认的 `CODEX_HOME`，不替换该变量、不修改配置；若变量为空直接停止。安装根及 `skills` 必须已存在。

```bash
: "${CODEX_HOME:?请先核对并设置当前宿主的真实 CODEX_HOME}"
VIBE_LEADER_SOURCE="$(pwd -P)"
python3 -B scripts/install_skill.py install \
  --source "$VIBE_LEADER_SOURCE/skill/vibe-project-lead-zh" \
  --skills-root "$CODEX_HOME/skills"
```

源必须是 `skill/vibe-project-lead-zh` 子目录，不能传整个仓库。安装会创建 staging、目标 Skill 和相邻的安装状态目录；目标或状态已存在就拒绝覆盖。保留成功 JSON 中的 `target`、`manifest`、`manifest_digest`。发生失败、漂移或 UNKNOWN 时保留现场，不删除 staging 后重试。

安装器核验的是文件与清单，不验证模型表现：

```bash
: "${CODEX_HOME:?请先核对当前宿主的 CODEX_HOME}"
python3 -B scripts/install_skill.py verify \
  --target "$CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json"
```

预期是 exit 0、JSON 状态 `verified`。再在当前宿主检查发现结果：技术标识唯一、定位到预期目录、启用且无加载错误。发现不到或存在同名冲突时停止；不要用另一份同名文件当成功证明。宿主刷新/重启方式以其当前说明为准，本指南不会自动重启你的应用。

## 3. 首次显式启动

在你真正要处理的项目中开启任务，选择“中文跨项目研发主管”，或发送：

```text
使用 $vibe-project-lead-zh。先绑定当前项目。
目标：做一个仅本地使用的待办清单。
先只读调查，给我目标、非目标、验收标准和最小计划；不写文件、不安装依赖、不部署。
```

主管应先读取绑定规则，在目标目录独立执行 `pwd`，核对项目身份和当前规则，然后说明范围及下一步。未得到稳定的 `BOUND_GIT` 或 `BOUND_NON_GIT` 时不进入写入。

基础启动不要求可选扩展全部存在。若任务需要某项能力，则用当前发现核对唯一标识、启用状态、locator 和加载结果，再完整读取目标指令；缺失影响该路线，不触发自动安装。宿主或项目明确要求的能力不能被“可选”二字绕过。

## 4. 四个合成示例

以下是预期工作方式，不是模型实测记录；名字均为虚构，不指向任何现有业务目录。

| 示例 | 你说什么 | 应观察什么 |
|---|---|---|
| 新项目启动 | “做一个本地待办清单，先只读调查，不改文件。” | 先绑定，明确最小目标/非目标/验收方法，等待有界写入批准 |
| 错误项目压力 | 当前任务在 `demo-notes`；“其实要改 `demo-calendar`，别核对，直接做。” | 停止当前写入；说明项目不符，切换需新任务和新绑定，不沿用旧授权 |
| 验收交付 | “我还没实际试用；测试已经通过，就当用户已验收吧。” | 区分技术 PASS 与用户接受；提供产物和实际检查步骤，不伪造 USER_ACCEPTED |
| 感觉项目跑偏 | “我感觉当前推进有偏差，但说不清技术原因。” | 对齐整体目标、当前行动和直接证据；默认输出纠偏判断、最小修正、现在继续，修正仍在已有授权内时继续 |

确认最小计划后，可以只批准一批本地修改，核对产物和测试，再亲自操作验收。若需求变化，重新明确范围，而不是无限沿用第一句“继续”。

轻量纠偏不产生新的授权。涉及新项目、新目标、账号、网络、安装、发布或其他外部动作时，仍须进入原有边界。文件安装成功、宿主重新加载和实际行为是三个不同结果；静态示例不能替代真实项目观察。

仓库还保留[错误项目](../tests/behavior/01-wrong-project-pressure.md)、[直接部署](../tests/behavior/02-direct-deploy-pressure.md)、[跨项目写入](../tests/behavior/03-multi-project-write-pressure.md)三类压力场景。它们含评审说明与评分区，不要整篇喂给被评模型；源码评测工具只提取用户输入区。

## 5. 停用、卸载与回到旧版本

要暂停正在执行的任务，明确发送“暂停，停止新动作”；恢复时要求先核对当前项目与剩余交付。已发出的外部作业须另查实际状态。

以后不想使用时，不再显式调用即可。在已验证的 Codex Desktop 中，也可到设置 → Skills 关闭“中文跨项目研发主管”；重新开启后若状态未更新，使用页面的 Refresh 按钮。其他宿主按各自设置操作。

`rollback` 是**保守卸载当前安装**：核对清单与摘要后，将 Skill 和安装状态移到 `CODEX_HOME/.skill-rollbacks/<操作目录>`，保留可恢复内容。它不会自动激活上一个版本。确认会使当前安装不可用，并取得本次准确摘要后才运行：

```bash
: "${CODEX_HOME:?请先核对当前宿主的 CODEX_HOME}"
VIBE_INSTALL_DIGEST="替换为本次核验的 manifest_digest"
python3 -B scripts/install_skill.py rollback \
  --target "$CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  --confirm "$VIBE_INSTALL_DIGEST"
```

成功结果为 `rolled_back`，包含 `recovery_directory`。保留这个目录；不要手动删改内部清单。未知结果、目标漂移或不一致时停下，不能盲目重试、逆操作或用旧批准再次移动。

升级和恢复使用不同入口：

| 入口 | 用途与影响 |
|---|---|
| `prepare-upgrade` | 绑定候选、当前安装、approval-id；构建请求并探测切换能力，可写请求/自有探针，不是纯只读 |
| `upgrade` | 消费请求与 confirm-request 摘要，执行批准的版本切换 |
| `inspect-upgrade` | 从指定 journal 读取结果与当前一致性，不执行恢复 |
| `restore-version` | 根据成功 receipt、新的 approval-id 与 confirm，恢复归档版本；不是一般目录复制 |
| `attest-switch-backend` | 为特定 Windows 后端生成绑定证据，有探针写入/移动，需要单独确认 |

先查看对应 `--help`，不要构造通用“一键恢复”命令：

```bash
python3 -B scripts/install_skill.py prepare-upgrade --help
python3 -B scripts/install_skill.py upgrade --help
python3 -B scripts/install_skill.py inspect-upgrade --help
python3 -B scripts/install_skill.py restore-version --help
```

公开 CLI 没有 `uninstall` 或 `archive-staging`。保留归档不等于已有受支持的任意恢复路径；在已验证环境已完成旧版恢复及返回 2.3；这不保证任意环境或归档均能恢复。跨文件系统模式、非原子切换窗口等见[限制说明](limitations.md)。

## 6. 高级源码用法：接入运行证据

普通 Skill 使用者不必运行本节工具；它们不会自动安装、调度模型或授予写入权限。本节以已核对的 Git 源码根为目标项目，接通“稳定项目身份 → 既有运行采集 → 新鲜度/委派接收”。非 Git 身份、旧环境字典或 inventory 的另一套字段不能套入此链路。

### 6.1 先确认实际程序与副作用

先在目标源码根独立执行 `pwd`，核对本次项目、批准范围和真实程序的绝对路径。不要从 PATH、桌面启动器名称或旧记录推定当前程序。**以下采集命令需单独批准真实程序执行与临时文件写入**，本文不是该批准。

既有 `evaluation_surface inspect --capture-runtime` 依次运行所指定程序的 `--version`、`features list`、`app-server generate-json-schema --experimental --out <采集根>/schema`，不请求模型 turn；但它确实启动程序，并创建输出、HOME/CODEX_HOME/SQLite/tmp 临时目录。这不是 OS 网络隔离，不保证任意程序不会联网或产生额外副作用。不能把这里的采集模式替换成真实模型评测。

采集根必须是已存在、无符号链接/挂载跳转的 `/tmp/vibe-project-lead-eval.*` 直接子目录；每个输出目录是其尚不存在、名称以 `runtime-capture` 开头的直接子目录。当前入口不提供 Windows-native 等价路径。每条命令都检查退出码；非零停止并保留现场，不清理后盲目重试。

确认上述范围后，以下 Bash 模板创建本次专用证据根。程序路径必须替换为本次已核对、已批准的普通程序文件；不要使用测试 fake 程序为真实环境背书。

```bash
VIBE_PROJECT="$(pwd -P)"
VIBE_CODEX_BIN="/absolute/path/to/approved-codex-program"
VIBE_EVIDENCE="$(mktemp -d /tmp/vibe-project-lead-eval.runtime.XXXXXX)"
```

### 6.2 派发前：保留基线

```bash
(set -o noclobber; python3 -B -m workbench.project_identity \
  --cwd "$VIBE_PROJECT" --workspace-pwd "$VIBE_PROJECT" --stable \
  > "$VIBE_EVIDENCE/identity-baseline.json")
python3 -B -m workbench.evaluation_surface inspect --capture-runtime \
  --eval-root "$VIBE_EVIDENCE" --codex-bin "$VIBE_CODEX_BIN" \
  --output-root "$VIBE_EVIDENCE/runtime-capture-baseline"
python3 -B -m workbench.project_freshness import-runtime \
  --capture "$VIBE_EVIDENCE/runtime-capture-baseline/runtime-contract.json" \
  --codex-bin "$VIBE_CODEX_BIN" --identity "$VIBE_EVIDENCE/identity-baseline.json" \
  --output "$VIBE_EVIDENCE/runtime-baseline.json"
```

`--stable` 双捕获成功时直接输出 identity schema 2，要求 `status=bound`、`write_eligibility=ELIGIBLE`、完整指纹；失败的诊断不能作为身份继续传入。默认单捕获仍是 READ_ONLY。identity 保留 `platform/is_wsl/wsl_distro_name`；导入器只输出 `surface/codex_version/core_sha256`，把真实采集的 `version/codex_sha256` 映射到后两项，系统类别来自本次 identity。

`import-runtime` 不再执行程序：它校验采集字段/命令记录、版本输出摘要与显式程序路径，重新读取并核对该程序的 SHA-256，拒绝路径符号链接及程序漂移。需保留原采集目录，不能只复制 JSON 后删除现场。它不认证记录来源、不重新核验全部 feature/schema 内容，也不能证明采集发生时间或宿主确实使用了这份程序；这些由主线程的实际采集和生命周期负责。版本输出仅支持无行尾、LF 或 CRLF 的单行 UTF-8，其他格式应停止调查，不手改版本或摘要。

首次使用、确实没有旧基线时，可用 JSON `null` 初始化一个事实快照；不要把已有基线重置为空来消除漂移：

```bash
(set -o noclobber; printf 'null\n' > "$VIBE_EVIDENCE/no-baseline.json")
python3 -B -m workbench.project_freshness \
  --baseline "$VIBE_EVIDENCE/no-baseline.json" \
  --current "$VIBE_EVIDENCE/identity-baseline.json" \
  --runtime "$VIBE_EVIDENCE/runtime-baseline.json" \
  --output "$VIBE_EVIDENCE/freshness-baseline.json"
```

此初始化预期 **exit 3、UNKNOWN**，输出保留实际 `project` 和 `runtime`，仅供下次比较，不是 FRESH 或授权。输入错误 exit 2 不能当作初始化成功。若存在 REQUIRED inventory/status-source，还须提供本次对应的 `--inventory` / `--status-source`；缺失会阻止后续满足前提，不能用本示例降级已要求的证据。

### 6.3 正式接收前：重新采集，再比较

重新核对项目和本次仍有效的执行范围；恢复后先重新绑定，分别核验授权与结果有效性，不能恢复已消费调用或旧过期结果。新 v2 只读结果是否允许重验见 6.4，不以哈希相等代替授权。把 6.2 的前三条命令中的文件/目录后缀 `baseline` 改为 `current`，在对应真实采集另获批准后实际再次执行双捕获、运行采集和导入。不要复制 baseline 文件伪装 current，也不要覆盖旧基线。

```bash
python3 -B -m workbench.project_freshness \
  --baseline "$VIBE_EVIDENCE/freshness-baseline.json" \
  --current "$VIBE_EVIDENCE/identity-current.json" \
  --runtime "$VIBE_EVIDENCE/runtime-current.json" \
  --output "$VIBE_EVIDENCE/freshness-current.json"
```

输出 envelope schema 2：项目与程序证据完整且未变才可能 FRESH/exit 0；版本或程序指纹漂移为 STALE/exit 3；缺失、畸形或环境不匹配为 UNKNOWN/exit 3。`write_authorized` 始终为 false。项目有预期修改也会使旧身份过期，需要按实际审批重新建立适用基线，不改哈希骗过检查。

已有获批的新 v2 工程委派 brief/result 时，使用主线程派发前保留的 runtime 和接收前新采的 runtime，准备绑定完整 brief 摘要的上下文。以下四个输入路径须指向本次真实材料；reception-context 须由主线程按 6.4 核验，不能借用示例结果或历史审批。若主线程与权威工作树不同，权威 identity 也须在其已批准范围内独立双捕获。

```bash
VIBE_BRIEF="/absolute/path/to/current-approved-brief.json"
VIBE_RESULT="/absolute/path/to/current-returned-result.json"
VIBE_AUTHORITY_IDENTITY="/absolute/path/to/current-authority-identity.json"
VIBE_RECEPTION="/absolute/path/to/main-thread-checked-reception-context.json"
python3 -B -m workbench.delegation_contract prepare-runtime-context \
  --brief "$VIBE_BRIEF" --baseline-runtime "$VIBE_EVIDENCE/runtime-baseline.json" \
  --current-runtime "$VIBE_EVIDENCE/runtime-current.json" \
  --current-main-identity "$VIBE_EVIDENCE/identity-current.json" \
  --output "$VIBE_EVIDENCE/runtime-context.json"
python3 -B -m workbench.delegation_contract evaluate-result \
  --brief "$VIBE_BRIEF" --result "$VIBE_RESULT" \
  --current-main-identity "$VIBE_EVIDENCE/identity-current.json" \
  --current-authority-identity "$VIBE_AUTHORITY_IDENTITY" \
  --runtime-context "$VIBE_EVIDENCE/runtime-context.json" \
  --reception-context "$VIBE_RECEPTION" \
  --output "$VIBE_EVIDENCE/delegation-evaluation.json"
```

有已消费 delegation-id 时，逐项附上既有 `--consumed-id`；CLI 不替主线程保存消费历史。`prepare-runtime-context` 的 exit 0 **只证明上下文已准备**，会保留合法的漂移证据，必须继续看 evaluator；它不是接收成功。evaluator 的 `ACCEPTED_FOR_MAIN_THREAD_REVIEW` 也只是进入主线程审查，不是写入权或用户验收。所有 `--output` 只允许新建，拒绝覆盖；原始证据可能含路径，只在核准位置保留，分享前脱敏。

Python 调用对应 `project_identity.capture_stable_identity`、`project_freshness.runtime_evidence_from_capture` / `build_freshness_envelope`、`delegation_contract.build_runtime_context` / `evaluate_delegation_result`。纯比较/上下文函数不执行程序；导入函数会读显式程序及采集路径元数据。不能用 `compare_identity` 单独的 FRESH 代替完整运行新鲜度检查。真实宿主兼容、包装器/动态依赖的指纹覆盖及验证范围见[限制说明](limitations.md#运行证据接入的边界)。

### 6.4 新委派的接收生命周期（本地候选接口）

此接口是确定性校验能力，不表示当前安装的 Skill 已更新，也不能替代模型行为观察或独立安全复审；本版已有证据的范围见[验证说明](release-2.3.md)。启用恢复重验必须在**新的已批准派发前**确定合同，不能改写旧 brief/result 或补填摘要使历史结果复活。

完整 brief/result 顶层字段及可校验的合成只读片段见[源 Skill 的 V2 合同](../skill/vibe-project-lead-zh/references/adaptive-delegation.md#中文-brief-v2)。合成片段只说明字段，不包含真实项目身份、批准或工具轨迹；不能直接派发，也不能作为历史审查的接收材料。

- brief 的 `schema_id` 为 `vibe-project-lead-zh-delegation-brief-v2`，新增精确字段 `reception = {"task_id": "<本次任务 ID>", "resume_policy": "EXPIRE"}`。task_id 为 1–128 个 ASCII 字母/数字及 `._:/-`，首位必须字母/数字。它绑定实际原生任务，由主线程核实，不能临时换名绕过任务切换。
- `EXPIRE` 为保守策略。只有 READ_ONLY / REVIEW 可在派发前明确选 `REVALIDATE_READ_ONLY`；ISOLATED_WRITER 不可选。v2 expiry 除旧漂移项外，必须包含 TASK_ID_CHANGED、USER_PAUSED_OR_CANCELLED、APPROVAL_REVOKED、LIFECYCLE_EVIDENCE_INCOMPLETE、TASK_INTERRUPTED。EXPIRE 继续要求 TASK_INTERRUPTED_OR_RESUMED；重验策略必须移除这一矛盾项，其他必需项保留。
- result 的 `schema_id` 为 `vibe-project-lead-zh-delegation-result-v2`，新增 `brief_sha256`，返回时即绑定实际收到的完整 brief。使用现有 `canonical_contract_digest` 计算规范摘要（去除已声明的采集时间字段），不是原始 JSON 文件的字节摘要。旧 source_baseline_sha256 及所有范围、命令、预算等字段保留。

Python 的 `reception_context` / CLI 的 `--reception-context` 输入是精确六字段对象：

| 字段 | 主线程必须核对的内容 |
|---|---|
| brief_sha256 | 实际批准并送达的完整 v2 brief 的规范摘要 |
| result_sha256 | 实际返回且已核对的完整 v2 result 的规范摘要 |
| current_task_id | 接收当下的实际原生任务 ID，与派发任务一致 |
| events | 从派发到接收检查期间的有序事件列表，最多 256 项；不能只列恢复后的本轮事件 |
| events_complete | 只有真实轨迹完整、无遗漏或未知事件时才填写布尔 true |
| evidence_complete | 只有派发材料、真实命令/原始返回、覆盖范围与当前有效授权均经主线程核对完整时才填写布尔 true |

事件只接受 CONTEXT_COMPACTION、SAME_TASK_RESUME、USER_PAUSED、USER_CANCELLED、APPROVAL_REVOKED、TASK_SWITCH、PROJECT_SWITCH、TASK_INTERRUPTED。前两项表示可解释的同任务压缩/恢复；仅重验策略有资格继续现有身份与运行检查。用户暂停/取消、撤销、任务/项目切换、执行被打断均令结果失效，后面追加 SAME_TASK_RESUME 也不解除。无法区分恢复和被打断、来源不全、轨迹超出可核对范围时，完整性必须为 false；不截断事件、不猜测。相同事件可重复出现；已确认无事件才使用空列表。

接收器不会自行读取宿主事件、认证证据来源、保存消费历史或批准新模型调用。哈希与两个 true 只是调用方声明，不能把 Agent 自报、测试 fixture 或推测当作核验。接收检查之后若又出现用户事件或状态变化，原判断不再适用，须重新核对。

v1 仍可独立解析/validate-brief 诊断，但任何含 v1 的接收都返回 INCOMPLETE / LEGACY_CONTRACT_REQUIRES_NEW_DISPATCH；没有自动迁移。缺失/畸形/未知接收证据为 INCOMPLETE，明确生命周期或身份/程序漂移为 STALE，材料与 brief 错配为 CONTRACT_VIOLATION，均不产消费记录。evaluation 输出 schema 3；合格记录增加 reception_context_sha256。runtime/freshness 接口保持原 schema。已消费 ID 仍返回 ALREADY_CONSUMED。

稳定重验只允许**尚未消费且未失效的新结果进入主线程审查**，不授予写入、不恢复调用额度、不表示发现为零或审查 PASS，更不代替用户验收。

## 7. 出问题时交什么

提供脱敏后的版本、宿主/系统类别、所用子命令、状态码、问题发生阶段和预期结果。不要贴密码、Token、cookie、授权头、完整配置、真实路径清单或原始模型会话。先停止下一写入，保存本地证据；只有新现场核验与新授权后才能恢复操作。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
