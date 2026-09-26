# Vibe Leader 3.1 macOS 验收指南

本指南同时适用于 `v3.1.0-rc.*` 候选验收和 `v3.1.0` 正式版实装。生命周期状态由外部 Release 元数据以及经签名或平台回读的收据建立，包内文字不自称候选已发布、正式版已发布或某版本仍是 latest。

通用设计目标是 macOS 14+、Apple Silicon M1+、`arm64` 和**稳定版 CPython 3.11–3.14（最低 3.11；新安装推荐 3.14.7）**；3.15+ 保持 `RUNTIME_UNVERIFIED`。**2026 Mac mini（M6）仅为参考验收目标，不是已验证结论**。可安装 Skill 仍为 **13 个文件**，本版不接入外部 Jev，也不使用真实业务数据。

WSL 测试不是 Mac 原生验证；用户观察前不得声称 Codex App 验收完成。失败、矛盾或 `UNKNOWN` 时停止，不清理现场后重试。

## 1. 核对系统摘要

在 Mac 本机终端运行只读检查：

```bash
set -euo pipefail
sw_vers
uname -m
sysctl -n machdep.cpu.brand_string
python3 -c 'import platform, sys; print(platform.python_implementation(), platform.python_version(), sys.version_info.releaselevel)'
```

要求架构为 `arm64`、macOS 主版本不低于 14、Python 属于上述稳定版区间。参考机使用验收当日的 macOS 27 稳定补丁版本和 CPython 3.14.7。系统摘要不能替代后续资产、安装和用户观察。

## 2. 绑定公共仓库、标签和私有证据目录

公共来源固定为仓库身份 `MsrWang/vibe-leader` 和 URL `https://github.com/MsrWang/vibe-leader.git`。候选检查把 `VIBE_RELEASE_TAG` 设为实际 prerelease 标签；正式版检查设为 `v3.1.0`。不要使用同名 fork、页面搜索结果或本机其他 remote。

```bash
set -euo pipefail
VIBE_PUBLIC_REPO_ID="MsrWang/vibe-leader"
VIBE_PUBLIC_REPO_URL="https://github.com/MsrWang/vibe-leader.git"
VIBE_RELEASE_TAG="v3.1.0-rc.1"
VIBE_ACCEPTANCE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/vibe-leader-3.1.XXXXXX")"
VIBE_PRIVATE_EVIDENCE="$(mktemp -d "${TMPDIR:-/tmp}/vibe-leader-private.XXXXXX")"
chmod 700 "$VIBE_ACCEPTANCE_ROOT" "$VIBE_PRIVATE_EVIDENCE"
VIBE_TRUSTED_REPO="$VIBE_ACCEPTANCE_ROOT/public-repo"
git clone --no-checkout "$VIBE_PUBLIC_REPO_URL" "$VIBE_TRUSTED_REPO"
test "$(git -C "$VIBE_TRUSTED_REPO" remote get-url origin)" = "$VIBE_PUBLIC_REPO_URL"
git -C "$VIBE_TRUSTED_REPO" fetch --force origin \
  "refs/tags/$VIBE_RELEASE_TAG:refs/tags/$VIBE_RELEASE_TAG"
VIBE_EXPECTED_COMMIT="$(git -C "$VIBE_TRUSTED_REPO" rev-parse "$VIBE_RELEASE_TAG^{commit}")"
test -n "$VIBE_EXPECTED_COMMIT"
```

`VIBE_PRIVATE_EVIDENCE` 是**私有原始证据**目录，可保存真实绝对路径、完整平台 JSON、安装/升级/恢复收据和失败现场。它不能提交到公开仓库或公开工单。

## 3. 用 GitHub Release API 绑定三个下载资产

从固定仓库的 `https://api.github.com/repos/MsrWang/vibe-leader/releases/tags/` 接口回读标签和 `assets[].digest`。三个下载资产 `Vibe-Leader-3.1.0-GitHub.zip`、`release_archive.py`、`SHA256SUMS.txt` 都必须各有唯一 `sha256:` digest；缺失、重复或格式错误时停止。

