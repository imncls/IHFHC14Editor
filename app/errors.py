class AppError(Exception):
    """An error whose message is written by the app and is safe to show."""


class ConfigError(AppError):
    pass


class FolderError(ConfigError):
    """The game or save folder is missing or wrong; `field` says which
    ("game_dir" or "save_dir"), so the window can ask for it."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


class CryptoError(AppError):
    pass


class WrongKeyError(CryptoError):
    pass


class GameRunningError(AppError):
    pass


class WorkspaceError(AppError):
    pass


class ValidationError(AppError):
    def __init__(
        self, message: str, field: str | None = None, team: int | None = None
    ) -> None:
        super().__init__(message)
        self.field = field
        # When several teams are created at once: the index of the wrong one.
        self.team = team
