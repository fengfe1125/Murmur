"""Regression tests for changed-file selection and the mandatory repository gate."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

# tests/test_ci_scope.py -> server/tests/test_ci_scope.py; no installed packages.
# The sibling fallback lets the standalone migration draft be tested before applying.
ROOT = next(
    (parent for parent in Path(__file__).resolve().parents
     if (parent / "scripts/check/ci_scope.py").is_file()),
    Path(__file__).resolve().parent,
)
SCRIPT = ROOT / "scripts/check/ci_scope.py"
if not SCRIPT.is_file():
    SCRIPT = Path(__file__).with_name("ci_scope.py")
SPEC = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
assert SPEC and SPEC.loader
ci_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scope)


class ScopeTests(unittest.TestCase):
    def assert_scope(self, paths, *, server=False, ios=False, android=False):
        self.assertEqual(
            ci_scope.classify_paths(paths),
            {"server": server, "ios": ios, "android": android},
        )

    def test_docs_only_and_empty_still_allow_repository_checks(self):
        self.assert_scope(["README.md", "docs/product/positioning.md"])
        self.assert_scope([])

    def test_mobile_only(self):
        self.assert_scope(["apps/ios/MurmurApp/Views/Home.swift"], ios=True)
        self.assert_scope(["apps/android/app/build.gradle.kts"], android=True)

    def test_legacy_mobile_only(self):
        self.assert_scope(["MurmurApp/Views/Home.swift"], ios=True)
        self.assert_scope(["MurmurApp.xcodeproj/project.pbxproj"], ios=True)
        self.assert_scope(["android/app/build.gradle.kts"], android=True)

    def test_server_only_runtime_changes(self):
        self.assert_scope(["server/murmur/engine.py"], server=True)
        self.assert_scope(["server/tests/test_continuity.py"], server=True)
        self.assert_scope(["infra/deploy/murmur-update"], server=True)
        self.assert_scope(["scripts/ops/run-test-bots.sh"], server=True)

    def test_legacy_server_only_runtime_changes(self):
        for path in ("murmur/engine.py", "tests/test_continuity.py", "deploy/murmur-update",
                     "run.sh", "scripts/sanitize_production_env.py"):
            with self.subTest(path=path):
                self.assert_scope([path], server=True)

    def test_shared_contracts_always_build_both_clients(self):
        for path in (
            "server/murmur/app_api.py",
            "server/murmur/app_auth.py",
            "server/murmur/app_store.py",
            "server/murmur/app_settings.py",
            "server/murmur/app_push.py",
            "server/murmur/app_push_fcm.py",
            "server/murmur/app_attest_android.py",
            "server/murmur/app_new_contract.py",
            "server/tests/test_app_api.py",
            "server/murmur/config.py",
            "server/murmur/cli.py",
            "server/pyproject.toml",
            "murmur/app_api.py",
            "murmur/app_auth.py",
            "murmur/app_store.py",
            "murmur/app_settings.py",
            "murmur/app_attest_android.py",
            "murmur/app_push.py",
            "murmur/app_push_fcm.py",
            "tests/test_app_api.py",
            "murmur/config.py",
            "murmur/cli.py",
            "pyproject.toml",
        ):
            with self.subTest(path=path):
                self.assert_scope([path], server=True, ios=True, android=True)

    def test_governance_and_unknown_paths_fail_open_to_all_checks(self):
        for path in (
            ".github/workflows/repository.yml",
            "scripts/check/check_repository.py",
            "AGENTS.md",
            ".gitignore",
            "scripts/dev/setup-murmur.sh",
            "some-new-component/source.py",
            "server/../docs/not-really-docs.md",
            "/docs/absolute.md",
        ):
            with self.subTest(path=path):
                self.assert_scope([path], server=True, ios=True, android=True)

    def test_brand_changes_build_both_clients(self):
        self.assert_scope(["assets/brand/mark.svg"], ios=True, android=True)
        self.assert_scope(["brand/mark.svg"], ios=True, android=True)

    def test_renamed_or_deleted_paths_are_classified_without_filesystem_lookup(self):
        self.assert_scope(
            ["apps/ios/deleted.swift", "apps/android/renamed.kt"], ios=True, android=True
        )
        self.assert_scope(["server/murmur/app_removed.py"], server=True, ios=True, android=True)
        self.assert_scope(["murmur/engine.py", "server/murmur/engine.py"],
                          server=True)
        self.assert_scope(["MurmurApp/Views/Home.swift", "apps/ios/MurmurApp/Views/Home.swift"],
                          ios=True)

    @patch.object(ci_scope.subprocess, "run")
    def test_diff_uses_exact_shas_and_nul_delimiters_without_rename_collapse(self, run):
        base, head = "a" * 40, "b" * 40
        run.return_value.stdout = b"apps/ios/old.swift\0apps/android/new.kt\0docs/a\nb.md\0"
        self.assertEqual(ci_scope.changed_paths(base, head, root=ROOT),
                         ["apps/ios/old.swift", "apps/android/new.kt", "docs/a\nb.md"])
        run.assert_called_once_with(
            ["git", "diff", "--name-only", "-z", "--no-renames", base, head, "--"],
            cwd=ROOT, check=True, stdout=subprocess.PIPE,
        )

    @patch.object(ci_scope.subprocess, "run")
    def test_invalid_shas_never_reach_git(self, run):
        for base, head in (("--output=bad", "b" * 40), ("a" * 40, "HEAD"),
                           ("a" * 40, "b" * 40 + "; touch bad")):
            with self.subTest(base=base, head=head), self.assertRaises(ValueError):
                ci_scope.changed_paths(base, head, root=ROOT)
        run.assert_not_called()

    @patch.object(ci_scope.subprocess, "run", side_effect=subprocess.CalledProcessError(128, "git"))
    def test_diff_failure_cannot_create_successful_empty_scope(self, _run):
        with self.assertRaises(subprocess.CalledProcessError):
            ci_scope.changed_paths("a" * 40, "b" * 40, root=ROOT)

    def test_manual_and_initial_push_run_all_checks(self):
        self.assertEqual(ci_scope.select_checks("workflow_dispatch", "", "", root=ROOT),
                         ci_scope.all_checks())
        self.assertEqual(ci_scope.select_checks("push", "0" * 40, "b" * 40, root=ROOT),
                         ci_scope.all_checks())

    def test_unknown_event_is_rejected(self):
        with self.assertRaises(ValueError):
            ci_scope.select_checks("pull_request_target", "a" * 40, "b" * 40, root=ROOT)


class LayoutTests(unittest.TestCase):
    @patch.object(Path, "is_file", return_value=False)
    def test_legacy_layout_uses_fixed_old_paths(self, is_file):
        self.assertEqual(ci_scope.layout_paths(ROOT), {
            "project_install": ".[dev]",
            "python_config": "pyproject.toml",
            "package_path": "murmur",
            "tests_path": "tests",
            "test_runner": "scripts/run_tests.py",
            "ios_project": "MurmurApp.xcodeproj",
            "android_directory": "android",
        })
        is_file.assert_called_once_with()

    @patch.object(Path, "is_file", return_value=True)
    def test_migrated_layout_uses_fixed_new_paths(self, is_file):
        self.assertEqual(ci_scope.layout_paths(ROOT), {
            "project_install": "./server[dev]",
            "python_config": "server/pyproject.toml",
            "package_path": "server/murmur",
            "tests_path": "server/tests",
            "test_runner": "scripts/check/run_tests.py",
            "ios_project": "apps/ios/MurmurApp.xcodeproj",
            "android_directory": "apps/android",
        })
        is_file.assert_called_once_with()


class GateTests(unittest.TestCase):
    @staticmethod
    def needs(*, enabled=()):
        return {
            "scope": {"result": "success", "outputs": {
                name: "true" if name in enabled else "false" for name in ci_scope.CHECKS
            }},
            "repository": {"result": "success"},
            **{name: {"result": "success" if name in enabled else "skipped"}
               for name in ci_scope.CHECKS},
        }

    def test_doc_only_accepts_legitimate_skips(self):
        self.assertEqual(ci_scope.gate_errors(self.needs()), [])

    def test_selected_jobs_must_succeed(self):
        self.assertEqual(ci_scope.gate_errors(self.needs(enabled=ci_scope.CHECKS)), [])
        for result in ("skipped", "failure", "cancelled", "unknown", None):
            needs = self.needs(enabled=("server",))
            needs["server"]["result"] = result
            with self.subTest(result=result):
                self.assertTrue(ci_scope.gate_errors(needs))

    def test_unselected_cancelled_failed_or_unknown_jobs_fail(self):
        for result in ("failure", "cancelled", "unknown", None):
            needs = self.needs()
            needs["ios"]["result"] = result
            with self.subTest(result=result):
                self.assertTrue(ci_scope.gate_errors(needs))

    def test_repository_and_scope_cannot_be_skipped(self):
        for job in ("scope", "repository"):
            needs = self.needs()
            needs[job]["result"] = "skipped"
            with self.subTest(job=job):
                self.assertTrue(ci_scope.gate_errors(needs))

    def test_missing_invalid_or_extra_records_fail(self):
        needs = self.needs()
        del needs["android"]
        self.assertTrue(ci_scope.gate_errors(needs))
        needs = self.needs()
        needs["extra"] = {"result": "success"}
        self.assertTrue(ci_scope.gate_errors(needs))
        needs = self.needs()
        needs["android"] = "success"
        self.assertTrue(ci_scope.gate_errors(needs))

    def test_invalid_or_missing_scope_decisions_fail(self):
        for value in (None, "", "TRUE", True):
            needs = self.needs()
            needs["scope"]["outputs"]["server"] = value
            with self.subTest(value=value):
                self.assertTrue(ci_scope.gate_errors(needs))
        needs = self.needs()
        del needs["scope"]["outputs"]
        self.assertTrue(ci_scope.gate_errors(needs))


if __name__ == "__main__":
    unittest.main()
