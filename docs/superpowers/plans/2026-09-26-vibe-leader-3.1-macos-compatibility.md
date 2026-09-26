# Vibe Leader 3.1 macOS Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Vibe Leader 3.1 在 Apple Silicon Mac 的 Codex App 中安全安装、发现、显式调用并运行本地证据筛选，同时保持 Linux/WSL 现有行为和可恢复升级合同。

**Architecture:** 保留 `evidence_filter.py` 和 `install_skill.py` 的公开接口，把 Linux 与 Darwin 的文件打开、文件系统身份和重命名原语收拢到内部平台后端。新增独立发行工具从固定 Git 提交生成确定性 ZIP，并在解包前执行严格路径、清单、摘要和大小校验。WSL 只证明分支合同与现有回归；Darwin 内核、APFS 和 Codex App 发现由 Apple Silicon Mac 候选验收负责。

**Tech Stack:** CPython 3.11–3.14 标准库、`unittest`、`ctypes`、系统 Git、GitHub Releases、macOS 14+ Codex App。

## Global Constraints

- 代码基线固定为 Vibe Leader 3.0.3 提交 `d0e17544584fb6c68254c49b87f4275fa19a71f4`；实施分支为 `codex/vibe-leader-3.1-macos`。
- 最低兼容版本为稳定版 CPython 3.11，支持区间为 `3.11 <= Python < 3.15`；新安装推荐版本和 M6 参考验收版本固定为 CPython 3.14.7。
- 低于 3.11 返回 `PYTHON_UPDATE_REQUIRED`；非 CPython、预发布版、无法解析的版本和 3.15+ 返回 `RUNTIME_UNVERIFIED`；不增加第三方 Python 包、Homebrew 依赖或系统安装动作。
- 通用目标为 macOS 14+ 与 Apple Silicon M1+；2026 Mac mini（M6）是本轮真实参考验收机，Intel Mac 只能标为未验收，不得外推支持结论。
- 可安装 Skill 仍是精确 13 个文件；本计划不修改 `SKILL.md` 的角色、权限、主循环或显式调用合同。
- 不接入外部 Jev API、第三方模型路由或凭据；不把项目级模块编排加入 Vibe Leader 或 Jev。
- Darwin 能力缺失、结果矛盾、身份漂移或清理不完整时失败关闭；不得退回普通路径打开、覆盖式移动、先删除后移动或普通复制。
- `preflight` 必须只读，不创建目录、不写能力探针、不修改 `CODEX_HOME`，也不搜索或猜测安装根。
- 本地实现阶段不得 push、创建 GitHub Release、更新 Hugging Face 或写入真实 Mac 的 Codex 配置目录。
- 公开发布目标固定为 `MsrWang/vibe-leader`；Hugging Face 目标固定为 `MsrWang0112/vibe-leader`。开发工作树 remote 不能作为发布目标推断依据。
- 候选与正式版必须逐字节复用 `Vibe-Leader-3.1.0-GitHub.zip`、`release_archive.py` 和 `SHA256SUMS.txt`。

## File Map

| File | Responsibility |
| --- | --- |
| `skill/vibe-project-lead-zh/scripts/evidence_filter.py` | 选择 Linux 或 Darwin 安全读取后端，保持筛选、隐私和结果合同不变 |
| `tests/test_evidence_filter.py` | 模拟 Darwin 能力并验证无 `/proc`、无 `O_PATH` 路径，以及 Linux 回归 |
| `scripts/install_skill.py` | Darwin `statfs`、`renameatx_np`、能力探针和只读 `preflight` |
| `tests/test_install_skill.py` | Darwin 安装器合同、错误映射、只读预检和既有升级/恢复回归 |
| `scripts/release_archive.py` | 从固定提交构建、验证和安全解包确定性发行 ZIP |
| `tests/test_release_archive.py` | 确定性、清单、摘要、路径逃逸、碰撞、特殊条目和排他解包测试 |
| `README.md` | 3.1 用户入口、已验证环境和下载路径 |
| `CHANGELOG.md` | 3.1 公开变化摘要 |
| `docs/getting-started.md` | macOS 预检、安装、升级、核验和恢复命令 |
| `docs/limitations.md` | 平台、文件系统、证据与未验收范围 |
| `docs/release-3.1.0.md` | 3.1 功能、验证组合和保留限制 |
| `docs/macos-acceptance-3.1.md` | Mac 就绪、候选验收、收据字段和用户观察步骤 |
| `demo/README.md`, `demo/index.html` | 静态示例版本和平台范围说明，不运行筛选器或安装器 |
| `tests/test_skill_contract.py` | 3.1 文档、13 文件清单和公开边界合同 |

新增辅助运行模块不在本计划内。如果任务实施证明现有两个脚本无法维持清晰边界，停止实施并先修订设计与本计划。

---

### Task 1: Darwin 安全证据读取后端

**Files:**
- Modify: `skill/vibe-project-lead-zh/scripts/evidence_filter.py:91-330,414-586`
- Modify: `tests/test_evidence_filter.py:43-392`

**Interfaces:**
- Consumes: `screen_project_files(query: str, files: list[str], *, enabled: bool = True) -> dict[str, object]`、现有 `_file_identity()`、相对路径和隐私检查合同。
- Produces: `_secure_read_backend(platform_name: str | None = None) -> str | None`，只返回 `LINUX_PROC_FD`、`DARWIN_OPENAT` 或 `None`；`_open_root(project_root: str) -> tuple[int, str, tuple[int, int, int, int, int, int], str]`；`_open_relative_file(root_fd: int, relative_bytes: bytes, backend: str) -> tuple[int, int, bytes]`。

- [ ] **Step 1: 写出 Darwin 后端选择失败测试**

在 `EvidenceFilterTests` 增加以下测试。测试必须注入平台和能力，不能把 WSL 模拟结果写成原生 macOS 成功：

```python
def test_darwin_backend_requires_nofollow_any_and_dir_fd(self):
    with mock.patch.object(EVIDENCE_FILTER.sys, "platform", "darwin"):
        with mock.patch.object(EVIDENCE_FILTER.os, "O_NOFOLLOW_ANY", 0x20000000, create=True):
            with mock.patch.object(EVIDENCE_FILTER.os, "O_DIRECTORY", 0x100000, create=True):
                with mock.patch.object(
                    EVIDENCE_FILTER.os,
                    "supports_dir_fd",
                    {EVIDENCE_FILTER.os.open},
                ):
                    self.assertEqual(
                        EVIDENCE_FILTER._secure_read_backend(),
                        "DARWIN_OPENAT",
                    )

    with mock.patch.object(EVIDENCE_FILTER.sys, "platform", "darwin"):
        with mock.patch.object(EVIDENCE_FILTER.os, "O_NOFOLLOW_ANY", 0, create=True):
            self.assertIsNone(EVIDENCE_FILTER._secure_read_backend())
```

