"""Command line: python -m app <command>."""

import argparse
import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from app import database, league_package
from app.appearance import AppearanceRepository, load_options
from app.config import Config, load_config
from app.errors import AppError, WorkspaceError
from app.gamecrypto import GameCrypto
from app.leagues import LEAGUE_KEYS, LeagueRepository
from app.logs import setup_logging
from app.portrait_image import prepare_logo, prepare_portrait
from app.portraits import PortraitStore
from app.repository import PlayerRepository, name_warnings
from app.saves import SaveGame, describe, saves_using
from app.teams import TeamRepository
from app.ui.main_window import run_gui
from app.workspace import Workspace

# Frozen builds keep config.local.json, work/ and backups/ next to the .exe.
ROOT = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
logger = logging.getLogger("app")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app", description="Without a command, opens the editor window."
    )
    commands = parser.add_subparsers(dest="command")
    backup = commands.add_parser("backup", help="save the originals once")
    backup.add_argument("--from", dest="source", type=Path, help="data file to save")
    backup.add_argument("--portraits-from", type=Path, help="portrait archive to save")
    start = commands.add_parser("start", help="decrypt the game data to edit it")
    start.add_argument("--force", action="store_true", help="discard the copy")
    find = commands.add_parser("find", help="search players by name")
    find.add_argument("text")
    show = commands.add_parser("show", help="show a player's data")
    show.add_argument("player_id", type=int)
    set_value = commands.add_parser("set", help="change one field of a player")
    set_value.add_argument("player_id", type=int)
    set_value.add_argument("key")
    set_value.add_argument("value")
    team = commands.add_parser("team", help="show a team's data")
    team.add_argument("team_id", type=int)
    team_set = commands.add_parser("team-set", help="change one field of a team")
    team_set.add_argument("team_id", type=int)
    team_set.add_argument("key")
    team_set.add_argument("value")
    team_league = commands.add_parser(
        "team-league", help="move a team made with the editor to an editor league"
    )
    team_league.add_argument("team_id", type=int)
    team_league.add_argument("league_id", type=int)
    new_team = commands.add_parser("new-team", help="create a copy of a club")
    new_team.add_argument("template_id", type=int)
    new_team.add_argument(
        "--league", type=int, required=True, help="league made with the editor"
    )
    new_team.add_argument("--name", help="name of the new team")
    new_team.add_argument(
        "--abbrev", help="scoreboard abbreviation (default: from the name)"
    )
    new_team.add_argument(
        "--with-players",
        action="store_true",
        help="also copy the club's players (same names, new ids)",
    )
    commands.add_parser("leagues", help="list the leagues made with the editor")
    new_league = commands.add_parser("new-league", help="create a playable league")
    new_league.add_argument("name")
    new_league.add_argument("--country", type=int, required=True, help="country id")
    league_set = commands.add_parser("league-set", help="change an editor league")
    league_set.add_argument("league_id", type=int)
    league_set.add_argument("key", choices=LEAGUE_KEYS)
    league_set.add_argument("value")
    export_league = commands.add_parser(
        "export-league", help="save an editor league, its teams and players to a file"
    )
    export_league.add_argument("league_id", type=int)
    export_league.add_argument("file", type=Path)
    import_league = commands.add_parser(
        "import-league", help="add a league from a file as a new editor league"
    )
    import_league.add_argument("file", type=Path)
    delete_league = commands.add_parser(
        "delete-league", help="delete an empty league made with the editor"
    )
    delete_league.add_argument("league_id", type=int)
    delete_league.add_argument(
        "--force", action="store_true", help="even if saved leagues use it"
    )
    delete_team = commands.add_parser(
        "delete-team", help="delete a team made with the editor"
    )
    delete_team.add_argument("team_id", type=int)
    delete_team.add_argument(
        "--force", action="store_true", help="even if saved leagues use it"
    )
    logo = commands.add_parser("logo", help="set or remove a team's custom logo")
    logo.add_argument("team_id", type=int)
    logo_group = logo.add_mutually_exclusive_group(required=True)
    logo_group.add_argument("image", type=Path, nargs="?", help="image to use")
    logo_group.add_argument("--remove", action="store_true", help="original logo")
    new = commands.add_parser("new", help="create a player, or copy one with --from")
    new.add_argument("--from", dest="source", type=int, help="player to copy")
    new.add_argument("--team", type=int, help="team to put him in (default: pools)")
    delete = commands.add_parser("delete", help="delete a player made with the editor")
    delete.add_argument("player_id", type=int)
    look = commands.add_parser("appearance", help="show a player's 3D look")
    look.add_argument("player_id", type=int)
    look.add_argument(
        "--default", action="store_true", help="remove it: the game's default look"
    )
    look_set = commands.add_parser("appearance-set", help="change a player's 3D look")
    look_set.add_argument("player_id", type=int)
    look_set.add_argument("key")
    look_set.add_argument("value")
    portrait = commands.add_parser("portrait", help="set or remove a portrait")
    portrait.add_argument("player_id", type=int)
    group = portrait.add_mutually_exclusive_group(required=True)
    group.add_argument("image", type=Path, nargs="?", help="photo to use")
    group.add_argument("--remove", action="store_true", help="back to the original")
    commands.add_parser("apply", help="encrypt and install the changes")
    restore = commands.add_parser("restore", help="install the original again")
    restore.add_argument("--yes", action="store_true", help="do not ask")
    commands.add_parser(
        "snapshot", help="decrypt the installed data and the original into work/"
    )
    commands.add_parser("verify", help="check the installed data opens")
    return parser


