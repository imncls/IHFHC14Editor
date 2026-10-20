# Contributing

How to work on the editor from its source. What the program does, its legal notice and its configuration are in the [README](README.md).

## Documentation
- [AGENTS.md](AGENTS.md): project rules (source of truth). Everything in this repository is written in English.
- [docs/game-data.md](docs/game-data.md): the game's file format and data model.

## Requirements
- Windows.
- **32-bit Python** (3.14) **with Tcl/Tk** (the "tcl/tk and IDLE" installer option) for the editor. The game's `SQLiteEncrypt.dll` is 32-bit and only loads into a 32-bit process.
- An installation of the game.
- Optional, only for the MCP server: a **64-bit Python** 3.13 or newer.

The built program (`IHFHCEditor.exe`) needs no Python at all: all of this is only for working on the source.

## Setup

### Python for the editor
Install a 32-bit Python 3.14 (from python.org, the "Windows installer (32-bit)", with "tcl/tk and IDLE" selected). Then, from the project root, in PowerShell:

```
<path to 32-bit Python>\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -c "import struct; print(struct.calcsize('P') * 8)"
python -m pip install -r requirements-dev.txt
```

The third line must print `32`; with `64`, the virtual environment was made with a 64-bit Python and the game's DLL will not load. Nothing about Python goes in `config.local.json`: running from the source uses the active `.venv`.

