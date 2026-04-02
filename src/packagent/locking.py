from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import fcntl
from typing import Iterator

from packagent.paths import PackagentPaths


@contextmanager
def mutation_lock(paths: PackagentPaths) -> Iterator[None]:
    paths.root.mkdir(parents=True, exist_ok=True)
    handle = paths.lock_file.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

