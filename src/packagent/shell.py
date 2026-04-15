from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import re
import subprocess

from packagent.models import ActivationResult


SUPPORTED_SHELLS = ("bash", "zsh")
INIT_BLOCK_START = "# >>> packagent initialize >>>"
INIT_BLOCK_END = "# <<< packagent initialize <<<"


@dataclass(frozen=True)
class ShellInitInstallResult:
    shell_name: str
    rc_path: str
    changed: bool


@dataclass(frozen=True)
class ShellInitRemoveResult:
    shell_name: str
    rc_path: str
    changed: bool


def detect_shell() -> str:
    for candidate in (
        os.environ.get("PACKAGENT_SHELL", ""),
        _detect_shell_from_process_tree(),
        os.environ.get("SHELL", ""),
        _detect_login_shell(),
    ):
        shell_name = Path(candidate).name.lstrip("-")
        if shell_name in SUPPORTED_SHELLS:
            return shell_name
    return "zsh"


def shell_hook_error_message(shell_name: str | None = None) -> str:
    shell_name = shell_name or detect_shell()
    return f"activation must be run through the shell hook. Run: eval \"$(packagent shell init {shell_name})\""


def default_rc_path(shell_name: str, home: Path | None = None) -> Path:
    _validate_shell(shell_name)
    root = home or Path.home()
    return root / (".bashrc" if shell_name == "bash" else ".zshrc")


def install_shell_init(shell_name: str, rc_path: Path) -> ShellInitInstallResult:
    _validate_shell(shell_name)
    block = render_shell_rc_block(shell_name)
    existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
    updated = _upsert_init_block(existing, block)
    changed = updated != existing
    rc_path.parent.mkdir(parents=True, exist_ok=True)
    if changed or not rc_path.exists():
        rc_path.write_text(updated, encoding="utf-8")
    return ShellInitInstallResult(shell_name=shell_name, rc_path=str(rc_path), changed=changed)


def remove_shell_init(shell_name: str, rc_path: Path) -> ShellInitRemoveResult:
    _validate_shell(shell_name)
    if not rc_path.exists():
        return ShellInitRemoveResult(shell_name=shell_name, rc_path=str(rc_path), changed=False)
    existing = rc_path.read_text(encoding="utf-8")
    updated = _remove_init_block(existing)
    changed = updated != existing
    if changed:
        rc_path.write_text(updated, encoding="utf-8")
    return ShellInitRemoveResult(shell_name=shell_name, rc_path=str(rc_path), changed=changed)


def render_shell_init(shell_name: str, initial_result: ActivationResult | None = None) -> str:
    if shell_name == "bash":
        script = _render_bash_init()
    elif shell_name == "zsh":
        script = _render_zsh_init()
    else:
        raise ValueError(f"unsupported shell: {shell_name}")
    if initial_result is None:
        return script
    return f"{script}\n{render_activate_commands(shell_name, initial_result)}"


def render_shell_rc_block(shell_name: str) -> str:
    _validate_shell(shell_name)
    lines = [
        INIT_BLOCK_START,
        "_packagent_bin=packagent",
        'if ! command -v "$_packagent_bin" >/dev/null 2>&1; then',
        "  _packagent_bin=",
        "fi",
        'if [ -z "$_packagent_bin" ] && [ -x "$HOME/.local/bin/packagent" ]; then',
        '  _packagent_bin="$HOME/.local/bin/packagent"',
        "fi",
        'if [ -n "$_packagent_bin" ]; then',
        '  PACKAGENT_BIN="$_packagent_bin"',
        "  export PACKAGENT_BIN",
        f'  eval "$("$_packagent_bin" shell init {shell_name})"',
        "fi",
        "unset _packagent_bin",
        INIT_BLOCK_END,
    ]
    return "\n".join(lines)


def render_activate_commands(shell_name: str, result: ActivationResult) -> str:
    _validate_shell(shell_name)
    lines = [
        f"export PACKAGENT_ACTIVE_ENV={_shell_quote(result.env_name)}",
        f"export PACKAGENT_ACTIVE_HOST={_shell_quote('codex')}",
        "_packagent_refresh_prompt >/dev/null 2>&1 || true",
    ]
    return "\n".join(lines)


def render_deactivate_commands(shell_name: str, result: ActivationResult) -> str:
    _validate_shell(shell_name)
    return render_activate_commands(shell_name, result)


def _validate_shell(shell_name: str) -> None:
    if shell_name not in SUPPORTED_SHELLS:
        raise ValueError(f"unsupported shell: {shell_name}")


