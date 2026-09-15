# 真人交付闭环

## 使用条件与非目标

当任务需要保留用户原始目标、判断交付是否完整、控制同一路线重试、决定真人验收等级，或在有意义检查点解释进度时，使用本 reference。先完成项目绑定，再把当前任务转换为一份结果合同；只有结果、权限、策略和验收前提彼此一致时，才继续执行或进入验收。

本流程不创建第二套 Goal、memory、项目状态或审批系统，不替代 Codex 原生主线程，也不凭空产生写入、模型、账号、网络、安装、push、发布或部署权限。五份机器合同都是无持久化裁决输入；项目现场和当前用户授权仍是事实与权威来源。

## OUTCOME_CONTRACT_V1

`OUTCOME_CONTRACT_V1` 保真记录 `original_goal`，并把它映射为用户可观察成果、完成成果所需能力、非目标和逐项证据要求。每项能力使用 `PLANNED / IMPLEMENTED / VERIFIED / PARTIAL / MISSING / UNKNOWN / NOT_APPLICABLE`，证据引用必须来自当前现场且按确定顺序记录。

原始目标仅由 Outcome Contract 权威保留。调用方另行提供并经 `SAFE_PUBLIC_TEXT_V1` 验证的 `safe_goal_summary` 只用于展示，安全目标摘要不参与完成裁决，也不能替代原始目标、成果、能力、证据要求或映射。Outcome 从原始 UTF-8 字节派生 `original_goal_sha256`，并输出只含安全摘要、该绑定和 `RETAINED_IN_OUTCOME_CONTRACT` 状态的 `goal_display`；Progress 不接收第二份原始目标，不得回退展示 original_goal。

原始目标仍只由 Outcome authority 保留。这一权威边界不因公开文本被拒绝而改变，也不能由固定提示、测试或进度卡替代。

先做关键能力验证，再判断是否具备交付或验收资格。关键成果对应的关键能力为 `PARTIAL`、`MISSING` 或 `UNKNOWN` 时，结果保持 `INCOMPLETE`，不得进入最终 UAT。代码存在只证明 `IMPLEMENTED`；测试、提交、构建、准备度或一次运行只证明直接覆盖的技术事实，不能单独证明真实目标已经交付。

目标改变时增加 revision，并用前一版本 digest 保留修订链。用户删除成果或接受限制时记录决定和原缺口，不静默改写 `original_goal`，也不把历史缺口伪装成从未存在。

## EXECUTION_ENVELOPE_V1

`EXECUTION_ENVELOPE_V1` 把当前任务、唯一写入权威项目、logical/physical path、Git/worktree、branch、HEAD、完整 dirty fingerprint、Outcome/设计/计划 digest、allowlist、允许及禁止动作、预期产物、停止条件、reviewer 预算和生命周期绑定在一起。每个动作必须携带 FILE_SET 或 BEHAVIOR scope；文件 scope 必须完全落在文件 allowlist，行为 scope 必须命中预先声明的 behavior allowlist。

它只验证既有用户请求或获批设计/计划是否覆盖当前本地动作，绝不创建新权威。验证结果必须明确剩余动作、漂移字段和 `write_authorized=false`；实际写入还必须同时满足当前包络、项目绑定、freshness 和更高优先级平台权限。

本地包络可以连续覆盖已列明的读取、编辑、测试、静态检查、文档、本地提交和 review。stable resume 必须逐项比较 task、envelope、worktree、environment、allowlist、permission 和 side-effect，并重新计算对应 digest；resume_state_digest 不得恢复已消费动作。动作不在 allowlist、项目或锚点漂移、生命周期已消费/取消、包络合同不完整，或动作属于外部/不可逆类别时，统一失败关闭。

envelope 中的集合语义列表按 UTF-8 规范排序后再计算 digest；事件、历史和其他序列语义数据保留原顺序。resume 决定必须匹配封闭真值表，只有合法 STABLE_RESUME 可参与完成；reason、eligible、drift 或 remaining actions 任一矛盾都在 Progress 输出前失败关闭。

## STRATEGY_ATTEMPT_V1

`STRATEGY_ATTEMPT_V1` 用“目标 digest + 根本机制 + 关键假设 + 目标环境 + 副作用类别”识别路线。请求 ID、临时目录、命令顺序、参数或输出格式变化只是表面变化，不能重置失败计数。

初始尝试失败后，只允许最多三次有新 `evidence_delta` 的修正重试。每次都记录失败现象、直接证据、修正内容和结果；没有新证据的重复执行仍计为失败，但不能取得下一次重试资格。第四次实质失败必须进入 `ROUTE_REASSESSMENT_REQUIRED`，不得第五次尝试。

