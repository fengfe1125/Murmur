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
    def __init__(self, content: str, finish_reason: str | None = None):
        self.choices = [
            type(
                "C",
                (),
                {
                    "message": type("M", (), {"content": content})(),
                    "finish_reason": finish_reason,
                },
            )()
        ]


class _FakeClient:
    def __init__(self, content: str, finish_reason: str | None = None):
        self.content = content
        self.finish_reason = finish_reason
        self.calls: list[dict] = []
        self.chat = type("X", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self.content, self.finish_reason)


class DossierRefreshPrefixTests(unittest.TestCase):
    def test_json_prefix_replaces_response_format(self):
        # cfg.json_prefix=True（deepseek 直连）：整理调用改走 assistant
        # prefix，引擎补回 "{"；json_object 挡不住它的长思考，不能再用。
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(
                db_path=str(Path(tmp) / "m.db"),
                model="deepseek-v4-flash",
                json_prefix=True,
                temperature=1.1,
                presence_penalty=0.3,
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
            self.assertEqual(d.covered_upto, 1)
            messages = client.calls[0]["messages"]
            self.assertEqual(
                messages[-1],
                {"role": "assistant", "content": "{", "prefix": True},
            )
            self.assertNotIn("response_format", client.calls[0])
            self.assertNotIn("temperature", client.calls[0])
            self.assertNotIn("presence_penalty", client.calls[0])
            # deepseek 的思考会先烧掉一截 token，整理上限不能卡在 1800。
            self.assertEqual(client.calls[0]["max_tokens"], 3000)
            mem.conn.close()


def _seed_entry(mem: Memory, chat_id: int = 7, thread: str = "t") -> None:
    mem.record(
        chat_id=chat_id, thread=thread, shot_at=None, bucket="午间",
        weekday="周六", spot=None, scene="测试", move="speak",
        said="出门了", note=None,
    )


class DossierRefreshRobustnessTests(unittest.TestCase):
    """整理记忆是「越聊越懂你」的命根子，线上却每次都整个丢掉。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = make_config(
            db_path=str(self.root / "m.db"),
            model="deepseek-v4-flash",
            json_prefix=True,
        )
        self.mem = Memory(str(self.root / "memory.db"))
        _seed_entry(self.mem)

    def tearDown(self):
        self.mem.conn.close()
        self.tmp.cleanup()

    def _refresh(self, content: str):
        client = _FakeClient(content)
        with patch.object(dossier, "_client", return_value=client):
            return dossier.refresh(
                self.cfg, self.mem, 7, "t", root=self.root, force=True
            )

    def test_prose_wrapped_json_is_extracted(self):
        # 模型在 JSON 前后裹废话：抓第一个花括号平衡的片段照样能用。
        d = self._refresh(
            '好的，整理好了：\n{"他是谁": "- 他住杭州", "正在发生": "- 在散步", '
            '"怎么跟他说话": "- 随意点"}\n希望对你有帮助'
        )
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["他是谁"], "- 他住杭州")
        self.assertEqual(d.covered_upto, 1)

    def test_wrapped_json_ignores_closing_brace_inside_a_partition_string(self):
        d = self._refresh(
            '整理如下：{"他是谁": "- 常用 } 表情", "正在发生": "- 在散步", '
            '"怎么跟他说话": "- 随意点"} 完成'
        )
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["他是谁"], "- 常用 } 表情")
        self.assertEqual(d.covered_upto, 1)

    def test_prefix_mode_accepts_an_already_complete_json_object(self):
        d = self._refresh(
            '{"他是谁": "- 他住杭州", "正在发生": "- 在散步", '
            '"怎么跟他说话": "- 随意点"}'
        )
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["正在发生"], "- 在散步")
        self.assertEqual(d.covered_upto, 1)

    def test_unrelated_dict_does_not_clobber_existing_blocks(self):
        # 模型返回了合法 JSON 但不是分区（比如把 say 数组包回来）：
        # 不能走「缺省刷成（还不知道）」的路径，那会把旧记忆清空。
        old = Dossier(thread="t", path=self.root / "t.md")
        old.blocks = {"他是谁": "- 他住杭州"}
        old.save()
        d = self._refresh('{"move": "speak", "say": "- 他周末会出门"}')
        self.assertIsNone(d)
        self.assertEqual(
            Dossier.load(self.root, "t").blocks["他是谁"], "- 他住杭州"
        )

    def test_truncated_json_salvages_only_the_finished_blocks(self):
        # 响应在第二个分区写到一半被 max_tokens 截断：写完的分区收下，
        # 没写完的保持旧内容，covered_upto 不前进，剩下的下轮再消化。
        old = Dossier(thread="t", path=self.root / "t.md")
        old.blocks = {"正在发生": "- 旧内容别动"}
        old.save()
        d = self._refresh('{"他是谁": "- 他住杭州", "正在发生": "- 在找工')
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["他是谁"], "- 他住杭州")
        self.assertEqual(d.blocks["正在发生"], "- 旧内容别动")
        self.assertEqual(d.covered_upto, 0)

    def test_complete_object_with_two_blocks_is_merged_as_partial(self):
        # JSON 语法完整不等于三个分区都整理完：缺一块时不得
        # 清空旧内容，也不得跳过这批 entries。
        old = Dossier(thread="t", path=self.root / "t.md")
        old.blocks = {"怎么跟他说话": "- 不要追问"}
        old.save()
        d = self._refresh(
            '{"他是谁": "- 他住杭州", "正在发生": "- 在找工作"}'
        )
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["他是谁"], "- 他住杭州")
        self.assertEqual(d.blocks["怎么跟他说话"], "- 不要追问")
        self.assertEqual(d.covered_upto, 0)

    def test_all_three_blocks_must_be_strings_before_advancing(self):
        old = Dossier(thread="t", path=self.root / "t.md")
        old.blocks = {"正在发生": "- 旧内容别动"}
        old.save()
        d = self._refresh(
            '{"他是谁": "- 他住杭州", "正在发生": ["- 在找工作"], '
            '"怎么跟他说话": "- 随意点"}'
        )
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["正在发生"], "- 旧内容别动")
        self.assertEqual(d.covered_upto, 0)

    def test_fullwidth_truncated_json_still_salvages(self):
        # deepseek 的全角病和截断病会同时发作。
        d = self._refresh('{“他是谁”: “- 他住杭州”, “正在发生”: “- 在找工')
        self.assertIsNotNone(d)
        self.assertEqual(d.blocks["他是谁"], "- 他住杭州")

    def test_failure_logs_structured_metadata_without_model_text(self):
        secret = "PRIVATE_MODEL_OUTPUT_9f73"
        client = _FakeClient(
            f'{{"move": "speak", "say": "{secret}"}}',
            finish_reason="length",
        )
        with self.assertLogs("murmur.dossier", level="WARNING") as captured:
            with patch.object(dossier, "_client", return_value=client):
                result = dossier.refresh(
                    self.cfg, self.mem, 7, "t", root=self.root, force=True
                )
        self.assertIsNone(result)
        logs = "\n".join(captured.output)
        self.assertIn("model=deepseek-v4-flash", logs)
        self.assertIn("attempt=1", logs)
        self.assertIn("finish_reason=length", logs)
        self.assertIn("length=", logs)
        self.assertIn("category=unrecognized_object", logs)
        self.assertNotIn(secret, logs)

    def test_pending_delivery_is_a_cursor_barrier(self):
        pending_id = self.mem.record(
            chat_id=7, thread="t", shot_at=None, bucket="午间",
            weekday="周六", spot=None, scene="不能归档", move="speak",
            said="还没送达", note=None, kind="out", delivery_state="pending",
        )
        later_id = self.mem.record(
            chat_id=7, thread="t", shot_at=None, bucket="午间",
            weekday="周六", spot=None, scene="稍后的消息", move="speak",
            said="已提交", note=None,
        )
        self.assertEqual((pending_id, later_id), (2, 3))

        first = self._refresh(
            '{"他是谁": "- 他住杭州", "正在发生": "- 在散步", '
            '"怎么跟他说话": "- 随意点"}'
        )
        self.assertEqual(first.covered_upto, 1)

        self.assertTrue(self.mem.commit_outbound_entry(pending_id, 7))
        second = self._refresh(
            '{"他是谁": "- 他住杭州", "正在发生": "- 在散步", '
            '"怎么跟他说话": "- 随意点"}'
        )
        self.assertEqual(second.covered_upto, 3)


if __name__ == "__main__":
    unittest.main()
