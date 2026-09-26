# SPDX-License-Identifier: MPL-2.0
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_archive.py"
ARCHIVE_NAME = "Vibe-Leader-3.1.0-GitHub.zip"
ASSETS = (ARCHIVE_NAME, "release_archive.py", "SHA256SUMS.txt")
TOP_LEVEL = "Vibe-Leader-3.1.0"
SKILL_PATH = "skill/vibe-project-lead-zh/SKILL.md"


def load_release(test):
    test.assertTrue(SCRIPT.is_file(), "scripts/release_archive.py is not implemented")
    spec = importlib.util.spec_from_file_location("release_archive", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.PIPE)


def initialize_repo(repo):
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Release Fixture")
    git(repo, "config", "user.email", "release-fixture@example.invalid")


def commit_all(repo):
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "Synthetic release fixture")
    return git(repo, "rev-parse", "HEAD").decode().strip()


class ReleaseArchiveBuildTests(unittest.TestCase):
    def setUp(self):
        self.release = load_release(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        initialize_repo(self.repo)
        files = {
            "LICENSE": b"fixture license\n",
            "README.md": b"fixture readme\n",
            "scripts/release_archive.py": SCRIPT.read_bytes(),
            SKILL_PATH: b"---\nname: vibe-project-lead-zh\n---\nfixture\n",
        }
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.repo / "scripts/release_archive.py").chmod(0o755)
        self.commit = commit_all(self.repo)

    def output(self, name="output"):
        path = self.root / name
        path.mkdir()
        return path

    def test_same_commit_builds_byte_identical_assets(self):
        first, second = self.output("first"), self.output("second")
        self.release.build_archive(self.repo, self.commit, first)
        self.release.build_archive(self.repo, self.commit, second)
        for name in ASSETS:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

    def test_zip_metadata_and_committed_script_are_fixed(self):
        output = self.output()
        self.release.build_archive(self.repo, self.commit, output)
        self.assertEqual((output / "release_archive.py").read_bytes(), SCRIPT.read_bytes())
        with zipfile.ZipFile(output / ARCHIVE_NAME) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names, key=lambda path: path.encode("utf-8")))
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual((info.create_system, info.create_version, info.extract_version), (3, 20, 20))
                self.assertEqual((info.extra, info.comment), (b"", b""))
                mode = 0o755 if info.filename.endswith("scripts/release_archive.py") else 0o644
                self.assertEqual(info.external_attr, (stat.S_IFREG | mode) << 16)
        checksums = (output / "SHA256SUMS.txt").read_bytes()
        expected = b"".join(
            hashlib.sha256((output / name).read_bytes()).hexdigest().encode()
            + b"  " + name.encode() + b"\n" for name in ASSETS[:2]
        )
        self.assertEqual(checksums, expected)

    def test_build_uses_selected_commit_even_when_clean_head_is_newer(self):
        (self.repo / "README.md").write_bytes(b"newer checkout content")
        (self.repo / "scripts/release_archive.py").write_bytes(b"newer script")
        commit_all(self.repo)
        output = self.output()
        self.release.build_archive(self.repo, self.commit, output)
        self.assertEqual((output / "release_archive.py").read_bytes(), SCRIPT.read_bytes())
        with zipfile.ZipFile(output / ARCHIVE_NAME) as archive:
            self.assertEqual(archive.read(TOP_LEVEL + "/README.md"), b"fixture readme\n")

    def test_dirty_worktree_or_index_is_rejected_without_output(self):
        for kind in ("tracked", "untracked", "staged"):
            with self.subTest(kind=kind):
                path = self.repo / ("README.md" if kind == "tracked" else "extra")
                path.write_bytes(b"dirty")
                if kind == "staged":
                    git(self.repo, "add", "extra")
                output = self.output(kind)
                with self.assertRaises(ValueError):
                    self.release.build_archive(self.repo, self.commit, output)
                self.assertEqual(list(output.iterdir()), [])
                git(self.repo, "reset", "--hard", "-q", self.commit)
                git(self.repo, "clean", "-fdq")

    def test_symlink_and_gitlink_are_rejected_without_output(self):
        for kind in ("symlink", "gitlink"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    (self.repo / "link").symlink_to("README.md")
                    commit = commit_all(self.repo)
                else:
                    git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.commit},submodule")
                    git(self.repo, "commit", "-qm", "Add gitlink")
                    commit = git(self.repo, "rev-parse", "HEAD").decode().strip()
                output = self.output(kind)
                with self.assertRaises(ValueError):
                    self.release.build_archive(self.repo, commit, output)
                self.assertEqual(list(output.iterdir()), [])
                git(self.repo, "reset", "--hard", "-q", self.commit)

    def test_noncommit_objects_are_rejected_without_output(self):
        for name, revision in (("tree", "HEAD^{tree}"), ("blob", "HEAD:README.md")):
            output = self.output(name)
            object_id = git(self.repo, "rev-parse", revision).decode().strip()
            with self.assertRaises(ValueError):
                self.release.build_archive(self.repo, object_id, output)
            self.assertEqual(list(output.iterdir()), [])

    def test_output_collisions_preserve_all_existing_assets(self):
        for index, name in enumerate(ASSETS):
            with self.subTest(name=name):
                output = self.output(str(index))
                (output / name).write_bytes(b"user-owned")
                with self.assertRaises((ValueError, FileExistsError)):
                    self.release.build_archive(self.repo, self.commit, output)
                self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, {name: b"user-owned"})


