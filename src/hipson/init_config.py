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


def _displaced_copies(path: Path) -> list[Path]:
    """Files atomic_write moved aside; one beside a missing config means a write was interrupted."""
    try:
        return sorted(item for item in path.parent.iterdir()
                      if item.name.startswith(f".{path.name}.") and item.name.endswith(".aside"))
    except OSError:
        return []


def snapshot(path: Path) -> Snapshot:
    """Bind bytes to the file identity; refuse links and unstable reads."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        leftovers = _displaced_copies(path)
        if leftovers:
            raise ValueError(f"An interrupted Hipson write left {leftovers[0]} while {path} is missing; move it back "
                             f"to {path} (or delete it if it is not needed) and rerun hipson init.") from None
        return Snapshot(None)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Config must be a regular file, not a link: {path}")
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        content = source.read()
        after = os.fstat(source.fileno())
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    # Windows CPython can report creation time in lstat().st_ctime_ns but change
    # time in fstat().st_ctime_ns. Compare full metadata within each API, then
    # bind the path to the descriptor using the fields with shared semantics.
    final = path.lstat()
    if (
        identity(info) != identity(final)
        or identity(before) != identity(after)
        or identity(info)[:-1] != identity(before)[:-1]
    ):
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


def _place_if_absent(source: Path, destination: Path) -> None:
    """Install without overwriting an editor's recreation of the destination."""
    if sys.platform == "win32":
        # Unlike POSIX rename, Windows rename refuses an existing destination.
        os.rename(source, destination)
    else:
        os.link(source, destination, follow_symlinks=False)
        source.unlink()


def _no_clobber_supported(staged: Path) -> bool:
    """POSIX no-clobber placement needs hard links; probe the target filesystem with the staged file."""
    if sys.platform == "win32":
        return True
    try:
        os.link(staged, _link_probe(staged), follow_symlinks=False)
    except OSError:
        return False
    _link_probe(staged).unlink()  # atomic_write also removes it if this is interrupted.
    return True


def _link_probe(staged: Path) -> Path:
    return staged.with_name(staged.name + ".link")


def atomic_write(path: Path, content: bytes, expected: Snapshot, *, mode: int | None = None) -> Snapshot:
    """Compare the atomically displaced source before installing staged bytes.

    The destination is moved aside, compared with the planned snapshot and replaced only if nothing recreated it
    meanwhile, so a concurrent editor save is kept. A crash in between leaves the original beside the missing
    config, which snapshot() reports. Filesystems without hard links fall back to a plain atomic replace after
    the comparison (the destination is never missing, the narrow check-to-replace window remains).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    aside = None
    displaced = False
    placed = False
    retain_aside = False
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        staged.chmod(mode if mode is not None else stat.S_IMODE(expected.identity[2]) if expected.identity else 0o600)
        identity = staged.stat()
        unchanged(path, expected)
        if not _no_clobber_supported(staged):
            os.replace(staged, path)
            placed = True
        elif expected.content is not None:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".aside", dir=path.parent)
            os.close(descriptor)
            aside = Path(temporary)
            try:
                os.replace(path, aside)
            except FileNotFoundError:
                raise ValueError(f"Configuration changed since planning: {path}; rerun hipson init.") from None
            displaced = True
            retain_aside = True  # Until the final comparison proves it holds nothing newer than the plan.
            moved = snapshot(aside)
            # Renaming can change ctime; the remaining identity and bytes must match.
            if moved.content != expected.content or moved.identity[:-1] != expected.identity[:-1]:
                raise ValueError(f"Configuration changed since planning: {path}; rerun hipson init.")
        if not placed:
            try:
                _place_if_absent(staged, path)
            except FileExistsError:
                raise ValueError(f"Configuration changed since planning: {path}; rerun hipson init.") from None
            placed = True
        written = snapshot(path)
        if written.content != content or written.identity[:2] != (identity.st_dev, identity.st_ino):
            raise ValueError(f"Configuration changed during atomic replacement: {path}"
                             + (f"; the previous file is kept in {aside}." if aside is not None else ""))
        if aside is not None:
            # Last step: an editor holding the old file open may have written to it. Any doubt keeps the copy;
            # a write after this comparison goes to a file the editor no longer shares with the config.
            try:
                late = snapshot(aside).content
            except Exception:
                late = None
            if late != expected.content:
                raise ValueError(f"Configuration changed during atomic replacement: {path}; the concurrent edit is "
                                 f"kept in {aside}.")
            retain_aside = False
        return written
    except BaseException:
        if displaced and not placed and aside is not None:
            try:
                _place_if_absent(aside, path)
            except OSError as exc:
                raise ValueError(f"Configuration changed since planning: {path}; recover displaced file from {aside}.") from exc
            retain_aside = False  # Restored: the aside name no longer exists.
        raise
    finally:
        staged.unlink(missing_ok=True)
        _link_probe(staged).unlink(missing_ok=True)
        if aside is not None and not retain_aside:
            aside.unlink(missing_ok=True)


def restore(path: Path, original: Snapshot, backup: Path | None, *, expected: Snapshot) -> None:
    """Restore only the failing action, retaining its backup as recovery evidence."""
    unchanged(path, expected)
    current = expected
    if current == original:
        return
    if original.content is None:
        unchanged(path, current)
        path.unlink(missing_ok=True)
    else:
        if backup is None or backup.read_bytes() != original.content:
            raise ValueError(f"Cannot safely restore {path}; inspect the backup manually.")
        atomic_write(path, original.content, current, mode=stat.S_IMODE(original.identity[2]))
