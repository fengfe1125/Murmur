"""Test the repository checker with known-good and deliberately broken documents."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if ROOT.name == "server":
    ROOT = ROOT.parent
SPEC = importlib.util.spec_from_file_location("repository_checks", ROOT / "scripts/check/check_repository.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)
HEADER = "# Fixture\n\n状态：现行规范｜适用：测试｜核验：2026-08-28｜依据：合成数据\n\n"


class RepositoryChecksTests(unittest.TestCase):
    def check_document(self, body: str, *, child: bool = False) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="murmur-document-test.") as directory:
            root = Path(directory)
            (root / "target.md").write_text(HEADER, encoding="utf-8")
            parent = root / "docs" if child else root
            parent.mkdir(exist_ok=True)
            document = parent / "README.md"
            document.write_text(body, encoding="utf-8")
            return checks.document_errors(document, root)

    def test_valid_document_is_accepted(self):
        self.assertEqual(self.check_document(HEADER + "[target](target.md#fixture)"), [])

    def test_nested_relative_link_is_accepted(self):
        self.assertEqual(self.check_document(HEADER + "[target](../target.md)", child=True), [])

    def test_missing_metadata_is_rejected(self):
        self.assertTrue(self.check_document("# Title\n"))

    def test_missing_link_is_rejected(self):
        self.assertTrue(self.check_document(HEADER + "[missing](missing.md)"))

    def test_absolute_machine_link_is_rejected(self):
        self.assertTrue(self.check_document(HEADER + "[private](/Users/example/file.md)"))

    def test_escape_from_repository_is_rejected(self):
        self.assertTrue(self.check_document(HEADER + "[escape](../../outside.md)"))

    def test_examples_and_external_links_are_not_local_navigation(self):
        body = HEADER + "```md\n[example](missing.md)\n```\n[web](https://example.invalid/path)"
        self.assertEqual(self.check_document(body), [])


if __name__ == "__main__":
    unittest.main()
