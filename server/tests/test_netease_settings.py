"""Fail-closed configuration tests for the isolated NetEase PoC."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_worker import build_catalog, build_processor  # noqa: E402
from murmur.config import Config  # noqa: E402


def settings(root: Path, **overrides) -> AppSettings:
    value = AppSettings(
        db_path=root / "murmur.db",
        memory_db_path=root / "murmur.db",
        data_root=root,
        upload_dir=root / "uploads",
        public_base_url="http://127.0.0.1:8766",
        app_id="",
        team_id="",
        attest_mode="development",
        attest_root_path=None,
        allow_development=True,
        development_token="development-token-0123456789",
        apns_key_path=None,
        apns_key_id=None,
        apns_team_id=None,
        apns_topic="com.sakura.Murmur",
        apns_environment="development",
    )
    return replace(value, **overrides)


class NeteaseSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_experimental_switches_are_off_by_default(self):
        value = settings(self.root)
        self.assertFalse(value.netease_catalog_enabled)
        self.assertFalse(value.netease_room_experiment_enabled)
        value.validate()

    def test_catalog_requires_https_when_enabled(self):
        with self.assertRaisesRegex(RuntimeError, "CATALOG_BASE_URL"):
            settings(
                self.root,
                netease_catalog_enabled=True,
                netease_catalog_base_url="http://music.163.com",
            ).validate()

    def test_room_requires_catalog_and_an_allowlist(self):
        with self.assertRaisesRegex(RuntimeError, "CATALOG_ENABLED"):
            settings(
                self.root,
                netease_room_experiment_enabled=True,
            ).validate()
        with self.assertRaisesRegex(RuntimeError, "allowlist"):
            settings(
                self.root,
                netease_catalog_enabled=True,
                netease_room_experiment_enabled=True,
            ).validate()

    def test_room_requires_an_absolute_private_secret_and_https_protocol(self):
        secret = self.root / "netease-bot.json"
        secret.write_text("{}", encoding="utf-8")
        secret.chmod(0o644)
        base = settings(
            self.root,
            netease_catalog_enabled=True,
            netease_room_experiment_enabled=True,
            netease_room_user_allowlist=frozenset({"user-1"}),
            netease_bot_secret_path=secret,
            netease_room_protocol_base_url="https://room.invalid",
        )
        with self.assertRaisesRegex(RuntimeError, "0600"):
            base.validate()
        secret.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "must use HTTPS"):
            replace(base, netease_room_protocol_base_url="http://room.invalid").validate()
        base.validate()

    def test_the_protocol_service_may_be_plain_http_only_on_loopback(self):
        """受审协议服务跑在本机回环，明文 http 在那里可以；别处不行。

        https 防的是链路上有人看得到，而回环的字节不出这台机器。但这个例外
        必须只认回环——否则它就变成「随便哪台机器都能明文」。
        """
        secret = self.root / "netease-bot.json"
        secret.write_text("{}", encoding="utf-8")
        secret.chmod(0o600)
        base = settings(
            self.root,
            netease_catalog_enabled=True,
            netease_room_experiment_enabled=True,
            netease_room_user_allowlist=frozenset({"user-1"}),
            netease_bot_secret_path=secret,
            netease_room_protocol_base_url="https://room.invalid",
        )
        for url in (
            "http://127.0.0.1:18763", "http://localhost:18763", "http://[::1]:18763",
        ):
            with self.subTest(url=url):
                replace(base, netease_room_protocol_base_url=url).validate()
        for url in (
            "http://192.168.1.5:18763",   # 局域网仍然是链路
            "http://room.invalid",
            "http://127.0.0.1.evil.example",  # 前缀像回环，主机名不是
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                    replace(base, netease_room_protocol_base_url=url).validate()

    def test_enabling_netease_makes_it_the_chat_catalog_without_removing_audius(self):
        value = settings(
            self.root,
            netease_catalog_enabled=True,
            music_enabled=True,
            audius_api_key="test-audius-key",
        )
        catalog = build_catalog(value)
        try:
            self.assertEqual(catalog.default_provider, "netease")
            self.assertEqual(catalog.providers, ("netease", "audius"))
        finally:
            catalog.close()

    def test_the_worker_processor_carries_the_music_allowlist(self):
        """灰度名单得真的走到 worker 里，不能只写在 App API 那面。"""
        value = settings(
            self.root,
            netease_catalog_enabled=True,
            music_user_allowlist=frozenset({"user-1"}),
        )
        store = SimpleNamespace(current_playback=lambda user_id: None)
        processor = build_processor(Config.load(), value, store)
        try:
            self.assertEqual(
                processor.music_user_allowlist, frozenset({"user-1"})
            )
        finally:
            processor.music.catalog.close()

    def test_a_deployment_without_music_carries_no_allowlist(self):
        processor = build_processor(
            Config.load(),
            settings(self.root, music_user_allowlist=frozenset({"user-1"})),
            None,
        )
        self.assertIsNone(processor.music)
        self.assertEqual(processor.music_user_allowlist, frozenset())


if __name__ == "__main__":
    unittest.main()
