import hashlib
from pathlib import Path

import pytest
from conftest import ENCRYPTED_MARK, Game

from app.errors import GameRunningError, WorkspaceError
from app.workspace import ROTATING_BACKUPS_KEPT, Workspace


def test_backup_is_made_once_with_hash(workspace: Workspace, dump: Path) -> None:
    original = workspace.backup()
    assert original.read_bytes() == dump.read_bytes()
    expected = hashlib.sha256(dump.read_bytes()).hexdigest()
    assert workspace.original_hash_path.read_text() == expected
    with pytest.raises(WorkspaceError):
        workspace.backup()


def test_backup_from_other_file(workspace: Workspace, dump: Path) -> None:
    older = dump.with_name(dump.name + ".bak")
    older.write_bytes(ENCRYPTED_MARK + b"older")
    workspace.backup(older)
    assert workspace.original_path.read_bytes() == ENCRYPTED_MARK + b"older"


def test_backup_rejects_file_that_does_not_open(
    workspace: Workspace, dump: Path
) -> None:
    plain = dump.with_name("plain")
    plain.write_bytes(b"not encrypted")
    with pytest.raises(WorkspaceError):
        workspace.backup(plain)
    assert not workspace.original_path.exists()


def test_start_does_not_discard_copy_without_force(workspace: Workspace) -> None:
    workspace.start()
    workspace.clear_db.write_bytes(b"edits")
    with pytest.raises(WorkspaceError):
        workspace.start()
    assert workspace.clear_db.read_bytes() == b"edits"
    workspace.start(force=True)
    assert workspace.clear_db.read_bytes() != b"edits"


def test_apply_installs_encrypted_copy(workspace: Workspace, dump: Path) -> None:
    workspace.backup()
    workspace.start()
    workspace.apply()
    assert dump.read_bytes() == ENCRYPTED_MARK + workspace.clear_db.read_bytes()
    assert len(list(workspace.rotating_dir.iterdir())) == 1
    assert not (workspace.work_dir / "encrypted.tmp").exists()


def test_apply_requires_backup(workspace: Workspace) -> None:
    workspace.start()
    with pytest.raises(WorkspaceError):
        workspace.apply()


def test_apply_refused_while_game_runs(
    workspace: Workspace, dump: Path, game: Game
) -> None:
    workspace.backup()
    workspace.start()
    before = dump.read_bytes()
    game.running = True
    with pytest.raises(GameRunningError):
        workspace.apply()
    assert dump.read_bytes() == before


def test_apply_refuses_corrupt_copy(workspace: Workspace, dump: Path) -> None:
    workspace.backup()
    workspace.start()
    before = dump.read_bytes()
    workspace.clear_db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
    with pytest.raises(WorkspaceError):
        workspace.apply()
    assert dump.read_bytes() == before


def test_apply_refuses_pending_journal(workspace: Workspace) -> None:
    workspace.backup()
    workspace.start()
    (workspace.work_dir / "clear.db-journal").write_bytes(b"")
    with pytest.raises(WorkspaceError):
        workspace.apply()


def test_rotating_backups_are_limited(workspace: Workspace) -> None:
    workspace.backup()
    workspace.start()
    for _ in range(ROTATING_BACKUPS_KEPT + 2):
        workspace.apply()
    assert len(list(workspace.rotating_dir.iterdir())) == ROTATING_BACKUPS_KEPT


def test_restore_installs_original(workspace: Workspace, dump: Path) -> None:
    original = dump.read_bytes()
    workspace.backup()
    dump.write_bytes(ENCRYPTED_MARK + b"changed")
    workspace.restore()
    assert dump.read_bytes() == original
    assert len(list(workspace.rotating_dir.iterdir())) == 1


def test_restore_refuses_tampered_original(workspace: Workspace, dump: Path) -> None:
    workspace.backup()
    workspace.original_path.write_bytes(ENCRYPTED_MARK + b"tampered")
    dump.write_bytes(ENCRYPTED_MARK + b"current")
    with pytest.raises(WorkspaceError):
        workspace.restore()
    assert dump.read_bytes() == ENCRYPTED_MARK + b"current"


def test_restore_refused_while_game_runs(workspace: Workspace, game: Game) -> None:
    workspace.backup()
    game.running = True
    with pytest.raises(GameRunningError):
        workspace.restore()


def test_restore_without_backup(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError):
        workspace.restore()


def test_dump_is_left_untouched_by_start(workspace: Workspace, dump: Path) -> None:
    before = dump.read_bytes()
    workspace.start()
    assert dump.read_bytes() == before


def test_original_clear_db_is_decrypted_once(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.backup()
    calls = []
    decrypt = workspace._crypto.decrypt
    monkeypatch.setattr(
        workspace._crypto,
        "decrypt",
        lambda source, target: calls.append(source) or decrypt(source, target),
    )
    first = workspace.original_clear_db()
    assert first.read_bytes().startswith(b"SQLite format 3")
    assert workspace.original_clear_db() == first
    assert calls == [workspace.original_path]


def test_original_clear_db_refuses_tampered_original(workspace: Workspace) -> None:
    workspace.backup()
    workspace.original_path.write_bytes(ENCRYPTED_MARK + b"tampered")
    with pytest.raises(WorkspaceError):
        workspace.original_clear_db()


def test_snapshot_installed(workspace: Workspace, dump: Path) -> None:
    workspace.backup()
    installed, original = workspace.snapshot_installed()
    assert installed.read_bytes() == dump.read_bytes().removeprefix(ENCRYPTED_MARK)
    assert original == workspace.original_clear_path
    assert not workspace.clear_db.exists()
