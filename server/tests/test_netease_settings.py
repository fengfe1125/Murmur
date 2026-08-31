"""Fail-closed configuration tests for the isolated NetEase PoC."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.app_settings import AppSettings  # noqa: E402
from murmur.app_worker import build_catalog  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
