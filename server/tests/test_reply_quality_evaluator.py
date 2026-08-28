from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

from _helpers import run_unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check" / "evaluate_reply_quality.py"
spec = importlib.util.spec_from_file_location("reply_quality_evaluator", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ReplyQualityEvaluatorTests(unittest.TestCase):
    def test_corpus_contains_thirty_unique_synthetic_cases(self):
        corpus = json.loads(module.DEFAULT_CORPUS.read_text(encoding="utf-8"))
        ids = [case["id"] for case in corpus["cases"]]
        self.assertEqual(len(ids), 30)
        self.assertEqual(len(set(ids)), 30)
        self.assertEqual(len(corpus["rubric"]), 6)

    def test_automatic_checks_cover_repetition_source_and_emoji(self):
        case = {
            "forbidden_openers": ["怎么了呀"],
            "forbidden_terms": ["西瓜"],
            "must_include_any": ["泡面"],
            "max_questions": 0,
        }
        failures = module.automatic_failures(case, ["怎么了呀☀️ 西瓜？"])
        self.assertIn("contains emoji", failures)
        self.assertIn("too many questions", failures)
        self.assertIn("repeats forbidden opener: 怎么了呀", failures)
        self.assertIn("source mismatch: 西瓜", failures)
        self.assertIn("misses the concrete event", failures)


if __name__ == "__main__":
    run_unittest()