再增加三项合同断言：Darwin 选择不读取 `/proc/self/fd`，根目录和叶子打开包含 `O_NOFOLLOW_ANY`，中间目录逐段使用 `dir_fd` 与 `O_NOFOLLOW`。

- [ ] **Step 2: 运行新测试并确认按预期失败**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_evidence_filter.EvidenceFilterTests.test_darwin_backend_requires_nofollow_any_and_dir_fd
```

Expected: FAIL，原因是 `_secure_read_backend` 尚不存在。

- [ ] **Step 3: 增加平台选择和 Darwin 根目录打开实现**

在 `_file_identity()` 后加入精确平台选择；Linux 判定保留当前全部能力条件，Darwin 不依赖 `O_PATH`、`os.readlink` 或 `/proc`：

```python
LINUX_SECURE_READ = "LINUX_PROC_FD"
DARWIN_SECURE_READ = "DARWIN_OPENAT"


def _secure_read_backend(platform_name: str | None = None) -> str | None:
    selected = sys.platform if platform_name is None else platform_name
    common = (
        os.name == "posix"
        and isinstance(getattr(os, "O_NOFOLLOW", None), int)
        and isinstance(getattr(os, "O_DIRECTORY", None), int)
        and os.open in os.supports_dir_fd
    )
    if not common:
        return None
    if selected == "darwin":
        nofollow_any = getattr(os, "O_NOFOLLOW_ANY", None)
        return DARWIN_SECURE_READ if isinstance(nofollow_any, int) and nofollow_any else None
    if selected.startswith("linux"):
        if (
            isinstance(getattr(os, "O_PATH", None), int)
            and os.readlink in os.supports_dir_fd
            and os.path.isdir("/proc/self/fd")
        ):
            return LINUX_SECURE_READ
    return None
```

把现有 `_open_root()` 内容移入 `_open_root_linux()`。新增 `_open_root_darwin()`：对 `os.path.abspath(project_root)` 前后执行 `lstat`，使用 `O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY | O_CLOEXEC` 打开，并要求前后路径身份与 `fstat` 完全一致。公开的内部包装 `_open_root()` 只调用一次 `_secure_read_backend()`，按结果分发并把已选 `backend` 与根描述符、规范根标识和身份一起返回；能力为 `None` 时继续抛出 `SECURE_READ_UNAVAILABLE`。不要调用 `realpath()` 来证明 Darwin 已打开对象。

- [ ] **Step 4: 把逐段打开和来源复核绑定到后端**

让 `_open_relative_file()` 接收 `backend`。Linux 叶子继续先用 `O_PATH | O_NOFOLLOW` 固定身份；Darwin 叶子直接用 `O_RDONLY | O_NOFOLLOW_ANY | O_NONBLOCK` 打开。两个后端都逐段打开中间目录并比较文件描述符身份。

把 `_SourceSnapshot.resolved_path` 改为 `resolved_path: str | None`。`_resolved_file_path()` 在 Linux 返回 `/proc/self/fd` 证明，在 Darwin 返回 `None`；Darwin 的边界证明来自根描述符、逐段 `dir_fd`、禁止跟随链接和重复打开后的身份与摘要比较。`_read_sources()`、`_verify_snapshots()`、`_readback()` 全部传递同一个 `backend`，只在 `resolved_path` 非空时比较 Linux 路径证明。

- [ ] **Step 5: 增加 Darwin 失败关闭和 Linux 回归测试**

新增并运行以下测试名：

```text
test_darwin_root_open_rejects_identity_change
test_darwin_relative_open_uses_anchored_descriptors
test_darwin_missing_nofollow_any_returns_unknown
test_linux_backend_keeps_proc_fd_identity_proof
```

Run:

```bash
python3 -B -m unittest -v tests.test_evidence_filter
```

Expected: PASS；现有输出字段、隐私阻断、24 条上限和 Linux 安全读取测试均保持通过。

- [ ] **Step 6: 提交证据读取后端**

```bash
git add skill/vibe-project-lead-zh/scripts/evidence_filter.py tests/test_evidence_filter.py
git commit -m "feat: add Darwin secure evidence reader"
```

---

### Task 2: Darwin 文件系统身份

**Files:**
- Modify: `scripts/install_skill.py:183-218,819-887`
- Modify: `tests/test_install_skill.py:285-935`

**Interfaces:**
- Consumes: 现有 `filesystem_identity(path: Path) -> dict[str, Any]` 调用点和 `TARGET_FILESYSTEM_KEYS` 精确四字段合同。
- Produces: `_linux_filesystem_identity(path: Path) -> dict[str, Any]`、`_darwin_statfs(path: Path) -> dict[str, int | str]`、`_darwin_filesystem_identity(path: Path) -> dict[str, Any]`；公开 `filesystem_identity()` 仍返回 `device`、`mount_target`、`filesystem_type`、`mount_options_sha256`。

- [ ] **Step 1: 写 Darwin 身份分发、规范化和漂移测试**

在 `tests/test_install_skill.py` 增加 `DarwinFilesystemIdentityTests`，使用 `_darwin_statfs` 注入值，不在 WSL 伪造真实 libc 调用：

```python
class DarwinFilesystemIdentityTests(unittest.TestCase):
    def test_darwin_identity_preserves_existing_schema(self):
        observed = {
            "device": 17,
            "mount_target": "/Users",
            "filesystem_type": "apfs",
            "mount_flags": 0x00000001,
        }
        with mock.patch.object(INSTALLER.sys, "platform", "darwin"):
            with mock.patch.object(INSTALLER, "_darwin_statfs", return_value=observed):
                with mock.patch.object(INSTALLER.os, "lstat") as lstat_call:
                    lstat_call.return_value.st_dev = 17
                    identity = INSTALLER.filesystem_identity(Path("/Users/test/.codex/skills"))

        self.assertEqual(set(identity), INSTALLER.TARGET_FILESYSTEM_KEYS)
        self.assertEqual(identity["device"], 17)
        self.assertEqual(identity["mount_target"], "/Users")
        self.assertEqual(identity["filesystem_type"], "apfs")
        self.assertEqual(len(identity["mount_options_sha256"]), 64)
