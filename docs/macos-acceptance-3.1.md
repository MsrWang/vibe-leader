# Vibe Leader 3.1 macOS 候选验收指南

本指南用于 Apple Silicon Mac 上的 3.1 候选验收。执行前确认下载的是候选 Release 的 `Vibe-Leader-3.1.0-GitHub.zip`、`release_archive.py` 和 `SHA256SUMS.txt`。当前参考验收目标是 2026 Mac mini（M6）、验收当日 macOS 27 稳定补丁版本和 CPython 3.14.7；通用设计目标是 macOS 14+、Apple Silicon M1+ 和稳定版 Python 3.11+。

本指南不接入外部 Jev，不使用真实业务数据。WSL 测试不是 Mac 原生验证；用户观察前不得声称 Codex App 验收完成。每一步只确认其直接证据，失败、矛盾或 `UNKNOWN` 时停止，不清理现场后盲目重试。

## 1. 核对系统摘要

在 Mac 本机终端运行以下只读命令：

```bash
sw_vers
uname -m
sysctl -n machdep.cpu.brand_string
python3 -c 'import platform, sys; print(platform.python_implementation(), platform.python_version(), sys.version_info.releaselevel)'
```

要求架构为 `arm64`，macOS 主版本不低于 14，Python 为稳定版 CPython 3.11–3.14。参考机使用 CPython 3.14.7；版本不足时先从可信来源更新，再从本节重新开始。收据只记录机型类别、芯片、系统版本、Python 版本和检查时间，不记录用户名、序列号、硬件 UUID、主目录或 Token。

## 2. 独立绑定候选提交

先进入已核对来源的本地仓库副本，计算候选标签实际指向的提交：

```bash
VIBE_TRUSTED_REPO="$(pwd -P)"
VIBE_EXPECTED_COMMIT="$(git -C "$VIBE_TRUSTED_REPO" rev-parse 'v3.1.0-rc.1^{commit}')"
test -n "$VIBE_EXPECTED_COMMIT"
```

再进入包含三个下载资产的目录，固定本次资产目录：

```bash
VIBE_ASSET_DIR="$(pwd -P)"
test -f "$VIBE_ASSET_DIR/Vibe-Leader-3.1.0-GitHub.zip"
test -f "$VIBE_ASSET_DIR/release_archive.py"
test -f "$VIBE_ASSET_DIR/SHA256SUMS.txt"
```

候选标签和三个资产尚未发布时不能执行本节，也不能从 ZIP 内的 manifest 反推 `VIBE_EXPECTED_COMMIT` 后自证来源。

## 3. 校验摘要并安全解包

先使用系统工具核对两个被摘要覆盖的资产，再让独立脚本验证 ZIP、内嵌脚本、清单、路径、模式和固定提交：

```bash
cd "$VIBE_ASSET_DIR"
shasum -a 256 -c SHA256SUMS.txt
python3 -B ./release_archive.py verify \
  --archive ./Vibe-Leader-3.1.0-GitHub.zip \
  --checksums ./SHA256SUMS.txt \
  --expected-commit "$VIBE_EXPECTED_COMMIT"
VIBE_ACCEPTANCE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/vibe-leader-3.1.XXXXXX")"
VIBE_EXTRACT_DIR="$VIBE_ACCEPTANCE_ROOT/extracted"
python3 -B ./release_archive.py extract \
  --archive ./Vibe-Leader-3.1.0-GitHub.zip \
  --checksums ./SHA256SUMS.txt \
  --expected-commit "$VIBE_EXPECTED_COMMIT" \
  --destination "$VIBE_EXTRACT_DIR"
VIBE_RELEASE_ROOT="$VIBE_EXTRACT_DIR/Vibe-Leader-3.1.0"
VIBE_SOURCE="$VIBE_RELEASE_ROOT/skill/vibe-project-lead-zh"
test "$(find "$VIBE_SOURCE" -type f | wc -l | tr -d ' ')" = 13
```

