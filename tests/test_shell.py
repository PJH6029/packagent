from __future__ import annotations

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
    assert 'PROMPT_COMMAND="_packagent_prompt_command"' in script
    assert 'PACKAGENT_SHELL=bash' in script


def test_zsh_shell_init_contains_wrapper_and_precmd_hook() -> None:
    script = render_shell_init("zsh")
    assert 'packagent() {' in script
    assert "precmd_functions+=(_packagent_refresh_prompt)" in script
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
        codex_home="/tmp/home/.packagent-v1/envs/base/.codex",
    )
    script = render_shell_init("bash", result)

    assert "export PACKAGENT_ACTIVE_ENV='base'" in script
    assert "export CODEX_HOME=" not in script


def test_activate_and_deactivate_shell_commands_are_export_friendly() -> None:
    result = ActivationResult(
        env_name="work",
        managed_home_path="/tmp/home/.codex",
        codex_home="/tmp/home/.packagent-v1/envs/work/.codex",
    )
    activate_script = render_activate_commands("zsh", result)
    deactivate_result = ActivationResult(
        env_name="base",
        managed_home_path="/tmp/home/.codex",
        codex_home="/tmp/home/.packagent-v1/envs/base/.codex",
    )
    deactivate_script = render_deactivate_commands("zsh", deactivate_result)

    assert "export PACKAGENT_ACTIVE_ENV='work'" in activate_script
    assert "export CODEX_HOME=" not in activate_script
    assert "export PACKAGENT_ACTIVE_ENV='base'" in deactivate_script
    assert "export CODEX_HOME=" not in deactivate_script
