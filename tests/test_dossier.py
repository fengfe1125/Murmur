"""dossier 落盘：原子写，不留半截文件。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import make_config  # noqa: E402

from murmur import dossier  # noqa: E402
from murmur.dossier import Dossier  # noqa: E402
from murmur.memory import Memory  # noqa: E402


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


class _Resp:
    def __init__(self, content: str):
        self.choices = [
            type("C", (), {"message": type("M", (), {"content": content})()})()
        ]


class _FakeClient:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []
        self.chat = type("X", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self.content)


class DossierRefreshPrefixTests(unittest.TestCase):
    def test_json_prefix_replaces_response_format(self):
        # cfg.json_prefix=True（deepseek 直连）：整理调用改走 assistant
        # prefix，引擎补回 "{"；json_object 挡不住它的长思考，不能再用。
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(
                db_path=str(Path(tmp) / "m.db"),
                model="deepseek-v4-flash",
                json_prefix=True,
            )
            mem = Memory(str(Path(tmp) / "memory.db"))
            mem.record(
                chat_id=7, thread="t", shot_at=None, bucket="午间",
                weekday="周六", spot=None, scene="测试", move="speak",
                said="出门了", note=None,
            )
            client = _FakeClient(
                '"他是谁": "- 他周末会出门", "正在发生": "- 在散步", '
                '"怎么跟他说话": "- 随意点"}'
            )
            with patch.object(dossier, "_client", return_value=client):
                d = dossier.refresh(cfg, mem, 7, "t",
                                    root=Path(tmp), force=True)
            self.assertIsNotNone(d)
            self.assertEqual(d.blocks["他是谁"], "- 他周末会出门")
            messages = client.calls[0]["messages"]
            self.assertEqual(
                messages[-1],
                {"role": "assistant", "content": "{", "prefix": True},
            )
            self.assertNotIn("response_format", client.calls[0])
            mem.conn.close()


if __name__ == "__main__":
    unittest.main()
