# Vibe Leader 3.1 RC3 macOS Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不占用用户实体 Mac 的前提下加固 3.1 验收流程，并用 GitHub 托管的 Apple Silicon macOS 运行器证明 RC3 的真实 Darwin 技术链，使用户 Mac 最后只承担一次 Codex App 实装验收。

**Architecture:** 保留现有安装器、证据筛选器和发行工具的实现边界；先用静态合同约束 macOS 指南中的解释器、网络和阶段顺序，再增加只在 Darwin 上执行的原生生命周期集成测试。GitHub Actions 在 `macos-14`、CPython 3.11/3.14 上运行同一代码和完整回归；任何原生失败都回到聚焦测试和最小修复，不由实体 Mac 承担首次发现。

**Tech Stack:** CPython 3.11–3.14 标准库、`unittest`、Bash、Git、GitHub Actions、macOS 14 Apple Silicon、现有 `install_skill.py` 与 `release_archive.py` CLI。

## Global Constraints

- 实施基线固定为提交 `29a187c`，其父级 RC2 提交为 `8788a9fd84929309e1739ebf69a3d31195c2d9e0`；实施分支为 `codex/vibe-leader-3.1-macos`。
- 设计来源固定为 `docs/superpowers/specs/2026-09-28-vibe-leader-3.1-rc3-macos-hardening-design.md`。
- 最低兼容版本为稳定版 CPython 3.11，支持区间为 `3.11 <= Python < 3.15`；低于 3.11 返回 `PYTHON_UPDATE_REQUIRED`，非 CPython、预发布版、无法解析版本和 3.15+ 返回 `RUNTIME_UNVERIFIED`。
- 通用目标为 macOS 14+、Apple Silicon M1+、`arm64`；Intel Mac、Windows 原生、网络盘和外接盘不在本轮支持范围。
- 可安装 Skill 保持精确 13 个文件；不修改 `SKILL.md` 的主管定位、授权、主循环或显式调用合同。
- 不接入外部 Jev、第三方模型、凭据、后台服务、真实业务数据或项目级模块调度。
- 所有技术写入必须位于测试自有临时目录；GitHub Actions 不读取、搜索、创建或修改真实 `CODEX_HOME`。
- 不自动安装 Python、Homebrew、Git 或 Codex App；不硬编码用户名、Home 目录或 Codex App 内部缓存路径。
- 网络操作不做无界重试。GitHub HTTP 从第一次请求使用 HTTP/1.1，下载先写 `.part`，摘要和身份核对后才形成最终文件。
- GitHub Actions 权限固定为 `contents: read`，不读取 secret、不发布、不 push、不写外部系统。
- 本地阶段不得 push、触发公开 Actions、创建 RC3 Release、更新 Hugging Face 或写入用户 Mac。
- 公开候选分支 push、RC3 Release 和实体 Mac 真实安装或升级分别取得明确批准；旧批准不继承到新的提交或外部动作。
- RC1、RC2 保持历史 prerelease；新字节只能形成 `v3.1.0-rc.3`，不得覆盖既有标签或资产。

## File Map

| File | Responsibility |
| --- | --- |
| `tests/test_macos_acceptance_contract.py` | 静态验证解释器绑定、HTTP/1.1、`.part` 落盘、阶段顺序、Bash 语法和 Actions 安全边界 |
| `docs/macos-acceptance-3.1.md` | 提供通用 Apple Silicon 验收命令；先完成隔离技术链，再进入真实根预检和独立批准 |
| `tests/test_macos_native_acceptance.py` | 仅在 Darwin 原生运行 `statfs`、交换探针、发行包、临时首次安装及临时升级恢复 |
| `.github/workflows/macos-native.yml` | 在 `macos-14` arm64、Python 3.11/3.14 上运行原生聚焦检查和完整回归 |
| `tests/test_skill_contract.py` | 保持 3.1 公开材料、证据边界和 13 文件合同一致，更新被新版命令替换的精确断言 |
| `docs/release-3.1.0.md` | 说明三层验证、GitHub macOS 技术门、RC3 修复范围及仍需实体 Mac 证明的内容 |

生产脚本只有在原生 CI 暴露可复现缺陷时才允许修改。此时先在现有聚焦测试中加入失败用例，再对 `scripts/install_skill.py`、`scripts/release_archive.py` 或 `skill/vibe-project-lead-zh/scripts/evidence_filter.py` 做最小修复，并重新执行本计划全部验证。

---

### Task 1: 锁定 macOS 验收指南合同

**Files:**
- Create: `tests/test_macos_acceptance_contract.py`
- Modify: `docs/macos-acceptance-3.1.md:1-430`
- Modify: `tests/test_skill_contract.py:510-680`

