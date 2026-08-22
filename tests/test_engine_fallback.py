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

from murmur.engine import (  # noqa: E402
    Reply,
    _extract_json,
    _salvage_bubbles,
    initiate,
    respond,
)
from murmur.initiative import INTENTS  # noqa: E402
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
            error = self.errors[model]
            # 队列形式：按调用次序逐个消费，None 表示这一次不抛。
            if isinstance(error, list):
                error = error.pop(0) if error else None
                if error is None:
                    return _Resp(self.content_by_model.get(model, GOOD_JSON))
            raise error
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

    def _no_fallback_config(self, name: str = "m2.db"):
        return make_config(
            db_path=str(Path(self.tmp.name) / name),
            model="kimi-k2.6",
            fallback_model="",
            image_model="",
        )

    def test_no_fallback_configured_still_tries_twice_then_raises(self):
        # 没有第二家可换的时候（直连 deepseek 就一家）也要再试一次，
        # 两次都挂才把错误抛给 worker——只有那时他才该看到发送失败。
        cfg = self._no_fallback_config()
        client = FakeClient({}, errors={"kimi-k2.6": gateway_error()})
        with patch("murmur.engine._client", return_value=client):
            with self.assertRaises(openai.InternalServerError):
                respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual([c["model"] for c in client.calls],
                         ["kimi-k2.6", "kimi-k2.6"])

    def test_one_off_gateway_error_no_longer_costs_him_the_message(self):
        # 线上真实故障：网关偶发 5xx，没配降级模型，一条消息就此变成
        # 红色感叹号。同一个模型再来一次就接住了。
        cfg = self._no_fallback_config("m2b.db")
        client = FakeClient(
            {"kimi-k2.6": PRIMARY_JSON},
            errors={"kimi-k2.6": [gateway_error(), None]},
        )
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["主模型说的"])
        self.assertEqual(len(client.calls), 2)

    def test_bare_bubble_array_is_read_as_bubbles(self):
        # 线上真实故障：模型把信封丢了，只吐 say 的那个数组，抢救逻辑
        # 把 '["七点多啦","…"]' 整个当成一句话发了出去。
        cfg = self._no_fallback_config("m2c.db")
        client = FakeClient(
            {"kimi-k2.6": '["七点多啦","这会儿是到家了还是在路上？"]'}
        )
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["七点多啦", "这会儿是到家了还是在路上？"])
        self.assertEqual(len(client.calls), 1)

    def test_json_prefix_pins_the_first_char(self):
        # MURMUR_JSON_PREFIX=1（deepseek 直连）：回复的第一个字符被
        # assistant prefix 钉成 "{"，模型只续写，引擎把 "{" 补回去。
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m4.db"),
            model="deepseek-v4-flash",
            fallback_model="",
            image_model="",
            json_schema=False,
            json_prefix=True,
        )
        client = FakeClient(
            {"deepseek-v4-flash":
             '"move":"speak","say":["续写出来的"],"scene":"测试"}'}
        )
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["续写出来的"])
        self.assertEqual(
            client.calls[0]["messages"][-1],
            {"role": "assistant", "content": "{", "prefix": True},
        )
        self.assertNotIn("response_format", client.calls[0])


class ExtractJsonTests(unittest.TestCase):
    def test_fullwidth_quotes_are_normalized_after_plain_parse_fails(self):
        # deepseek 偶尔拿全角引号当 JSON 定界符，正常解析失败后兜底一把
        obj = _extract_json(
            '{"move": “speak”, “say”: [“哪句呀”], “scene”: “测试”}'
        )
        self.assertEqual(obj["say"], ["哪句呀"])

    def test_fullwidth_quotes_without_object_still_raise(self):
        with self.assertRaises(ValueError):
            _extract_json("“这里没有花括号”")

    def test_bare_array_of_strings_is_wrapped_as_a_reply(self):
        obj = _extract_json('["七点多啦","这会儿是到家了还是在路上？"]')
        self.assertEqual(obj["say"], ["七点多啦", "这会儿是到家了还是在路上？"])
        self.assertEqual(obj["move"], "speak")

    def test_bare_array_of_non_strings_is_not_a_reply(self):
        with self.assertRaises(ValueError):
            _extract_json("[1, 2, 3]")


class SalvageTests(unittest.TestCase):
    """抢救的边界：捞得回气泡就捞，捞不回来宁可交给下一次尝试。"""

    def test_truncated_json_yields_the_finished_bubbles(self):
        self.assertEqual(
            _salvage_bubbles('{"move":"speak","say":["写完了","写到一半', 3),
            ["写完了"],
        )

    def test_plain_spoken_lines_are_kept(self):
        self.assertEqual(_salvage_bubbles("怎么又这么晚\n早点睡", 3),
                         ["怎么又这么晚", "早点睡"])

    def test_half_written_array_is_not_read_out_loud(self):
        # 抢救的目的是把气泡捞回来，不是把 JSON 残骸念给他听。
        self.assertEqual(_salvage_bubbles('["七点多啦",', 3), [])

    def test_a_stray_key_value_line_is_not_a_bubble(self):
        self.assertEqual(_salvage_bubbles('"scene": 周四傍晚', 3), [])


class InitiateTests(unittest.TestCase):
    """主动开口：和普通回复走同一套 JSON 纪律，之前只有这里漏了 prefix。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(str(Path(self.tmp.name) / "memory.db"))
        self.cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m.db"),
            model="deepseek-v4-flash",
            fallback_model="",
            image_model="",
            json_schema=False,
            json_prefix=True,
        )
        self.moment = Moment.text_only(self.cfg.tz)

    def tearDown(self):
        self.mem.conn.close()
        self.tmp.cleanup()

    def _call(self, client):
        with patch("murmur.engine._client", return_value=client):
            return initiate(self.moment, self.mem, self.cfg, INTENTS[0], chat_id=0)

    def test_json_prefix_pins_the_first_char(self):
        client = FakeClient(
            {"deepseek-v4-flash":
             '"move":"speak","say":["七点多啦","到家了吗"],"scene":"测试"}'}
        )
        reply = self._call(client)
        self.assertEqual(reply.say, ["七点多啦", "到家了吗"])
        self.assertEqual(
            client.calls[0]["messages"][-1],
            {"role": "assistant", "content": "{", "prefix": True},
        )

    def test_prefix_is_not_added_when_the_schema_is_doing_the_work(self):
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m2.db"),
            model="kimi-k2.6",
            fallback_model="",
            image_model="",
            json_schema=True,
            json_prefix=True,
        )
        client = FakeClient({"kimi-k2.6": PRIMARY_JSON})
        with patch("murmur.engine._client", return_value=client):
            initiate(self.moment, self.mem, cfg, INTENTS[0], chat_id=0)
        self.assertEqual(client.calls[0]["messages"][-1]["role"], "user")
        self.assertIn("response_format", client.calls[0])


if __name__ == "__main__":
    unittest.main()
