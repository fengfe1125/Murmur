"""网易曲库适配与分享链接的窄边界测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import EnrolledClient, make_config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from murmur.app_api import create_app  # noqa: E402
from murmur.app_listen_together import RoomIPCUnavailable  # noqa: E402
from murmur.app_music import (  # noqa: E402
    MAX_MUSIC_SEARCH_CANDIDATES,
    MusicCatalog,
    MusicCatalogUnavailable,
    MusicTrackInvalid,
    MusicTrackUnavailable,
    NeteaseCatalogAdapter,
    music_prompt_line,
    normalize_music_track,
    rank_music_search_results,
)
from murmur.app_music_links import (  # noqa: E402
    MAX_SHARED_TEXT_BYTES,
    MusicLink,
    MusicLinkRejected,
)
from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_store import AppStore  # noqa: E402


def app_settings(root: Path, **overrides) -> AppSettings:
    value = AppSettings(
        db_path=root / "murmur.db", memory_db_path=root / "murmur.db",
        data_root=root, upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766", app_id="", team_id="",
        attest_mode="development", attest_root_path=None, allow_development=True,
        development_token="development-token-0123456789",
        apns_key_path=None, apns_key_id=None, apns_team_id=None,
        apns_topic="com.sakura.Murmur", apns_environment="development",
        requests_per_minute=500, timezone=ZoneInfo("Asia/Shanghai"),
    )
    return replace(value, **overrides)


NETEASE_TRACK = {
    "version": 1,
    "provider": "netease",
    "track_id": "186016",
    "title": "夜曲",
    "artists": ["周杰伦"],
    "artwork_url": "https://p1.music.126.net/cover.jpg",
    "canonical_url": "https://music.163.com/song?id=186016",
    "duration_seconds": 226,
}


def netease_song(**overrides) -> dict:
    song = {
        "id": 186016,
        "name": "夜曲",
        "artists": [{"name": "周杰伦"}],
        "album": {"picUrl": "https://p1.music.126.net/cover.jpg"},
        "duration": 226000,
        "status": 0,
    }
    song.update(overrides)
    return song


class TrackProviderTests(unittest.TestCase):
    def test_track_v1_accepts_netease_without_changing_audius(self):
        self.assertEqual(normalize_music_track(dict(NETEASE_TRACK)), NETEASE_TRACK)
        audius = {
            **NETEASE_TRACK,
            "provider": "audius",
            "canonical_url": "https://audius.co/a/track",
        }
        self.assertEqual(normalize_music_track(dict(audius)), audius)

    def test_provider_neutral_prompt_does_not_claim_a_catalog(self):
        for provider in ("audius", "netease"):
            line = music_prompt_line({**NETEASE_TRACK, "provider": provider}, actor="他")
            self.assertNotIn("Audius", line)
            self.assertNotIn("网易", line)
            self.assertIn("夜曲", line)


class StubAdapter:
    def __init__(self, provider: str, track: dict):
        self.provider = provider
        self.track = track
        self.resolved = []
        self.searched = []

    def resolve(self, track_id: str) -> dict:
        self.resolved.append(track_id)
        return dict(self.track)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        self.searched.append((query, limit))
        return [dict(self.track)]


class MusicCatalogTests(unittest.TestCase):
    def test_routes_resolve_and_search_to_the_selected_provider(self):
        audius_track = {
            **NETEASE_TRACK,
            "provider": "audius",
            "canonical_url": "https://audius.co/a/track",
        }
        audius = StubAdapter("audius", audius_track)
        netease = StubAdapter("netease", NETEASE_TRACK)
        catalog = MusicCatalog([audius, netease], default_provider="audius")

        self.assertEqual(catalog.resolve("netease", "186016"), NETEASE_TRACK)
        self.assertEqual(catalog.search("夜晚", provider="netease"), [NETEASE_TRACK])
        self.assertEqual(catalog.get_track("186016")["provider"], "audius")
        self.assertEqual(netease.resolved, ["186016"])
        self.assertEqual(netease.searched, [("夜晚", 5)])

    def test_rejects_missing_duplicate_and_mismatched_adapters(self):
        adapter = StubAdapter("netease", NETEASE_TRACK)
        with self.assertRaises(ValueError):
            MusicCatalog([adapter])
        with self.assertRaises(ValueError):
            MusicCatalog([adapter, adapter], default_provider="netease")
        wrong = StubAdapter("netease", {**NETEASE_TRACK, "provider": "audius"})
        catalog = MusicCatalog([wrong], default_provider="netease")
        with self.assertRaises(MusicCatalogUnavailable):
            catalog.resolve("netease", "186016")
        with self.assertRaises(MusicTrackUnavailable):
            catalog.resolve("spotify", "x")


def netease_catalog(handler, **kwargs) -> NeteaseCatalogAdapter:
    return NeteaseCatalogAdapter(transport=httpx.MockTransport(handler), **kwargs)


class NeteaseCatalogTests(unittest.TestCase):
    def test_search_normalises_public_metadata_and_bounds_the_request(self):
        seen = {}

        def handler(request):
            seen["url"] = request.url
            seen["headers"] = request.headers
            return httpx.Response(200, json={"result": {"songs": [netease_song()]}})

        client = netease_catalog(handler)
        self.assertEqual(client.search("夜曲", limit=99), [NETEASE_TRACK])
        self.assertEqual(seen["url"].path, "/api/search/get")
        self.assertEqual(seen["url"].params["s"], "夜曲")
        self.assertEqual(seen["url"].params["type"], "1")
        self.assertEqual(seen["url"].params["limit"], str(MAX_MUSIC_SEARCH_CANDIDATES))
        self.assertNotIn("authorization", seen["headers"])
        self.assertNotIn("cookie", seen["headers"])

    def test_search_fetches_ten_candidates_before_public_ranking(self):
        songs = [netease_song(id=index) for index in range(1, 11)]
        client = netease_catalog(
            lambda request: httpx.Response(200, json={"result": {"songs": songs}})
        )
        found = client.search("夜曲", limit=MAX_MUSIC_SEARCH_CANDIDATES)
        self.assertEqual(len(found), 10)

    def test_a_small_cached_search_does_not_hide_later_candidates(self):
        calls = []
        songs = [netease_song(id=index) for index in range(1, 11)]

        def handler(request):
            calls.append(request.url.params["limit"])
            return httpx.Response(200, json={"result": {"songs": songs}})

        client = netease_catalog(handler)
        self.assertEqual(len(client.search("夜曲", limit=1)), 1)
        self.assertEqual(len(client.search("夜曲", limit=10)), 10)
        self.assertEqual(calls, ["10"])


class NeteaseSearchRankingTests(unittest.TestCase):
    def track(self, track_id, title, artist):
        return {
            **NETEASE_TRACK,
            "track_id": str(track_id),
            "title": title,
            "artists": [artist],
            "canonical_url": f"https://music.163.com/song?id={track_id}",
        }

    def test_exact_titles_exclude_similar_and_unrelated_results(self):
        tracks = [
            self.track(1, "山楂树之恋", "程佳佳"),
            self.track(2, "山楂树の恋(DJ版)", "祝酒"),
            self.track(3, "山楂树之恋", "雷智皓"),
            self.track(4, "山楂树之恋（官方版）", "尚文婷"),
            self.track(5, "新鲜感", "雷智皓"),
        ]
        ranked = rank_music_search_results("  山楂树之恋  ", tracks)
        self.assertEqual([track["track_id"] for track in ranked], ["1", "3"])

    def test_nfkc_case_and_whitespace_are_equivalent(self):
        tracks = [
            self.track(1, "ＡＢＣ  Song", "First"),
            self.track(2, "ABC Song Remix", "Second"),
        ]
        self.assertEqual(
            [track["track_id"] for track in rank_music_search_results("abc song", tracks)],
            ["1"],
        )

    def test_artist_disambiguation_moves_the_named_artist_first(self):
        tracks = [
            self.track(1, "山楂树之恋", "雷智皓"),
            self.track(2, "山楂树之恋", "程佳佳"),
            self.track(3, "山楂树之恋（官方版）", "程佳佳"),
        ]
        ranked = rank_music_search_results("山楂树之恋 程佳佳", tracks)
        self.assertEqual([track["track_id"] for track in ranked], ["2", "1"])

    def test_artist_can_come_before_the_exact_title(self):
        tracks = [
            self.track(1, "山楂树之恋", "雷智皓"),
            self.track(2, "山楂树之恋", "程佳佳"),
            self.track(3, "山楂树之恋（官方版）", "程佳佳"),
        ]
        ranked = rank_music_search_results("程佳佳 山楂树之恋", tracks)
        self.assertEqual([track["track_id"] for track in ranked], ["2", "1"])

    def test_artist_only_search_is_not_removed_by_exact_title_filter(self):
        tracks = [self.track(1, "夜曲", "周杰伦"), self.track(2, "晴天", "周杰伦")]
        ranked = rank_music_search_results("周杰伦", tracks)
        self.assertEqual(len(ranked), 2)

    def test_artist_search_survives_a_song_whose_title_matches_the_artist_name(self):
        tracks = [
            self.track(1, "周杰伦", "别的歌手"),
            self.track(2, "夜曲", "周杰伦"),
            self.track(3, "晴天", "周杰伦"),
        ]
        ranked = rank_music_search_results("周杰伦", tracks)
        self.assertEqual([track["track_id"] for track in ranked[:2]], ["2", "3"])

    def test_no_exact_title_keeps_fuzzy_fallbacks(self):
        tracks = [self.track(1, "山楂树之恋（官方版）", "尚文婷"), self.track(2, "新鲜感", "雷智皓")]
        ranked = rank_music_search_results("山楂树之恋", tracks)
        self.assertEqual([track["track_id"] for track in ranked], ["1", "2"])

    def test_resolve_supports_detail_shape_and_caches_it(self):
        calls = []

        def handler(request):
            calls.append(request.url)
            return httpx.Response(200, json={"songs": [netease_song()]})

        client = netease_catalog(handler)
        self.assertEqual(client.resolve("186016"), NETEASE_TRACK)
        self.assertEqual(client.get_track("186016"), NETEASE_TRACK)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].params["ids"], "[186016]")

    def test_understands_compact_detail_field_names(self):
        compact = netease_song(
            artists=None,
            album=None,
            duration=None,
            ar=[{"name": "周杰伦"}],
            al={"picUrl": "https://p1.music.126.net/cover.jpg"},
            dt=226000,
        )
        del compact["artists"]
        del compact["album"]
        del compact["duration"]
        client = netease_catalog(lambda request: httpx.Response(200, json={"songs": [compact]}))
        self.assertEqual(client.resolve("186016"), NETEASE_TRACK)

    def test_unavailable_track_is_negatively_cached(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(200, json={"songs": []})

        client = netease_catalog(handler)
        for _ in range(2):
            with self.assertRaises(MusicTrackUnavailable):
                client.resolve("186016")
        self.assertEqual(len(calls), 1)

    def test_search_skips_unavailable_and_incomplete_results(self):
        payload = {"result": {"songs": [
            netease_song(id=1, noCopyrightRcmd={"type": 1}),
            netease_song(id=2, privilege={"st": -200}),
            netease_song(id=3, artists=[]),
            netease_song(id=4),
        ]}}
        client = netease_catalog(lambda request: httpx.Response(200, json=payload))
        self.assertEqual([track["track_id"] for track in client.search("夜曲")], ["4"])

    def test_rejects_non_numeric_and_mismatched_ids(self):
        client = netease_catalog(lambda request: httpx.Response(200, json={"songs": [netease_song(id=7)]}))
        with self.assertRaises(MusicTrackInvalid):
            client.resolve("not-an-id")
        with self.assertRaises(MusicCatalogUnavailable):
            client.resolve("186016")

    def test_transport_and_server_failures_are_retryable(self):
        def broken(request):
            raise httpx.ConnectError("offline", request=request)

        with self.assertRaises(MusicCatalogUnavailable) as caught:
            netease_catalog(broken).search("夜曲")
        self.assertTrue(caught.exception.retryable)
        for status in (429, 500, 503):
            with self.subTest(status=status):
                client = netease_catalog(lambda request, s=status: httpx.Response(s))
                with self.assertRaises(MusicCatalogUnavailable):
                    client.search("夜曲")


class ResolvingCatalog:
    def __init__(self):
        self.calls = []

    def resolve(self, provider: str, track_id: str) -> dict:
        self.calls.append((provider, track_id))
        return {**NETEASE_TRACK, "track_id": track_id,
                "canonical_url": f"https://music.163.com/song?id={track_id}"}


def public_dns(host: str) -> list[str]:
    return ["93.184.216.34"]


class MusicLinkTests(unittest.TestCase):
    def test_long_and_fragment_links_extract_id_then_resolve(self):
        for url in (
            "https://music.163.com/song?id=186016&utm_source=share",
            "https://music.163.com/#/song?id=186016",
            "https://music.163.com/m/song?id=186016",
        ):
            with self.subTest(url=url):
                catalog = ResolvingCatalog()
                link = MusicLink(catalog, address_resolver=public_dns)
                self.assertEqual(link.parse_shared_text(f"分享歌曲：{url}。"), NETEASE_TRACK)
                self.assertEqual(catalog.calls, [("netease", "186016")])

    def test_unrelated_text_and_other_netease_pages_are_not_music(self):
        catalog = ResolvingCatalog()
        link = MusicLink(catalog, address_resolver=public_dns)
        self.assertIsNone(link.parse_shared_text("今天没有链接"))
        self.assertIsNone(link.parse_shared_text("https://example.com/song?id=186016"))
        self.assertIsNone(link.parse_shared_text("https://music.163.com/playlist?id=1"))
        self.assertEqual(catalog.calls, [])

    def test_supported_song_with_bad_id_is_rejected(self):
        link = MusicLink(ResolvingCatalog(), address_resolver=public_dns)
        for url in (
            "https://music.163.com/song",
            "https://music.163.com/song?id=",
            "https://music.163.com/song?id=one",
            "https://music.163.com/song?id=1&id=2",
        ):
            with self.subTest(url=url):
                with self.assertRaises(MusicLinkRejected):
                    link.parse_shared_text(url)

    def test_official_short_link_follows_bounded_redirect_and_resolves(self):
        seen = []

        def handler(request):
            seen.append(request.url.host)
            return httpx.Response(
                302,
                headers={"location": "https://music.163.com/song?id=186016"},
            )

        catalog = ResolvingCatalog()
        link = MusicLink(
            catalog,
            transport=httpx.MockTransport(handler),
            address_resolver=public_dns,
        )
        self.assertEqual(link.parse_shared_text("听听 https://y.music.163.com/xYz"), NETEASE_TRACK)
        self.assertEqual(seen, ["y.music.163.com"])
        self.assertEqual(catalog.calls, [("netease", "186016")])

    def test_search_results_never_become_the_answer_to_a_later_resolve(self):
        """搜索接口不返回 picUrl，所以它的结果不能冒充完整的歌。

        以前 search 会把这份缺封面的版本写进 resolve 缓存，于是同一首歌有没有
        封面，取决于它是先被搜到还是先被解析——这就是卡片封面「有时候有有时候
        没有」的服务端那一半。
        """
        detail_calls = []

        def handler(request):
            if "/api/search/get" in str(request.url):
                # 真实搜索响应里 album 没有 picUrl。
                return httpx.Response(200, json={"result": {"songs": [{
                    "id": 186016, "name": "晴天", "duration": 269000,
                    "artists": [{"name": "周杰伦"}], "album": {"name": "叶惠美"},
                }]}})
            detail_calls.append(str(request.url))
            return httpx.Response(200, json={"songs": [{
                "id": 186016, "name": "晴天", "duration": 269000,
                "artists": [{"name": "周杰伦"}],
                "album": {"name": "叶惠美", "picUrl": "https://p1.music.126.net/cover.jpg"},
            }]})

        adapter = NeteaseCatalogAdapter(transport=httpx.MockTransport(handler))
        try:
            found = adapter.search("晴天", limit=1)
            self.assertNotIn("artwork_url", found[0])
            # 关键：这一次必须真的去查详情，而不是拿搜索那份顶。
            resolved = adapter.resolve("186016")
            self.assertEqual(
                resolved["artwork_url"], "https://p1.music.126.net/cover.jpg"
            )
            self.assertEqual(len(detail_calls), 1)
        finally:
            adapter.close()

    def test_the_real_mobile_share_ends_on_y_music_without_redirecting_again(self):
        """手机端分享的真实形状：163cn.tv 跳一次就落在 y.music 的歌曲页。

        那一跳返回 200 而不是又一个 302，歌曲 id 就在地址里。以前代码要求
        短链必须一路跳到 music.163.com，于是手机上分享的每一首歌都被判成
        「这个链接打不开成一首歌」——这条竖切最主要的入口整个是坏的。
        """
        seen = []

        def handler(request):
            seen.append(request.url.host)
            return httpx.Response(302, headers={
                "location":
                    "https://y.music.163.com/m/song?fx-wechatnew=t1&id=13918916",
            })

        catalog = ResolvingCatalog()
        link = MusicLink(
            catalog,
            transport=httpx.MockTransport(handler),
            address_resolver=public_dns,
        )
        track = link.parse_shared_text(
            "分享颜人中的单曲《嗜好》https://163cn.tv/bfjiU65m (@网易云音乐)"
        )
        self.assertEqual(track["track_id"], "13918916")
        self.assertEqual(track["provider"], "netease")
        # 只请求了短链那一跳；y.music 那一跳不再被要求继续重定向。
        self.assertEqual(seen, ["163cn.tv"])
        self.assertEqual(catalog.calls, [("netease", "13918916")])

    def test_a_pasted_y_music_song_link_resolves_without_any_request(self):
        """直接粘 y.music 的歌曲地址，不该再去网络上跟一次。"""
        catalog = ResolvingCatalog()
        link = MusicLink(
            catalog,
            transport=httpx.MockTransport(
                lambda request: self.fail("不应该发起请求")
            ),
            address_resolver=public_dns,
        )
        self.assertEqual(
            link.parse_shared_text("https://y.music.163.com/m/song?id=186016"),
            NETEASE_TRACK,
        )
        self.assertEqual(catalog.calls, [("netease", "186016")])

    def test_supports_the_official_163cn_short_host(self):
        link = MusicLink(
            ResolvingCatalog(),
            transport=httpx.MockTransport(lambda request: httpx.Response(
                301, headers={"location": "https://music.163.com/song?id=186016"}
            )),
            address_resolver=public_dns,
        )
        self.assertEqual(link.parse_shared_text("https://163cn.tv/abc"), NETEASE_TRACK)

    def test_redirect_never_leaves_the_allowlist(self):
        for target in (
            "http://music.163.com/song?id=186016",
            "https://evil.example/song?id=186016",
            "https://127.0.0.1/song?id=186016",
            "https://user:pw@music.163.com/song?id=186016",
        ):
            with self.subTest(target=target):
                link = MusicLink(
                    ResolvingCatalog(),
                    transport=httpx.MockTransport(lambda request, t=target: httpx.Response(
                        302, headers={"location": t}
                    )),
                    address_resolver=public_dns,
                )
                with self.assertRaises(MusicLinkRejected):
                    link.parse_shared_text("https://y.music.163.com/x")

    def test_private_dns_is_rejected_before_any_request(self):
        requested = []
        link = MusicLink(
            ResolvingCatalog(),
            transport=httpx.MockTransport(lambda request: requested.append(1)),
            address_resolver=lambda host: ["127.0.0.1"],
        )
        with self.assertRaises(MusicLinkRejected):
            link.parse_shared_text("https://y.music.163.com/x")
        self.assertEqual(requested, [])

    def test_redirect_limit_non_redirect_and_oversize_are_rejected(self):
        loop = MusicLink(
            ResolvingCatalog(),
            transport=httpx.MockTransport(lambda request: httpx.Response(
                302, headers={"location": "https://y.music.163.com/again"}
            )),
            address_resolver=public_dns,
        )
        with self.assertRaises(MusicLinkRejected):
            loop.parse_shared_text("https://y.music.163.com/start")
        no_redirect = MusicLink(
            ResolvingCatalog(),
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
            address_resolver=public_dns,
        )
        with self.assertRaises(MusicLinkRejected):
            no_redirect.parse_shared_text("https://y.music.163.com/start")
        with self.assertRaises(MusicLinkRejected):
            no_redirect.parse_shared_text("x" * (MAX_SHARED_TEXT_BYTES + 1))


class FailingCatalog:
    """A catalog that always answers one way, to shape an API error."""

    def __init__(self, error):
        self.error = error

    def resolve(self, provider: str, track_id: str) -> dict:
        raise self.error


class ResolveSharedAPITests(unittest.TestCase):
    """/v1/music/resolve-shared：先确认，再由人自己发出去。"""

    SHARE = "分享周杰伦的单曲《随便什么名字》https://music.163.com/song?id=186016"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AppStore(self.root / "murmur.db")
        self.clients = []
        self.device = EnrolledClient(
            self._client(app_settings(self.root)), self.store,
            app_settings(self.root).development_token,
        )

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.store.close()
        self.tmp.cleanup()

    def _client(self, settings, **kwargs) -> TestClient:
        client = TestClient(create_app(
            settings, cfg=make_config(settings.memory_db_path), store=self.store,
            **kwargs,
        ))
        self.clients.append(client)
        return client

    def use(self, *, catalog=None, enabled: bool = True) -> TestClient:
        settings = app_settings(self.root, netease_catalog_enabled=enabled)
        link = MusicLink(catalog or ResolvingCatalog(), address_resolver=public_dns)
        self.device.client = self._client(settings, music_link=link)
        return self.device.client

    def post(self, client, text: str, key: str = "share-000000001"):
        return client.post(
            "/v1/music/resolve-shared", headers=self.device.headers(),
            json={"text": text, "idempotency_key": key},
        )

    def test_the_route_is_closed_until_the_catalog_switch_is_on(self):
        client = self.use(enabled=False)
        response = self.post(client, self.SHARE)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "music_unavailable")

    def test_a_shared_link_comes_back_as_catalog_metadata_not_share_prose(self):
        catalog = ResolvingCatalog()
        response = self.post(self.use(catalog=catalog), self.SHARE)
        self.assertEqual(response.status_code, 200, response.text)
        track = response.json()["track"]
        self.assertEqual(catalog.calls, [("netease", "186016")])
        # 分享文案里的歌名是攻击面，不是元数据。
        self.assertEqual(track["title"], NETEASE_TRACK["title"])
        self.assertNotIn("随便什么名字", json.dumps(track, ensure_ascii=False))

    def test_ordinary_chat_is_not_an_error_but_is_not_a_song_either(self):
        response = self.post(self.use(), "今天下雨了，想听点安静的")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "music_link_absent")

    def test_an_unsafe_or_malformed_share_is_refused_with_its_own_code(self):
        client = self.use()
        for text in (
            "https://music.163.com/song?id=not-a-number",
            "https://evil.invalid/song?id=186016 https://music.163.com/user/home?id=1",
        ):
            with self.subTest(text=text):
                response = self.post(client, text)
                self.assertIn(response.status_code, {400, 422})

    def test_an_oversized_share_never_reaches_the_parser(self):
        response = self.post(self.use(), "x" * (MAX_SHARED_TEXT_BYTES + 1))
        self.assertEqual(response.status_code, 413, response.text)

    def test_a_catalog_outage_is_retryable_and_a_gone_song_is_not(self):
        outage = self.post(
            self.use(catalog=FailingCatalog(MusicCatalogUnavailable("down"))),
            self.SHARE,
        )
        self.assertEqual(outage.status_code, 503, outage.text)
        self.assertTrue(outage.json()["error"]["retryable"])

        gone = self.post(
            self.use(catalog=FailingCatalog(MusicTrackUnavailable("gone"))),
            self.SHARE,
        )
        self.assertEqual(gone.status_code, 404, gone.text)
        self.assertFalse(gone.json()["error"]["retryable"])

    def test_a_missing_idempotency_key_is_a_validation_error(self):
        client = self.use()
        response = client.post(
            "/v1/music/resolve-shared", headers=self.device.headers(),
            json={"text": self.SHARE},
        )
        self.assertEqual(response.status_code, 400, response.text)


class MusicAttachmentGateTests(unittest.TestCase):
    """两个 provider 之后，"音乐开着"不再等于"这家开着"。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AppStore(self.root / "murmur.db")
        self.clients = []
        self.device = EnrolledClient(
            self._client(app_settings(self.root)), self.store,
            app_settings(self.root).development_token,
        )

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.store.close()
        self.tmp.cleanup()

    def _client(self, settings) -> TestClient:
        client = TestClient(create_app(
            settings, cfg=make_config(settings.memory_db_path), store=self.store,
        ))
        self.clients.append(client)
        return client

    def send(self, track: dict, *, key: str, **switches):
        self.device.client = self._client(app_settings(self.root, **switches))
        return self.device.client.post(
            "/v1/moments", headers=self.device.headers(),
            data={
                "note": f"{track['title']} — {'、'.join(track['artists'])}",
                "idempotency_key": key,
                "music_track": json.dumps(track, ensure_ascii=False),
            },
            # A song is its own turn with no photo; this part only forces the
            # multipart encoding the endpoint requires.
            files={"_multipart": (None, "1")},
        )

    def test_a_card_for_a_switched_off_provider_is_refused(self):
        response = self.send(
            dict(NETEASE_TRACK), key="netease-off-0001",
            music_enabled=True, audius_api_key="k",
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "music_unavailable")

    def test_the_same_card_goes_through_once_that_provider_is_on(self):
        response = self.send(
            dict(NETEASE_TRACK), key="netease-on-00001",
            music_enabled=True, audius_api_key="k", netease_catalog_enabled=True,
        )
        self.assertEqual(response.status_code, 202, response.text)

    def test_audius_still_needs_its_own_switch(self):
        audius = {
            **NETEASE_TRACK, "provider": "audius",
            "canonical_url": "https://audius.co/a/track",
        }
        response = self.send(
            audius, key="audius-off-00001", netease_catalog_enabled=True,
        )
        self.assertEqual(response.status_code, 403, response.text)


