"""Exercise safe source archives using local, fake-data Git fixtures only."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = next(
    (parent / "scripts/check/package_source.py"
     for parent in Path(__file__).resolve().parents
     if (parent / "scripts/check/package_source.py").is_file()),
    Path(__file__).with_name("package_source.py"),
)
SPEC = importlib.util.spec_from_file_location("package_source", SCRIPT)
assert SPEC and SPEC.loader
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="murmur-source-test.")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "checkout with spaces"
        self.root.mkdir()
        self.output = self.base / "source.tar.gz"
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Source Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        for path, body in {
            "server/pyproject.toml": '[project]\nname="murmur-fixture"\n',
            "server/murmur/__init__.py": 'VERSION = "main"\n',
            "server/tests/test_fixture.py": "print('fixture')\n",
            "infra/deploy/murmur-update": "#!/bin/bash\nexit 0\n",
            "scripts/check/run_tests.py": "print('fixture')\n",
            "scripts/ops/migrate-to-vps.sh": "#!/bin/bash\nexit 0\n",
            ".env.example": "MURMUR_DB=murmur.db\n",
            ".gitignore": ".env\n.venv/\n.trash-backup/\n*.db\n",
            "README.md": "fixture readme\n",
            "AGENTS.md": "fixture rules\n",
            "CONTEXT.md": "fixture context\n",
            "CREDITS.md": "fixture credits\n",
            "apps/ios/source.swift": "// excluded\n",
            "apps/android/source.kt": "// excluded\n",
            "assets/brand/logo.svg": "excluded\n",
            "docs/product/overview.md": "excluded\n",
        }.items():
            self.write(path, body)
        (self.root / "infra/deploy/murmur-update").chmod(0o755)
        self.commit("baseline")

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True,
            check=True, timeout=30,
        )
        return result.stdout.strip()

    def write(self, relative, body):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def members(self):
        with tarfile.open(self.output, "r:gz") as archive:
            return {item.name: archive.extractfile(item).read()
                    for item in archive if item.isfile()}

    def reject(self, match):
        with self.assertRaisesRegex(package.PackagingError, match):
            package.package_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_default_head_exact_allowlist_and_executable_mode(self):
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(package.package_source(self.root, self.output), head)
        files = self.members()
        self.assertIn("Murmur/server/murmur/__init__.py", files)
        self.assertIn("Murmur/.env.example", files)
        self.assertNotIn("Murmur/apps/ios/source.swift", files)
        self.assertNotIn("Murmur/assets/brand/logo.svg", files)
        self.assertNotIn("Murmur/docs/product/overview.md", files)
        self.assertTrue(all(name.startswith("Murmur/") for name in files))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        with tarfile.open(self.output) as archive:
            self.assertEqual(archive.getmember("Murmur/infra/deploy/murmur-update").mode & 0o777, 0o755)

    def test_current_branch_head_is_packaged_not_main(self):
        self.git("switch", "-c", "codex/chore/source-package")
        self.write("server/murmur/__init__.py", 'VERSION = "branch"\n')
        head = self.commit("branch source")
        self.assertEqual(package.package_source(self.root, self.output), head)
        self.assertEqual(self.members()["Murmur/server/murmur/__init__.py"], b'VERSION = "branch"\n')

    def test_unstaged_tracked_file_is_rejected(self):
        self.write("server/murmur/__init__.py", "dirty\n")
        self.reject("must both be clean")

    def test_staged_file_is_rejected(self):
        self.write("server/murmur/__init__.py", "staged\n")
        self.git("add", "server/murmur/__init__.py")
        self.reject("must both be clean")

    def test_index_changes_hidden_by_matching_head_worktree_are_rejected(self):
        self.write("server/murmur/__init__.py", "staged\n")
        self.git("add", "server/murmur/__init__.py")
        self.write("server/murmur/__init__.py", 'VERSION = "main"\n')
        self.reject("must both be clean")

    def test_dirty_excluded_client_also_rejects_package(self):
        self.write("apps/ios/source.swift", "dirty excluded code\n")
        self.reject("must both be clean")

    def test_assume_unchanged_cannot_hide_dirty_source(self):
        self.git("update-index", "--assume-unchanged", "server/murmur/__init__.py")
        self.write("server/murmur/__init__.py", "hidden dirty source\n")
        self.reject("clear index flags")

    def test_materialized_skip_worktree_cannot_hide_dirty_source(self):
        self.git("update-index", "--skip-worktree", "server/murmur/__init__.py")
        self.write("server/murmur/__init__.py", "hidden dirty source\n")
        self.reject("clear index flags")

    def test_untracked_and_ignored_private_data_never_enters_archive(self):
        for path in (".env", ".venv/private", ".trash-backup/export.md", "murmur.db",
                     "server/untracked-secret.txt", "personal-notes.txt"):
            self.write(path, "FAKE PRIVATE FIXTURE\n")
        package.package_source(self.root, self.output)
        self.assertFalse(any(b"FAKE PRIVATE FIXTURE" in body for body in self.members().values()))

    def test_tracked_private_key_is_rejected_before_archiving(self):
        self.write("infra/deploy/AuthKey_FAKE.p8", "FAKE KEY FIXTURE\n")
        self.commit("unsafe key")
        self.reject("private/runtime")

    def test_tracked_private_data_even_outside_allowlist_is_rejected(self):
        self.write("docs/.env", "FAKE PRIVATE FIXTURE\n")
        self.git("add", "-f", "docs/.env")
        self.git("commit", "-m", "unsafe env")
        self.reject("private/runtime")

    def test_tracked_runtime_directory_is_rejected(self):
        self.write("server/runtime/state.json", "FAKE STATE FIXTURE\n")
        self.commit("unsafe runtime")
        self.reject("private/runtime")

    def test_tracked_symlink_is_rejected(self):
        (self.root / "server/link").symlink_to("../.env")
        self.commit("unsafe symlink")
        self.reject("symlink")

    def test_submodule_gitlink_is_rejected_without_network(self):
        head = self.git("rev-parse", "HEAD")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{head},server/vendor")
        self.git("commit", "-m", "gitlink")
        self.reject("submodule")

    def test_sparse_checkout_still_packages_committed_allowlist(self):
        self.git("sparse-checkout", "set", "--cone", "server", "infra", "scripts")
        self.assertFalse((self.root / "apps").exists())
        self.assertFalse((self.root / "docs").exists())
        package.package_source(self.root, self.output)
        self.assertIn("Murmur/server/tests/test_fixture.py", self.members())

    def test_export_ignore_cannot_silently_remove_committed_source(self):
        self.write(".gitattributes", "server/murmur/__init__.py export-ignore\n")
        self.commit("incomplete archive")
        self.reject("omitted committed source")

    def test_existing_output_and_symlink_are_not_overwritten(self):
        self.output.write_text("existing artifact\n")
        with self.assertRaisesRegex(package.PackagingError, "already exists"):
            package.package_source(self.root, self.output)
        self.assertEqual(self.output.read_text(), "existing artifact\n")
        self.output.unlink()
        target = self.base / "protected"
        target.write_text("keep\n")
        self.output.symlink_to(target)
        with self.assertRaisesRegex(package.PackagingError, "already exists"):
            package.package_source(self.root, self.output)
        self.assertEqual(target.read_text(), "keep\n")

    def test_permissive_umask_does_not_expose_archive(self):
        previous = os.umask(0o022)
        try:
            package.package_source(self.root, self.output)
        finally:
            os.umask(previous)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
