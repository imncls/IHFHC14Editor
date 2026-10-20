import zipfile
from pathlib import Path

import pytest
from conftest import (
    ARCHIVE_ENTRIES,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    Game,
    fake_png,
)

from app.errors import GameRunningError, ValidationError, WorkspaceError
from app.portraits import (
    DDS_SIGNATURE,
    PortraitStore,
    changed_portraits,
    logo_file,
    logo_name,
    open_archive,
    portrait_entries,
    portrait_files,
    portrait_name,
    rebuild,
    small_logo_name,
)
from app.workspace import Workspace

NEW_PLAYER = 4518


def entries(encoded: bytes) -> dict[str, bytes]:
    archive = open_archive(encoded)
    return {info.filename: archive.read(info) for info in archive.infolist()}


def test_portrait_entries_ignore_case(portrait_archive: Path) -> None:
    archive = open_archive(portrait_archive.read_bytes())
    assert sorted(portrait_entries(archive)) == [
        PLAYER_WITHOUT_IMAGE,
        PLAYER_WITH_IMAGE,
    ]


def test_rebuild_replaces_and_adds(portrait_archive: Path) -> None:
    original = portrait_archive.read_bytes()
    result = entries(
        rebuild(
            original,
            portrait_files(
                {
                    PLAYER_WITHOUT_IMAGE: fake_png("new 1"),
                    PLAYER_WITH_IMAGE: fake_png("new 2"),
                    NEW_PLAYER: fake_png("new player"),
                }
            ),
        )
    )
    assert result[portrait_name(PLAYER_WITHOUT_IMAGE)] == fake_png("new 1")
    # The odd capitalisation is replaced, not left next to the new entry.
    assert result[portrait_name(PLAYER_WITH_IMAGE)] == fake_png("new 2")
    assert not any(
        name.endswith(f"Portrait_{PLAYER_WITH_IMAGE}.png") for name in result
    )
    assert result[portrait_name(NEW_PLAYER)] == fake_png("new player")
    assert len(result) == len(ARCHIVE_ENTRIES) + 1
    logo = "gui/images_fullprice/logos/teams/team_105.png"
    assert result[logo] == ARCHIVE_ENTRIES[logo]


def test_changed_portraits(portrait_archive: Path) -> None:
    original = portrait_archive.read_bytes()
    rebuilt = rebuild(original, portrait_files({PLAYER_WITH_IMAGE: fake_png("new")}))
    assert changed_portraits(rebuilt, original) == {PLAYER_WITH_IMAGE: fake_png("new")}
    assert changed_portraits(original, original) == {}


def test_open_archive_rejects_other_files() -> None:
    with pytest.raises(WorkspaceError):
        open_archive(b"not a zip")


def test_backup_is_made_once(portraits: PortraitStore) -> None:
    assert portraits.has_original()
    with pytest.raises(WorkspaceError):
        portraits.backup()


def test_backup_rejects_a_file_without_portraits(tmp_path: Path) -> None:
    other = tmp_path / "other.v"
    other.write_bytes(b"plain text")
    store = PortraitStore(other, tmp_path / "project")
    with pytest.raises(WorkspaceError):
        store.backup()


def test_set_get_remove(portraits: PortraitStore) -> None:
    assert portraits.get(PLAYER_WITH_IMAGE) == fake_png("original 2")
    assert portraits.get(NEW_PLAYER) is None
    portraits.set(PLAYER_WITH_IMAGE, fake_png("friend"))
    assert portraits.get(PLAYER_WITH_IMAGE) == fake_png("friend")
    assert portraits.custom_ids() == [PLAYER_WITH_IMAGE]
    assert portraits.remove(PLAYER_WITH_IMAGE)
    assert not portraits.remove(PLAYER_WITH_IMAGE)
    assert portraits.get(PLAYER_WITH_IMAGE) == fake_png("original 2")


@pytest.mark.parametrize("player_id, png", [(0, fake_png("x")), (5, b"GIF89a")])
def test_set_rejects_bad_input(portraits: PortraitStore, player_id: int, png) -> None:
    with pytest.raises(ValidationError):
        portraits.set(player_id, png)
    assert portraits.custom_ids() == []


def test_install_restore_and_sync(
    portraits: PortraitStore, portrait_archive: Path
) -> None:
    original = portrait_archive.read_bytes()
    portraits.set(PLAYER_WITHOUT_IMAGE, fake_png("friend"))
    assert portraits.install(portraits.build())
    assert entries(portrait_archive.read_bytes())[
        portrait_name(PLAYER_WITHOUT_IMAGE)
    ] == fake_png("friend")
    assert not portraits.install(portraits.build())  # already installed

    portraits.remove(PLAYER_WITHOUT_IMAGE)
    portraits.sync_from_installed()
    assert portraits.custom_ids() == [PLAYER_WITHOUT_IMAGE]

    portraits.restore_original()
    assert portrait_archive.read_bytes() == original
    assert portraits.custom_ids() == []


def test_tampered_original_is_refused(portraits: PortraitStore) -> None:
    portraits.original_path.write_bytes(b"tampered")
    with pytest.raises(WorkspaceError):
        portraits.build()