**Interfaces:**
- Consumes: 当前验收指南中的公共仓库、三个资产、临时安装、升级恢复和真实安装命令。
- Produces: 单一 `VIBE_PYTHON` 解释器合同；`curl --http1.1` + `.part` 资产合同；`git -c http.version=HTTP/1.1` 仓库合同；隔离阶段早于真实根阶段的固定顺序。

- [ ] **Step 1: 写验收指南静态失败测试**

创建 `tests/test_macos_acceptance_contract.py`，先加入以下解析器和合同。当前指南应因裸 `python3`、普通 curl、普通 git 和真实根预检顺序而失败：

```python
# SPDX-License-Identifier: MPL-2.0
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "macos-acceptance-3.1.md"


def guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", text, flags=re.DOTALL)


def continued_commands(text: str) -> str:
    return text.replace("\\\n", " ")


class MacAcceptanceGuideContractTests(unittest.TestCase):
    def test_one_python_is_bound_and_reused(self):
        text = guide_text()
        self.assertEqual(text.count("command -v python3"), 1)
        without_bootstrap = text.replace("command -v python3", "")
        self.assertNotRegex(without_bootstrap, r"(?<![A-Za-z0-9_$])python3(?:\s|$)")
        self.assertIn('VIBE_PYTHON="${VIBE_PYTHON:-', text)
        self.assertGreaterEqual(text.count('"$VIBE_PYTHON" -B'), 15)

    def test_github_transport_is_http1_and_staged(self):
        commands = continued_commands(guide_text())
        curl_commands = re.findall(r"(?m)^\s*curl\b[^\n]+", commands)
        self.assertGreaterEqual(len(curl_commands), 2)
        self.assertTrue(all("--http1.1" in command for command in curl_commands))
        self.assertIn(".part", commands)
        self.assertIn("git -c http.version=HTTP/1.1 clone", commands)
        self.assertIn("git -c http.version=HTTP/1.1 -C", commands)

    def test_isolated_checks_precede_real_codex_home(self):
        text = guide_text()
        isolated = text.split("## 2. 绑定公共仓库", 1)[1].split(
            "## 8. 真实根只读预检", 1
        )[0]
        self.assertNotIn("VIBE_REAL_CODEX_HOME", isolated)
        self.assertLess(
            text.index("## 5. 原生 Mac 证据筛选技术检查"),
            text.index("## 6. 隔离临时 `CODEX_HOME` 首次安装"),
        )
        self.assertLess(
            text.index("## 7. 第二个临时根完成 3.0.3→3.1 升级和恢复"),
            text.index("## 8. 真实根只读预检"),
        )

    def test_every_bash_block_parses(self):
        blocks = bash_blocks(guide_text())
        self.assertGreaterEqual(len(blocks), 10)
        for index, block in enumerate(blocks):
            with self.subTest(index=index):
                result = subprocess.run(
                    ["bash", "-n"], input=block, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
```

- [ ] **Step 2: 运行静态合同并确认按预期失败**

Run:

```bash
python3 -B -m unittest -v tests.test_macos_acceptance_contract
```

Expected: FAIL；失败分别指出裸 `python3`、缺少 `--http1.1` 或阶段 8 标题不存在，不能出现导入错误。

- [ ] **Step 3: 在指南开头绑定并验证唯一 Python**

把系统摘要段替换为以下入口。该代码不安装 Python；解释器不合格时输出稳定原因并以 4 退出：

```bash
set -euo pipefail
sw_vers
test "$(uname -m)" = "arm64"
sysctl -n machdep.cpu.brand_string
VIBE_PYTHON="${VIBE_PYTHON:-$(command -v python3 || true)}"
test -n "$VIBE_PYTHON"
case "$VIBE_PYTHON" in
  /*) ;;
  *) printf '%s\n' 'RUNTIME_UNVERIFIED' >&2; exit 4 ;;
esac
VIBE_PYTHON="$("$VIBE_PYTHON" -B - <<'PY'
import os
import platform
import stat
import sys
from pathlib import Path

reason = None
if platform.python_implementation() != "CPython":
    reason = "RUNTIME_UNVERIFIED"
elif sys.version_info.releaselevel != "final":
    reason = "RUNTIME_UNVERIFIED"
elif sys.version_info < (3, 11):
    reason = "PYTHON_UPDATE_REQUIRED"
elif sys.version_info >= (3, 15):
    reason = "RUNTIME_UNVERIFIED"
elif platform.machine() != "arm64":
    reason = "RUNTIME_UNVERIFIED"
try:
    executable = Path(sys.executable).resolve(strict=True)
    metadata = executable.stat()
except OSError:
    reason = "RUNTIME_UNVERIFIED"
if reason is None and (not stat.S_ISREG(metadata.st_mode) or not os.access(executable, os.X_OK)):
    reason = "RUNTIME_UNVERIFIED"
if reason is not None:
    print(reason, file=sys.stderr)
    raise SystemExit(4)
print(executable)
PY
)"
"$VIBE_PYTHON" -B -c \
  'import platform, sys; print(platform.python_implementation(), platform.python_version(), sys.version_info.releaselevel, platform.machine())'
```

