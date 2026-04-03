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


def namespace_shell_error_message() -> str:
    return "linux per-shell activation requires entering a packagent-managed shell via 'packagent init' or 'packagent shell init'"


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


def render_shell_init(
    shell_name: str,
    initial_result: ActivationResult | None = None,
    *,
    use_linux_namespace: bool = False,
) -> str:
    if shell_name == "bash":
        script = _render_bash_init(use_linux_namespace=use_linux_namespace)
    elif shell_name == "zsh":
        script = _render_zsh_init(use_linux_namespace=use_linux_namespace)
    else:
        raise ValueError(f"unsupported shell: {shell_name}")
    if initial_result is None:
        return script
    activate_block = render_activate_commands(shell_name, initial_result)
    if use_linux_namespace:
        activate_block = "\n".join(
            [
                'if [ -n "${PACKAGENT_NAMESPACE_ACTIVE-}" ]; then',
                _indent_block(activate_block),
                "fi",
            ],
        )
    return f"{script}\n{activate_block}"


def render_shell_rc_block(shell_name: str) -> str:
    _validate_shell(shell_name)
    return "\n".join(
        [
            INIT_BLOCK_START,
            "if command -v packagent >/dev/null 2>&1; then",
            f'  eval "$(packagent shell init {shell_name})"',
            "fi",
            INIT_BLOCK_END,
        ],
    )


def render_activate_commands(shell_name: str, result: ActivationResult) -> str:
    _validate_shell(shell_name)
    lines = [
        f"export PACKAGENT_ACTIVE_ENV={_shell_quote(result.env_name)}",
        f"export PACKAGENT_ACTIVE_HOST={_shell_quote('codex')}",
        f"export PACKAGENT_BACKING_HOME={_shell_quote(result.backing_home_path)}",
        f"export CODEX_HOME={_shell_quote(result.managed_home_path)}",
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


def _render_bash_init(*, use_linux_namespace: bool) -> str:
    namespace_block = _render_linux_namespace_block("bash") if use_linux_namespace else ""
    return f"""
{namespace_block}
if [ -z "${{PACKAGENT_ORIGINAL_PS1+x}}" ]; then
  export PACKAGENT_ORIGINAL_PS1="${{PS1-}}"
fi
_packagent_original_prompt_command="${{PROMPT_COMMAND-}}"
_packagent_refresh_prompt() {{
  local base_prompt="${{PACKAGENT_ORIGINAL_PS1-}}"
  if [ -n "${{PACKAGENT_ACTIVE_ENV-}}" ]; then
    PS1="(${{PACKAGENT_ACTIVE_ENV}}) ${{base_prompt}}"
  else
    PS1="${{base_prompt}}"
  fi
}}
_packagent_prompt_command() {{
  if [ -n "${{_packagent_original_prompt_command-}}" ]; then
    eval "${{_packagent_original_prompt_command}}"
  fi
  _packagent_refresh_prompt
}}
PROMPT_COMMAND="_packagent_prompt_command"
packagent() {{
  if [ "$1" = "activate" ] || [ "$1" = "deactivate" ]; then
    local _packagent_output
    _packagent_output="$(PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=bash command packagent "$@")" || return $?
    eval "${{_packagent_output}}"
    return 0
  fi
  command packagent "$@"
}}
_packagent_refresh_prompt
""".strip()


def _render_zsh_init(*, use_linux_namespace: bool) -> str:
    namespace_block = _render_linux_namespace_block("zsh") if use_linux_namespace else ""
    return f"""
{namespace_block}
if [[ -z "${{PACKAGENT_ORIGINAL_PROMPT+x}}" ]]; then
  export PACKAGENT_ORIGINAL_PROMPT="${{PROMPT-}}"
fi
_packagent_refresh_prompt() {{
  local base_prompt="${{PACKAGENT_ORIGINAL_PROMPT-}}"
  if [[ -n "${{PACKAGENT_ACTIVE_ENV-}}" ]]; then
    PROMPT="(${{PACKAGENT_ACTIVE_ENV}}) ${{base_prompt}}"
  else
    PROMPT="${{base_prompt}}"
  fi
}}
if (( ${{precmd_functions[(I)_packagent_refresh_prompt]}} == 0 )); then
  precmd_functions+=(_packagent_refresh_prompt)
fi
packagent() {{
  if [[ "$1" == "activate" || "$1" == "deactivate" ]]; then
    local _packagent_output
    _packagent_output="$(PACKAGENT_SHELL_HOOK=1 PACKAGENT_SHELL=zsh command packagent "$@")" || return $?
    eval "${{_packagent_output}}"
    return 0
  fi
  command packagent "$@"
}}
_packagent_refresh_prompt
""".strip()


def _render_linux_namespace_block(shell_name: str) -> str:
    return f"""
if [ -z "${{PACKAGENT_NAMESPACE_ACTIVE-}}" ]; then
  if command packagent shell supports-namespace >/dev/null 2>&1; then
    exec packagent shell enter {shell_name}
  else
    command packagent shell supports-namespace
  fi
fi
""".strip()


def _indent_block(value: str, prefix: str = "  ") -> str:
    return "\n".join(f"{prefix}{line}" if line else line for line in value.splitlines())
