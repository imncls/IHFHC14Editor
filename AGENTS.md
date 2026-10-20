# Agent Guidelines

## Project

- Desktop tool for Windows (Python 32-bit, Tkinter) that edits the player data of IHF Handball Challenge 14. Single-player game, personal use, own copies.
- Game format and data model: `docs/game-data.md`.
- The game's `sqliteencrypt.dll` is 32-bit, so the app runs on a 32-bit Python inside `.venv` (created with that interpreter). A 64-bit process cannot load the DLL.
- Only `app/gamecrypto.py` may load the DLL. Everything else works on the decrypted copy with the standard `sqlite3` module.
- Use Tkinter only. Do not add another GUI framework.
- `tools/` holds a development MCP server on 64-bit Python (`.venv-mcp`, `requirements-mcp.txt`). It must never load the DLL or read the key: it calls the editor's command line on the 32-bit Python for that, and reuses the pure modules (`repository`, `diff`, `config.load_paths`). The editor must not depend on `tools/` or on the MCP SDK. Keyboard input (`tools/game_input.py`) is only sent while the foreground window belongs to `HC14.exe`, only from the allowlist of navigation keys, never free text.
- Agents may add new tools to the MCP server whenever they need one to check something in the game, without asking first, as long as they follow the same rules (64-bit Python, no DLL or key, input only to the game window, tests, and the tool table in `CONTRIBUTING.md` updated).
- The key is obtained only through `app.config.get_key`, which finds it in the installed `HC14.exe` the first time and keeps it in `config.local.json`. Shared modules must also run on Python 3.13 (the MCP side).

## Language

- Everything written in this repository is in English: code, identifiers, comments, docstrings, CLI and UI text, error and log messages, Markdown documentation, test names and messages, pytest markers, and commit messages.

## General

- Read this file before making changes.
- Inspect the existing code before implementing new functionality.
- Prefer small, focused changes over large rewrites.
- Do not modify unrelated code.
- Reuse existing abstractions when appropriate.
- Avoid unnecessary dependencies and abstractions.
- If an architectural decision is required, prefer the simplest maintainable solution.
- Do not implement features that have not been requested.

## Code quality

- Follow PEP 8 and standard Python conventions. `ruff` is the formatter and linter.
- Use type hints on public functions and methods. Use dataclasses for domain models.
- Keep business logic outside Tkinter widgets and UI modules.
- Keep UI, domain logic, data access, and the game DLL wrapper separated.
- Prefer small, cohesive classes and functions.
- Avoid duplicated logic.
- Use meaningful names rather than comments to explain code.
- Add comments only when they explain non-obvious decisions.
- Handle errors explicitly. No bare `except:` and no `except Exception: pass`; do not silently swallow exceptions.
- Do not expose raw exceptions directly to users.

## Security

- Security is a first-class requirement and must be considered in every implementation.
- Follow established security best practices and, where applicable, use recognized standards and guidance such as OWASP and NIST.
- Treat all external input as untrusted, including user input, typed or chosen paths, files in the game folders (databases, configuration), imported data, and values returned by the DLL.
- Validate and sanitize input at appropriate trust boundaries.
- Never hardcode secrets, credentials, keys, or private configuration in source code. The game's encryption key is never shipped or typed: the editor finds it in the installed game (`app/keyfinder.py`) and saves it only in `config.local.json` (git-ignored), as a hexadecimal string. Builds of the program (`dist/`) must never contain it. Do not assume every game build uses the same key or key length.
- Never log the key, include it in an exception message, or show it in the UI.
- Do not expose sensitive information through logs, error messages, or debug output.
- Never show a raw caught exception, its message, or a stack trace in the UI (for example `messagebox.showerror("Error", str(error))`). Exception text can leak file paths or other internals. Always translate caught errors to a short, generic, app-authored message before showing them (see `app/ui/user_facing_error.py`). The raw error may still be written to the log through `logging`, but never displayed.
- Do not use `eval`, `exec`, `pickle`, or `shell=True`. Pass subprocess arguments as a list.
- Resolve and validate paths before copying, overwriting, or deleting anything. Never delete files outside the project's work and backup folders.
- Use ctypes safely: declare `argtypes` and `restype` for every DLL function and check every return code.
- Apply the principle of least privilege. The app needs no administrator rights and no network access. Do not add network calls without being asked.
- Keep dependencies up to date and avoid dependencies with known security vulnerabilities.
- Consider common application security risks such as injection, path traversal, data leakage, and unsafe file handling.
- Do not disable security mechanisms merely to work around development problems.
- Avoid introducing security-sensitive functionality without first considering its threat model and failure modes.
- Assume the files read from the game folders may be malformed or unexpected.
- Security-sensitive changes should include appropriate tests where practical.
- When a requested implementation conflicts with a security best practice, prefer the secure implementation and clearly explain the trade-off.

## Input sanitization and database queries

Every field a person can type into gets sanitized or restricted — no exceptions, and no field is "obviously fine" enough to skip this. Prefer an existing library over a hand-rolled check; write custom logic only when nothing suitable exists (documented per case below).

