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
from murmur.app_music import (  # noqa: E402
    MusicCatalog,
    MusicCatalogUnavailable,
    MusicTrackInvalid,
    MusicTrackUnavailable,
    NeteaseCatalogAdapter,
    music_prompt_line,
    normalize_music_track,
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
        self.assertEqual(seen["url"].params["limit"], "5")
        self.assertNotIn("authorization", seen["headers"])
        self.assertNotIn("cookie", seen["headers"])

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


if __name__ == "__main__":
    unittest.main()