class ReleaseArchiveSecurityTests(unittest.TestCase):
    def setUp(self):
        self.release = load_release(self)
        self.assertTrue(callable(getattr(self.release, "verify_archive", None)), "verify_archive is not implemented")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / ARCHIVE_NAME
        self.checksums = self.root / "SHA256SUMS.txt"
        self.commit = "1" * 40
        self.files = [("LICENSE", b"license\n", 0o644),
                      ("scripts/release_archive.py", SCRIPT.read_bytes(), 0o755),
                      (SKILL_PATH, b"---\nname: vibe-project-lead-zh\n---\n", 0o644)]

    def make_archive(self, *, files=None, manifest_edit=None, missing_manifest=False, extra=()):
        files = self.files if files is None else files
        manifest = {
            "format_version": 1, "source_commit": self.commit, "top_level": TOP_LEVEL,
            "files": [{"path": name, "mode": mode, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                      for name, content, mode in files],
        }
        if manifest_edit:
            manifest_edit(manifest)
        entries = [(TOP_LEVEL + "/" + name, content, mode) for name, content, mode in files]
        if not missing_manifest:
            entries.append((TOP_LEVEL + "/RELEASE-MANIFEST.json", json.dumps(manifest).encode(), 0o644))
        entries.extend(extra)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w") as archive:
                for name, content, mode in entries:
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    archive.writestr(info, content)
        self.update_checksums()
        return manifest

    def update_checksums(self, *, script=SCRIPT):
        self.checksums.write_bytes(
            hashlib.sha256(self.archive.read_bytes()).hexdigest().encode() + b"  " + ARCHIVE_NAME.encode() + b"\n"
            + hashlib.sha256(script.read_bytes()).hexdigest().encode() + b"  release_archive.py\n"
        )

    def verify(self, expected_commit=None):
        return self.release.verify_archive(self.archive, self.checksums, expected_commit=expected_commit)

    def assert_rejected(self, reason=None):
        with self.assertRaises(ValueError) as raised:
            self.verify(self.commit)
        if reason:
            self.assertEqual(str(raised.exception), reason)
        destination = self.root / "not-created"
        with self.assertRaises(ValueError):
            self.release.extract_archive(self.archive, self.checksums, destination, expected_commit=self.commit)
        self.assertFalse(destination.exists())

    def patch_headers(self, mutate):
        data = bytearray(self.archive.read_bytes())
        with zipfile.ZipFile(self.archive) as archive:
            central = archive.start_dir
            for info in archive.infolist():
                mutate(data, info.header_offset, central)
                name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, central + 28)
                central += 46 + name_len + extra_len + comment_len
        self.archive.write_bytes(data)
        self.update_checksums()

    def test_valid_archive_and_extract_match_every_manifest_record(self):
        manifest = self.make_archive()
        result = self.verify(self.commit)
        self.assertEqual(result["source_commit"], self.commit)
        destination = self.root / "extracted"
        self.release.extract_archive(self.archive, self.checksums, destination, expected_commit=self.commit)
        top = destination / TOP_LEVEL
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        actual_files = {path.relative_to(top).as_posix() for path in top.rglob("*") if path.is_file()}
        self.assertEqual(actual_files, {item["path"] for item in manifest["files"]} | {"RELEASE-MANIFEST.json"})
        for item in manifest["files"]:
            path = top / item["path"]
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), item["mode"])
            self.assertEqual(path.stat().st_size, item["size"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
        for path in top.rglob("*"):
            if path.is_dir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)

    def test_unsafe_paths_are_rejected_before_extraction(self):
        for path in ("/absolute", "../escape", "backslash\\entry", "bad\x01entry", "bad\x7fentry",
                     "a/../escape", "a//entry", "./entry", "C:/escape"):
            with self.subTest(path=path):
                self.make_archive(extra=[(path, b"unsafe", 0o644)])
                self.assert_rejected("unsafe_path")

    def test_embedded_nul_in_zip_filename_is_rejected(self):
        self.make_archive(extra=[(TOP_LEVEL + "/bad@entry", b"unsafe", 0o644)])
        self.archive.write_bytes(self.archive.read_bytes().replace(b"bad@entry", b"bad\0entry"))
        self.update_checksums()
        self.assert_rejected("unsafe_path")

    def test_duplicate_unicode_case_and_implicit_directory_collisions_are_rejected(self):
        for paths in (("same", "same"), ("caf\u00e9", "cafe\u0301"), ("Name", "name"),
                      ("A/one", "a/two"), ("file", "file/child")):
            with self.subTest(paths=paths):
                self.make_archive(extra=[(TOP_LEVEL + "/" + name, b"unsafe", 0o644) for name in paths])
                self.assert_rejected("path_collision")

    def test_symlinks_devices_and_other_special_modes_are_rejected(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o644, stat.S_IFBLK | 0o644,
                     stat.S_IFIFO | 0o644, stat.S_IFSOCK | 0o644, stat.S_IFDIR | 0o755,
                     stat.S_IFREG | 0o4755):
            with self.subTest(mode=mode):
                self.make_archive()
                self.patch_headers(lambda data, local, central: struct.pack_into("<I", data, central + 38, mode << 16))
                self.assert_rejected("unsafe_zip_entry")

    def test_deflate_and_encrypted_flags_are_rejected(self):
        for field, local_offset, central_offset, value in (("deflate", 8, 10, 8), ("encryption", 6, 8, 1)):
            with self.subTest(field=field):
                self.make_archive()
                def mutate(data, local, central):
                    struct.pack_into("<H", data, local + local_offset, value)
                    struct.pack_into("<H", data, central + central_offset, value)
                self.patch_headers(mutate)
                self.assert_rejected("unsafe_zip_entry")

    def test_local_header_cannot_hide_encryption_or_compression_from_central_header(self):
        for offset, value in ((6, 1), (8, 8)):
            with self.subTest(offset=offset):
                self.make_archive()
                self.patch_headers(lambda data, local, central: struct.pack_into("<H", data, local + offset, value))
                self.assert_rejected("unsafe_zip_entry")

    def test_archive_cannot_supply_git_metadata_that_overrides_manifest_commit(self):
        for name in (".git/HEAD", "skill/.GIT/HEAD"):
            with self.subTest(name=name):
                self.make_archive(files=self.files + [(name, b"2" * 40 + b"\n", 0o644)])
                self.assert_rejected("unsafe_path")

    def test_manifest_duplicate_json_keys_are_rejected(self):
        self.make_archive()
        # Create a new ordinary ZIP so the attack has valid CRCs and checksums.
        with zipfile.ZipFile(self.archive) as opened:
            entries = [(info, opened.read(info)) for info in opened.infolist()]
        with zipfile.ZipFile(self.archive, "w") as opened:
            for info, content in entries:
                if info.filename.endswith("RELEASE-MANIFEST.json"):
                    content = b'{"format_version":1,' + content[1:]
                opened.writestr(info, content)
        self.update_checksums()
        self.assert_rejected("manifest_invalid")

    def test_embedded_script_must_match_the_executing_standalone_script(self):
        files = [(name, b"different script" if name == "scripts/release_archive.py" else content, mode)
                 for name, content, mode in self.files]
        self.make_archive(files=files)
        self.assert_rejected("script_archive_mismatch")

    def test_second_top_level_and_zero_or_two_skills_are_rejected(self):
        self.make_archive(extra=[("other/README.md", b"other root", 0o644)])
        self.assert_rejected("release_layout_invalid")
        for files in (self.files[:-1], self.files + [("other/SKILL.md", b"second skill", 0o644)]):
            self.make_archive(files=files)
            self.assert_rejected("skill_inventory_not_unique")

    def test_archive_file_count_file_size_and_total_size_limits_are_enforced(self):
        self.make_archive(extra=[(TOP_LEVEL + f"/empty-{index}", b"", 0o644) for index in range(497)])
        self.assert_rejected("archive_limit_exceeded")
        for size in (25 * 1024 * 1024 + 1, 18 * 1024 * 1024):
            with self.subTest(size=size):
                self.make_archive()
                def mutate(data, local, central):
                    struct.pack_into("<II", data, central + 20, size, size)
                self.patch_headers(mutate)
                self.assert_rejected("archive_limit_exceeded")
        self.make_archive()
        with self.archive.open("r+b") as stream:
            stream.truncate(50 * 1024 * 1024 + 1)
        self.update_checksums()
        self.assert_rejected("archive_limit_exceeded")

    def test_manifest_missing_extra_file_or_digest_drift_is_rejected(self):
        self.make_archive(missing_manifest=True)
        self.assert_rejected("manifest_invalid")
        self.make_archive(extra=[(TOP_LEVEL + "/extra", b"extra", 0o644)])
        self.assert_rejected("manifest_invalid")
        for field, value in (("size", 0), ("sha256", "f" * 64), ("mode", 0o755)):
            with self.subTest(field=field):
                self.make_archive(manifest_edit=lambda manifest: manifest["files"][0].update({field: value}))
                self.assert_rejected("manifest_invalid")

    def test_expected_commit_rejects_a_different_valid_object_id(self):
        self.make_archive(manifest_edit=lambda manifest: manifest.update(source_commit="2" * 40))
        self.assert_rejected("source_commit_mismatch")

    def test_manifest_schema_and_invalid_commit_are_rejected(self):
        for field, value in (("format_version", True), ("format_version", 2), ("top_level", "other"),
                             ("source_commit", "bad"), ("files", {})):
            with self.subTest(field=field):
                self.make_archive(manifest_edit=lambda manifest: manifest.update({field: value}))
                self.assert_rejected("manifest_invalid")

    def test_zip_and_currently_executing_script_both_match_strict_checksums(self):
        self.make_archive()
        original = self.checksums.read_bytes()
        wrong = b"0" * 64
        variants = [original.replace(original[:64], wrong, 1),
                    original.replace(hashlib.sha256(SCRIPT.read_bytes()).hexdigest().encode(), wrong),
                    original + b"extra\n", original.replace(b"  ", b" "), original.replace(b"\n", b"\r\n")]
        for content in variants:
            with self.subTest(content=content[:20]):
                self.checksums.write_bytes(content)
                self.assert_rejected("checksum_mismatch")
        self.checksums.write_bytes(original)
        sibling = self.root / "release_archive.py"
        sibling.write_bytes(b"not the script that is running")
        self.assertEqual(self.verify(self.commit)["source_commit"], self.commit)
        self.update_checksums(script=sibling)
        self.assert_rejected("checksum_mismatch")

    def test_existing_destination_is_never_modified(self):
        self.make_archive()
        destination = self.root / "existing"
        destination.mkdir()
        marker = destination / "keep"
        marker.write_bytes(b"user owned")
        with self.assertRaises(FileExistsError):
            self.release.extract_archive(self.archive, self.checksums, destination, expected_commit=self.commit)
        self.assertEqual(list(destination.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b"user owned")

    def test_extraction_failure_preserves_written_evidence_without_following_symlink(self):
        self.make_archive()
        destination = self.root / "partial"
        outside = self.root / "outside"
        outside.write_bytes(b"keep")
        real_open = os.open
        def insert_symlink(path, flags, *args, **kwargs):
            if flags & os.O_CREAT and str(path) == "RELEASE-MANIFEST.json":
                os.symlink(outside, path, dir_fd=kwargs["dir_fd"])
            return real_open(path, flags, *args, **kwargs)
        with mock.patch.object(self.release.os, "open", side_effect=insert_symlink):
            with self.assertRaises(FileExistsError):
                self.release.extract_archive(self.archive, self.checksums, destination, expected_commit=self.commit)
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertEqual((destination / TOP_LEVEL / "LICENSE").read_bytes(), b"license\n")
        self.assertTrue((destination / TOP_LEVEL / "RELEASE-MANIFEST.json").is_symlink())


class ReleaseArchiveCLITests(unittest.TestCase):
    setUp = ReleaseArchiveBuildTests.setUp
    output = ReleaseArchiveBuildTests.output
    def run_cli(self, script, *arguments):
        result = subprocess.run([sys.executable, "-B", str(script), *map(str, arguments)], capture_output=True, text=True)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(str(self.root), result.stdout)
        return result, json.loads(result.stdout)

    def test_standalone_asset_cli_build_verify_extract_and_tamper(self):
        output = self.output()
        result, payload = self.run_cli(SCRIPT, "build", "--repo", self.repo, "--commit", self.commit, "--output-dir", output)
        self.assertEqual(result.returncode, 0, payload)
        downloaded = output / "release_archive.py"
        archive, checksums = output / ARCHIVE_NAME, output / "SHA256SUMS.txt"
        result, payload = self.run_cli(downloaded, "verify", "--archive", archive, "--checksums", checksums, "--expected-commit", self.commit)
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["source_commit"], self.commit)
        result, payload = self.run_cli(downloaded, "extract", "--archive", archive, "--checksums", checksums,
                                       "--destination", self.root / "extracted", "--expected-commit", self.commit)
        self.assertEqual(result.returncode, 0, payload)
        downloaded.write_bytes(downloaded.read_bytes() + b"\n# modified download\n")
        result, payload = self.run_cli(downloaded, "verify", "--archive", archive, "--checksums", checksums, "--expected-commit", self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["reason"], "checksum_mismatch")

    def test_cli_invalid_arguments_are_one_redacted_json_line(self):
        for arguments in ((), ("unknown",), ("verify",), ("--help",)):
            with self.subTest(arguments=arguments):
                result, payload = self.run_cli(SCRIPT, *arguments)
                self.assertIsInstance(payload, dict)


if __name__ == "__main__":
    unittest.main()