```bash
set -euo pipefail
VIBE_RELEASE_API="https://api.github.com/repos/$VIBE_PUBLIC_REPO_ID/releases/tags/$VIBE_RELEASE_TAG"
VIBE_RELEASE_JSON="$VIBE_PRIVATE_EVIDENCE/release-api.json"
VIBE_ASSET_INDEX="$VIBE_PRIVATE_EVIDENCE/release-assets.tsv"
VIBE_ASSET_DIR="$VIBE_ACCEPTANCE_ROOT/assets"
mkdir -m 700 "$VIBE_ASSET_DIR"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  "$VIBE_RELEASE_API" --output "$VIBE_RELEASE_JSON"
python3 - "$VIBE_RELEASE_JSON" "$VIBE_ASSET_INDEX" "$VIBE_RELEASE_TAG" <<'PY'
import json
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
output = Path(sys.argv[2])
expected_tag = sys.argv[3]
release = json.loads(source.read_text(encoding="utf-8"))
if release.get("tag_name") != expected_tag:
    raise SystemExit("release tag mismatch")
wanted = (
    "Vibe-Leader-3.1.0-GitHub.zip",
    "release_archive.py",
    "SHA256SUMS.txt",
)
prefix = f"https://github.com/MsrWang/vibe-leader/releases/download/{expected_tag}/"
found = {}
for asset in release.get("assets", []):
    name = asset.get("name")
    if name not in wanted:
        continue
    if name in found:
        raise SystemExit("duplicate release asset")
    digest = asset.get("digest")
    url = asset.get("browser_download_url")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise SystemExit("missing release asset digest")
    if not isinstance(url, str) or not url.startswith(prefix):
        raise SystemExit("unexpected release asset URL")
    found[name] = (url, digest.removeprefix("sha256:"))
if set(found) != set(wanted):
    raise SystemExit("release asset set mismatch")
with output.open("x", encoding="utf-8") as stream:
    for name in wanted:
        url, digest = found[name]
        stream.write(f"{name}\t{url}\t{digest}\n")
PY
while IFS=$'\t' read -r VIBE_ASSET_NAME VIBE_ASSET_URL VIBE_ASSET_DIGEST; do
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
    "$VIBE_ASSET_URL" --output "$VIBE_ASSET_DIR/$VIBE_ASSET_NAME"
  VIBE_ACTUAL_DIGEST="$(shasum -a 256 "$VIBE_ASSET_DIR/$VIBE_ASSET_NAME" | awk '{print $1}')"
  test "$VIBE_ACTUAL_DIGEST" = "$VIBE_ASSET_DIGEST"
done < "$VIBE_ASSET_INDEX"
```

SHA256SUMS.txt 不是自身信任根。只有它自己的下载字节先匹配 GitHub API 回读 digest 后，才可用来核对 ZIP 和脚本的内部配对关系。

执行下载脚本前，还必须从固定提交读出仓库内脚本并逐字节比较。可信内容来自 `git show "$VIBE_EXPECTED_COMMIT:scripts/release_archive.py"`；下面用已绑定仓库执行同一读取：

```bash
set -euo pipefail
VIBE_TRUSTED_RELEASE_SCRIPT="$VIBE_PRIVATE_EVIDENCE/release_archive-from-commit.py"
git -C "$VIBE_TRUSTED_REPO" show "$VIBE_EXPECTED_COMMIT:scripts/release_archive.py" \
  > "$VIBE_TRUSTED_RELEASE_SCRIPT"
cmp "$VIBE_ASSET_DIR/release_archive.py" "$VIBE_TRUSTED_RELEASE_SCRIPT"
cd "$VIBE_ASSET_DIR"
shasum -a 256 -c SHA256SUMS.txt
```

这里绑定了公共仓库、标签提交、GitHub API 三个 digest、下载字节和可信 Git 脚本字节。它不声称能抵御整个 GitHub 账户或渠道失陷。

## 4. 验证、解包和真实根只读预检

只有第 3 节全部通过后，才执行下载的 `release_archive.py`：

```bash
set -euo pipefail
VIBE_EXTRACT_DIR="$VIBE_ACCEPTANCE_ROOT/extracted"
python3 -B "$VIBE_ASSET_DIR/release_archive.py" verify \
  --archive "$VIBE_ASSET_DIR/Vibe-Leader-3.1.0-GitHub.zip" \
  --checksums "$VIBE_ASSET_DIR/SHA256SUMS.txt" \
  --expected-commit "$VIBE_EXPECTED_COMMIT" \
  > "$VIBE_PRIVATE_EVIDENCE/archive-verify.json"
python3 -B "$VIBE_ASSET_DIR/release_archive.py" extract \
  --archive "$VIBE_ASSET_DIR/Vibe-Leader-3.1.0-GitHub.zip" \
  --checksums "$VIBE_ASSET_DIR/SHA256SUMS.txt" \
  --expected-commit "$VIBE_EXPECTED_COMMIT" \
  --destination "$VIBE_EXTRACT_DIR" \
  > "$VIBE_PRIVATE_EVIDENCE/archive-extract.json"
VIBE_RELEASE_ROOT="$VIBE_EXTRACT_DIR/Vibe-Leader-3.1.0"
VIBE_SOURCE="$VIBE_RELEASE_ROOT/skill/vibe-project-lead-zh"
test "$(find "$VIBE_SOURCE" -type f | wc -l | tr -d ' ')" = 13
```

