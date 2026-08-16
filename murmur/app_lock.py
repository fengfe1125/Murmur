"""Cross-process serialization for operations that touch one app user's data."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: msvcrt byte-range locking (dev machines only;
    fcntl = None    # the production targets are POSIX and keep fcntl.flock).

if fcntl is None:
    import msvcrt


class UserOperationLock:
    def __init__(self, root: str | Path, user_id: str):
        directory = Path(root) / "app-locks"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        name = hashlib.sha256(user_id.encode("utf-8")).hexdigest() + ".lock"
        self.path = directory / name
        self.fd: int | None = None

    def __enter__(self) -> UserOperationLock:
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is not None:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        else:
            # msvcrt locks a byte range from the current position.
            os.lseek(self.fd, 0, os.SEEK_SET)
            msvcrt.locking(self.fd, msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *_exc) -> None:
        if self.fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            else:
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self.fd)
            self.fd = None
