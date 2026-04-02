from __future__ import annotations

import os
from pathlib import Path

from packagent.models import ActivationResult


SUPPORTED_SHELLS = ("bash", "zsh")


def detect_shell() -> str:
    shell_path = os.environ.get("SHELL", "")
    shell_name = Path(shell_path).name
    if shell_name in SUPPORTED_SHELLS:
        return shell_name
    return "zsh"


def shell_hook_error_message(shell_name: str | None = None) -> str:
    shell_name = shell_name or detect_shell()
    return f"activation must be run through the shell hook. Run: eval \"$(packagent shell init {shell_name})\""


def render_shell_init(shell_name: str) -> str:
    if shell_name == "bash":
        return _render_bash_init()
    if shell_name == "zsh":
        return _render_zsh_init()
    raise ValueError(f"unsupported shell: {shell_name}")


def render_activate_commands(shell_name: str, result: ActivationResult) -> str:
    _validate_shell(shell_name)
    lines = [
        f"export PACKAGENT_ACTIVE_ENV={_shell_quote(result.env_name)}",
        f"export PACKAGENT_ACTIVE_HOST={_shell_quote('codex')}",
        f"export CODEX_HOME={_shell_quote(result.codex_home)}",
        "_packagent_refresh_prompt >/dev/null 2>&1 || true",
    ]
    return "\n".join(lines)


def render_deactivate_commands(shell_name: str) -> str:
    _validate_shell(shell_name)
    lines = [
        "unset PACKAGENT_ACTIVE_ENV",
        "unset PACKAGENT_ACTIVE_HOST",
        "unset CODEX_HOME",
        "_packagent_refresh_prompt >/dev/null 2>&1 || true",
    ]
    return "\n".join(lines)


def _validate_shell(shell_name: str) -> None:
    if shell_name not in SUPPORTED_SHELLS:
        raise ValueError(f"unsupported shell: {shell_name}")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _render_bash_init() -> str:
    return """
if [ -z "${PACKAGENT_ORIGINAL_PS1+x}" ]; then
  export PACKAGENT_ORIGINAL_PS1="${PS1-}"
fi
_packagent_original_prompt_command="${PROMPT_COMMAND-}"
_packagent_refresh_prompt() {
  local base_prompt="${PACKAGENT_ORIGINAL_PS1-}"
  if [ -n "${PACKAGENT_ACTIVE_ENV-}" ]; then
    PS1="(${PACKAGENT_ACTIVE_ENV}) ${base_prompt}"
  else
    PS1="${base_prompt}"
  fi
}
_packagent_prompt_command() {
  if [ -n "${_packagent_original_prompt_command-}" ]; then
    eval "${_packagent_original_prompt_command}"
  fi
  _packagent_refresh_prompt
}
PROMPT_COMMAND="_packagent_prompt_command"
packagent() {
  if [ "$1" = "activate" ] || [ "$1" = "deactivate" ]; then
    local _packagent_output
    _packagent_output="$(PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=bash command packagent "$@")" || return $?
    eval "${_packagent_output}"
    return 0
  fi
  command packagent "$@"
}
_packagent_refresh_prompt
""".strip()


def _render_zsh_init() -> str:
    return """
if [[ -z "${PACKAGENT_ORIGINAL_PROMPT+x}" ]]; then
  export PACKAGENT_ORIGINAL_PROMPT="${PROMPT-}"
fi
_packagent_refresh_prompt() {
  local base_prompt="${PACKAGENT_ORIGINAL_PROMPT-}"
  if [[ -n "${PACKAGENT_ACTIVE_ENV-}" ]]; then
    PROMPT="(${PACKAGENT_ACTIVE_ENV}) ${base_prompt}"
  else
    PROMPT="${base_prompt}"
  fi
}
if (( ${precmd_functions[(I)_packagent_refresh_prompt]} == 0 )); then
  precmd_functions+=(_packagent_refresh_prompt)
fi
packagent() {
  if [[ "$1" == "activate" || "$1" == "deactivate" ]]; then
    local _packagent_output
    _packagent_output="$(PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=zsh command packagent "$@")" || return $?
    eval "${_packagent_output}"
    return 0
  fi
  command packagent "$@"
}
_packagent_refresh_prompt
""".strip()
