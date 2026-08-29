"""Open a private service log after systemd has applied the service user."""

from __future__ import annotations

import os
import stat
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_logged_service.py LOG_PATH COMMAND [ARG ...]", file=sys.stderr)
        return 2

    log_path = sys.argv[1]
    command = sys.argv[2:]
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_APPEND
        | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(log_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("service log is not a regular file")
        os.fchmod(descriptor, 0o600)
    except OSError as error:
        print(
            f"cannot open service log {log_path}: {type(error).__name__}",
            file=sys.stderr,
        )
        return 1

    os.dup2(descriptor, sys.stdout.fileno())
    os.dup2(descriptor, sys.stderr.fileno())
    if descriptor > sys.stderr.fileno():
        os.close(descriptor)
    os.execv(command[0], command)
    return 1  # pragma: no cover - os.execv only returns by raising


if __name__ == "__main__":
    raise SystemExit(main())
