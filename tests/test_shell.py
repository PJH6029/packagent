from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from packagent.models import ActivationResult
from packagent.shell import (
    default_rc_path,
    detect_shell,
    install_shell_init,
    remove_shell_init,
    render_activate_commands,
    render_deactivate_commands,
    render_shell_init,
    render_shell_rc_block,
)


def _write_active_env_packagent(tmp_path: Path) -> Path:
    executable = tmp_path / "packagent"
    executable.write_text(
        """#!/usr/bin/env bash
if [ "$1" = "shell" ] && [ "${2-}" = "active-env" ]; then
  printf '%s\\n' "${PACKAGENT_ACTIVE_ENV-}"
  exit 0
fi
exit 64
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_bash_shell_init_contains_wrapper_and_prompt_hook() -> None:
    script = render_shell_init("bash")
    assert 'packagent() {' in script
    assert "_packagent_sync_active_env" in script
    assert "shell active-env" in script
    assert 'PROMPT_COMMAND="_packagent_prompt_command"' not in script
    assert "_omb_util_add_prompt_command _packagent_prompt_command" in script
    assert "packagent_prompt_info()" in script
    assert "__powerline_packagent_prompt()" in script
    assert 'PACKAGENT_SHELL=bash' in script
    assert '[ "$1" = "uninstall" ]' in script


def test_bash_shell_init_is_safe_to_source_repeatedly(tmp_path: Path) -> None:
    prompt_output = tmp_path / "prompt-output.txt"
    script_path = tmp_path / "double-source.bash"
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
export PACKAGENT_TEST_PROMPT_OUTPUT={shlex.quote(str(prompt_output))}
export PACKAGENT_BIN={shlex.quote(str(executable))}
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
export PACKAGENT_ACTIVE_ENV=base
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
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
export PACKAGENT_TEST_PROMPT_OUTPUT={shlex.quote(str(prompt_output))}
export PACKAGENT_BIN={shlex.quote(str(executable))}
PS1='prompt$ '
PROMPT_COMMAND=('printf first >> "$PACKAGENT_TEST_PROMPT_OUTPUT"' 'printf second >> "$PACKAGENT_TEST_PROMPT_OUTPUT"')
{hook}
{hook}
[ "${{#PROMPT_COMMAND[@]}}" -eq 3 ]
[ "${{PROMPT_COMMAND[2]}}" = "_packagent_prompt_command" ]
export PACKAGENT_ACTIVE_ENV=work
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
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
PACKAGENT_BIN={shlex.quote(str(executable))}
PS1='prompt$ '
export PACKAGENT_ACTIVE_ENV=base
{hook}
_packagent_prompt_command
[ "$PS1" = '(base) prompt$ ' ]
PS1="(llm) $PS1"
_packagent_prompt_command
[ "$PS1" = '(base) (llm) prompt$ ' ]
export PACKAGENT_ACTIVE_ENV=work
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
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
PACKAGENT_BIN={shlex.quote(str(executable))}
PS1='initial$ '
_omb_util_prompt_command=()
_omb_util_add_prompt_command() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]+"${{_omb_util_prompt_command[@]}}"}}"; do
    [ "$hook" = "$1" ] && return 0
  done
  _omb_util_prompt_command+=("$1")
  PROMPT_COMMAND="_omb_util_prompt_command_hook"
}}
_omb_util_prompt_command_hook() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]+"${{_omb_util_prompt_command[@]}}"}}"; do
    "$hook"
  done
}}
_omb_theme_PROMPT_COMMAND() {{
  PS1='theme$ '
}}
_omb_util_add_prompt_command _omb_theme_PROMPT_COMMAND
export PACKAGENT_ACTIVE_ENV=base
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
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
PACKAGENT_BIN={shlex.quote(str(executable))}
PS1='initial$ '
_omb_util_prompt_command=()
_omb_util_add_prompt_command() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]+"${{_omb_util_prompt_command[@]}}"}}"; do
    [ "$hook" = "$1" ] && return 0
  done
  _omb_util_prompt_command+=("$1")
  PROMPT_COMMAND="_omb_util_prompt_command_hook"
}}
_omb_util_prompt_command_hook() {{
  local hook
  for hook in "${{_omb_util_prompt_command[@]+"${{_omb_util_prompt_command[@]}}"}}"; do
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
export PACKAGENT_ACTIVE_ENV=base
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
    assert "_packagent_sync_active_env" in script
    assert "shell active-env" in script
    assert "add-zsh-hook precmd _packagent_prompt_command" in script
    assert "precmd_functions+=(_packagent_prompt_command)" in script
    assert "packagent_prompt_info()" in script
    assert "prompt_packagent()" in script
    assert "POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS" in script
    assert "spaceship_packagent()" in script
    assert "SPACESHIP_PROMPT_ORDER" in script
    assert "PACKAGENT_ZSH_PROMPT_POSITION" in script
    assert 'PACKAGENT_SHELL=zsh' in script
    assert '[[ "$1" == "uninstall" ]]' in script


def test_zsh_shell_init_uses_right_prompt_when_theme_has_rprompt(tmp_path: Path) -> None:
    if shutil.which("zsh") is None:
        pytest.skip("zsh is not installed")

    script_path = tmp_path / "zsh-rprompt.zsh"
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("zsh")
    script_path.write_text(
        f"""
set -e
PACKAGENT_BIN={shlex.quote(str(executable))}
PROMPT='theme%# '
RPROMPT='conda kube'
export PACKAGENT_ACTIVE_ENV=base
{hook}
_theme_precmd() {{
  PROMPT='theme%# '
  RPROMPT='conda kube'
}}
_theme_precmd
_packagent_prompt_command
[[ "$PROMPT" == 'theme%# ' ]] || {{ print -u2 -- "unexpected left prompt: $PROMPT"; exit 1; }}
[[ "$RPROMPT" == '(base) conda kube' ]] || {{ print -u2 -- "unexpected right prompt: $RPROMPT"; exit 1; }}
export PACKAGENT_ACTIVE_ENV=work
_theme_precmd
_packagent_prompt_command
[[ "$PROMPT" == 'theme%# ' ]] || {{ print -u2 -- "unexpected left prompt after switch: $PROMPT"; exit 1; }}
[[ "$RPROMPT" == '(work) conda kube' ]] || {{ print -u2 -- "unexpected right prompt after switch: $RPROMPT"; exit 1; }}
_plain_theme_precmd() {{
  PROMPT='plain%# '
  RPROMPT=''
}}
_plain_theme_precmd
_packagent_prompt_command
[[ "$PROMPT" == '(work) plain%# ' ]] || {{ print -u2 -- "unexpected plain prompt: $PROMPT"; exit 1; }}
[[ -z "$RPROMPT" ]] || {{ print -u2 -- "unexpected plain right prompt: $RPROMPT"; exit 1; }}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["zsh", "-f", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_zsh_shell_init_installs_powerlevel10k_segment(tmp_path: Path) -> None:
    if shutil.which("zsh") is None:
        pytest.skip("zsh is not installed")

    script_path = tmp_path / "zsh-powerlevel10k.zsh"
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("zsh")
    script_path.write_text(
        f"""
set -e
PACKAGENT_BIN={shlex.quote(str(executable))}
PROMPT='theme%# '
RPROMPT='theme-right'
typeset -ga POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS
POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(status command_execution_time virtualenv pyenv)
typeset -gi PACKAGENT_TEST_P10K_RELOADS=0
p10k() {{
  case "$1" in
    reload)
      PACKAGENT_TEST_P10K_RELOADS=$((PACKAGENT_TEST_P10K_RELOADS + 1))
      return 0
      ;;
    segment)
      shift
      local text=''
      local foreground=''
      local background=''
      while (( $# )); do
        case "$1" in
          -f)
            foreground="$2"
            shift 2
            ;;
          -b)
            background="$2"
            shift 2
            ;;
          -t)
            text="$2"
            shift 2
            ;;
          *)
            shift
            ;;
        esac
      done
      print -rn -- "SEGMENT:$foreground:$background:$text"
      return 0
      ;;
  esac
}}
export PACKAGENT_ACTIVE_ENV=base
{hook}
[[ "${{(j: :)POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS}}" == 'packagent status command_execution_time virtualenv pyenv' ]] || {{
  print -u2 -- "unexpected elements: ${{(j: :)POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS}}"
  exit 1
}}
[[ "$PACKAGENT_PROMPT_NATIVE" == '1' ]] || {{ print -u2 -- "native prompt was not enabled"; exit 1; }}
[[ "$PACKAGENT_TEST_P10K_RELOADS" == '1' ]] || {{ print -u2 -- "unexpected reload count: $PACKAGENT_TEST_P10K_RELOADS"; exit 1; }}
[[ "$PROMPT" == 'theme%# ' ]] || {{ print -u2 -- "prompt was rewritten: $PROMPT"; exit 1; }}
[[ "$RPROMPT" == 'theme-right' ]] || {{ print -u2 -- "right prompt was rewritten: $RPROMPT"; exit 1; }}
[[ "$(prompt_packagent)" == 'SEGMENT:255:31:base pa' ]] || {{ print -u2 -- "unexpected segment output: $(prompt_packagent)"; exit 1; }}
PACKAGENT_POWERLEVEL_SUFFIX=''
export PACKAGENT_POWERLEVEL_SUFFIX
[[ "$(prompt_packagent)" == 'SEGMENT:255:31:base' ]] || {{ print -u2 -- "unexpected suffix override output: $(prompt_packagent)"; exit 1; }}
POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(status command_execution_time virtualenv pyenv)
{hook}
[[ "${{(j: :)POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS}}" == 'packagent status command_execution_time virtualenv pyenv' ]] || {{
  print -u2 -- "unexpected elements after resourcing: ${{(j: :)POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS}}"
  exit 1
}}
[[ "$PACKAGENT_TEST_P10K_RELOADS" == '2' ]] || {{ print -u2 -- "unexpected reload count after resourcing: $PACKAGENT_TEST_P10K_RELOADS"; exit 1; }}
_packagent_prompt_command
[[ "$PACKAGENT_TEST_P10K_RELOADS" == '2' ]] || {{ print -u2 -- "prompt refresh reloaded unnecessarily: $PACKAGENT_TEST_P10K_RELOADS"; exit 1; }}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["zsh", "-f", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_zsh_shell_init_installs_spaceship_section(tmp_path: Path) -> None:
    if shutil.which("zsh") is None:
        pytest.skip("zsh is not installed")

    script_path = tmp_path / "zsh-spaceship.zsh"
    executable = _write_active_env_packagent(tmp_path)
    hook = render_shell_init("zsh")
    script_path.write_text(
        f"""
set -e
PACKAGENT_BIN={shlex.quote(str(executable))}
PROMPT='theme%# '
RPROMPT=''
typeset -ga SPACESHIP_PROMPT_ORDER
typeset -ga SPACESHIP_RPROMPT_ORDER
SPACESHIP_PROMPT_ORDER=(dir git line_sep char)
SPACESHIP_RPROMPT_ORDER=()
SPACESHIP_PROMPT_DEFAULT_SUFFIX=' '
spaceship::section::v4() {{
  local color=''
  local symbol=''
  local content=''
  while (( $# )); do
    case "$1" in
      --color)
        color="$2"
        shift 2
        ;;
      --symbol)
        symbol="$2"
        shift 2
        ;;
      --prefix|--suffix)
        shift 2
        ;;
      *)
        content="$1"
        shift
        ;;
    esac
  done
  print -rn -- "SECTION:$color:$symbol:$content"
}}
export PACKAGENT_ACTIVE_ENV=base
{hook}
[[ "$PACKAGENT_PROMPT_NATIVE" == '1' ]] || {{ print -u2 -- "native prompt was not enabled"; exit 1; }}
[[ "$PACKAGENT_ZSH_NATIVE_PROMPT" == 'spaceship' ]] || {{ print -u2 -- "unexpected native prompt: $PACKAGENT_ZSH_NATIVE_PROMPT"; exit 1; }}
[[ "${{(j: :)SPACESHIP_PROMPT_ORDER}}" == 'dir git line_sep packagent char' ]] || {{
  print -u2 -- "unexpected prompt order: ${{(j: :)SPACESHIP_PROMPT_ORDER}}"
  exit 1
}}
[[ "$(spaceship_packagent)" == 'SECTION:cyan:pa :base' ]] || {{ print -u2 -- "unexpected section output: $(spaceship_packagent)"; exit 1; }}
SPACESHIP_PROMPT_ORDER=(dir char)
SPACESHIP_RPROMPT_ORDER=(exec_time)
PACKAGENT_ZSH_PROMPT_POSITION=right
export PACKAGENT_ZSH_PROMPT_POSITION
{hook}
[[ "${{(j: :)SPACESHIP_RPROMPT_ORDER}}" == 'packagent exec_time' ]] || {{
  print -u2 -- "unexpected rprompt order: ${{(j: :)SPACESHIP_RPROMPT_ORDER}}"
  exit 1
}}
[[ "${{(j: :)SPACESHIP_PROMPT_ORDER}}" == 'dir char' ]] || {{
  print -u2 -- "unexpected prompt order after right install: ${{(j: :)SPACESHIP_PROMPT_ORDER}}"
  exit 1
}}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["zsh", "-f", str(script_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_prompt_hook_syncs_marker_from_global_active_env(
    shell_name: str,
    tmp_path: Path,
) -> None:
    if shell_name == "zsh" and shutil.which("zsh") is None:
        pytest.skip("zsh is not installed")

    active_file = tmp_path / "active-env.txt"
    executable = tmp_path / "packagent"
    executable.write_text(
        f"""#!/usr/bin/env bash
if [ "$1" = "shell" ] && [ "${{2-}}" = "active-env" ]; then
  cat {shlex.quote(str(active_file))}
  exit 0
fi
exit 64
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script_path = tmp_path / f"sync-marker.{shell_name}"
    hook = render_shell_init(shell_name)

    if shell_name == "bash":
        script_path.write_text(
            f"""
set -euo pipefail
export PACKAGENT_BIN={shlex.quote(str(executable))}
PS1='prompt$ '
printf 'omx' > {shlex.quote(str(active_file))}
{hook}
_packagent_prompt_command
[ "$PS1" = '(omx) prompt$ ' ] || {{ echo "unexpected prompt after omx: $PS1" >&2; exit 1; }}
printf 'base' > {shlex.quote(str(active_file))}
_packagent_prompt_command
[ "$PS1" = '(base) prompt$ ' ] || {{ echo "unexpected prompt after base: $PS1" >&2; exit 1; }}
: > {shlex.quote(str(active_file))}
_packagent_prompt_command
[ "$PS1" = 'prompt$ ' ] || {{ echo "unexpected prompt after uninstall: $PS1" >&2; exit 1; }}
""",
            encoding="utf-8",
        )
        command = ["bash", str(script_path)]
    else:
        script_path.write_text(
            f"""
set -e
export PACKAGENT_BIN={shlex.quote(str(executable))}
PROMPT='prompt%# '
RPROMPT=''
printf 'omx' > {shlex.quote(str(active_file))}
{hook}
_packagent_prompt_command
[[ "$PROMPT" == '(omx) prompt%# ' ]] || {{ print -u2 -- "unexpected prompt after omx: $PROMPT"; exit 1; }}
printf 'base' > {shlex.quote(str(active_file))}
_packagent_prompt_command
[[ "$PROMPT" == '(base) prompt%# ' ]] || {{ print -u2 -- "unexpected prompt after base: $PROMPT"; exit 1; }}
: > {shlex.quote(str(active_file))}
_packagent_prompt_command
[[ "$PROMPT" == 'prompt%# ' ]] || {{ print -u2 -- "unexpected prompt after uninstall: $PROMPT"; exit 1; }}
""",
            encoding="utf-8",
        )
        command = ["zsh", "-f", str(script_path)]

    result = subprocess.run(command, capture_output=True, text=True, timeout=5)

    assert result.returncode == 0, result.stderr


def test_bash_uninstall_wrapper_clears_current_prompt_state(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "packagent"
    executable.write_text(
        """#!/usr/bin/env bash
if [ "$1" = "shell" ] && [ "${2-}" = "active-env" ]; then
  printf '%s\\n' "${PACKAGENT_ACTIVE_ENV-}"
  exit 0
fi
printf 'uninstalled\\n'
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script_path = tmp_path / "uninstall-wrapper.bash"
    output_path = tmp_path / "uninstall-output.txt"
    hook = render_shell_init("bash")
    script_path.write_text(
        f"""
set -euo pipefail
export PATH={shlex.quote(str(bin_dir))}:$PATH
export PACKAGENT_BIN={shlex.quote(str(executable))}
PS1='prompt$ '
{hook}
export PACKAGENT_ACTIVE_ENV=base
_packagent_refresh_prompt
case "$PS1" in
  "(base) "*) ;;
  *) echo "prompt was not prefixed before uninstall: $PS1" >&2; exit 1 ;;
esac
packagent uninstall --restore-source base > {shlex.quote(str(output_path))}
[ -z "${{PACKAGENT_ACTIVE_ENV-}}" ]
case "$PS1" in
  "(base) "*) echo "prompt prefix remained after uninstall: $PS1" >&2; exit 1 ;;
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
    assert output_path.read_text(encoding="utf-8") == "uninstalled\n"


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


@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_shell_rc_block_includes_executable_fallback(shell_name: str) -> None:
    block = render_shell_rc_block(shell_name)

    assert "_packagent_bin=packagent" in block
    assert '"$HOME/.local/bin/packagent"' in block
    assert "/home/" not in block
    assert f'shell init {shell_name})"' in block


@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_shell_rc_block_can_bootstrap_when_path_is_not_ready(shell_name: str, tmp_path: Path) -> None:
    if shell_name == "zsh" and shutil.which("zsh") is None:
        pytest.skip("zsh is not installed")

    executable = tmp_path / "packagent"
    executable.write_text(
        """#!/bin/sh
if [ "$1" = "shell" ] && [ "$2" = "init" ]; then
  printf "export PACKAGENT_TEST_BOOTSTRAPPED='%s'\\n" "$3"
  exit 0
fi
exit 64
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    local_bin.joinpath("packagent").symlink_to(executable)
    script_path = tmp_path / f"bootstrap.{shell_name}"
    script_path.write_text(
        f"""
set -eu
HOME={shlex.quote(str(home))}
PATH=/tmp/packagent-path-not-ready
{render_shell_rc_block(shell_name)}
[ "${{PACKAGENT_TEST_BOOTSTRAPPED-}}" = "{shell_name}" ]
[ -z "${{_packagent_bin+x}}" ]
""",
        encoding="utf-8",
    )
    command = ["bash", str(script_path)] if shell_name == "bash" else ["zsh", "-f", str(script_path)]

    result = subprocess.run(command, capture_output=True, text=True, timeout=5)

    assert result.returncode == 0, result.stderr


def test_install_shell_init_writes_managed_block_idempotently(tmp_path: Path) -> None:
    rc_path = tmp_path / ".bashrc"
    rc_path.write_text("export PATH=/tmp/bin:$PATH\n", encoding="utf-8")

    first = install_shell_init("bash", rc_path)
    second = install_shell_init("bash", rc_path)
    content = rc_path.read_text(encoding="utf-8")

    assert first.changed is True
    assert second.changed is False
    assert content.count("# >>> packagent initialize >>>") == 1
    assert "shell init bash" in content
    assert '"$HOME/.local/bin/packagent"' in content


def test_remove_shell_init_removes_managed_block_idempotently(tmp_path: Path) -> None:
    rc_path = tmp_path / ".bashrc"
    install_shell_init("bash", rc_path)
    with_existing_content = "export PATH=/tmp/bin:$PATH\n\n" + rc_path.read_text(encoding="utf-8")
    rc_path.write_text(with_existing_content, encoding="utf-8")

    first = remove_shell_init("bash", rc_path)
    second = remove_shell_init("bash", rc_path)
    content = rc_path.read_text(encoding="utf-8")

    assert first.changed is True
    assert second.changed is False
    assert "# >>> packagent initialize >>>" not in content
    assert "export PATH=/tmp/bin:$PATH" in content


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
    assert "export OPENCODE_CONFIG_DIR=" not in script


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
    assert "export OPENCODE_CONFIG_DIR=" not in activate_script
    assert "export PACKAGENT_ACTIVE_ENV='base'" in deactivate_script
    assert "export CODEX_HOME=" not in deactivate_script
    assert "export CLAUDE_CONFIG_DIR=" not in deactivate_script
    assert "export OPENCODE_CONFIG_DIR=" not in deactivate_script
