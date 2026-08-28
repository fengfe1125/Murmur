"""降级网关的端点和 key 必须成对。

配一半是很容易犯的错（两个独立的环境变量，README 又把它们写在一行里），
而后果是静默的：`_client` 里 `api_key or cfg.api_key` 会拿主网关的凭据
去打第二家的域名，只在降级路径上触发，日志里只看得到「尝试降级」。
所以在启动时就挡住，照 MURMUR_AFFECT 拼错时的同一条规矩。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import make_config, run_unittest  # noqa: E402

from murmur import engine  # noqa: E402
from murmur.config import Config  # noqa: E402

_BOTH = {
    "MURMUR_FALLBACK_BASE_URL": "https://api.deepseek.com/beta",
    "MURMUR_FALLBACK_API_KEY": "sk-second",
}


class FallbackGatewayConfigTests(unittest.TestCase):
    def test_unset_keeps_same_gateway_behaviour(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = Config.load()
        self.assertIsNone(cfg.fallback_base_url)
        self.assertIsNone(cfg.fallback_api_key)

    def test_both_set_is_accepted(self):
        with patch.dict(os.environ, _BOTH, clear=True):
            cfg = Config.load()
        self.assertEqual(cfg.fallback_base_url, "https://api.deepseek.com/beta")
        self.assertEqual(cfg.fallback_api_key, "sk-second")

    def test_url_without_key_refuses_to_start(self):
        # 这一半最危险：主网关的 key 会被发到第二家的域名上。
        env = {"MURMUR_FALLBACK_BASE_URL": _BOTH["MURMUR_FALLBACK_BASE_URL"]}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as caught:
                Config.load()
        self.assertIn("MURMUR_FALLBACK_API_KEY", str(caught.exception))

    def test_key_without_url_refuses_to_start(self):
        env = {"MURMUR_FALLBACK_API_KEY": _BOTH["MURMUR_FALLBACK_API_KEY"]}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as caught:
                Config.load()
        self.assertIn("MURMUR_FALLBACK_BASE_URL", str(caught.exception))

    def test_blank_values_count_as_unset(self):
        # .env.example 里这两行是留空的，不该被当成"配了一半"。
        env = {"MURMUR_FALLBACK_BASE_URL": "  ", "MURMUR_FALLBACK_API_KEY": ""}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        self.assertIsNone(cfg.fallback_base_url)
        self.assertIsNone(cfg.fallback_api_key)


class LegacyKeyNameTests(unittest.TestCase):
    """改 key 的变量名是会停服的改动，旧名字得继续认。"""

    def test_new_name_is_read(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-new"}, clear=True):
            self.assertEqual(Config.load().api_key, "sk-new")

    def test_old_opencode_name_still_works(self):
        # 线上 .env 还是旧名字时不能直接没 key——那会在下一次部署后
        # 变成"每条消息都报错"，而且只在有人发消息时才炸出来。
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-old"}, clear=True):
            with self.assertLogs("murmur.config", level="WARNING") as logs:
                cfg = Config.load()
        self.assertEqual(cfg.api_key, "sk-old")
        self.assertIn("DEEPSEEK_API_KEY", "\n".join(logs.output))

    def test_new_name_wins_when_both_set(self):
        env = {"DEEPSEEK_API_KEY": "sk-new", "OPENCODE_API_KEY": "sk-old"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Config.load().api_key, "sk-new")


class ClientPairingTests(unittest.TestCase):
    """Config 之外再挡一道：_client 也被 dossier / continuity 直接调用。"""

    def test_client_rejects_a_lone_base_url(self):
        cfg = make_config(api_key="sk-primary")
        with self.assertRaises(RuntimeError):
            engine._client(cfg, base_url="https://second-gateway.test/v1")

    def test_client_rejects_a_lone_api_key(self):
        cfg = make_config(api_key="sk-primary")
        with self.assertRaises(RuntimeError):
            engine._client(cfg, api_key="sk-second")


if __name__ == "__main__":
    run_unittest()
