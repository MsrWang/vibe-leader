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