`verify` 必须输出 `status=VERIFIED` 并显示同一个提交。这里证明的是候选资产完整性；还没有证明 Mac 安装或 Codex App 使用成功。

## 4. 对真实安装根运行只读 `preflight`

先由用户在 Codex App 当前设置和实际发现行为中确认真实 `CODEX_HOME`，再在同一终端显式设置。不要让脚本搜索或猜测：

```bash
: "${CODEX_HOME:?先核对并设置当前 Codex App 的真实 CODEX_HOME}"
test -d "$CODEX_HOME/skills"
VIBE_PREFLIGHT_RECEIPT="$VIBE_ACCEPTANCE_ROOT/preflight.json"
CODEX_HOME="$CODEX_HOME" python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" preflight \
  --source "$VIBE_SOURCE" \
  --skills-root "$CODEX_HOME/skills" \
  --selection-source CODEX_HOME > "$VIBE_PREFLIGHT_RECEIPT"
cat "$VIBE_PREFLIGHT_RECEIPT"
```

要求退出 0、`status` 为 `READY`、`read_only` 为 `true`，并核对 `install_mode` 是 `FRESH_INSTALL` 或 `CONTROLLED_UPGRADE`。报告中的挂载点是摘要；`pending_write_checks` 表明写入阶段能力尚未执行，不能把预检称为安装成功或 Mac 本机就绪。

## 5. 在隔离临时 `CODEX_HOME` 做技术安装

以下目录只用于候选技术检查，不是 Codex App 的真实安装根：

```bash
VIBE_TEMP_CODEX_HOME="$VIBE_ACCEPTANCE_ROOT/codex-home"
mkdir -m 700 "$VIBE_TEMP_CODEX_HOME"
mkdir -m 700 "$VIBE_TEMP_CODEX_HOME/skills"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" install \
  --source "$VIBE_SOURCE" \
  --skills-root "$VIBE_TEMP_CODEX_HOME/skills"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$VIBE_TEMP_CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$VIBE_TEMP_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json"
test "$(find "$VIBE_TEMP_CODEX_HOME/skills/vibe-project-lead-zh" -type f | wc -l | tr -d ' ')" = 13
```

要求安装和核验退出 0，安装内容仍为 13 个文件。临时安装不证明 Codex App 能发现它。

## 6. 取得真实安装批准并执行一次

检查第 1–5 节收据后，明确本次提交、资产摘要、真实 `CODEX_HOME`、`install_mode` 和恢复路径，再批准一次真实安装动作。不要把候选下载或临时安装当成真实安装批准。

### 首次安装

`install_mode=FRESH_INSTALL` 时执行并保留 JSON：

```bash
VIBE_REAL_INSTALL_RESULT="$VIBE_ACCEPTANCE_ROOT/real-install.json"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" install \
  --source "$VIBE_SOURCE" \
  --skills-root "$CODEX_HOME/skills" > "$VIBE_REAL_INSTALL_RESULT"
cat "$VIBE_REAL_INSTALL_RESULT"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json"
```

### 受控升级

`install_mode=CONTROLLED_UPGRADE` 时先为本轮升级确定唯一批准编号，再准备请求。`prepare-upgrade` 会执行写能力探针；它已属于批准动作：

```bash
VIBE_UPGRADE_APPROVAL_ID="MAC-3.1-UPGRADE-$(date -u +%Y%m%dT%H%M%SZ)"
VIBE_UPGRADE_REQUEST="$VIBE_ACCEPTANCE_ROOT/upgrade-request.json"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" prepare-upgrade \
  --source "$VIBE_SOURCE" \
  --target "$CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  --approval-id "$VIBE_UPGRADE_APPROVAL_ID" \
  --output "$VIBE_UPGRADE_REQUEST"
VIBE_REQUEST_DIGEST="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["request_digest"])' "$VIBE_UPGRADE_REQUEST")"
VIBE_UPGRADE_RESULT="$VIBE_ACCEPTANCE_ROOT/upgrade-result.json"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" upgrade \
  --request "$VIBE_UPGRADE_REQUEST" \
  --confirm-request "$VIBE_REQUEST_DIGEST" > "$VIBE_UPGRADE_RESULT"
cat "$VIBE_UPGRADE_RESULT"
```

