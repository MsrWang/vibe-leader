# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Focused behavior tests for the local-only evidence screening helper."""

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "vibe-project-lead-zh" / "scripts" / "evidence_filter.py"
MODULE_NAME = "evidence_filter_under_test"
EVIDENCE_FILTER = types.ModuleType(MODULE_NAME)
EVIDENCE_FILTER.__file__ = str(SCRIPT)
EVIDENCE_FILTER.__package__ = ""
sys.modules[MODULE_NAME] = EVIDENCE_FILTER
exec(
    compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"),
    EVIDENCE_FILTER.__dict__,
)

RESULT_KEYS = {
    "schema_version",
    "status",
    "mode",
    "candidate_count",
    "selected_count",
    "readback_count",
    "fallback_reason",
    "items",
}


class EvidenceFilterTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self._tempdir.cleanup)
        self.root = Path(self._tempdir.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.previous_cwd)

    def write_text(self, relative_path, text):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def screen(self, query, files, *, enabled=True):
        return EVIDENCE_FILTER.screen_project_files(
            query,
            files,
            enabled=enabled,
        )

    def assert_empty_result(
        self,
        result,
        *,
        status,
        mode,
        reason,
        selected_count=0,
    ):
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["status"], status)
        self.assertEqual(result["mode"], mode)
        self.assertEqual(result["fallback_reason"], reason)
        self.assertEqual(result["selected_count"], selected_count)
        self.assertEqual(result["readback_count"], 0)
        self.assertEqual(result["items"], [])

    def run_cli(self, raw):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT)],
            cwd=self.root,
            input=raw,
            capture_output=True,
            check=False,
        )

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

        with mock.patch.object(EVIDENCE_FILTER.sys, "platform", "darwin"):
            with mock.patch.object(EVIDENCE_FILTER.os, "O_NOFOLLOW_ANY", 0x20000000, create=True):
                with mock.patch.object(EVIDENCE_FILTER.os, "supports_dir_fd", set()):
                    self.assertIsNone(EVIDENCE_FILTER._secure_read_backend())

    def test_darwin_root_open_rejects_identity_change(self):
        self.write_text("other/file.txt", "evidence")
        root_path = str(self.root)
        original_lstat = os.lstat
        changed_identity = original_lstat(self.root / "other")
        root_lstats = 0

        def changing_lstat(path, *args, **kwargs):
            nonlocal root_lstats
            if path == root_path:
                root_lstats += 1
                if root_lstats == 2:
                    return changed_identity
            return original_lstat(path, *args, **kwargs)

        fake_flag = 0x20000000
        original_open = os.open
        opened = []

        def opening(path, flags, *args, **kwargs):
            opened.append((path, flags, kwargs))
            return original_open(path, flags & ~fake_flag, *args, **kwargs)

        with mock.patch.object(EVIDENCE_FILTER, "_secure_read_backend", return_value="DARWIN_OPENAT"):
            with mock.patch.object(EVIDENCE_FILTER.os, "O_NOFOLLOW_ANY", fake_flag, create=True):
                with mock.patch.object(EVIDENCE_FILTER.os, "lstat", side_effect=changing_lstat):
                    with mock.patch.object(EVIDENCE_FILTER.os, "open", side_effect=opening):
                        with self.assertRaises(EVIDENCE_FILTER.EvidenceInputError) as context:
                            EVIDENCE_FILTER._open_root(root_path)

        self.assertEqual(context.exception.reason, "PATH_UNSAFE")
        self.assertEqual(root_lstats, 2)
        self.assertTrue(opened[0][1] & fake_flag)

    def test_darwin_relative_open_uses_anchored_descriptors(self):
        self.write_text("nested/source.txt", "inventory evidence")
        fake_flag = 0x20000000
        original_open = os.open
        opens = []

        def opening(path, flags, *args, **kwargs):
            opens.append((path, flags, kwargs))
            return original_open(path, flags & ~fake_flag, *args, **kwargs)

        with mock.patch.object(EVIDENCE_FILTER, "_secure_read_backend", return_value="DARWIN_OPENAT"):
            with mock.patch.object(EVIDENCE_FILTER.os, "O_NOFOLLOW_ANY", fake_flag, create=True):
                with mock.patch.object(EVIDENCE_FILTER.os, "open", side_effect=opening):
                    with mock.patch.object(EVIDENCE_FILTER.os, "readlink", side_effect=AssertionError("/proc use")):
                        result = self.screen("inventory", ["nested/source.txt"])

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["items"][0]["text"], "inventory evidence")
        root_opens = [item for item in opens if item[0] == str(self.root)]
        directory_opens = [item for item in opens if item[0] == b"nested"]
        leaf_opens = [item for item in opens if item[0] == b"source.txt"]
        self.assertEqual(len(root_opens), 1)
        self.assertTrue(root_opens[0][1] & fake_flag)
        self.assertTrue(directory_opens)
        self.assertTrue(all(item[1] & os.O_NOFOLLOW for item in directory_opens))
        self.assertTrue(all("dir_fd" in item[2] for item in directory_opens + leaf_opens))
        self.assertTrue(all(item[1] & fake_flag for item in leaf_opens))

    def test_darwin_missing_nofollow_any_returns_unknown(self):
        self.write_text("source.txt", "inventory evidence")
        with mock.patch.object(EVIDENCE_FILTER.sys, "platform", "darwin"):
            with mock.patch.object(EVIDENCE_FILTER.os, "O_NOFOLLOW_ANY", 0, create=True):
                result = self.screen("inventory", ["source.txt"])
        self.assert_empty_result(
            result, status="UNKNOWN", mode="UNKNOWN", reason="SECURE_READ_UNAVAILABLE"
        )

    def test_linux_backend_keeps_proc_fd_identity_proof(self):
        self.write_text("source.txt", "inventory evidence")
        original_readlink = os.readlink
        with mock.patch.object(EVIDENCE_FILTER.sys, "platform", "linux"):
            with mock.patch.object(EVIDENCE_FILTER.os, "readlink", wraps=original_readlink) as readlink:
                with mock.patch.object(
                    EVIDENCE_FILTER.os,
                    "supports_dir_fd",
                    set(os.supports_dir_fd) | {readlink},
                ):
                    result = self.screen("inventory", ["source.txt"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["items"][0]["text"], "inventory evidence")
        self.assertTrue(any(
            call.args and str(call.args[0]).startswith("/proc/self/fd/")
            for call in readlink.call_args_list
        ))

    def test_local_filter_returns_exact_verified_bytes_without_paths(self):
        content = "inventory reconciliation evidence\n"
        self.write_text("evidence/sample.txt", content)

        result = self.screen("inventory", ["evidence/sample.txt"])

        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["mode"], "LOCAL_FILTER")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["readback_count"], 1)
        self.assertIsNone(result["fallback_reason"])
        self.assertEqual(
            result["items"],
            [{
                "source_ref": "F0001",
                "candidate_id": "C0001",
                "byte_start": 0,
                "byte_end": len(content.encode("utf-8")),
                "text": content,
            }],
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("evidence/sample.txt", serialized)

    def test_disabled_mode_bypasses_without_reading_sources(self):
        result = self.screen("inventory", ["missing.txt"], enabled=False)

        self.assert_empty_result(
            result,
            status="OK",
            mode="BYPASS",
            reason="DISABLED",
        )
        self.assertEqual(result["candidate_count"], 0)

    def test_sensitive_queries_are_blocked_without_echoing_input(self):
        self.write_text("safe.txt", "ordinary evidence")
        sensitive_queries = (
            '"api_key" = "private-value"',
            "service.access_token=private-value",
            "Authorization: Bearer private-value",
            "-----BEGIN PRIVATE KEY-----",
            "sk-1234567890abcdefghijklmnop",
            "person@example.com",
            "13800138000",
            "inspect /tmp/private.txt",
            r"inspect C:\Users\pc\private.txt",
            "inspect file:///tmp/private.txt",
        )

        for query in sensitive_queries:
            with self.subTest(query=query):
                result = self.screen(query, ["safe.txt"])
                self.assert_empty_result(
                    result,
                    status="BLOCKED",
                    mode="BLOCKED",
                    reason="SENSITIVE_INPUT_BLOCKED",
                )
                self.assertNotIn(query, json.dumps(result, ensure_ascii=False))

    def test_sensitive_file_content_is_blocked_without_excerpts(self):
        sensitive_contents = (
            '"client_secret": "private-value"',
            "Cookie: session=private-value",
            "person@example.com",
            "13800138000",
            "read /var/private/evidence.txt",
        )

        for content in sensitive_contents:
            with self.subTest(content=content):
                self.write_text("source.txt", content)
                result = self.screen("evidence", ["source.txt"])
                self.assert_empty_result(
                    result,
                    status="BLOCKED",
                    mode="BLOCKED",
                    reason="SENSITIVE_INPUT_BLOCKED",
                )
                self.assertNotIn(content, json.dumps(result, ensure_ascii=False))

    def test_unsafe_paths_symlinks_and_special_files_are_refused(self):
        self.write_text("safe.txt", "ordinary evidence")
        unsafe_inputs = (
            ["../safe.txt"],
            [str(self.root / "safe.txt")],
            [r"folder\safe.txt"],
            ["C:/temp/safe.txt"],
            ["folder//safe.txt"],
            ["safe.txt", "safe.txt"],
        )
        for files in unsafe_inputs:
            with self.subTest(files=files):
                result = self.screen("evidence", files)
                self.assert_empty_result(
                    result,
                    status="UNKNOWN",
                    mode="UNKNOWN",
                    reason="PATH_UNSAFE",
                )

        os.symlink("safe.txt", self.root / "linked.txt")
        os.mkfifo(self.root / "pipe")
        for relative_path in ("linked.txt", "pipe"):
            with self.subTest(relative_path=relative_path):
                result = self.screen("evidence", [relative_path])
                self.assert_empty_result(
                    result,
                    status="UNKNOWN",
                    mode="UNKNOWN",
                    reason="PATH_UNSAFE",
                )

    def test_invalid_utf8_is_refused_without_excerpts(self):
        (self.root / "invalid.bin").write_bytes(b"valid-prefix\xff")

        result = self.screen("evidence", ["invalid.bin"])

        self.assert_empty_result(
            result,
            status="UNKNOWN",
            mode="UNKNOWN",
            reason="INVALID_UTF8",
        )

    def test_multibyte_text_crossing_window_boundary_is_read_back_exactly(self):
        raw = b"a" * 4_095 + "库存".encode("utf-8") + b" evidence"
        (self.root / "boundary.txt").write_bytes(raw)

        result = self.screen("库存", ["boundary.txt"])

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["readback_count"], 1)
        self.assertEqual(result["items"][0]["candidate_id"], "C0002")
        self.assertEqual(result["items"][0]["byte_start"], 3_583)
        self.assertEqual(result["items"][0]["byte_end"], len(raw))
        self.assertEqual(
            result["items"][0]["text"],
            raw[3_583:].decode("utf-8"),
        )

    def test_primary_selector_failure_and_invalid_ids_use_local_fallback(self):
        self.write_text("source.txt", "inventory evidence")
        primary_failures = (
            RuntimeError("synthetic selector failure"),
            ("NOT-A-CANDIDATE",),
        )

        for primary_result in primary_failures:
            with self.subTest(primary_result=primary_result):
                if isinstance(primary_result, Exception):
                    patch = mock.patch.object(
                        EVIDENCE_FILTER.LocalLexicalSelector,
                        "select",
                        side_effect=primary_result,
                    )
                else:
                    patch = mock.patch.object(
                        EVIDENCE_FILTER.LocalLexicalSelector,
                        "select",
                        return_value=primary_result,
                    )
                with patch:
                    result = self.screen("inventory", ["source.txt"])
                self.assertEqual(result["status"], "OK")
                self.assertEqual(result["mode"], "LOCAL_FALLBACK")
                self.assertEqual(result["fallback_reason"], "SELECTOR_ERROR")
                self.assertEqual(result["selected_count"], 1)
                self.assertEqual(result["readback_count"], 1)
                self.assertEqual(result["items"][0]["text"], "inventory evidence")

    def test_change_in_unselected_source_discards_selected_excerpts(self):
        self.write_text("selected.txt", "target evidence")
        unselected = self.write_text("unselected.txt", "stable secondary evidence")

        def mutate_unselected(_query, candidates):
            unselected.write_text("changed secondary evidence", encoding="utf-8")
            return (candidates[0].candidate_id,)

        with mock.patch.object(
            EVIDENCE_FILTER.LocalLexicalSelector,
            "select",
            side_effect=mutate_unselected,
        ):
            result = self.screen(
                "target",
                ["selected.txt", "unselected.txt"],
            )

        self.assert_empty_result(
            result,
            status="UNKNOWN",
            mode="UNKNOWN",
            reason="SOURCE_CHANGED_DURING_READBACK",
            selected_count=1,
        )
        self.assertEqual(result["candidate_count"], 2)

    def test_selection_is_deterministic_and_capped_at_twenty_four(self):
        files = []
        for index in range(30):
            relative_path = f"source-{index:02d}.txt"
            self.write_text(relative_path, f"inventory evidence item {index}\n")
            files.append(relative_path)

        result = self.screen("inventory", files)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["mode"], "LOCAL_FILTER")
        self.assertEqual(result["candidate_count"], 30)
        self.assertEqual(result["selected_count"], 24)
        self.assertEqual(result["readback_count"], 24)
        self.assertEqual(
            [item["candidate_id"] for item in result["items"]],
            [f"C{index:04d}" for index in range(1, 25)],
        )

    def test_empty_and_unmatched_sources_return_explicit_reasons(self):
        self.write_text("empty.txt", "")
        empty = self.screen("inventory", ["empty.txt"])
        self.assert_empty_result(
            empty,
            status="OK",
            mode="LOCAL_FILTER",
            reason="NO_CANDIDATE_WINDOWS",
        )
        self.assertEqual(empty["candidate_count"], 0)

        self.write_text("unmatched.txt", "alpha beta")
        unmatched = self.screen("inventory", ["unmatched.txt"])
        self.assert_empty_result(
            unmatched,
            status="OK",
            mode="LOCAL_FILTER",
            reason="NO_LOCAL_MATCH",
        )
        self.assertEqual(unmatched["candidate_count"], 1)

    def test_cli_enforces_strict_json_and_reports_compact_results(self):
        self.write_text("source.txt", "inventory evidence")
        valid = self.run_cli(
            b'{"query":"inventory","files":["source.txt"]}'
        )
        self.assertEqual(valid.returncode, 0, valid.stderr.decode("utf-8"))
        self.assertEqual(len(valid.stdout.splitlines()), 1)
        valid_payload = json.loads(valid.stdout)
        self.assertEqual(set(valid_payload), RESULT_KEYS)
        self.assertEqual(valid_payload["status"], "OK")

        invalid_requests = (
            b'{"query":"one","query":"two","files":[]}',
            b'{"query":"one","files":[],"extra":true}',
            b'{"query":NaN,"files":[]}',
        )
        for raw in invalid_requests:
            with self.subTest(raw=raw):
                rejected = self.run_cli(raw)
                self.assertEqual(rejected.returncode, 3)
                self.assertEqual(rejected.stderr, b"")
                self.assertEqual(len(rejected.stdout.splitlines()), 1)
                payload = json.loads(rejected.stdout)
                self.assert_empty_result(
                    payload,
                    status="UNKNOWN",
                    mode="UNKNOWN",
                    reason="INPUT_SCHEMA_INVALID",
                )
                self.assertNotIn(raw.decode("utf-8"), rejected.stdout.decode("utf-8"))

        oversized = self.run_cli(b" " * (EVIDENCE_FILTER.MAX_JSON_BYTES + 1))
        self.assertEqual(oversized.returncode, 3)
        oversized_payload = json.loads(oversized.stdout)
        self.assert_empty_result(
            oversized_payload,
            status="UNKNOWN",
            mode="UNKNOWN",
            reason="INPUT_LIMIT_EXCEEDED",
        )

    def test_missing_secure_read_capability_returns_unknown(self):
        self.write_text("source.txt", "inventory evidence")

        with mock.patch.object(
            EVIDENCE_FILTER,
            "_secure_read_backend",
            return_value=None,
        ):
            result = self.screen("inventory", ["source.txt"])

        self.assert_empty_result(
            result,
            status="UNKNOWN",
            mode="UNKNOWN",
            reason="SECURE_READ_UNAVAILABLE",
        )


if __name__ == "__main__":
    unittest.main()
