#!/usr/bin/env python3
"""Package clean, committed server source, never the user's working directory.

Only Git tree metadata is inspected before private paths are rejected. This
stdlib helper also works from a sparse checkout without mobile/docs files.
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ALLOWLIST = (
    "server", "infra", "scripts", ".env.example", ".gitignore", "README.md",
    "AGENTS.md", "CONTEXT.md", "CREDITS.md",
)
PRIVATE_DIRS = {
    ".git", ".venv", ".ssh", ".trash-backup", "secrets", "runtime", "logs",
    "backups", "dossiers", "photos", "app-uploads", "app-locks", "openclaw",
    "wechat", "xcuserdata", "DerivedData", "__pycache__", ".cache", ".gradle",
    "node_modules", "build", "dist",
}
PRIVATE_PATTERNS = (
    "*.local.xcconfig", "*.p8", "*.p12", "*.pfx", "*.pem", "*.key", "*.jks",
    "*.mobileprovision", "*.keystore", "*.db", "*.db-*", "*.sqlite", "*.sqlite-*",
    "*.sqlite3", "*.sqlite3-*", "*.log", "*.log.*", "*.pyc", "*.env",
    "*service-account*.json", "*firebase-adminsdk*.json", "local.properties",
    "id_rsa", "id_ed25519", "id_ecdsa", "places.json",
)


class PackagingError(RuntimeError):
    """A rejected package does not create or replace the requested output."""


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=False, capture_output=True,
    )
    if result.returncode:
        # Do not echo git's stdout/stderr: an operator path or custom driver may
        # include private material. These operations only need a concise reason.
        raise PackagingError(f"Git operation failed: {args[0]}")
    return result.stdout


def require_clean(root: Path, revision: str) -> None:
    if git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip() != revision:
        raise PackagingError("HEAD changed while packaging; retry from a stable checkout")
    for entry in git(root, "ls-files", "-v", "-z").split(b"\0"):
        if not entry:
            continue
        flag, path = entry[:1], root / os.fsdecode(entry[2:])
        if flag.islower() or (flag == b"S" and (path.exists() or path.is_symlink())):
            raise PackagingError("Tracked file has hidden worktree changes; clear index flags first")
    for args in (
        ("diff", "--quiet", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", "--"),
        ("diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv",
         "--ignore-submodules=none", revision, "--"),
    ):
        try:
            git(root, *args)
        except PackagingError as error:
            raise PackagingError("Tracked working tree and index must both be clean") from error


def selected(path: str) -> bool:
    return any(path == entry or path.startswith(entry + "/") for entry in ALLOWLIST)


def private_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (
        any(part in PRIVATE_DIRS for part in parts)
        or path == "test" or path.startswith("test/")
        or (name.startswith(".env") and not name.endswith(".example"))
        or any(fnmatch.fnmatchcase(name.lower(), pattern.lower()) for pattern in PRIVATE_PATTERNS)
    )


def source_files(root: Path, revision: str) -> set[str]:
    files = set()
    for record in git(root, "ls-tree", "--full-tree", "-rz", revision).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, _object_id = metadata.split(b" ", 2)
        path = os.fsdecode(raw_path)
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise PackagingError(f"Refusing tracked symlink/submodule/special entry: {path!r}")
        if path.startswith("/") or ".." in PurePosixPath(path).parts or "\\" in path:
            raise PackagingError(f"Unsafe tracked path: {path!r}")
        if private_path(path):
            raise PackagingError(f"Refusing tracked private/runtime path: {path!r}")
        if selected(path):
            files.add(path)
    for entry in ALLOWLIST:
        if not any(path == entry or path.startswith(entry + "/") for path in files):
            raise PackagingError(f"Missing committed source allowlist entry: {entry}")
    return files


def verify_archive(path: Path, expected_files: set[str]) -> None:
    actual = set()
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != "Murmur" or ".." in parts:
                raise PackagingError("Git archive contains an unsafe path")
            relative = "/".join(parts[1:])
            if member.isdir():
                if relative and not selected(relative):
                    raise PackagingError("Git archive contains an unrequested directory")
            elif member.isfile() and relative in expected_files:
                if relative in actual:
                    raise PackagingError("Git archive contains duplicate source entries")
                actual.add(relative)
            else:
                raise PackagingError("Git archive contains an unexpected or non-regular file")
    if actual != expected_files:
        raise PackagingError("Git archive omitted committed source (check export-ignore attributes)")


def package_source(root: Path, output: Path) -> str:
    root = root.resolve()
    if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve() != root:
        raise PackagingError("--root must be the Git repository root")
    revision = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    files = source_files(root, revision)
    require_clean(root, revision)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise PackagingError("Output already exists; refusing to overwrite it")
    if not output.parent.is_dir():
        raise PackagingError("Output directory does not exist")
    with tempfile.TemporaryDirectory(prefix=".murmur-source-", dir=output.parent) as temporary:
        tar_path = Path(temporary) / "source.tar"
        packed = Path(temporary) / "source.tar.gz"
        git(root, "-c", "tar.umask=0022", "archive", "--format=tar", "--prefix=Murmur/", f"--output={tar_path}",
            revision, "--", *ALLOWLIST)
        verify_archive(tar_path, files)
        with tar_path.open("rb") as source, packed.open("xb") as destination:
            os.chmod(packed, 0o600)
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        require_clean(root, revision)
        # Same-filesystem link is atomic and refuses an output created by another
        # process since the initial check. Temporary source is then unlinked.
        os.link(packed, output)
    return revision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        revision = package_source(args.root, args.output)
    except (PackagingError, OSError, tarfile.TarError) as error:
        print(f"Source package rejected: {error}", file=sys.stderr)
        return 1
    print(f"Packaged committed source {revision} as {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
