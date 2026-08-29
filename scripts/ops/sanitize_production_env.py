#!/usr/bin/env python3
"""Remove test-platform credentials from a production Murmur dotenv file.

Values are never printed.  The parser intentionally accepts the harmless
dotenv spelling variations supported by python-dotenv (leading whitespace,
``export KEY=...`` and whitespace around ``=``), so legacy secrets cannot slip
through migration merely because the file was hand-edited.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

PLATFORM_KEYS = {
    "TELEGRAM_BOT_TOKEN", "MURMUR_ALLOWED_CHAT_IDS",
    "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET", "DINGTALK_ALLOWED_USERS",
    "DINGTALK_INITIATIVE_USERS", "WECHAT_TOKEN", "WECHAT_ACCOUNT_ID",
    "WECHAT_ALLOWED_USERS", "WECHAT_INITIATIVE", "QQ_APP_ID",
    "QQ_CLIENT_SECRET", "QQ_ALLOWED_USERS", "QQ_INITIATIVE", "QQ_SANDBOX",
}

SAFE_PRODUCTION_VALUES = {
    "MURMUR_ENABLE_TEST_BOTS": "0",
    "MURMUR_AUTO_ENROLL": "0",
    "MURMUR_APP_ATTEST_MODE": "production",
    "MURMUR_APP_ALLOW_DEVELOPMENT": "0",
    "MURMUR_APP_DEVELOPMENT_TOKEN": "",
}

REMOTE_PATH_VALUES = {
    "MURMUR_DB": "./murmur.db",
    "MURMUR_APP_DB": "./murmur.db",
    "MURMUR_APP_MEMORY_DB": "./murmur.db",
    "MURMUR_APP_DATA_ROOT": ".",
    "MURMUR_APP_TEMP_DIR": "./app-uploads",
}

_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=",
    re.ASCII,
)


def sanitize(path: Path, *, normalize_remote_paths: bool = False) -> None:
    replacements = dict(SAFE_PRODUCTION_VALUES)
    if normalize_remote_paths:
        replacements.update(REMOTE_PATH_VALUES)

    output: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        key = match.group(1) if match else None
        if key in PLATFORM_KEYS:
            continue
        if key in replacements:
            if key not in seen:
                output.append(f"{key}={replacements[key]}")
                seen.add(key)
            continue
        output.append(line)
    for key, value in replacements.items():
        if key not in seen:
            output.append(f"{key}={value}")

    fd, temporary = tempfile.mkstemp(prefix=".murmur-env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--remote-paths", action="store_true")
    args = parser.parse_args()
    sanitize(args.env_file, normalize_remote_paths=args.remote_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
