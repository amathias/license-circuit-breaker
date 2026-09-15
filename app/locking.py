"""Advisory locks shared by cooperating threads and processes on one host."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()
_held = threading.local()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    key = os.path.normcase(str(path.resolve()))
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