真实安装根由用户从 Codex App 当前配置和唯一 locator 观察中确认，不由脚本搜索：

```bash
set -euo pipefail
: "${VIBE_REAL_CODEX_HOME:?先核对并设置当前 Codex App 的真实 CODEX_HOME}"
test -d "$VIBE_REAL_CODEX_HOME/skills"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" preflight \
  --source "$VIBE_SOURCE" \
  --skills-root "$VIBE_REAL_CODEX_HOME/skills" \
  --selection-source CODEX_HOME \
  > "$VIBE_PRIVATE_EVIDENCE/real-preflight.json"
```

要求退出 0、九字段报告为 `READY`、`read_only=true`，安装模式为 `FRESH_INSTALL` 或 `CONTROLLED_UPGRADE`。`pending_write_checks` 仍未完成，不能据此声称安装成功或 Mac 就绪。

## 5. 原生 Mac 证据筛选技术检查

从已验证解包的候选根运行完整筛选套件：

```bash
set -euo pipefail
cd "$VIBE_RELEASE_ROOT"
python3 -B -m unittest -v tests.test_evidence_filter \
  > "$VIBE_PRIVATE_EVIDENCE/native-evidence-filter.log" 2>&1
```

结果必须包含并通过以下真实本地行为：

- 普通文件：`test_local_filter_returns_exact_verified_bytes_without_paths`；
- 符号链接拒绝和特殊文件拒绝：`test_unsafe_paths_symlinks_and_special_files_are_refused`；
- 来源变更：`test_change_in_unselected_source_discards_selected_excerpts`；
- Darwin 锚定路径合同：`test_darwin_relative_open_uses_anchored_descriptors`。

这是 Mac 原生技术证据，仍不等于 Codex App 发现和用户观察。

## 6. 隔离临时 `CODEX_HOME` 首次安装

临时安装只写测试自有目录，所有安装和核验命令显式绑定同一个临时 `CODEX_HOME`：

```bash
set -euo pipefail
VIBE_TEMP_CODEX_HOME="$VIBE_ACCEPTANCE_ROOT/fresh-codex-home"
mkdir -m 700 "$VIBE_TEMP_CODEX_HOME" "$VIBE_TEMP_CODEX_HOME/skills"
CODEX_HOME="$VIBE_TEMP_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" install \
  --source "$VIBE_SOURCE" --skills-root "$VIBE_TEMP_CODEX_HOME/skills" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-install.json"
CODEX_HOME="$VIBE_TEMP_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$VIBE_TEMP_CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$VIBE_TEMP_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-verify.json"
test "$(find "$VIBE_TEMP_CODEX_HOME/skills/vibe-project-lead-zh" -type f | wc -l | tr -d ' ')" = 13
```

临时安装不证明真实 Codex App 能发现它。

## 7. 第二个临时根完成 3.0.3→3.1 升级和恢复

基线标签固定为公共 `v3.0.3`。从已绑定的公共仓库提交创建独立本地 clone；使用已验证解包的 **3.1 Darwin 安装器**把可信 v3.0.3 Skill 源码安装到第二个临时 `CODEX_HOME`，再执行 `prepare-upgrade`、`upgrade`、`inspect-upgrade` 和 `restore-version`。这只验证 3.1 Darwin 安装器对**旧内容升级与恢复**的合同，不证明 v3.0.3 安装器的 macOS 兼容性；Mac 上不执行旧安装器。

