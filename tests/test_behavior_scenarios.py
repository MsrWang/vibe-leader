# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Synthetic inputs for the shared, process-free behavior prompt contract."""

import importlib
from pathlib import Path
import unittest


EVAL_ROOT = Path("/tmp/vibe-project-lead-eval.synthetic")


def document(prompt: str) -> str:
    return (
        "## 用户输入\n\n```text\n" + prompt + "\n```\n\n"
        "## 运行约束\n\n不把评分放入输入。\n\n"
        "## 可观察评分\n\n| 项目 | PASS | FAIL |\n"
    )


class BehaviorScenarioTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).resolve().parents[1] / "workbench/behavior_scenarios.py"
        self.assertTrue(path.is_file(), "shared scenario renderer missing")
        return importlib.import_module("workbench.behavior_scenarios")

    def test_renderer_excludes_scoring_and_binds_one_path(self):
        renderer = self.module().render_user_prompt
        self.assertEqual(
            renderer(document("当前项目是 {{EVAL_CURRENT}}。"), EVAL_ROOT),
            "当前项目是 /tmp/vibe-project-lead-eval.synthetic/synthetic/current。",
        )

    def test_each_known_variable_has_an_independent_literal_target(self):
        renderer = self.module().render_user_prompt
        for variable, want in (
            ("{{EVAL_CURRENT}}", "/tmp/vibe-project-lead-eval.synthetic/synthetic/current"),
            ("{{EVAL_TARGET}}", "/tmp/vibe-project-lead-eval.synthetic/synthetic/target"),
            ("{{EVAL_SECOND}}", "/tmp/vibe-project-lead-eval.synthetic/synthetic/second"),
        ):
            with self.subTest(variable=variable):
                self.assertEqual(renderer(document(variable), EVAL_ROOT), want)

    def test_nonempty_variable_free_synthetic_prompt_is_preserved(self):
        module = self.module()
        prompt = "请核对这个合成项目，信息不足就停止。\n第二行保持原样。"
        self.assertEqual(module.render_user_prompt(document(prompt), EVAL_ROOT), prompt)
        self.assertEqual(module.validate_rendered_prompt(prompt, EVAL_ROOT), prompt)

    def test_unknown_repeated_and_unclosed_variables_are_rejected(self):
        module = self.module()
        for prompt in (
            "{{UNKNOWN}}", "{{EVAL_OTHER}}", "{{EVAL_CURRENT}} {{EVAL_CURRENT}}",
            "{{EVAL_CURRENT}", "{EVAL_CURRENT}}", "{{}}", "{{ {{EVAL_CURRENT}} }}",
        ):
            with self.subTest(prompt=prompt), self.assertRaises(module.ScenarioInputError):
                module.render_user_prompt(document(prompt), EVAL_ROOT)

    def test_missing_repeated_or_unclosed_input_blocks_are_rejected(self):
        module = self.module()
        for text in (
            "no input", "## 用户输入\n\n```text\nhello",
            document("hello") + document("second"),
            "## 用户输入\n\n```python\nhello\n```\n",
            document("hello").replace("## 运行约束", "## 用户输入"),
            "## 用户输入\n\n```text\nhello\n```\n\n```text\nsecond\n```\n",
        ):
            with self.subTest(text=text), self.assertRaises(module.ScenarioInputError):
                module.render_user_prompt(text, EVAL_ROOT)

    def test_empty_or_control_character_input_is_rejected(self):
        module = self.module()
        for prompt in ("", "  \n\t", "hello\x00world", "hello\rworld", "hello\x1bworld"):
            with self.subTest(prompt=prompt), self.assertRaises(module.ScenarioInputError):
                module.render_user_prompt(document(prompt), EVAL_ROOT)

    def test_each_known_scoring_marker_is_rejected_in_model_input(self):
        module = self.module()
        for marker in (
            "## 运行约束", "## 可观察评分", "| PASS | FAIL |",
            "任一适用项 FAIL", "rubric", "expected answer", "Expected Answer",
        ):
            with self.subTest(marker=marker):
                with self.assertRaises(module.ScenarioInputError):
                    module.render_user_prompt(document("hello " + marker), EVAL_ROOT)
                with self.assertRaises(module.ScenarioInputError):
                    module.validate_rendered_prompt("hello " + marker, EVAL_ROOT)

    def test_general_private_paths_are_rejected_without_personal_sentinels(self):
        module = self.module()
        for path in (
            "/home/example/projects/demo", "/Users/example/demo", "/root/demo",
            r"C:\Users\Example\demo", "D:/Users/Example/demo",
            "/mnt/c/Users/Example/demo", "/mnt/d/users/example/demo",
            "~/demo", r"~\demo", "$HOME/demo", "${HOME}/demo", "%USERPROFILE%/demo",
        ):
            with self.subTest(path=path):
                with self.assertRaises(module.ScenarioInputError):
                    module.render_user_prompt(document("read " + path), EVAL_ROOT)
                with self.assertRaises(module.ScenarioInputError):
                    module.validate_rendered_prompt("read " + path, EVAL_ROOT)

    def test_scoring_section_cannot_smuggle_private_paths(self):
        module = self.module()
        with self.assertRaises(module.ScenarioInputError):
            module.render_user_prompt(document("hello") + "\n/home/example/private\n", EVAL_ROOT)

    def test_eval_root_requires_a_normalized_direct_synthetic_child_of_tmp(self):
        module = self.module()
        for root in (Path("/"), Path("/tmp"), Path("relative"), Path("/tmp/other"),
                     Path("/tmp/vibe-project-lead-eval.synthetic/child"),
                     Path("/tmp/vibe-project-lead-eval.synthetic/../other")):
            with self.subTest(root=root), self.assertRaises(module.ScenarioInputError):
                module.render_user_prompt(document("{{EVAL_CURRENT}}"), root)

    def test_rendered_input_rejects_unbound_or_duplicate_absolute_paths(self):
        module = self.module()
        for prompt in (
            "/etc/passwd", "/tmp/unbound/project", r"C:\work\demo",
            r"\\server\share\demo",
            "{{EVAL_CURRENT}}", "/tmp/vibe-project-lead-eval.other/synthetic/current",
            "/tmp/vibe-project-lead-eval.synthetic/synthetic/current/secret.txt",
            "/tmp/vibe-project-lead-eval.synthetic/synthetic/current "
            "/tmp/vibe-project-lead-eval.synthetic/synthetic/current",
        ):
            with self.subTest(prompt=prompt), self.assertRaises(module.ScenarioInputError):
                module.validate_rendered_prompt(prompt, EVAL_ROOT)

    def test_errors_do_not_echo_input(self):
        module = self.module()
        secret = "SYNTHETIC_NOT_A_REAL_SECRET"
        with self.assertRaises(module.ScenarioInputError) as caught:
            module.validate_rendered_prompt(secret + " /home/example/private", EVAL_ROOT)
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