将指南内其余所有 Python 调用替换为 `"$VIBE_PYTHON" -B`；包括内联 JSON 读取、unittest、发行工具、安装器和安全清理。更新 `tests/test_skill_contract.py` 中旧的精确命令断言。

- [ ] **Step 4: 固定 HTTP/1.1 和 `.part` 落盘**

所有 API 和资产 curl 增加 `--http1.1`。Release JSON 先写 `release-api.json.part`，用已绑定 Python 验证后通过同目录硬链接排他形成最终文件。资产循环遵守相同规则：

```bash
VIBE_ASSET_FINAL="$VIBE_ASSET_DIR/$VIBE_ASSET_NAME"
VIBE_ASSET_PART="$VIBE_ASSET_FINAL.part"
test ! -e "$VIBE_ASSET_FINAL" && test ! -e "$VIBE_ASSET_PART"
curl --http1.1 --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  "$VIBE_ASSET_URL" --output "$VIBE_ASSET_PART"
VIBE_ACTUAL_DIGEST="$(shasum -a 256 "$VIBE_ASSET_PART" | awk '{print $1}')"
test "$VIBE_ACTUAL_DIGEST" = "$VIBE_ASSET_DIGEST"
ln "$VIBE_ASSET_PART" "$VIBE_ASSET_FINAL"
rm "$VIBE_ASSET_PART"
```

GitHub clone/fetch 命令全部显式加入 HTTP/1.1，初始 clone 写入测试根的 `.part` 目录，核对 origin、tag 和 commit 后再改名：

```bash
VIBE_TRUSTED_REPO_PART="$VIBE_TRUSTED_REPO.part"
git -c http.version=HTTP/1.1 clone --no-checkout \
  "$VIBE_PUBLIC_REPO_URL" "$VIBE_TRUSTED_REPO_PART"
test "$(git -C "$VIBE_TRUSTED_REPO_PART" remote get-url origin)" = "$VIBE_PUBLIC_REPO_URL"
git -c http.version=HTTP/1.1 -C "$VIBE_TRUSTED_REPO_PART" fetch --force origin \
  "refs/tags/$VIBE_RELEASE_TAG:refs/tags/$VIBE_RELEASE_TAG"
test ! -e "$VIBE_TRUSTED_REPO"
mv "$VIBE_TRUSTED_REPO_PART" "$VIBE_TRUSTED_REPO"
```

不得加入 `--retry`、循环重试、删除失败现场或摘要不符后的重新下载。

- [ ] **Step 5: 重排真实根预检并明确阶段边界**

固定标题和顺序：

```text
1 核对系统摘要并绑定唯一 Python
2 绑定公共仓库、标签和私有证据目录
3 用 GitHub Release API 绑定三个下载资产
4 验证和解包
5 原生 Mac 证据筛选技术检查
6 隔离临时 CODEX_HOME 首次安装
7 第二个临时根完成 3.0.3→3.1 升级和恢复
8 真实根只读预检
9 真实安装或升级：单独外部批准
10 Codex App 用户观察
11 候选回滚并确认原状态
12 私有原始证据和公开去标识收据
13 清理与残留
14 稳定版实装
```

第 8 节之前不得出现读取、设置或验证 `VIBE_REAL_CODEX_HOME` 的命令。第 8 节只运行 `preflight`；真实写入继续放在第 9 节。

- [ ] **Step 6: 运行聚焦合同**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_macos_acceptance_contract \
  tests.test_skill_contract.RuleAlignmentContractTests.test_3_1_public_materials_define_macos_candidate_boundary