def run(args: argparse.Namespace) -> None:
    config = load_config(ROOT / "config.local.json")
    crypto = GameCrypto(config.dll_path, config.key)
    portraits = PortraitStore(config.portraits_path, ROOT)
    workspace = Workspace(config.dump_path, crypto, ROOT, portraits=portraits)

    match args.command:
        case "backup":
            saved = []
            if not workspace.original_path.exists():
                workspace.backup(args.source)
                saved.append("data")
            if not portraits.has_original():
                portraits.backup(args.portraits_from)
                saved.append("portraits")
            if not saved:
                raise WorkspaceError("The originals are already backed up.")
            print(f"Original {' and '.join(saved)} saved.")
        case "start":
            workspace.start(args.force)
            print("Working copy ready.")
        case (
            "find"
            | "show"
            | "set"
            | "new"
            | "delete"
            | "team"
            | "team-set"
            | "new-team"
            | "delete-team"
            | "team-league"
            | "logo"
            | "leagues"
            | "new-league"
            | "league-set"
            | "delete-league"
            | "export-league"
            | "import-league"
            | "appearance"
            | "appearance-set"
        ):
            if not workspace.clear_db.is_file():
                raise WorkspaceError("There is no working copy. Run 'start' first.")
            with closing(database.connect(workspace.clear_db)) as connection:
                edit(connection, workspace, args, config)
        case "portrait":
            if args.remove:
                removed = portraits.remove(args.player_id)
                print("Custom portrait removed." if removed else "No custom portrait.")
            else:
                portraits.set(args.player_id, prepare_portrait(args.image))
                print("Portrait saved in the working copy. Use 'apply' to install it.")
        case "apply":
            workspace.apply()
            print("Changes installed.")
        case "restore":
            if workspace.clear_db.is_file():
                with closing(database.connect(workspace.clear_db)) as connection:
                    teams, leagues = TeamRepository(connection).editor_made(
                        workspace.original_clear_db()
                    )
                warn_saves(saves_using(workspace.saves_dir, teams, leagues))
            question = "Install the original and lose the changes? [y/N] "
            if not args.yes and input(question) != "y":
                print("Cancelled.")
                return
            workspace.restore()
            print("Original restored.")
        case "snapshot":
            installed, original = workspace.snapshot_installed()
            print(f"Decrypted {installed.name} and {original.name} in work/.")
        case "verify":
            ok = crypto.verify(config.dump_path)
            print("The installed data opens." if ok else "The installed data FAILS.")


