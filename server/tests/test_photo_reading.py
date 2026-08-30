"""当年今日 的读图：上游看着照片猜他想说什么，再递三个话头。

覆盖三层——解析（_parse_reading 逐条硬校验）、调用（read_photo 只走看得见
图的那档模型）、投递（intent 为 photo_reading 的 job 在 done 之前追加
angles，普通 moment 一条都不追加，读图失败静默退回普通回复）。
"""

from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai  # noqa: E402
from _helpers import make_config  # noqa: E402
from PIL import Image  # noqa: E402

from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore  # noqa: E402
from murmur.app_worker import AppWorker, EngineMomentProcessor, ProcessedMoment  # noqa: E402
from murmur.engine import (  # noqa: E402
    READING_ATTEMPTS,
    Reply,
    _parse_reading,
    read_photo,
)
from murmur.moment import Moment  # noqa: E402
from murmur.photo import Photo  # noqa: E402


def jpeg_b64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "coral").save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


class _Msg:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


class _HttpResponse:
    """openai 3.x 的 APIStatusError 需要 response.request/status_code/headers。"""

    def __init__(self):
        self.request = None
        self.status_code = 503
        self.headers = {}


def gateway_error() -> openai.InternalServerError:
    return openai.InternalServerError("boom", response=_HttpResponse(), body=None)


def bad_request_error() -> openai.BadRequestError:
    resp = _HttpResponse()
    resp.status_code = 400
    return openai.BadRequestError("unsupported image", response=resp, body=None)


class FakeClient:
    """返回固定内容 / 抛错，并记录每次 create 的 kwargs。

    replies 给一串就按顺序吐，元素可以是字符串也可以是异常；用完之后一直
    重复最后一个。读图会重试，「第一次交白卷、第二次正常」这种剧本要靠它。
    """

    def __init__(
        self,
        content: str = "",
        error: Exception | None = None,
        replies: list | None = None,
    ):
        self.content = content
        self.error = error
        self.replies = list(replies) if replies is not None else None
        self.calls: list[dict] = []
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, owner):
            self.completions = owner

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.replies is not None:
            item = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
            if isinstance(item, Exception):
                raise item
            return _Resp(item)
        if self.error is not None:
            raise self.error
        return _Resp(self.content)


def photo() -> Photo:
    return Photo(None, None, None, None, "image/jpeg", jpeg_b64())


class ParseReadingTests(unittest.TestCase):
    def test_full_reading_passes_through(self):
        reading = _parse_reading(
            '{"guess": "这是……刚下过雨？", '
            '"angles": ["那天的天气", "右边那个人是谁", "你上次说要再来"], '
            '"scene": "湿的柏油路面，路灯亮着"}'
        )
        self.assertEqual(reading.guess, "这是……刚下过雨？")
        self.assertEqual(
            reading.angles, ["那天的天气", "右边那个人是谁", "你上次说要再来"]
        )
        self.assertEqual(reading.scene, "湿的柏油路面，路灯亮着")

    def test_code_fence_and_think_tags_are_stripped(self):
        raw = '<think>让我想想</think>```json\n{"guess": "又熬到这个点了", "angles": []}\n```'
        self.assertEqual(_parse_reading(raw).guess, "又熬到这个点了")

    def test_invalid_angles_are_dropped_individually(self):
        reading = _parse_reading(
            '{"guess": "又熬到这个点了", "angles": ["那天的天气", "", '
            '"这条实在是太长了已经超过十四个字了不行", "记得休息😴", '
            '"两行\\n不行", "那天的天气"]}'
        )
        self.assertEqual(reading.angles, ["那天的天气"])

    def test_more_than_three_angles_is_capped(self):
        reading = _parse_reading('{"guess": "猜一句", "angles": ["一","二","三","四"]}')
        self.assertEqual(reading.angles, ["一", "二", "三"])

    def test_wrapped_angles_object_is_unwrapped(self):
        reading = _parse_reading(
            '{"guess": "猜一句", "angles": {"angles": ["那天的天气"]}}'
        )
        self.assertEqual(reading.angles, ["那天的天气"])

    def test_angles_and_scene_may_be_missing(self):
        reading = _parse_reading('{"guess": "这地方你去过好多次吧"}')
        self.assertEqual(reading.angles, [])
        self.assertEqual(reading.scene, "")

    def test_missing_or_unusable_guess_raises(self):
        with self.assertRaises(ValueError):
            _parse_reading('{"angles": ["那天的天气"]}')
        with self.assertRaises(ValueError):
            _parse_reading('{"guess": "这张照片真好看😊"}')
        with self.assertRaises(ValueError):
            _parse_reading('{"guess": "' + "太长" * 30 + '"}')
        with self.assertRaises(ValueError):
            _parse_reading("我完全不知道该说什么")

    def test_truncated_json_salvages_complete_parts(self):
        # 截在 scene 写到一半：guess 和三条话头都已经完整落地
        reading = _parse_reading(
            '{"guess": "这是那天风很大的海边？", '
            '"angles": ["左边那块石头", "水面上漂的东西", "远处那条线"], '
            '"scene": "海边深色礁石群，左侧有一'
        )
        self.assertEqual(reading.guess, "这是那天风很大的海边？")
        self.assertEqual(
            reading.angles, ["左边那块石头", "水面上漂的东西", "远处那条线"]
        )
        self.assertEqual(reading.scene, "")

    def test_truncated_mid_angles_keeps_only_complete_ones(self):
        reading = _parse_reading(
            '{"guess": "又熬到这个点了", "angles": ["屏幕的光", "写到一半的'
        )
        self.assertEqual(reading.angles, ["屏幕的光"])

    def test_truncated_mid_guess_raises(self):
        # guess 自己都没写完：没有可交付的东西，照旧抛给调用方降级
        with self.assertRaises(ValueError):
            _parse_reading('{"guess": "这是没写完的')

    def test_salvage_does_not_confuse_scene_with_angles(self):
        # angles 数组已正常收尾、scene 写到一半：scene 的碎片不许混进话头
        reading = _parse_reading(
            '{"guess": "猜一句", "angles": ["那天的天气"], "scene": "一本打开的'
        )
        self.assertEqual(reading.angles, ["那天的天气"])
        self.assertEqual(reading.scene, "")


class ReadPhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m.db"),
            image_model="deepseek-v4-flash-vision-exp",
            fallback_model="deepseek-v4-flash",
        )
        self.moment = Moment.text_only(self.cfg.tz)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, client, cfg=None):
        with patch("murmur.engine._client", return_value=client):
            return read_photo(self.moment, photo(), cfg or self.cfg)

    def test_sends_the_image_to_the_image_model(self):
        client = FakeClient('{"guess": "这是……刚下过雨？", "angles": ["那天的天气"]}')
        reading = self._call(client)
        self.assertEqual(reading.guess, "这是……刚下过雨？")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["model"], "deepseek-v4-flash-vision-exp")
        # 不支持 json_schema 的那档，靠 READING_SYSTEM 约束输出。
        self.assertNotIn("response_format", client.calls[0])
        parts = client.calls[0]["messages"][-1]["content"]
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertIn("base64,", parts[0]["image_url"]["url"])

    def test_never_falls_back_to_a_model_that_cannot_see(self):
        client = FakeClient(error=gateway_error())
        with self.assertRaises(openai.OpenAIError):
            self._call(client)
        # 重试是「同一个模型再来一次」，不是「换一个模型」：降级档看不见图，
        # 这一屏没有它的位置。
        self.assertEqual(len(client.calls), READING_ATTEMPTS)
        self.assertEqual({c["model"] for c in client.calls}, {"deepseek-v4-flash-vision-exp"})

    def test_a_transient_blank_is_rescued_by_the_retry(self):
        # 实测最常见的死法：finish_reason=stop，正文长度 0，重试几乎必中。
        client = FakeClient(replies=["", '{"guess": "第二次才写出来的"}'])
        reading = self._call(client)
        self.assertEqual(reading.guess, "第二次才写出来的")
        self.assertEqual(len(client.calls), 2)

    def test_a_bad_request_is_not_retried(self):
        # 400 是这次请求本身不合法，再发一遍还是同一份请求。
        client = FakeClient(error=bad_request_error())
        with self.assertRaises(openai.BadRequestError):
            self._call(client)
        self.assertEqual(len(client.calls), 1)

    def test_json_prefix_pins_the_first_character(self):
        cfg = make_config(
            db_path=str(Path(self.tmp.name) / "m3.db"),
            image_model="deepseek-v4-flash-vision-exp",
            json_prefix=True,
        )
        # prefix 模式下模型只写「续写部分」，开头那个 { 由我们补回去。
        client = FakeClient('"guess": "钉住首字符之后写的"}')
        reading = self._call(client, cfg=cfg)
        self.assertEqual(reading.guess, "钉住首字符之后写的")
        tail = client.calls[0]["messages"][-1]
        self.assertEqual(tail["role"], "assistant")
        self.assertEqual(tail["content"], "{")
        self.assertTrue(tail["prefix"])

    def test_unparseable_output_raises(self):
        client = FakeClient("他拍了一张照片，看起来心情不错")
        with self.assertRaises(ValueError):
            self._call(client)
        self.assertEqual(len(client.calls), READING_ATTEMPTS)

    def test_no_image_model_configured_raises_without_calling(self):
        cfg = make_config(db_path=str(Path(self.tmp.name) / "m2.db"), image_model="")
        client = FakeClient('{"guess": "猜一句"}')
        with self.assertRaises(ValueError):
            self._call(client, cfg=cfg)
        self.assertEqual(client.calls, [])


def app_settings(root: Path) -> AppSettings:
    return AppSettings(
        db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
        data_root=root, upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766",
        app_id="", team_id="", attest_mode="development", attest_root_path=None,
        allow_development=True, development_token="d" * 32,
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        timezone=ZoneInfo("Asia/Shanghai"),
    )


class WorkerReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = app_settings(self.root)
        self.settings.upload_dir.mkdir()
        self.cfg = make_config(self.settings.memory_db_path, image_model="deepseek-v4-flash-vision-exp")
        self.store = AppStore(self.settings.db_path)
        code = self.store.create_invite()
        self.enrollment = self.store.redeem_invite(
            code=code, key_id="dev-reading", public_key=None, receipt=None,
            counter=0, environment="development",
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def queue(self, *, intent: str | None, note: str | None = "hello",
              image: bool = True):
        path = None
        if image:
            path = self.settings.upload_dir / f"murmur-upload-{intent}-{note}"
            path.write_bytes(b"original-private-photo")
        return self.store.create_moment(
            user_id=self.enrollment.user_id, note=note,
            image_path=str(path) if path else None,
            idempotency_key=f"reading-{intent}-{note}-{image}",
            request_digest=f"digest-{intent}-{note}-{image}",
            intent=intent,
        )

    def _events(self, moment_id: str) -> list[dict]:
        return self.store.events_after(moment_id, self.enrollment.user_id)

    def test_reading_appends_angles_before_done(self):
        result = self.queue(intent="photo_reading", note=None)
        subject = photo()

        def processor(_job, _memory, _on_bubble):
            return ProcessedMoment(
                Reply("湿的柏油路面", "speak", ["这是……刚下过雨？"]),
                Moment.of(subject, self.cfg.tz), subject,
                ["那天的天气", "右边那个人是谁", "你上次说要再来"],
            )

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        self.assertTrue(worker.process_one())
        events = self._events(result.moment_id)
        self.assertEqual(
            [event["event"] for event in events],
            ["accepted", "bubble", "angles", "done"],
        )
        self.assertEqual(events[1]["data"], {"text": "这是……刚下过雨？"})
        self.assertEqual(
            events[2]["data"],
            {"angles": ["那天的天气", "右边那个人是谁", "你上次说要再来"]},
        )

    def test_ordinary_photo_moment_carries_no_angles(self):
        result = self.queue(intent=None)
        subject = photo()

        def processor(_job, _memory, on_bubble):
            on_bubble("又这么晚啊")
            return ProcessedMoment(
                Reply("深夜的工位", "speak", ["又这么晚啊"]),
                Moment.of(subject, self.cfg.tz), subject,
            )

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        self.assertTrue(worker.process_one())
        self.assertEqual(
            [event["event"] for event in self._events(result.moment_id)],
            ["accepted", "bubble", "done"],
        )

    def test_the_job_carries_the_intent_to_the_processor(self):
        self.queue(intent="photo_reading", note=None)
        seen: list[str | None] = []
        subject = photo()

        def processor(job, _memory, _on_bubble):
            seen.append(job.intent)
            return ProcessedMoment(
                Reply("湿的柏油路面", "speak", ["猜一句"]),
                Moment.of(subject, self.cfg.tz), subject,
            )

        worker = AppWorker(self.store, self.cfg, self.settings, processor=processor)
        self.assertTrue(worker.process_one())
        self.assertEqual(seen, ["photo_reading"])

    def test_unknown_intent_is_refused_at_the_door(self):
        with self.assertRaises(ValueError):
            self.queue(intent="do_something_else", note=None)


class ProcessorFallbackTests(unittest.TestCase):
    """读图挂了，这间房照样得开门——退回普通回复，只是没有三个话头。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = self.root / "murmur-upload-fallback.jpg"
        Image.new("RGB", (24, 24), "coral").save(self.image, "JPEG")
        self.cfg = make_config(
            db_path=str(self.root / "m.db"), image_model="deepseek-v4-flash-vision-exp"
        )
        self.processor = EngineMomentProcessor(self.cfg, self.root)
        self.job = type(
            "J", (), {"id": "j", "moment_id": "m", "user_id": "u", "note": None,
                      "image_path": str(self.image), "intent": "photo_reading"}
        )()

    def tearDown(self):
        self.tmp.cleanup()

    def test_reading_result_becomes_the_first_bubble_and_the_angles(self):
        from murmur.engine import PhotoReading

        with patch(
            "murmur.app_worker.read_photo",
            return_value=PhotoReading("这是……刚下过雨？", ["那天的天气"], "湿的路面"),
        ), patch("murmur.app_worker.respond") as respond:
            result = self.processor(self.job, None, lambda _text: None)
        respond.assert_not_called()
        self.assertEqual(result.reply.say, ["这是……刚下过雨？"])
        self.assertEqual(result.reply.scene, "湿的路面")
        self.assertEqual(result.angles, ["那天的天气"])

    def test_reading_failure_falls_back_to_an_ordinary_reply(self):
        with patch(
            "murmur.app_worker.read_photo", side_effect=RuntimeError("model exploded")
        ), patch(
            "murmur.app_worker.respond",
            return_value=Reply("湿的路面", "speak", ["诶，这是哪儿"]),
        ) as respond:
            result = self.processor(self.job, None, lambda _text: None)
        respond.assert_called_once()
        self.assertEqual(result.reply.say, ["诶，这是哪儿"])
        self.assertEqual(result.angles, [])


if __name__ == "__main__":
    unittest.main()
