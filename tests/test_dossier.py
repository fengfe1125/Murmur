"""dossier 落盘：原子写，不留半截文件。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.dossier import Dossier  # noqa: E402


class DossierSaveTests(unittest.TestCase):
    def test_save_is_atomic_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dossier = Dossier(thread="t", path=root / "t.md")
            dossier.blocks = {"他是谁": "- 他住杭州"}
            dossier.save()
            loaded = Dossier.load(root, "t")
            self.assertEqual(loaded.blocks["他是谁"], "- 他住杭州")
            # os.replace 之后目录里不能有残留的 .tmp——有就说明写路径不是原子的。
            self.assertEqual([p.name for p in root.iterdir()], ["t.md"])

    def test_save_overwrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "t.md"
            path.write_text("旧内容", encoding="utf-8")
            dossier = Dossier(thread="t", path=path)
            dossier.blocks = {"正在发生": "- 在找工作"}
            dossier.save()
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("旧内容", text)
            self.assertIn("在找工作", text)


if __name__ == "__main__":
    unittest.main()
