# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Offline Skill commands reject encoding errors without changing outputs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SkillInputEncodingTests(unittest.TestCase):
    def check_invalid_encoding(self, field):
        for command in ("skill_inventory", "skill_route"):
            for outputs_exist in (False, True):
                with self.subTest(command=command, outputs_exist=outputs_exist):
                    with tempfile.TemporaryDirectory(
                        prefix=".skill-input-test-", dir=ROOT,
                    ) as directory:
                        temporary = Path(directory)
                        response = temporary / "response.json"
                        response.write_text("{}", encoding="utf-8")
                        policy = ROOT / "workbench" / "policy.toml"
                        if field == "policy":
                            policy = temporary / "policy.toml"
                            policy.write_bytes(b"\xff")
                        else:
                            response.write_bytes(b"\xff")
                        output = temporary / "navigation.md"
                        inventory = temporary / "inventory.json"
                        if outputs_exist:
                            output.write_bytes(b"existing navigation\n")
                            inventory.write_bytes(b"existing inventory\n")
                        before = {
                            p.name: p.read_bytes() for p in temporary.iterdir()
                        }
                        arguments = [
                            sys.executable, "-B", "-m", f"workbench.{command}",
                            "--input", str(response), "--policy", str(policy),
                        ]
                        if command == "skill_inventory":
                            arguments.extend([
                                "--codex-version", "synthetic-1",
                                "--codex-bin", "/synthetic/unused-codex",
                                "--output", str(output),
                                "--inventory-output", str(inventory),
                            ])
                        else:
                            arguments.extend(["--cwd", str(temporary)])
                        result = subprocess.run(
                            arguments, cwd=ROOT, capture_output=True,
                            text=True, check=False, timeout=10,
                        )
                        self.assertEqual(result.returncode, 4, result.stderr)
                        self.assertEqual(result.stderr, "")
                        payload = json.loads(result.stdout)
                        self.assertEqual(
                            payload["status"],
                            "protocol_error" if command == "skill_inventory" else "UNKNOWN",
                        )
                        self.assertIn("UTF-8", payload["reason"])
                        if command == "skill_route":
                            self.assertFalse(payload["write_authorized"])
                            self.assertFalse(payload["instructions_loaded"])
                        self.assertEqual(
                            {p.name: p.read_bytes() for p in temporary.iterdir()},
                            before,
                        )

    def test_invalid_response_encoding_is_reported_without_output_changes(self):
        self.check_invalid_encoding("response")

    def test_invalid_policy_encoding_is_reported_without_output_changes(self):
        self.check_invalid_encoding("policy")
