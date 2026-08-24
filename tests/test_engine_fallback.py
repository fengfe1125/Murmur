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
    _guard_bubbles,
    _output_error,
    _reply_style_note,
    _salvage_bubbles,
    initiate,
    read_photo,
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
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


class _StreamChunk:
    def __init__(self, content: str):
        self.choices = [type("C", (), {
            "delta": type("D", (), {"content": content})(),
            "finish_reason": None,
        })()]


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
        if isinstance(content, list):
            content = content.pop(0)
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
        self.assertNotIn("temperature", client.calls[0])
        self.assertNotIn("presence_penalty", client.calls[0])
        self.assertNotIn("response_format", client.calls[0])

    def test_sampling_params_are_sent_only_when_configured(self):
        # MURMUR_TEMPERATURE / MURMUR_PRESENCE_PENALTY：配了就带上，
        # 没配一个都不带——不支持的网关不该被塞陌生参数。
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m5.db"),
            model="deepseek-v4-flash",
            fallback_model="",
            image_model="",
            json_schema=False,
            json_prefix=True,
            temperature=1.1,
            presence_penalty=0.3,
        )
        client = FakeClient(
            {"deepseek-v4-flash":
             '"move":"speak","say":["鲜活起来的"],"scene":"测试"}'}
        )
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["鲜活起来的"])
        self.assertEqual(client.calls[0]["temperature"], 1.1)
        self.assertEqual(client.calls[0]["presence_penalty"], 0.3)

        client = FakeClient({"kimi-k2.6": PRIMARY_JSON})
        reply = self._call(client)
        self.assertNotIn("temperature", client.calls[0])
        self.assertNotIn("presence_penalty", client.calls[0])

    def test_all_reply_shapes_pass_through_the_same_emoji_guard(self):
        cases = [
            '{"move":"speak","say":["收到啦☀️"],"scene":"测试"}',
            '["收到啦😊"]',
            "收到啦❤️",
        ]
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                cfg = self._no_fallback_config(f"guard-{index}.db")
                client = FakeClient({"kimi-k2.6": raw})
                with patch("murmur.engine._client", return_value=client):
                    reply = respond(self.moment, self.mem, cfg,
                                    note="测试", chat_id=0)
                self.assertEqual(reply.say, ["收到啦"])

    def test_json_debris_inside_say_is_never_read_out(self):
        cfg = self._no_fallback_config("debris.db")
        client = FakeClient({
            "kimi-k2.6": [
                '{"move":"speak","say":["\\\"scene\\\": 半截"],"scene":""}',
                '{"move":"speak","say":["这次是干净的"],"scene":""}',
            ]
        })
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["这次是干净的"])
        self.assertEqual(len(client.calls), 2)

    def test_explicit_photo_quiet_remains_a_valid_non_output(self):
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "photo-quiet.db"),
            model="kimi-k2.6", fallback_model="", image_model="mimo-v2.5",
            json_schema=False,
        )
        client = FakeClient({
            "mimo-v2.5": '{"move":"quiet","say":[],"scene":"只是普通桌面"}'
        })
        photo = Photo(None, None, None, None, "test", "QUJD")
        with patch("murmur.engine._client", return_value=client):
            reply = respond(
                self.moment, self.mem, cfg, photo=photo, note=None, chat_id=0
            )
        self.assertTrue(reply.silent)
        self.assertEqual(reply.scene, "只是普通桌面")

    def test_text_only_quiet_with_content_is_still_delivered(self):
        # 模型偶尔会说了话却把 move 标成 quiet。reply.silent 会让每个调用方
        # 直接不发，用户那边就是"发消息没反应"——最劝退的一种失败。
        cfg = self._no_fallback_config("text-quiet.db")
        client = FakeClient({
            "kimi-k2.6": '{"move":"quiet","say":["我在的，怎么了"],"scene":"（纯文字）"}'
        })
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="在吗", chat_id=0)
        self.assertEqual(reply.say, ["我在的，怎么了"])
        self.assertEqual(reply.move, "brief")
        self.assertFalse(reply.silent)

    def test_text_only_empty_say_still_fails_instead_of_faking_a_reply(self):
        # 上面那道兜底只救"说了话却标 quiet"，不能顺手把空回复
        # 变成罐头"嗯"——空的仍然要走重试/失败链。
        cfg = self._no_fallback_config("text-empty.db")
        client = FakeClient({
            "kimi-k2.6": '{"move":"quiet","say":[],"scene":"（纯文字）"}'
        })
        with patch("murmur.engine._client", return_value=client):
            with self.assertRaises(ValueError):
                respond(self.moment, self.mem, cfg, note="在吗", chat_id=0)

    def test_proactive_duplicate_is_suppressed_without_directives(self):
        # 主动消息去重比 MURMUR_REPLY_DIRECTIVES 早得多，默认就该生效，
        # 否则连着两条"在干嘛"会直接发出去。
        cfg = self._no_fallback_config("proactive-dup.db")
        self.assertFalse(cfg.reply_directives)
        self.mem.record(
            chat_id=0, thread="t", shot_at=None, bucket="午后", weekday="周一",
            spot=None, scene="s", move="speak", said="在干嘛呢", note=None,
            kind="out", intent="在干嘛",
        )
        client = FakeClient({
            "kimi-k2.6": '{"move":"speak","say":["在干嘛呢"],"scene":"测试"}'
        })
        with patch("murmur.engine._client", return_value=client):
            reply = initiate(self.moment, self.mem, cfg, INTENTS[0], chat_id=0)
        self.assertEqual(reply.say, [])
        self.assertTrue(reply.silent)

    def test_last_attempt_delivers_rather_than_veto_on_similarity(self):
        # initiate 判定太像可以安静跳过，respond 不行——它只能把异常抛给
        # 调用方，用户会收到"（出错了：_TooSimilar）"。最后一次尝试宁可
        # 发一条重一点的回复。
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "similar-last.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            reply_directives=True,
        )
        self.mem.record(
            chat_id=0, thread="t", shot_at=None, bucket="午后", weekday="周一",
            spot=None, scene="（纯文字）", move="speak", said="今天也挺累的吧",
            note="累死了", kind="in",
        )
        client = FakeClient({
            "kimi-k2.6": '{"move":"speak","say":["今天也挺累的吧"],"scene":"测试"}'
        })
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="又加班", chat_id=0)
        # 第一次否决并重试，第二次（最后一次）照发。
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(reply.say, ["今天也挺累的吧"])
        self.assertFalse(reply.silent)

    def test_salvaged_stream_reply_only_reports_delivered_bubbles(self):
        # 成功路径用 `say = emitted` 对账，salvage 路径也必须对账：salvage
        # 是从 raw 重新捞的，会把被 previous_exact 拦下、故意没发的重复气泡
        # 一起捞回来，而调用方会照着 reply.say 补发一遍。
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "salvage-emitted.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            reply_directives=True,
        )
        self.mem.record(
            chat_id=0, thread="t", shot_at=None, bucket="午后", weekday="周一",
            spot=None, scene="（纯文字）", move="speak", said="我也刚到家",
            note="到家了", kind="in",
        )
        # 第二条和最近历史逐字相同 -> 流式拦下不发；JSON 又截断走 salvage。
        truncated = '{"move":"speak","say":["先去洗个澡","我也刚到家"'
        client = FakeClient({"kimi-k2.6": truncated})
        seen: list[str] = []
        with patch("murmur.engine._client", return_value=client):
            reply = respond(
                self.moment, self.mem, cfg, note="累死了", chat_id=0,
                on_bubble=seen.append,
            )
        self.assertEqual(seen, ["先去洗个澡"])
        self.assertEqual(reply.say, seen)
        self.assertNotIn("我也刚到家", reply.say)

    def test_directive_tells_proactive_apart_from_a_wordless_photo(self):
        # 用户发一张不带配文的图时 note 也是 None。用"没有文字"去猜主动开口，
        # 会让它对着用户发起的这一轮解释"我为什么找你"。
        history = [{"role": "assistant", "content": "昨天那事怎么样了"}]
        proactive = _reply_style_note(history, proactive=True)
        wordless_photo = _reply_style_note(history, None)
        short_text = _reply_style_note(history, "是的")

        self.assertIn("主动开口", proactive)
        self.assertNotIn("主动开口", wordless_photo)
        self.assertIn("图", wordless_photo)
        self.assertNotIn("主动开口", short_text)
        self.assertIn("不脑补背景", short_text)

    def test_parse_failure_log_is_structured_and_never_contains_raw(self):
        cfg = self._no_fallback_config("safe-log.db")
        secret = "PRIVATE_RAW_MODEL_TEXT"
        client = FakeClient({"kimi-k2.6": secret * 20})
        with patch("murmur.engine._client", return_value=client):
            with self.assertLogs("murmur.engine", level="WARNING") as captured:
                with self.assertRaises(ValueError):
                    respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        joined = "\n".join(captured.output)
        self.assertNotIn(secret, joined)
        self.assertIn("model=kimi-k2.6", joined)
        self.assertIn("attempt=1", joined)
        self.assertIn("category=non_json", joined)
        self.assertIn("length=", joined)
        self.assertIn("finish_reason=stop", joined)

    def test_parse_failures_have_aggregate_safe_categories(self):
        self.assertEqual(_output_error("", "stop").category, "empty")
        self.assertEqual(
            _output_error('{"say":["写到一半', "length").category,
            "truncated",
        )
        self.assertEqual(_output_error("直接说人话", "stop").category, "non_json")
        self.assertEqual(_output_error("{broken", "stop").category, "invalid_json")

    def test_style_note_is_after_history_and_before_current_user(self):
        self.mem.record(
            chat_id=0, shot_at=None, bucket=None, weekday=None, spot=None,
            scene="（纯文字）", move="speak", said="怎么了呀 ⏎ 说说呗",
            note="旧消息", kind="in",
        )
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "style.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            reply_directives=True,
        )
        client = FakeClient({"kimi-k2.6": PRIMARY_JSON})
        with patch("murmur.engine._client", return_value=client):
            respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        messages = client.calls[0]["messages"]
        style_index = next(i for i, m in enumerate(messages)
                           if m["role"] == "system" and "本轮回复要求" in m["content"])
        history_index = max(i for i, m in enumerate(messages[:style_index])
                            if m["role"] == "assistant")
        user_index = next(i for i in range(style_index + 1, len(messages))
                          if messages[i]["role"] == "user")
        self.assertLess(history_index, style_index)
        self.assertLess(style_index, user_index)
        self.assertIn("怎么了呀", messages[style_index]["content"])

    def test_non_streaming_similarity_retries_once(self):
        self.mem.record(
            chat_id=0, shot_at=None, bucket=None, weekday=None, spot=None,
            scene="（纯文字）", move="speak", said="怎么又这么晚",
            note="旧消息", kind="in",
        )
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "similar.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            reply_directives=True,
        )
        client = FakeClient({"kimi-k2.6": [
            '{"move":"speak","say":["怎么又这么晚呀"],"scene":""}',
            '{"move":"speak","say":["你今天是被事情绊住了吗"],"scene":""}',
        ]})
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["你今天是被事情绊住了吗"])
        self.assertEqual(len(client.calls), 2)

    def test_reply_directives_flag_off_adds_no_note_or_similarity_retry(self):
        self.mem.record(
            chat_id=0, shot_at=None, bucket=None, weekday=None, spot=None,
            scene="（纯文字）", move="speak", said="怎么又这么晚",
            note="旧消息", kind="in",
        )
        cfg = self._no_fallback_config("directives-off.db")
        client = FakeClient({
            "kimi-k2.6":
            '{"move":"speak","say":["怎么又这么晚呀"],"scene":""}'
        })
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0)
        self.assertEqual(reply.say, ["怎么又这么晚呀"])
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(any(
            m["role"] == "system" and "本轮回复要求" in m["content"]
            for m in client.calls[0]["messages"]
        ))

    def test_streaming_similar_first_bubble_is_not_emitted_before_retry(self):
        self.mem.record(
            chat_id=0, shot_at=None, bucket=None, weekday=None, spot=None,
            scene="（纯文字）", move="speak", said="怎么又这么晚",
            note="旧消息", kind="in",
        )
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "stream-similar.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            reply_directives=True,
        )
        client = FakeClient({"kimi-k2.6": [
            '{"move":"speak","say":["怎么又这么晚呀"],"scene":""}',
            '{"move":"speak","say":["今天忙到现在啊"],"scene":""}',
        ]})
        streamed: list[str] = []
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0,
                            on_bubble=streamed.append)
        self.assertEqual(streamed, ["今天忙到现在啊"])
        self.assertEqual(reply.say, ["今天忙到现在啊"])
        self.assertEqual(len(client.calls), 2)

    def test_streaming_suppresses_later_exact_duplicate(self):
        client = FakeClient({
            "kimi-k2.6": '{"move":"speak","say":["听见了","听见了"],"scene":""}'
        })
        streamed: list[str] = []
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, self.cfg, note="测试", chat_id=0,
                            on_bubble=streamed.append)
        self.assertEqual(streamed, ["听见了"])
        self.assertEqual(reply.say, ["听见了"])

    def test_streaming_later_bubble_cannot_repeat_history_exactly(self):
        self.mem.record(
            chat_id=0, shot_at=None, bucket=None, weekday=None, spot=None,
            scene="（纯文字）", move="speak", said="说说呗",
            note="旧消息", kind="in",
        )
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "stream-exact.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            reply_directives=True,
        )
        client = FakeClient({
            "kimi-k2.6":
            '{"move":"speak","say":["我记得这件事","说说呗"],"scene":""}'
        })
        streamed: list[str] = []
        with patch("murmur.engine._client", return_value=client):
            reply = respond(self.moment, self.mem, cfg, note="测试", chat_id=0,
                            on_bubble=streamed.append)
        self.assertEqual(streamed, ["我记得这件事"])
        self.assertEqual(reply.say, ["我记得这件事"])


