from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from packagent.models import ActivationResult
from packagent.shell import (
    default_rc_path,
    detect_shell,
    install_shell_init,
    render_activate_commands,
    render_deactivate_commands,
    render_shell_init,
)


def test_bash_shell_init_contains_wrapper_and_prompt_hook() -> None:
    script = render_shell_init("bash")
    assert 'packagent() {' in script
    assert 'PROMPT_COMMAND="_packagent_prompt_command"' not in script
    assert "_omb_util_add_prompt_command _packagent_prompt_command" in script
    assert "packagent_prompt_info()" in script
    assert "__powerline_packagent_prompt()" in script
    assert 'PACKAGENT_SHELL=bash' in script


def test_bash_shell_init_is_safe_to_source_repeatedly(tmp_path: Path) -> None:
    prompt_output = tmp_path / "prompt-output.txt"
    script_path = tmp_path / "double-source.bash"
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
export PACKAGENT_TEST_PROMPT_OUTPUT={shlex.quote(str(prompt_output))}
PS1='prompt$ '
PROMPT_COMMAND='printf original >> "$PACKAGENT_TEST_PROMPT_OUTPUT"'
{hook}
{hook}
case "${{PROMPT_COMMAND-}}" in
  *"_packagent_prompt_command"*) ;;
  *) echo "missing packagent prompt command: $PROMPT_COMMAND" >&2; exit 1 ;;
esac
case "${{PROMPT_COMMAND-}}" in
  *"_packagent_prompt_command;_packagent_prompt_command"*) echo "duplicate prompt command: $PROMPT_COMMAND" >&2; exit 1 ;;
esac
PACKAGENT_ACTIVE_ENV=base
eval "$PROMPT_COMMAND"
case "$PS1" in
  "(base) "*) ;;
  *) echo "prompt was not refreshed: $PS1" >&2; exit 1 ;;
esac
printf "%s" "$(packagent_prompt_info)" > "{prompt_output}.modifier"
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert prompt_output.read_text(encoding="utf-8") == "original"
    assert Path(f"{prompt_output}.modifier").read_text(encoding="utf-8") == "(base) "


def test_bash_shell_init_appends_to_array_prompt_command(tmp_path: Path) -> None:
    prompt_output = tmp_path / "array-prompt-output.txt"
    script_path = tmp_path / "array-prompt.bash"
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
export PACKAGENT_TEST_PROMPT_OUTPUT={shlex.quote(str(prompt_output))}
PS1='prompt$ '
PROMPT_COMMAND=('printf first >> "$PACKAGENT_TEST_PROMPT_OUTPUT"' 'printf second >> "$PACKAGENT_TEST_PROMPT_OUTPUT"')
{hook}
{hook}
[ "${{#PROMPT_COMMAND[@]}}" -eq 3 ]
[ "${{PROMPT_COMMAND[2]}}" = "_packagent_prompt_command" ]
PACKAGENT_ACTIVE_ENV=work
for command in "${{PROMPT_COMMAND[@]}}"; do
  eval "$command"
done
case "$PS1" in
  "(work) "*) ;;
  *) echo "prompt was not refreshed: $PS1" >&2; exit 1 ;;
esac
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert prompt_output.read_text(encoding="utf-8") == "firstsecond"


def test_bash_shell_init_does_not_duplicate_after_conda_prefix(tmp_path: Path) -> None:
    script_path = tmp_path / "conda-prefix.bash"
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
PS1='prompt$ '
PACKAGENT_ACTIVE_ENV=base
{hook}
_packagent_prompt_command
[ "$PS1" = '(base) prompt$ ' ]
PS1="(llm) $PS1"
_packagent_prompt_command
[ "$PS1" = '(base) (llm) prompt$ ' ]
PACKAGENT_ACTIVE_ENV=work
_packagent_prompt_command
[ "$PS1" = '(work) (llm) prompt$ ' ]
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_bash_shell_init_registers_with_oh_my_bash_prompt_hook(tmp_path: Path) -> None:
    script_path = tmp_path / "omb-prompt.bash"
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
PS1='initial$ '
_omb_util_prompt_command=()
_omb_util_add_prompt_command() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]}}"; do
    [ "$hook" = "$1" ] && return 0
  done
  _omb_util_prompt_command+=("$1")
  PROMPT_COMMAND="_omb_util_prompt_command_hook"
}}
_omb_util_prompt_command_hook() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]}}"; do
    "$hook"
  done
}}
_omb_theme_PROMPT_COMMAND() {{
  PS1='theme$ '
}}
_omb_util_add_prompt_command _omb_theme_PROMPT_COMMAND
PACKAGENT_ACTIVE_ENV=base
{hook}
{hook}
[ "${{PROMPT_COMMAND-}}" = "_omb_util_prompt_command_hook" ]
[ "${{#_omb_util_prompt_command[@]}}" -eq 2 ]
[ "${{_omb_util_prompt_command[0]}}" = "_omb_theme_PROMPT_COMMAND" ]
[ "${{_omb_util_prompt_command[1]}}" = "_packagent_prompt_command" ]
_omb_util_prompt_command_hook
[ "$PS1" = '(base) theme$ ' ]
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_bash_shell_init_adds_oh_my_bash_powerline_segment(tmp_path: Path) -> None:
    script_path = tmp_path / "omb-powerline.bash"
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
PS1='initial$ '
_omb_util_prompt_command=()
_omb_util_add_prompt_command() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]}}"; do
    [ "$hook" = "$1" ] && return 0
  done
  _omb_util_prompt_command+=("$1")
  PROMPT_COMMAND="_omb_util_prompt_command_hook"
}}
_omb_util_prompt_command_hook() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]}}"; do
    "$hook"
  done
}}
POWERLINE_PROMPT='user_info scm cwd'
PYTHON_VENV_THEME_PROMPT_COLOR=35
__powerline_user_info_prompt() {{ printf 'user|32\\n'; }}
__powerline_scm_prompt() {{ return 1; }}
__powerline_cwd_prompt() {{ printf '~/code|240\\n'; }}
_omb_theme_PROMPT_COMMAND() {{
  local segment info
  PS1=''
  for segment in $POWERLINE_PROMPT; do
    info=''
    if command -v "__powerline_${{segment}}_prompt" >/dev/null 2>&1; then
      info="$("__powerline_${{segment}}_prompt")" || true
    fi
    [ -n "$info" ] && PS1="${{PS1}}${{info}} "
  done
}}
_omb_util_add_prompt_command _omb_theme_PROMPT_COMMAND
PACKAGENT_ACTIVE_ENV=base
{hook}
{hook}
[ "$POWERLINE_PROMPT" = 'user_info scm packagent cwd' ]
_omb_util_prompt_command_hook
case "$PS1" in
  *'[pa] base|35'*) ;;
  *) echo "missing packagent segment: $PS1" >&2; exit 1 ;;