### Folders
Like the program, the source finds the game and its save data by itself and asks when it cannot (see [Configuration](README.md#configuration)). `config.local.json` lives in the project root; `config.example.json` shows its keys.

### Python for the MCP server (optional)
The MCP server runs on its own 64-bit Python in `.venv-mcp` (see [MCP server](#mcp-server-development)) and calls the editor's command line on the 32-bit Python. It looks for that Python in `.venv\Scripts\python.exe`; if yours is elsewhere, set it in `config.local.json`:

```json
{
  "python32": "C:/Python314-32/python.exe"
}
```

or with the environment variable `IHFHC_PYTHON32`, which wins over the file. A relative path is relative to the project root. Only the MCP server reads this value; the editor ignores it.

## Running from source
With `.venv` active, open the editor window:

```
python -m app
```

The same operations are available from the command line:

```
python -m app backup [--from FILE]   # save the original once (with its hash)
python -m app start [--force]        # decrypt the installed data into work/clear.db
python -m app find TEXT              # search players by name (case and accents ignored)
python -m app show PLAYER_ID         # show a player's fields
python -m app set PLAYER_ID KEY VALUE  # change one field (validated)
                                     # KEY can also be club or national_team (team id)
python -m app new [--team TEAM_ID]   # create a player (default: in the pools)
python -m app new --from PLAYER_ID [--team TEAM_ID]  # copy a player
python -m app delete PLAYER_ID       # delete a player made with the editor
python -m app team TEAM_ID           # show a team's data
python -m app team-set TEAM_ID KEY VALUE  # change one team field (validated)
python -m app new-team TEMPLATE_ID --league ID [--name NAME] [--abbrev ABC] [--with-players]  # copy of a club, empty or with copies of its players
python -m app team-league TEAM_ID LEAGUE_ID  # move an editor team between editor leagues
python -m app leagues                # list the leagues made with the editor
python -m app new-league NAME --country ID  # create a playable league
python -m app league-set ID name|country_id VALUE  # change an editor league
python -m app delete-league ID [--force]  # delete an empty editor league (--force: even if a saved league uses it)
python -m app export-league ID FILE   # save an editor league to a .hc14league file
python -m app import-league FILE      # add a league from a file as a new editor league
python -m app delete-team TEAM_ID [--force]  # delete a team made with the editor (--force: even if a saved league uses it)
python -m app logo TEAM_ID FILE      # give a team a logo from any image
python -m app logo TEAM_ID --remove  # back to the original logo
python -m app apply                  # encrypt, back up the installed file, install
python -m app restore [--yes]        # install the original again
python -m app appearance ID [--default]  # show a player's 3D look (or remove it)
python -m app appearance-set ID KEY VALUE  # change it (values from the game's list)
python -m app portrait ID FILE      # set a player's portrait from any photo (JPEG, PNG, BMP, GIF)
python -m app portrait ID --remove  # back to the original portrait
python -m app snapshot               # decrypt the installed data and the original into work/
python -m app verify                 # check the installed data opens with the key
```

## Checks
With `.venv` active:

```
python -m ruff format .
python -m ruff check .
python -m pytest
python -m pip_audit -r requirements-dev.txt
```

Tests marked `game_dll` use the real DLL, key and save data, and are skipped when they are not available.

## MCP server (development)
`tools/mcp_server.py` is an MCP server (Model Context Protocol, official Python SDK, `MCPServer`, stdio) that lets an AI coding assistant drive the game while the editor is developed: any MCP client works (Claude Code, Claude Desktop, VS Code agent mode, Cursor...). It runs on **64-bit Python** in its own environment and never loads the game DLL or reads the key: anything that needs them runs through the editor's command line on the 32-bit Python (`python -m app ...`). The editor does not depend on it.

| Tool | What it does |
|---|---|
| `is_running` | Whether `HC14.exe` is running. |
| `launch_game` | Starts the game through Steam (`steam://rungameid/<appid>`; the app id is read from the Steam manifest) and waits until its window is visible. The intro videos (about two minutes) cannot be skipped; on the profile screen `enter` starts and `escape` asks to quit. |
| `close_game(force=false)` | Asks the game to close and confirms the process ended; `force=true` kills it if it does not close. |
| `apply_db` | Encrypts the working copy (`work/clear.db`) and installs it in `save`. Refuses while the game runs; backs up the installed file first (`backups/rotating/`). |
| `restore_backup` | Installs the original player database again (from `backups/original/`). |
| `diff_db` | Decrypts the installed database and lists every change from the original, with field and team names, plus custom portraits and logos. |
| `read_player(player_id or name, source="installed")` | All fields of a player, from the installed data or the working copy (`source="working"`). |
| `set_player_field(player_id, key, value)` | Changes one field in the working copy, with the editor's validation and average recalculation. Install it with `apply_db`. |
| `screenshot(delay=0)` | Captures the game window as a PNG image, optionally after waiting `delay` seconds (up to 30) for a video or loading screen. |
| `press_key(key, times=1, capture_after=0.5)` | Navigates the menus like a gamepad: `up`, `down`, `left`, `right`, `enter` (confirm), `escape` (back), `space`, `backspace`, `tab`. Sends the key `times` times (1 to 20), then returns a screenshot after `capture_after` seconds (`null` for none). |

Setup, from the project root in PowerShell:

```
py -3.13 -m venv .venv-mcp
.\.venv-mcp\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

Any 64-bit Python 3.13 or newer works. The editor's `.venv` (32-bit) and `config.local.json` must be set up as described above. `.mcp.json` registers the server for this project in Claude Code (open the project and approve the `ihfhc14` server when asked, or with `/mcp`); in other clients, add a stdio server with the command `.venv-mcp/Scripts/python.exe` and the argument `tools/mcp_server.py`, run from the project root. The server logs to `work/mcp.log`.

The screenshot copies the window's area of the screen: in exclusive full screen it may come out black; windowed mode always works.

`press_key` injects real key presses (scan codes through `SendInput`, which DirectInput reads). It first brings the game window to the front and sends each key only while the foreground window belongs to `HC14.exe`, so keys never reach another program; only the keys listed above are accepted. While it runs, the game takes the keyboard focus: do not type in other windows at the same time.

## Release build
With `.venv` active:

```
python -m pip_audit -r requirements-dev.txt
cyclonedx-py requirements --pyproject pyproject.toml --output-reproducible requirements.txt -o sbom.cdx.json
python packaging/make_icon.py
python -m PyInstaller --noconfirm --clean --windowed --onedir --name IHFHCEditor --specpath build --paths . --version-file ../packaging/version_info.txt --icon icon.ico --add-data "../icons/icon.png:icons" app/__main__.py
python packaging/make_release.py
```

`make_release.py` writes `dist/IHFHCEditor-<version>-windows.zip`, the file to publish: the program folder without any user data, with this licence and the licences of what the build bundles (Python with its Tcl/Tk, OpenSSL, SQLite..., and Pillow). The version is in `app/__init__.py`, `pyproject.toml` and `packaging/version_info.txt` (the Windows properties of the `.exe`); a test checks they match. `make_icon.py` turns `icons/icon.png` into `build/icon.ico`, the icon of the `.exe`; the PNG also goes inside the program for the window and taskbar icon. The `--version-file`, `--icon` and `--add-data` paths are relative to `build/`, where PyInstaller writes its spec.

The program is the folder `dist/IHFHCEditor/`. It contains no key and no `config.local.json`: on its first run it finds the key in the installed game and saves it in a `config.local.json` next to `IHFHCEditor.exe` (create that file yourself only for non-default game or save folders). A new build replaces the folder, including any `config.local.json`, `work/` and `backups/` inside it, so copy `backups/` out first and back in afterwards to keep the existing original backup. The program keeps `work/` and `backups/` next to `IHFHCEditor.exe`, so put the folder somewhere writable (not under Program Files). The command line is only available from the source with `python -m app`.

## Dependencies
- Runtime (`requirements.txt`): Pillow, used only to turn a photo into a player portrait. Everything else is the standard library.
- Development, with pinned versions (`requirements-dev.txt`): pytest, ruff, pip-audit, cyclonedx-bom and pyinstaller. The SBOM of the runtime dependencies is `sbom.cdx.json`.

## Structure
```
app/
  __main__.py          # entry point: window or command line
  config.py            # folders and the key (found once, then kept in config.local.json)
  steam.py             # finds the game in the Steam libraries
  keyfinder.py         # finds the key in HC14.exe
  saves.py             # the game's saved leagues (SGT_*.sg), to warn before deleting
  league_package.py    # export and import a league (.hc14league)
  errors.py            # errors with messages safe to show
  database.py          # opening the databases; the original attached read-only
  gamecrypto.py        # the only module that touches the DLL
  workspace.py         # backup, start, apply, restore
  repository.py        # read and write players (data, skills, teams)
  teams.py             # read and write team data (playerDBTeam)
  leagues.py           # leagues made with the editor (playerDBLeague)
  validation.py        # validation by field type
  diff.py              # readable differences between two copies of the data
  logs.py              # logging setup
  portraits.py         # portraits and logos in guiportraits.v (original + custom)
  portrait_image.py    # photo to portrait PNG or logo DDS (Pillow)
  appearance.py        # 3D look (Customize_table) with the game's allowed values
  files.py             # hash and safe delete helpers
  averages.py          # player average formulas
  ui/
    main_window.py     # the window: tabs, Game menu, apply counter, status bar
    home_tab.py        # Home tab
    players_tab.py     # Players tab (search, team list, player panel)
    player_panel.py    # one player's editor
    new_player_dialog.py  # "New player" wizard
    player_form.py     # form generated from playerDBData
    teams_tab.py       # Teams tab (tree, team panel, roster)
    team_panel.py      # one team's editor
    leagues_tab.py     # Leagues tab
    new_team_dialog.py    # "New team" wizard
    new_league_dialog.py  # "New league" wizard and the league form
    appearance_dialog.py  # "Appearance..." dialog
    settings_form.py   # the setup window and the Settings tab (folders)
    widgets.py         # shared widget helpers
    user_facing_error.py  # the only way errors are shown
icons/
  icon.png             # the editor's icon (512x512, transparent), by imncls
packaging/
  make_icon.py         # build/icon.ico from icons/icon.png
  version_info.txt     # Windows version properties of IHFHCEditor.exe
  make_release.py      # the release zip
tools/
  mcp_server.py        # development MCP server (64-bit Python)
  game_control.py      # game process, screenshots and editor calls
  game_input.py        # menu navigation keys for the game window
tests/
docs/game-data.md
```