def test_apply_installs_data_and_portraits(
    workspace_with_portraits: Workspace, portrait_archive: Path, dump: Path
) -> None:
    workspace = workspace_with_portraits
    workspace.backup()
    workspace.start()
    workspace.portraits.set(NEW_PLAYER, fake_png("new player"))
    workspace.apply()
    installed = entries(portrait_archive.read_bytes())
    assert installed[portrait_name(NEW_PLAYER)] == fake_png("new player")


def test_apply_refused_while_game_runs_touches_nothing(
    workspace_with_portraits: Workspace, portrait_archive: Path, game: Game
) -> None:
    workspace = workspace_with_portraits
    workspace.backup()
    workspace.start()
    workspace.portraits.set(NEW_PLAYER, fake_png("new player"))
    before = portrait_archive.read_bytes()
    game.running = True
    with pytest.raises(GameRunningError):
        workspace.apply()
    assert portrait_archive.read_bytes() == before


def test_apply_needs_the_original_portraits_for_custom_ones(
    workspace: Workspace, tmp_path: Path, portrait_archive: Path
) -> None:
    workspace.portraits = PortraitStore(portrait_archive, tmp_path / "project")
    workspace.portraits.set(NEW_PLAYER, fake_png("new player"))
    workspace.backup()
    workspace.start()
    with pytest.raises(WorkspaceError):
        workspace.apply()


def test_restore_and_reload_cover_portraits(
    workspace_with_portraits: Workspace, portrait_archive: Path
) -> None:
    workspace = workspace_with_portraits
    original = portrait_archive.read_bytes()
    workspace.backup()
    workspace.start()
    workspace.portraits.set(PLAYER_WITH_IMAGE, fake_png("friend"))
    workspace.apply()
    workspace.portraits.remove(PLAYER_WITH_IMAGE)
    workspace.start(force=True)  # reload: the installed portrait comes back
    assert workspace.portraits.custom_ids() == [PLAYER_WITH_IMAGE]
    workspace.restore()
    assert portrait_archive.read_bytes() == original
    assert workspace.portraits.custom_ids() == []


def test_rebuilt_archive_is_a_valid_zip(portrait_archive: Path) -> None:
    rebuilt = rebuild(
        portrait_archive.read_bytes(), portrait_files({NEW_PLAYER: fake_png("x")})
    )
    archive = open_archive(rebuilt)
    assert isinstance(archive, zipfile.ZipFile)
    assert archive.testzip() is None


def fake_logo(label: str) -> tuple[bytes, bytes]:
    return DDS_SIGNATURE + label.encode(), DDS_SIGNATURE + (label + " small").encode()


def test_custom_logos(portraits: PortraitStore) -> None:
    big, small = fake_logo("friends")
    portraits.set_logo(400, big, small)
    assert portraits.custom_logo_ids() == [400]
    assert portraits.get_logo(400) == (big, small)
    assert portraits.has_custom()
    built = entries(portraits.build())
    assert built[logo_name(400)] == big
    assert built[logo_name(400, small=True)] == small
    assert built[logo_name(400, small=True)] == built[small_logo_name(logo_name(400))]
    assert len(built) == len(ARCHIVE_ENTRIES) + 2
    with pytest.raises(ValidationError):
        portraits.set_logo(400, fake_png("x"), small)
    assert portraits.remove_logo(400)
    assert not portraits.remove_logo(400)
    assert portraits.custom_logo_ids() == []
    assert portraits.get_logo(400) is None


def test_logo_file_points_to_the_entry() -> None:
    assert logo_file(400) == r"gui\images_fullprice\logos\teams\custom_logo_400.dds"


def test_read_original_entry(portraits: PortraitStore) -> None:
    logo = "gui/images_fullprice/logos/teams/team_105.png"
    assert portraits.read_original(logo.upper()) == ARCHIVE_ENTRIES[logo]
    assert portraits.read_original("missing.dds") is None


def test_restore_and_reload_cover_logos(
    workspace_with_portraits: Workspace, portrait_archive: Path
) -> None:
    workspace = workspace_with_portraits
    original = portrait_archive.read_bytes()
    workspace.backup()
    workspace.start()
    workspace.portraits.set_logo(400, *fake_logo("friends"))
    workspace.apply()
    workspace.portraits.remove_logo(400)
    workspace.start(force=True)  # reload: the installed logo comes back
    assert workspace.portraits.get_logo(400) == fake_logo("friends")
    workspace.restore()
    assert portrait_archive.read_bytes() == original
    assert workspace.portraits.custom_logo_ids() == []


def test_failed_portrait_install_leaves_the_data_untouched(
    workspace_with_portraits: Workspace,
    dump: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = workspace_with_portraits
    workspace.backup()
    workspace.start()
    workspace.portraits.set(NEW_PLAYER, fake_png("new player"))
    before = dump.read_bytes()

    def fail(_encoded: bytes) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(workspace.portraits, "install", fail)
    with pytest.raises(OSError):
        workspace.apply()
    assert dump.read_bytes() == before
