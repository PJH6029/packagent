from __future__ import annotations

from packagent.models import ActivationResult
from packagent.shell import render_activate_commands, render_deactivate_commands, render_shell_init


def test_bash_shell_init_contains_wrapper_and_prompt_hook() -> None:
    script = render_shell_init("bash")
    assert 'packagent() {' in script
    assert 'PROMPT_COMMAND="_packagent_prompt_command"' in script
    assert 'PACKAGENT_SHELL=bash' in script


def test_zsh_shell_init_contains_wrapper_and_precmd_hook() -> None:
    script = render_shell_init("zsh")
    assert 'packagent() {' in script
    assert "precmd_functions+=(_packagent_refresh_prompt)" in script
    assert 'PACKAGENT_SHELL=zsh' in script


def test_activate_and_deactivate_shell_commands_are_export_friendly() -> None:
    result = ActivationResult(
        env_name="work",
        managed_home_path="/tmp/home/.codex",
        codex_home="/tmp/home/.packagent-v1/envs/work/.codex",
    )
    activate_script = render_activate_commands("zsh", result)
    deactivate_script = render_deactivate_commands("zsh")

    assert "export PACKAGENT_ACTIVE_ENV='work'" in activate_script
    assert "export CODEX_HOME='/tmp/home/.packagent-v1/envs/work/.codex'" in activate_script
    assert "unset PACKAGENT_ACTIVE_ENV" in deactivate_script
    assert "unset CODEX_HOME" in deactivate_script