```bash
set -euo pipefail
VIBE_BASELINE_TAG="v3.0.3"
git -C "$VIBE_TRUSTED_REPO" fetch --force origin \
  "refs/tags/$VIBE_BASELINE_TAG:refs/tags/$VIBE_BASELINE_TAG"
VIBE_BASELINE_COMMIT="$(git -C "$VIBE_TRUSTED_REPO" rev-parse "$VIBE_BASELINE_TAG^{commit}")"
VIBE_BASELINE_REPO="$VIBE_ACCEPTANCE_ROOT/baseline-repo"
git clone --no-hardlinks "$VIBE_TRUSTED_REPO" "$VIBE_BASELINE_REPO"
git -C "$VIBE_BASELINE_REPO" checkout --detach "$VIBE_BASELINE_COMMIT"
VIBE_UPGRADE_CODEX_HOME="$VIBE_ACCEPTANCE_ROOT/upgrade-codex-home"
mkdir -m 700 "$VIBE_UPGRADE_CODEX_HOME" "$VIBE_UPGRADE_CODEX_HOME/skills"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" install \
  --source "$VIBE_BASELINE_REPO/skill/vibe-project-lead-zh" \
  --skills-root "$VIBE_UPGRADE_CODEX_HOME/skills" \
  > "$VIBE_PRIVATE_EVIDENCE/baseline-install.json"
VIBE_UPGRADE_TARGET="$VIBE_UPGRADE_CODEX_HOME/skills/vibe-project-lead-zh"
VIBE_UPGRADE_MANIFEST="$VIBE_UPGRADE_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json"
VIBE_ORIGINAL_STATE="$VIBE_PRIVATE_EVIDENCE/original-install-state"
cp -pR "$VIBE_UPGRADE_CODEX_HOME/skills/.vibe-project-lead-zh-install" "$VIBE_ORIGINAL_STATE"
cp -p "$VIBE_UPGRADE_MANIFEST" "$VIBE_PRIVATE_EVIDENCE/original-install-manifest.json"
```

升级、检查和恢复继续显式绑定第二个临时根：

```bash
set -euo pipefail
VIBE_TEMP_UPGRADE_APPROVAL="MAC-3.1-TEMP-UPGRADE-$(date -u +%Y%m%dT%H%M%SZ)"
VIBE_TEMP_UPGRADE_REQUEST="$VIBE_PRIVATE_EVIDENCE/temp-upgrade-request.json"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" prepare-upgrade \
  --source "$VIBE_SOURCE" --target "$VIBE_UPGRADE_TARGET" \
  --manifest "$VIBE_UPGRADE_MANIFEST" \
  --approval-id "$VIBE_TEMP_UPGRADE_APPROVAL" \
  --output "$VIBE_TEMP_UPGRADE_REQUEST" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-upgrade-prepared.json"
VIBE_TEMP_REQUEST_DIGEST="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["request_digest"])' "$VIBE_TEMP_UPGRADE_REQUEST")"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" upgrade \
  --request "$VIBE_TEMP_UPGRADE_REQUEST" \
  --confirm-request "$VIBE_TEMP_REQUEST_DIGEST" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-upgrade-result.json"
VIBE_TEMP_UPGRADE_RECEIPT="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["receipt"])' "$VIBE_PRIVATE_EVIDENCE/temp-upgrade-result.json")"
VIBE_TEMP_UPGRADE_JOURNAL="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["journal"])' "$VIBE_TEMP_UPGRADE_RECEIPT")"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" inspect-upgrade \
  --journal "$VIBE_TEMP_UPGRADE_JOURNAL" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-upgrade-inspection.json"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$VIBE_UPGRADE_TARGET" --manifest "$VIBE_UPGRADE_MANIFEST" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-candidate-verify.json"
VIBE_TEMP_RESTORE_CONFIRM="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["restore_confirmation_digest"])' "$VIBE_TEMP_UPGRADE_RECEIPT")"
VIBE_TEMP_RESTORE_APPROVAL="MAC-3.1-TEMP-RESTORE-$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" restore-version \
  --receipt "$VIBE_TEMP_UPGRADE_RECEIPT" \
  --approval-id "$VIBE_TEMP_RESTORE_APPROVAL" \
  --confirm "$VIBE_TEMP_RESTORE_CONFIRM" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-restore-result.json"
CODEX_HOME="$VIBE_UPGRADE_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$VIBE_UPGRADE_TARGET" --manifest "$VIBE_UPGRADE_MANIFEST" \
  > "$VIBE_PRIVATE_EVIDENCE/temp-restored-verify.json"
cmp "$VIBE_PRIVATE_EVIDENCE/original-install-manifest.json" "$VIBE_UPGRADE_MANIFEST"
diff -r "$VIBE_ORIGINAL_STATE" "$VIBE_UPGRADE_CODEX_HOME/skills/.vibe-project-lead-zh-install"
```

