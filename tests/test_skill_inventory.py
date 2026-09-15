# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import copy
import importlib.util
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import closing
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workbench" / "skill_inventory.py"
POLICY = ROOT / "workbench" / "policy.toml"
FIXTURES = ROOT / "tests" / "fixtures"


class SkillInventoryTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory(
            prefix="vibe-project-lead-inventory-test-",
            dir=ROOT.parent,
        )
        self.addCleanup(self._tempdir.cleanup)
        self.tempdir = Path(self._tempdir.name)
        self.output = self.tempdir / "SKILL_INDEX_ZH.md"
        self.inventory_output = self.tempdir / "inventory.json"
        self._fixture_index = 0

    def load_module(self, path=SCRIPT):
        name = f"skill_inventory_under_test_{id(self)}_{self._fixture_index}"
        self._fixture_index += 1
        spec = importlib.util.spec_from_file_location(name, path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        return module

    def fixture_payload(self, fixture_name):
        return json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))

    def materialize_payload(self, payload, name="fixture.json"):
        payload = copy.deepcopy(payload)
        fixture_root = self.tempdir / "discovery" / f"{self._fixture_index:02d}"
        self._fixture_index += 1
        for index, item in enumerate(payload["result"]["data"][0]["skills"]):
            declared = item["name"].rsplit(":", 1)[-1]
            skill_dir = fixture_root / f"{index:02d}-{declared}"
            skill_dir.mkdir(parents=True)
            locator = skill_dir / "SKILL.md"
            locator.write_text(
                f"---\nname: {declared}\ndescription: fixture\n---\n",
                encoding="utf-8",
            )
            item["path"] = str(locator)
        path = self.tempdir / f"{self._fixture_index:02d}-{name}"
        self._fixture_index += 1
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def materialize_fixture(self, fixture_name):
        return self.materialize_payload(self.fixture_payload(fixture_name), fixture_name)

    def run_index(self, fixture, *extra, policy=POLICY):
        fixture_path = fixture if isinstance(fixture, Path) else self.materialize_fixture(fixture)
        return subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--input",
                fixture_path,
                "--codex-version",
                "fixture-codex-1.0",
                "--codex-bin",
                "/fixture/bin/codex",
                "--policy",
                policy,
                "--output",
                self.output,
                "--inventory-output",
                self.inventory_output,
                *map(str, extra),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def run_payload(self, payload, *extra, policy=POLICY, name="payload.json"):
        return self.run_index(
            self.materialize_payload(payload, name),
            *extra,
            policy=policy,
        )

    def inventory(self):
        return json.loads(self.inventory_output.read_text(encoding="utf-8"))

    def inventory_fixture(
        self,
        skills,
        *,
        runtime=None,
        load_errors=None,
        inventory_hash=None,
    ):
        return {
            "schema_version": 2,
            "runtime": runtime
            or {
                "surface": "WSL",
                "codex_version": "codex-cli fixture 1.0",
                "codex_bin_sha256": "1" * 64,
                "codex_home_identity": {
                    "normalized_path_sha256": "2" * 64,
                    "filesystem_identity": {"device": 1, "inode": 2, "mode": 448},
                },
                "sqlite_home_identity": {
                    "normalized_path_sha256": "3" * 64,
                    "filesystem_identity": {"device": 1, "inode": 3, "mode": 448},
                },
            },
            "skills": copy.deepcopy(skills),
            "load_errors": copy.deepcopy(load_errors or []),
            "inventory_sha256": inventory_hash or "4" * 64,
        }

    @staticmethod
    def skill_fixture(
        name,
        *,
        description="fixture skill",
        path=None,
        scope="user",
        enabled=True,
        source_namespace=None,
        declared_name=None,
        required=False,
    ):
        resolved_declared = declared_name or name.rsplit(":", 1)[-1]
        resolved_namespace = source_namespace or (
            name.rsplit(":", 1)[0] if ":" in name else "standalone"
        )
        return {
            "name": name,
            "description": description,
            "path": path or f"/skills/{resolved_declared}/SKILL.md",
            "scope": scope,
            "enabled": enabled,
            "source_namespace": resolved_namespace,
            "declared_name": resolved_declared,
            "required": required,
        }

    def make_sqlite_home(self, name="sqlite-home", *, with_schema=True):
        sqlite_home = self.tempdir / f"{name}-{self._fixture_index}"
        self._fixture_index += 1
        sqlite_home.mkdir()
        database = sqlite_home / "state.sqlite"
        connection = sqlite3.connect(database)
        if with_schema:
            connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
            connection.execute("INSERT INTO evidence VALUES ('snapshot-only-row')")
            connection.commit()
        connection.close()
        return sqlite_home, database

    def run_query_failure(self, behavior, *, fixture_name=None):
        sequence = self._fixture_index
        self._fixture_index += 1
        codex_home = self.tempdir / f"failure-codex-home-{sequence}"
        codex_home.mkdir()
        sqlite_home, database = self.make_sqlite_home(f"failure-sqlite-home-{sequence}")
        before = (
            database.stat().st_dev,
            database.stat().st_ino,
            database.stat().st_size,
            database.stat().st_mtime_ns,
            hashlib.sha256(database.read_bytes()).hexdigest(),
        )
        payload = (
            json.loads((FIXTURES / "inventory" / fixture_name).read_text(encoding="utf-8"))
            if fixture_name
            else {"behavior": behavior}
        )
        fake_codex = self.tempdir / f"failure-codex-{sequence}"
        fake_codex.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import sys
                import time

                payload = {payload!r}
                if sys.argv[1:] == ["--version"]:
                    print("codex-cli fixture 1.0")
                    raise SystemExit(0)

                behavior = payload["behavior"]
                if behavior == "PROCESS_EXIT":
                    for line in payload["stderr"]:
                        print(line, file=sys.stderr, flush=True)
                    raise SystemExit(payload["returncode"])
                if behavior == "SQLITE_INIT_FAILED":
                    print("SQLite initialization failed: unable to open database file", file=sys.stderr, flush=True)
                    raise SystemExit(1)
                if behavior == "PROTOCOL_ERROR":
                    print(payload["stdout_line"], flush=True)
                    time.sleep(1)
                    raise SystemExit(0)
                if behavior == "BACKFILL_TIMEOUT":
                    print("session backfill started", file=sys.stderr, flush=True)
                    time.sleep(1)
                    raise SystemExit(0)
                if behavior == "PROTOCOL_TIMEOUT":
                    time.sleep(1)
                    raise SystemExit(0)
                raise SystemExit(99)
                """
            ),
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--query",
                "--cwd",
                "/fixture/project",
                "--codex-bin",
                fake_codex,
                "--codex-home",
                codex_home,
                "--codex-sqlite-home",
                sqlite_home,
                "--version-timeout",
                "0.05",
                "--initialize-timeout",
                "0.05",
                "--skills-list-timeout",
                "0.05",
                "--backfill-timeout",
                "0.05",
                "--terminate-timeout",
                "0.05",
                "--policy",
                POLICY,
                "--output",
                self.output,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        after = (
            database.stat().st_dev,
            database.stat().st_ino,
            database.stat().st_size,
            database.stat().st_mtime_ns,
            hashlib.sha256(database.read_bytes()).hexdigest(),
        )
        self.assertEqual(after, before)
        return result

    def materialized_payload_and_locator(self, fixture_name="skills-list-ok.json"):
        fixture = self.materialize_fixture(fixture_name)
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        locator = Path(payload["result"]["data"][0]["skills"][0]["path"])
        return fixture, payload, locator

    def assert_locator_protocol_failure(self, fixture):
        result = self.run_index(fixture)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("protocol_error", result.stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.inventory_output.exists())

    def make_candidate_environment(self, behavior="valid"):
        sequence = self._fixture_index
        self._fixture_index += 1
        checkout = self.tempdir / f"candidate-checkout-{sequence}"
        (checkout / "workbench").mkdir(parents=True)
        copied_script = checkout / "workbench" / "skill_inventory.py"
        shutil.copy2(SCRIPT, copied_script)

        source = checkout / "skill" / "vibe-project-lead-zh"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: vibe-project-lead-zh\ndescription: candidate fixture\n---\n",
            encoding="utf-8",
        )

        codex_home = self.tempdir / f"candidate-home-{sequence}"
        skills_root = codex_home / "skills"
        skills_root.mkdir(parents=True)
        sqlite_home, sqlite_database = self.make_sqlite_home(
            f"candidate-sqlite-home-{sequence}"
        )
        cwd = str(checkout.resolve())
        payload = self.fixture_payload("skills-list-ok.json")
        payload["result"]["data"][0]["cwd"] = cwd
        for index, item in enumerate(payload["result"]["data"][0]["skills"]):
            declared = item["name"].rsplit(":", 1)[-1]
            directory = (
                skills_root / "using-superpowers"
                if item["name"] == "using-superpowers"
                else skills_root / f"{index:02d}-{declared}"
            )
            directory.mkdir()
            locator = directory / "SKILL.md"
            locator.write_text(
                f"---\nname: {declared}\ndescription: fixture\n---\n",
                encoding="utf-8",
            )
            item["path"] = str(locator)

        target = skills_root / "vibe-project-lead-zh"
        calls_log = self.tempdir / f"candidate-calls-{sequence}.jsonl"
        bwrap_log = self.tempdir / f"candidate-bwrap-{sequence}.json"
        signal = self.tempdir / f"candidate-signal-{sequence}"
        fake_codex = self.tempdir / f"candidate-codex-{sequence}"
        fake_codex.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import copy
                import json
                import os
                import sys
                import time
                from pathlib import Path

                target = Path(os.environ["FAKE_CANDIDATE_TARGET"])
                behavior = os.environ["FAKE_CANDIDATE_BEHAVIOR"]

                if sys.argv[1:] == ["--version"]:
                    with Path(os.environ["FAKE_CANDIDATE_CALLS"]).open(
                        "a", encoding="utf-8"
                    ) as handle:
                        handle.write(json.dumps({{"kind": "version"}}) + "\\n")
                    print("codex-cli fixture 1.0")
                    raise SystemExit(0)

                mounted = (target / "SKILL.md").is_file()
                write_blocked = None
                if mounted:
                    try:
                        (target / "write-probe").write_text("unexpected", encoding="utf-8")
                    except OSError:
                        write_blocked = True
                    else:
                        write_blocked = False
                if mounted and not write_blocked:
                    raise SystemExit("candidate mount is writable")

                initialize = json.loads(sys.stdin.readline())
                print(json.dumps({{"id": initialize["id"], "result": {{"codexHome": str(Path(os.environ["CODEX_HOME"]))}}}}), flush=True)
                json.loads(sys.stdin.readline())
                request = json.loads(sys.stdin.readline())
                response = copy.deepcopy({payload!r})
                response["id"] = request["id"]
                skills = response["result"]["data"][0]["skills"]
                if mounted and behavior == "change-base":
                    skills[0]["description"] = "changed base description"
                if mounted and behavior != "missing":
                    candidate_id = "wrong-candidate-id" if behavior == "wrong-id" else "vibe-project-lead-zh"
                    candidate_path = (
                        target / "wrong.md"
                        if behavior == "wrong-path"
                        else target / "SKILL.md"
                    )
                    skills.append(
                        {{
                            "name": candidate_id,
                            "description": "candidate fixture",
                            "path": str(candidate_path),
                            "scope": "user",
                            "enabled": True,
                        }}
                    )
                print(json.dumps(response), flush=True)
                """
            ),
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        real_bwrap = shutil.which("bwrap")
        self.assertIsNotNone(real_bwrap)
        bwrap_wrapper = self.tempdir / f"candidate-bwrap-wrapper-{sequence}"
        bwrap_wrapper.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json
                import os
                import sys
                import time
                from pathlib import Path

                with open(os.environ["FAKE_BWRAP_LOG"], "w", encoding="utf-8") as handle:
                    json.dump(sys.argv[1:], handle)
                calls = Path(os.environ["FAKE_CANDIDATE_CALLS"])
                args = sys.argv[1:]
                separator = args.index("--")
                app_argv = args[separator + 2:]
                candidate_target = str(Path(os.environ["FAKE_CANDIDATE_TARGET"]))
                mounted = any(
                    args[index:index + 3] == [
                        "--ro-bind",
                        str(Path(os.environ["FAKE_CANDIDATE_SOURCE"])),
                        candidate_target,
                    ]
                    for index in range(separator - 2)
                )
                with calls.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({{
                        "kind": "app-server",
                        "argv": app_argv,
                        "mounted": mounted,
                        "write_blocked": mounted,
                    }}) + "\\n")
                behavior = os.environ["FAKE_CANDIDATE_BEHAVIOR"]
                if mounted and behavior == "mutate-source":
                    (Path(os.environ["FAKE_CANDIDATE_SOURCE"]) / "SKILL.md").write_text(
                        "---\\nname: vibe-project-lead-zh\\ndescription: mutated\\n---\\n",
                        encoding="utf-8",
                    )
                if mounted and behavior == "signal-host-target":
                    Path(os.environ["FAKE_CANDIDATE_SIGNAL"]).write_text("ready", encoding="utf-8")
                    time.sleep(0.3)
                os.execv({real_bwrap!r}, [{real_bwrap!r}, *sys.argv[1:]])
                """
            ),
            encoding="utf-8",
        )
        bwrap_wrapper.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "FAKE_CANDIDATE_CALLS": str(calls_log),
                "FAKE_CANDIDATE_TARGET": str(target),
                "FAKE_CANDIDATE_SOURCE": str(source),
                "FAKE_CANDIDATE_BEHAVIOR": behavior,
                "FAKE_CANDIDATE_SIGNAL": str(signal),
                "FAKE_BWRAP_LOG": str(bwrap_log),
            }
        )
        return {
            "checkout": checkout,
            "script": copied_script,
            "source": source,
            "codex_home": codex_home,
            "sqlite_home": sqlite_home,
            "sqlite_database": sqlite_database,
            "skills_root": skills_root,
            "target": target,
            "cwd": cwd,
            "fake_codex": fake_codex,
            "bwrap": bwrap_wrapper,
            "calls_log": calls_log,
            "bwrap_log": bwrap_log,
            "signal": signal,
            "env": env,
        }

    def candidate_command(self, setup, *, candidate_id="vibe-project-lead-zh", source=None, target=None):
        return [
            sys.executable,
            setup["script"],
            "--query",
            "--cwd",
            setup["cwd"],
            "--codex-bin",
            setup["fake_codex"],
            "--codex-home",
            setup["codex_home"],
            "--codex-sqlite-home",
            setup["sqlite_home"],
            "--bwrap-bin",
            setup["bwrap"],
            "--candidate-id",
            candidate_id,
            "--candidate-source",
            source or setup["source"],
            "--candidate-target",
            target or setup["target"],
            "--policy",
            POLICY,
            "--output",
            self.output,
            "--inventory-output",
            self.inventory_output,
        ]

    def run_candidate(self, setup, **overrides):
        return subprocess.run(
            self.candidate_command(setup, **overrides),
            check=False,
            capture_output=True,
            text=True,
            env=setup["env"],
        )

    def test_index_uses_runtime_inventory_and_stable_group_order(self):
        result = self.run_index("skills-list-ok.json")

        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("Superpowers 主流程", text)
        self.assertIn("共发现 9 个 Skill", text)
        self.assertLess(text.index("需求与规划"), text.index("开发与测试"))
        router_rows = [
            line
            for line in text.splitlines()
            if "| `using-superpowers` | `standalone` | `using-superpowers` |" in line
        ]
        self.assertEqual(len(router_rows), 1)

        inventory = json.loads(self.inventory_output.read_text(encoding="utf-8"))
        self.assertEqual(inventory["summary"]["total"], 9)
        self.assertEqual(inventory["summary"]["drift_count"], 0)
        self.assertEqual(
            [item["name"] for item in inventory["skills"]],
            sorted(item["name"] for item in inventory["skills"]),
        )

    def test_inventory_schema_two_contains_runtime_and_home_identity(self):
        result = self.run_index("skills-list-ok.json")

        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = self.inventory()
        self.assertEqual(inventory["schema_version"], 2)
        self.assertEqual(
            set(inventory["runtime"]),
            {
                "surface",
                "codex_version",
                "codex_bin_sha256",
                "codex_home_identity",
                "sqlite_home_identity",
            },
        )
        self.assertEqual(
            inventory["runtime"]["codex_home_identity"]["state"],
            "NOT_CAPTURED_INPUT_MODE",
        )
        self.assertEqual(
            inventory["runtime"]["sqlite_home_identity"]["state"],
            "NOT_CAPTURED_INPUT_MODE",
        )
        self.assertEqual(inventory["runtime"]["surface"], "OFFLINE_FIXTURE")
        self.assertIsNone(inventory["runtime"]["codex_bin_sha256"])
        self.assertEqual(inventory["query"]["isolation"]["state"], "OFFLINE_FIXTURE")
        self.assertEqual(inventory["baseline_inventory_sha256"], None)
        self.assertEqual(inventory["diff"], [])
        router = next(
            item for item in inventory["skills"] if item["name"] == "using-superpowers"
        )
        self.assertTrue(router["required"])

    def test_reordering_skills_and_errors_keeps_inventory_hash_stable(self):
        payload = self.fixture_payload("skills-list-drift.json")
        payload["result"]["data"][0]["errors"].append(
            {"path": "/skills/another/SKILL.md", "message": "another error"}
        )
        fixture = self.materialize_payload(payload, "ordered.json")
        first = self.run_index(fixture)
        self.assertEqual(first.returncode, 3, first.stderr)
        first_hash = self.inventory()["inventory_sha256"]

        materialized = json.loads(fixture.read_text(encoding="utf-8"))
        materialized["result"]["data"][0]["skills"].reverse()
        materialized["result"]["data"][0]["errors"].reverse()
        fixture.write_text(json.dumps(materialized), encoding="utf-8")
        second = self.run_index(fixture)

        self.assertEqual(second.returncode, 3, second.stderr)
        self.assertEqual(self.inventory()["inventory_sha256"], first_hash)

    def test_generation_time_warning_count_and_temp_path_do_not_change_hash(self):
        module = self.load_module()
        normalized = {
            "cwd": "/fixture/project",
            "skills": [
                {
                    "name": "using-superpowers",
                    "description": "fixture",
                    "path": "/skills/using-superpowers/SKILL.md",
                    "scope": "user",
                    "enabled": True,
                    "source_namespace": "standalone",
                    "declared_name": "using-superpowers",
                }
            ],
            "errors": [],
            "generated_at_utc": "first",
            "query": {
                "warning_count": 1,
                "warning_classes": ["OTHER_QUERY_WARNING"],
                "warning_signatures": [
                    {"sha256": "5" * 64, "occurrences": 1, "temporary_path": "/tmp/one"}
                ],
            },
        }
        first = module.inventory_sha256(normalized)
        normalized["generated_at_utc"] = "second"
        normalized["query"] = {
            "warning_count": 9,
            "warning_classes": ["NETWORK_BLOCK_EXPECTED", "OTHER_QUERY_WARNING"],
            "warning_signatures": [
                {"sha256": "6" * 64, "occurrences": 9, "temporary_path": "/tmp/two"}
            ],
        }

        self.assertEqual(module.inventory_sha256(normalized), first)

    def test_inventory_hash_includes_namespace_declared_name_and_metadata(self):
        module = self.load_module()
        base = {
            "cwd": "/fixture/project",
            "skills": [self.skill_fixture("plugin:fixture-skill")],
            "errors": [],
        }
        original = module.inventory_sha256(base)

        for field, changed_value in (
            ("source_namespace", "other-plugin"),
            ("declared_name", "renamed-skill"),
            ("description", "changed semantic metadata"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(base)
                changed["skills"][0][field] = changed_value
                self.assertNotEqual(module.inventory_sha256(changed), original)

    def test_each_raw_discovery_field_changes_inventory_hash(self):
        module = self.load_module()
        base = {
            "cwd": "/fixture/project",
            "skills": [
                {
                    "name": "using-superpowers",
                    "description": "fixture",
                    "path": "/skills/using-superpowers/SKILL.md",
                    "scope": "user",
                    "enabled": True,
                    "source_namespace": "standalone",
                    "declared_name": "using-superpowers",
                }
            ],
            "errors": [],
        }
        original = module.inventory_sha256(base)
        changes = {
            "description": "changed",
            "path": "/other/SKILL.md",
            "scope": "repo",
            "enabled": False,
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                changed = copy.deepcopy(base)
                changed["skills"][0][key] = value
                self.assertNotEqual(module.inventory_sha256(changed), original)

    def test_warning_classes_are_normalized_and_unknown_warning_is_visible(self):
        module = self.load_module()
        fixture = self.fixture_payload("inventory/skills-list-warning-classes.json")

        normalized = module.normalize_query_warnings(
            [item["raw"] for item in fixture["warnings"]]
        )

        self.assertEqual(
            set(normalized["warning_classes"]),
            {item["expected_class"] for item in fixture["warnings"]},
        )
        self.assertEqual(normalized["status"], "WARNING")
        unknown = next(
            item
            for item in normalized["warning_signatures"]
            if item["class"] == "OTHER_QUERY_WARNING"
        )
        self.assertRegex(unknown["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("sample", unknown)
        self.assertNotIn(
            "unclassified fixture warning",
            json.dumps(normalized),
        )

    def test_added_disabled_renamed_duplicate_locator_and_load_error_diffs_are_typed(self):
        module = self.load_module()
        baseline_skills = [
            self.skill_fixture("removed-skill"),
            self.skill_fixture("required-skill", required=True),
            self.skill_fixture("toggle-skill"),
            self.skill_fixture("enable-skill", enabled=False),
            self.skill_fixture("duplicate-skill"),
            self.skill_fixture("locator-skill"),
            self.skill_fixture("old-plugin:namespace-skill"),
            self.skill_fixture("metadata-skill"),
        ]
        current_skills = [
            self.skill_fixture("added-skill"),
            self.skill_fixture("toggle-skill", enabled=False),
            self.skill_fixture("enable-skill", enabled=True),
            self.skill_fixture("duplicate-skill"),
            self.skill_fixture(
                "duplicate-skill",
                path="/other/duplicate-skill/SKILL.md",
                scope="repo",
            ),
            self.skill_fixture(
                "locator-skill",
                path="/plugins/cache/2.0/locator-skill/SKILL.md",
            ),
            self.skill_fixture("new-plugin:namespace-skill"),
            self.skill_fixture("metadata-skill", description="changed metadata"),
        ]
        baseline = self.inventory_fixture(baseline_skills)
        current_runtime = copy.deepcopy(baseline["runtime"])
        current_runtime["codex_version"] = "codex-cli fixture 2.0"
        current_runtime["codex_home_identity"] = {
            "normalized_path_sha256": "9" * 64,
            "filesystem_identity": {"device": 9, "inode": 9, "mode": 448},
        }
        current = self.inventory_fixture(
            current_skills,
            runtime=current_runtime,
            load_errors=[{"path": "/skills/broken/SKILL.md", "message": "bad metadata"}],
            inventory_hash="8" * 64,
        )

        comparison = module.compare_inventory(baseline, current)

        self.assertEqual(comparison["supervisor_state"], "BLOCKED")
        self.assertEqual(
            {item["type"] for item in comparison["diff"]},
            {
                "SKILL_ADDED",
                "SKILL_REMOVED",
                "SKILL_ENABLED",
                "SKILL_DISABLED",
                "SKILL_RENAMED_OR_MISSING",
                "SKILL_DUPLICATE",
                "SOURCE_NAMESPACE_CHANGED",
                "LOCATOR_CHANGED",
                "METADATA_CHANGED",
                "LOAD_ERROR_CHANGED",
                "RUNTIME_CHANGED",
                "HOME_IDENTITY_CHANGED",
            },
        )
        self.assertEqual(
            [
                (item["type"], item["skill"])
                for item in comparison["diff"]
            ],
            sorted(
                (item["type"], item["skill"])
                for item in comparison["diff"]
            ),
        )
        self.assertTrue(
            all(
                item["impact"]
                in {
                    "BLOCKING_SUPERVISOR",
                    "BLOCKING_ROUTE",
                    "NON_BLOCKING_DRIFT",
                    "INFORMATIONAL",
                }
                for item in comparison["diff"]
            )
        )

    def test_supervisor_drift_is_blocking_without_blocking_unrelated_routes(self):
        module = self.load_module()
        baseline = self.inventory_fixture(
            [
                self.skill_fixture("vibe-project-lead-zh"),
                self.skill_fixture("route-a"),
                self.skill_fixture("route-b"),
                self.skill_fixture("route-c"),
                self.skill_fixture("route-c", path="/duplicate/route-c/SKILL.md"),
                self.skill_fixture("route-d"),
            ]
        )
        current = self.inventory_fixture(
            [
                self.skill_fixture("vibe-project-lead-zh", enabled=False),
                self.skill_fixture("route-a", path="/upgraded/route-a/SKILL.md"),
                self.skill_fixture("route-b"),
                self.skill_fixture("route-c"),
                self.skill_fixture("route-c", path="/duplicate/route-c/SKILL.md"),
                self.skill_fixture("route-d"),
            ],
            load_errors=[
                {"path": "/skills/route-d/SKILL.md", "message": "invalid frontmatter"}
            ],
            inventory_hash="7" * 64,
        )

        comparison = module.compare_inventory(baseline, current)

        self.assertEqual(comparison["supervisor_state"], "BLOCKED")
        self.assertEqual(
            comparison["route_states"],
            {
                "route-a": "BLOCKED",
                "route-b": "READY",
                "route-c": "BLOCKED",
                "route-d": "BLOCKED",
                "vibe-project-lead-zh": "BLOCKED",
            },
        )
        impacts = {
            (item["type"], item["skill"]): item["impact"]
            for item in comparison["diff"]
        }
        self.assertEqual(
            impacts[("SKILL_DISABLED", "vibe-project-lead-zh")],
            "BLOCKING_SUPERVISOR",
        )
        self.assertEqual(
            impacts[("LOCATOR_CHANGED", "route-a")],
            "BLOCKING_ROUTE",
        )

    def test_unrelated_locator_drift_remains_route_local(self):
        module = self.load_module()
        supervisor = self.skill_fixture("vibe-project-lead-zh")
        routed = self.skill_fixture("route-a")
        baseline = self.inventory_fixture([supervisor, routed])
        current = self.inventory_fixture(
            [
                copy.deepcopy(supervisor),
                self.skill_fixture(
                    "route-a",
                    path="/upgraded/route-a/SKILL.md",
                ),
            ],
            inventory_hash="9" * 64,
        )

        comparison = module.compare_inventory(baseline, current)

        self.assertEqual(comparison["supervisor_state"], "READY")
        self.assertEqual(comparison["route_states"]["vibe-project-lead-zh"], "READY")
        self.assertEqual(comparison["route_states"]["route-a"], "BLOCKED")
        self.assertEqual(
            [
                (item["type"], item["skill"], item["impact"])
                for item in comparison["diff"]
            ],
            [("LOCATOR_CHANGED", "route-a", "BLOCKING_ROUTE")],
        )

    def test_new_disabled_and_duplicate_skills_are_not_route_ready(self):
        module = self.load_module()
        baseline = self.inventory_fixture(
            [
                self.skill_fixture("stable-route"),
                self.skill_fixture("disabled-stays-disabled", enabled=False),
                self.skill_fixture("load-error-stays"),
            ]
            ,
            load_errors=[
                {
                    "path": "/skills/load-error-stays/SKILL.md",
                    "message": "invalid frontmatter",
                }
            ],
        )
        current = self.inventory_fixture(
            [
                self.skill_fixture("stable-route"),
                self.skill_fixture("disabled-stays-disabled", enabled=False),
                self.skill_fixture("load-error-stays"),
                self.skill_fixture("new-disabled", enabled=False),
                self.skill_fixture("new-duplicate"),
                self.skill_fixture(
                    "new-duplicate",
                    path="/other/new-duplicate/SKILL.md",
                    scope="repo",
                ),
            ],
            load_errors=[
                {
                    "path": "/skills/load-error-stays/SKILL.md",
                    "message": "invalid frontmatter",
                }
            ],
            inventory_hash="6" * 64,
        )

        comparison = module.compare_inventory(baseline, current)

        self.assertEqual(comparison["route_states"]["stable-route"], "READY")
        self.assertEqual(
            comparison["route_states"]["disabled-stays-disabled"],
            "BLOCKED",
        )
        self.assertEqual(
            comparison["route_states"]["load-error-stays"],
            "BLOCKED",
        )
        self.assertEqual(comparison["route_states"]["new-disabled"], "BLOCKED")
        self.assertEqual(comparison["route_states"]["new-duplicate"], "BLOCKED")
        typed = {(item["type"], item["skill"]) for item in comparison["diff"]}
        self.assertIn(("SKILL_DISABLED", "new-disabled"), typed)
        self.assertIn(("SKILL_DUPLICATE", "new-duplicate"), typed)

    def test_static_mapping_never_receives_dynamic_locator(self):
        with POLICY.open("rb") as handle:
            policy = __import__("tomllib").load(handle)

        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key.casefold(), {"path", "locator", "source_locator"})
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str):
                self.assertFalse(value.startswith(("/", "\\\\", "C:\\")))

        visit(policy)
        result = self.run_index("skills-list-ok.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.output.read_text(encoding="utf-8")
        self.assertLess(text.index("## 稳定中文映射"), text.index("## 本次动态发现"))
        self.assertIn(self.inventory()["skills"][0]["path"], text[text.index("## 本次动态发现") :])

    def test_declared_names_and_namespaces_come_from_exact_locators(self):
        result = self.run_index("skills-list-ok.json")

        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = self.inventory()
        standalone = next(item for item in inventory["skills"] if ":" not in item["name"])
        namespaced = next(item for item in inventory["skills"] if ":" in item["name"])
        self.assertEqual(standalone["source_namespace"], "standalone")
        self.assertEqual(standalone["declared_name"], standalone["name"])
        self.assertEqual(namespaced["source_namespace"], namespaced["name"].rsplit(":", 1)[0])
        self.assertEqual(namespaced["declared_name"], namespaced["name"].rsplit(":", 1)[1])
        self.assertTrue(Path(standalone["path"]).is_file())
        self.assertTrue(Path(namespaced["path"]).is_file())

    def test_supported_yaml_name_scalars_normalize_to_same_value(self):
        for raw in ("using-superpowers", "'using-superpowers'", '"using-superpowers"'):
            with self.subTest(raw=raw):
                fixture = self.materialize_fixture("skills-list-ok.json")
                payload = json.loads(fixture.read_text(encoding="utf-8"))
                item = next(
                    item
                    for item in payload["result"]["data"][0]["skills"]
                    if item["name"] == "using-superpowers"
                )
                Path(item["path"]).write_text(
                    f"---\nname: {raw}\ndescription: fixture\n---\n",
                    encoding="utf-8",
                )
                fixture.write_text(json.dumps(payload), encoding="utf-8")

                result = self.run_index(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                discovered = next(
                    item for item in self.inventory()["skills"] if item["name"] == "using-superpowers"
                )
                self.assertEqual(discovered["declared_name"], "using-superpowers")

    def test_invalid_yaml_name_scalar_forms_are_rejected(self):
        module = self.load_module()

        for raw in (
            "{name: using-superpowers}",
            "[using-superpowers]",
            "|",
            ">",
            "using-superpowers # comment",
            '"using-superpowers" trailing',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(module.ProtocolError):
                    module.decode_declared_name(raw)

    def test_index_renders_complete_provenance_summary_and_locator(self):
        result = self.run_index("skills-list-ok.json")

        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = self.inventory()
        text = self.output.read_text(encoding="utf-8")
        for field in (
            "schema_version",
            "generated_at_utc",
            "cwd",
            "codex_version",
            "codex_bin",
            "inventory_sha256",
            "total",
            "enabled",
            "disabled",
            "duplicate",
            "unmapped",
            "missing_or_renamed",
            "load_errors",
            "query_warnings",
        ):
            self.assertIn(field, text)
        self.assertIn(
            "| 中文名称 | discovery ID | source namespace | declared name | 适用阶段 | 用途 | 状态 | scope | 映射 | source locator |",
            text,
        )
        self.assertIn("## 分类导航", text)
        self.assertIn("inventory cwd", text)
        self.assertIn("不同 cwd 的完整哈希不可直接比较", text)
        self.assertIn("逐 locator 校验", text)
        self.assertIn("UNINDEXED_PROJECT_SKILL", text)
        self.assertIn(inventory["skills"][0]["path"], text)

    def test_same_input_is_deterministic_except_generation_time(self):
        fixture = self.materialize_fixture("skills-list-ok.json")
        first = self.run_index(fixture)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_text = self.output.read_text(encoding="utf-8")
        first_hash = self.inventory()["inventory_sha256"]

        second_output = self.tempdir / "second-index.md"
        second_inventory = self.tempdir / "second-inventory.json"
        second = self.run_index(
            fixture,
            "--output",
            second_output,
            "--inventory-output",
            second_inventory,
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        second_text = second_output.read_text(encoding="utf-8")
        strip_time = lambda text: "\n".join(
            line for line in text.splitlines() if "generated_at_utc" not in line
        )
        self.assertEqual(strip_time(first_text), strip_time(second_text))
        self.assertEqual(
            json.loads(second_inventory.read_text(encoding="utf-8"))["inventory_sha256"],
            first_hash,
        )

    def test_drift_is_visible_and_returns_warning_status(self):
        result = self.run_index("skills-list-drift.json")

        self.assertEqual(result.returncode, 3, result.stderr)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("未映射", text)
        self.assertIn("已禁用", text)
        self.assertIn("重名", text)
        self.assertIn("invalid frontmatter", text)
        inventory = json.loads(self.inventory_output.read_text(encoding="utf-8"))
        drift_types = {item["type"] for item in inventory["drift"]}
        self.assertTrue({"duplicate", "disabled", "unmapped", "load_error"} <= drift_types)

    def test_missing_locator_fails_without_same_name_fallback(self):
        fixture, payload, locator = self.materialized_payload_and_locator()
        locator.unlink()
        fallback = self.tempdir / "fallback" / locator.parent.name
        fallback.mkdir(parents=True)
        (fallback / "SKILL.md").write_text(
            "---\nname: verification-before-completion\ndescription: fallback\n---\n",
            encoding="utf-8",
        )
        fixture.write_text(json.dumps(payload), encoding="utf-8")

        self.assert_locator_protocol_failure(fixture)

    def test_symlink_locator_is_rejected(self):
        fixture, payload, locator = self.materialized_payload_and_locator()
        target = locator.with_name("real-SKILL.md")
        locator.replace(target)
        locator.symlink_to(target.name)
        fixture.write_text(json.dumps(payload), encoding="utf-8")

        self.assert_locator_protocol_failure(fixture)

    def test_non_regular_locator_is_rejected_without_blocking(self):
        fixture, payload, _ = self.materialized_payload_and_locator()
        with tempfile.TemporaryDirectory(dir="/tmp") as fifo_root:
            locator = Path(fifo_root) / "SKILL.md"
            os.mkfifo(locator)
            payload["result"]["data"][0]["skills"][0]["path"] = str(locator)
            fixture.write_text(json.dumps(payload), encoding="utf-8")

            self.assert_locator_protocol_failure(fixture)

    def test_oversized_locator_is_rejected(self):
        fixture, payload, locator = self.materialized_payload_and_locator()
        locator.write_bytes(b"x" * (1024 * 1024 + 1))
        fixture.write_text(json.dumps(payload), encoding="utf-8")

        self.assert_locator_protocol_failure(fixture)

    def test_non_utf8_locator_is_rejected(self):
        fixture, payload, locator = self.materialized_payload_and_locator()
        locator.write_bytes(b"---\nname: \xff\n---\n")
        fixture.write_text(json.dumps(payload), encoding="utf-8")

        self.assert_locator_protocol_failure(fixture)

    def test_unnamespaced_discovery_id_must_match_declared_name(self):
        fixture, payload, locator = self.materialized_payload_and_locator()
        locator.write_text(
            "---\nname: wrong-declared-name\ndescription: fixture\n---\n",
            encoding="utf-8",
        )
        fixture.write_text(json.dumps(payload), encoding="utf-8")

        self.assert_locator_protocol_failure(fixture)

    def test_namespaced_discovery_suffix_must_match_declared_name(self):
        fixture = self.materialize_fixture("skills-list-ok.json")
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        item = next(
            item for item in payload["result"]["data"][0]["skills"] if ":" in item["name"]
        )
        Path(item["path"]).write_text(
            "---\nname: wrong-declared-name\ndescription: fixture\n---\n",
            encoding="utf-8",
        )
        fixture.write_text(json.dumps(payload), encoding="utf-8")

        self.assert_locator_protocol_failure(fixture)

    def test_locator_change_during_read_is_rejected(self):
        module = self.load_module()
        locator = self.tempdir / "changing" / "SKILL.md"
        locator.parent.mkdir()
        locator.write_text(
            "---\nname: changing-skill\ndescription: fixture\n---\n",
            encoding="utf-8",
        )
        real_read = module.os.read
        changed = False

        def changing_read(descriptor, size):
            nonlocal changed
            chunk = real_read(descriptor, size)
            if not changed:
                changed = True
                locator.write_text(
                    "---\nname: changed-skill\ndescription: changed and longer\n---\n",
                    encoding="utf-8",
                )
            return chunk

        with mock.patch.object(module.os, "read", side_effect=changing_read):
            with self.assertRaisesRegex(module.ProtocolError, "changed during read"):
                module.read_declared_name(str(locator))

    def test_missing_required_skill_is_reported_as_missing_or_renamed(self):
        policy = self.tempdir / "policy.toml"
        policy.write_text(
            POLICY.read_text(encoding="utf-8")
            + '\n[required_for_test]\nids = ["renamed-required-skill"]\n',
            encoding="utf-8",
        )

        result = self.run_index("skills-list-ok.json", policy=policy)

        self.assertEqual(result.returncode, 3, result.stderr)
        drift = json.loads(self.inventory_output.read_text(encoding="utf-8"))["drift"]
        self.assertIn("missing_or_renamed", {item["type"] for item in drift})

    def test_invalid_response_is_protocol_failure(self):
        invalid = self.tempdir / "invalid.json"
        invalid.write_text('{"id": 2, "result": {"data": []}}', encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--input",
                invalid,
                "--codex-version",
                "fixture-codex-1.0",
                "--codex-bin",
                "/fixture/bin/codex",
                "--policy",
                POLICY,
                "--output",
                self.output,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("protocol_error", result.stdout)
        self.assertFalse(self.output.exists())

    def test_codex_home_and_sqlite_home_are_captured_separately(self):
        module = self.load_module()
        codex_home = self.tempdir / "separate-codex-home"
        codex_home.mkdir()
        sqlite_home, _ = self.make_sqlite_home("separate-sqlite-home")

        self.assertTrue(
            hasattr(module, "capture_query_roots"),
            "capture_query_roots is not implemented",
        )
        roots = module.capture_query_roots(codex_home, sqlite_home)

        self.assertNotEqual(
            roots["codex_home"]["normalized_path_sha256"],
            roots["sqlite_home"]["normalized_path_sha256"],
        )
        self.assertNotEqual(
            roots["codex_home"]["filesystem_identity"],
            roots["sqlite_home"]["filesystem_identity"],
        )
        self.assertNotIn(str(codex_home), json.dumps(roots))
        self.assertNotIn(str(sqlite_home), json.dumps(roots))

    def test_sqlite_snapshot_uses_backup_api_and_writes_only_temp_root(self):
        module = self.load_module()
        sqlite_home = self.tempdir / "wal-sqlite-home"
        sqlite_home.mkdir()
        database = sqlite_home / "state.sqlite"
        writer = sqlite3.connect(database)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        writer.execute("INSERT INTO evidence VALUES ('visible-through-backup')")
        writer.commit()
        destination = self.tempdir / "snapshot-destination"
        destination.mkdir(mode=0o700)
        source_names = sorted(path.name for path in sqlite_home.iterdir())

        self.assertTrue(
            hasattr(module, "snapshot_sqlite_home"),
            "snapshot_sqlite_home is not implemented",
        )
        receipt = module.snapshot_sqlite_home(sqlite_home, destination)

        snapshot = Path(receipt["destination_path"])
        self.assertTrue(snapshot.is_relative_to(destination))
        self.assertEqual(receipt["state"], "SNAPSHOT_READY")
        with closing(sqlite3.connect(snapshot)) as copied:
            value = copied.execute("SELECT value FROM evidence").fetchone()[0]
        self.assertEqual(value, "visible-through-backup")
        self.assertEqual(sorted(path.name for path in sqlite_home.iterdir()), source_names)
        self.assertNotIn("visible-through-backup", json.dumps(receipt))

    def test_real_sqlite_identity_is_unchanged_after_snapshot(self):
        module = self.load_module()
        sqlite_home, database = self.make_sqlite_home("unchanged-source")
        destination = self.tempdir / "unchanged-destination"
        destination.mkdir(mode=0o700)
        before = (
            database.stat().st_dev,
            database.stat().st_ino,
            database.stat().st_size,
            database.stat().st_mtime_ns,
            hashlib.sha256(database.read_bytes()).hexdigest(),
        )

        receipt = module.snapshot_sqlite_home(sqlite_home, destination)
        after = (
            database.stat().st_dev,
            database.stat().st_ino,
            database.stat().st_size,
            database.stat().st_mtime_ns,
            hashlib.sha256(database.read_bytes()).hexdigest(),
        )

        self.assertEqual(after, before)
        self.assertEqual(receipt["source_content_digest"], before[-1])

    def test_empty_snapshot_is_not_used_as_backfill_substitute(self):
        module = self.load_module()
        sqlite_home, _ = self.make_sqlite_home("empty-source", with_schema=False)
        destination = self.tempdir / "empty-destination"
        destination.mkdir(mode=0o700)

        with self.assertRaises(module.ProtocolError) as raised:
            module.snapshot_sqlite_home(sqlite_home, destination)

        self.assertEqual(raised.exception.code, "SQLITE_SNAPSHOT_FAILED")
        self.assertEqual(list(destination.iterdir()), [])

    def test_candidate_target_never_appears_on_host(self):
        module = self.load_module()
        source = self.tempdir / "candidate-source-contract"
        target = self.tempdir / "candidate-target-contract"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: vibe-project-lead-zh\n---\n",
            encoding="utf-8",
        )
        candidate = module.CandidateMount(
            skill_id="vibe-project-lead-zh",
            source_dir=source,
            target_dir=target,
        )

        module.ensure_host_target_absent(candidate)
        target.mkdir()
        with self.assertRaises(module.ProtocolError):
            module.ensure_host_target_absent(candidate)

    def test_network_isolator_absence_is_isolation_unavailable(self):
        module = self.load_module()

        with self.assertRaises(module.ProtocolError) as raised:
            module.resolve_query_executables("/bin/true", str(self.tempdir / "missing"))

        self.assertEqual(raised.exception.code, "ISOLATION_UNAVAILABLE")

    def test_remote_plugin_and_multi_agent_are_disabled_in_argv(self):
        module = self.load_module()
        codex_home = self.tempdir / "argv-codex-home"
        snapshot_home = self.tempdir / "argv-snapshot-home"
        codex_home.mkdir()
        snapshot_home.mkdir()

        self.assertTrue(
            hasattr(module, "build_isolated_query_command"),
            "build_isolated_query_command is not implemented",
        )
        command = module.build_isolated_query_command(
            "/bin/codex",
            "/bin/bwrap",
            codex_home,
            snapshot_home,
        )

        self.assertIn(["--disable", "remote_plugin"], [command[i : i + 2] for i in range(len(command) - 1)])
        self.assertIn(["--disable", "multi_agent"], [command[i : i + 2] for i in range(len(command) - 1)])
        self.assertIn(["--ro-bind", "/", "/"], [command[i : i + 3] for i in range(len(command) - 2)])
        self.assertIn(["--tmpfs", "/tmp"], [command[i : i + 2] for i in range(len(command) - 1)])
        sqlite_setenv = command.index("CODEX_SQLITE_HOME")
        codex_setenv = command.index("CODEX_HOME")
        self.assertNotEqual(command[sqlite_setenv + 1], command[codex_setenv + 1])

    def test_query_deadlines_are_stage_specific(self):
        module = self.load_module()

        self.assertEqual(
            module.QueryDeadlines(),
            module.QueryDeadlines(
                version_seconds=5.0,
                initialize_seconds=5.0,
                skills_list_seconds=10.0,
                backfill_seconds=3.0,
                terminate_seconds=2.0,
            ),
        )

    def test_process_exit_is_distinct_and_diagnostics_are_sanitized(self):
        result = self.run_query_failure(
            "PROCESS_EXIT",
            fixture_name="skills-list-process-exit.json",
        )

        self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["code"], "PROCESS_EXIT")
        self.assertRegex(payload["warning_signature"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertLessEqual(payload["warning_signature"]["line_count"], 40)
        self.assertNotIn("/private/user", result.stdout)
        self.assertNotIn("fixture-secret-must-not-escape", result.stdout)
        self.assertFalse(self.output.exists())

    def test_sqlite_initialization_failure_is_distinct(self):
        result = self.run_query_failure("SQLITE_INIT_FAILED")

        self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout)["code"], "SQLITE_INIT_FAILED")
        self.assertFalse(self.output.exists())

    def test_observed_backfill_uses_its_own_deadline(self):
        result = self.run_query_failure("BACKFILL_TIMEOUT")

        self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout)["code"], "BACKFILL_TIMEOUT")
        self.assertFalse(self.output.exists())

    def test_live_process_protocol_deadline_is_distinct(self):
        result = self.run_query_failure("PROTOCOL_TIMEOUT")

        self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout)["code"], "PROTOCOL_TIMEOUT")
        self.assertFalse(self.output.exists())

    def test_malformed_json_is_protocol_error(self):
        result = self.run_query_failure(
            "PROTOCOL_ERROR",
            fixture_name="skills-list-protocol-error.json",
        )

        self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout)["code"], "PROTOCOL_ERROR")
        self.assertFalse(self.output.exists())

    def test_query_disables_remote_plugins_and_multi_agent(self):
        bwrap_log = self.tempdir / "query-bwrap.json"
        fake_codex_home = self.tempdir / "codex-home"
        fake_codex_home.mkdir()
        fake_sqlite_home, fake_sqlite_database = self.make_sqlite_home(
            "query-sqlite-home"
        )
        sqlite_before = hashlib.sha256(fake_sqlite_database.read_bytes()).hexdigest()
        fake_codex = self.tempdir / "codex"
        fixture = json.loads(
            self.materialize_fixture("skills-list-ok.json").read_text(encoding="utf-8")
        )
        fake_codex.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json
                import os
                import socket
                import sys
                from pathlib import Path

                if sys.argv[1:] == ["--version"]:
                    print("codex-cli fixture 1.0")
                    raise SystemExit(0)

                if sys.argv[1:3] != ["app-server", "--stdio"]:
                    raise SystemExit("wrong app-server argv")
                for required in (("--disable", "remote_plugin"), ("--disable", "multi_agent")):
                    if not any(sys.argv[index:index + 2] == list(required) for index in range(len(sys.argv) - 1)):
                        raise SystemExit("missing disabled feature")
                if {{name for _, name in socket.if_nameindex()}} - {{"lo"}}:
                    raise SystemExit("network namespace exposes non-loopback interface")
                (Path(os.environ["CODEX_HOME"]) / "query-write-probe").write_text("temporary", encoding="utf-8")
                (Path(os.environ["CODEX_SQLITE_HOME"]) / "query-sqlite-write-probe").write_text("temporary", encoding="utf-8")

                initialize = json.loads(sys.stdin.readline())
                print(json.dumps({{"id": initialize["id"], "result": {{"codexHome": "/tmp/codex"}}}}), flush=True)
                json.loads(sys.stdin.readline())
                request = json.loads(sys.stdin.readline())
                response = {fixture!r}
                response["id"] = request["id"]
                print(json.dumps(response), flush=True)
                """
            ),
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        real_bwrap = shutil.which("bwrap")
        self.assertIsNotNone(real_bwrap)
        bwrap_wrapper = self.tempdir / "query-bwrap-wrapper"
        bwrap_wrapper.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json
                import os
                import sys

                with open(os.environ["FAKE_QUERY_BWRAP_LOG"], "w", encoding="utf-8") as handle:
                    json.dump(sys.argv[1:], handle)
                os.execv({real_bwrap!r}, [{real_bwrap!r}, *sys.argv[1:]])
                """
            ),
            encoding="utf-8",
        )
        bwrap_wrapper.chmod(0o755)
        env = os.environ.copy()
        env["FAKE_QUERY_BWRAP_LOG"] = str(bwrap_log)

        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--query",
                "--cwd",
                "/fixture/project",
                "--codex-bin",
                fake_codex,
                "--codex-home",
                fake_codex_home,
                "--codex-sqlite-home",
                fake_sqlite_home,
                "--bwrap-bin",
                bwrap_wrapper,
                "--policy",
                POLICY,
                "--output",
                self.output,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bwrap_args = json.loads(bwrap_log.read_text(encoding="utf-8"))
        self.assertIn(["--ro-bind", "/", "/"], [bwrap_args[i : i + 3] for i in range(len(bwrap_args) - 2)])
        self.assertIn(["--tmpfs", "/tmp"], [bwrap_args[i : i + 2] for i in range(len(bwrap_args) - 1)])
        separator = bwrap_args.index("--")
        app_args = bwrap_args[separator + 2 :]
        self.assertEqual(app_args[:2], ["app-server", "--stdio"])
        self.assertIn(["--disable", "remote_plugin"], [app_args[i : i + 2] for i in range(len(app_args) - 1)])
        self.assertIn(["--disable", "multi_agent"], [app_args[i : i + 2] for i in range(len(app_args) - 1)])
        self.assertFalse((fake_codex_home / "query-write-probe").exists())
        self.assertFalse((fake_sqlite_home / "query-sqlite-write-probe").exists())
        self.assertEqual(
            hashlib.sha256(fake_sqlite_database.read_bytes()).hexdigest(),
            sqlite_before,
        )

    def test_candidate_mount_is_read_only_and_preserves_future_locator(self):
        setup = self.make_candidate_environment()

        result = self.run_candidate(setup)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertFalse(setup["target"].exists())
        inventory = self.inventory()
        candidate = next(
            item for item in inventory["skills"] if item["name"] == "vibe-project-lead-zh"
        )
        self.assertEqual(candidate["declared_name"], "vibe-project-lead-zh")
        self.assertEqual(candidate["source_namespace"], "standalone")
        self.assertEqual(candidate["path"], str(setup["target"] / "SKILL.md"))
        self.assertNotIn(str(setup["source"]), json.dumps(inventory))
        rendered = self.output.read_text(encoding="utf-8")
        self.assertIn(str(setup["target"] / "SKILL.md"), rendered)
        self.assertNotIn(str(setup["source"]), rendered)

        calls = [json.loads(line) for line in setup["calls_log"].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(call["kind"] == "version" for call in calls), 1)
        app_calls = [call for call in calls if call["kind"] == "app-server"]
        self.assertEqual([call["mounted"] for call in app_calls], [False, True])
        self.assertTrue(app_calls[1]["write_blocked"])

        bwrap_args = json.loads(setup["bwrap_log"].read_text(encoding="utf-8"))
        overlay_position = bwrap_args.index("--overlay-src")
        candidate_triplet = ["--ro-bind", str(setup["source"].resolve()), str(setup["target"].resolve())]
        read_only_position = next(
            index
            for index in range(len(bwrap_args) - 2)
            if bwrap_args[index : index + 3] == candidate_triplet
        )
        self.assertLess(overlay_position, read_only_position)
        self.assertEqual(
            bwrap_args[read_only_position + 1 : read_only_position + 3],
            [str(setup["source"].resolve()), str(setup["target"].resolve())],
        )

    def test_candidate_arguments_are_rejected_in_input_mode(self):
        fixture = self.materialize_fixture("skills-list-ok.json")

        result = self.run_index(
            fixture,
            "--candidate-id",
            "vibe-project-lead-zh",
            "--candidate-source",
            self.tempdir / "source",
            "--candidate-target",
            self.tempdir / "target",
        )

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertFalse(self.output.exists())

    def test_partial_candidate_arguments_fail_before_app_server(self):
        setup = self.make_candidate_environment()
        command = self.candidate_command(setup)
        target_flag = command.index("--candidate-target")
        del command[target_flag : target_flag + 2]

        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=setup["env"],
        )

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertFalse(setup["calls_log"].exists())
        self.assertFalse(self.output.exists())

    def test_candidate_identity_source_and_target_are_locked_before_candidate_process(self):
        cases = []

        wrong_id = self.make_candidate_environment()
        cases.append(("wrong-id", wrong_id, {"candidate_id": "other-skill"}))

        outside_source = self.make_candidate_environment()
        other_source = outside_source["checkout"] / "other-source"
        other_source.mkdir()
        (other_source / "SKILL.md").write_text(
            "---\nname: vibe-project-lead-zh\ndescription: fixture\n---\n",
            encoding="utf-8",
        )
        cases.append(("outside-source", outside_source, {"source": other_source}))

        symlink_source = self.make_candidate_environment()
        real_source = symlink_source["source"].with_name("real-source")
        symlink_source["source"].rename(real_source)
        symlink_source["source"].symlink_to(real_source, target_is_directory=True)
        cases.append(("symlink-source", symlink_source, {}))

        outside_target = self.make_candidate_environment()
        cases.append(
            (
                "outside-target",
                outside_target,
                {"target": outside_target["codex_home"] / "other" / "vibe-project-lead-zh"},
            )
        )

        existing_target = self.make_candidate_environment()
        existing_target["target"].mkdir()
        cases.append(("existing-target", existing_target, {}))

        for name, setup, overrides in cases:
            with self.subTest(case=name):
                result = self.run_candidate(setup, **overrides)
                self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.inventory_output.exists())
                if setup["calls_log"].exists():
                    calls = [
                        json.loads(line)
                        for line in setup["calls_log"].read_text(encoding="utf-8").splitlines()
                    ]
                    app_calls = [call for call in calls if call["kind"] == "app-server"]
                    self.assertLessEqual(len(app_calls), 1)

    def test_candidate_discovery_must_be_unique_at_expected_target(self):
        for behavior in ("missing", "wrong-id", "wrong-path", "change-base"):
            with self.subTest(behavior=behavior):
                setup = self.make_candidate_environment(behavior)
                result = self.run_candidate(setup)
                self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.inventory_output.exists())
                self.assertFalse(setup["target"].exists())

    def test_candidate_source_mutation_fails_without_output(self):
        setup = self.make_candidate_environment("mutate-source")

        result = self.run_candidate(setup)

        self.assertEqual(result.returncode, 4, result.stderr or result.stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.inventory_output.exists())
        self.assertFalse(setup["target"].exists())

    def test_host_target_creation_during_candidate_query_fails(self):
        setup = self.make_candidate_environment("signal-host-target")
        process = subprocess.Popen(
            self.candidate_command(setup),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=setup["env"],
        )
        deadline = time.monotonic() + 10
        while (
            not setup["signal"].exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if not setup["signal"].exists():
            stdout, stderr = process.communicate(timeout=10)
            self.fail(f"candidate process did not reach mounted discovery: {stderr or stdout}")
        setup["target"].mkdir()
        stdout, stderr = process.communicate(timeout=10)

        self.assertEqual(process.returncode, 4, stderr or stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.inventory_output.exists())

    def test_query_refuses_to_run_without_network_isolator(self):
        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--query",
                "--cwd",
                "/fixture/project",
                "--codex-bin",
                "/bin/true",
                "--bwrap-bin",
                self.tempdir / "missing-bwrap",
                "--policy",
                POLICY,
                "--output",
                self.output,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("network isolation", result.stdout)

    def test_query_reports_isolator_spawn_failure_as_protocol_error(self):
        fake_codex_home = self.tempdir / "spawn-failure-home"
        fake_codex_home.mkdir()
        fake_sqlite_home, _ = self.make_sqlite_home("spawn-failure-sqlite-home")
        fake_codex = self.tempdir / "spawn-failure-codex"
        fake_codex.write_text(
            f"#!{sys.executable}\nprint('codex-cli fixture 1.0')\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        invalid_bwrap = self.tempdir / "invalid-bwrap"
        invalid_bwrap.write_text("not an executable image\n", encoding="utf-8")
        invalid_bwrap.chmod(0o755)

        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--query",
                "--cwd",
                "/fixture/project",
                "--codex-bin",
                fake_codex,
                "--codex-home",
                fake_codex_home,
                "--codex-sqlite-home",
                fake_sqlite_home,
                "--bwrap-bin",
                invalid_bwrap,
                "--policy",
                POLICY,
                "--output",
                self.output,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('"status": "protocol_error"', result.stdout)
        self.assertFalse(self.output.exists())

    def test_input_and_query_are_mutually_exclusive(self):
        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--input",
                FIXTURES / "skills-list-ok.json",
                "--query",
                "--cwd",
                "/fixture/project",
                "--policy",
                POLICY,
                "--output",
                self.output,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr)


if __name__ == "__main__":
    unittest.main()
