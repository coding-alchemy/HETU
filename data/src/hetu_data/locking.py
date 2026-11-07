"""单根写入锁：文件排他锁及显式同线程重入。"""

import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_owners = {}


@contextmanager
def worker_lock(root: Path, *, reentrant: bool = False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "worker.lock"
    owner = (os.getpid(), threading.get_ident())
    with _guard:
        state = _owners.get(root)
        if state is not None:
            if not reentrant or state[0] != owner:
                raise RuntimeError(f"another worker holds the lock at {path}")
            state[1] += 1
        else:
            handle = open(path, "a+b")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise RuntimeError(f"another worker holds the lock at {path}") from None
            state = [owner, 1, handle]
            _owners[root] = state
    try:
        yield path
    finally:
        with _guard:
            state[1] -= 1
            if state[1] == 0:
                del _owners[root]
                fcntl.flock(state[2].fileno(), fcntl.LOCK_UN)
                state[2].close()
