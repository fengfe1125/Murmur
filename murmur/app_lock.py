"""Cross-process serialization for operations that touch one app user's data."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path


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
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc) -> None:
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None