- Databases are opened and the original data attached only through `app/database.py` (`connect`, `open_read_only`, `attached_original`, which attaches it read-only).
- SQL: parameterized queries only (`?` placeholders). Never build SQL with f-strings or concatenation using values. Table and column names come only from fixed constants, or from `playerDBData.key` after checking it against an allowlist.
- Free text (`name`, `first_name`): trim, reject control characters, enforce a maximum length based on the longest value in the database. Unicode letters, spaces, hyphens, apostrophes (straight and curly), periods, and parentheses are legitimate in the game's data.
- `player_img`: a file name only. No path separators, no `..`, and only the extensions already used by the game (`.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`). Empty is allowed.
- Integers: parse with `int` and range-check per field. Ranges come from `docs/game-data.md`. Reject anything else.
- Dates: parse `d.m.yyyy` with `datetime`, require a real calendar date, and write it in the game's format.
- Lookup fields: the value must exist in `playerDBLookupTable` for the field's `lookup_type_id`. The UI offers a dropdown, never free text.
- Country: the id must exist in `Country`.
- Game folder path chosen by the user: normalize it with `pathlib`, require that it exists, is a directory, and contains the expected files. Only the known file names are ever copied.

## State and data

- Keep domain models independent.
- Do not duplicate derived state unless there is a clear reason.
- Preserve data integrity.
- Never write to the game's `save` folder without a fresh backup of the file, and only while `HC14.exe` is not running.
- Work on copies. The first backup of the originals is made once, with its hash, and is never overwritten.
- Run `PRAGMA integrity_check` on the decrypted copy before encrypting, and verify that the encrypted result opens with the key before installing it.
- Avoid destructive changes to existing data without considering what the game expects (keys, references from other tables).
- Keep persistence logic isolated from business logic.

## External resources

- The game's DLL and files are the external boundary. There are no external APIs.
- Never call the DLL or touch game files directly from UI code.
- Route DLL access through one wrapper behind an interface, so tests can replace it with a fake.
- Handle failures explicitly: DLL missing, wrong interpreter bitness, wrong key (`SQLITE_NOTADB`, rc 26), locked or missing files.
- Do not redistribute game resources, extracted assets, databases, or keys.

## Testing

After meaningful changes, run from the project root with `.venv` active:

```
python -m pytest
```

Add or update tests for business-critical behavior.

Prioritize tests for:

- field validation and sanitization
- the repository (read and write of `playerDBPlayerData`)
- date parsing and lookup handling
- backup, install, and restore logic
- edge cases (empty values, 14 versus 15 rows per player, the `player_img` row that may not exist)
- bug fixes

Test rules:

- Use small synthetic SQLite fixtures built inside the tests. Never commit real game databases or the key.
- Tests that need the real DLL and key carry `@pytest.mark.game_dll` and are skipped when they are not available.

## Formatting

Before finishing a task, run:

```
python -m ruff format .
python -m ruff check .
```

Keep formatting consistent with the existing project.

## Git

- Keep changes focused and reviewable.
- Do not modify unrelated files.
- Do not remove existing functionality without a reason.
- Avoid unnecessary refactors while implementing a feature.
- Do not commit generated files, secrets, credentials, or local configuration.
- Before adding new tooling or config files, check whether `.gitignore` already covers what they produce. It must cover at least: `config.local.json`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `build/`, `dist/`, working and backup folders, `*.db`, the game's save files (`handball.challenge.metadata.dump.*`, `hc.metadata.stats.dump.*`), and any extracted game assets. Extend it if not.
- Run `git status` before committing and check the file list for anything that looks like a key, a credential, a game database, or a local machine path before staging it.
- Keep the documentation current: `README.md` for people who use the program (what it does, legal notice, download, configuration, usage) and `CONTRIBUTING.md` for people who work on it (setup, running from source, command line, checks, MCP server, release build, dependencies, structure).

## Security and secrets

- Never hardcode the key, passwords, tokens, or credentials.
- Do not log sensitive information.
- Validate external input.
- Treat the contents of game files and imported data as untrusted input.

## Security reviews

- Treat security as a first-class requirement.
- For security-sensitive changes, perform an appropriate security review before considering the work complete.
- Use automated security tooling where available.
- For significant security-sensitive changes, prefer a dedicated security audit rather than relying only on the normal code review.
- Do not automatically apply security fixes without reviewing their impact.
- Treat security findings as hypotheses that must be validated before making significant changes.

## Dependencies and SBOM

- Prefer the standard library (`sqlite3`, `tkinter`, `ctypes`, `logging`, `pathlib`, `dataclasses`). The only runtime dependency is Pillow, used only in `app/portrait_image.py` to decode and resize portrait photos (untrusted input: size and pixel limits, errors translated).
- Keep dependencies explicit and pinned: runtime in `requirements.txt`, development tools (`pytest`, `ruff`, `pip-audit`, `cyclonedx-bom`) in `requirements-dev.txt`.
- Do not introduce dependencies without checking their maintenance status, security history, and license.
- Check for known vulnerabilities with `pip-audit -r requirements.txt` after changing dependencies and before a release build.
- Maintain an SBOM for release builds. This project uses CycloneDX. Regenerate `sbom.cdx.json` from the pinned requirements with `cyclonedx-py requirements --pyproject pyproject.toml --output-reproducible requirements.txt -o sbom.cdx.json` (check `cyclonedx-py --help` if the options change), after any change to `requirements.txt` and before cutting a release build.
- Release builds are produced with PyInstaller on 32-bit Python.

## Before finishing

Check:

- Does the implementation match the requested behavior?
- Did I preserve existing functionality?
- Did I introduce unnecessary complexity?
- Did I add appropriate tests?
- Does `ruff check` pass?
- Do the tests pass?
- Did I accidentally modify unrelated files?
- Did any key, game database, or local path end up in the change?
