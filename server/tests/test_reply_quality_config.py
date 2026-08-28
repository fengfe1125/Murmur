from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import run_unittest  # noqa: E402

from murmur.config import Config  # noqa: E402


class ReplyQualityConfigTests(unittest.TestCase):
    def test_p1_features_default_off_for_staged_rollout(self):
        names = {
            "MURMUR_OPEN_LOOPS",
            "MURMUR_REPLY_DIRECTIVES",
            "MURMUR_PROACTIVE_MATERIALS",
            "MURMUR_AFFECT",
        }
        clean = {key: value for key, value in os.environ.items() if key not in names}
        with patch.dict(os.environ, clean, clear=True):
            cfg = Config.load()
        self.assertFalse(cfg.open_loops)
        self.assertFalse(cfg.reply_directives)
        self.assertFalse(cfg.proactive_materials)
        self.assertFalse(cfg.affect)

    def test_each_p1_feature_can_be_enabled_independently(self):
        with patch.dict(
            os.environ,
            {
                "MURMUR_OPEN_LOOPS": "1",
                "MURMUR_REPLY_DIRECTIVES": "0",
                "MURMUR_PROACTIVE_MATERIALS": "yes",
                "MURMUR_AFFECT": "false",
            },
            clear=True,
        ):
            cfg = Config.load()
        self.assertTrue(cfg.open_loops)
        self.assertFalse(cfg.reply_directives)
        self.assertTrue(cfg.proactive_materials)
        self.assertFalse(cfg.affect)

    def test_misspelled_flag_fails_closed(self):
        with patch.dict(os.environ, {"MURMUR_AFFECT": "sometimes"}, clear=True):
            with self.assertRaises(ValueError):
                Config.load()


if __name__ == "__main__":
    run_unittest()
