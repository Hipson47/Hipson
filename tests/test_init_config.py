"""Config snapshot regressions, including Windows stat semantics on every host."""

import hashlib
import os
import sys
from pathlib import Path
from unittest import SkipTest, TestCase
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


def test_atomic_write_retains_edit_after_final_check(tmp_path):
    for edit in ("in-place", "replacement", "creation"):
        root = tmp_path / edit
        root.mkdir()
        _retains_edit_after_final_check(root, edit)


def test_atomic_write_without_hard_links_replaces_in_place_and_never_strands_the_config(tmp_path):
    if sys.platform == "win32":
        raise SkipTest("POSIX no-clobber placement only; Windows rename refuses an existing destination")
    path = tmp_path / "config.toml"
    path.write_bytes(b'model = "before"\n')
    expected = init_config.snapshot(path)
    replace = os.replace

    def replace_while_present(source, destination):
        assert Path(destination) != path or path.exists()  # The config is never moved away without links.
        replace(source, destination)

    with (patch.object(init_config.os, "link", side_effect=PermissionError("links unsupported")),
          patch.object(init_config.os, "replace", side_effect=replace_while_present)):
        init_config.atomic_write(path, b'model = "after"\n', expected)
    assert path.read_bytes() == b'model = "after"\n'
    assert list(tmp_path.iterdir()) == [path]
    # A stale read is still refused before writing.
    with TestCase().assertRaisesRegex(ValueError, "changed since planning"):
        init_config.atomic_write(path, b"# planned\n", expected)


def test_atomic_write_keeps_a_late_in_place_edit_to_the_displaced_file(tmp_path):
    if sys.platform == "win32":
        raise SkipTest("POSIX no-clobber placement only; Windows rename refuses an existing destination")
    path = tmp_path / "config.toml"
    path.write_bytes(b'model = "before"\n')
    expected = init_config.snapshot(path)
    place = init_config._place_if_absent

    def editor_writes_old_inode_then_place(source, destination):
        aside = next(tmp_path.glob(".config.toml.*.aside"))
        aside.write_bytes(b"# editor wrote through its open descriptor\n")
        place(source, destination)

    with (patch.object(init_config, "_place_if_absent", side_effect=editor_writes_old_inode_then_place),
          TestCase().assertRaisesRegex(ValueError, "concurrent edit is kept in")):
        init_config.atomic_write(path, b"# planned\n", expected)
    kept = list(tmp_path.glob(".config.toml.*.aside"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"# editor wrote through its open descriptor\n"


def test_snapshot_reports_an_interrupted_write_instead_of_a_missing_config(tmp_path):
    path = tmp_path / "config.toml"
    (tmp_path / ".config.toml.abc123.aside").write_bytes(b'model = "original"\n')
    with TestCase().assertRaisesRegex(ValueError, "interrupted Hipson write left .*abc123.aside"):
        init_config.snapshot(path)
    (tmp_path / ".config.toml.abc123.aside").unlink()
    assert init_config.snapshot(path).content is None


def _retains_edit_after_final_check(tmp_path, edit):
    path = tmp_path / "config.toml"
    before = b'model = "before"\n'
    newer = b'# editor saved after the check\n'
    if edit != "creation":
        path.write_bytes(before)
    expected = init_config.snapshot(path)
    check = init_config.unchanged

    def save_after_check(target, original):
        check(target, original)
        if edit == "replacement":
            replacement = tmp_path / "editor.tmp"
            replacement.write_bytes(newer)
            os.replace(replacement, target)
        else:
            target.write_bytes(newer)

    with (patch.object(init_config, "unchanged", side_effect=save_after_check),
          TestCase().assertRaisesRegex(ValueError, "changed since planning")):
        init_config.atomic_write(path, b'# planned bytes\n', expected)
    assert path.read_bytes() == newer
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_restores_displaced_file_if_install_fails(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'# original\n')
    expected = init_config.snapshot(path)
    place = init_config._place_if_absent
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("injected install failure")
        place(source, destination)

    with (patch.object(init_config, "_place_if_absent", side_effect=fail_once),
          TestCase().assertRaisesRegex(PermissionError, "injected install failure")):
        init_config.atomic_write(path, b'# planned\n', expected)
    assert path.read_bytes() == expected.content
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_preserves_file_recreated_after_displacement(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'# original\n')
    expected = init_config.snapshot(path)
    replace = os.replace

    def recreate(source, destination):
        replace(source, destination)
        if source == path:
            path.write_bytes(b'# recreated by editor\n')

    with (patch.object(init_config.os, "replace", side_effect=recreate),
          TestCase().assertRaisesRegex(ValueError, "changed since planning.*recover displaced file")):
        init_config.atomic_write(path, b'# planned\n', expected)
    assert path.read_bytes() == b'# recreated by editor\n'
    aside, = tmp_path.glob("*.aside")
    assert aside.read_bytes() == expected.content
    assert not list(tmp_path.glob("*.tmp"))
