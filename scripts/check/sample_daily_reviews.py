"""Opt-in model QA. Only fixed fictional fixtures are sent; no databases are opened."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=False)
    from murmur.config import Config
    from murmur.daily_review import ReviewGenerator, validate
    from murmur.dossier import _loads_tolerant
    from murmur.engine import _client
    from murmur.persona import SYSTEM

    cfg = Config.load()
    cases = json.loads(
        (ROOT / "server/tests/fixtures/daily_review_quality.json").read_text()
    )

    def sample(case):
        source = {
            "id": case["id"],
            "user_text": case["note"],
            "observation": case["observation"],
            "shot_at": case["shot_at"],
            "occurred_at": "2026-09-21T20:00:00+08:00",
            "channel": "home",
        }
        task = {
            "day": "2026-09-21",
            "timezone": "Asia/Shanghai",
            "question": "",
            "sources": [source],
        }
        result = {
            "case": case,
            "model": cfg.model,
            "memory_model": cfg.memory_model or cfg.model,
        }
        try:
            output = ReviewGenerator(cfg)(task)
            validate(output, [source])
            result["review"] = output
            messages = [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            ]
            kwargs = {}
            if cfg.json_prefix:
                messages.append({"role": "assistant", "content": "{", "prefix": True})
            else:
                kwargs["response_format"] = {"type": "json_object"}
            reply = _client(cfg).chat.completions.create(
                model=cfg.model, messages=messages, max_tokens=1200, **kwargs
            )
            raw = reply.choices[0].message.content or ""
            if cfg.json_prefix and raw.lstrip().startswith(('"', "“")):
                raw = "{" + raw
            result["chat"] = _loads_tolerant(raw)
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(sample, cases))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Fictional cases: {len(results)}; failures: {sum('error_type' in r for r in results)}"
    )
    return int(any("error_type" in r for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
