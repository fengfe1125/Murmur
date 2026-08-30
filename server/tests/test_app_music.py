"""Audius 音乐这条竖切：wire 校验、目录客户端、播放状态、接口和 worker。

这个文件守的是两件事：一是歌曲元数据进出 Murmur 的形状必须窄——token、
stream 地址、完整曲库都不许出现；二是加了音乐之后，纯文字和照片那两条老
路径的字节必须一个都没变。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from _helpers import make_config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from murmur.app_api import (  # noqa: E402
    create_app,
    erase_account,
    music_enabled_for,
)
from murmur.app_music import (  # noqa: E402
    MUSIC_NOT_FOUND_LINE,
    AppMusic,
    AppMusicPlanner,
    AudiusCatalogClient,
    MusicCatalogUnavailable,
    MusicPlan,
    MusicPlanningFailed,
    MusicTrackInvalid,
    MusicTrackUnavailable,
    choose_music_track,
    load_music_track,
    music_fallback_text,
    music_prompt_line,
    normalize_music_track,
    normalize_playback_track,
    parse_music_track,
    playback_prompt_line,
)
from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore, Conflict  # noqa: E402
from murmur.app_worker import (  # noqa: E402
    AppWorker,
    EngineMomentProcessor,
    ProcessedMoment,
)
from murmur.engine import Reply, history_turns  # noqa: E402
from murmur.memory import Entry, Memory, thread_key  # noqa: E402
from murmur.moment import Moment  # noqa: E402

TRACK = {
    "version": 1,
    "provider": "audius",
    "track_id": "abc123",
    "title": "Rainy Night",
    "artists": ["Nova"],
    "artwork_url": "https://audius.co/art.jpg",
    "canonical_url": "https://audius.co/nova/rainy-night",
    "duration_seconds": 183,
    "explicit": False,
}


def settings(root: Path, **overrides) -> AppSettings:
    base = AppSettings(
        db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
        data_root=root, upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766", app_id="", team_id="",
        attest_mode="development", attest_root_path=None, allow_development=True,
        development_token="development-token-0123456789",
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        requests_per_minute=500, timezone=ZoneInfo("Asia/Shanghai"),
    )
    return replace(base, **overrides) if overrides else base


# ---------------------------------------------------------------------------
# TrackV1：进出这条线的那一个对象
# ---------------------------------------------------------------------------


class TrackWireTests(unittest.TestCase):
    def test_accepts_the_documented_shape(self):
        self.assertEqual(normalize_music_track(dict(TRACK)), TRACK)

    def test_optional_fields_are_omitted_rather_than_nulled(self):
        minimal = {k: TRACK[k] for k in
                   ("version", "provider", "track_id", "title", "artists",
                    "canonical_url")}
        clean = normalize_music_track(minimal)
        self.assertNotIn("artwork_url", clean)
        self.assertNotIn("duration_seconds", clean)
        self.assertNotIn("explicit", clean)

    def test_rejects_anything_not_on_the_whitelist(self):
        """多一个字段就整条拒绝——比如有人想夹一个 stream 地址进来。"""
        for extra in ("stream_url", "access_token", "lyrics"):
            with self.subTest(extra=extra):
                with self.assertRaises(MusicTrackInvalid):
                    normalize_music_track({**TRACK, extra: "https://x.invalid/a"})

    def test_rejects_other_providers_and_versions(self):
        with self.assertRaises(MusicTrackInvalid):
            normalize_music_track({**TRACK, "provider": "spotify"})
        with self.assertRaises(MusicTrackInvalid):
            normalize_music_track({**TRACK, "version": 2})

    def test_rejects_non_https_and_credentialed_urls(self):
        for url in ("http://audius.co/a", "audius.co/a",
                    "https://user:pw@audius.co/a", "javascript:alert(1)"):
            with self.subTest(url=url):
                with self.assertRaises(MusicTrackInvalid):
                    normalize_music_track({**TRACK, "canonical_url": url})

    def test_rejects_bad_artists_and_durations(self):
        for artists in ([], ["a"] * 9, ["Nova", "Nova"], "Nova", [1]):
            with self.subTest(artists=artists):
                with self.assertRaises(MusicTrackInvalid):
                    normalize_music_track({**TRACK, "artists": artists})
        for duration in (0, -1, True, "183", 24 * 60 * 60 + 1):
            with self.subTest(duration=duration):
                with self.assertRaises(MusicTrackInvalid):
                    normalize_music_track({**TRACK, "duration_seconds": duration})

    def test_parse_is_canonical_and_order_independent(self):
        """幂等摘要建立在这份字符串上，键序不能影响它。"""
        forward = json.dumps(TRACK)
        backward = json.dumps(dict(reversed(list(TRACK.items()))))
        self.assertEqual(parse_music_track(forward)[1],
                         parse_music_track(backward)[1])

    def test_parse_rejects_oversized_and_non_json(self):
        with self.assertRaises(MusicTrackInvalid):
            parse_music_track(json.dumps({**TRACK, "title": "x" * 20000}))
        with self.assertRaises(MusicTrackInvalid):
            parse_music_track("not json")

    def test_parse_treats_absent_as_absent(self):
        self.assertEqual(parse_music_track(None), (None, None))
        self.assertEqual(parse_music_track(""), (None, None))

    def test_stored_track_reads_back(self):
        """回归：这个函数一度永远返回 None，历史里的歌就整个消失了。"""
        _, canonical = parse_music_track(json.dumps(TRACK))
        self.assertEqual(load_music_track(canonical), TRACK)

    def test_unreadable_stored_track_is_absent_not_fatal(self):
        for raw in (None, "", "{oops", json.dumps({"provider": "spotify"})):
            with self.subTest(raw=raw):
                self.assertIsNone(load_music_track(raw))

    def test_playback_track_is_a_smaller_shape(self):
        clean = normalize_playback_track({
            "provider": "audius", "track_id": "abc123",
            "title": "Rainy Night", "artists": ["Nova"], "duration_seconds": 183,
        })
        self.assertEqual(clean["track_id"], "abc123")
        with self.assertRaises(MusicTrackInvalid):
            normalize_playback_track({
                "provider": "audius", "track_id": "a", "title": "t",
                "artists": ["n"], "canonical_url": "https://audius.co/a",
            })

    def test_fallback_text_carries_the_whole_message(self):
        text = music_fallback_text(TRACK)
        self.assertIn(TRACK["title"], text)
        self.assertIn("Nova", text)
        self.assertIn(TRACK["canonical_url"], text)

    def test_prompt_lines_never_carry_a_url(self):
        line = music_prompt_line(TRACK, actor="他")
        self.assertIn("Rainy Night", line)
        self.assertNotIn("https://", line)


# ---------------------------------------------------------------------------
# 目录客户端：只读公开元数据
# ---------------------------------------------------------------------------


def api_track(**overrides) -> dict:
    value = {
        "id": "abc123", "title": "Rainy Night", "duration": 183,
        "permalink": "/nova/rainy-night", "is_explicit": False,
        "user": {"name": "Nova"},
        "artwork": {"_1000x1000": "https://audius.co/art.jpg"},
    }
    value.update(overrides)
    return value


def catalog(handler, **kwargs) -> AudiusCatalogClient:
    return AudiusCatalogClient(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


class CatalogClientTests(unittest.TestCase):
    def test_normalises_an_api_track_into_the_wire_shape(self):
        client = catalog(lambda r: httpx.Response(200, json={"data": api_track()}))
        self.assertEqual(client.get_track("abc123"), TRACK)

    def test_sends_the_server_key_and_never_a_user_token(self):
        seen = {}

        def handler(request):
            seen.update(request.headers)
            return httpx.Response(200, json={"data": api_track()})

        catalog(handler).get_track("abc123")
        self.assertEqual(seen["x-api-key"], "test-key")
        self.assertNotIn("authorization", seen)

    def test_missing_track_is_unavailable_and_negatively_cached(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(404)

        client = catalog(handler)
        for _ in range(2):
            with self.assertRaises(MusicTrackUnavailable):
                client.get_track("gone")
        self.assertEqual(len(calls), 1, "负缓存没生效，每次都去问了一遍")

    def test_unplayable_tracks_are_treated_as_gone(self):
        for flag in ({"is_delete": True}, {"is_unlisted": True},
                     {"is_streamable": False}):
            with self.subTest(flag=flag):
                client = catalog(
                    lambda r, f=flag: httpx.Response(
                        200, json={"data": api_track(**f)}
                    )
                )
                with self.assertRaises(MusicTrackUnavailable):
                    client.get_track("abc123")

    def test_metadata_is_cached(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(200, json={"data": api_track()})

        client = catalog(handler)
        client.get_track("abc123")
        client.get_track("abc123")
        self.assertEqual(len(calls), 1)

    def test_rate_limit_and_outage_are_retryable(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                client = catalog(lambda r, s=status: httpx.Response(s))
                with self.assertRaises(MusicCatalogUnavailable) as caught:
                    client.get_track("abc123")
                self.assertTrue(caught.exception.retryable)

    def test_transport_failure_is_retryable(self):
        def handler(request):
            raise httpx.ConnectError("no route", request=request)

        with self.assertRaises(MusicCatalogUnavailable):
            catalog(handler).get_track("abc123")

    def test_search_skips_unplayable_results(self):
        payload = {"data": [
            api_track(id="gone", is_delete=True),
            api_track(id="ok"),
        ]}
        client = catalog(lambda r: httpx.Response(200, json=payload))
        found = client.search_tracks("rainy night")
        self.assertEqual([t["track_id"] for t in found], ["ok"])

    def test_search_results_are_cached_case_insensitively(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"data": [api_track()]})

        client = catalog(handler)
        client.search_tracks("Rainy  Night")
        client.search_tracks("rainy night")
        self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------------
# 选歌：模型只给关键词，选择是确定性的
# ---------------------------------------------------------------------------


class ChoiceTests(unittest.TestCase):
    def test_discover_takes_the_first_playable_result(self):
        other = {**TRACK, "track_id": "second"}
        plan = MusicPlan(True, "discover", "rainy")
        self.assertEqual(choose_music_track(plan, [TRACK, other]), TRACK)

    def test_exact_never_degrades_into_a_different_song(self):
        plan = MusicPlan(True, "exact", "moonlight", "Moonlight", "Nova")
        self.assertIsNone(choose_music_track(plan, [TRACK]))

    def test_exact_matches_ignoring_case_and_punctuation(self):
        plan = MusicPlan(True, "exact", "rainy night", "rainy  night!", "nova")
        self.assertEqual(choose_music_track(plan, [TRACK]), TRACK)

    def test_exact_without_an_artist_matches_on_title_alone(self):
        plan = MusicPlan(True, "exact", "rainy night", "Rainy Night", None)
        self.assertEqual(choose_music_track(plan, [TRACK]), TRACK)

    def test_nothing_is_chosen_when_nothing_was_requested(self):
        self.assertIsNone(choose_music_track(MusicPlan(False, "discover", None), [TRACK]))
        self.assertIsNone(choose_music_track(MusicPlan(True, "discover", "x"), []))


class PlannerGateTests(unittest.TestCase):
    def test_only_music_shaped_text_reaches_the_model(self):
        for text in ("给我放首歌", "来首适合雨天的", "点歌", "听点什么好"):
            self.assertTrue(AppMusicPlanner.might_be_request(text), text)
        for text in ("", None, "今天好累", "帮我看看这张照片"):
            self.assertFalse(AppMusicPlanner.might_be_request(text), text)


class StubCatalog:
    def __init__(self, track=None, results=None, error=None):
        self.track = track
        self.results = results or []
        self.error = error
        self.searches: list[str] = []

    def get_track(self, track_id):
        if self.error:
            raise self.error
        if self.track is None:
            raise MusicTrackUnavailable("gone")
        return self.track

    def search_tracks(self, query, limit=5):
        self.searches.append(query)
        if self.error:
            raise self.error
        return list(self.results)


class StubPlanner:
    might_be_request = staticmethod(AppMusicPlanner.might_be_request)

    def __init__(self, plan=None, error=None):
        self.plan_result = plan
        self.error = error

    def plan(self, text):
        if self.error:
            raise self.error
        return self.plan_result


class AppMusicTests(unittest.TestCase):
    def test_shared_track_is_re_resolved_from_the_catalog(self):
        """客户端提交的标题只是快照，一律以服务端查回来的为准。"""
        stale = json.dumps({**TRACK, "title": "Whatever The Client Said"})
        music = AppMusic(StubCatalog(track=TRACK), StubPlanner())
        self.assertEqual(music.verify_shared(stale)["title"], "Rainy Night")

    def test_shared_track_falls_back_to_the_snapshot_when_gone(self):
        """歌下架了也不该让他发出的这条消息整条失败。"""
        music = AppMusic(StubCatalog(track=None), StubPlanner())
        got = music.verify_shared(json.dumps(TRACK))
        self.assertEqual(got["track_id"], "abc123")

    def test_no_attachment_is_no_lookup(self):
        catalog_stub = StubCatalog(track=TRACK)
        music = AppMusic(catalog_stub, StubPlanner())
        self.assertIsNone(music.verify_shared(None))

    def test_ordinary_talk_never_reaches_the_planner(self):
        planner = StubPlanner(error=AssertionError("planner should not run"))
        music = AppMusic(StubCatalog(), planner)
        self.assertFalse(music.choose_for("今天走了很久").requested)

    def test_planning_failure_sends_no_card(self):
        music = AppMusic(
            StubCatalog(results=[TRACK]),
            StubPlanner(error=MusicPlanningFailed("boom")),
        )
        choice = music.choose_for("给我放首歌")
        self.assertFalse(choice.requested)
        self.assertIsNone(choice.track)

    def test_search_outage_is_a_request_with_no_track(self):
        music = AppMusic(
            StubCatalog(error=MusicCatalogUnavailable("down")),
            StubPlanner(MusicPlan(True, "discover", "rainy")),
        )
        choice = music.choose_for("给我放首歌")
        self.assertTrue(choice.not_found)

    def test_a_found_track_is_returned(self):
        music = AppMusic(
            StubCatalog(results=[TRACK]),
            StubPlanner(MusicPlan(True, "discover", "rainy")),
        )
        self.assertEqual(music.choose_for("给我放首歌").track, TRACK)

    def test_only_the_query_reaches_the_catalog(self):
        catalog_stub = StubCatalog(results=[TRACK])
        music = AppMusic(catalog_stub, StubPlanner(MusicPlan(True, "discover", "rainy")))
        music.choose_for("给我放首歌，随便什么都行")
        self.assertEqual(catalog_stub.searches, ["rainy"])


class PlaybackPromptTests(unittest.TestCase):
    def test_says_what_is_playing_without_any_progress(self):
        line = playback_prompt_line({
            "state": "started",
            "track": {"title": "Rainy Night", "artists": ["Nova"]},
        })
        self.assertIn("Rainy Night", line)
        for leak in ("http", "%", "秒", ":"):
            self.assertNotIn(leak, line)

    def test_paused_reads_differently_from_playing(self):
        track = {"title": "Rainy Night", "artists": ["Nova"]}
        playing = playback_prompt_line({"state": "resumed", "track": track})
        paused = playback_prompt_line({"state": "paused", "track": track})
        self.assertNotEqual(playing, paused)

    def test_nothing_playing_is_nothing_said(self):
        self.assertIsNone(playback_prompt_line(None))
        self.assertIsNone(playback_prompt_line({}))
        self.assertIsNone(playback_prompt_line({"state": "stopped", "track": {}}))


# ---------------------------------------------------------------------------
# 播放状态：只有离散状态，而且会过期
# ---------------------------------------------------------------------------


class PlaybackStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AppStore(self.root / "murmur.db")
        invite = self.store.create_invite()
        self.enrollment = self.store.redeem_invite(
            code=invite, key_id="dev-playback", public_key=b"k", receipt=None,
            counter=0, environment="development", device_name="iPhone",
        )
        self.user = self.enrollment.user_id
        self.device = self.enrollment.device_id
        self.track = json.dumps(
            {"provider": "audius", "track_id": "abc123",
             "title": "Rainy Night", "artists": ["Nova"]},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def apply(self, state, sequence, *, session="s-1", duration=183, track=None):
        return self.store.update_playback_state(
            user_id=self.user, device_id=self.device, session_id=session,
            sequence=sequence, state=state, track_json=track or self.track,
            duration_seconds=duration,
        )

    def test_nothing_is_playing_until_something_starts(self):
        self.assertIsNone(self.store.current_playback(self.user))

    def test_a_start_becomes_the_current_state(self):
        self.assertTrue(self.apply("started", 1)["accepted"])
        current = self.store.current_playback(self.user)
        self.assertEqual(current["state"], "started")
        self.assertEqual(current["track"]["title"], "Rainy Night")

    def test_stale_sequences_never_overwrite_newer_state(self):
        self.apply("started", 1)
        self.assertTrue(self.apply("paused", 2)["accepted"])
        self.assertFalse(self.apply("resumed", 2)["accepted"])
        self.assertEqual(self.store.current_playback(self.user)["state"], "paused")

    def test_completion_clears_the_state_entirely(self):
        self.apply("started", 1)
        self.assertTrue(self.apply("completed", 2)["accepted"])
        self.assertIsNone(self.store.current_playback(self.user))

    def test_stop_clears_the_state_entirely(self):
        self.apply("started", 1)
        self.apply("stopped", 2)
        self.assertIsNone(self.store.current_playback(self.user))

    def test_transitions_for_an_unknown_session_are_ignored(self):
        self.assertFalse(self.apply("paused", 2, session="never-started")["accepted"])
        self.assertIsNone(self.store.current_playback(self.user))

    def test_a_new_load_replaces_the_previous_session(self):
        self.apply("started", 1, session="first")
        self.apply("started", 1, session="second")
        self.assertEqual(self.store.current_playback(self.user)["state"], "started")
        self.assertFalse(self.apply("paused", 2, session="first")["accepted"])

    def test_changing_tracks_inside_one_session_is_a_conflict(self):
        self.apply("started", 1)
        other = json.dumps(
            {"provider": "audius", "track_id": "other", "title": "Other",
             "artists": ["Nova"]},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
        with self.assertRaises(Conflict):
            self.apply("paused", 2, track=other)

    def test_a_paused_state_expires_sooner_than_a_playing_one(self):
        playing = self.apply("started", 1, session="a")["expires_at"]
        paused = self.apply("paused", 2, session="a")["expires_at"]
        self.assertLess(paused, playing)

    def test_playing_ttl_is_clamped_for_very_short_and_very_long_tracks(self):
        brief = self.apply("started", 1, session="a", duration=5)["expires_at"]
        epic = self.apply("started", 1, session="b", duration=10 * 60 * 60)["expires_at"]
        self.assertLess(brief, epic)

    def test_expired_state_is_no_longer_visible(self):
        self.apply("started", 1)
        self.store.conn.execute(
            "UPDATE app_playback_states SET expires_at='2000-01-01T00:00:00+00:00'"
        )
        self.store.conn.commit()
        self.assertIsNone(self.store.current_playback(self.user))
        self.assertEqual(self.store.cleanup_playback_states(), 0)

    def test_cleanup_removes_expired_rows(self):
        self.apply("started", 1)
        self.store.conn.execute(
            "UPDATE app_playback_states SET expires_at='2000-01-01T00:00:00+00:00'"
        )
        self.store.conn.commit()
        self.assertEqual(self.store.cleanup_playback_states(), 1)

    def playback_rows(self) -> int:
        return self.store.conn.execute(
            "SELECT count(*) AS n FROM app_playback_states"
        ).fetchone()["n"]

    def test_deleting_the_user_takes_the_playback_state_with_it(self):
        self.apply("started", 1)
        self.store.conn.execute("DELETE FROM app_users WHERE id=?", (self.user,))
        self.store.conn.commit()
        self.assertEqual(self.playback_rows(), 0)

    def test_erasing_the_account_takes_the_playback_state_with_it(self):
        """删号那条路走完之后，"他刚才在听什么"不能留在库里。"""
        self.apply("started", 1)
        erase_account(self.store, settings(self.root), self.user)
        self.assertEqual(self.playback_rows(), 0)

    def test_the_hourly_sweep_reaches_a_user_who_never_comes_back(self):
        """过期行在读的时候会被清掉，但没人再读的那一行也不该永远留着。"""
        self.apply("started", 1)
        self.store.conn.execute(
            "UPDATE app_playback_states SET expires_at='2000-01-01T00:00:00+00:00'"
        )
        self.store.conn.commit()
        AppWorker(
            self.store, make_config(self.root / "murmur.db"),
            settings(self.root),
            processor=lambda *_: (_ for _ in ()).throw(RuntimeError("not used")),
        ).cleanup()
        self.assertEqual(self.playback_rows(), 0)


# ---------------------------------------------------------------------------
# 灰度闸门
# ---------------------------------------------------------------------------


class FeatureGateTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(music_enabled_for(settings(Path("/tmp")), "u1"))

    def test_on_for_everyone_when_no_allowlist(self):
        self.assertTrue(music_enabled_for(
            settings(Path("/tmp"), music_enabled=True, audius_api_key="k"), "u1"
        ))

    def test_allowlist_narrows_it_to_named_accounts(self):
        gated = settings(
            Path("/tmp"), music_enabled=True, audius_api_key="k",
            music_user_allowlist=frozenset({"u1"}),
        )
        self.assertTrue(music_enabled_for(gated, "u1"))
        self.assertFalse(music_enabled_for(gated, "u2"))

    def test_the_switch_beats_the_allowlist(self):
        self.assertFalse(music_enabled_for(
            settings(Path("/tmp"), music_user_allowlist=frozenset({"u1"})), "u1"
        ))

    def test_enabling_music_without_a_key_is_a_configuration_error(self):
        with self.assertRaises(RuntimeError):
            settings(Path("/tmp"), music_enabled=True).validate()


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


class MusicAPIHarness:
    """接口测试共用的入组和签名。两个开关由子类决定，其余完全一样。"""

    music_enabled = True
    playback_reporting = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = settings(
            self.root, music_enabled=self.music_enabled, audius_api_key="k",
            music_playback_reporting=self.playback_reporting,
        )
        self.cfg = make_config(self.settings.memory_db_path)
        self.store = AppStore(self.settings.db_path)
        self.app = create_app(self.settings, cfg=self.cfg, store=self.store)
        self.client = TestClient(self.app)
        self.dev_headers = {
            "X-Murmur-Development-Token": self.settings.development_token
        }
        invite = self.store.create_invite()
        challenge = self.client.post(
            "/v1/auth/challenges", json={"purpose": "enrollment"}
        ).json()
        response = self.client.post(
            "/v1/enrollments", headers=self.dev_headers,
            json={"challenge_id": challenge["challenge_id"], "invite_code": invite,
                  "key_id": "dev-music-phone", "environment": "development",
                  "device_name": "iPhone"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.identity = response.json()

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.tmp.cleanup()

    def headers(self) -> dict:
        response = self.client.post(
            "/v1/auth/challenges", headers=self.dev_headers,
            json={"purpose": "request", "key_id": self.identity["key_id"]},
        )
        return {
            **self.dev_headers,
            "X-Murmur-Key-ID": self.identity["key_id"],
            "X-Murmur-Challenge-ID": response.json()["challenge_id"],
        }

    def config(self) -> dict:
        return self.client.get("/v1/music/config", headers=self.headers()).json()

    def playback_body(self, **overrides) -> dict:
        body = {
            "version": 1, "session_id": "11111111-2222-3333-4444-555555555555",
            "sequence": 1, "state": "started",
            "track": {"provider": "audius", "track_id": "abc123",
                      "title": "Rainy Night", "artists": ["Nova"],
                      "duration_seconds": 183},
            "occurred_at": "2026-08-30T10:00:00Z",
        }
        body.update(overrides)
        return body

    def report(self, **overrides):
        return self.client.put(
            "/v1/music/playback-state", headers=self.headers(),
            json=self.playback_body(**overrides),
        )

    def post_music_moment(self, key: str, track=None, **extra):
        data = {"idempotency_key": key,
                "note": music_fallback_text(track or TRACK),
                "music_track": json.dumps(track or TRACK)}
        data.update(extra)
        return self.client.post(
            "/v1/moments", headers=self.headers(), data=data,
            files={"_multipart": (None, "1")},
        )

    def stored_music(self, moment_id: str):
        return self.store.conn.execute(
            "SELECT music_track FROM app_moments WHERE id=?", (moment_id,)
        ).fetchone()["music_track"]


class MusicAPITests(MusicAPIHarness, unittest.TestCase):
    """音乐开着的时候，这条线该有的样子。"""

    # ---- config ----------------------------------------------------------

    def test_config_reports_the_provider_and_both_switches(self):
        self.assertEqual(
            self.config(),
            {"enabled": True, "provider": "audius", "playback_reporting": True},
        )

    def test_config_requires_authentication(self):
        self.assertEqual(self.client.get("/v1/music/config").status_code, 401)

    # ---- sending a song --------------------------------------------------

    def test_a_song_is_accepted_and_stored_canonically(self):
        response = self.post_music_moment("music-0001")
        self.assertEqual(response.status_code, 202, response.text)
        stored = json.loads(self.stored_music(response.json()["moment_id"]))
        self.assertEqual(stored, TRACK)

    def test_a_song_travels_with_a_text_fallback(self):
        response = self.post_music_moment("music-0002")
        row = self.store.moment_for_user(
            response.json()["moment_id"], self.identity["user_id"]
        )
        self.assertIn("Rainy Night", row["note"])

    def test_a_song_may_be_the_only_content(self):
        response = self.client.post(
            "/v1/moments", headers=self.headers(),
            data={"idempotency_key": "music-0003",
                  "music_track": json.dumps(TRACK)},
            files={"_multipart": (None, "1")},
        )
        self.assertEqual(response.status_code, 202, response.text)

    def test_a_song_cannot_ride_with_a_photo(self):
        response = self.client.post(
            "/v1/moments", headers=self.headers(),
            data={"idempotency_key": "music-0004",
                  "music_track": json.dumps(TRACK)},
            files={"image": ("photo.jpg", b"\xff\xd8\xfffake-jpeg", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "invalid_music_attachment"
        )

    def test_a_rejected_pairing_leaves_no_upload_behind(self):
        self.client.post(
            "/v1/moments", headers=self.headers(),
            data={"idempotency_key": "music-0005",
                  "music_track": json.dumps(TRACK)},
            files={"image": ("photo.jpg", b"\xff\xd8\xfffake-jpeg", "image/jpeg")},
        )
        self.assertEqual(list(self.settings.upload_dir.glob("murmur-upload-*")), [])

    def test_smuggled_fields_are_refused(self):
        for extra in ("stream_url", "access_token"):
            with self.subTest(extra=extra):
                response = self.post_music_moment(
                    f"music-smuggle-{extra}",
                    track={**TRACK, extra: "https://cdn.invalid/a.mp3"},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["error"]["code"], "invalid_music_track"
                )

    def test_a_foreign_provider_is_refused(self):
        response = self.post_music_moment(
            "music-provider", track={**TRACK, "provider": "spotify"}
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_an_oversized_song_is_refused(self):
        response = self.post_music_moment(
            "music-huge", track={**TRACK, "title": "x" * 20000}
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_the_same_key_and_song_is_idempotent(self):
        first = self.post_music_moment("music-same")
        second = self.post_music_moment("music-same")
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(first.json()["moment_id"], second.json()["moment_id"])

    def test_the_same_key_with_a_different_song_conflicts(self):
        self.post_music_moment("music-swap")
        response = self.post_music_moment(
            "music-swap", track={**TRACK, "track_id": "different"}
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_ordinary_text_is_unaffected(self):
        response = self.client.post(
            "/v1/moments", headers=self.headers(),
            data={"note": "今天走了很久", "idempotency_key": "text-on-0001"},
            files={"_multipart": (None, "1")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIsNone(self.stored_music(response.json()["moment_id"]))

    # ---- playback state --------------------------------------------------

    def test_a_transition_is_accepted_and_becomes_current(self):
        response = self.report()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["accepted"])
        current = self.store.current_playback(self.identity["user_id"])
        self.assertEqual(current["track"]["title"], "Rainy Night")

    def test_playback_state_requires_authentication(self):
        response = self.client.put(
            "/v1/music/playback-state", json=self.playback_body()
        )
        self.assertEqual(response.status_code, 401)

    def test_playback_state_rejects_malformed_reports(self):
        cases = {
            "version": {"version": 2},
            "state": {"state": "scrubbing"},
            "sequence": {"sequence": -1},
            "sequence_type": {"sequence": "1"},
            "session": {"session_id": "x"},
            "occurred_at": {"occurred_at": "yesterday"},
            "track": {"track": {"provider": "spotify", "track_id": "a",
                                "title": "t", "artists": ["n"]}},
        }
        for name, override in cases.items():
            with self.subTest(field=name):
                response = self.report(**override)
                self.assertEqual(response.status_code, 400, response.text)

    def test_playback_state_refuses_a_progress_field(self):
        """连续进度没有入口——多一个字段就整条拒绝。"""
        body = self.playback_body()
        body["track"]["position_seconds"] = 42
        response = self.client.put(
            "/v1/music/playback-state", headers=self.headers(), json=body
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_a_completed_report_clears_the_state(self):
        self.report()
        self.report(sequence=2, state="completed")
        self.assertIsNone(self.store.current_playback(self.identity["user_id"]))


class MusicDisabledAPITests(MusicAPIHarness, unittest.TestCase):
    """开关关掉之后，音乐这条线整条不存在，其余一切照旧。"""

    music_enabled = False

    def test_config_reports_the_feature_off(self):
        self.assertEqual(
            self.config(),
            {"enabled": False, "provider": "audius", "playback_reporting": False},
        )

    def test_sending_a_song_is_refused(self):
        response = self.post_music_moment("music-off-0001")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "music_unavailable")

    def test_reporting_playback_is_refused(self):
        self.assertEqual(self.report().status_code, 403)

    def test_ordinary_text_still_works(self):
        response = self.client.post(
            "/v1/moments", headers=self.headers(),
            data={"note": "今天走了很久", "idempotency_key": "text-off-0001"},
            files={"_multipart": (None, "1")},
        )
        self.assertEqual(response.status_code, 202, response.text)

    def test_a_photo_still_works(self):
        response = self.client.post(
            "/v1/moments", headers=self.headers(),
            data={"idempotency_key": "photo-off-0001"},
            files={"image": ("photo.jpg", b"\xff\xd8\xfffake-jpeg", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 202, response.text)


class PlaybackReportingOffTests(MusicAPIHarness, unittest.TestCase):
    """只关上报，音乐本身还开着。"""

    playback_reporting = False

    def test_config_says_reporting_is_off(self):
        body = self.config()
        self.assertTrue(body["enabled"])
        self.assertFalse(body["playback_reporting"])

    def test_a_report_is_accepted_and_dropped(self):
        """没有刷新过 config 的客户端不该被卡在一个会一直重试的错误上。"""
        response = self.report()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["accepted"])
        self.assertIsNone(self.store.current_playback(self.identity["user_id"]))

    def test_a_malformed_report_is_still_refused(self):
        self.assertEqual(self.report(state="scrubbing").status_code, 400)

    def test_sending_a_song_still_works(self):
        self.assertEqual(self.post_music_moment("music-report-off").status_code, 202)


# ---------------------------------------------------------------------------
# 历史：模型看到的那份上下文
# ---------------------------------------------------------------------------


class HistoryTests(unittest.TestCase):
    def entry(self, **overrides) -> Entry:
        base = {
            "id": 1, "logged_at": "2026-08-30T10:00:00+00:00", "shot_at": None,
            "bucket": "上午", "weekday": "周日", "scene": "", "move": "speak",
            "said": "在听什么", "note": None, "reply": None, "kind": "in",
            "music_track": json.dumps(TRACK),
        }
        base.update(overrides)
        return Entry(**base)

    def test_a_shared_song_is_attributed_to_him(self):
        turns = history_turns([self.entry(note="听听这个", music_track_role="in")])
        self.assertIn("Rainy Night", turns[0]["content"])
        self.assertEqual(turns[0]["role"], "user")

    def test_a_sent_song_is_attributed_to_murmur(self):
        turns = history_turns([self.entry(note="放首歌", music_track_role="out")])
        assistant = [t for t in turns if t["role"] == "assistant"]
        self.assertIn("Rainy Night", assistant[0]["content"])

    def test_no_urls_or_ids_reach_the_model(self):
        turns = history_turns([self.entry(note="听听这个", music_track_role="in")])
        joined = " ".join(t["content"] for t in turns)
        self.assertNotIn("https://", joined)
        self.assertNotIn("abc123", joined)

    def test_history_without_music_is_unchanged(self):
        turns = history_turns([self.entry(note="今天走了很久", music_track=None)])
        self.assertEqual(turns[0]["content"], "今天走了很久")

    def test_an_unreadable_stored_song_is_simply_absent(self):
        turns = history_turns([
            self.entry(note="今天走了很久", music_track="{oops",
                       music_track_role="in")
        ])
        self.assertEqual(turns[0]["content"], "今天走了很久")


# ---------------------------------------------------------------------------
# worker：卡片什么时候发、崩溃之后还发不发
# ---------------------------------------------------------------------------


class MusicWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = settings(
            self.root, music_enabled=True, audius_api_key="k",
        )
        self.cfg = make_config(self.settings.memory_db_path)
        self.store = AppStore(self.settings.db_path)
        invite = self.store.create_invite()
        self.enrollment = self.store.redeem_invite(
            code=invite, key_id="dev-music-worker", public_key=b"k", receipt=None,
            counter=0, environment="development", device_name="iPhone",
        )
        self.user = self.enrollment.user_id

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def queue(self, note="给我放首歌", music_track=None, key="worker-music-1"):
        return self.store.create_moment(
            user_id=self.user, note=note, image_path=None,
            idempotency_key=key, request_digest=key, music_track=music_track,
        )

    def run_worker(self, result: ProcessedMoment) -> list[dict]:
        AppWorker(
            self.store, self.cfg, self.settings,
            processor=lambda job, memory, on_bubble: result,
        ).process_one()
        return self.store.events_after(self.store.conn.execute(
            "SELECT id FROM app_moments ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"], self.user)

    def card_moment(self, say=("给你放这首。",)) -> ProcessedMoment:
        return ProcessedMoment(
            Reply(scene="", move="speak", say=list(say)),
            Moment.text_only(self.cfg.tz), None,
            music_track=TRACK, music_track_role="out",
        )

    def test_the_card_arrives_after_the_words_and_before_done(self):
        self.queue()
        events = self.run_worker(self.card_moment())
        kinds = [event["event"] for event in events]
        self.assertEqual(kinds[-1], "done")
        bubbles = [e for e in events if e["event"] == "bubble"]
        self.assertEqual(bubbles[0]["data"]["text"], "给你放这首。")
        self.assertNotIn("music_track", bubbles[0]["data"])
        self.assertEqual(bubbles[-1]["data"]["music_track"], TRACK)

    def test_the_card_bubble_carries_a_text_fallback(self):
        """旧客户端把多出来的字段丢掉之后，剩下的仍然说得通。"""
        self.queue()
        events = self.run_worker(self.card_moment())
        card = [e for e in events if e["event"] == "bubble"][-1]
        self.assertEqual(card["data"]["text"], music_fallback_text(TRACK))

    def test_a_card_never_arrives_alongside_a_quiet_turn(self):
        self.queue()
        events = self.run_worker(ProcessedMoment(
            Reply(scene="", move="quiet", say=[]),
            Moment.text_only(self.cfg.tz), None,
            music_track=TRACK, music_track_role="out",
        ))
        self.assertNotIn("quiet", [e["event"] for e in events])

    def test_the_sent_card_is_persisted_with_its_side(self):
        self.queue()
        self.run_worker(self.card_moment())
        with Memory(self.settings.memory_db_path) as memory:
            chat_id, _ = thread_key("app", "direct", self.user)
            row = memory.conn.execute(
                "SELECT music_track,music_track_role FROM entries WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        self.assertEqual(json.loads(row["music_track"]), TRACK)
        self.assertEqual(row["music_track_role"], "out")

    def test_a_shared_song_is_persisted_as_his_side_and_sends_no_card(self):
        self.queue(note="听听这个", music_track=json.dumps(TRACK))
        events = self.run_worker(ProcessedMoment(
            Reply(scene="", move="speak", say=["好听。"]),
            Moment.text_only(self.cfg.tz), None,
            music_track=TRACK, music_track_role="in",
        ))
        for event in events:
            self.assertNotIn("music_track", event["data"])
        with Memory(self.settings.memory_db_path) as memory:
            chat_id, _ = thread_key("app", "direct", self.user)
            role = memory.conn.execute(
                "SELECT music_track_role FROM entries WHERE chat_id=?", (chat_id,)
            ).fetchone()["music_track_role"]
        self.assertEqual(role, "in")

    def requeue(self):
        """把崩溃后留下的那条 job 交还给下一个 worker。"""
        self.store.conn.execute(
            "UPDATE app_jobs SET status='queued',lease_until=NULL,worker_id=NULL"
        )
        self.store.conn.execute("UPDATE app_moments SET status='queued'")
        self.store.conn.commit()

    def reclaim(self):
        """重来的那一次绝不许再问模型——歌已经在库里了。"""
        AppWorker(
            self.store, self.cfg, self.settings,
            processor=lambda *_: (_ for _ in ()).throw(
                AssertionError("model must not run again")
            ),
        ).process_one()

    def cards_for(self, moment_id: str) -> list[dict]:
        return [event["data"]["music_track"]
                for event in self.store.events_after(moment_id, self.user)
                if event["data"].get("music_track")]

    def test_a_crash_before_the_card_is_sent_still_sends_it(self):
        """落库之后、发卡之前断电：重来的那一次必须补上同一首。"""
        result = self.queue()
        worker = AppWorker(
            self.store, self.cfg, self.settings,
            processor=lambda job, memory, on_bubble: self.card_moment(),
        )
        with patch.object(
            AppWorker, "_append_music_card", side_effect=RuntimeError("crash")
        ):
            worker.process_one()
        self.assertEqual(self.cards_for(result.moment_id), [])
        self.requeue()
        self.reclaim()
        self.assertEqual(self.cards_for(result.moment_id), [TRACK])

    def test_a_crash_after_the_card_is_sent_does_not_send_a_second(self):
        """卡片已经在事件流上了，重来的那一次不能再发一张。"""
        result = self.queue()
        worker = AppWorker(
            self.store, self.cfg, self.settings,
            processor=lambda job, memory, on_bubble: self.card_moment(),
        )
        with patch.object(
            AppStore, "complete_owned_job", side_effect=RuntimeError("crash")
        ):
            worker.process_one()
        self.assertEqual(self.cards_for(result.moment_id), [TRACK])
        self.requeue()
        self.reclaim()
        self.assertEqual(self.cards_for(result.moment_id), [TRACK])

    def test_an_ordinary_turn_still_streams_and_carries_no_music(self):
        self.queue(note="今天走了很久")
        events = self.run_worker(ProcessedMoment(
            Reply(scene="", move="speak", say=["嗯。"]),
            Moment.text_only(self.cfg.tz), None,
        ))
        bubbles = [e for e in events if e["event"] == "bubble"]
        self.assertEqual(len(bubbles), 1)
        self.assertNotIn("music_track", bubbles[0]["data"])

    def test_the_finished_moment_keeps_no_song(self):
        result = self.queue(note="听听这个", music_track=json.dumps(TRACK))
        self.run_worker(ProcessedMoment(
            Reply(scene="", move="speak", say=["好听。"]),
            Moment.text_only(self.cfg.tz), None,
            music_track=TRACK, music_track_role="in",
        ))
        row = self.store.conn.execute(
            "SELECT music_track,status FROM app_moments WHERE id=?",
            (result.moment_id,),
        ).fetchone()
        self.assertEqual(row["status"], "complete")
        self.assertIsNone(row["music_track"])


class MusicProcessorTests(unittest.TestCase):
    """处理器这一层：什么时候去问模型、卡片和文字怎么各就各位。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = make_config(self.root / "murmur.db")
        self.memory = Memory(self.root / "murmur.db")

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def job(self, note="给我放首歌", music_track=None):
        from murmur.app_store import Job

        return Job("j1", "m1", "u1", note, None, None, (), None, music_track)

    def process(self, job, music, on_bubble=None, playback=None):
        processor = EngineMomentProcessor(
            self.cfg, self.root, music=music, playback_state=playback,
        )
        return processor(job, self.memory, on_bubble or (lambda text: None))

    def test_a_requested_song_closes_the_bubble_stream(self):
        """点歌那一轮不流式：崩溃重来不能换歌，也不能重复发卡。"""
        streamed = []
        music = AppMusic(
            StubCatalog(results=[TRACK]),
            StubPlanner(MusicPlan(True, "discover", "rainy")),
        )
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="speak", say=["给你放这首。"])
            result = self.process(self.job(), music, on_bubble=streamed.append)
        self.assertIsNone(respond.call_args.kwargs["on_bubble"])
        self.assertEqual(streamed, [])
        self.assertEqual(result.music_card, TRACK)

    def test_an_ordinary_turn_keeps_streaming(self):
        streamed = []
        music = AppMusic(StubCatalog(), StubPlanner())
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="speak", say=["嗯。"])
            self.process(self.job("今天走了很久"), music, on_bubble=streamed.append)
        self.assertIsNotNone(respond.call_args.kwargs["on_bubble"])

    def test_the_chosen_song_is_named_in_this_turns_context(self):
        music = AppMusic(
            StubCatalog(results=[TRACK]),
            StubPlanner(MusicPlan(True, "discover", "rainy")),
        )
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="speak", say=["给你放这首。"])
            self.process(self.job(), music)
        extra = " ".join(respond.call_args.kwargs["context_extra"])
        self.assertIn("Rainy Night", extra)
        self.assertNotIn("https://", extra)

    def test_a_failed_request_says_one_fixed_line_and_is_not_silent(self):
        music = AppMusic(
            StubCatalog(results=[]),
            StubPlanner(MusicPlan(True, "exact", "moonlight", "Moonlight", None)),
        )
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="quiet", say=[])
            result = self.process(self.job("放首 Moonlight"), music)
        self.assertIn(MUSIC_NOT_FOUND_LINE, result.reply.say)
        self.assertIsNone(result.music_card)
        self.assertFalse(result.reply.silent)

    def test_being_handed_a_song_never_triggers_another_one(self):
        """他分享了一首歌，不等于在要另一首。"""
        planner = StubPlanner(error=AssertionError("planner must not run"))
        music = AppMusic(StubCatalog(track=TRACK), planner)
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="speak", say=["好听。"])
            result = self.process(
                self.job("听听这首歌", music_track=json.dumps(TRACK)), music
            )
        self.assertIsNone(result.music_card)
        self.assertEqual(result.music_track_role, "in")

    def test_music_off_leaves_the_turn_completely_untouched(self):
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="speak", say=["嗯。"])
            result = self.process(self.job(), None)
        self.assertIsNone(respond.call_args.kwargs["context_extra"])
        self.assertIsNone(result.music_track)

    def test_what_he_is_listening_to_reaches_this_turn(self):
        music = AppMusic(StubCatalog(), StubPlanner())
        playback = lambda user_id: {  # noqa: E731
            "state": "started",
            "track": {"title": "Rainy Night", "artists": ["Nova"]},
        }
        with patch("murmur.app_worker.respond") as respond:
            respond.return_value = Reply(scene="", move="speak", say=["嗯。"])
            self.process(self.job("今天好安静"), music, playback=playback)
        extra = " ".join(respond.call_args.kwargs["context_extra"])
        self.assertIn("Rainy Night", extra)


if __name__ == "__main__":
    unittest.main(verbosity=2)
