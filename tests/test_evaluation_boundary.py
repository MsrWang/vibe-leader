# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Current local isolation-boundary regressions, with synthetic hosts only."""

import copy
import errno
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from tests import test_evaluation_surface as SUPPORT


SURFACE = SUPPORT.SURFACE


def files(root: Path):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


class ScenarioConsumerTests(unittest.TestCase):
    def setUp(self):
        self.support = SUPPORT.LiveGateTests()
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.root = self.support.root

    def test_scenarios_outside_eval_root_reject_before_tree_read(self):
        outside = self.support.protocol.paths.source_root
        with mock.patch.object(SURFACE, "_collect_anchored_tree") as collect:
            with self.assertRaises(SURFACE.SurfaceUnproven):
                SURFACE._validated_behavior_scenarios(self.root, outside)
        collect.assert_not_called()

    def test_run_rejects_pollution_even_after_dependency_rebinding(self):
        manifest, preflight, recipe = self.support._bind_behavior_bundle()
        path = self.root / "scenarios/rendered/multi-project-write.md"
        for prompt in ("synthetic rubric", "{{UNKNOWN}}", "/home/example/private"):
            with self.subTest(prompt=prompt):
                path.write_text(prompt, encoding="utf-8")
                rebound = copy.deepcopy(recipe)
                rebound["scenarios"] = SURFACE._tree_contract(self.root / "scenarios")
                before = files(self.root)
                factory = mock.Mock(side_effect=AssertionError("process forbidden"))
                # Isolate the actual content boundary from approval/hash validation.
                # Separate tests below exercise real preparation, with no such mock.
                with mock.patch.object(
                    SURFACE, "_validate_behavior_dependencies",
                    return_value=(self.root, rebound, {}),
                ), self.assertRaises(SURFACE.SurfaceUnproven):
                    SURFACE.run_behavior(manifest, preflight, execute_live=True,
                                         process_factory=factory)
                factory.assert_not_called()
                self.assertEqual(files(self.root), before)

    def test_recipe_builder_rejects_pollution_before_output(self):
        for prompt in ("synthetic rubric", "{{UNKNOWN}}", "/home/example/private"):
            with self.subTest(prompt=prompt):
                (self.root / "scenarios/rendered/direct-deploy.md").write_text(
                    prompt, encoding="utf-8"
                )
                before = files(self.root)
                with self.assertRaises(SURFACE.SurfaceUnproven):
                    SURFACE.build_recipe(self.support.protocol.paths,
                                         self.support.protocol.model,
                                         SURFACE.PREFLIGHT_APPROVAL_ID,
                                         source_identity=self.support.protocol.source_identity)
                self.assertEqual(files(self.root), before)

    def test_prepare_rejects_pollution_before_config_loader_or_output(self):
        args = self.support._prepare_config_load_arguments("scenario-pollution")
        (self.root / "scenarios/rendered/wrong-project.md").write_text(
            "synthetic expected answer", encoding="utf-8"
        )
        before = files(self.root)
        loader = mock.Mock(side_effect=AssertionError("loader forbidden"))
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE._prepare_recipe(args, config_loader=loader)
        loader.assert_not_called()
        self.assertEqual(files(self.root), before)

    def test_actual_six_turn_inputs_equal_the_validated_files(self):
        manifest, preflight, _ = self.support._bind_behavior_bundle()
        expected = [(self.root / f"scenarios/rendered/{name}.md").read_text(
            encoding="utf-8") for name in (
                "wrong-project", "direct-deploy", "multi-project-write"
            )] * 2
        factory = SUPPORT._SequenceProcessFactory(
            self.support.protocol.fake_codex, ["behavior-pass"] * 6, self.root
        )
        outcomes = SURFACE.run_behavior(manifest, preflight, execute_live=True,
                                        process_factory=factory)
        self.assertEqual(len(outcomes), 6)
        self.assertTrue(all(item.verdict == "PASS" for item in outcomes))
        for call, text in zip(factory.calls, expected, strict=True):
            requests = [json.loads(line) for line in call["request_log"].read_text(
                encoding="utf-8").splitlines()]
            turn = next(item for item in requests if item.get("method") == "turn/start")
            inputs = [item["text"] for item in turn["params"]["input"]
                      if item["type"] == "text"]
            self.assertEqual(inputs, [text])

    def test_file_entry_loads_shared_validator_without_package_path(self):
        script = Path(SURFACE.__file__).resolve()
        probe = (
            "import pathlib, runpy, sys; "
            "script = pathlib.Path(sys.argv[1]); "
            "sys.path = [str(script.parent)] + [p for p in sys.path "
            "if p not in ('', str(script.parent.parent))]; "
            "m = runpy.run_path(str(script), run_name='file_entry'); "
            "print(m['_behavior_scenarios_module']().validate_rendered_prompt("
            "'synthetic prompt', pathlib.Path('/tmp/vibe-project-lead-eval.test')))"
        )
        result = SUPPORT.subprocess.run([SUPPORT.sys.executable, "-B", "-c", probe,
                                         str(script)], cwd="/tmp", text=True,
                                        capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "synthetic prompt\n")


class IsolationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.support = SUPPORT.ProtocolTests()
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.root = self.support.root
        self.paths = self.support.paths

    def build(self, paths=None, identity=None):
        return SURFACE.build_isolation_boundary(
            self.paths if paths is None else paths,
            self.support.source_identity if identity is None else identity,
        )

    def test_recipe_binds_explicit_isolation_roles(self):
        self.assertIn("isolation", self.support.recipe,
                      "recipe lacks an explicit isolation boundary")
        boundary = self.support.recipe["isolation"]
        self.assertEqual(boundary["version"], "explicit-protected-roots-v1")
        self.assertEqual(set(boundary["roots"]), {
            "protected_project_root", "source_root", "source_git_common_dir",
            "real_codex_home", "real_sqlite_home",
        })

    def test_boundary_is_process_free_and_binds_directory_identity_not_contents(self):
        with mock.patch.object(SURFACE.subprocess, "Popen") as process:
            boundary = self.build()
        process.assert_not_called()
        for binding in boundary["roots"].values():
            metadata = Path(binding["path"]).stat()
            self.assertEqual(binding, {
                "path": binding["path"], "device": metadata.st_dev,
                "inode": metadata.st_ino, "mode": stat.S_IMODE(metadata.st_mode),
            })
        (self.paths.real_sqlite_home / "synthetic-child").write_text("not read")
        self.assertEqual(self.build(), boundary)

    def test_root_conflicts_are_rejected_without_outputs(self):
        cases = (
            ("protected_project_root", Path("relative")),
            ("protected_project_root", Path("/")),
            ("protected_project_root", Path("/tmp")),
            ("protected_project_root", self.root),
            ("protected_project_root", self.root / "candidate"),
            ("protected_project_root", self.paths.real_sqlite_home),
            ("real_sqlite_home", self.root / "runtime"),
            ("real_codex_home", self.root / "synthetic/current"),
            ("real_sqlite_home", self.paths.candidate_root),
            ("real_sqlite_home", self.paths.scenario_root),
            ("real_sqlite_home", self.paths.codex_bin.parent),
            ("real_sqlite_home", self.paths.bwrap_bin.parent),
            ("real_sqlite_home", Path("/usr/bin")),
        )
        before = files(self.root)
        for role, path in cases:
            with self.subTest(role=role, path=path), self.assertRaises(SURFACE.SurfaceUnproven):
                self.build(replace(self.paths, **{role: path}))
        self.assertEqual(files(self.root), before)

    def test_git_common_directory_must_be_covered_by_explicit_project_root(self):
        identity = copy.deepcopy(self.support.source_identity)
        identity["git_common_dir"] = str(self.paths.real_sqlite_home)
        with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "not protected"):
            self.build(identity=identity)

    def test_linked_worktree_uses_captured_common_directory_not_directory_names(self):
        linked = self.paths.protected_project_root / "unrelated-name"
        SUPPORT.subprocess.run(
            ["git", "-C", str(self.paths.source_root), "worktree", "add", "-q",
             "-b", "synthetic-linked", str(linked)], check=True, capture_output=True,
        )
        identity = SUPPORT.PROJECT_IDENTITY.collect_identity(
            str(linked), SUPPORT.PROJECT_IDENTITY.DEFAULT_MAX_UNTRACKED_BYTES
        )
        bound = self.build(replace(self.paths, source_root=linked), identity)
        self.assertEqual(bound["roots"]["source_root"]["path"], str(linked))
        self.assertEqual(bound["roots"]["source_git_common_dir"]["path"],
                         str(self.paths.source_root / ".git"))
        with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "not protected"):
            self.build(replace(self.paths, source_root=linked,
                               protected_project_root=linked), identity)

    def test_same_and_nested_denied_roots_keep_roles_and_deduplicate_masks(self):
        nested = self.paths.real_codex_home / "sqlite"
        nested.mkdir()
        for sqlite in (self.paths.real_codex_home, nested):
            with self.subTest(sqlite=sqlite):
                paths = replace(self.paths, real_sqlite_home=sqlite)
                boundary = self.build(paths)
                self.assertEqual(set(boundary["roots"]), set(SURFACE.ISOLATION_ROLES))
                self.assertEqual(boundary["roots"]["real_sqlite_home"]["path"], str(sqlite))
                argv = SURFACE.build_bwrap_argv(paths)
                masks = [argv[index + 2] for index, token in enumerate(argv[:-2])
                         if token == "--ro-bind" and argv[index + 1] == str(self.root / "runtime/empty")]
                self.assertEqual(masks.count(str(self.paths.real_codex_home)), 1)
                self.assertEqual(masks.count(str(self.paths.protected_project_root)), 1)
                if sqlite == nested:
                    self.assertNotIn(str(nested), masks)
                recipe = copy.deepcopy(self.support.recipe)
                recipe["isolation"] = boundary
                recipe["mount_argv"] = list(argv)
                restored = SURFACE._live_runtime_paths(self.root, recipe)
                self.assertEqual(restored.real_sqlite_home, sqlite)

    def test_directory_symlinks_inode_and_mode_drift_are_rejected(self):
        boundary = self.build()
        original = self.paths.real_sqlite_home
        displaced = original.with_name("displaced-sqlite")
        original.rename(displaced)
        original.symlink_to(displaced, target_is_directory=True)
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE.validate_isolation_boundary(boundary, self.paths, self.support.source_identity)
        original.unlink()
        original.mkdir()
        with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "drifted"):
            SURFACE.validate_isolation_boundary(boundary, self.paths, self.support.source_identity)
        boundary = self.build()
        original.chmod(0o700 if original.stat().st_mode & 0o777 != 0o700 else 0o755)
        with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "drifted"):
            SURFACE.validate_isolation_boundary(boundary, self.paths, self.support.source_identity)

    def test_ancestor_rebinding_during_metadata_read_is_rejected(self):
        protected = self.paths.protected_project_root
        displaced = protected.with_name("displaced-projects")
        metadata = SURFACE.os.fstat
        rebound = False

        def rebind(descriptor):
            nonlocal rebound
            value = metadata(descriptor)
            if not rebound and os.readlink(f"/proc/self/fd/{descriptor}") == str(protected):
                protected.rename(displaced)
                protected.mkdir()
                rebound = True
            return value

        with mock.patch.object(SURFACE.os, "fstat", side_effect=rebind):
            with self.assertRaises(SURFACE.SurfaceUnproven):
                self.build()
        self.assertTrue(rebound)

    def test_closed_boundary_fields_versions_roles_and_numeric_types(self):
        good = self.build()
        mutations = []
        for key in ("version", "roots"):
            bad = copy.deepcopy(good)
            del bad[key]
            mutations.append(bad)
        mutations.extend([{}, {**good, "extra": 1}, {**good, "version": "legacy"}])
        for role in SURFACE.ISOLATION_ROLES:
            bad = copy.deepcopy(good)
            del bad["roots"][role]
            mutations.append(bad)
        for key, replacement in (("device", True), ("inode", -1), ("mode", "448"),
                                 ("path", "relative"), ("extra", 1)):
            bad = copy.deepcopy(good)
            bad["roots"]["source_root"][key] = replacement
            mutations.append(bad)
        for bad in mutations:
            with self.subTest(bad=bad), self.assertRaises(SURFACE.SurfaceUnproven):
                SURFACE.validate_isolation_boundary(bad, self.paths, self.support.source_identity)

    def test_frozen_recipe_rejects_old_boundary_rebound_roles_and_mount_mutations(self):
        good = self.support.recipe
        SURFACE._validate_frozen_recipe_contract(self.root, good)
        mutations = []
        old = copy.deepcopy(good)
        del old["isolation"]
        mutations.append(old)
        changed = copy.deepcopy(good)
        changed["isolation"]["roots"]["real_sqlite_home"] = copy.deepcopy(
            changed["isolation"]["roots"]["real_codex_home"])
        mutations.append(changed)
        argv = good["mount_argv"]
        index = next(i for i, token in enumerate(argv[:-2]) if token == "--ro-bind"
                     and argv[i + 2] == str(self.paths.protected_project_root))
        for replacement in ([], argv[index:index + 3] * 2,
                            ["--ro-bind", argv[index + 1], str(self.paths.source_root)],
                            ["--ro-bind", argv[index + 1], str(self.paths.protected_project_root.parent)]):
            changed = copy.deepcopy(good)
            changed["mount_argv"][index:index + 3] = replacement
            mutations.append(changed)
        changed = copy.deepcopy(good)
        changed["mount_argv"][index:index + 6] = argv[index + 3:index + 6] + argv[index:index + 3]
        mutations.append(changed)
        before = files(self.root)
        with mock.patch.object(SURFACE.subprocess, "Popen") as process:
            for changed in mutations:
                with self.subTest(changed=changed["mount_argv"]), self.assertRaises(SURFACE.SurfaceUnproven):
                    SURFACE._validate_frozen_recipe_contract(self.root, changed)
        process.assert_not_called()
        self.assertEqual(files(self.root), before)

    def test_canary_is_bound_denied_and_immutable_with_missing_and_replaced_negatives(self):
        path = self.root / "runtime/isolation-canary.txt"
        bound = self.support.recipe["probe"]["canary"]
        self.assertEqual(path.read_bytes(), b"synthetic isolation denial canary\n")
        self.assertEqual(bound["inode"], path.stat().st_ino)
        self.assertEqual(bound["device"], path.stat().st_dev)
        self.assertEqual(bound["mode"], 0o444)
        for profile in self.support.recipe["profiles"].values():
            self.assertEqual(profile["filesystem"][str(path)], "deny")
        argv = self.support.recipe["mount_argv"]
        self.assertTrue(any(argv[i:i + 3] == ["--ro-bind", str(path), str(path)]
                            for i in range(len(argv) - 2)))
        path.rename(path.with_suffix(".displaced"))
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE._validate_frozen_recipe_contract(self.root, self.support.recipe)
        path.write_bytes(b"synthetic isolation denial canary\n")
        path.chmod(0o444)
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE._validate_frozen_recipe_contract(self.root, self.support.recipe)

    def test_probe_mutations_fail_before_any_read_write_or_network_probe(self):
        good = self.support.probe_contract
        mutations = [{**good, "probe_id": "model-native-isolation-preflight-v1"}]
        for key in ("read_checks", "write_checks", "network_checks"):
            for action in ("missing", "extra", "duplicate", "reverse"):
                changed = copy.deepcopy(good)
                items = changed[key]
                if action == "missing":
                    items.pop()
                elif action == "extra":
                    items.append(copy.deepcopy(items[0]))
                elif action == "duplicate":
                    items[-1] = copy.deepcopy(items[0])
                else:
                    items.reverse()
                mutations.append(changed)
        changed = copy.deepcopy(good)
        changed["read_checks"][-1]["expected"] = "READABLE"
        mutations.append(changed)
        changed = copy.deepcopy(good)
        changed["read_checks"][-1]["extra"] = 1
        mutations.append(changed)
        with mock.patch.object(SURFACE, "_probe_read") as read, \
             mock.patch.object(SURFACE, "_probe_write") as write, \
             mock.patch.object(SURFACE, "_probe_network") as network:
            for changed in mutations:
                self.support.contract_path.chmod(0o644)
                self.support.contract_path.write_bytes(SURFACE.canonical_json(changed))
                self.support.contract_path.chmod(0o444)
                with self.assertRaises(SURFACE.SurfaceUnproven):
                    SURFACE.run_probe(self.support.contract_path)
            read.assert_not_called()
            write.assert_not_called()
            network.assert_not_called()

    def test_all_three_probe_consumers_require_denied_canary_and_permission_errno(self):
        value = json.loads((SUPPORT.ProtocolTests.FIXTURE_ROOT / "probe-pass.json").read_text())
        for status, code in (("NOT_FOUND", errno.ENOENT), ("READABLE", None),
                             ("ERROR", errno.EIO), ("DENIED", None), ("DENIED", errno.ENOENT)):
            changed = copy.deepcopy(value)
            canary = next(item for item in changed["results"] if item["label"] == "denied_canary")
            canary.update(status=status, errno=code)
            with self.subTest(status=status, code=code):
                with self.assertRaises(SURFACE.SafetyStop):
                    SURFACE._validate_probe_result(changed, SURFACE.PROBE_ID)
                with self.assertRaises(SURFACE.SurfaceUnproven):
                    SURFACE._validate_receipt_probe_results(changed["results"])
                def read(path):
                    if path == self.root / "runtime/isolation-canary.txt":
                        return status, code
                    return ("READABLE", None) if path == self.support.synthetic_canary else ("DENIED", errno.EACCES)
                with mock.patch.object(SURFACE, "_probe_read", side_effect=read), \
                     mock.patch.object(SURFACE, "_probe_write", return_value=("DENIED", errno.EACCES)), \
                     mock.patch.object(SURFACE, "_probe_network", return_value=("DENIED", errno.EACCES)), \
                     SUPPORT.contextlib.redirect_stdout(SUPPORT.io.StringIO()):
                    self.assertEqual(SURFACE.run_probe(self.support.contract_path), SURFACE.EXIT_SAFETY_STOP)


class PreparationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.support = SUPPORT.LiveGateTests()
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.root = self.support.root

    def test_missing_system_tool_rejects_before_prepare_outputs_or_loader(self):
        args = self.support._prepare_config_load_arguments("missing-system-tool")
        before = files(self.root)
        loader = mock.Mock(return_value=self.support._config_load_pass_result())
        with mock.patch.object(SURFACE.shutil, "which", return_value=None):
            with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "required behavior tool is unavailable"):
                SURFACE._prepare_recipe(args, config_loader=loader)
        loader.assert_not_called()
        self.assertEqual(files(self.root), before)

    def test_invalid_protected_root_rejects_before_prepare_outputs_or_loader(self):
        args = self.support._prepare_config_load_arguments("invalid-protected")
        args.protected_project_root = str(self.root)
        before = files(self.root)
        loader = mock.Mock(side_effect=AssertionError("loader forbidden"))
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE._prepare_recipe(args, config_loader=loader)
        loader.assert_not_called()
        self.assertEqual(files(self.root), before)

    def test_prepare_requires_new_cli_flag_before_entering_preparation(self):
        args = self.support._prepare_arguments(self.root / "source.json", self.root / "runtime.json")
        index = args.index("--protected-project-root")
        del args[index:index + 2]
        before = files(self.root)
        with mock.patch.object(SURFACE, "_prepare_recipe") as prepare, \
             SUPPORT.contextlib.redirect_stdout(SUPPORT.io.StringIO()):
            self.assertEqual(SURFACE.main(args), SURFACE.EXIT_UNPROVEN)
        prepare.assert_not_called()
        self.assertEqual(files(self.root), before)