```

增加 `device` 不一致、空挂载点、非法 UTF-8、空文件系统类型、`statfs` 非零返回和未知平台测试；全部必须产生 `filesystem_identity_unavailable`，退出级别 4。

- [ ] **Step 2: 运行新测试并确认失败**

Run:

```bash
python3 -B -m unittest -v tests.test_install_skill.DarwinFilesystemIdentityTests
```

Expected: FAIL，原因是 `_darwin_statfs` 和 Darwin 分发尚不存在。

- [ ] **Step 3: 拆出 Linux 实现并加入 Darwin `statfs` 结构**

把现有 `/proc/self/mountinfo` 逻辑原样移到 `_linux_filesystem_identity()`。加入 macOS `struct statfs` 的 `ctypes.Structure`，只暴露所需字段，并通过 `ctypes.CDLL(None, use_errno=True).statfs` 调用：

```python
class _DarwinFsid(ctypes.Structure):
    _fields_ = [("val", ctypes.c_int32 * 2)]


class _DarwinStatfs(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", _DarwinFsid),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_reserved", ctypes.c_uint32 * 8),
    ]
```

`_darwin_statfs()` 必须检查返回码、NUL 结尾字符串、严格 UTF-8、绝对挂载点和非空文件系统类型，并以 `lstat(mount_target).st_dev` 形成返回的 `device`；不得把 `f_fsid` 猜成 `st_dev`。`_darwin_filesystem_identity()` 对目标路径执行前后两次 `lstat`，要求完整目录身份稳定且 `st_dev` 与返回的 mount device 一致，再把规范化的 `darwin:<八位十六进制 flags>` 做 SHA-256。

- [ ] **Step 4: 加入平台分发并运行聚焦回归**

```python
def filesystem_identity(path: Path) -> dict[str, Any]:
    if sys.platform == "darwin":
        return _darwin_filesystem_identity(path)
    if sys.platform.startswith("linux"):
        return _linux_filesystem_identity(path)
    raise InstallError("filesystem_identity_unavailable", 4)
```

Run:

```bash
python3 -B -m unittest -v \
  tests.test_install_skill.DarwinFilesystemIdentityTests \
  tests.test_install_skill.InstallSkillTests.test_install_manifest_matches_every_file \
  tests.test_install_skill.UpgradePreflightTests.test_prepare_upgrade_binds_filesystem_and_mode_capability
```

Expected: PASS；Linux manifest 和升级请求的四字段身份形状不变。

- [ ] **Step 5: 提交文件系统身份后端**

```bash
git add scripts/install_skill.py tests/test_install_skill.py
git commit -m "feat: add Darwin filesystem identity"
```

---

### Task 3: Darwin 排他重命名、交换和能力探针

**Files:**
- Modify: `scripts/install_skill.py:51-67,2474-2733`
- Modify: `tests/test_install_skill.py:2136-2557,3421-5626`

**Interfaces:**
- Consumes: `rename_noreplace(source: Path, destination: Path) -> None`、`renameat2_direct(source: Path, destination: Path, flags: int) -> None`、`probe_switch_capability(root: Path) -> tuple[str, dict[str, Any]]` 和既有三态能力合同。
- Produces: `_darwin_renameatx_np_direct(source: Path, destination: Path, flags: int) -> None`；Linux 与 Windows 分支保持现有错误和收据语义。

- [ ] **Step 1: 写 Darwin 调用和 errno 映射失败测试**

新增 `DarwinRenameBackendTests`。使用可调用的 fake libc 函数捕获参数，禁止调用真实 WSL 系统调用：

```python
class DarwinRenameBackendTests(unittest.TestCase):
    def test_darwin_noreplace_maps_to_rename_excl_once(self):
        calls = []

        def renameatx_np(from_fd, source, to_fd, destination, flags):
            calls.append((from_fd, source, to_fd, destination, flags))
            return 0

        function = mock.Mock(side_effect=renameatx_np)
        library = mock.Mock(renameatx_np=function)
        with mock.patch.object(INSTALLER.ctypes, "CDLL", return_value=library):
            INSTALLER._darwin_renameatx_np_direct(
                Path("/tmp/source"),
                Path("/tmp/target"),
                INSTALLER.RENAME_NOREPLACE,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], INSTALLER.DARWIN_RENAME_EXCL)
```

增加以下错误测试：`EEXIST` 映射 `target_exists`；`ENOSYS`、`EINVAL`、`ENOTSUP` 和 `EOPNOTSUPP` 对交换映射 `exchange_unsupported`；其他 errno 映射 `exchange_probe_outcome_unknown` 或 `noreplace_probe_outcome_unknown`，并带 `status="unknown"`。每种情况只允许一次系统调用。

- [ ] **Step 2: 运行新测试并确认失败**

Run:

```bash
python3 -B -m unittest -v tests.test_install_skill.DarwinRenameBackendTests
```

Expected: FAIL，原因是 Darwin 常量和 `_darwin_renameatx_np_direct` 尚不存在。

- [ ] **Step 3: 实现 Darwin 原语并接入现有公共函数**

加入与 Darwin SDK 一致、和 Linux 标志分离的常量：

```python
DARWIN_RENAME_SWAP = 0x00000002
DARWIN_RENAME_EXCL = 0x00000004
```

`_darwin_renameatx_np_direct()` 必须：

1. 只接受现有逻辑标志 `RENAME_NOREPLACE` 或 `RENAME_EXCHANGE`；
2. 使用 `AT_FDCWD`、`os.fsencode()` 和 `ctypes.CDLL(None, use_errno=True).renameatx_np`；
3. 调用前设置 `argtypes` 和 `restype`；
4. 成功只接受返回 0；
5. 按 Step 1 的错误表失败关闭，不调用 `os.rename()`、复制或 Windows fallback。

在 `rename_noreplace()` 和 `renameat2_direct()` 的第一处分发 `sys.platform == "darwin"`。保留函数名和现有调用点，避免破坏升级、恢复及现有测试 patch seam。

- [ ] **Step 4: 验证能力探针和清理证据**

用现有 `probe_switch_capability()` 驱动 Darwin 分发，增加以下测试：

```text
test_darwin_exchange_probe_swaps_and_restores_once
test_darwin_exchange_unsupported_can_prove_noreplace_only
test_darwin_probe_contradiction_returns_unknown_without_retry
test_darwin_probe_cleanup_failure_preserves_probe_root
```

能力结果只能是 `EXCHANGE_SUPPORTED`、`NOREPLACE_ONLY` 或 `UNKNOWN`。成功必须同时具有 `postconditions_verified=True` 与 `restored=True`；未知结果不得自动重试或逆操作。

- [ ] **Step 5: 运行安装、升级和恢复聚焦回归**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_install_skill.DarwinRenameBackendTests \
  tests.test_install_skill.UpgradePreflightTests \
  tests.test_install_skill.NoReplaceUpgradeJournalTests \
  tests.test_install_skill.ExchangeUpgradeInspectionTests \
  tests.test_install_skill.RouteARestoreTests \
  tests.test_staging_recovery
```

