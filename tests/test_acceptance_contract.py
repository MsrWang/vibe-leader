# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Synthetic acceptance contracts, never evidence of real user acceptance."""

import json
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/acceptance-contract.json"
ALLOWED_NAMES = {
    "technical-acceptance.md",
    "behavior-evaluation.md",
    "evaluation-surface-preflight.md",
    "user-acceptance.md",
}
REQUIRED_FIELDS = (
    "状态",
    "需求或门禁 ID",
    "方法或命令",
    "cwd",
    "HEAD / diff 或适用环境",
    "关键结果",
    "已通过",
    "警告",
    "未验证",
    "未知",
    "用户验收",
    "下一门禁",
)


MISSING_GATES = {
    "behavior-evaluation.md": "SYNTHETIC-BEHAVIOR",
    "evaluation-surface-preflight.md": "SYNTHETIC-PREFLIGHT",
    "user-acceptance.md": "SYNTHETIC-USER",
}
EXPECTED_GATES = {"SYNTHETIC-INSTALL", *MISSING_GATES.values()}


def load_documents(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise AssertionError("acceptance directory missing")
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(root.glob("*.md"))
    }


def validate_documents(documents: dict[str, str]) -> str:
    """Check synthetic fixture contracts; never grant real user acceptance."""

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    require("technical-acceptance.md" in documents, "technical missing")
    require(set(documents) <= ALLOWED_NAMES, "noncanonical document")
    for name, text in documents.items():
        require("合成测试资料：" in text, f"{name}: synthetic label missing")
        for field in REQUIRED_FIELDS:
            require(field in text, f"{name}: required field missing: {field}")
    technical = documents["technical-acceptance.md"]
    require(
        sum(line.startswith("版本状态：") for line in technical.splitlines()) == 1,
        "version status declaration must be unique",
    )
    statuses = re.findall(r"^版本状态：`?([A-Z_]+)`?$", technical, re.MULTILINE)
    require(len(statuses) == 1, "version status must be unique")
    status = statuses[0]
    require(status in {"IN_PROGRESS", "USER_ACCEPTED"}, "unsupported status")
    if status == "IN_PROGRESS":
        require("WAITING_GATE" in technical, "waiting gate missing")
        for name, gate in MISSING_GATES.items():
            if name not in documents:
                require(
                    name in technical and gate in technical,
                    "missing gate not named",
                )
        return status

    require(set(documents) == ALLOWED_NAMES, "accepted documents incomplete")
    combined = "\n".join(
        documents[name]
        for name in (
            "technical-acceptance.md", "behavior-evaluation.md", "user-acceptance.md"
        )
    )
    for marker in (
        "SYNTHETIC-INSTALL", "install-manifest", "禁用", "回滚", "当前候选"
    ):
        require(marker in combined, f"evidence marker missing: {marker}")
    rows = [
        line for line in technical.splitlines() if line.startswith("| SYNTHETIC-")
    ]
    gate_ids = [line.split("|")[1].strip() for line in rows]
    require(
        len(gate_ids) == len(EXPECTED_GATES) and set(gate_ids) == EXPECTED_GATES,
        "applicable gate set changed",
    )
    unresolved = re.compile(r"\b(?:FAIL|UNKNOWN|WAITING(?:_GATE)?|UNVERIFIED)\b")
    for row in rows:
        require(unresolved.search(row) is None, "gate unresolved")
    passed = re.findall(
        r"^\|\s*([1-9])\s*\|.*\|\s*PASS\s*\|",
        documents["user-acceptance.md"],
        re.MULTILINE,
    )
    require(
        sorted(set(passed)) == [str(number) for number in range(1, 10)],
        "user rows incomplete",
    )
    return status


class AcceptanceContractTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            set(self.fixtures), {"kind", "in_progress", "user_accepted"}
        )
        self.assertEqual(self.fixtures["kind"], "synthetic-acceptance-contract")

    def test_synthetic_validator_interface_exists(self):
        self.assertTrue(
            callable(globals().get("validate_documents")),
            "synthetic acceptance validator missing",
        )

    def test_in_progress_fixture_enters_its_contract(self):
        self.assertEqual(
            validate_documents(self.fixtures["in_progress"]), "IN_PROGRESS"
        )

    def test_accepted_fixture_enters_its_contract(self):
        self.assertEqual(
            validate_documents(self.fixtures["user_accepted"]), "USER_ACCEPTED"
        )

    def test_each_required_field_is_enforced_in_each_document(self):
        for name in sorted(ALLOWED_NAMES):
            for field in REQUIRED_FIELDS:
                changed = dict(self.fixtures["user_accepted"])
                changed[name] = changed[name].replace(field, "已移除字段")
                with self.subTest(name=name, field=field):
                    with self.assertRaisesRegex(
                        AssertionError,
                        re.escape(f"{name}: required field missing: {field}"),
                    ):
                        validate_documents(changed)

    def test_missing_acceptance_row_is_rejected(self):
        for number in range(1, 10):
            changed = dict(self.fixtures["user_accepted"])
            row = f"| {number} | 合成观察 | PASS |\n"
            self.assertEqual(changed["user-acceptance.md"].count(row), 1)
            changed["user-acceptance.md"] = changed["user-acceptance.md"].replace(
                row, ""
            )
            with self.subTest(number=number):
                with self.assertRaisesRegex(AssertionError, "user rows incomplete"):
                    validate_documents(changed)

    def test_technical_document_is_required(self):
        for state in ("in_progress", "user_accepted"):
            changed = dict(self.fixtures[state])
            changed.pop("technical-acceptance.md")
            with self.subTest(state=state):
                with self.assertRaisesRegex(AssertionError, "technical missing"):
                    validate_documents(changed)

    def test_only_canonical_names_are_allowed(self):
        for state in ("in_progress", "user_accepted"):
            changed = dict(self.fixtures[state])
            changed["extra.md"] = "合成正文"
            with self.subTest(state=state):
                with self.assertRaisesRegex(AssertionError, "noncanonical document"):
                    validate_documents(changed)

    def test_accepted_requires_each_document(self):
        for name in (
            "behavior-evaluation.md",
            "evaluation-surface-preflight.md",
            "user-acceptance.md",
        ):
            changed = dict(self.fixtures["user_accepted"])
            changed.pop(name)
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    AssertionError, "accepted documents incomplete"
                ):
                    validate_documents(changed)

    def test_in_progress_names_each_missing_document_and_gate(self):
        for token in (
            "behavior-evaluation.md",
            "evaluation-surface-preflight.md",
            "user-acceptance.md",
            "SYNTHETIC-BEHAVIOR",
            "SYNTHETIC-PREFLIGHT",
            "SYNTHETIC-USER",
        ):
            changed = dict(self.fixtures["in_progress"])
            self.assertIn(token, changed["technical-acceptance.md"])
            changed["technical-acceptance.md"] = changed[
                "technical-acceptance.md"
            ].replace(token, "")
            with self.subTest(token=token):
                with self.assertRaisesRegex(AssertionError, "missing gate not named"):
                    validate_documents(changed)

    def test_in_progress_requires_waiting_gate(self):
        changed = dict(self.fixtures["in_progress"])
        changed["technical-acceptance.md"] = changed[
            "technical-acceptance.md"
        ].replace("WAITING_GATE", "")
        with self.assertRaisesRegex(AssertionError, "waiting gate missing"):
            validate_documents(changed)

    def test_accepted_requires_each_evidence_marker(self):
        for marker in (
            "SYNTHETIC-INSTALL", "install-manifest", "禁用", "回滚", "当前候选"
        ):
            changed = dict(self.fixtures["user_accepted"])
            for name in (
                "technical-acceptance.md",
                "behavior-evaluation.md",
                "user-acceptance.md",
            ):
                changed[name] = changed[name].replace(marker, "")
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(
                    AssertionError, re.escape(f"evidence marker missing: {marker}")
                ):
                    validate_documents(changed)

    def test_accepted_requires_each_gate_row(self):
        original = self.fixtures["user_accepted"]["technical-acceptance.md"]
        rows = (
            "| SYNTHETIC-INSTALL | PASS |\n",
            "| SYNTHETIC-BEHAVIOR | PASS |\n",
            "| SYNTHETIC-PREFLIGHT | PASS |\n",
            "| SYNTHETIC-USER | PASS |\n",
        )
        cases = {row: original.replace(row, "") for row in rows}
        cases["all-absent"] = "".join(
            line for line in original.splitlines(keepends=True) if line not in rows
        )
        cases["duplicate"] = original + rows[0]
        cases["extra"] = original + "| SYNTHETIC-EXTRA | PASS |\n"
        for label, text in cases.items():
            changed = dict(self.fixtures["user_accepted"])
            changed["technical-acceptance.md"] = text
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    AssertionError, "applicable gate set changed"
                ):
                    validate_documents(changed)

    def test_accepted_rejects_each_unresolved_gate_status(self):
        for gate in (
            "SYNTHETIC-INSTALL",
            "SYNTHETIC-BEHAVIOR",
            "SYNTHETIC-PREFLIGHT",
            "SYNTHETIC-USER",
        ):
            for status in ("FAIL", "UNKNOWN", "WAITING", "WAITING_GATE", "UNVERIFIED"):
                changed = dict(self.fixtures["user_accepted"])
                changed["technical-acceptance.md"] = changed[
                    "technical-acceptance.md"
                ].replace(f"| {gate} | PASS |", f"| {gate} | {status} |")
                with self.subTest(gate=gate, status=status):
                    with self.assertRaisesRegex(AssertionError, "gate unresolved"):
                        validate_documents(changed)

    def test_version_status_is_unique_and_supported(self):
        for replacement in (
            "",
            "版本状态：USER_ACCEPTED\n版本状态：USER_ACCEPTED\n",
            "版本状态：USER_ACCEPTED\n版本状态：IN_PROGRESS\n",
            "版本状态：USER_ACCEPTED\n版本状态：malformed\n",
            "版本状态：UNVERIFIED\n",
            "版本状态：BOGUS\n",
            "版本状态：malformed\n",
        ):
            changed = dict(self.fixtures["user_accepted"])
            changed["technical-acceptance.md"] = changed[
                "technical-acceptance.md"
            ].replace("版本状态：USER_ACCEPTED\n", replacement)
            with self.subTest(replacement=replacement):
                with self.assertRaises(AssertionError):
                    validate_documents(changed)

    def test_disk_fixture_has_exact_names_and_fields(self):
        for state, expected in (
            ("in_progress", "IN_PROGRESS"), ("user_accepted", "USER_ACCEPTED")
        ):
            with self.subTest(state=state), tempfile.TemporaryDirectory(
                dir="/tmp", prefix="vibe-acceptance-contract.test."
            ) as directory:
                root = Path(directory)
                for name, text in self.fixtures[state].items():
                    (root / name).write_text(text, encoding="utf-8")
                documents = load_documents(root)
                self.assertEqual(documents, self.fixtures[state])
                self.assertEqual(validate_documents(documents), expected)

    def test_each_document_requires_synthetic_label(self):
        for name in sorted(ALLOWED_NAMES):
            changed = dict(self.fixtures["user_accepted"])
            changed[name] = changed[name].replace("合成测试资料：", "")
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    AssertionError, re.escape(f"{name}: synthetic label missing")
                ):
                    validate_documents(changed)

    def test_missing_document_directory_is_rejected(self):
        with tempfile.TemporaryDirectory(
            dir="/tmp", prefix="vibe-acceptance-contract.test."
        ) as directory:
            with self.assertRaisesRegex(
                AssertionError, "acceptance directory missing"
            ):
                load_documents(Path(directory) / "missing")


if __name__ == "__main__":
    unittest.main()