最后两项确认原始 manifest 和状态目录已经恢复。任何失败均保留私有现场，不反向猜测或重复执行。

## 8. 真实安装或升级：单独外部批准

GitHub 候选批准、Mac 候选技术检查、真实安装/升级、Codex App 用户观察、候选恢复、GitHub 正式发布、稳定版实装和 Hugging Face 同步是不同外部动作，外部动作分别批准。以下真实命令全部显式绑定先前确认的 `VIBE_REAL_CODEX_HOME`。

首次安装路径：

```bash
set -euo pipefail
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" install \
  --source "$VIBE_SOURCE" --skills-root "$VIBE_REAL_CODEX_HOME/skills" \
  > "$VIBE_PRIVATE_EVIDENCE/real-install.json"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$VIBE_REAL_CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$VIBE_REAL_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  > "$VIBE_PRIVATE_EVIDENCE/real-install-verify.json"
```

受控升级路径：

```bash
set -euo pipefail
VIBE_REAL_UPGRADE_APPROVAL="MAC-3.1-REAL-UPGRADE-$(date -u +%Y%m%dT%H%M%SZ)"
VIBE_REAL_UPGRADE_REQUEST="$VIBE_PRIVATE_EVIDENCE/real-upgrade-request.json"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" prepare-upgrade \
  --source "$VIBE_SOURCE" \
  --target "$VIBE_REAL_CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$VIBE_REAL_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  --approval-id "$VIBE_REAL_UPGRADE_APPROVAL" \
  --output "$VIBE_REAL_UPGRADE_REQUEST" \
  > "$VIBE_PRIVATE_EVIDENCE/real-upgrade-prepared.json"
VIBE_REAL_REQUEST_DIGEST="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["request_digest"])' "$VIBE_REAL_UPGRADE_REQUEST")"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" upgrade \
  --request "$VIBE_REAL_UPGRADE_REQUEST" \
  --confirm-request "$VIBE_REAL_REQUEST_DIGEST" \
  > "$VIBE_PRIVATE_EVIDENCE/real-upgrade-result.json"
VIBE_REAL_UPGRADE_RECEIPT="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["receipt"])' "$VIBE_PRIVATE_EVIDENCE/real-upgrade-result.json")"
VIBE_REAL_UPGRADE_JOURNAL="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["journal"])' "$VIBE_REAL_UPGRADE_RECEIPT")"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" inspect-upgrade \
  --journal "$VIBE_REAL_UPGRADE_JOURNAL" \
  > "$VIBE_PRIVATE_EVIDENCE/real-upgrade-inspection.json"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" verify \
  --target "$VIBE_REAL_CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$VIBE_REAL_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json" \
  > "$VIBE_PRIVATE_EVIDENCE/real-upgrade-verify.json"
python3 - \
  "$VIBE_PRIVATE_EVIDENCE/real-upgrade-inspection.json" \
  "$VIBE_PRIVATE_EVIDENCE/real-upgrade-verify.json" <<'PY'
import json
from pathlib import Path
import sys

for result_path in map(Path, sys.argv[1:]):
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("status") != "verified":
        raise SystemExit(f"unverified result: {result_path.name}")
PY
```

只执行与 `real-preflight.json` 中 `install_mode` 相符的一条路径。受控升级必须先确认检查结果和活动安装核验结果的状态为 `verified`，然后才能进入 Codex App 用户观察。漂移、拒绝、恢复必需或未知均停止。

## 9. Codex App 用户观察

刷新或重新打开 Codex App 后，新建任务。确认只有一个技术标识 `vibe-project-lead-zh`，显示名为“中文跨项目研发主管”，locator 与真实安装根一致。公开收据只记录 locator 的 SHA-256、匹配数量和状态，不记录绝对路径。

```text
使用 $vibe-project-lead-zh。先绑定当前这个合成测试项目，只读调查。
请确认主管定位和权限边界，不部署、不安装依赖、不访问外部 Jev。
```

再用不含业务信息的两份合成文本，要求显式调用已安装的本地证据筛选器查找一个关键词。用户应看到来源引用、字节范围和本地模式；无匹配不能解释为事实不存在。

## 10. 候选回滚并确认原状态

恢复需要新的独立批准。首次安装使用 `rollback`：