路线假设按规范顺序参与身份计算；同一组假设换序不能伪装成新路线。`SUCCESS / EXTERNAL_STOP / ROUTE_REASSESSMENT_REQUIRED` 等终止状态后不得追加 attempt。重新比较时至少给出两条根本机制不同的路线，并分别说明预期效果、风险、维护成本和证据缺口。若切换会改变用户结果、数据模型、费用、外部副作用或长期维护责任，由用户决定。外部、不可逆或结果为 `UNKNOWN` 的动作第一次未成功即停止，不进入自动重试。

## ACCEPTANCE_DECISION_V1

`ACCEPTANCE_DECISION_V1` 根据用户可见性、业务影响、数据行为、外部副作用、可逆性、自动覆盖、环境差异、新颖程度和 UNKNOWN 取最高风险等级：

| 级别 | 适用结果 | 最低验收 |
|---|---|---|
| `U0` | 纯内部变化，用户行为和输出不变 | 自动测试与审查；有完整替代证据时可免真人验收 |
| `U1` | 低影响可见变化，可快速回滚 | 自动或视觉证据；免除时记录理由和证据 |
| `U2` | 功能、业务逻辑、数据行为、兼容或工作流变化 | 引导式真实场景观察 |
| `U3` | 账号、敏感数据、费用、公开、生产、部署或不可逆动作 | 动作前人工门、动作后观察和回滚验证 |

关键能力验证必须先于最终 UAT。关键成果为 UNKNOWN 时至少提升到 U2；外部、安全或不可逆事实 UNKNOWN 时提升到 U3。多个因素取最高级别，用户可以提高验收等级，主管没有新证据时不能降低。

只读敏感数据仍为 U3，不能因为没有写入就降到 U2。U0/U1 的免除必须附替代证据和理由。U2/U3 的真人结果只能来自当前任务实际用户事件生成的 `USER_EVENT_RECEIPT_V1`；不能使用 recorded_by="USER" 自证，也不能用摘要、模型输出、测试 fixture 或主管代填替代用户事件。

主管不得替用户确认真人观察；它只能展示预绑定的观察项、用户操作、预期结果和失败停止点，并等待当前任务中的真实用户事件。

不得替用户确认 U2 观察；固定安全提示只能解释边界，不能生成或暗示用户观察结果。

U3 使用预绑定 requirement 和同一 action digest 的严格顺序：先取得 `PRE_ACTION_APPROVAL`，随后才可执行外部动作；取得外部动作结果并验证 result digest 后，才能消费 `POST_ACTION_OBSERVATION`；观察通过后再取得 `ROLLBACK_VERIFICATION`；全部 receipt、动作结果和 digest 一致后，才允许 U3 验收接受。任一步缺失、失败、UNKNOWN、跨任务或 digest 不一致都保持未接受且 `write_authorized=false`。

## PROGRESS_REPORT_V1

`PROGRESS_REPORT_V1` 只在阶段完成、路线失败、恢复、需要用户判断或收口等有意义边界生成。

生成进度卡前，包括在当前对话中直接撰写自然语言进度卡时，先做以下核对：

1. 从当前证据列出具体判断、对象与环境、证据时点、来源和已有结果；区分已取得的检查结果、尚未开展的检查和等待用户提供的观察。不推测缺失的结果。
2. 按验收 reference 的“固定结果分区”确定每项判断的归属，再写摘要。每个 UNKNOWN 都附无法裁决的具体原因；没有未知项则显示“无”，不为填栏目添加猜测。
3. 输出前检查同一判断是否因同义改写被重复分类，以及不同判断是否被过度合并；最后核对进度、用户参与和下一步是否与这些证据一致。

这些核对只使用当前任务中的证据，不创建新的状态文件或事实 ID 合同，也不改变既有治理组件的 UNKNOWN 语义。实际未调用构建器或校验器时，不能声称该对话输出已通过程序校验。

首屏使用中文管理摘要，依次说明：

1. 安全目标摘要、原始目标绑定、当前结论、阶段和总体进度；
2. 已实现的用户可见功能及环境；
3. 当前方案、选择原因、实际实现逻辑和关键数据流；
4. 已验证、Warning、未验证和 UNKNOWN；
5. 路线累计失败、本次证据变化和实质备选；
6. 用户是否需要参与、为什么、一个下一操作、预期结果、副作用和失败停止点；
7. 下一步、停止条件和将到达的关键门。

项目路径、branch、HEAD、dirty fingerprint、文件、测试 ID、测试计数、reviewer、digest、提交和回滚点使用封闭字段放入技术附录，不挤占管理摘要。`completion_state` 必须由保留的 completion_basis 重新派生，完成措辞由结构化状态生成，不能从自由文本关键词推断。结构化裁决相互冲突、任一 authority 标志为 true、字段含 remote、凭据、diff 或其他秘密形状时敏感值失败关闭，不做静默脱敏后继续输出。