```

Expected: PASS；所有 Bash fence 通过 `bash -n`，旧裸 `python3` 精确断言已经替换。

- [ ] **Step 7: 提交验收指南加固**

```bash
git add docs/macos-acceptance-3.1.md tests/test_macos_acceptance_contract.py tests/test_skill_contract.py
git commit -m "docs: harden macOS acceptance transport"
```

---

### Task 2: 增加真实 Darwin 原生集成测试

**Files:**
- Create: `tests/test_macos_native_acceptance.py`

**Interfaces:**
- Consumes: `scripts/install_skill.py` 的 `_darwin_statfs()`、`probe_switch_capability()` 和 CLI；`scripts/release_archive.py` CLI；固定基线提交 `d0e17544584fb6c68254c49b87f4275fa19a71f4`。
- Produces: 只在 `sys.platform == "darwin"` 且 `platform.machine() == "arm64"` 时执行的原生证据；所有路径位于 `tempfile.TemporaryDirectory()`。

- [ ] **Step 1: 创建平台门、CLI helper 和原生内核测试**

创建测试文件，使用真实系统调用，不 patch `ctypes.CDLL`、`sys.platform` 或安装器切换原语：

```python
# SPDX-License-Identifier: MPL-2.0
import importlib.util
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = ROOT / "scripts" / "install_skill.py"
RELEASE_SCRIPT = ROOT / "scripts" / "release_archive.py"
SKILL_NAME = "vibe-project-lead-zh"
BASELINE_COMMIT = "d0e17544584fb6c68254c49b87f4275fa19a71f4"

def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

INSTALLER = load_module("mac_native_installer", INSTALLER_SCRIPT)