def _detect_shell_from_process_tree() -> str:
    pid = os.getppid()
    visited: set[int] = set()
    while pid > 1 and pid not in visited:
        visited.add(pid)
        process_name, parent_pid = _read_process_info(pid)
        shell_name = Path(process_name).name.lstrip("-")
        if shell_name in SUPPORTED_SHELLS:
            return shell_name
        if not parent_pid or parent_pid == pid:
            break
        pid = parent_pid
    return ""


def _detect_login_shell() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        return ""


def _read_process_info(pid: int) -> tuple[str, int]:
    process_name, parent_pid = _read_process_info_procfs(pid)
    if process_name:
        return process_name, parent_pid
    return _read_process_info_ps(pid)


def _read_process_info_procfs(pid: int) -> tuple[str, int]:
    status_path = Path("/proc") / str(pid) / "status"
    comm_path = Path("/proc") / str(pid) / "comm"
    try:
        process_name = comm_path.read_text(encoding="utf-8").strip()
        status_text = status_path.read_text(encoding="utf-8")
    except OSError:
        return "", 0
    parent_pid = 0
    for line in status_text.splitlines():
        if line.startswith("PPid:"):
            try:
                parent_pid = int(line.split(":", 1)[1].strip())
            except ValueError:
                parent_pid = 0
            break
    return process_name, parent_pid


