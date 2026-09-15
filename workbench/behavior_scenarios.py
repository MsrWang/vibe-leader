# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pure extraction and closed synthetic-path validation for behavior inputs.

This rejects known scoring structures, not every possible natural-language leak.
It performs no filesystem reads and grants no permission to the rendered paths.
"""

from pathlib import Path
import re


class ScenarioInputError(ValueError):
    """A scenario cannot safely be used as model input."""


_PRIVATE_PATH = re.compile(
    r"/home/|/Users/|/root(?:/|\b)|[a-z]:[\\/]Users[\\/]"
    r"|/mnt/[a-z]/users/|~[\\/]|\$HOME\b|\$\{HOME\}|%USERPROFILE%",
    re.IGNORECASE,
)
_SCORING = re.compile(
    r"##\s*(?:运行约束|可观察评分)|\|\s*PASS\s*\|\s*FAIL\s*\|"
    r"|任一适用项\s*FAIL|\brubric\b|\bexpected\s+answer\b",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(
    r"[A-Za-z]:[\\/][^\s`，。；、（）()\[\]{}<>\"']*"
    r"|/[^\s`，。；、（）()\[\]{}<>\"']*"
)
_TOKEN = re.compile(r"\{\{[^{}]*\}\}")


def synthetic_path_variables(eval_root: Path) -> dict[str, str]:
    if (
        not isinstance(eval_root, Path)
        or eval_root.parent != Path("/tmp")
        or not eval_root.name.startswith("vibe-project-lead-eval.")
        or eval_root.name == "vibe-project-lead-eval."
    ):
        raise ScenarioInputError("invalid synthetic evaluation root")
    return {
        "{{EVAL_CURRENT}}": str(eval_root / "synthetic/current"),
        "{{EVAL_TARGET}}": str(eval_root / "synthetic/target"),
        "{{EVAL_SECOND}}": str(eval_root / "synthetic/second"),
    }


def extract_user_prompt(text: str) -> str:
    if not isinstance(text, str) or _PRIVATE_PATH.search(text):
        raise ScenarioInputError("invalid scenario source")
    headings = list(re.finditer(r"^## 用户输入$", text, re.MULTILINE))
    if len(headings) != 1:
        raise ScenarioInputError("expected one user input section")
    section = text[headings[0].end():]
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading is not None:
        section = section[:next_heading.start()]
    match = re.fullmatch(r"\n\n```text\n(.*?)\n```\n*", section, re.DOTALL)
    if match is None or "```" in match[1]:
        raise ScenarioInputError("expected one closed text input block")
    return match[1]


def validate_rendered_prompt(prompt: str, eval_root: Path) -> str:
    allowed = set(synthetic_path_variables(eval_root).values())
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127
               for char in prompt)
        or "{{" in prompt or "}}" in prompt or "\\\\" in prompt
        or _SCORING.search(prompt)
        or _PRIVATE_PATH.search(prompt)
    ):
        raise ScenarioInputError("invalid rendered scenario input")
    paths = _ABSOLUTE_PATH.findall(prompt)
    if any(path not in allowed for path in paths) or len(paths) != len(set(paths)):
        raise ScenarioInputError("unbound or repeated scenario path")
    return prompt


def render_user_prompt(text: str, eval_root: Path) -> str:
    variables = synthetic_path_variables(eval_root)
    prompt = extract_user_prompt(text)
    tokens = _TOKEN.findall(prompt)
    if any(token not in variables for token in tokens) or len(tokens) != len(set(tokens)):
        raise ScenarioInputError("unknown or repeated scenario variable")
    prompt = _TOKEN.sub(lambda match: variables[match[0]], prompt)
    return validate_rendered_prompt(prompt, eval_root)