esac
case "$PS1" in
  "(base) "*) echo "unexpected fallback prefix: $PS1" >&2; exit 1 ;;
esac
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_zsh_shell_init_contains_wrapper_and_precmd_hook() -> None:
    script = render_shell_init("zsh")
    assert 'packagent() {' in script
    assert "add-zsh-hook precmd _packagent_prompt_command" in script
    assert "precmd_functions+=(_packagent_prompt_command)" in script
    assert "packagent_prompt_info()" in script
    assert 'PACKAGENT_SHELL=zsh' in script


def test_detect_shell_prefers_process_tree_over_login_shell(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr("packagent.shell._detect_shell_from_process_tree", lambda: "/bin/bash")
    monkeypatch.setattr("packagent.shell._detect_login_shell", lambda: "/bin/zsh")

    assert detect_shell() == "bash"


def test_detect_shell_falls_back_to_login_shell_when_env_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr("packagent.shell._detect_shell_from_process_tree", lambda: "")
    monkeypatch.setattr("packagent.shell._detect_login_shell", lambda: "/bin/bash")

    assert detect_shell() == "bash"


def test_default_rc_path_matches_shell(tmp_path: Path) -> None:
    assert default_rc_path("bash", tmp_path) == tmp_path / ".bashrc"
    assert default_rc_path("zsh", tmp_path) == tmp_path / ".zshrc"


def test_install_shell_init_writes_managed_block_idempotently(tmp_path: Path) -> None:
    rc_path = tmp_path / ".bashrc"
    rc_path.write_text("export PATH=/tmp/bin:$PATH\n", encoding="utf-8")

    first = install_shell_init("bash", rc_path)
    second = install_shell_init("bash", rc_path)
    content = rc_path.read_text(encoding="utf-8")

    assert first.changed is True
    assert second.changed is False
    assert content.count("# >>> packagent initialize >>>") == 1
    assert 'eval "$(packagent shell init bash)"' in content


def test_shell_init_can_bootstrap_the_current_env() -> None:
    result = ActivationResult(
        env_name="base",
        managed_home_path="/tmp/home/.codex",
        codex_home="/tmp/home/.packagent/envs/base/.codex",
    )
    script = render_shell_init("bash", result)

    assert "export PACKAGENT_ACTIVE_ENV='base'" in script
    assert "export CODEX_HOME=" not in script
    assert "export CLAUDE_CONFIG_DIR=" not in script


def test_activate_and_deactivate_shell_commands_are_export_friendly() -> None:
    result = ActivationResult(
        env_name="work",
        managed_home_path="/tmp/home/.codex",
        codex_home="/tmp/home/.packagent/envs/work/.codex",
    )
    activate_script = render_activate_commands("zsh", result)
    deactivate_result = ActivationResult(
        env_name="base",
        managed_home_path="/tmp/home/.codex",
        codex_home="/tmp/home/.packagent/envs/base/.codex",
    )
    deactivate_script = render_deactivate_commands("zsh", deactivate_result)

    assert "export PACKAGENT_ACTIVE_ENV='work'" in activate_script
    assert "export CODEX_HOME=" not in activate_script
    assert "export CLAUDE_CONFIG_DIR=" not in activate_script
    assert "export PACKAGENT_ACTIVE_ENV='base'" in deactivate_script
    assert "export CODEX_HOME=" not in deactivate_script
    assert "export CLAUDE_CONFIG_DIR=" not in deactivate_script
