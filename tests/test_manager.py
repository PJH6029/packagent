from __future__ import annotations

from pathlib import Path

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
from packagent.paths import PackagentPaths


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PackagentManager:
    monkeypatch.setenv("HOME", str(tmp_path))
    return PackagentManager(paths=PackagentPaths.discover())


def provider_host(manager: PackagentManager, provider: str):
    return manager.hosts[provider]


def test_home_inspection_detects_missing_directory_and_managed_symlinks_for_all_providers(
    manager: PackagentManager,
) -> None:
    backend = GlobalSymlinkBackend()
    for provider in ("codex", "claude"):
        inspection = backend.inspect(manager.paths, provider_host(manager, provider))
        assert inspection.kind == HOME_KIND_MISSING

    manager.create_env("work")
    manager.activate_env("work")

    for provider in ("codex", "claude"):
        inspection = backend.inspect(manager.paths, provider_host(manager, provider))
        assert inspection.kind == HOME_KIND_MANAGED
        assert inspection.managed_env == "work"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_home_inspection_detects_unmanaged_directory(manager: PackagentManager, provider: str) -> None:
    host = provider_host(manager, provider)
    managed_home = host.managed_home_path(manager.paths)
    managed_home.mkdir(parents=True)
    (managed_home / "marker.txt").write_text("legacy", encoding="utf-8")

    inspection = manager.backend.inspect(manager.paths, host)
    assert inspection.kind == HOME_KIND_UNMANAGED_DIRECTORY


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_home_inspection_detects_unmanaged_symlink(manager: PackagentManager, provider: str) -> None:
    host = provider_host(manager, provider)
    source = manager.paths.home / f"legacy-{provider}"
    source.mkdir()
    managed_home = host.managed_home_path(manager.paths)
    managed_home.symlink_to(source)

    inspection = manager.backend.inspect(manager.paths, host)
    assert inspection.kind == HOME_KIND_UNMANAGED_SYMLINK


def test_remove_refuses_base_and_active_environment(manager: PackagentManager) -> None:
    manager.create_env("work")
    manager.activate_env("work")

    with pytest.raises(Exception):
        manager.remove_env("base")
    with pytest.raises(Exception):
        manager.remove_env("work")