Expected: PASS；既有 Linux/WSL、Windows Route A、升级 journal 和恢复收据合同均未改变。

- [ ] **Step 6: 提交 Darwin 切换后端**

```bash
git add scripts/install_skill.py tests/test_install_skill.py
git commit -m "feat: add Darwin atomic rename backend"
```

---

### Task 4: 只读 macOS 预检入口

**Files:**
- Modify: `scripts/install_skill.py:590-760,2735-2763,7091-7400`
- Modify: `tests/test_install_skill.py:178-208,285-452,2136-2557`

**Interfaces:**
- Consumes: `scan_tree()`、`validate_runtime_layout()`、`filesystem_identity()`、`verify_internal()`、`directory_identity()`。
- Produces: `build_preflight(source: Path, skills_root: Path, selection_source: str) -> dict[str, Any]`；CLI `preflight --source PATH --skills-root PATH --selection-source {CODEX_HOME,EXPLICIT_SKILLS_ROOT}`。

- [ ] **Step 1: 写预检 schema、安装模式和只读性失败测试**

新增 `MacPreflightTests`，固定精确顶层字段：

```python
PREFLIGHT_KEYS = {
    "preflight_schema_version",
    "status",
    "read_only",
    "platform",
    "source",
    "skills_root",
    "install_mode",
    "pending_write_checks",
    "reasons",
}


def test_ready_fresh_install_preflight_is_byte_level_read_only(self):
    before = self.snapshot_tree(self.codex_home)
    with mock.patch.object(
        INSTALLER,
        "_platform_facts",
        return_value={
            "system": "darwin",
            "machine": "arm64",
            "python_version": "3.11.9",
        },
    ):
        report = INSTALLER.build_preflight(
            self.source,
            self.skills_root,
            "CODEX_HOME",
        )

    self.assertEqual(set(report), PREFLIGHT_KEYS)
    self.assertEqual(report["status"], "READY")
    self.assertEqual(report["install_mode"], "FRESH_INSTALL")
    self.assertTrue(report["read_only"])
    self.assertEqual(self.snapshot_tree(self.codex_home), before)
```

增加 `CONTROLLED_UPGRADE`、残缺 target/state、非法 13 文件清单、非 arm64、Python 低于 3.11、未知平台、符号链接 root、文件系统身份不可用和 `CODEX_HOME` 选择不匹配测试。报告不得包含用户名、源码绝对路径或 skills-root 绝对路径。

- [ ] **Step 2: 运行新测试并确认失败**

Run:

```bash
python3 -B -m unittest -v tests.test_install_skill.MacPreflightTests
```

Expected: FAIL，原因是 `build_preflight` 尚不存在。

- [ ] **Step 3: 实现精确预检报告**

加入：

```python
PREFLIGHT_SCHEMA_VERSION = 1
PREFLIGHT_SELECTION_SOURCES = frozenset({"CODEX_HOME", "EXPLICIT_SKILLS_ROOT"})


def _platform_facts() -> dict[str, str]:
    return {
        "system": sys.platform,
        "machine": platform.machine().lower(),
        "python_version": platform.python_version(),
    }
```

`build_preflight()` 按固定顺序执行：

1. 验证 `selection_source`；`CODEX_HOME` 必须与环境变量对应的现有规范目录下 `skills` 完全一致，`EXPLICIT_SKILLS_ROOT` 只使用明确参数；
2. 严格读取并核对源 Skill 的精确 13 文件布局，返回文件数和 `canonical_tree_digest()`，不返回源路径；
3. 读取平台、架构和 Python 版本，先按 Task 4 的 `darwin`、`arm64`、Python 3.11+ 基线判断；Task 5A 再把运行时合同收束为稳定版 CPython 3.11–3.14；
4. 读取 skills-root 稳定目录身份和文件系统身份，只返回选择来源、稳定身份和四字段文件系统身份；
5. target 与 state 均不存在时返回 `FRESH_INSTALL`；两者均存在且旧 manifest 核验无漂移时返回 `CONTROLLED_UPGRADE`；其他组合返回 `BLOCKED`；
6. 不调用 `probe_mode_capability()` 或 `probe_switch_capability()`，只列出 `MODE_CAPABILITY_PROBE` 与 `SWITCH_CAPABILITY_PROBE` 为待写入阶段检查；
7. 任一事实不足时返回 `NOT_READY` 和稳定、无路径的 reason code。

- [ ] **Step 4: 接入第九个公开 CLI 命令**

```python
preflight_parser = commands.add_parser("preflight")
preflight_parser.add_argument("--source", required=True, type=Path)
preflight_parser.add_argument("--skills-root", required=True, type=Path)
preflight_parser.add_argument(
    "--selection-source",
    required=True,
    choices=tuple(sorted(PREFLIGHT_SELECTION_SOURCES)),
)
```

`main()` 使用 `json.dumps(report, ensure_ascii=False, sort_keys=True)` 输出一行 JSON；`READY` 返回 0，`NOT_READY` 返回 4。更新 `tests/test_staging_recovery.py` 的公开命令数量断言为九个，并精确列出 `preflight`。

- [ ] **Step 5: 运行预检和安装器回归**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_install_skill.MacPreflightTests \
  tests.test_install_skill.InstallSkillTests \
  tests.test_staging_recovery.PublicInstallerBoundaryTests
