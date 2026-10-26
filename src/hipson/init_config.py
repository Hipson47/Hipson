"""File-only discovery and serialized, recoverable init config updates."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Snapshot:
    content: bytes | None
    identity: tuple[int, ...] = ()
    digest: bytes = b""


def snapshot(path: Path) -> Snapshot:
    """Bind bytes to the file identity; refuse links and unstable reads."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return Snapshot(None)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Config must be a regular file, not a link: {path}")
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        content = source.read()
        after = os.fstat(source.fileno())
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if identity(info) != identity(before) or identity(before) != identity(after) or identity(after) != identity(path.lstat()):
        raise ValueError(f"Configuration changed while reading: {path}; rerun hipson init.")
    return Snapshot(content, identity(after), hashlib.sha256(content).digest())


def unchanged(path: Path, original: Snapshot) -> None:
    if snapshot(path) != original:
        raise ValueError(f"Configuration changed since planning: {path}; rerun hipson init.")


@contextmanager
def config_locks(paths: list[Path]) -> Iterator[None]:
    """Lock canonical paths in a stable order, independently of their parent existence.

    Persistent lock files live outside client config directories. Never unlink them:
    waiting processes must keep referring to the same inode across atomic replacements.
    OS locks are released even if init crashes.
    """
    if not paths:
        yield
        return
    root = Path.home() / ".hipson" / "init-locks"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with ExitStack() as stack:
        for name in sorted({os.path.normcase(str(path.resolve())) for path in paths}):
            key = hashlib.sha256(os.fsencode(name)).hexdigest()
            descriptor = os.open(root / key, os.O_RDWR | os.O_CREAT, 0o600)
            handle = stack.enter_context(os.fdopen(descriptor, "r+b"))
            deadline = time.monotonic() + 30
            if sys.platform == "win32":
                import msvcrt

                # locking may cover bytes beyond EOF; the file need not be written.
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Timed out waiting for another hipson init") from None
                        time.sleep(0.05)
                stack.callback(msvcrt.locking, handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Timed out waiting for another hipson init") from None
                        time.sleep(0.05)
                stack.callback(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
        yield


def atomic_write(path: Path, content: bytes, expected: Snapshot, *, mode: int | None = None) -> Snapshot:
    """Stage privately beside the destination, then compare identity/hash and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        staged.chmod(mode if mode is not None else stat.S_IMODE(expected.identity[2]) if expected.identity else 0o600)
        identity = staged.stat()
        unchanged(path, expected)
        os.replace(staged, path)
        written = snapshot(path)
        if written.content != content or written.identity[:2] != (identity.st_dev, identity.st_ino):
            raise ValueError(f"Configuration changed during atomic replacement: {path}")
        return written
    finally:
        staged.unlink(missing_ok=True)


def restore(path: Path, original: Snapshot, backup: Path | None) -> None:
    """Restore only the failing action, retaining its backup as recovery evidence."""
    current = snapshot(path)
    if current == original:
        return
    if original.content is None:
        unchanged(path, current)
        path.unlink(missing_ok=True)
    else:
        if backup is None or backup.read_bytes() != original.content:
            raise ValueError(f"Cannot safely restore {path}; inspect the backup manually.")
        atomic_write(path, original.content, current, mode=stat.S_IMODE(original.identity[2]))
