# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import unittest

from workbench import safe_public_text


class SafePublicTextTests(unittest.TestCase):
    def assert_unsafe(self, value):
        with self.assertRaises(safe_public_text.SafePublicTextError) as caught:
            safe_public_text.validate_safe_public_text(value)
        self.assertEqual(caught.exception.reason, "UNSAFE_PUBLIC_TEXT")
        self.assertEqual(str(caught.exception), "UNSAFE_PUBLIC_TEXT")

    def test_rejects_projection_equivalent_forms_for_every_detector(self):
        values_by_detector = {
            "url": (
                "ｈｔｔｐｓ：／／example.com/private",
                "ht\u200btps://example.com/private",
            ),
            "scp_remote": (
                "git＠example.com：private/repo.git",
                "git@exam\u200bple.com:private/repo.git",
            ),
            "credential": (
                "Ｂｅａｒｅｒ private-token",
                "Bea\u200brer private-token",
                "Ｂａｓｉｃ private-token",
                "Co\u200bokie: private-token",
                "－－－－－ＢＥＧＩＮ ＰＲＩＶＡＴＥ ＫＥＹ－－－－－",
            ),
            "assignment": (
                "ＦＥＡＴＵＲＥ＿ＦＬＡＧ＝private-value",
                "FEATURE_\u200bFLAG＝private-value",
            ),
            "raw_git": (
                "> - ｄｉｆｆ －－ｇｉｔ a/private b/private",
                "> - di\u200bff --git a/private b/private",
                "1. ＠＠ -1 +1 ＠＠",
                "    －－－ a/private",
                "    ＋＋＋ b/private",
                "    ？？ private.txt",
            ),
            "task_list": (
                "- ［ ］ inspect private value",
                "> - ［ｘ］ inspect private value",
                "1. [x\u200b] inspect private value",
            ),
            "inline_code": (
                "use ｀private value｀ here",
                "use `pri\u200bvate value` here",
            ),
            "forbidden_marker": (
                "Ａｕｔｈｏｒｉｚａｔｉｏｎopaque",
                "Auth\u200borizationopaque",
            ),
        }
        for detector, values in values_by_detector.items():
            for value in values:
                with self.subTest(detector=detector, value=value):
                    self.assert_unsafe(value)

    def test_rejects_prohibited_payloads_inside_markdown_containers(self):
        payloads = (
            "FEATURE_FLAG=true",
            "export NAME=value",
            "mixedCase_name = value",
            "diff --git a/private b/private",
            "@@ -1 +1 @@",
            "--- a/private",
            "+++ b/private",
            "?? private.txt",
            "[ ] inspect private value",
            "[x] inspect private value",
            "[X] inspect private value",
        )
        containers = ("{}", "    {}", "> {}", "- {}", "* {}", "+ {}", "1. {}", "1) {}", "> - {}", "  * > {}")
        for payload in payloads:
            for container in containers:
                value = container.format(payload)
                with self.subTest(value=value):
                    self.assert_unsafe(value)

    def test_rejects_code_urls_remotes_credentials_and_private_keys(self):
        values = (
            "use `FEATURE_FLAG=true` here",
            "```text\nordinary text\n```",
            "https://example.com/private",
            "HTTP://example.com/private",
            "ssh://host/private",
            "custom+scheme://host/private",
            "git@example.com:private/repo.git",
            "user@host:private/path",
            "Authorization: Bearer abc",
            "Authorization: Basic abc",
            "Authorization: opaque-value",
            "Authorization: Custom 中文令牌",
            "Authorization opaque",
            "Authorization\topaque",
            "Authorization\nopaque",
            "Authorization\r\nopaque",
            "Authorization Custom 中文令牌",
            "authorization custom-token",
            "Authorization，opaque",
            "Authorization, opaque",
            "Authorization：Custom 中文令牌",
            "Authorization；opaque",
            "Authorization; opaque",
            "Authorization header: opaque",
            "Authorization policy: opaque",
            "Authorization field: value",
            "Authorization policy opaque",
            "Authorization field opaque",
            "Authorization 字段 opaque",
            "Authorization [field definition]",
            "Authorization （说明）",
            "Authorization policy opaque should be logged",
            "Authorization header, opaque",
            "Authorization policy—opaque",
            "Authorization=opaque",
            "Authorization（opaque）",
            "Authorization / opaque",
            "说明：Authorization: opaque-value。",
            "(Authorization: opaque-value)",
            "[Authorization: opaque-value]",
            "`Authorization: opaque-value`",
            "说明（Authorization opaque）",
            "说明[Authorization，opaque]",
            "> Authorization opaque",
            "- Authorization opaque",
            "* Authorization opaque",
            "1. Authorization opaque",
            "- [ ] Authorization opaque",
            "> - [x] Authorization opaque",
            "  * > Authorization opaque",
            "> 1. [X] Authorization Custom 中文令牌",
            "Bearer abc",
            "Basic abc",
            "Cookie: session=abc",
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_unsafe(value)

    def test_rejects_authorization_marker_without_language_or_word_boundaries(self):
        payloads = (
            "Authorization",
            "authorization",
            "AUTHORIZATION",
            "Authorizationopaque",
            "preAuthorizationValue",
            "Authorization私密值",
            "Authorizationｏｐａｑｕｅ",
            "Ａｕｔｈｏｒｉｚａｔｉｏｎopaque",
            "Auth\u200borizationopaque",
            "𝐀𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐚𝐭𝐢𝐨𝐧opaque",
            "Authｏｒization私密值",
        )
        containers = ("{}", "> {}", "- {}", "1. {}", "> - [ ] {}", "    {}")
        for payload in payloads:
            for container in containers:
                with self.subTest(payload=payload, container=container):
                    self.assert_unsafe(container.format(payload))

    def test_accepts_authentication_explanations_without_forbidden_marker(self):
        values = (
            "认证请求头的值不得进入公开进度。",
            "认证边界由安全合同管理。",
            "授权规则保持只读。",
            "普通说明不包含认证值。",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(safe_public_text.validate_safe_public_text(value), value)

    def test_renders_only_closed_safe_public_messages(self):
        self.assertTrue(
            hasattr(safe_public_text, "render_safe_public_message"),
            "closed safe-public message renderer is required",
        )
        self.assertEqual(
            safe_public_text.render_safe_public_message(
                "AUTHORIZATION_VALUE_HIDDEN"
            ),
            "认证请求头的值不得进入公开进度。",
        )
        for code in (
            None,
            True,
            1,
            "",
            "UNKNOWN",
            "AUTHORIZATION_VALUE_HIDDEN:extra",
        ):
            with self.subTest(code=code):
                with self.assertRaises(
                    safe_public_text.SafePublicTextError
                ) as caught:
                    safe_public_text.render_safe_public_message(code)
                self.assertEqual(caught.exception.reason, "UNSAFE_PUBLIC_TEXT")

    def test_rejects_authorization_pseudo_explanations_with_nonempty_values(self):
        payloads = (
            "Authorization 字段 私密值",
            "Authorization 说明 秘密令牌",
            "Authorization 字段 ｏｐａｑｕｅ",
            "Authorization 字段定义 密钥",
            "Authorization 说明 private令牌",
            "Authorization 字段私密值",
            "Authorization 说明私密值",
            "Authorization 仅私密值",
        )
        containers = ("{}", "> {}", "- {}", "1. {}", "> - {}")
        for payload in payloads:
            for container in containers:
                value = container.format(payload)
                with self.subTest(value=value):
                    self.assert_unsafe(value)

    def test_rejects_prohibited_payloads_surrounded_by_punctuation(self):
        values = (
            "（FEATURE_FLAG=true）",
            "[FEATURE_FLAG=true]",
            "{FEATURE_FLAG=true}",
            '"FEATURE_FLAG=true"',
            "'FEATURE_FLAG=true'",
            "“FEATURE_FLAG=true”",
            "‘FEATURE_FLAG=true’",
            "说明：FEATURE_FLAG=true。",
            "说明: diff --git a/private b/private.",
            "说明：http://example.com/private。",
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_unsafe(value)

    def test_rejects_invalid_type_bounds_controls_and_unicode(self):
        values = (None, True, 1, "", "x" * 4097, "nul\x00value", "bell\x07value", "\ud800")
        for value in values:
            with self.subTest(value=repr(value)):
                self.assert_unsafe(value)

    def test_accepts_safe_technical_and_chinese_text(self):
        values = (
            "中文说明：当前阶段等待用户观察。",
            "认证请求头由只读安全合同管理。",
            "FEATURE_FLAG",
            "环境变量名称 DATABASE_URL 未展示值",
            "中文说明：状态=就绪",
            "a == b",
            "/srv/fixture/projects/sample-app",
            r"C:\fixture\projects\sample-app",
            "git at example.com colon local path",
            "协议说明 https colon slash slash host",
            "差异标记说明只用于风险解释",
            "方括号 x 表示普通文字，不是任务列表",
            "TASK-2.2-DEMO",
            "a" * 40,
            "b" * 64,
            "普通括号（只读说明）和 [状态]",
            "列表项 - 普通说明，不包含原始差异",
            "第一行\n第二行",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(safe_public_text.validate_safe_public_text(value), value)

    def test_lexical_boundaries_do_not_depend_on_one_wrapper_regex(self):
        unsafe = (
            "FEATURE_FLAG =true",
            "FEATURE_FLAG= true",
            "(FEATURE_FLAG=true)",
            "http://host",
            " diff --git a/a b/a",
            "- [ ] task",
            "prefix `code` suffix",
        )
        safe = (
            "FEATURE_FLAG",
            "FEATURE_FLAG true",
            "FEATURE_FLAG==true",
            "http:/host",
            "dif --git a/a b/a",
            "- [] task",
            "prefix 'code' suffix",
        )
        for value in unsafe:
            with self.subTest(kind="unsafe", value=value):
                self.assert_unsafe(value)
        for value in safe:
            with self.subTest(kind="safe", value=value):
                self.assertEqual(safe_public_text.validate_safe_public_text(value), value)

    def test_escape_markdown_validates_then_escapes_structural_characters(self):
        value = "阶段 #1: [只读] *通过* | 保持原义"
        escaped = safe_public_text.escape_markdown_text(value)
        self.assertEqual(escaped, r"阶段 \#1: \[只读\] \*通过\* \| 保持原义")
        self.assertEqual(safe_public_text.escape_markdown_text(value), escaped)

        with self.assertRaises(safe_public_text.SafePublicTextError) as caught:
            safe_public_text.escape_markdown_text("[x] FEATURE_FLAG=true")
        self.assertEqual(caught.exception.reason, "UNSAFE_PUBLIC_TEXT")


if __name__ == "__main__":
    unittest.main()