```

Expected: PASS；预检前后目录快照逐字节相同，既有八个命令行为不变并新增第九个只读命令。

- [ ] **Step 6: 提交只读预检**

```bash
git add scripts/install_skill.py tests/test_install_skill.py tests/test_staging_recovery.py
git commit -m "feat: add read-only macOS preflight"
```

---

### Task 5: 确定性发行包、安全验证和排他解包

**Files:**
- Create: `scripts/release_archive.py`
- Create: `tests/test_release_archive.py`
- Modify: `scripts/install_skill.py:3824-3890`
- Modify: `tests/test_install_skill.py:2136-2370`

**Interfaces:**
- Consumes: 一个干净 Git 仓库、调用者指定的 commit-ish 和新建输出目录；安装器现有 `read_source_head(source: Path) -> str`。
- Produces: `build_archive(repo: Path, commit: str, output_dir: Path) -> dict[str, Any]`、`verify_archive(archive: Path, checksums: Path, *, expected_commit: str | None) -> dict[str, Any]`、`extract_archive(archive: Path, checksums: Path, destination: Path, *, expected_commit: str | None) -> dict[str, Any]`；CLI 子命令 `build`、`verify`、`extract`；无 `.git` 的已验证解包树可通过 `RELEASE-MANIFEST.json` 向现有 `read_source_head()` 提供候选提交。

- [ ] **Step 1: 建立发行工具的合成 Git fixture 和确定性失败测试**

`tests/test_release_archive.py` 使用临时 Git 仓库并显式设置本地作者信息。测试仓库包含 `LICENSE`、`README.md`、`scripts/release_archive.py` 和唯一 `skill/vibe-project-lead-zh/SKILL.md`。首个测试执行两次构建并比较所有资产字节：

```python
def test_same_commit_builds_byte_identical_assets(self):
    first = self.root / "first"
    second = self.root / "second"
    first.mkdir()
    second.mkdir()

    RELEASE.build_archive(self.repo, self.commit, first)
    RELEASE.build_archive(self.repo, self.commit, second)

    for name in (
        "Vibe-Leader-3.1.0-GitHub.zip",
        "release_archive.py",
        "SHA256SUMS.txt",
    ):
        self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
```

增加 dirty 工作树、符号链接、gitlink、输出碰撞和非 commit 对象测试。所有失败都不得覆盖已有输出。

- [ ] **Step 2: 运行构建测试并确认失败**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_release_archive.ReleaseArchiveBuildTests.test_same_commit_builds_byte_identical_assets
```

Expected: FAIL，原因是 `scripts/release_archive.py` 尚不存在。

- [ ] **Step 3: 实现固定提交读取和 ZIP 字节合同**

使用固定常量：

```python
ARCHIVE_NAME = "Vibe-Leader-3.1.0-GitHub.zip"
SCRIPT_ASSET_NAME = "release_archive.py"
CHECKSUM_NAME = "SHA256SUMS.txt"
TOP_LEVEL = "Vibe-Leader-3.1.0"
RELEASE_MANIFEST_NAME = "RELEASE-MANIFEST.json"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILE_COUNT = 500
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
```

`build_archive()` 必须使用参数数组调用以下 Git 命令，不经 shell：

```text
git -C <repo> status --porcelain=v1 --untracked-files=all
git -C <repo> rev-parse --verify <commit>^{commit}
git -C <repo> ls-tree -rz --full-tree <resolved-commit>
git -C <repo> cat-file blob <object-id>
```

只接受 mode `100644` 和 `100755` 的 blob。生成 `RELEASE-MANIFEST.json` 后，对所有条目按 UTF-8 路径字节排序。每个 `ZipInfo` 显式设置 `date_time=FIXED_TIME`、`compress_type=ZIP_STORED`、`create_system=3`、固定 `create_version`、`extract_version`、空 `extra`、空 `comment` 和基于 Git mode 的 `external_attr`。使用排他创建写出三个资产，`SHA256SUMS.txt` 只包含 ZIP 和独立脚本两行摘要。

- [ ] **Step 4: 写安全验证和解包攻击测试**

构造独立恶意 ZIP，逐项断言拒绝：

```text
/absolute
../escape
backslash\entry
NUL 或控制字符
重复条目
Unicode NFC/NFD 等价碰撞
大小写折叠碰撞
符号链接 mode
设备或其他特殊 mode
ZIP_DEFLATED 或加密 flag
第二个顶层目录
零个或两个 SKILL.md
单文件、文件数、总大小或归档大小超限
manifest 缺失、额外文件、摘要错误或 expected commit 不符
已存在的解包目标
```

成功解包测试必须比较解包树与 `RELEASE-MANIFEST.json` 的每一个路径、mode、size 和 SHA-256。

- [ ] **Step 5: 实现先验证后排他写入的 `verify` 和 `extract`**

路径键统一为：

```python
def collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()
```

`verify_archive()` 先用系统读取计算 ZIP 和当前独立脚本 `Path(__file__)` 的摘要，再严格解析 `SHA256SUMS.txt`，两项都必须与摘要文件逐字节匹配；检查每个 `ZipInfo` 后才读取内容。这样 Mac 上实际执行的下载脚本本身也受摘要约束。`extract_archive()` 必须先取得完整验证结果，再以 `destination.mkdir(mode=0o700)` 排他创建新根，逐层创建目录，叶子使用 `os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW` 打开。禁止使用 `ZipFile.extract()` 或 `extractall()`。失败时保留本次新建目录和已写条目作为可检查现场，不自动换目标或重试。

- [ ] **Step 6: 接入 CLI 并运行完整发行工具测试**

CLI 形状固定为：

```text
release_archive.py build --repo PATH --commit COMMIT --output-dir PATH
release_archive.py verify --archive PATH --checksums PATH --expected-commit COMMIT
release_archive.py extract --archive PATH --checksums PATH --destination PATH --expected-commit COMMIT
```

每个命令只输出一行 JSON，不输出源码路径、文件内容或凭据。

Run:

```bash
python3 -B -m unittest -v tests.test_release_archive
```

Expected: PASS；相同提交两次构建的三个资产逐字节相同，全部恶意 ZIP 被拒绝。

- [ ] **Step 7: 让受控升级识别已验证 ZIP 的来源提交**

现有 `read_source_head()` 先保留 Git 读取路径。当源码树没有 `.git` 时，只检查固定位置 `source.parents[1] / "RELEASE-MANIFEST.json"`，不得向其他祖先搜索。新增 `_read_release_source_head(source: Path) -> str`，使用安装器现有的有界普通文件读取，严格验证：

```text
format_version == 1
source_commit 是有效 Git object id
顶层目录名等于 Vibe-Leader-3.1.0
清单只含唯一 skill/vibe-project-lead-zh/SKILL.md
skill/vibe-project-lead-zh/ 前缀下每个普通文件的 mode、size、sha256 与 scan_tree(source) 一致
没有清单外 Skill 文件，也没有 Skill 内目录或类型漂移
```

