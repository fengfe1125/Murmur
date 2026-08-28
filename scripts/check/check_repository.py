#!/usr/bin/env python3
"""Check repository layout, active documentation and tracked-data boundaries.

Only paths and versioned documentation are read; runtime files and credentials
are never opened. Archive bodies retain historical links intentionally.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_LAYOUT = "either"  # The directory-migration commit changes this to new.
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
PRIVATE_PATTERNS = (
    ".env", ".env.test-bots", ".env.dev-app", "*.local.xcconfig", "*.p8",
    "*.p12", "*.mobileprovision", "*.keystore", "*.db", "*.db-wal", "*.db-shm",
    "*service-account*.json", "*firebase-adminsdk*.json", "local.properties",
)
PRIVATE_DIRS = {".venv", "dossiers", "photos", "logs", "backups", ".trash-backup",
                "app-uploads", "app-locks", ".ssh", "xcuserdata", "DerivedData"}


def repository_files(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, stdout=subprocess.PIPE,
    )
    return sorted({root / p.decode("utf-8") for p in result.stdout.split(b"\0") if p})


def document_errors(path: Path, root: Path = ROOT) -> list[str]:
    rel = path.relative_to(root).as_posix()
    body = path.read_text(encoding="utf-8")
    errors = []
    if not all(field in body[:600] for field in ("状态：", "适用：", "核验：", "依据：")):
        errors.append(f"{rel}: missing document status/scope/verification/evidence")
    # Code examples may contain Markdown syntax; they are not navigation links.
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    for match in LINK.finditer(body):
        target = match.group(1).strip()
        target = target[1:target.index(">") ] if target.startswith("<") and ">" in target else target.split()[0]
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        if parsed.path.startswith("/"):
            errors.append(f"{rel}: machine-specific absolute link: {target}")
            continue
        resolved = (path.parent / unquote(parsed.path)).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
            errors.append(f"{rel}: broken or out-of-repository link: {target}")
    return errors


def main() -> int:
    errors = []
    new = (ROOT / "server/pyproject.toml").is_file()
    layout = "new" if new else "legacy"
    if EXPECTED_LAYOUT != "either" and layout != EXPECTED_LAYOUT:
        errors.append(f"expected {EXPECTED_LAYOUT} layout, found {layout}")
    required = (
        ["server/murmur", "server/tests", "apps/ios/MurmurApp.xcodeproj",
         "apps/android/gradlew", "infra/deploy/murmur-update", "scripts/check/run_tests.py"]
        if new else ["pyproject.toml", "murmur", "tests", "MurmurApp.xcodeproj",
                     "android/gradlew", "deploy/murmur-update", "scripts/run_tests.py"]
    )
    for name in required:
        if not (ROOT / name).exists():
            errors.append(f"missing {layout} entry: {name}")
    if new:
        for name in ("murmur", "tests", "MurmurApp", "MurmurApp.xcodeproj", "android", "deploy", "brand", "pyproject.toml", "run.sh"):
            if (ROOT / name).exists():
                errors.append(f"legacy source entry remains: {name}")
    files = repository_files()
    documents = 0
    for path in files:
        if not path.is_file():
            continue  # A working-tree move can leave the old path in the index.
        rel = path.relative_to(ROOT)
        if any(part in PRIVATE_DIRS for part in rel.parts) or any(
            fnmatch.fnmatch(path.name, pattern) for pattern in PRIVATE_PATTERNS
        ):
            errors.append(f"private/runtime path is not ignored: {rel}")
        archive = rel.as_posix().startswith("docs/archive/") and path.name != "README.md"
        if path.suffix == ".md" and not archive:
            documents += 1
            errors.extend(document_errors(path))
    baseline = ROOT / "docs/development/test-baseline.txt"
    if baseline.exists():
        expected = set(baseline.read_text().splitlines())
        tests_root = ROOT / ("server/tests" if new else "tests")
        actual = {p.name for p in tests_root.glob("test_*.py")}
        for missing in sorted(expected - actual):
            errors.append(f"original test disappeared: {missing}")
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Repository checks: layout={layout}, active_documents={documents}, errors={len(errors)}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
