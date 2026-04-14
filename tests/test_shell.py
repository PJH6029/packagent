from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from packagent.models import ActivationResult
from packagent.shell import (
    default_rc_path,
    detect_shell,
    install_shell_init,
    remove_shell_init,
    render_activate_commands,
    render_deactivate_commands,
    render_shell_init,
)


def test_bash_shell_init_contains_wrapper_and_prompt_hook() -> None:
    script = render_shell_init("bash")
    assert 'packagent() {' in script
    assert 'PROMPT_COMMAND="_packagent_prompt_command"' in script
    assert "PACKAGENT_ORIGINAL_PROMPT_COMMAND" in script
    assert 'PACKAGENT_SHELL=bash' in script
    assert '[ "$1" = "uninstall" ]' in script


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
[ "${{PROMPT_COMMAND-}}" = "_packagent_prompt_command" ]
[ "${{PACKAGENT_ORIGINAL_PROMPT_COMMAND-}}" = 'printf original >> "$PACKAGENT_TEST_PROMPT_OUTPUT"' ]
PACKAGENT_ACTIVE_ENV=base
_packagent_prompt_command
case "$PS1" in
  "(base) "*) ;;
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
    assert prompt_output.read_text(encoding="utf-8") == "original"


def test_zsh_shell_init_contains_wrapper_and_precmd_hook() -> None:
    script = render_shell_init("zsh")
    assert 'packagent() {' in script
    assert "precmd_functions+=(_packagent_refresh_prompt)" in script
    assert 'PACKAGENT_SHELL=zsh' in script
    assert '[[ "$1" == "uninstall" ]]' in script


def test_bash_uninstall_wrapper_clears_current_prompt_state(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "packagent"
    executable.write_text(
        "#!/usr/bin/env bash\nprintf 'uninstalled\\n'\n",
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
PS1='prompt$ '
{hook}
PACKAGENT_ACTIVE_ENV=base
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