增加集成测试 `test_verified_release_archive_can_prepare_upgrade_without_git_directory`：构建合成发行 ZIP、安全解包、删除合成仓库关联后，以解包得到的 13 文件 Skill 调用 `build_upgrade_request()`，断言 `source_head` 等于发行清单提交。篡改 manifest、Skill 文件或提交字段时必须在任何升级写入前返回 `source_head_unavailable` 或 `candidate_inventory_not_unique`。

Run:

```bash
python3 -B -m unittest -v \
  tests.test_release_archive \
  tests.test_install_skill.UpgradePreflightTests.test_verified_release_archive_can_prepare_upgrade_without_git_directory
```

Expected: PASS；Git checkout 路径继续读取 `.git/HEAD`，GitHub ZIP 路径只接受与源码树完全一致的发行清单。

- [ ] **Step 8: 提交发行工具和 ZIP 来源绑定**

```bash
git add scripts/release_archive.py tests/test_release_archive.py \
  scripts/install_skill.py tests/test_install_skill.py
git commit -m "feat: add deterministic release archive"
```

---

### Task 5A: 收束 Python 兼容下限、推荐版本与未验证运行时

**Files:**
- Modify: `scripts/install_skill.py:3998-4065`
- Modify: `tests/test_install_skill.py:2483-2770`

**Interfaces:**
- Consumes: Task 4 的 `_platform_facts()` 与 `build_preflight()` 九字段只读报告。
- Produces: `_python_runtime_reason(facts: dict[str, str]) -> str | None`；平台事实新增 `python_implementation` 与 `python_releaselevel`，支持稳定版 CPython 3.11–3.14。

- [ ] **Step 1: 写版本边界和实现类型失败测试**

将 `MacPreflightTests.preflight()` 的默认事实固定为：

```python
{
    "system": "darwin",
    "machine": "arm64",
    "python_implementation": "CPython",
    "python_version": "3.14.7",
    "python_releaselevel": "final",
}
```

新增精确断言：稳定版 CPython 3.11.0、3.11.9、3.12、3.13 与 3.14.7 返回 `READY`；3.10.14 返回 `PYTHON_UPDATE_REQUIRED`；PyPy、`3.14.7rc1`、`python_releaselevel=release candidate`、3.15.0、非法或缺失版本字段返回 `RUNTIME_UNVERIFIED`。报告继续不包含路径或用户名。

- [ ] **Step 2: 运行新边界测试并确认失败**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_install_skill.MacPreflightTests.test_supported_cpython_range_is_ready \
  tests.test_install_skill.MacPreflightTests.test_python_below_floor_requires_update \
  tests.test_install_skill.MacPreflightTests.test_unverified_runtime_is_not_ready
```

Expected: FAIL；当前实现把所有 3.11+ 数字版本都接受，并只返回 `PYTHON_VERSION_UNSUPPORTED`。

- [ ] **Step 3: 实现稳定版 CPython 版本分类**

加入：

```python
MIN_SUPPORTED_PYTHON = (3, 11, 0)
MAX_EXCLUSIVE_SUPPORTED_PYTHON = (3, 15, 0)


def _python_runtime_reason(facts: dict[str, str]) -> str | None:
    if (
        facts.get("python_implementation") != "CPython"
        or facts.get("python_releaselevel") != "final"
    ):
        return "RUNTIME_UNVERIFIED"
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", facts.get("python_version", ""))
    if match is None:
        return "RUNTIME_UNVERIFIED"
    version = tuple(map(int, match.groups()))
    if version < MIN_SUPPORTED_PYTHON:
        return "PYTHON_UPDATE_REQUIRED"
    if version >= MAX_EXCLUSIVE_SUPPORTED_PYTHON:
        return "RUNTIME_UNVERIFIED"
    return None
```

`_platform_facts()` 使用 `platform.python_implementation()`、`platform.python_version()` 和 `sys.version_info.releaselevel` 填充完整事实。`build_preflight()` 只追加 `_python_runtime_reason()` 返回的稳定 reason code；不自动安装、升级或调用包管理器。

- [ ] **Step 4: 运行预检聚焦回归**

Run:

```bash
python3 -B -m unittest -v tests.test_install_skill.MacPreflightTests
```

Expected: PASS；3.11 仍是兼容下限，3.14.7 是默认测试与推荐版本，3.15+ 保持未验证。

- [ ] **Step 5: 提交运行时合同修订**

```bash
git add scripts/install_skill.py tests/test_install_skill.py \
  docs/superpowers/specs/2026-09-26-vibe-leader-3.1-macos-compatibility-design.md \
  docs/superpowers/plans/2026-09-26-vibe-leader-3.1-macos-compatibility.md
git commit -m "fix: define macOS Python compatibility contract"
```

---

### Task 5B: 修复旧运行时启动与 macOS 版本门

**Files:**
- Modify: `scripts/install_skill.py`
- Modify: `tests/test_install_skill.py`
- Modify: `docs/superpowers/specs/2026-09-26-vibe-leader-3.1-macos-compatibility-design.md`
- Modify: `docs/superpowers/plans/2026-09-26-vibe-leader-3.1-macos-compatibility.md`

**Interfaces:**
- Consumes: Task 5A 的 Python 运行时分类和九字段只读预检报告。
- Produces: 缺少 `tomllib` 时仍可启动的 `preflight`；`macos_version` 平台事实；macOS 14+ 分类。

- [ ] **Step 1: 写启动期和 macOS 边界失败测试**

新增可执行级子进程测试：导入阻断器让 `tomllib` 不可用，模拟 Darwin/arm64、macOS 27 和稳定版 CPython 3.10.14，执行公开 `preflight` CLI，要求退出 4、只输出一行九字段 JSON、原因精确为 `PYTHON_UPDATE_REQUIRED`，并保持目录逐字节不变。使用 `ast.parse(..., feature_version=(3, 10))` 验证安装器源码保持 Python 3.10 语法可解析。

默认平台事实增加 `macos_version`。新增精确边界：13.x 返回 `MACOS_UPDATE_REQUIRED`；14.x 和 27.x 保持 `READY`；缺失或畸形版本返回 `MACOS_VERSION_UNVERIFIED`。TOML 消费者在模块不可用时返回既有失败关闭原因，不能抛出 `AttributeError`。

- [ ] **Step 2: 运行新测试并确认失败**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_install_skill.MacPreflightTests.test_preflight_cli_reports_python_update_when_tomllib_is_unavailable \
  tests.test_install_skill.MacPreflightTests.test_macos_below_floor_requires_update \
  tests.test_install_skill.MacPreflightTests.test_supported_macos_versions_are_ready \
  tests.test_install_skill.MacPreflightTests.test_unverified_macos_version_is_not_ready
```