def edit(
    connection: sqlite3.Connection,
    workspace: Workspace,
    args: argparse.Namespace,
    config: Config,
) -> None:
    repository = PlayerRepository(connection)
    teams = TeamRepository(connection)
    leagues = LeagueRepository(connection)
    portraits = workspace.portraits
    match args.command:
        case "appearance" | "appearance-set":
            appearance(
                AppearanceRepository(connection, load_options(config.gfx_path)), args
            )
        case "team":
            for key, value in teams.get_team(args.team_id).items():
                print(f"{key}: {value}")
        case "team-set":
            values = teams.set_team_values(args.team_id, {args.key: args.value})
            print(f"{args.key} = {values[args.key]}")
        case "new-team" if args.with_players:
            team_id, copies = teams.create_team_with_players(
                args.template_id,
                args.league,
                workspace.original_clear_db(),
                args.name,
                args.abbrev,
            )
            for source_id, player_id in copies.items():
                portraits.copy(source_id, player_id)
            print(
                f"Created team {team_id} as a copy of {args.template_id}, "
                f"with {len(copies)} copied players."
            )
            print_warnings(leagues.league_warnings(args.league))
        case "new-team":
            team_id = teams.create_team(
                args.template_id,
                args.league,
                workspace.original_clear_db(),
                args.name,
                args.abbrev,
            )
            print(f"Created team {team_id} as a copy of {args.template_id}.")
            print_warnings(leagues.league_warnings(args.league))
        case "team-league":
            teams.move_team(args.team_id, args.league_id, workspace.original_clear_db())
            print(f"Team {args.team_id} is in league {args.league_id}.")
            print_warnings(leagues.league_warnings(args.league_id))
        case "leagues":
            for league in leagues.editor_leagues(workspace.original_clear_db()):
                print(f"{league.id}\t{league.name}\t{league.team_count} teams")
                print_warnings(leagues.league_warnings(league.id))
        case "new-league":
            league_id = leagues.create_league(args.name, args.country)
            print(f"Created league {league_id}.")
        case "league-set":
            values = leagues.set_league_values(
                args.league_id, {args.key: args.value}, workspace.original_clear_db()
            )
            print(f"{args.key} = {values[args.key]}")
        case "export-league":
            summary = league_package.export_league(
                connection,
                args.league_id,
                workspace.original_clear_db(),
                portraits,
                args.file,
            )
            print(
                f"Exported {summary.league} ({summary.teams} teams, "
                f"{summary.players} players) to {args.file}. It includes the "
                "players' photos."
            )
        case "import-league":
            package = league_package.read_package(args.file)
            try:
                options = load_options(config.gfx_path)
            except AppError:
                options = None  # looks are then checked only for their form
            league_id = league_package.import_league(
                connection, package, portraits, options, args.file.name
            )
            name = leagues.get_league(league_id).name
            print(
                f"Imported {name} as league {league_id} ({package.summary.teams} "
                f"teams, {package.summary.players} players). Use 'apply' to "
                "install it."
            )
            print_warnings(leagues.league_warnings(league_id))
        case "delete-league":
            original = workspace.original_clear_db()
            if not leagues.is_editor_league(args.league_id, original):
                raise WorkspaceError(
                    "Only leagues made with the editor can be deleted."
                )
            refuse_if_saved(
                saves_using(workspace.saves_dir, league_ids={args.league_id}), args
            )
            leagues.delete_league(args.league_id, original)
            print(f"Deleted league {args.league_id}.")
        case "delete-team":
            original = workspace.original_clear_db()
            if teams.is_original(args.team_id, original):
                raise WorkspaceError(
                    "Only teams created with the editor can be deleted."
                )
            refuse_if_saved(
                saves_using(workspace.saves_dir, team_ids={args.team_id}), args
            )
            teams.delete_team(args.team_id, original)
            if portraits:
                portraits.remove_logo(args.team_id)
            print(f"Deleted team {args.team_id}.")
        case "logo" if args.remove:
            if portraits and portraits.remove_logo(args.team_id):
                teams.restore_logo_file(args.team_id, workspace.original_clear_db())
                print("Custom logo removed.")
            else:
                print("No custom logo.")
        case "logo":
            teams.get_team(args.team_id)  # refuse unknown teams first
            portraits.set_logo(args.team_id, *prepare_logo(args.image))
            teams.use_custom_logo(args.team_id)
            print("Logo saved in the working copy. Use 'apply' to install it.")
        case "new" if args.source is None:
            player_id = repository.create_player(args.team)
            print(f"Created player {player_id}.")
        case "new":
            player_id = repository.duplicate_player(args.source, args.team)
            if portraits:
                portraits.copy(args.source, player_id)
            print(f"Created player {player_id} as a copy of {args.source}.")
        case "delete":
            repository.delete_player(args.player_id, workspace.original_clear_db())
            if portraits:
                portraits.remove(args.player_id)
            print(f"Deleted player {args.player_id}.")
        case "find":
            for player in repository.find_players(args.text):
                print(f"{player.id}\t{player.first_name} {player.name}")
        case "show":
            for key, value in repository.get_player(args.player_id).items():
                print(f"{key}: {value}")
        case "set":
            value = repository.set_value(args.player_id, args.key, args.value)
            print(f"{args.key} = {value}")
            print_warnings(name_warnings({args.key: value}))


def appearance(looks: AppearanceRepository, args: argparse.Namespace) -> None:
    if args.command == "appearance-set":
        values = looks.set(args.player_id, {args.key: args.value})
        print(f"{args.key} = {values[args.key]}")
        return
    if args.default:
        removed = looks.remove(args.player_id)
        print("Custom look removed." if removed else "No custom look.")
        return
    look = looks.get(args.player_id)
    if look is None:
        print("No custom look: the game uses its default.")
        return
    for key, value in look.items():
        print(f"{key}: {value}")


def warn_saves(saves: list[SaveGame]) -> None:
    if saves:
        print(f"Warning: these saved leagues may no longer load: {describe(saves)}.")


def refuse_if_saved(saves: list[SaveGame], args: argparse.Namespace) -> None:
    """Deleting what a saved league uses needs --force."""
    if saves and not args.force:
        raise WorkspaceError(
            f"Saved leagues use it and may no longer load: {describe(saves)}. "
            "Use --force to delete it anyway."
        )
    warn_saves(saves)


def print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"Warning: {warning}")


def main() -> int:
    args = build_parser().parse_args()
    setup_logging(ROOT / "work")
    if args.command is None:
        return run_gui(ROOT)
    # Player names hold characters a non-UTF-8 console cannot encode.
    if sys.stdout:
        sys.stdout.reconfigure(errors="replace")
    try:
        run(args)
    except AppError as error:
        logger.warning("%s failed: %s", args.command, error)
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception:
        logger.exception("%s failed unexpectedly", args.command)
        print("Error: something went wrong. See work/app.log.", file=sys.stderr)
        return 1
    logger.info("%s done", args.command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