```bash
set -euo pipefail
VIBE_REAL_MANIFEST="$VIBE_REAL_CODEX_HOME/skills/.vibe-project-lead-zh-install/install-manifest.json"
VIBE_REAL_MANIFEST_DIGEST="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["manifest_digest"])' "$VIBE_REAL_MANIFEST")"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" rollback \
  --target "$VIBE_REAL_CODEX_HOME/skills/vibe-project-lead-zh" \
  --manifest "$VIBE_REAL_MANIFEST" --confirm "$VIBE_REAL_MANIFEST_DIGEST" \
  > "$VIBE_PRIVATE_EVIDENCE/real-rollback.json"
```

受控升级使用成功收据执行 `restore-version`：

```bash
set -euo pipefail
VIBE_REAL_UPGRADE_RECEIPT="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["receipt"])' "$VIBE_PRIVATE_EVIDENCE/real-upgrade-result.json")"
VIBE_REAL_RESTORE_CONFIRM="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["restore_confirmation_digest"])' "$VIBE_REAL_UPGRADE_RECEIPT")"
VIBE_REAL_RESTORE_APPROVAL="MAC-3.1-REAL-RESTORE-$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_HOME="$VIBE_REAL_CODEX_HOME" python3 -B \
  "$VIBE_RELEASE_ROOT/scripts/install_skill.py" restore-version \
  --receipt "$VIBE_REAL_UPGRADE_RECEIPT" \
  --approval-id "$VIBE_REAL_RESTORE_APPROVAL" \
  --confirm "$VIBE_REAL_RESTORE_CONFIRM" \
  > "$VIBE_PRIVATE_EVIDENCE/real-restore-result.json"
```

刷新 Codex App。首次安装应恢复未安装状态；升级应恢复原活动版本和原 manifest。用户实际观察与文件核验都要记录，命令退出 0 不能单独证明原状态。

## 11. 私有原始证据和公开去标识收据

**私有原始证据**保留真实路径、完整 Release API JSON、资产索引、安装/升级/恢复收据、journal 和失败日志，访问权限保持 `0700`。绝对路径只保留在私有原始证据。

**公开去标识收据**只包含：仓库身份和标签、提交 OID、三个资产 SHA-256、系统/架构/CPython 版本、各步骤状态码、13 文件计数、manifest/journal 摘要、locator SHA-256 与唯一匹配数量、用户观察状态、恢复状态和清理状态。不得包含用户名、主目录、绝对路径、Token、序列号、硬件 UUID、完整 JSON、原始日志或业务文本。公开收据从私有原始证据提取这些有界字段，不能直接复制原始收据。

## 12. 清理与残留

技术检查成功后，先记录测试自有目录中的清理前残留；失败或 `UNKNOWN` 时保留现场并停止。只有用户确认私有证据已保留后，才删除 `VIBE_ACCEPTANCE_ROOT`，不能删除 `VIBE_REAL_CODEX_HOME` 或其他未知目录。

```bash
set -euo pipefail
find "$VIBE_ACCEPTANCE_ROOT" -mindepth 1 -maxdepth 4 -print \
  > "$VIBE_PRIVATE_EVIDENCE/residue-before-cleanup.txt"
python3 - "$VIBE_ACCEPTANCE_ROOT" <<'PY'
from pathlib import Path
import shutil
import sys

target = Path(sys.argv[1]).resolve(strict=True)
if not target.name.startswith("vibe-leader-3.1."):
    raise SystemExit("unsafe cleanup target")
shutil.rmtree(target)
PY
test ! -e "$VIBE_ACCEPTANCE_ROOT"
printf '%s\n' 'acceptance_root_removed=true' \
  > "$VIBE_PRIVATE_EVIDENCE/residue-after-cleanup.txt"
```

公开去标识收据只写 `cleanup_status` 和残留数量，不公开 `find` 的原始路径列表。

## 13. 稳定版实装

正式 Release 必须从相同公共仓库的 `v3.1.0` 元数据重新开始第 2–4 节，并逐项核对提交和三个 GitHub API digest。正式安装/升级、Codex App 用户观察和保留稳定版活动状态分别取得新的外部批准。

与已接受候选相比，正式标签的提交或三个资产摘要任一不同，必须停止晋升并生成新的候选；不得把不同字节作为稳定版继续安装。只有提交与三个资产字节完全一致时，候选恢复证据才可作为支持材料。此时若**环境或安装前状态**任一改变，稳定版实装仍必须**重新执行真实恢复演练**；不得用候选回滚、平台上传成功或相近机器结果替代。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
