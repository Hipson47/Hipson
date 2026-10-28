"""Installers share init's editor-race protection and per-path locks."""

from contextlib import contextmanager
from itertools import product
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hipson import agent_install, codex_install, init_config


def test_installers_refuse_concurrent_editor_save(tmp_path):
    for surface, boundary in product(("codex", "cursor", "claude", "mcp", "hook"), ("backup", "final-check")):
        root = tmp_path / f"{surface}-{boundary}"
        root.mkdir()
        with patch.object(Path, "home", return_value=root):
            _refuse_concurrent_editor_save(root, surface, boundary)


def _refuse_concurrent_editor_save(tmp_path, surface, boundary):
    path = tmp_path / "target" / ("AGENTS.md" if surface == "codex" else "rules.md")
    path.parent.mkdir()
    before = b'# original custom instructions\r\n'
    edited = b'# newer editor instructions\r\n'
    path.write_bytes(before)
    owner = codex_install if surface == "codex" else agent_install
    backup = owner.backup_file
    unchanged = init_config.unchanged

    def edit_during_backup(target):
        result = backup(target)
        target.write_bytes(edited)
        return result

    def edit_after_check(target, expected):
        unchanged(target, expected)
        target.write_bytes(edited)

    if boundary == "backup":
        context = patch.object(owner, "backup_file", side_effect=edit_during_backup)
    else:
        context = patch.object(init_config, "unchanged", side_effect=edit_after_check)
    with context, TestCase().assertRaisesRegex(ValueError, "changed since planning"):
        if surface == "codex":
            codex_install.install_codex(dry_run=False, codex_home=path.parent)
        elif surface == "hook":
            agent_install._write_preserved_file(path, "# hook template\n")
        else:
            agent_install._install_text_surface(target=surface, home=path.parent, relative_path=Path(path.name),
                                                body="# managed instructions", dry_run=False)
    assert path.read_bytes() == edited
    saved, = path.parent.glob(f"{path.name}.backup-*")
    assert saved.read_bytes() == before
    assert not list(path.parent.glob(".*.tmp"))
    assert not list(path.parent.glob(".*.aside"))


def test_installers_hold_config_lock_from_snapshot_through_write(tmp_path):
    for surface in ("codex", "claude", "hook"):
        root = tmp_path / surface
        root.mkdir()
        _hold_config_lock_from_snapshot_through_write(root, surface)


def _hold_config_lock_from_snapshot_through_write(tmp_path, surface):
    owner = codex_install if surface == "codex" else agent_install
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b'# custom\n')
    held = []
    observed = []
    snapshot = owner.snapshot
    write = owner.atomic_write

    @contextmanager
    def lock(paths):
        held.extend(paths)
        try:
            yield
        finally:
            held.clear()

    def locked_snapshot(target):
        assert target in held
        observed.append("snapshot")
        return snapshot(target)

    def locked_write(target, *args, **kwargs):
        assert target in held
        observed.append("write")
        return write(target, *args, **kwargs)

    with (patch.object(owner, "config_locks", lock), patch.object(owner, "snapshot", side_effect=locked_snapshot),
          patch.object(owner, "atomic_write", side_effect=locked_write)):
        if surface == "codex":
            codex_install.install_codex(dry_run=False, codex_home=tmp_path)
        elif surface == "hook":
            agent_install._write_preserved_file(path, "# template\n")
        else:
            agent_install._install_text_surface(target=surface, home=tmp_path, relative_path=Path(path.name),
                                                body="# managed", dry_run=False)
    assert observed == ["snapshot", "write"]