Expected: FAIL；当前安装器在 `tomllib` 导入阶段退出，且不读取或分类 macOS 版本。

- [ ] **Step 3: 实现可缺失导入和系统版本分类**

用 `try/except ModuleNotFoundError` 导入 `tomllib`。`preflight` 不依赖该模块；TOML 消费者先检查模块是否存在，再返回既有 `toggle_config_invalid`。删除未被执行合同使用的 `RECOMMENDED_PYTHON` 常量，推荐版本继续只由设计和发布文档定义。

`_platform_facts()` 用 `platform.mac_ver()[0]` 写入 `macos_version`。仅对 Darwin 调用 macOS 分类：主版本低于 14 返回 `MACOS_UPDATE_REQUIRED`；缺失或无法解析返回 `MACOS_VERSION_UNVERIFIED`；14+ 不追加原因。

- [ ] **Step 4: 运行聚焦回归和静态检查**

Run:

```bash
python3 -B -m unittest -v tests.test_install_skill.MacPreflightTests
python3 -B -m unittest -v \
  tests.test_install_skill.ReceiptBoundRestoreAndToggleTests \
  tests.test_staging_recovery.PublicInstallerBoundaryTests
git diff --check
```

Expected: PASS；九字段、只读和脱敏合同保持不变，公开命令仍可启动。

- [ ] **Step 5: 提交审查修复**

```bash
git add scripts/install_skill.py tests/test_install_skill.py \
  docs/superpowers/specs/2026-09-26-vibe-leader-3.1-macos-compatibility-design.md \
  docs/superpowers/plans/2026-09-26-vibe-leader-3.1-macos-compatibility.md
git commit -m "fix: enforce macOS preflight runtime floors"
```

---

### Checkpoint A: 固定 Apple Silicon 参考配置门

Task 6 开始前不读取用户个人 Mac，也不要求用户先执行本机命令。候选文档和测试固定使用以下去个人化参考配置：

- 通用设计范围：macOS 14+、Apple Silicon M1+、`arm64`；
- 参考验收机：2026 Mac mini（M6）；
- 参考验收系统：macOS 27 的验收当日稳定补丁版本；
- 推荐与参考验收运行时：CPython 3.14.7；
- 兼容下限：稳定版 CPython 3.11；
- 真实 `CODEX_HOME`、Codex App 版本、目标文件系统和安装前状态在候选下载后的 Mac 验收中重新绑定。

继续条件：设计、预检和公开材料均区分“通用支持目标”“参考验收目标”“真实验收结果”。该检查点不生成 Mac 就绪结论；3.0.3 继续作为稳定版，直到候选完成真实 Mac 验收。

---

### Task 6: 3.1 文档合同、集成回归和本地候选冻结

**Files:**
- Create: `docs/release-3.1.0.md`
- Create: `docs/macos-acceptance-3.1.md`
- Modify: `README.md:1-100`
- Modify: `CHANGELOG.md:1-12`
- Modify: `docs/getting-started.md:1-120`
- Modify: `docs/limitations.md:1-90,120-170`
- Modify: `demo/README.md:1-40`
- Modify: `demo/index.html:1-75`
- Modify: `tests/test_skill_contract.py:449-535`

**Interfaces:**
- Consumes: Checkpoint A 的固定参考配置、Task 1–5A 的稳定命令和 13 文件 Skill 清单。
- Produces: 3.1 公开说明、Mac 验收步骤、全量本地回归证据，以及冻结提交对应的三个未发布候选资产。

- [ ] **Step 1: 先写 3.1 公开合同失败测试**

在 `tests/test_skill_contract.py` 增加：

```python
def test_3_1_public_materials_define_macos_candidate_boundary(self):
    release = (ROOT / "docs" / "release-3.1.0.md").read_text(encoding="utf-8")
    acceptance = (ROOT / "docs" / "macos-acceptance-3.1.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    limitations = (ROOT / "docs" / "limitations.md").read_text(encoding="utf-8")

    for required in (
        "Apple Silicon",
        "Python 3.11+",
        "CPython 3.14.7",
        "2026 Mac mini（M6）",
        "Vibe-Leader-3.1.0-GitHub.zip",
        "release_archive.py",
        "SHA256SUMS.txt",
        "不接入外部 Jev",
        "13 个文件",
    ):
        self.assertIn(required, release + acceptance + readme + limitations)

    self.assertIn("候选回滚", acceptance)
    self.assertIn("稳定版实装", acceptance)
    self.assertIn("Intel Mac 未验收", limitations)
```

增加断言，确保公开文字不会把 WSL 模拟测试称为 Mac 原生通过，也不会在用户观察前声称 Codex App 验收完成。

- [ ] **Step 2: 运行文档测试并确认失败**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_skill_contract.SkillContractTests.test_3_1_public_materials_define_macos_candidate_boundary
```

Expected: FAIL，原因是 3.1 发行说明和 Mac 验收指南尚不存在。

- [ ] **Step 3: 编写 3.1 文档并保持证据边界**

`docs/release-3.1.0.md` 必须写明：

- 3.1 只替换平台原语，不改变主管定位、授权合同或 Jev 的本地证据筛选范围；
- 可安装 Skill 仍为 13 个文件；
- 通用支持目标为 macOS 14+、Apple Silicon M1+ 和稳定版 CPython 3.11–3.14；
- 2026 Mac mini（M6）、macOS 27 稳定补丁版本和 CPython 3.14.7 只能写为“参考验收目标”，真实验收前不得写为“已验证”；
- WSL 测试、Mac 技术检查、Codex App 用户观察和平台发布是四种不同证据；
- Intel Mac、Windows 原生、网络盘、外接盘和外部 Jev 没有由本版证明。

`docs/macos-acceptance-3.1.md` 必须给出完整顺序：系统摘要核对、安全解包、`preflight`、隔离临时 `CODEX_HOME`、真实安装批准、新任务显式调用、合成本地证据筛选、用户观察、候选回滚、原状态确认、正式版重新安装。文档只给命令模板中可由本机计算的 shell 变量，不写用户名、Token 或固定绝对路径。

README、Changelog、入门、限制和 demo 只陈述已经有证据的范围。Checkpoint A 只能写为“固定参考配置”，不能写为“本机就绪”或“兼容验收通过”。

- [ ] **Step 4: 运行文档、Skill 清单和聚焦回归**

Run:

```bash
python3 -B -m unittest -v \
  tests.test_skill_contract \
  tests.test_evidence_filter \
  tests.test_install_skill \
  tests.test_staging_recovery \
  tests.test_release_archive \
  tests.test_safe_public_text
