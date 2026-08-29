#!/usr/bin/env python3
"""Select CI checks from tracked changes and validate the one required gate.

Paths are data, never shell input. Unknown paths conservatively run every build;
unknown/missing job states fail the gate instead of silently waiving a check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

CHECKS = ("server", "ios", "android")
SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def all_checks() -> dict[str, bool]:
    return dict.fromkeys(CHECKS, True)


def layout_paths(root: Path) -> dict[str, str]:
    """Return only fixed paths; deploy the same gate before and after migration."""
    if (root / "server/pyproject.toml").is_file():
        return {
            "project_install": "./server[dev]",
            "python_config": "server/pyproject.toml",
            "package_path": "server/murmur",
            "tests_path": "server/tests",
            "test_runner": "scripts/check/run_tests.py",
            "ios_project": "apps/ios/MurmurApp.xcodeproj",
            "android_directory": "apps/android",
        }
    return {
        "project_install": ".[dev]",
        "python_config": "pyproject.toml",
        "package_path": "murmur",
        "tests_path": "tests",
        "test_runner": "scripts/run_tests.py",
        "ios_project": "MurmurApp.xcodeproj",
        "android_directory": "android",
    }


def classify_paths(paths: Iterable[str]) -> dict[str, bool]:
    """Return checks needed in addition to unconditional repository validation.

The broad app_* boundary deliberately covers new API/auth/storage/push modules
without requiring authors to remember a second explicit contract allowlist.
"""
    selected = dict.fromkeys(CHECKS, False)
    for path in paths:
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or ".." in parts or "\\" in path:
            return all_checks()
        if path.startswith((".github/", "scripts/check/")) or path == "AGENTS.md":
            return all_checks()
        if path.startswith("docs/") or path in {"README.md", "LICENSE", "LICENSE.md"}:
            continue
        if path.startswith(("apps/ios/", "MurmurApp/", "MurmurApp.xcodeproj/")):
            selected["ios"] = True
        elif path.startswith(("apps/android/", "android/")):
            selected["android"] = True
        elif path.startswith(("assets/brand/", "brand/")):
            selected["ios"] = selected["android"] = True
        elif path.startswith(("server/", "murmur/", "tests/")) or path == "pyproject.toml":
            selected["server"] = True
            if path.startswith((
                "server/murmur/app_", "server/tests/test_app_", "murmur/app_", "tests/test_app_",
            )) or path in {
                "server/murmur/config.py",
                "server/murmur/cli.py",
                "server/pyproject.toml",
                "murmur/config.py",
                "murmur/cli.py",
                "pyproject.toml",
            }:
                selected["ios"] = selected["android"] = True
        elif path.startswith(("infra/deploy/", "scripts/ops/", "deploy/")) or path in {
            "run.sh",
            "scripts/migrate-to-vps.sh",
            "scripts/link-vps-to-github.sh",
            "scripts/start-vps-panel.bat",
            "scripts/sanitize_production_env.py",
        }:
            selected["server"] = True
        else:
            # Includes development tooling, new root config and unknown layout.
            return all_checks()
    return selected


def changed_paths(base: str, head: str, *, root: Path) -> list[str]:
    """Diff exact commits, including old and new names of renamed files."""
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("base and head must be complete hexadecimal commit SHAs")
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", base, head, "--"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def select_checks(event: str, base: str, head: str, *, root: Path) -> dict[str, bool]:
    if event == "workflow_dispatch":
        return all_checks()
    if event not in {"pull_request", "push"}:
        raise ValueError(f"unsupported CI event: {event}")
    # A branch's first push has no previous commit; a full build is the safe baseline.
    if event == "push" and SHA.fullmatch(base) and set(base) == {"0"}:
        if not SHA.fullmatch(head):
            raise ValueError("head must be a complete hexadecimal commit SHA")
        return all_checks()
    return classify_paths(changed_paths(base, head, root=root))


def gate_errors(needs: Mapping[str, object]) -> list[str]:
    """Accept only completed required checks and explicitly unneeded skips."""
    expected = {"scope", "repository", *CHECKS}
    if set(needs) != expected:
        return ["gate received missing or unexpected jobs"]
    errors = []
    for name in expected:
        if not isinstance(needs[name], dict):
            return [f"invalid job record: {name}"]
    for name in ("scope", "repository"):
        if needs[name].get("result") != "success":
            errors.append(f"{name} must succeed")
    outputs = needs["scope"].get("outputs")
    if not isinstance(outputs, dict):
        return [*errors, "scope outputs are missing or invalid"]
    for name in CHECKS:
        selected = outputs.get(name)
        result = needs[name].get("result")
        if selected not in {"true", "false"}:
            errors.append(f"{name}: missing/invalid scope decision")
        elif selected == "true" and result != "success":
            errors.append(f"{name}: selected check must succeed (got {result!r})")
        elif selected == "false" and result not in {"success", "skipped"}:
            errors.append(f"{name}: unselected check has invalid result {result!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scope = commands.add_parser("scope")
    scope.add_argument("--event", required=True)
    scope.add_argument("--base", required=True)
    scope.add_argument("--head", required=True)
    scope.add_argument("--github-output", type=Path)
    layout = commands.add_parser("layout")
    layout.add_argument("--github-output", type=Path)
    commands.add_parser("gate")
    args = parser.parse_args()
    if args.command == "gate":
        try:
            needs = json.loads(os.environ["NEEDS_JSON"])
            if not isinstance(needs, dict):
                raise ValueError("NEEDS_JSON must contain an object")
            errors = gate_errors(needs)
        except (KeyError, TypeError, ValueError) as error:
            print(f"Repository gate failed: {error}", file=sys.stderr)
            return 1
        for error in errors:
            print(error, file=sys.stderr)
        if not errors:
            print("Repository gate passed: all selected checks succeeded.")
        return int(bool(errors))
    if args.command == "layout":
        outputs = layout_paths(Path.cwd())
    else:
        selected = select_checks(args.event, args.base, args.head, root=Path.cwd())
        outputs = {name: str(enabled).lower() for name, enabled in selected.items()}
    print(json.dumps(outputs, sort_keys=True))
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            for name, value in outputs.items():
                output.write(f"{name}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
