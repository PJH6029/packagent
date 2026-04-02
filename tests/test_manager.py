from __future__ import annotations

from pathlib import Path
import os

import pytest

from packagent.activation import (
    GlobalSymlinkBackend,
    HOME_KIND_MANAGED,
    HOME_KIND_MISSING,
    HOME_KIND_UNMANAGED_DIRECTORY,
    HOME_KIND_UNMANAGED_SYMLINK,
)
from packagent.app import PackagentManager
from packagent.cli import main
from packagent.hosts import CodexHost
from packagent.paths import PackagentPaths


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PackagentManager:
    monkeypatch.setenv("HOME", str(tmp_path))
    return PackagentManager(paths=PackagentPaths.discover())


def test_home_inspection_detects_missing_directory_and_managed_symlink(manager: PackagentManager) -> None:
    backend = GlobalSymlinkBackend()
    inspection = backend.inspect(manager.paths, CodexHost())
    assert inspection.kind == HOME_KIND_MISSING

    manager.create_env("work")
    manager.activate_env("work")
    inspection = backend.inspect(manager.paths, CodexHost())
    assert inspection.kind == HOME_KIND_MANAGED
    assert inspection.managed_env == "work"


def test_home_inspection_detects_unmanaged_directory(manager: PackagentManager) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir(parents=True)
    (codex_home / "AGENTS.md").write_text("legacy", encoding="utf-8")

    inspection = manager.backend.inspect(manager.paths, manager.host)
    assert inspection.kind == HOME_KIND_UNMANAGED_DIRECTORY


def test_home_inspection_detects_unmanaged_symlink(manager: PackagentManager) -> None:
    source = manager.paths.home / "legacy-codex"
    source.mkdir()
    home = manager.host.managed_home_path(manager.paths)
    home.symlink_to(source)

    inspection = manager.backend.inspect(manager.paths, manager.host)
    assert inspection.kind == HOME_KIND_UNMANAGED_SYMLINK


def test_remove_refuses_base_and_active_environment(manager: PackagentManager) -> None:
    manager.create_env("work")
    manager.activate_env("work")

    with pytest.raises(Exception):
        manager.remove_env("base")
    with pytest.raises(Exception):
        manager.remove_env("work")


def test_first_activation_imports_existing_home_into_base_and_replaces_link(manager: PackagentManager) -> None:
    legacy_home = manager.host.managed_home_path(manager.paths)
    legacy_home.mkdir(parents=True)
    (legacy_home / "AGENTS.md").write_text("legacy-agents", encoding="utf-8")

    manager.create_env("work")
    result = manager.activate_env("work")

    assert Path(result.managed_home_path).is_symlink()
    assert Path(result.managed_home_path).resolve() == manager.host.env_home_path(manager.paths, "work")
    assert manager.host.env_home_path(manager.paths, "base").joinpath("AGENTS.md").read_text(encoding="utf-8") == "legacy-agents"
    backups = sorted(manager.paths.backups_root.iterdir())
    assert backups


def test_activation_keeps_env_writes_isolated(manager: PackagentManager) -> None:
    manager.create_env("env-a")
    manager.create_env("env-b")

    manager.activate_env("env-a")
    managed_home = manager.host.managed_home_path(manager.paths)
    managed_home.joinpath("AGENTS.md").write_text("from-a", encoding="utf-8")
    managed_home.joinpath("skills").mkdir()
    managed_home.joinpath("skills", "demo.txt").write_text("skill-a", encoding="utf-8")

    manager.activate_env("env-b")

    assert manager.host.env_home_path(manager.paths, "env-a").joinpath("AGENTS.md").read_text(encoding="utf-8") == "from-a"
    assert not manager.host.env_home_path(manager.paths, "env-b").joinpath("AGENTS.md").exists()
    assert manager.host.managed_home_path(manager.paths).resolve() == manager.host.env_home_path(manager.paths, "env-b")


def test_deactivate_restores_base(manager: PackagentManager) -> None:
    manager.status()
    base_home = manager.host.env_home_path(manager.paths, "base")
    base_home.joinpath("config.toml").write_text("model = 'gpt-5-codex'\n", encoding="utf-8")
    manager.create_env("work")
    manager.activate_env("work")

    manager.deactivate_env()

    assert manager.host.managed_home_path(manager.paths).resolve() == base_home


