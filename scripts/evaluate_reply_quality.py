#!/usr/bin/env python3
"""Score model replies against the fixed, synthetic reply-quality corpus.

Predictions are JSON in either ``{"case-id": ["bubble"]}`` form or a list of
``{"id": "case-id", "bubbles": [...]}`` objects.  Automatic checks are a guard
rail; the printed rubric still calls out the six judgements that need a human.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "reply_quality_cases.json"
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\u2600-\u27BF"
    "\uFE0F"
    "]"
)


def load_predictions(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {
            str(key): [str(part) for part in value]
            for key, value in raw.items()
            if isinstance(value, list)
        }
    if isinstance(raw, list):
        return {
            str(item["id"]): [str(part) for part in item.get("bubbles", [])]
            for item in raw
            if isinstance(item, dict) and "id" in item
        }
    raise ValueError("predictions must be an object or a list")


def automatic_failures(case: dict, bubbles: list[str]) -> list[str]:
    text = " ".join(part.strip() for part in bubbles if part.strip())
    failures = []
    if not text:
        return ["empty reply"]
    if _EMOJI.search(text):
        failures.append("contains emoji")
    if text.count("?") + text.count("？") > int(case.get("max_questions", 1)):
        failures.append("too many questions")
    for opener in case.get("forbidden_openers", []):
        if text.startswith(opener):
            failures.append(f"repeats forbidden opener: {opener}")
    for term in case.get("forbidden_terms", []):
        if term in text:
            failures.append(f"source mismatch: {term}")
    required = case.get("must_include_any", [])
    if required and not any(term in text for term in required):
        failures.append("misses the concrete event")
    if len(bubbles) > 3:
        failures.append("more than three bubbles")
    return failures


def evaluate(corpus: dict, predictions: dict[str, list[str]]) -> tuple[int, list[str]]:
    failures = []
    cases = corpus.get("cases", [])
    for case in cases:
        case_id = str(case["id"])
        if case_id not in predictions:
            failures.append(f"{case_id}: missing prediction")
            continue
        for reason in automatic_failures(case, predictions[case_id]):
            failures.append(f"{case_id}: {reason}")
    return len(cases), failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    predictions = load_predictions(args.predictions)
    total, failures = evaluate(corpus, predictions)
    print(f"cases={total} predictions={len(predictions)} automatic_failures={len(failures)}")
    for failure in failures:
        print(f"- {failure}")
    print("manual rubric: " + " / ".join(corpus.get("rubric", [])))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