任一结果为漂移、拒绝、恢复必需或未知时停止。不要用首次安装命令覆盖现有 Skill。

## 7. 在新任务中做 Codex App 用户观察

刷新或重新打开 Codex App 后，新建一个任务。先确认 Skills 中只有一个技术标识 `vibe-project-lead-zh`，显示名为“中文跨项目研发主管”，locator 指向刚核对的真实安装根。然后发送：

```text
使用 $vibe-project-lead-zh。先绑定当前这个合成测试项目，只读调查。
请确认你的主管定位和权限边界，不要部署、安装依赖或访问外部 Jev。
```

再创建一个不含业务信息的临时项目，放入两份合成文本，并要求它显式调用已安装的本地证据筛选器查找其中一个关键词。预期结果要保留来源引用、字节范围和本地模式；无匹配不能解释为事实不存在。只有用户实际看到唯一发现、显式调用和合成筛选结果后，才能记录这一步通过。

## 8. 候选回滚并确认原状态

回滚是独立动作，需要新的明确批准。首次安装路径读取刚安装 manifest 的摘要后执行 `rollback`：

```bash
VIBE_MANIFEST_DIGEST="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["manifest_digest"])' "$CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json")"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" rollback \
  --target "$CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  --confirm "$VIBE_MANIFEST_DIGEST"
```

受控升级路径从 `upgrade-result.json` 取得成功收据路径，再从成功收据读取 `restore_confirmation_digest`，以新的恢复批准编号执行：

```bash
VIBE_RESTORE_APPROVAL_ID="MAC-3.1-RESTORE-$(date -u +%Y%m%dT%H%M%SZ)"
VIBE_UPGRADE_RECEIPT="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["receipt"])' "$VIBE_UPGRADE_RESULT")"
VIBE_RESTORE_CONFIRM="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["restore_confirmation_digest"])' "$VIBE_UPGRADE_RECEIPT")"
python3 -B "$VIBE_RELEASE_ROOT/scripts/install_skill.py" restore-version \
  --receipt "$VIBE_UPGRADE_RECEIPT" \
  --approval-id "$VIBE_RESTORE_APPROVAL_ID" \
  --confirm "$VIBE_RESTORE_CONFIRM"
```

刷新 Codex App。首次安装路径应恢复到未安装状态；升级路径应恢复原版本并通过原 manifest 核验。记录实际观察，不能把命令退出 0 单独当成原状态确认。

## 9. 稳定版实装

只有同一冻结提交和同一三个资产完成候选验收并被原样晋升为正式 Release 后，才开始稳定版实装。重新从正式 Release 下载三个资产，以正式标签 `v3.1.0` 代替第 2 节的候选标签 `v3.1.0-rc.1`，再重复第 2–4 节并逐字节确认正式资产摘要与已验收候选一致；任何差异都要停止并回到新候选流程。

对正式版重新取得安装或升级批准，重复第 6–7 节，再核对 manifest、恢复材料和新任务显式调用。稳定版实装是新的用户观察，候选成功、平台上传成功或候选回滚成功都不会自动授权或证明它。

## 收据最小字段

最终验收记录至少包括：候选标签和提交、三个资产摘要、系统/架构/Python 摘要、脱敏文件系统身份、`preflight` 九字段、安装模式、安装或升级收据、13 文件清单结果、Codex App 唯一 locator 和显示名观察、显式调用、合成筛选结果、候选回滚或恢复结果、原状态确认，以及稳定版重新下载和实装结果。用户名、绝对路径、Token、序列号、硬件 UUID 和真实业务数据不得进入公开收据。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