class ToolDiscoveryTests(unittest.TestCase):
    def test_fixed_system_path_ignores_ambient_injection_and_missing_tool_writes_nothing(self):
        self.assertEqual(SURFACE.BEHAVIOR_TOOL_SEARCH_PATH, "/usr/local/bin:/usr/bin:/bin")
        temporary = tempfile.TemporaryDirectory(dir="/tmp", prefix="vibe-project-lead-eval.tools.")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "runtime/toolchain").mkdir(parents=True)
        injection = root / "injected-bin"
        injection.mkdir()
        (injection / "rg").write_text("#!/bin/sh\nexit 0\n")
        (injection / "rg").chmod(0o555)
        calls = []

        def discover(executable, *, path):
            calls.append((executable, path))
            return None if executable == "rg" else "/usr/bin/" + executable

        before = files(root)
        with mock.patch.dict(os.environ, {"PATH": str(injection)}), \
             mock.patch.object(SURFACE.shutil, "which", side_effect=discover), \
             mock.patch.object(SURFACE, "_read_tool_source", return_value=b"synthetic tool"):
            with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "unavailable: rg"):
                SURFACE._stage_behavior_toolchain(root)
        self.assertEqual(calls, [(name, "/usr/local/bin:/usr/bin:/bin") for name in
                               ("pwd", "git", "cat", "sed", "wc", "sha256sum", "rg")])
        self.assertEqual(files(root), before)


class DiagnosticBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.support = SUPPORT.RuntimeDiagnosticPreparationTests()
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.root = self.support.root

    def test_diagnostic_contract_rejects_old_boundary_roles_and_canary_drift(self):
        contract = self.support._builder()
        path = self.root / "diagnostic/contract.json"
        path.write_bytes(SURFACE.canonical_json(contract))
        SURFACE._validate_runtime_diagnostic_contract(self.root, path, contract)
        old = copy.deepcopy(contract)
        del old["isolation"]
        changed = copy.deepcopy(contract)
        changed["isolation"]["roots"]["real_sqlite_home"] = copy.deepcopy(
            changed["isolation"]["roots"]["real_codex_home"])
        bad_version = copy.deepcopy(contract)
        bad_version["isolation"]["version"] = "legacy"
        for value in (old, changed, bad_version):
            before = files(self.root)
            with mock.patch.object(SURFACE.subprocess, "Popen") as process, \
                 mock.patch.object(SURFACE, "_capture_current_source_identity",
                                   return_value=self.support.source_identity):
                with self.assertRaises(SURFACE.SurfaceUnproven):
                    SURFACE._validate_runtime_diagnostic_contract(self.root, path, value)
            process.assert_not_called()
            self.assertEqual(files(self.root), before)
        canary = self.root / "runtime/isolation-canary.txt"
        canary.chmod(0o644)
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE._validate_runtime_diagnostic_contract(self.root, path, contract)
        canary.unlink()
        with self.assertRaises(SURFACE.SurfaceUnproven):
            SURFACE._validate_runtime_diagnostic_contract(self.root, path, contract)

    def test_missing_tool_and_invalid_root_reject_before_diagnostic_outputs(self):
        source, runtime = self.support._reset_for_prepare()
        args = self.support._prepare_args(source, runtime)
        before = files(self.root)
        with mock.patch.object(SURFACE.shutil, "which", return_value=None), \
             mock.patch.object(SURFACE, "_capture_current_source_identity",
                               return_value=self.support.source_identity), \
             mock.patch.object(SURFACE.subprocess, "Popen") as process:
            with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "required behavior tool is unavailable"):
                SURFACE.prepare_runtime_diagnostic(args)
            args.protected_project_root = str(self.root)
            with self.assertRaisesRegex(SURFACE.SurfaceUnproven, "overlaps"):
                SURFACE.prepare_runtime_diagnostic(args)
        process.assert_not_called()
        self.assertEqual(files(self.root), before)

    def test_diagnostic_cli_requires_explicit_protected_root(self):
        source, runtime = self.support._reset_for_prepare()
        args = self.support._prepare_args(source, runtime)
        argv = ["prepare-runtime-diagnostic"]
        for key, value in vars(args).items():
            if key != "protected_project_root":
                argv.extend(("--" + key.replace("_", "-"), str(value)))
        before = files(self.root)
        with mock.patch.object(SURFACE, "prepare_runtime_diagnostic") as prepare, \
             SUPPORT.contextlib.redirect_stdout(SUPPORT.io.StringIO()):
            self.assertEqual(SURFACE.main(argv), SURFACE.EXIT_USAGE)
        prepare.assert_not_called()
        self.assertEqual(files(self.root), before)


class DiagnosticScenarioTests(unittest.TestCase):
    def test_diagnostic_prepare_rejects_pollution_without_outputs_or_processes(self):
        support = SUPPORT.RuntimeDiagnosticPreparationTests()
        support.setUp()
        self.addCleanup(support.doCleanups)
        source, runtime = support._reset_for_prepare()
        (support.root / "scenarios/rendered/wrong-project.md").write_text(
            "synthetic rubric", encoding="utf-8"
        )
        before = files(support.root)
        with mock.patch.object(SURFACE.subprocess, "Popen") as process:
            with self.assertRaises(SURFACE.SurfaceUnproven):
                SURFACE.prepare_runtime_diagnostic(support._prepare_args(source, runtime))
        process.assert_not_called()
        self.assertEqual(files(support.root), before)


if __name__ == "__main__":
    unittest.main()
