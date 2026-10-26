"""Config snapshot regressions, including Windows stat semantics on every host."""

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

from hipson import init_config


def _with_ctime(info: os.stat_result, value: int) -> os.stat_result:
    return os.stat_result(info, {
        "st_atime_ns": info.st_atime_ns,
        "st_mtime_ns": info.st_mtime_ns,
        "st_ctime_ns": value,
    })


def test_snapshot_windows_path_and_descriptor_ctimes_can_differ(tmp_path):
    path = tmp_path / "config.toml"
    content = b'model = "existing"\r\n# preserve CRLF\r\n'
    path.write_bytes(content)
    change_time = path.lstat().st_ctime_ns + 1_000_000_000
    original_fstat = os.fstat

    def windows_fstat(fd):
        # CPython 3.13 Windows lstat uses creation time; fstat uses change time.
        return _with_ctime(original_fstat(fd), change_time)

    with patch.object(init_config.os, "fstat", side_effect=windows_fstat):
        state = init_config.snapshot(path)
        init_config.unchanged(path, state)
    assert state.content == content
    assert state.digest == hashlib.sha256(content).digest()


def test_snapshot_still_rejects_ctime_changes_within_each_stat_api(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'model = "existing"\r\n')
    info = path.lstat()
    descriptor = _with_ctime(info, info.st_ctime_ns + 1_000_000_000)
    for changed_api in ("path", "descriptor"):
        final_path = _with_ctime(info, info.st_ctime_ns + 100) if changed_api == "path" else info
        final_descriptor = (_with_ctime(descriptor, descriptor.st_ctime_ns + 100)
                            if changed_api == "descriptor" else descriptor)
        with (patch.object(Path, "lstat", side_effect=[info, final_path]),
              patch.object(init_config.os, "fstat", side_effect=[descriptor, final_descriptor])):
            try:
                init_config.snapshot(path)
            except ValueError as exc:
                assert "changed while reading" in str(exc)
            else:
                raise AssertionError(f"{changed_api} ctime change went undetected")


def test_snapshot_windows_same_size_edit_with_restored_mtime_is_detected(tmp_path):
    # Real file APIs also run on native Windows CI; no platform simulation or skip.
    path = tmp_path / "config.toml"
    path.write_bytes(b'model = "before"\r\n')
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    original = init_config.snapshot(path)
    init_config.unchanged(path, original)
    info = path.stat()
    path.write_bytes(b'model = "edited"\r\n')
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert path.stat().st_size == info.st_size
    assert path.stat().st_mtime_ns == info.st_mtime_ns
    try:
        init_config.unchanged(path, original)
    except ValueError as exc:
        assert "changed since planning" in str(exc)
    else:
        raise AssertionError("same-size edit with restored mtime went undetected")


def test_snapshot_windows_same_bytes_replacement_with_restored_mtime_is_detected(tmp_path):
    path = tmp_path / "config.toml"
    content = b'model = "existing"\r\n'
    path.write_bytes(content)
    original = init_config.snapshot(path)
    info = path.stat()
    staged = tmp_path / "replacement.toml"
    staged.write_bytes(content)
    os.utime(staged, ns=(info.st_atime_ns, info.st_mtime_ns))
    staged.replace(path)
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == info.st_mtime_ns
    try:
        init_config.unchanged(path, original)
    except ValueError as exc:
        assert "changed since planning" in str(exc)
    else:
        raise AssertionError("same-bytes replacement with restored mtime went undetected")
