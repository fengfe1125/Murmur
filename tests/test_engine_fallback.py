"""模型降级备案的回归测试：主模型挂了自动换 deepseek-v4-flash，
带图消息走 mimo 多模态模型，降级调用不带 json_schema、不看图。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai  # noqa: E402
from _helpers import make_config  # noqa: E402

from murmur.engine import Reply, respond  # noqa: E402
from murmur.memory import Memory  # noqa: E402
from murmur.moment import Moment  # noqa: E402
from murmur.photo import Photo  # noqa: E402

GOOD_JSON = '{"move":"speak","say":["这条是降级模型说的"],"scene":"测试"}'
PRIMARY_JSON = '{"move":"speak","say":["主模型说的"],"scene":"测试"}'


class _Msg:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


class _StreamChunk:
    def __init__(self, content: str):
        self.choices = [type("C", (), {"delta": type("D", (), {"content": content})()})()]


class _HttpResponse:
    """openai 3.x 的 APIStatusError 需要 response.request/status_code/headers。"""

    def __init__(self):
        self.request = None
        self.status_code = 503
        self.headers = {}


def gateway_error() -> openai.InternalServerError:
    return openai.InternalServerError("boom", response=_HttpResponse(), body=None)


class FakeClient:
    """按模型名排队返回内容 / 抛错，并记录每次 create 的 kwargs。"""

    def __init__(self, content_by_model: dict[str, str],
                 errors: dict[str, Exception] | None = None):
        self.content_by_model = content_by_model
        self.errors = errors or {}
        self.calls: list[dict] = []
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, owner):
            self.completions = owner

    def create(self, **kwargs):
        self.calls.append(kwargs)
        model = kwargs["model"]
        if model in self.errors:
            raise self.errors[model]
        content = self.content_by_model.get(
            model, '{"move":"brief","say":["默认回复"],"scene":"测试"}'
        )
        if kwargs.get("stream"):
            return [_StreamChunk(content)]
        return _Resp(content)


class EngineFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m.db"),
            model="kimi-k2.6",
            fallback_model="deepseek-v4-flash",
            image_model="mimo-v2.5",
        )
        self.mem = Memory(str(Path(self.tmp.name) / "memory.db"))
        self.moment = Moment.text_only(self.cfg.tz)

    def tearDown(self):
        self.mem.conn.close()
        self.tmp.cleanup()

    def _call(self, client):
        with patch("murmur.engine._client", return_value=client):
            return respond(self.moment, self.mem, self.cfg,
                           note="测试", chat_id=0, on_bubble=None)

    def test_text_primary_ok_no_fallback(self):
        client = FakeClient({"kimi-k2.6": PRIMARY_JSON})
        reply = self._call(client)
        self.assertEqual(reply.say, ["主模型说的"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["model"], "kimi-k2.6")
        self.assertIn("response_format", client.calls[0])

    def test_text_gateway_error_falls_back_without_schema(self):
        client = FakeClient(
            {"kimi-k2.6": PRIMARY_JSON, "deepseek-v4-flash": GOOD_JSON},
            errors={"kimi-k2.6": gateway_error()},
        )
        reply = self._call(client)
        self.assertEqual(reply.say, ["这条是降级模型说的"])
        self.assertEqual([c["model"] for c in client.calls],
                         ["kimi-k2.6", "deepseek-v4-flash"])
        self.assertIn("response_format", client.calls[0])
        self.assertNotIn("response_format", client.calls[1])

    def test_text_brace_garbage_falls_back(self):
        # 带花括号但不是 JSON 的胡话：不进纯文本抢救，交给降级模型。
        client = FakeClient(
            {"kimi-k2.6": "他说 {这不是 JSON", "deepseek-v4-flash": GOOD_JSON},
        )
        reply = self._call(client)
        self.assertEqual(reply.say, ["这条是降级模型说的"])
        self.assertEqual([c["model"] for c in client.calls],
                         ["kimi-k2.6", "deepseek-v4-flash"])

    def test_image_uses_image_model_first_without_schema(self):
        photo = Photo(None, None, None, None, "test", "QUJD")
        client = FakeClient({"mimo-v2.5": GOOD_JSON})
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, self.cfg,
                            photo=photo, note="看图", chat_id=0)
        self.assertEqual(reply.say, ["这条是降级模型说的"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["model"], "mimo-v2.5")
        self.assertNotIn("response_format", client.calls[0])
        user_content = client.calls[0]["messages"][-1]["content"]
        self.assertTrue(any(x.get("type") == "image_url" for x in user_content))

    def test_image_model_failure_falls_back_text_only(self):
        photo = Photo(None, None, None, None, "test", "QUJD")
        client = FakeClient(
            {"mimo-v2.5": GOOD_JSON, "deepseek-v4-flash": GOOD_JSON},
            errors={"mimo-v2.5": gateway_error()},
        )
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, self.cfg,
                            photo=photo, note="看图", chat_id=0)
        self.assertEqual(reply.say, ["这条是降级模型说的"])
        self.assertEqual([c["model"] for c in client.calls],
                         ["mimo-v2.5", "deepseek-v4-flash"])
        # 降级模型不看图：请求里不能再带 image_url
        fallback_content = client.calls[1]["messages"][-1]["content"]
        self.assertFalse(any(x.get("type") == "image_url" for x in fallback_content))
        self.assertIsInstance(reply, Reply)

    def test_plain_text_reply_is_salvaged_not_discarded(self):
        # kimi-k2.6 在 OpenCode 网关上偶尔无视 json_schema 直接吐纯文本：
        # 内容是对的，必须收下，不能当垃圾丢掉。
        client = FakeClient({"kimi-k2.6": "在呢，怎么啦"})
        reply = self._call(client)
        self.assertEqual(reply.say, ["在呢，怎么啦"])
        self.assertEqual(len(client.calls), 1)

    def test_history_joiner_is_never_shown_to_the_user(self):
        # 历史里多条气泡用 ' ⏎ ' 拼接喂给模型，它偶尔会原样模仿回来。
        # 无论走 JSON、纯文本抢救还是流式哪条路，⏎ 都不能发给他。
        client = FakeClient(
            {"kimi-k2.6": '{"move":"speak","say":["摸！ ⏎ 吃饱了正是一天里最该摸的时候 ⏎"],"scene":"测试"}'}
        )
        reply = self._call(client)
        self.assertEqual(reply.say, ["摸！", "吃饱了正是一天里最该摸的时候"])

        client = FakeClient({"kimi-k2.6": "摸！ ⏎ 吃饱了正是一天里最该摸的时候"})
        reply = self._call(client)
        self.assertEqual(reply.say, ["摸！", "吃饱了正是一天里最该摸的时候"])

    def test_streamed_bubbles_split_the_history_joiner(self):
        client = FakeClient(
            {"kimi-k2.6": '{"move":"speak","say":["摸！ ⏎ 多吃点"],"scene":"测试"}'}
        )
        streamed: list[str] = []
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, self.cfg,
                            note="测试", chat_id=0, on_bubble=streamed.append)
        self.assertEqual(streamed, ["摸！", "多吃点"])
        self.assertEqual(reply.say, ["摸！", "多吃点"])

    def test_long_prose_without_json_still_falls_back(self):
        # 没有花括号的长篇思考痕迹不能当气泡发出去，交给降级模型。
        client = FakeClient(
            {"kimi-k2.6": "用户希望我根据这张照片回复一句话" * 12,
             "deepseek-v4-flash": GOOD_JSON},
        )
        reply = self._call(client)
        self.assertEqual(reply.say, ["这条是降级模型说的"])
        self.assertEqual(len(client.calls), 2)

    def test_json_schema_off_primary_drops_response_format(self):
        # glm / deepseek 系不支持 response_format：MURMUR_JSON_SCHEMA=0
        # 时主模型调用不带 json_schema，直接靠提示词约束。
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m3.db"),
            model="glm-5.3",
            fallback_model="deepseek-v4-flash",
            image_model="mimo-v2.5",
            json_schema=False,
        )
        client = FakeClient({"glm-5.3": PRIMARY_JSON})
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["主模型说的"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["model"], "glm-5.3")
        self.assertNotIn("response_format", client.calls[0])

    def test_no_fallback_configured_raises(self):
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m2.db"),
            model="kimi-k2.6",
            fallback_model="",
            image_model="",
        )
        client = FakeClient({}, errors={"kimi-k2.6": gateway_error()})
        with patch("murmur.engine._client", return_value=client):
            with self.assertRaises(openai.InternalServerError):
                respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
