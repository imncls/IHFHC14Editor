from pathlib import Path

from app.__main__ import build_parser


def test_league_package_commands() -> None:
    parser = build_parser()
    args = parser.parse_args(["export-league", "19", "liga.hc14league"])
    assert (args.command, args.league_id, args.file) == (
        "export-league",
        19,
        Path("liga.hc14league"),
    )
    args = parser.parse_args(["import-league", "liga.hc14league"])
    assert (args.command, args.file) == ("import-league", Path("liga.hc14league"))
