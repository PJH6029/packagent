class PackagentError(Exception):
    """Base error for packagent."""


class UserFacingError(PackagentError):
    """An error that should be shown to the user without a traceback."""