class StyleNoteTests(unittest.TestCase):
    def test_note_sets_compact_reply_constraints(self):
        note = _reply_style_note([
            {"role": "assistant", "content": "怎么了呀 ⏎ 说说呗"},
        ], "今天事情有点多")
        self.assertIn("怎么了呀", note)
        self.assertIn("最多一个问句", note)
        self.assertIn("先回应具体内容", note)
        self.assertIn("1–3 条气泡", note)

    def test_emotional_result_is_marked_as_unsuitable_for_followup(self):
        note = _reply_style_note([], "面试没过，我现在特别难受")
        self.assertIn("明确表达的情绪", note)
        self.assertIn("本轮不适合追问", note)
        self.assertIn("建议 1–2 条", note)

    def test_direct_question_is_answered_before_any_clarifying_question(self):
        note = _reply_style_note([], "SSE 为什么会断开？")
        self.assertIn("先直接回答", note)
        self.assertIn("不适合用反问代替答案", note)


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

    def test_guard_never_coerces_non_string_json_values_into_bubbles(self):
        self.assertEqual(_guard_bubbles([1, {"private": "value"}, None]), [])
        self.assertEqual(_guard_bubbles(["能说的", 1]), ["能说的"])


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
        self.assertNotIn("temperature", client.calls[0])
        self.assertNotIn("presence_penalty", client.calls[0])

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

    def test_sampling_params_apply_to_initiate(self):
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "sampling.db"),
            model="kimi-k2.6", fallback_model="", image_model="",
            temperature=1.1, presence_penalty=0.3,
        )
        client = FakeClient({"kimi-k2.6": PRIMARY_JSON})
        with patch("murmur.engine._client", return_value=client):
            initiate(self.moment, self.mem, cfg, INTENTS[0], chat_id=0)
        self.assertEqual(client.calls[0]["temperature"], 1.1)
        self.assertEqual(client.calls[0]["presence_penalty"], 0.3)

    def test_initiate_also_uses_the_unified_emoji_guard(self):
        client = FakeClient({
            "deepseek-v4-flash":
            '"move":"speak","say":["想起你啦😊"],"scene":"测试"}'
        })
        reply = self._call(client)
        self.assertEqual(reply.say, ["想起你啦"])


class ReadPhotoSamplingTests(unittest.TestCase):
    def test_read_photo_does_not_receive_dialogue_sampling_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(
                db_path=str(Path(tmp) / "read.db"), model="kimi-k2.6",
                fallback_model="", image_model="mimo-v2.5",
                temperature=1.1, presence_penalty=0.3,
            )
            client = FakeClient({
                "mimo-v2.5":
                '{"guess":"像是刚忙完","angles":["今天累吗"],"scene":"桌面"}'
            })
            photo = Photo(None, None, None, None, "test", "QUJD")
            with patch("murmur.engine._client", return_value=client):
                reading = read_photo(Moment.text_only(cfg.tz), photo, cfg)
        self.assertEqual(reading.guess, "像是刚忙完")
        self.assertNotIn("temperature", client.calls[0])
        self.assertNotIn("presence_penalty", client.calls[0])


if __name__ == "__main__":
    unittest.main()
