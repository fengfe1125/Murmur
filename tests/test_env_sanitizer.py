"""Production dotenv sanitising must understand real python-dotenv syntax."""

from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "sanitize_production_env", ROOT / "scripts/sanitize_production_env.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProductionEnvSanitizerTests(unittest.TestCase):
    def test_export_and_whitespace_cannot_smuggle_test_credentials_or_local_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "# keep this comment\n"
                " export TELEGRAM_BOT_TOKEN = telegram-secret\n"
                "\tDINGTALK_CLIENT_SECRET=ding-secret\n"
                " export WECHAT_TOKEN = wechat-secret\n"
                " QQ_CLIENT_SECRET = qq-secret\n"
                " export MURMUR_APP_DB = /Users/person/private-app.db\n"
                " MURMUR_APP_MEMORY_DB = /Users/person/private-memory.db\n"
                "MURMUR_DB=/Users/person/private.db\n"
                "MURMUR_APP_DATA_ROOT=/Users/person/Murmur\n"
                "MURMUR_APP_TEMP_DIR=/Users/person/Murmur/app-uploads\n"
                "MURMUR_ENABLE_TEST_BOTS=1\n"
                "export MURMUR_AUTO_ENROLL = 1\n"
                "MURMUR_APP_ATTEST_MODE=development\n"
                "MURMUR_APP_ALLOW_DEVELOPMENT=1\n"
                "MURMUR_APP_DEVELOPMENT_TOKEN=local-dev-bypass-token-value\n"
                "MURMUR_MODEL=qwen3.7-plus\n",
                encoding="utf-8",
            )

            MODULE.sanitize(env_file, normalize_remote_paths=True)

            raw = env_file.read_text(encoding="utf-8")
            values = dotenv_values(env_file)
            for key in MODULE.PLATFORM_KEYS:
                self.assertNotIn(key, values)
            self.assertNotIn("telegram-secret", raw)
            self.assertNotIn("ding-secret", raw)
            self.assertNotIn("wechat-secret", raw)
            self.assertNotIn("qq-secret", raw)
            self.assertEqual(values["MURMUR_DB"], "./murmur.db")
            self.assertEqual(values["MURMUR_APP_DB"], "./murmur.db")
            self.assertEqual(values["MURMUR_APP_MEMORY_DB"], "./murmur.db")
            self.assertEqual(values["MURMUR_APP_DATA_ROOT"], ".")
            self.assertEqual(values["MURMUR_APP_TEMP_DIR"], "./app-uploads")
            self.assertEqual(values["MURMUR_ENABLE_TEST_BOTS"], "0")
            self.assertEqual(values["MURMUR_AUTO_ENROLL"], "0")
            self.assertEqual(values["MURMUR_APP_ATTEST_MODE"], "production")
            self.assertEqual(values["MURMUR_APP_ALLOW_DEVELOPMENT"], "0")
            self.assertEqual(values["MURMUR_APP_DEVELOPMENT_TOKEN"], "")
            self.assertNotIn("local-dev-bypass-token-value", raw)
            self.assertEqual(values["MURMUR_MODEL"], "qwen3.7-plus")
            self.assertIn("# keep this comment", raw)
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