class NeteaseCompleteTests(unittest.TestCase):
    """`complete()`：给搜索结果补封面，且不动 `search()` 的那条不变量。

    这一组和 `test_search_results_never_become_the_answer_to_a_later_resolve`
    是一对读的：那条说「搜索结果不能冒充完整的歌」，这里说「要封面就显式地
    再走一趟详情」。两件事不矛盾，缺一条就会有人把补全塞回 search 里。
    """

    SEARCH_SONG = {
        "id": 186016, "name": "夜曲", "duration": 226000,
        "artists": [{"name": "周杰伦"}], "album": {"name": "十一月的萧邦"},
    }

    def test_completion_fills_covers_with_one_batched_detail_call(self):
        detail_urls = []

        def handler(request):
            if "/api/search/get" in str(request.url):
                return httpx.Response(200, json={"result": {"songs": [
                    self.SEARCH_SONG,
                    {**self.SEARCH_SONG, "id": 186017, "name": "晴天"},
                ]}})
            detail_urls.append(str(request.url))
            return httpx.Response(200, json={"songs": [
                # 顺序刻意和请求相反：回填必须按 id，不能按位置。
                {**self.SEARCH_SONG, "id": 186017, "name": "晴天",
                 "album": {"name": "叶惠美", "picUrl": "https://p1.music.126.net/b.jpg"}},
                {**self.SEARCH_SONG,
                 "album": {"name": "十一月的萧邦", "picUrl": "https://p1.music.126.net/a.jpg"}},
            ]})

        adapter = NeteaseCatalogAdapter(transport=httpx.MockTransport(handler))
        try:
            # Artist search intentionally keeps both titles; an exact-title
            # search now narrows to exact-title versions before completion.
            found = adapter.search("周杰伦", limit=2)
            self.assertNotIn("artwork_url", found[0])
            completed = adapter.complete(found)
            self.assertEqual(len(detail_urls), 1, "一页结果只该多花一个请求")
            self.assertIn("186016", detail_urls[0])
            self.assertIn("186017", detail_urls[0])
            covers = {t["track_id"]: t.get("artwork_url") for t in completed}
            self.assertEqual(covers["186016"], "https://p1.music.126.net/a.jpg")
            self.assertEqual(covers["186017"], "https://p1.music.126.net/b.jpg")
            # 标题不被详情悄悄改写：补的是缺的东西，不是重新解析一首歌。
            self.assertEqual([t["title"] for t in completed], ["夜曲", "晴天"])
        finally:
            adapter.close()

    def test_completion_writes_the_resolve_cache_so_a_later_resolve_is_free(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if "/api/search/get" in str(request.url):
                return httpx.Response(200, json={"result": {"songs": [self.SEARCH_SONG]}})
            return httpx.Response(200, json={"songs": [{
                **self.SEARCH_SONG,
                "album": {"name": "十一月的萧邦", "picUrl": "https://p1.music.126.net/a.jpg"},
            }]})

        adapter = NeteaseCatalogAdapter(transport=httpx.MockTransport(handler))
        try:
            adapter.complete(adapter.search("夜曲", limit=1))
            self.assertEqual(len(calls), 2)
            # 补全拿到的就是 detail 那一份，所以写缓存在这里是对的——
            # 这正是 search 自己不写缓存所保护的东西。
            resolved = adapter.resolve("186016")
            self.assertEqual(resolved["artwork_url"], "https://p1.music.126.net/a.jpg")
            self.assertEqual(len(calls), 2, "resolve 应当命中补全写下的缓存")
        finally:
            adapter.close()

    def test_a_catalog_failure_costs_the_cover_not_the_search(self):
        def handler(request):
            if "/api/search/get" in str(request.url):
                return httpx.Response(200, json={"result": {"songs": [self.SEARCH_SONG]}})
            return httpx.Response(503)

        adapter = NeteaseCatalogAdapter(transport=httpx.MockTransport(handler))
        try:
            found = adapter.search("夜曲", limit=1)
            completed = adapter.complete(found)
            self.assertEqual(completed, found)
            self.assertNotIn("artwork_url", completed[0])
        finally:
            adapter.close()

    def test_a_non_numeric_id_never_reaches_the_ids_array(self):
        """ids 是拼进查询参数的，所以只有数字 id 能进去。"""
        detail_urls = []

        def handler(request):
            detail_urls.append(str(request.url))
            return httpx.Response(200, json={"songs": []})

        adapter = NeteaseCatalogAdapter(transport=httpx.MockTransport(handler))
        try:
            hostile = {**NETEASE_TRACK, "track_id": "1,2]&x=["}
            del hostile["artwork_url"]
            self.assertEqual(adapter.complete([hostile]), [hostile])
            self.assertEqual(detail_urls, [], "没有可用 id 时不该发请求")
        finally:
            adapter.close()


class SearchingCatalog:
    """A catalog double with the search seam the route actually calls."""

    def __init__(self, tracks=None, error=None):
        self.calls = []
        self.tracks = [NETEASE_TRACK] if tracks is None else tracks
        self.error = error

    def resolve(self, provider: str, track_id: str) -> dict:
        return {**NETEASE_TRACK, "track_id": track_id}

    def search(self, query, limit=5, *, provider=None, complete=False):
        self.calls.append((query, limit, provider, complete))
        if self.error is not None:
            raise self.error
        return [dict(track) for track in self.tracks[:limit]]


class PlayableRoomClient:
    def __init__(self, playable_ids):
        self.playable_ids = set(playable_ids)
        self.calls = []

    def playable_song_ids(self, *, user_id, song_ids):
        self.calls.append((user_id, list(song_ids)))
        return [song_id for song_id in song_ids if song_id in self.playable_ids]


class FailingPlayableRoomClient:
    def playable_song_ids(self, *, user_id, song_ids):
        raise RoomIPCUnavailable("phase 0 unavailable")


class MusicSearchAPITests(unittest.TestCase):
    """/v1/music/search：选歌器要的那一页。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AppStore(self.root / "murmur.db")
        self.clients = []
        self.device = EnrolledClient(
            self._client(app_settings(self.root)), self.store,
            app_settings(self.root).development_token,
        )

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.store.close()
        self.tmp.cleanup()

    def _client(self, settings, **kwargs) -> TestClient:
        client = TestClient(create_app(
            settings, cfg=make_config(settings.memory_db_path), store=self.store,
            **kwargs,
        ))
        self.clients.append(client)
        return client

    def use(self, *, catalog=None, enabled: bool = True, rooms=None) -> TestClient:
        overrides = {"netease_catalog_enabled": enabled}
        if rooms is not None:
            secret = self.root / "netease-bot.json"
            secret.write_text("{}", encoding="utf-8")
            secret.chmod(0o600)
            overrides.update({
                "netease_room_experiment_enabled": True,
                "netease_room_user_allowlist": frozenset({self.device.user_id}),
                "netease_bot_secret_path": secret,
                "netease_room_protocol_base_url": "http://127.0.0.1:18763",
            })
        settings = app_settings(self.root, **overrides)
        self.catalog = catalog or SearchingCatalog()
        self.device.client = self._client(
            settings, music_catalog=self.catalog, room_client=rooms
        )
        return self.device.client

    def post(self, client, body):
        return client.post(
            "/v1/music/search", headers=self.device.headers(), json=body,
        )

    def test_the_route_is_closed_until_the_catalog_switch_is_on(self):
        response = self.post(self.use(enabled=False), {"query": "夜曲"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "music_unavailable")

    def test_a_query_comes_back_as_normalised_tracks(self):
        response = self.post(self.use(), {"query": "  夜曲   周杰伦 "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tracks"], [NETEASE_TRACK])
        # 空白折叠后才交给曲库，且补全是开着的——这一页每行都要给人看。
        self.assertEqual(
            self.catalog.calls,
            [("夜曲 周杰伦", MAX_MUSIC_SEARCH_CANDIDATES, "netease", True)],
        )

    def test_omitted_purpose_defaults_to_share_without_room_filtering(self):
        rooms = PlayableRoomClient([])
        response = self.post(self.use(rooms=rooms), {"query": "夜曲"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tracks"], [NETEASE_TRACK])
        self.assertEqual(rooms.calls, [])

    def test_listen_together_filters_by_robot_account_playability(self):
        second = {
            **NETEASE_TRACK, "track_id": "186017", "title": "晴天",
            "canonical_url": "https://music.163.com/song?id=186017",
        }
        rooms = PlayableRoomClient({"186017"})
        response = self.post(
            self.use(catalog=SearchingCatalog([NETEASE_TRACK, second]), rooms=rooms),
            {"query": "周杰伦", "purpose": "listen_together"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([t["track_id"] for t in response.json()["tracks"]], ["186017"])
        self.assertEqual(len(rooms.calls), 1)

    def test_playability_filter_can_select_an_exact_candidate_after_public_page_limit(self):
        tracks = [
            {
                **NETEASE_TRACK,
                "track_id": str(186016 + index),
                "title": "同名歌",
                "canonical_url": f"https://music.163.com/song?id={186016 + index}",
            }
            for index in range(7)
        ]
        rooms = PlayableRoomClient({tracks[5]["track_id"]})
        response = self.post(
            self.use(catalog=SearchingCatalog(tracks), rooms=rooms),
            {"query": "同名歌", "purpose": "listen_together", "limit": 5},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["track_id"] for item in response.json()["tracks"]],
            [tracks[5]["track_id"]],
        )

    def test_shanzhashuzhilian_selects_the_playable_exact_title_without_hardcoding(self):
        def track(track_id, title, artist):
            return {
                **NETEASE_TRACK,
                "track_id": str(track_id), "title": title, "artists": [artist],
                "canonical_url": f"https://music.163.com/song?id={track_id}",
            }

        catalog = SearchingCatalog([
            track(1381755293, "山楂树之恋", "程佳佳"),
            track(2737771303, "山楂树の恋(DJ版)", "祝酒"),
            track(446557635, "山楂树之恋", "雷智皓"),
            track(1383727340, "山楂树之恋（官方版）", "尚文婷"),
            track(446627373, "新鲜感", "雷智皓"),
        ])
        rooms = PlayableRoomClient({"1381755293"})
        response = self.post(
            self.use(catalog=catalog, rooms=rooms),
            {"query": "山楂树之恋", "purpose": "listen_together"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [(item["title"], item["artists"]) for item in response.json()["tracks"]],
            [("山楂树之恋", ["程佳佳"])],
        )

    def test_no_common_playable_track_has_a_specific_empty_reason(self):
        rooms = PlayableRoomClient([])
        response = self.post(
            self.use(rooms=rooms),
            {"query": "夜曲", "purpose": "listen_together"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tracks"], [])
        self.assertEqual(response.json()["empty_reason"], "no_common_playable_track")

    def test_listen_together_playability_outage_is_retryable_not_a_generic_500(self):
        response = self.post(
            self.use(rooms=FailingPlayableRoomClient()),
            {"query": "夜曲", "purpose": "listen_together"},
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "room_ipc_unavailable")
        self.assertTrue(response.json()["error"]["retryable"])

    def test_invalid_purpose_is_rejected_before_catalog_or_room_calls(self):
        rooms = PlayableRoomClient({"186016"})
        response = self.post(
            self.use(rooms=rooms), {"query": "夜曲", "purpose": "broadcast"}
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.catalog.calls, [])
        self.assertEqual(rooms.calls, [])

    def test_non_string_purpose_is_rejected_as_validation_error(self):
        response = self.post(self.use(), {"query": "夜曲", "purpose": {"bad": True}})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.catalog.calls, [])

    def test_finding_nothing_is_an_answer_not_an_error(self):
        response = self.post(self.use(catalog=SearchingCatalog(tracks=[])), {"query": "没有这首"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tracks"], [])

    def test_an_over_long_query_is_a_validation_error_not_a_bad_gateway(self):
        response = self.post(self.use(), {"query": "夜" * 121})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertEqual(self.catalog.calls, [], "越界的查询不该打到曲库")

    def test_an_empty_query_is_refused(self):
        for body in ({"query": "   "}, {"query": ""}, {"query": 7}, {}):
            with self.subTest(body=body):
                response = self.post(self.use(), body)
                self.assertEqual(response.status_code, 400, response.text)

    def test_asking_for_more_than_the_catalog_gives_is_refused_not_clamped(self):
        """悄悄钳到 5 会让客户端写出永远停不下来的分页。"""
        response = self.post(self.use(), {"query": "夜曲", "limit": 20})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        for bad in (0, -1, True, "5", 2.5):
            with self.subTest(limit=bad):
                self.assertEqual(
                    self.post(self.use(), {"query": "夜曲", "limit": bad}).status_code, 400,
                )

    def test_a_catalog_outage_is_retryable_and_a_bad_shape_is_not(self):
        outage = self.post(
            self.use(catalog=SearchingCatalog(error=MusicCatalogUnavailable("down"))),
            {"query": "夜曲"},
        )
        self.assertEqual(outage.status_code, 503, outage.text)
        self.assertTrue(outage.json()["error"]["retryable"])

        broken = self.post(
            self.use(catalog=SearchingCatalog(error=MusicTrackUnavailable("weird"))),
            {"query": "夜曲"},
        )
        self.assertEqual(broken.status_code, 502, broken.text)

    def test_the_config_route_advertises_search_so_the_client_can_offer_it(self):
        client = self.use()
        config = client.get("/v1/music/config", headers=self.device.headers())
        self.assertEqual(config.status_code, 200, config.text)
        netease = [
            item for item in config.json()["providers"] if item["id"] == "netease"
        ]
        self.assertEqual(len(netease), 1)
        self.assertIn("search", netease[0]["capabilities"])


if __name__ == "__main__":
    unittest.main()