def test_first_activation_imports_existing_homes_into_base_and_replaces_links(manager: PackagentManager) -> None:
    codex_host = provider_host(manager, "codex")
    claude_host = provider_host(manager, "claude")
    codex_home = codex_host.managed_home_path(manager.paths)
    claude_home = claude_host.managed_home_path(manager.paths)
    codex_home.mkdir(parents=True)
    claude_home.mkdir(parents=True)
    (codex_home / "AGENTS.md").write_text("legacy-codex", encoding="utf-8")
    (claude_home / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")

    result = manager.create_env("work", provider="claude")
    activation = manager.activate_env("work")

    assert result.provider == "claude"
    assert activation.provider == "claude"
    assert codex_home.is_symlink()
    assert claude_home.is_symlink()
    assert codex_home.resolve() == codex_host.env_home_path(manager.paths, "work")
    assert claude_home.resolve() == claude_host.env_home_path(manager.paths, "work")
    assert codex_host.env_home_path(manager.paths, "base").joinpath("AGENTS.md").read_text(encoding="utf-8") == "legacy-codex"
    assert (
        claude_host.env_home_path(manager.paths, "base").joinpath("settings.json").read_text(encoding="utf-8")
        == '{"theme":"dark"}'
    )
    assert len(sorted(manager.paths.backups_root.iterdir())) == 2


def test_activation_keeps_provider_writes_isolated(manager: PackagentManager) -> None:
    codex_host = provider_host(manager, "codex")
    claude_host = provider_host(manager, "claude")
    manager.create_env("env-a")
    manager.create_env("env-b", provider="claude")

    manager.activate_env("env-a")
    codex_home = codex_host.managed_home_path(manager.paths)
    claude_home = claude_host.managed_home_path(manager.paths)
    codex_home.joinpath("AGENTS.md").write_text("from-a", encoding="utf-8")
    claude_home.joinpath("settings.json").write_text('{"profile":"a"}', encoding="utf-8")

    manager.activate_env("env-b")

    assert codex_host.env_home_path(manager.paths, "env-a").joinpath("AGENTS.md").read_text(encoding="utf-8") == "from-a"
    assert (
        claude_host.env_home_path(manager.paths, "env-a").joinpath("settings.json").read_text(encoding="utf-8")
        == '{"profile":"a"}'
    )
    assert not codex_host.env_home_path(manager.paths, "env-b").joinpath("AGENTS.md").exists()
    assert not claude_host.env_home_path(manager.paths, "env-b").joinpath("settings.json").exists()
    assert codex_home.resolve() == codex_host.env_home_path(manager.paths, "env-b")
    assert claude_home.resolve() == claude_host.env_home_path(manager.paths, "env-b")


def test_deactivate_restores_base_for_all_providers(manager: PackagentManager) -> None:
    codex_host = provider_host(manager, "codex")
    claude_host = provider_host(manager, "claude")
    manager.status()
    base_codex = codex_host.env_home_path(manager.paths, "base")
    base_claude = claude_host.env_home_path(manager.paths, "base")
    base_codex.joinpath("config.toml").write_text("model = 'gpt-5-codex'\n", encoding="utf-8")
    base_claude.joinpath("settings.json").write_text('{"model":"sonnet"}', encoding="utf-8")
    manager.create_env("work", provider="claude")
    manager.activate_env("work")

    manager.deactivate_env()

    assert codex_host.managed_home_path(manager.paths).resolve() == base_codex
    assert claude_host.managed_home_path(manager.paths).resolve() == base_claude


@pytest.mark.parametrize(
    ("provider", "env_var", "custom_home"),
    [
        ("codex", "CODEX_HOME", ".config/codex-home"),
        ("claude", "CLAUDE_CONFIG_DIR", ".config/claude-home"),
    ],
)
def test_activation_uses_existing_provider_home_paths(
    manager: PackagentManager,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    env_var: str,
    custom_home: str,
) -> None:
    host = provider_host(manager, provider)
    custom_managed_home = manager.paths.home / custom_home
    monkeypatch.setenv(env_var, str(custom_managed_home))

    manager.create_env("work")
    manager.activate_env("work")

    assert custom_managed_home.is_symlink()
    assert custom_managed_home.resolve() == host.env_home_path(manager.paths, "work")


def test_create_clone_base_copies_all_provider_homes_and_can_override_provider(manager: PackagentManager) -> None:
    codex_host = provider_host(manager, "codex")
    claude_host = provider_host(manager, "claude")
    manager.status()
    codex_host.env_home_path(manager.paths, "base").joinpath("auth.json").write_text('{"token":"demo"}', encoding="utf-8")
    claude_host.env_home_path(manager.paths, "base").joinpath("settings.json").write_text('{"profile":"base"}', encoding="utf-8")

    copied = manager.create_env("copy-base", clone_from="base", provider="claude")
    inherited = manager.create_env("copy-copy", clone_from="copy-base")

    assert copied.provider == "claude"
    assert inherited.provider == "claude"
    assert codex_host.env_home_path(manager.paths, "copy-base").joinpath("auth.json").read_text(encoding="utf-8") == '{"token":"demo"}'
    assert (
        claude_host.env_home_path(manager.paths, "copy-base").joinpath("settings.json").read_text(encoding="utf-8")
        == '{"profile":"base"}'
    )


def test_doctor_detects_and_repairs_provider_symlink_drift(manager: PackagentManager) -> None:
    codex_host = provider_host(manager, "codex")
    claude_host = provider_host(manager, "claude")
    manager.create_env("env-a", provider="claude")
    manager.activate_env("env-a")

    codex_home = codex_host.managed_home_path(manager.paths)
    claude_home = claude_host.managed_home_path(manager.paths)
    codex_home.unlink()
    claude_home.unlink()
    codex_home.symlink_to(codex_host.env_home_path(manager.paths, "base"))
    claude_home.symlink_to(claude_host.env_home_path(manager.paths, "base"))

    report = manager.doctor()
    assert report.issues

    fixed = manager.doctor(fix=True)
    assert not fixed.issues
    assert codex_home.resolve() == codex_host.env_home_path(manager.paths, "env-a")
    assert claude_home.resolve() == claude_host.env_home_path(manager.paths, "env-a")


def test_harness_style_write_and_read_follow_the_active_homes(manager: PackagentManager) -> None:
    codex_host = provider_host(manager, "codex")
    claude_host = provider_host(manager, "claude")
    manager.create_env("work", provider="claude")
    manager.activate_env("work")

    active_codex_home = codex_host.managed_home_path(manager.paths)
    active_claude_home = claude_host.managed_home_path(manager.paths)
    skill_dir = active_codex_home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text("demo skill", encoding="utf-8")
    active_claude_home.joinpath("settings.json").write_text('{"mode":"cli"}', encoding="utf-8")

    assert active_codex_home.resolve() == codex_host.env_home_path(manager.paths, "work")
    assert active_claude_home.resolve() == claude_host.env_home_path(manager.paths, "work")
    assert active_codex_home.joinpath("skills", "demo", "SKILL.md").read_text(encoding="utf-8") == "demo skill"
    assert active_claude_home.joinpath("settings.json").read_text(encoding="utf-8") == '{"mode":"cli"}'


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
    assert "export PACKAGENT_ACTIVE_PROVIDER='codex'" in output
    assert "export CODEX_HOME=" not in output
    assert "export CLAUDE_CONFIG_DIR=" not in output


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
    manager.create_env("work", provider="claude")
    manager.activate_env("work")
    monkeypatch.setenv("PACKAGENT_SHELL_HOOK", "1")
    monkeypatch.setenv("PACKAGENT_SHELL", "bash")

    exit_code = main(["deactivate"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "export PACKAGENT_ACTIVE_ENV='base'" in output
    assert "export PACKAGENT_ACTIVE_PROVIDER='codex'" in output
    assert "export CODEX_HOME=" not in output
    assert "export CLAUDE_CONFIG_DIR=" not in output


def test_cli_list_and_status_are_script_friendly(manager: PackagentManager, capsys: pytest.CaptureFixture[str]) -> None:
    manager.create_env("work")
    manager.create_env("claude-work", provider="claude")

    list_code = main(["list"])
    list_output = capsys.readouterr().out
    assert list_code == 0
    assert "false\tcodex\twork\t" in list_output
    assert "false\tclaude\tclaude-work\t" in list_output

    status_code = main(["status"])
    status_output = capsys.readouterr().out
    assert status_code == 0
    assert "active_env=base" in status_output
    assert "provider=codex" in status_output
    assert "codex_managed_home=" in status_output
    assert "claude_managed_home=" in status_output