@unittest.skipUnless(sys.platform == "darwin", "Darwin native evidence only")
class MacNativeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.machine(), "arm64")
        self.temporary = tempfile.TemporaryDirectory(prefix="vibe-macos-native-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_json(self, script: Path, *arguments: object, codex_home: Path | None = None):
        environment = os.environ.copy()
        environment.pop("CODEX_HOME", None)
        if codex_home is not None:
            environment["CODEX_HOME"] = str(codex_home)
        result = subprocess.run(
            [sys.executable, "-B", str(script), *map(str, arguments)],
            cwd=ROOT, env=environment, check=False,
            capture_output=True, text=True, timeout=180,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result, json.loads(result.stdout)

    def test_native_statfs_and_exchange_probe(self):
        skills_root = self.root / "skills"
        skills_root.mkdir()
        observed = INSTALLER._darwin_statfs(skills_root)
        self.assertEqual(observed["device"], os.lstat(skills_root).st_dev)
        self.assertTrue(Path(observed["mount_target"]).is_absolute())
        self.assertTrue(observed["filesystem_type"])
        capability, evidence = INSTALLER.probe_switch_capability(skills_root)
        self.assertEqual(capability, "EXCHANGE_SUPPORTED")
        self.assertEqual(evidence["exchange_result"], "VERIFIED")
        self.assertTrue(evidence["postconditions_verified"])
        self.assertTrue(evidence["restored"])
        self.assertEqual(list(skills_root.iterdir()), [])
```

- [ ] **Step 2: 增加冻结提交发行包原生测试**

在同一测试类加入确定性 build、verify、extract。工作树必须干净，测试绑定 `HEAD^{commit}`，连续构建的三个资产逐字节相同，并核对解包后的 Skill 仍为 13 文件：

```python
    def git(self, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), *arguments],
            stderr=subprocess.PIPE, text=True,
        ).strip()

    def test_release_archive_build_verify_extract_is_native(self):
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), "")
        commit = self.git("rev-parse", "HEAD^{commit}")
        first, second = self.root / "assets-a", self.root / "assets-b"
        first.mkdir(); second.mkdir()
        for destination in (first, second):
            result, payload = self.run_json(
                RELEASE_SCRIPT, "build", "--repo", ROOT,
                "--commit", commit, "--output-dir", destination,
            )
            self.assertEqual(result.returncode, 0, payload)
        for name in ("Vibe-Leader-3.1.0-GitHub.zip", "release_archive.py", "SHA256SUMS.txt"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        downloaded = first / "release_archive.py"
        archive = first / "Vibe-Leader-3.1.0-GitHub.zip"
        checksums = first / "SHA256SUMS.txt"
        result, payload = self.run_json(
            downloaded, "verify", "--archive", archive,
            "--checksums", checksums, "--expected-commit", commit,
        )
        self.assertEqual(result.returncode, 0, payload)
        extracted = self.root / "extracted"
        result, payload = self.run_json(
            downloaded, "extract", "--archive", archive,
            "--checksums", checksums, "--expected-commit", commit,
            "--destination", extracted,
        )
        self.assertEqual(result.returncode, 0, payload)
        skill = extracted / "Vibe-Leader-3.1.0" / "skill" / SKILL_NAME
        self.assertEqual(len([path for path in skill.rglob("*") if path.is_file()]), 13)
```

- [ ] **Step 3: 增加临时首次安装、升级和恢复测试**

增加第三个测试：用当前源码完成临时首次安装与 verify；再用 `git clone --local --no-hardlinks` 创建测试自有 baseline repo，detach 到固定 v3.0.3 提交。使用 3.1 安装器把旧 Skill 装入第二个临时根，再依次执行 `prepare-upgrade`、`upgrade`、`inspect-upgrade`、`verify`、`restore-version`，最终逐字节比较原 manifest，并比较恢复前后 `canonical_tree_digest(scan_tree(target))`。

关键命令必须使用以下固定参数和测试专用批准值：

```python
result, payload = self.run_json(
    INSTALLER_SCRIPT, "prepare-upgrade",
    "--source", current_source, "--target", target, "--manifest", manifest,
    "--approval-id", "MAC-NATIVE-CI-UPGRADE", "--output", request,
    codex_home=upgrade_home,
)
request_payload = json.loads(request.read_text(encoding="utf-8"))
result, payload = self.run_json(
    INSTALLER_SCRIPT, "upgrade", "--request", request,
    "--confirm-request", request_payload["request_digest"],
    codex_home=upgrade_home,
)
receipt = Path(payload["receipt"])
receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
result, restored = self.run_json(
    INSTALLER_SCRIPT, "restore-version", "--receipt", receipt,
    "--approval-id", "MAC-NATIVE-CI-RESTORE",
    "--confirm", receipt_payload["restore_confirmation_digest"],
    codex_home=upgrade_home,
)
```

所有 `fresh_home`、`upgrade_home`、`baseline_repo` 和 receipt 路径都必须满足 `path.is_relative_to(self.root)`。不得读取环境中已有的 `CODEX_HOME`；helper 在未显式传入时应删除该环境变量。

- [ ] **Step 4: 在 WSL 验证原生测试不会伪造成功**

Run:

```bash
python3 -B -m unittest -v tests.test_macos_native_acceptance
```

Expected: 三个测试明确显示 `skipped 'Darwin native evidence only'`；退出 0 只证明“不适用”，不能记录为 macOS 通过。

- [ ] **Step 5: 运行现有模拟合同回归**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_install_skill.DarwinFilesystemIdentityTests \
  tests.test_install_skill.DarwinRenameBackendTests \
  tests.test_evidence_filter
```

Expected: PASS；`tests.test_evidence_filter` 的 failure、error、skip 均为 0。

- [ ] **Step 6: 提交原生集成测试**

```bash
git add tests/test_macos_native_acceptance.py
git commit -m "test: add native macOS lifecycle gate"
```

---

### Task 3: 增加 GitHub Apple Silicon 工作流

**Files:**
- Create: `.github/workflows/macos-native.yml`
- Modify: `tests/test_macos_acceptance_contract.py`

**Interfaces:**
- Consumes: Task 2 的 `tests.test_macos_native_acceptance`；GitHub 托管 `macos-14`；Python 3.11 与 3.14。
- Produces: 只读、无 secret、无发布能力的两项原生 job；公开候选分支 push 后自动运行。

- [ ] **Step 1: 写工作流缺失与安全边界失败测试**

在 `tests/test_macos_acceptance_contract.py` 增加 `MacWorkflowContractTests`，要求以下精确片段：

```python
WORKFLOW = ROOT / ".github" / "workflows" / "macos-native.yml"

class MacWorkflowContractTests(unittest.TestCase):
    def test_workflow_is_arm64_read_only_and_pinned(self):
        self.assertTrue(WORKFLOW.is_file())
        text = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "runs-on: macos-14",
            "contents: read",
            'python-version: ${{ matrix.python-version }}',
            'python-version: ["3.11.14", "3.14.7"]',
            "fetch-depth: 0",
            "tests.test_macos_native_acceptance",
            "tests.test_evidence_filter",
            "python -B -m unittest discover -s tests -v",
            "cancel-in-progress: true",
            "codex/vibe-leader-3.1-macos",
        ):
            self.assertIn(required, text)
        self.assertIn("actions/checkout@11d5960a326750d5838078e36cf38b85af677262", text)
        self.assertIn("actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1", text)
        for forbidden in ("secrets.", "contents: write", "pull-requests: write", "git push"):
            self.assertNotIn(forbidden, text)
```

- [ ] **Step 2: 运行测试并确认工作流缺失**

Run:

```bash
python3 -B -m unittest -v tests.test_macos_acceptance_contract.MacWorkflowContractTests
```

Expected: FAIL at `WORKFLOW.is_file()`。

- [ ] **Step 3: 创建固定权限和固定 action SHA 的工作流**

创建 `.github/workflows/macos-native.yml`：

```yaml
name: macOS native gate

on:
  push:
    branches:
      - codex/vibe-leader-3.1-macos
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: macos-native-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  native:
    name: macOS 14 arm64 / Python ${{ matrix.python-version }}
    runs-on: macos-14
    timeout-minutes: 30
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11.14", "3.14.7"]
    steps:
      - name: Check out frozen candidate history
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          fetch-depth: 0
      - name: Select CPython
        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1
        with:
          python-version: ${{ matrix.python-version }}
      - name: Record bounded platform facts
        shell: bash
        run: |
          set -euo pipefail
          sw_vers
          test "$(uname -m)" = "arm64"
          python -B -c 'import platform, sys; print(platform.python_implementation(), platform.python_version(), sys.version_info.releaselevel, platform.machine())'
          git rev-parse HEAD
      - name: Run native focused gates
        shell: bash
        run: |
          set -euo pipefail
          python -B -m unittest -v tests.test_evidence_filter tests.test_macos_native_acceptance 2>&1 | tee "$RUNNER_TEMP/native-focused.log"
          ! grep -Eq 'FAILED|errors=[1-9]|failures=[1-9]|skipped=[1-9]' "$RUNNER_TEMP/native-focused.log"
      - name: Run complete regression
        shell: bash
        run: |
          set -euo pipefail
          python -B -m unittest discover -s tests -v
```

`actions/checkout` 的固定 SHA 已从官方 `refs/tags/v4` 核对为 `11d5960a326750d5838078e36cf38b85af677262`；`actions/setup-python` 的固定 SHA已从官方 `refs/tags/v6` 核对为 `ece7cb06caefa5fff74198d8649806c4678c61a1`。实施时只读复核，不自动跟随 tag 漂移。

- [ ] **Step 4: 运行工作流静态合同**

Run:

```bash
python3 -B -m unittest -v tests.test_macos_acceptance_contract
git diff --check
```

Expected: PASS；工作流无 secret、写权限和 push 命令。

- [ ] **Step 5: 提交 macOS 工作流**

```bash
git add .github/workflows/macos-native.yml tests/test_macos_acceptance_contract.py
git commit -m "ci: add Apple Silicon macOS gate"
```

---

### Task 4: 更新 3.1 证据边界和 RC3 说明

**Files:**
- Modify: `tests/test_skill_contract.py:510-680`
- Modify: `docs/release-3.1.0.md:1-63`

**Interfaces:**
- Consumes: 三层验证设计、公开仓库分支门、RC3 不覆盖 RC2 的生命周期边界。
- Produces: 包内只描述功能和门，不自称 CI、RC3 Release 或实体 Mac 已经通过。

- [ ] **Step 1: 写公开材料失败断言**

在 `test_3_1_public_materials_define_macos_candidate_boundary` 中要求：

```python
self.assert_contains_all(
    release,
    (
        "GitHub Apple Silicon 技术门",
        "macos-14",
        "Python 3.11 与 3.14",
        "不读取真实 `CODEX_HOME`",
        "不能替代 Codex App 用户观察",
        "RC1、RC2",
        "v3.1.0-rc.3",
    ),
)
```

并把 `"GitHub macOS 验证已通过"`、`"RC3 已发布"`、`"Mac 实装验收已经完成"` 加入 forbidden 集合。

- [ ] **Step 2: 运行断言并确认缺少新边界**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_skill_contract.RuleAlignmentContractTests.test_3_1_public_materials_define_macos_candidate_boundary
```

Expected: FAIL，原因是发布说明尚未包含新门。

- [ ] **Step 3: 更新发布说明**

加入以下章节：

```markdown
## RC3 验证加固

3.1 的候选验证增加 GitHub Apple Silicon 技术门：在 `macos-14`、`arm64` 上分别使用 Python 3.11 与 3.14，运行真实 Darwin 文件系统身份、目录交换、证据筛选、确定性发行包、临时首次安装以及临时升级恢复。该门只使用测试自有临时目录，不读取真实 `CODEX_HOME`，也不能替代 Codex App 用户观察。

验收指南同时固定单一 Python 解释器、GitHub HTTP/1.1 和 `.part` 下载，并把真实根只读预检移到全部隔离技术检查之后。任何代码、文档或资产字节变化都必须形成新提交和 `v3.1.0-rc.3`；RC1、RC2 保留为历史 prerelease，不被覆盖。

包内文字只定义门和边界，不声明 GitHub macOS 验证已通过、RC3 已发布或 Mac 实装验收已经完成。实际状态由外部 Actions、Release 和用户观察收据建立。
```

- [ ] **Step 4: 运行公开材料聚焦回归**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_skill_contract.RuleAlignmentContractTests.test_3_1_public_materials_define_macos_candidate_boundary \
  tests.test_macos_acceptance_contract
```

Expected: PASS。

- [ ] **Step 5: 提交发布边界更新**

```bash
git add docs/release-3.1.0.md tests/test_skill_contract.py
git commit -m "docs: define RC3 macOS verification gate"
```

---

### Task 5: 本地完整回归和确定性候选预构建

**Files:**
- Verify only: entire repository

**Interfaces:**
- Consumes: Tasks 1–4 的所有提交。
- Produces: 干净 HEAD、本地完整回归记录、两次逐字节一致的 RC3 预构建资产；不发布。

- [ ] **Step 1: 运行聚焦测试**

```bash
python3 -B -m unittest -v \
  tests.test_macos_acceptance_contract \
  tests.test_macos_native_acceptance \
  tests.test_evidence_filter \
  tests.test_install_skill.DarwinFilesystemIdentityTests \
  tests.test_install_skill.DarwinRenameBackendTests \
  tests.test_release_archive \
  tests.test_skill_contract.RuleAlignmentContractTests.test_3_1_public_materials_define_macos_candidate_boundary
```

Expected on WSL: PASS；只有 `tests.test_macos_native_acceptance` 的三个 Darwin 原生测试明确 skip，`tests.test_evidence_filter` 无 skip。

- [ ] **Step 2: 运行完整回归**

```bash
python3 -B -m unittest discover -s tests -v
```

Expected: PASS；记录测试总数和已有环境型 skip，任何新增 failure/error 先修复。

- [ ] **Step 3: 检查源码和文档卫生**

```bash
git diff --check
git status --short
rg -n 'AKIA|ghp_|github_pat_|hf_[A-Za-z0-9]|/Users/[^/[:space:]`]+|/home/[^/[:space:]`]+' \
  .github docs tests scripts skill README.md CHANGELOG.md \
  --glob '!docs/superpowers/specs/**' --glob '!docs/superpowers/plans/**'
```

Expected: 工作树干净；没有新增真实 Token、用户名或个人绝对路径。

- [ ] **Step 4: 从冻结 HEAD 连续构建两份候选资产**

```bash
set -euo pipefail
VIBE_RC3_HEAD="$(git rev-parse HEAD^{commit})"
VIBE_RC3_BUILD_A="$(mktemp -d /tmp/vibe-rc3-a.XXXXXX)"
VIBE_RC3_BUILD_B="$(mktemp -d /tmp/vibe-rc3-b.XXXXXX)"
python3 -B scripts/release_archive.py build --repo . --commit "$VIBE_RC3_HEAD" --output-dir "$VIBE_RC3_BUILD_A"
python3 -B scripts/release_archive.py build --repo . --commit "$VIBE_RC3_HEAD" --output-dir "$VIBE_RC3_BUILD_B"
cmp "$VIBE_RC3_BUILD_A/Vibe-Leader-3.1.0-GitHub.zip" "$VIBE_RC3_BUILD_B/Vibe-Leader-3.1.0-GitHub.zip"
cmp "$VIBE_RC3_BUILD_A/release_archive.py" "$VIBE_RC3_BUILD_B/release_archive.py"
cmp "$VIBE_RC3_BUILD_A/SHA256SUMS.txt" "$VIBE_RC3_BUILD_B/SHA256SUMS.txt"
python3 -B "$VIBE_RC3_BUILD_A/release_archive.py" verify \
  --archive "$VIBE_RC3_BUILD_A/Vibe-Leader-3.1.0-GitHub.zip" \
  --checksums "$VIBE_RC3_BUILD_A/SHA256SUMS.txt" \
  --expected-commit "$VIBE_RC3_HEAD"
```

Expected: 三个 `cmp` 和 verify 均退出 0；这些只是本地预构建。

- [ ] **Step 5: 检查提交序列和干净状态**

```bash
git log --oneline --decorate 29a187c..HEAD
git status --short --branch
```

Expected: 仅包含本计划聚焦提交，工作树干净。

---

### Task 6: 公开候选分支和 GitHub macOS 原生验证

**Files:**
- External operation only after explicit approval

**Interfaces:**
- Consumes: Task 5 的干净、已验证 HEAD。
- Produces: `MsrWang/vibe-leader` 的 `codex/vibe-leader-3.1-macos` 分支回读和两项 `macos-14` job 结果；不创建 Release。

- [ ] **Step 1: 停止并取得候选分支 push 的明确批准**

报告公开仓库、目标分支、精确 HEAD、将触发两个 macOS job、不会创建 Release/Hugging Face 更新。没有用户明确批准时不得继续。

- [ ] **Step 2: 只读核对公开目标和分支状态**

```bash
set -euo pipefail
VIBE_PUBLIC_URL="https://github.com/MsrWang/vibe-leader.git"
VIBE_RC3_HEAD="$(git rev-parse HEAD^{commit})"
git ls-remote "$VIBE_PUBLIC_URL" HEAD refs/heads/main refs/heads/codex/vibe-leader-3.1-macos
```

Expected: 仓库可访问；目标分支若不是当前候选的可快进祖先，停止，不 force push。

- [ ] **Step 3: 推送精确 HEAD 到专用候选分支**

```bash
git push https://github.com/MsrWang/vibe-leader.git \
  HEAD:refs/heads/codex/vibe-leader-3.1-macos
```

Expected: push 成功；`git ls-remote` 回读 OID 必须等于 `VIBE_RC3_HEAD`。

- [ ] **Step 4: 等待并核对两项 macOS job**

平台回读必须同时确认：

```text
macOS 14 arm64 / Python 3.11 = success
macOS 14 arm64 / Python 3.14 = success
headSha = VIBE_RC3_HEAD
event = push
branch = codex/vibe-leader-3.1-macos
```

任一 job 为 failure、cancelled、timed_out、action_required 或结论缺失时停止并保存首次失败日志；不得直接 rerun 制造成功。修复形成新 HEAD 后，旧 push 批准不自动覆盖新提交。

- [ ] **Step 5: 记录技术门结论**

只报告工作流 URL、精确提交、两个 Python job 结论和原生覆盖项。不得声称 Codex App、真实 `CODEX_HOME` 或实体 Mac 已验收。

---

### Task 7: 冻结 RC3 发布准备并停在发布批准前

**Files:**
- Local release artifacts only; do not commit generated assets

**Interfaces:**
- Consumes: Task 6 中两个 macOS job 均为 success 的精确 HEAD。
- Produces: 与 CI 通过提交一致的三个本地 RC3 资产、SHA-256 和发布检查表；等待独立 RC3 发布批准。

- [ ] **Step 1: 重新绑定 CI 通过的精确提交**

```bash
set -euo pipefail
VIBE_RC3_HEAD="$(git rev-parse HEAD^{commit})"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

Expected: HEAD 与 Task 6 的 `headSha` 完全一致，工作树干净。

- [ ] **Step 2: 构建并验证最终本地资产**

```bash
set -euo pipefail
VIBE_RC3_ASSETS="$(mktemp -d /tmp/vibe-leader-3.1.0-rc3.XXXXXX)"
python3 -B scripts/release_archive.py build \
  --repo . --commit "$VIBE_RC3_HEAD" --output-dir "$VIBE_RC3_ASSETS"
python3 -B "$VIBE_RC3_ASSETS/release_archive.py" verify \
  --archive "$VIBE_RC3_ASSETS/Vibe-Leader-3.1.0-GitHub.zip" \
  --checksums "$VIBE_RC3_ASSETS/SHA256SUMS.txt" \
  --expected-commit "$VIBE_RC3_HEAD"
shasum -a 256 \
  "$VIBE_RC3_ASSETS/Vibe-Leader-3.1.0-GitHub.zip" \
  "$VIBE_RC3_ASSETS/release_archive.py" \
  "$VIBE_RC3_ASSETS/SHA256SUMS.txt"
```

Expected: build 和 verify 退出 0，三个摘要均为 64 位小写十六进制。

- [ ] **Step 3: 报告 RC3 发布准备并停止**

报告精确 HEAD、三个资产 SHA-256、本地完整回归、两个 GitHub macOS job、仍未执行的 RC3 Release、Hugging Face 和实体 Mac 实装。请求独立的 `v3.1.0-rc.3` 发布批准；没有批准不得创建 tag、Release 或上传资产。

---

## Plan Completion Evidence

本计划只有在以下证据同时存在时才算完成到“等待实体 Mac 最终验收”状态：

1. Task 1–4 的提交均在 `codex/vibe-leader-3.1-macos`；
2. WSL 聚焦测试和完整回归通过，Darwin 原生测试只在 WSL 明确 skip；
3. 两次本地发行资产逐字节一致且 verify 通过；
4. 公开候选分支回读精确匹配本地 HEAD；
5. GitHub `macos-14` 的 Python 3.11 与 3.14 job 对同一 HEAD 均为 success；
6. 最终本地 RC3 三资产和 SHA-256 已冻结；
7. 没有真实 `CODEX_HOME` 写入，没有实体 Mac 占用，没有 RC3 Release 或 Hugging Face 更新；
8. 下一步清楚停在独立 RC3 发布批准。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
