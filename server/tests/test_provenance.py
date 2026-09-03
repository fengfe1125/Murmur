"""照片自己带的事实：什么时候拍的、在哪儿拍的。

当年今日推上来的是一张重新编码过的 JPEG，EXIF 已经没了。不显式声明，
一张三年前的照片就会被当成刚拍的——而这一屏的全部价值就是问对那一天。

覆盖四层：门口的校验（_parse_provenance）、落库与清除（AppStore）、
盖到 Photo 上（_apply_provenance）、说给模型听（Moment.describe_recalled
与 _reading_context）。
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

from murmur.app_api import APIError, _parse_provenance  # noqa: E402
from murmur.app_store import AppStore  # noqa: E402
from murmur.app_worker import _apply_provenance, _provenance  # noqa: E402
from murmur.engine import _reading_context, build_context  # noqa: E402
from murmur.moment import Moment  # noqa: E402
from murmur.photo import Photo  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def jpeg_b64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "coral").save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


def stripped_photo() -> Photo:
    """重新编码过的那张：EXIF 一个字节都不剩，所以三个字段全是 None。"""
    return Photo(None, None, None, None, None, jpeg_b64())


class _Job:
    def __init__(self, provenance: str | None):
        self.provenance = provenance


class ParseProvenanceTests(unittest.TestCase):
    """门口这一关。App 说什么都得先证明它是个合法的说法。"""

    def test_full_block_becomes_canonical_json(self):
        # 键排过序、空白压掉：幂等摘要要算它，同一份内容必须是同一串字节。
        self.assertEqual(
            _parse_provenance(json.dumps({
                "place": "  上海市 · 徐汇区  ", "lon": 121.447,
                "shot_at": "2023-08-30T15:04:11+08:00", "lat": 31.201,
            })),
            '{"lat":31.201,"lon":121.447,"place":"上海市 · 徐汇区",'
            '"shot_at":"2023-08-30T15:04:11+08:00"}',
        )

    def test_absent_and_empty_are_both_nothing(self):
        for value in (None, "", "{}", '{"place": "   "}'):
            with self.subTest(value=value):
                self.assertIsNone(_parse_provenance(value))

    def test_a_photo_may_know_only_when_not_where(self):
        # 关了定位的老照片是常态，不是错误。
        self.assertEqual(
            _parse_provenance('{"shot_at": "2023-08-30T15:04:11+08:00"}'),
            '{"shot_at":"2023-08-30T15:04:11+08:00"}',
        )

    def test_half_a_coordinate_is_refused(self):
        # 半个坐标折不出地点指纹，也没法解释它是什么意思。
        for block in ('{"lat": 31.2}', '{"lon": 121.4}'):
            with self.subTest(block=block), self.assertRaises(APIError):
                _parse_provenance(block)

    def test_impossible_coordinates_are_refused(self):
        for block in (
            '{"lat": 91, "lon": 0}', '{"lat": 0, "lon": 181}',
            '{"lat": -91, "lon": 0}', '{"lat": "31.2", "lon": "121.4"}',
            # bool 是 int 的子类，不挡的话 True 会被当成 1.0 度。
            '{"lat": true, "lon": true}',
        ):
            with self.subTest(block=block), self.assertRaises(APIError):
                _parse_provenance(block)

    def test_unparseable_or_unknown_shapes_are_refused(self):
        for block in (
            "not json", "[]", '"a string"',
            '{"place": "家", "surprise": 1}',
            '{"shot_at": "上周三"}', '{"shot_at": 1693382651}',
            '{"place": ' + json.dumps("远" * 121) + "}",
        ):
            with self.subTest(block=block), self.assertRaises(APIError):
                _parse_provenance(block)


class ApplyProvenanceTests(unittest.TestCase):
    """盖到 Photo 上。图里读不出来的，由 App 声明补上。"""

    def test_declared_facts_land_on_a_photo_that_lost_its_exif(self):
        photo = _apply_provenance(stripped_photo(), {
            "shot_at": "2023-08-30T15:04:11+08:00",
            "lat": 31.201, "lon": 121.447, "place": "星巴克",
        })
        self.assertEqual(photo.shot_at.year, 2023)
        self.assertEqual((photo.lat, photo.lon), (31.201, 121.447))
        self.assertTrue(photo.has_gps)

    def test_nothing_declared_leaves_the_photo_alone(self):
        original = Photo(None, datetime(2024, 1, 1), 1.0, 2.0, None, jpeg_b64())
        self.assertEqual(_apply_provenance(original, {}), original)

    def test_a_broken_block_is_read_as_no_block(self):
        # API 那层已经校验过一遍；这里只是不信任地读回来，宁可少一句地名，
        # 也不该因为一个字段把整条 moment 弄失败。
        for stored in (None, "", "not json", "[]"):
            with self.subTest(stored=stored):
                self.assertEqual(_provenance(_Job(stored)), {})

    def test_a_job_without_the_field_at_all_is_fine(self):
        self.assertEqual(_provenance(object()), {})


class RecalledMomentTests(unittest.TestCase):
    """说给模型听。两个时间必须分开，否则它会以为今天是三年前的那天。"""

    def moment(self, *, place: str | None = "上海市 · 徐汇区 · 星巴克") -> Moment:
        photo = _apply_provenance(stripped_photo(), {
            "shot_at": "2023-08-30T15:04:11+08:00", "lat": 31.201, "lon": 121.447,
        })
        return Moment.of(
            photo, TZ,
            received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ),
            place=place,
        )

    def test_both_times_are_said_and_they_are_not_the_same_one(self):
        text = self.moment().describe_recalled()
        self.assertIn("拍摄于 2023-08-30", text)
        self.assertIn("现在是 2026-08-30", text)

    def test_same_day_across_years_is_named_as_such(self):
        # 「3 年前的今天」是这个功能的名字，值得单独一档。
        self.assertIn("（3 年前的今天）", self.moment().describe_recalled())

    def test_an_ordinary_gap_is_not_dressed_up_as_an_anniversary(self):
        photo = _apply_provenance(stripped_photo(), {"shot_at": "2026-05-02T09:00:00+08:00"})
        text = Moment.of(
            photo, TZ, received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ)
        ).describe_recalled()
        self.assertIn("个月前", text)
        self.assertNotIn("的今天", text)

    def test_the_place_is_said_only_when_there_is_one(self):
        self.assertIn("拍摄地点：上海市 · 徐汇区 · 星巴克", self.moment().describe_recalled())
        # 「地点：未知」会被模型当成一条信息去用。没有就是不说。
        self.assertNotIn("拍摄地点", self.moment(place=None).describe_recalled())

    def test_a_photo_with_no_shot_time_is_not_given_an_invented_one(self):
        moment = Moment.of(
            stripped_photo(), TZ,
            received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ),
        )
        text = moment.describe_recalled()
        self.assertIn("没带拍摄时间", text)
        self.assertNotIn("拍摄于", text)
        self.assertIn("现在是 2026-08-30", text)

    def test_a_long_place_is_trimmed_before_it_reaches_the_prompt(self):
        moment = self.moment(place="远" * 200)
        self.assertEqual(len(moment.place), 60)

    def test_the_ordinary_line_is_still_one_line(self):
        # 随手拍随手发那条路没有变：describe() 仍然只说时间，且只有一行。
        self.assertNotIn("\n", self.moment().describe())


class ReadingContextTests(unittest.TestCase):
    def moment(self) -> Moment:
        photo = _apply_provenance(stripped_photo(), {
            "shot_at": "2023-08-30T15:04:11+08:00", "lat": 31.201, "lon": 121.447,
        })
        return Moment.of(
            photo, TZ, received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ),
            place="星巴克",
        )

    def test_a_familiar_spot_is_handed_over_as_a_count_not_a_name(self):
        # 「这地方你去过好多次吧」现在有依据了——依据是次数，不是地名。
        text = _reading_context(self.moment(), visits=5)
        self.assertIn("发过 5 次图", text)
        self.assertIn("现在是 2026-08-30", text)

    def test_a_place_seen_once_or_twice_says_nothing_about_it(self):
        for visits in (0, 1, 2):
            with self.subTest(visits=visits):
                self.assertNotIn("次图", _reading_context(self.moment(), visits))


class FallbackContextTests(unittest.TestCase):
    """读图挂了会退回普通回复，那条路走的是 build_context。

    只在 read_photo 里把两个时间分开说，等于只修了一半：退回去的那句话
    照样会把三年前当成此刻。
    """

    def old_photo(self, *, place: str | None = "星巴克") -> Moment:
        photo = _apply_provenance(stripped_photo(), {
            "shot_at": "2023-08-30T15:04:11+08:00", "lat": 31.201, "lon": 121.447,
        })
        return Moment.of(
            photo, TZ, received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ),
            place=place,
        )

    def fresh_photo(self) -> Moment:
        photo = Photo(None, datetime(2026, 8, 30, 21, 9), None, None, None, jpeg_b64())
        return Moment.of(
            photo, TZ, received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ)
        )

    def test_an_old_photo_is_never_called_this_moment(self):
        text = build_context(self.old_photo(), 0, None)
        self.assertNotIn("此刻", text)
        self.assertIn("拍摄于 2023-08-30", text)
        self.assertIn("现在是 2026-08-30", text)

    def test_a_photo_taken_minutes_ago_reads_exactly_as_it_did_before(self):
        text = build_context(self.fresh_photo(), 0, "你看这个")
        self.assertTrue(text.startswith("此刻："))
        self.assertIn("他随图说了：你看这个", text)

    def test_recall_is_about_the_day_not_the_clock(self):
        self.assertTrue(self.old_photo().is_recalled)
        self.assertFalse(self.fresh_photo().is_recalled)

    def test_a_photo_with_no_shot_time_is_not_guessed_to_be_old(self):
        # 时间是回落来的，那就无从判断它是不是旧照片——不猜。
        moment = Moment.of(
            stripped_photo(), TZ,
            received_at=datetime(2026, 8, 30, 21, 10, tzinfo=TZ),
        )
        self.assertFalse(moment.is_recalled)
        self.assertTrue(build_context(moment, 0, None).startswith("此刻："))


class StoredProvenanceTests(unittest.TestCase):
    """地名和原图一样是临时的：每条终结路径上一起清空。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AppStore(Path(self.tmp.name) / "app.db")
        code = self.store.create_invite()
        self.user = self.store.redeem_invite(
            code=code, key_id="dev-provenance", public_key=None, receipt=None,
            counter=0, environment="development",
        ).user_id
        self.block = '{"lat":31.201,"lon":121.447,"place":"星巴克"}'

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def queue(self, key: str = "k-1"):
        image = Path(self.tmp.name) / f"upload-{key}"
        image.write_bytes(b"original-private-photo")
        return self.store.create_moment(
            user_id=self.user, note=None, image_path=str(image),
            idempotency_key=key, request_digest=f"digest-{key}",
            intent="photo_reading", provenance=self.block,
        )

    def stored(self, moment_id: str):
        return self.store.conn.execute(
            "SELECT provenance FROM app_moments WHERE id=?", (moment_id,)
        ).fetchone()["provenance"]

    def test_it_reaches_the_worker(self):
        moment = self.queue()
        job = self.store.claim_job("w-1")
        self.assertEqual(job.moment_id, moment.moment_id)
        self.assertEqual(job.provenance, self.block)

    def test_a_finished_moment_keeps_no_place_name(self):
        moment = self.queue()
        job = self.store.claim_job("w-1")
        self.store.finish_job(
            job, scene="湿的路面", move="speak", memory_entry_id=1, preview_path=None
        )
        self.assertIsNone(self.stored(moment.moment_id))

    def test_a_failed_moment_keeps_no_place_name(self):
        moment = self.queue()
        job = self.store.claim_job("w-1")
        self.store.fail_job(job, code="boom", message="炸了", retryable=False)
        self.assertIsNone(self.stored(moment.moment_id))

    def test_an_ordinary_moment_stores_nothing(self):
        result = self.store.create_moment(
            user_id=self.user, note="就一句话", image_path=None,
            idempotency_key="k-text", request_digest="digest-text",
        )
        self.assertIsNone(self.stored(result.moment_id))


if __name__ == "__main__":
    unittest.main()