```

Expected: PASS；可安装 Skill 文件集合仍精确等于原 13 文件，公开材料不包含敏感路径或未经证明的兼容声明。

- [ ] **Step 5: 运行完整仓库回归**

先确认没有设置真实安装或 Windows interop 开关：

```bash
env | rg '^(CODEX_HOME|VIBE_INSTALL_|VIBE_RUN_WINDOWS_NOREPLACE_INTEROP|VIBE_REQUIRE_WINDOWS_NOREPLACE_INTEROP)=' || true
```

然后运行：

```bash
python3 -B -m unittest discover -s tests -v
```

Expected: PASS，零 failure、零 error；skip 单列报告且不得新增用于掩盖 macOS 合同的 skip。

- [ ] **Step 6: 做两遍审查并修复发现**

第一遍按已确认设计逐条核对目标、非目标、失败关闭、回滚和六个高阶发布门，并核对本计划展开后的七个外部动作门。第二遍只检查实现质量：平台分支集中、错误映射、资源关闭、无重试、无路径泄漏、测试负例和文档用词。任何代码或文档修复后重新运行 Step 4 和 Step 5。

- [ ] **Step 7: 提交最终候选源码**

```bash
git add README.md CHANGELOG.md docs/getting-started.md docs/limitations.md \
  docs/release-3.1.0.md docs/macos-acceptance-3.1.md \
  demo/README.md demo/index.html tests/test_skill_contract.py
git commit -m "docs: prepare Vibe Leader 3.1 macOS candidate"
```

- [ ] **Step 8: 从干净冻结提交构建并复验本地资产**

```bash
test -z "$(git status --porcelain=v1 --untracked-files=all)"
FROZEN_COMMIT="$(git rev-parse HEAD)"
rm -rf dist/v3.1.0-rc.1 dist/v3.1.0-rebuild
mkdir -p dist/v3.1.0-rc.1 dist/v3.1.0-rebuild
python3 -B scripts/release_archive.py build \
  --repo "$PWD" --commit "$FROZEN_COMMIT" --output-dir dist/v3.1.0-rc.1
python3 -B scripts/release_archive.py build \
  --repo "$PWD" --commit "$FROZEN_COMMIT" --output-dir dist/v3.1.0-rebuild
cmp dist/v3.1.0-rc.1/Vibe-Leader-3.1.0-GitHub.zip \
  dist/v3.1.0-rebuild/Vibe-Leader-3.1.0-GitHub.zip
cmp dist/v3.1.0-rc.1/release_archive.py \
  dist/v3.1.0-rebuild/release_archive.py
cmp dist/v3.1.0-rc.1/SHA256SUMS.txt \
  dist/v3.1.0-rebuild/SHA256SUMS.txt
python3 -B dist/v3.1.0-rc.1/release_archive.py verify \
  --archive dist/v3.1.0-rc.1/Vibe-Leader-3.1.0-GitHub.zip \
  --checksums dist/v3.1.0-rc.1/SHA256SUMS.txt \
  --expected-commit "$FROZEN_COMMIT"
```

Expected: 两次构建逐字节一致；`verify` 输出 `status=VERIFIED` 并绑定 `FROZEN_COMMIT`。这些资产仍是本地候选，不代表已经上传或通过 Mac 验收。

---

## External Rollout Gates

以下动作不属于本地自动实施。每一门都要重新绑定当时提交、资产、目标仓库和授权。

1. **GitHub 候选批准**：取得新的 E 级批准；显式对 `MsrWang/vibe-leader` 推送冻结提交或标签并创建 `v3.1.0-rc.1` prerelease；上传本地冻结的三个资产；回读标签提交与 GitHub 资产 digest。公开 `main` 和 latest 稳定版仍保持 3.0.3。
2. **Mac 候选技术检查**：用户在 Apple Silicon Mac 下载 Release 资产，用系统 SHA-256 和独立 `release_archive.py` 验证并解包；在临时目录运行聚焦检查。结果写成无用户名、无绝对路径、无凭据、无业务数据的收据。
3. **Mac Codex App 实装批准**：对真实 Skill 根取得独立 E 级批准；安装或受控升级后，由用户在新任务中确认唯一 locator、显示名、显式调用和一次合成证据筛选。
4. **候选恢复批准**：首次安装使用 `rollback`，旧版升级使用 `restore-version`；恢复后刷新 Codex App 并确认原版本或未安装状态。恢复成功不自动授权正式安装。
5. **GitHub 正式发布批准**：只有同一提交和同一三个资产可以晋升 `v3.1.0`；不得重新打包。正式发布后回读仓库、标签、latest 状态和三个资产 digest。
6. **Mac 稳定版实装批准**：从正式 Release 重新下载相同摘要资产并安装，完成 manifest、新任务显式调用和恢复材料复核后保留稳定版活动状态。
7. **Hugging Face 同步批准**：最后更新 `MsrWang0112/vibe-leader`，回读公开页面；GitHub 成功不能替代 Hugging Face 回读。

任一候选步骤要求修改代码、文档、包内容或安装步骤时，停止晋升，生成新提交和 `rc.2`。不得覆盖 `rc.1` 的标签、资产或摘要。

---

## Completion Evidence

完成 3.1.0 需要同时具备：

- Linux/WSL 完整回归和受影响聚焦测试通过；
- Darwin 合同测试与 Apple Silicon Mac 原生技术检查通过；
- 同一冻结提交两次构建得到相同 ZIP、独立脚本和摘要文件；
- GitHub 候选、Mac 下载、GitHub 正式版的三个资产摘要完全一致；
- Mac Codex App 用户完成真实发现、显式调用、合成筛选、候选恢复和稳定版复装观察；
- 安装 manifest、升级或回滚 journal、成功收据和恢复材料完整；
- 没有未裁决的 Critical/High、外部状态 `UNKNOWN` 或候选字节漂移；
- GitHub 正式发布和 Hugging Face 更新分别取得当时的新批准并完成回读。

实现完成、测试通过、上传成功、Mac 安装成功和用户验收是不同结论，不能互相替代。