所有可进入 Progress JSON/Markdown 的字符串都使用同一个 `SAFE_PUBLIC_TEXT_V1` 验证器，最终 report 在两个 renderer 前再次递归扫描。环境变量赋值、URL/remote、凭据、raw diff、hunk、未跟踪文件原文、task-list 和 inline-code 内容失败关闭，不返回部分结果。Markdown 把已验证的调用方普通文本按 plain text 转义；标题、项目符号和技术附录围栏只由固定模板生成。

Progress 在构建后和两个 renderer 前执行最终递归复扫；任一显式或嵌套字符串不合规时，JSON 和 Markdown 都整体拒绝，不能返回已生成的局部内容。用户只看到固定的风险类别、受影响字段和下一安全步骤，不看到被拒绝原文。需要继续时，应改用不含秘密值的中文说明、本地路径、SHA-256、无值标识符或封闭消息，而不是改换语言、标点或 Markdown 包装来绕过检查。

这一拒绝只保护公开输出，不改变 Outcome authority、完成裁决或真人验收。U2 真人观察不得自动填写或判定 PASS；主管必须展示真实场景、预期结果和失败停止点，等待当前任务中的用户事件。

需要说明认证边界时，只使用封闭消息代码 `AUTHORIZATION_VALUE_HIDDEN`，其固定展示为“认证请求头的值不得进入公开进度。”。该消息不接收调用方参数，不表示完成或验收通过。

## 功能迁移完整性示例

例一，用户要求“把本地库存同步完整迁移到服务器”。Outcome 必须分别列出源端现有能力、目标端入口、同步逻辑、持久化、认证、监控和回滚，并为每项标注目标环境证据。即使服务器页面已经打开，只要目标端同步逻辑仍为 `MISSING`，当前状态就是 `INCOMPLETE`；不得让用户先测后续页面，也不得用本地测试、部署成功或源端可用代替目标端交付。补齐目标能力及证据后，才能进入关键能力验证和最终 UAT。

例二，任务只是在同一模块内重命名私有 helper，输出、数据和用户工作流均不变。只有相关测试、静态检查和审查完整，且没有环境差异、外部副作用或 UNKNOWN 时，才可判为 U0 并用替代证据免真人验收；任一条件不满足就按实际最高风险升级。

例三，任务只读查看带客户资料的生产报表。即使没有写入，`data_sensitivity=SENSITIVE` 仍判为 U3；必须预先绑定真人要求并取得当前任务的用户事件 receipt，不能把“只读”当成免除前置人工门、事后观察或敏感值保护的理由。

## Stable resume 与失效

审批处理以 safety-gates.md 的“审批绑定与生命周期”为唯一详细真源。用户暂停时停止新动作，恢复前重新核验；暂停不自动等同取消，也不恢复已消费次数或旧工程结果。

`stable same-task resume` 只适用于同一 Codex 任务的普通续跑、上下文压缩或 Goal resume。重新捕获后，任务、项目、logical/physical path、worktree、branch、HEAD、dirty fingerprint、Outcome/设计/计划 digest、allowlist、权限和副作用范围全部一致时，可继续尚未消费的本地包络。

用户取消或改变目标，切换任务/项目/环境，任何锚点或范围漂移，出现 Critical/High、无法归因失败、合同/身份/清理不完整，或需要外部动作时，包络立即失效。已消费的一次性审批永不复用；E/F 外部审批永不恢复，也不能由本地包络、设计或计划推导出来。

## 外部和不可逆动作例外

账号、凭据、网络、真实 `CODEX_HOME`、业务数据写入、付费、push、发布、部署、删除和生产迁移保持独立精确门。外部动作第一次失败或结果 UNKNOWN 就停止，先调查可能副作用，不换账号、Provider 或实现自动重试。

仓库合同不能覆盖操作系统、Codex App 或组织策略；平台强制权限提示不能被本 Skill 取消。平台提示出现时由用户按实际界面决定，Skill 只负责解释目标、范围、风险、成功判定和回滚。

## 停止、回滚与交接

停止时先保留用户修改和可恢复证据，不使用 reset、clean、覆盖、重发或重复部署来掩盖失败。报告当前分类、项目与锚点、已验证/Warning/未验证/UNKNOWN、已消费预算、实际或可能副作用、可执行回滚及限制，并只给一个下一安全步骤。

交接摘要必须保留原始目标、Outcome revision、当前能力和证据状态、有效或失效的本地包络、路线失败计数、验收等级及待用户观察。技术实现、一次提交或阶段 PASS 不等于长期 Goal 完成。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