def test_create_clone_base_copies_home_contents(manager: PackagentManager) -> None:
    manager.status()
    base_home = manager.host.env_home_path(manager.paths, "base")
    base_home.joinpath("auth.json").write_text('{"token":"demo"}', encoding="utf-8")

    manager.create_env("copy-base", clone_from="base")

    cloned = manager.host.env_home_path(manager.paths, "copy-base")
    assert cloned.joinpath("auth.json").read_text(encoding="utf-8") == '{"token":"demo"}'


def test_doctor_detects_and_repairs_symlink_drift(manager: PackagentManager) -> None:
    manager.create_env("env-a")
    manager.activate_env("env-a")

    home = manager.host.managed_home_path(manager.paths)
    home.unlink()
    home.symlink_to(manager.host.env_home_path(manager.paths, "base"))

    report = manager.doctor()
    assert report.issues

    fixed = manager.doctor(fix=True)
    assert not fixed.issues
    assert home.resolve() == manager.host.env_home_path(manager.paths, "env-a")


def test_harness_style_write_and_read_follow_the_active_home(manager: PackagentManager) -> None:
    manager.create_env("work")
    manager.activate_env("work")

    active_home = manager.host.managed_home_path(manager.paths)
    skill_dir = active_home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("demo skill", encoding="utf-8")

    assert active_home.resolve() == manager.host.env_home_path(manager.paths, "work")
    assert active_home.joinpath("skills", "demo", "SKILL.md").read_text(encoding="utf-8") == "demo skill"


def test_cli_activate_requires_shell_hook(manager: PackagentManager, capsys: pytest.CaptureFixture[str]) -> None:
    manager.create_env("work")

    exit_code = main(["activate", "work"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "shell init" in captured.err


def test_cli_shell_init_bootstraps_base_prompt_state(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["shell", "init", "bash"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "export PACKAGENT_ACTIVE_ENV='base'" in output
    assert f"export CODEX_HOME='{manager.host.env_home_path(manager.paths, 'base')}'" in output


def test_cli_init_writes_detected_shell_rc_file(
    manager: PackagentManager,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr("packagent.shell._detect_shell_from_process_tree", lambda: "")

    exit_code = main(["init"])
    output = capsys.readouterr().out
    rc_path = manager.paths.home / ".bashrc"

    assert exit_code == 0
    assert rc_path.exists()
    assert 'eval "$(packagent shell init bash)"' in rc_path.read_text(encoding="utf-8")
    assert f"initialized\tbash\t{rc_path}\tupdated" in output
    assert 'run_now\teval "$(packagent shell init bash)"' in output


def test_cli_init_can_target_explicit_rc_file(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc_path = manager.paths.home / ".config" / "packagent-test-zshrc"

    exit_code = main(["init", "--shell", "zsh", "--rc-file", str(rc_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert 'eval "$(packagent shell init zsh)"' in rc_path.read_text(encoding="utf-8")
    assert f"initialized\tzsh\t{rc_path}\tupdated" in output


def test_cli_deactivate_emits_base_activation_commands(
    manager: PackagentManager,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager.create_env("work")
    manager.activate_env("work")
    monkeypatch.setenv("PACKAGENT_SHELL_HOOK", "1")
    monkeypatch.setenv("PACKAGENT_SHELL", "bash")

    exit_code = main(["deactivate"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "export PACKAGENT_ACTIVE_ENV='base'" in output
    assert f"export CODEX_HOME='{manager.host.env_home_path(manager.paths, 'base')}'" in output


def test_cli_list_and_status_are_script_friendly(manager: PackagentManager, capsys: pytest.CaptureFixture[str]) -> None:
    manager.create_env("work")

    list_code = main(["list"])
    list_output = capsys.readouterr().out
    assert list_code == 0
    assert "false\twork\t" in list_output

    status_code = main(["status"])
    status_output = capsys.readouterr().out
    assert status_code == 0
    assert "active_env=base" in status_output
