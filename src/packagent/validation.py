from __future__ import annotations

import re

from packagent.errors import UserFacingError

ENV_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_env_name(name: str, *, allow_base: bool = False) -> str:
    if not name:
        raise UserFacingError("environment name must not be empty")
    if name in {".", ".."}:
        raise UserFacingError("environment name must not be '.' or '..'")
    if not allow_base and name == "base":
        raise UserFacingError("'base' is reserved for the built-in environment")
    if not ENV_NAME_PATTERN.match(name):
        raise UserFacingError(
            "environment names must start with a letter or number and use only letters, numbers, '.', '_' or '-'",
        )
    return name