def _read_process_info_ps(pid: int) -> tuple[str, int]:
    try:
        process_name = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        parent_pid_raw = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid="],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return "", 0
    try:
        parent_pid = int(parent_pid_raw)
    except ValueError:
        parent_pid = 0
    return process_name, parent_pid


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _upsert_init_block(existing: str, block: str) -> str:
    normalized_block = block.rstrip() + "\n"
    pattern = re.compile(
        rf"{re.escape(INIT_BLOCK_START)}.*?{re.escape(INIT_BLOCK_END)}\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        return pattern.sub(normalized_block, existing, count=1)
    if not existing:
        return normalized_block
    prefix = existing
    if not prefix.endswith("\n"):
        prefix += "\n"
    if not prefix.endswith("\n\n"):
        prefix += "\n"
    return prefix + normalized_block


def _remove_init_block(existing: str) -> str:
    pattern = re.compile(
        rf"{re.escape(INIT_BLOCK_START)}.*?{re.escape(INIT_BLOCK_END)}\n?",
        re.DOTALL,
    )
    return pattern.sub("", existing, count=1)


def _render_bash_init() -> str:
    return """
_packagent_call() {
  command "${PACKAGENT_BIN:-packagent}" "$@"
}
_packagent_sync_active_env() {
  local active_env
  if active_env="$(_packagent_call shell active-env 2>/dev/null)"; then
    if [ -n "$active_env" ]; then
      PACKAGENT_ACTIVE_ENV="$active_env"
      PACKAGENT_ACTIVE_HOST="codex"
      export PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
    else
      unset PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
    fi
  else
    unset PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
  fi
}
_packagent_update_prompt_modifier() {
  _packagent_sync_active_env
  if [ -n "${PACKAGENT_ACTIVE_ENV-}" ]; then
    PACKAGENT_PROMPT_MODIFIER="(${PACKAGENT_ACTIVE_ENV}) "
  else
    PACKAGENT_PROMPT_MODIFIER=""
  fi
  export PACKAGENT_PROMPT_MODIFIER
}
packagent_prompt_info() {
  _packagent_update_prompt_modifier
  [ -n "${PACKAGENT_PROMPT_MODIFIER-}" ] || return 1
  printf "%s" "${PACKAGENT_PROMPT_MODIFIER}"
}
_packagent_remove_prompt_modifier() {
  local prompt="$1"
  local modifier="$2"
  if [ -n "$modifier" ]; then
    prompt="${prompt/"$modifier"/}"
  fi
  printf "%s" "$prompt"
}
_packagent_strip_prompt_prefix() {
  local prompt="$1"
  prompt="$(_packagent_remove_prompt_modifier "$prompt" "${PACKAGENT_PROMPT_LAST_MODIFIER-}")"
  prompt="$(_packagent_remove_prompt_modifier "$prompt" "${PACKAGENT_PROMPT_MODIFIER-}")"
  printf "%s" "$prompt"
}
_packagent_word_in_list() {
  case " $1 " in
    *" $2 "*) return 0 ;;
    *) return 1 ;;
  esac
}
_packagent_insert_prompt_segment_before_cwd() {
  local list="${1-}"
  local segment
  local result=""
  local inserted=""
  if _packagent_word_in_list "$list" "packagent"; then
    printf "%s" "$list"
    return 0
  fi
  for segment in $list; do
    if [ "$segment" = "cwd" ] && [ -z "$inserted" ]; then
      result="${result:+$result }packagent"
      inserted=1
    fi
    result="${result:+$result }$segment"
  done
  if [ -z "$inserted" ]; then
    result="${result:+$result }packagent"
  fi
  printf "%s" "$result"
}
__powerline_packagent_prompt() {
  _packagent_update_prompt_modifier
  [ -n "${PACKAGENT_ACTIVE_ENV-}" ] || return 1
  local label="${PACKAGENT_OMB_POWERLINE_LABEL:-[pa] }"
  local color="${PACKAGENT_OMB_POWERLINE_COLOR:-${PYTHON_VENV_THEME_PROMPT_COLOR:-35}}"
  printf "%s%s|%s\\n" "$label" "$PACKAGENT_ACTIVE_ENV" "$color"
}
_packagent_install_omb_powerline_segment() {
  PACKAGENT_PROMPT_NATIVE=0
  if [ -n "${POWERLINE_PROMPT+x}" ]; then
    POWERLINE_PROMPT="$(_packagent_insert_prompt_segment_before_cwd "$POWERLINE_PROMPT")"
    PACKAGENT_PROMPT_NATIVE=1
  fi
  if [ -n "${POWERLINE_LEFT_PROMPT+x}" ]; then
    POWERLINE_LEFT_PROMPT="$(_packagent_insert_prompt_segment_before_cwd "$POWERLINE_LEFT_PROMPT")"
    PACKAGENT_PROMPT_NATIVE=1
  fi
}
_packagent_refresh_prompt() {
  _packagent_update_prompt_modifier
  if [ "${PACKAGENT_PROMPT_NATIVE-0}" = "1" ]; then
    PACKAGENT_PROMPT_LAST_MODIFIER=""
    return 0
  fi
  local base_prompt
  base_prompt="$(_packagent_strip_prompt_prefix "${PS1-}")"
  if [ -n "${PACKAGENT_PROMPT_MODIFIER-}" ]; then
    PS1="${PACKAGENT_PROMPT_MODIFIER}${base_prompt}"
  else
    PS1="${base_prompt}"
  fi
  PACKAGENT_PROMPT_LAST_MODIFIER="${PACKAGENT_PROMPT_MODIFIER-}"
}
_packagent_prompt_command() {
  _packagent_refresh_prompt
}
_packagent_prompt_command_is_array() {
  local declaration
  declaration="$(declare -p PROMPT_COMMAND 2>/dev/null || true)"
  [[ "$declaration" =~ ^declare[[:space:]]+-[^[:space:]]*a[^[:space:]]*[[:space:]]+PROMPT_COMMAND= ]]
}
_packagent_prompt_command_registered() {
  local command
  if _packagent_prompt_command_is_array; then
    for command in "${PROMPT_COMMAND[@]}"; do
      [ "$command" = "_packagent_prompt_command" ] && return 0
    done
    return 1
  fi
  case ";${PROMPT_COMMAND-};" in
    *";_packagent_prompt_command;"*) return 0 ;;
    *) return 1 ;;
  esac
}
_packagent_install_prompt_command() {
  _packagent_install_omb_powerline_segment
  if command -v _omb_util_add_prompt_command >/dev/null 2>&1; then
    _omb_util_add_prompt_command _packagent_prompt_command
    return 0
  fi
  _packagent_prompt_command_registered && return 0
  if _packagent_prompt_command_is_array; then
    PROMPT_COMMAND+=(_packagent_prompt_command)
  else
    PROMPT_COMMAND="${PROMPT_COMMAND:+${PROMPT_COMMAND};}_packagent_prompt_command"
  fi
}
_packagent_install_prompt_command
packagent() {
  if [ "$1" = "activate" ] || [ "$1" = "deactivate" ]; then
    local _packagent_output
    _packagent_output="$(PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=bash _packagent_call "$@")" || return $?
    eval "${_packagent_output}"
    return 0
  fi
  if [ "$1" = "uninstall" ]; then
    PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=bash _packagent_call "$@"
    local _packagent_status=$?
    if [ "$_packagent_status" -eq 0 ]; then
      unset PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
      _packagent_refresh_prompt >/dev/null 2>&1 || true
      unset PACKAGENT_PROMPT_MODIFIER PACKAGENT_PROMPT_LAST_MODIFIER
    fi
    return "$_packagent_status"
  fi
  _packagent_call "$@"
}
_packagent_refresh_prompt
""".strip()


def _render_zsh_init() -> str:
    return """
_packagent_call() {
  command "${PACKAGENT_BIN:-packagent}" "$@"
}
_packagent_sync_active_env() {
  local active_env
  if active_env="$(_packagent_call shell active-env 2>/dev/null)"; then
    if [[ -n "$active_env" ]]; then
      PACKAGENT_ACTIVE_ENV="$active_env"
      PACKAGENT_ACTIVE_HOST="codex"
      export PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
    else
      unset PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
    fi
  else
    unset PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
  fi
}
_packagent_update_prompt_modifier() {
  _packagent_sync_active_env
  if [[ -n "${PACKAGENT_ACTIVE_ENV-}" ]]; then
    PACKAGENT_PROMPT_MODIFIER="(${PACKAGENT_ACTIVE_ENV}) "
  else
    PACKAGENT_PROMPT_MODIFIER=""
  fi
  export PACKAGENT_PROMPT_MODIFIER
}
packagent_prompt_info() {
  _packagent_update_prompt_modifier
  [[ -n "${PACKAGENT_PROMPT_MODIFIER-}" ]] || return 1
  print -r -- "${PACKAGENT_PROMPT_MODIFIER}"
}
_packagent_remove_prompt_modifier() {
  local prompt="$1"
  local modifier="$2"
  if [[ -n "$modifier" ]]; then
    prompt="${prompt/${modifier}/}"
  fi
  print -r -- "$prompt"
}
_packagent_strip_prompt_prefix() {
  local prompt="$1"
  prompt="$(_packagent_remove_prompt_modifier "$prompt" "${PACKAGENT_PROMPT_LAST_MODIFIER-}")"
  prompt="$(_packagent_remove_prompt_modifier "$prompt" "${PACKAGENT_PROMPT_MODIFIER-}")"
  print -r -- "$prompt"
}
_packagent_zsh_prompt_position() {
  local requested="${PACKAGENT_ZSH_PROMPT_POSITION:-auto}"
  case "$requested" in
    left|right)
      print -r -- "$requested"
      return 0
      ;;
  esac
  local base_rprompt="$1"
  if [[ -n "$base_rprompt" ]]; then
    print -r -- "right"
  else
    print -r -- "left"
  fi
}
_packagent_refresh_prompt() {
  _packagent_update_prompt_modifier
  local base_prompt
  local base_rprompt
  local prompt_position
  base_prompt="$(_packagent_strip_prompt_prefix "${PROMPT-}")"
  base_rprompt="$(_packagent_strip_prompt_prefix "${RPROMPT-}")"
  prompt_position="$(_packagent_zsh_prompt_position "$base_rprompt")"
  if [[ "$prompt_position" == "right" ]]; then
    PROMPT="${base_prompt}"
    if [[ -n "${PACKAGENT_PROMPT_MODIFIER-}" ]]; then
      RPROMPT="${PACKAGENT_PROMPT_MODIFIER}${base_rprompt}"
    else
      RPROMPT="${base_rprompt}"
    fi
  else
    if [[ -n "${PACKAGENT_PROMPT_MODIFIER-}" ]]; then
      PROMPT="${PACKAGENT_PROMPT_MODIFIER}${base_prompt}"
    else
      PROMPT="${base_prompt}"
    fi
    RPROMPT="${base_rprompt}"
  fi
  PACKAGENT_PROMPT_LAST_MODIFIER="${PACKAGENT_PROMPT_MODIFIER-}"
}
_packagent_prompt_command() {
  _packagent_refresh_prompt
}
autoload -Uz add-zsh-hook 2>/dev/null || true
if (( $+functions[add-zsh-hook] )); then
  add-zsh-hook -d precmd _packagent_refresh_prompt >/dev/null 2>&1 || true
  add-zsh-hook -d precmd _packagent_prompt_command >/dev/null 2>&1 || true
  add-zsh-hook precmd _packagent_prompt_command
else
  precmd_functions=(${precmd_functions:#_packagent_refresh_prompt})
  if (( ${precmd_functions[(I)_packagent_prompt_command]} == 0 )); then
    precmd_functions+=(_packagent_prompt_command)
  fi
fi
packagent() {
  if [[ "$1" == "activate" || "$1" == "deactivate" ]]; then
    local _packagent_output
    _packagent_output="$(PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=zsh _packagent_call "$@")" || return $?
    eval "${_packagent_output}"
    return 0
  fi
  if [[ "$1" == "uninstall" ]]; then
    PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=zsh _packagent_call "$@"
    local _packagent_status=$?
    if [[ "$_packagent_status" -eq 0 ]]; then
      unset PACKAGENT_ACTIVE_ENV PACKAGENT_ACTIVE_HOST
      _packagent_refresh_prompt >/dev/null 2>&1 || true
      unset PACKAGENT_PROMPT_MODIFIER PACKAGENT_PROMPT_LAST_MODIFIER
    fi
    return "$_packagent_status"
  fi
  _packagent_call "$@"
}
_packagent_refresh_prompt
""".strip()
