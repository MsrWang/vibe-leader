# SPDX-License-Identifier: MPL-2.0
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "macos-acceptance-3.1.md"
WORKFLOW = ROOT / ".github" / "workflows" / "macos-native.yml"


def guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", text, flags=re.DOTALL)


def continued_commands(text: str) -> str:
    return text.replace("\\\n", " ")


class MacAcceptanceGuideContractTests(unittest.TestCase):
    def test_python_bootstrap_rejects_missing_or_unusable_executable(self):
        bootstrap = bash_blocks(guide_text())[0]
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            for name, body in {
                "sw_vers": "exit 0\n",
                "uname": "printf 'arm64\\n'\n",
                "sysctl": "exit 0\n",
            }.items():
                command = tools / name
                command.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
                command.chmod(0o700)
            nonexecutable = tools / "nonexecutable-python"
            nonexecutable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            nonexecutable.chmod(0o600)
            broken_executable = tools / "broken-python"
            broken_executable.write_text("#!/no-such-interpreter\n", encoding="utf-8")
            broken_executable.chmod(0o700)

            for name, candidate in (
                ("python absent from PATH", None),
                ("invalid explicit path", tools / "absent-python"),
                ("nonexecutable explicit path", nonexecutable),
                ("broken executable", broken_executable),
            ):
                with self.subTest(name=name):
                    environment = os.environ.copy()
                    environment["PATH"] = str(tools)
                    if candidate is None:
                        environment.pop("VIBE_PYTHON", None)
                    else:
                        environment["VIBE_PYTHON"] = str(candidate)
                    result = subprocess.run(
                        ["/bin/bash"], input=bootstrap, text=True,
                        capture_output=True, env=environment, check=False,
                    )
                    self.assertEqual(result.returncode, 4)
                    self.assertEqual(result.stderr.strip(), "RUNTIME_UNVERIFIED")

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
