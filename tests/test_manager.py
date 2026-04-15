from __future__ import annotations

import io
import json
from pathlib import Path
import os
import shutil
import sys

import pytest

from packagent.activation import (
    GlobalSymlinkBackend,
    HOME_KIND_BROKEN_MANAGED,
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


def test_paths_discover_uses_packagent_root(tmp_path: Path) -> None:
    paths = PackagentPaths.discover(home=tmp_path)

    assert paths.root == tmp_path / ".packagent"
    assert paths.env_dir("work") == tmp_path / ".packagent" / "envs" / "work"
    assert paths.backups_root == tmp_path / ".packagent-backups"
    assert paths.backups_root.parent == paths.root.parent


def test_home_inspection_detects_missing_directory_and_managed_symlink(manager: PackagentManager) -> None:
    backend = GlobalSymlinkBackend()
    inspection = backend.inspect(manager.paths, CodexHost())
    assert inspection.kind == HOME_KIND_MISSING

    manager.create_env("work")
    manager.activate_env("work")
    inspection = backend.inspect(manager.paths, CodexHost())
    assert inspection.kind == HOME_KIND_MANAGED
    assert inspection.managed_env == "work"


@pytest.mark.parametrize("target_key", ["agents-home", "claude-home"])
def test_companion_target_inspection_detects_managed_symlink(
    manager: PackagentManager,
    target_key: str,
) -> None:
    backend = GlobalSymlinkBackend()
    host = CodexHost()
    target = host.target_by_key(target_key)

    inspection = backend.inspect(manager.paths, host, target)
    assert inspection.kind == HOME_KIND_MISSING

    manager.create_env("work")
    manager.activate_env("work")
    inspection = backend.inspect(manager.paths, host, target)

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
    assert backups[0].parent == manager.paths.home / ".packagent-backups"


def test_first_activation_preserves_symlinks_when_importing_home(manager: PackagentManager) -> None:
    legacy_home = manager.host.managed_home_path(manager.paths)
    legacy_home.joinpath("tmp").mkdir(parents=True)
    legacy_home.joinpath("tmp", "dangling").symlink_to(legacy_home / "missing-tool")

    manager.create_env("work")
    manager.activate_env("work")

    imported_link = manager.host.env_home_path(manager.paths, "base") / "tmp" / "dangling"
    assert imported_link.is_symlink()
    assert os.readlink(imported_link) == str(legacy_home / "missing-tool")


def test_initialize_base_symlink_backup_uses_target_name(manager: PackagentManager) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    source_home = manager.paths.home / "external-codex"
    source_home.mkdir()
    source_home.joinpath("auth.json").write_text("codex", encoding="utf-8")
    codex_home.symlink_to(source_home)

    manager.initialize_base("import")

    state = manager.load_state()
    codex_record = next(record for record in state.backups if record.target_key == "codex-home")
    backup_root = Path(codex_record.backup_path)
    assert backup_root.joinpath(".codex", "auth.json").read_text(encoding="utf-8") == "codex"
    assert backup_root.joinpath(".codex.symlink.json").exists()
    assert not backup_root.joinpath("resolved-home").exists()
    assert manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").read_text(encoding="utf-8") == "codex"


@pytest.mark.parametrize(
    ("target_key", "legacy_parts", "content"),
    [
        ("agents-home", ("skills", "demo", "SKILL.md"), "legacy-skill"),
        ("claude-home", ("settings.json",), '{"theme":"legacy"}'),
    ],
)
def test_first_activation_imports_existing_companion_home_into_base_and_replaces_link(
    manager: PackagentManager,
    target_key: str,
    legacy_parts: tuple[str, ...],
    content: str,
) -> None:
    target = manager.host.target_by_key(target_key)
    legacy_home = manager.host.managed_target_path(manager.paths, target)
    legacy_file = legacy_home.joinpath(*legacy_parts)
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(content, encoding="utf-8")

    manager.create_env("work")
    manager.activate_env("work")

    base_home = manager.host.env_target_path(manager.paths, "base", target)
    work_home = manager.host.env_target_path(manager.paths, "work", target)
    assert legacy_home.is_symlink()
    assert legacy_home.resolve() == work_home
    assert base_home.joinpath(*legacy_parts).read_text(encoding="utf-8") == content


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


def test_activation_keeps_all_target_writes_isolated(manager: PackagentManager) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    claude_target = manager.host.target_by_key("claude-home")
    manager.create_env("env-a")
    manager.create_env("env-b")

    manager.activate_env("env-a")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    claude_home = manager.host.managed_target_path(manager.paths, claude_target)
    codex_home.joinpath("AGENTS.md").write_text("from-a", encoding="utf-8")
    agents_home.joinpath("skills").mkdir()
    agents_home.joinpath("skills", "demo.txt").write_text("skill-a", encoding="utf-8")
    claude_home.joinpath("settings.json").write_text('{"env":"a"}', encoding="utf-8")

    manager.activate_env("env-b")

    assert manager.host.env_home_path(manager.paths, "env-a").joinpath("AGENTS.md").read_text(encoding="utf-8") == "from-a"
    assert manager.host.env_target_path(manager.paths, "env-a", agents_target).joinpath("skills", "demo.txt").read_text(encoding="utf-8") == "skill-a"
    assert manager.host.env_target_path(manager.paths, "env-a", claude_target).joinpath("settings.json").read_text(encoding="utf-8") == '{"env":"a"}'
    assert not manager.host.env_home_path(manager.paths, "env-b").joinpath("AGENTS.md").exists()
    assert not manager.host.env_target_path(manager.paths, "env-b", agents_target).joinpath("skills", "demo.txt").exists()
    assert not manager.host.env_target_path(manager.paths, "env-b", claude_target).joinpath("settings.json").exists()
    assert codex_home.resolve() == manager.host.env_home_path(manager.paths, "env-b")
    assert agents_home.resolve() == manager.host.env_target_path(manager.paths, "env-b", agents_target)
    assert claude_home.resolve() == manager.host.env_target_path(manager.paths, "env-b", claude_target)


def test_deactivate_restores_base(manager: PackagentManager) -> None:
    manager.status()
    base_home = manager.host.env_home_path(manager.paths, "base")
    base_home.joinpath("config.toml").write_text("model = 'gpt-5-codex'\n", encoding="utf-8")
    manager.create_env("work")
    manager.activate_env("work")

    manager.deactivate_env()

    assert manager.host.managed_home_path(manager.paths).resolve() == base_home


def test_create_seeds_only_shared_auth_files_from_base(manager: PackagentManager) -> None:
    claude_target = manager.host.target_by_key("claude-home")
    manager.status()
    base_codex = manager.host.env_home_path(manager.paths, "base")
    base_claude = manager.host.env_target_path(manager.paths, "base", claude_target)
    base_codex.joinpath("auth.json").write_text('{"token":"codex"}', encoding="utf-8")
    base_codex.joinpath("history.jsonl").write_text("history\n", encoding="utf-8")
    base_codex.joinpath("config.toml").write_text("model = 'demo'\n", encoding="utf-8")
    base_codex.joinpath("log").mkdir()
    base_codex.joinpath("log", "codex-tui.log").write_text("log\n", encoding="utf-8")
    base_codex.joinpath("sessions").mkdir()
    base_codex.joinpath("sessions", "session.jsonl").write_text("session\n", encoding="utf-8")
    base_claude.joinpath(".credentials.json").write_text('{"token":"claude"}', encoding="utf-8")
    base_claude.joinpath("settings.json").write_text('{"theme":"demo"}', encoding="utf-8")

    manager.create_env("work")

    work_codex = manager.host.env_home_path(manager.paths, "work")
    work_claude = manager.host.env_target_path(manager.paths, "work", claude_target)
    assert work_codex.joinpath("auth.json").read_text(encoding="utf-8") == '{"token":"codex"}'
    assert work_claude.joinpath(".credentials.json").read_text(encoding="utf-8") == '{"token":"claude"}'
    assert not work_codex.joinpath("history.jsonl").exists()
    assert not work_codex.joinpath("config.toml").exists()
    assert not work_codex.joinpath("log").exists()
    assert not work_codex.joinpath("sessions").exists()
    assert not work_claude.joinpath("settings.json").exists()


def test_create_seeds_shared_auth_files_from_active_env(manager: PackagentManager) -> None:
    claude_target = manager.host.target_by_key("claude-home")
    manager.create_env("env-a")
    manager.activate_env("env-a")
    manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").write_text("base", encoding="utf-8")
    env_a_codex = manager.host.env_home_path(manager.paths, "env-a")
    env_a_claude = manager.host.env_target_path(manager.paths, "env-a", claude_target)
    env_a_codex.joinpath("auth.json").write_text("env-a", encoding="utf-8")
    env_a_claude.joinpath(".credentials.json").write_text("env-a-claude", encoding="utf-8")

    manager.create_env("env-b")

    assert manager.host.env_home_path(manager.paths, "env-b").joinpath("auth.json").read_text(encoding="utf-8") == "env-a"
    assert manager.host.env_target_path(manager.paths, "env-b", claude_target).joinpath(".credentials.json").read_text(encoding="utf-8") == "env-a-claude"


def test_create_skips_shared_auth_symlinks(manager: PackagentManager) -> None:
    manager.status()
    base_home = manager.host.env_home_path(manager.paths, "base")
    secret = manager.paths.home / "outside-auth.json"
    secret.write_text("secret", encoding="utf-8")
    base_home.joinpath("auth.json").symlink_to(secret)

    manager.create_env("work")

    assert not manager.host.env_home_path(manager.paths, "work").joinpath("auth.json").exists()


def test_activation_uses_existing_codex_home_path(manager: PackagentManager, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_home = manager.paths.home / ".config" / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(custom_home))

    manager.create_env("work")
    result = manager.activate_env("work")

    assert Path(result.managed_home_path) == custom_home
    assert custom_home.is_symlink()
    assert custom_home.resolve() == manager.host.env_home_path(manager.paths, "work")


def test_custom_codex_home_does_not_move_agents_home(manager: PackagentManager, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_home = manager.paths.home / ".config" / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(custom_home))
    agents_target = manager.host.target_by_key("agents-home")
    claude_target = manager.host.target_by_key("claude-home")

    manager.create_env("work")
    manager.activate_env("work")

    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    assert agents_home == manager.paths.home / ".agents"
    assert agents_home.is_symlink()
    assert agents_home.resolve() == manager.host.env_target_path(manager.paths, "work", agents_target)
    claude_home = manager.host.managed_target_path(manager.paths, claude_target)
    assert claude_home == manager.paths.home / ".claude"
    assert claude_home.is_symlink()
    assert claude_home.resolve() == manager.host.env_target_path(manager.paths, "work", claude_target)


def test_activation_uses_existing_claude_config_dir(manager: PackagentManager, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_home = manager.paths.home / ".config" / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom_home))
    claude_target = manager.host.target_by_key("claude-home")

    manager.create_env("work")
    manager.activate_env("work")

    assert manager.host.managed_target_path(manager.paths, claude_target) == custom_home
    assert custom_home.is_symlink()
    assert custom_home.resolve() == manager.host.env_target_path(manager.paths, "work", claude_target)


def test_create_clone_base_copies_home_contents(manager: PackagentManager) -> None:
    manager.status()
    base_home = manager.host.env_home_path(manager.paths, "base")
    base_home.joinpath("auth.json").write_text('{"token":"demo"}', encoding="utf-8")

    manager.create_env("copy-base", clone_from="base")

    cloned = manager.host.env_home_path(manager.paths, "copy-base")
    assert cloned.joinpath("auth.json").read_text(encoding="utf-8") == '{"token":"demo"}'


def test_create_clone_base_copies_all_target_contents(manager: PackagentManager) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    claude_target = manager.host.target_by_key("claude-home")
    manager.status()
    manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").write_text("codex", encoding="utf-8")
    base_agents = manager.host.env_target_path(manager.paths, "base", agents_target)
    base_agents.joinpath("skills").mkdir()
    base_agents.joinpath("skills", "demo.txt").write_text("agents", encoding="utf-8")
    base_claude = manager.host.env_target_path(manager.paths, "base", claude_target)
    base_claude.joinpath("settings.json").write_text("claude", encoding="utf-8")

    manager.create_env("copy-base", clone_from="base")

    assert manager.host.env_home_path(manager.paths, "copy-base").joinpath("auth.json").read_text(encoding="utf-8") == "codex"
    assert manager.host.env_target_path(manager.paths, "copy-base", agents_target).joinpath("skills", "demo.txt").read_text(encoding="utf-8") == "agents"
    assert manager.host.env_target_path(manager.paths, "copy-base", claude_target).joinpath("settings.json").read_text(encoding="utf-8") == "claude"


def test_initialize_base_imports_existing_targets_and_activates_base(manager: PackagentManager) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    claude_target = manager.host.target_by_key("claude-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    claude_home = manager.host.managed_target_path(manager.paths, claude_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")
    agents_home.joinpath("skills").mkdir(parents=True)
    agents_home.joinpath("skills", "demo.txt").write_text("agents", encoding="utf-8")
    claude_home.mkdir()
    claude_home.joinpath(".credentials.json").write_text("claude", encoding="utf-8")

    result = manager.initialize_base("import")

    assert result.env_name == "base"
    assert codex_home.is_symlink()
    assert agents_home.is_symlink()
    assert claude_home.is_symlink()
    assert codex_home.resolve() == manager.host.env_home_path(manager.paths, "base")
    assert manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").read_text(encoding="utf-8") == "codex"
    assert manager.host.env_target_path(manager.paths, "base", agents_target).joinpath("skills", "demo.txt").read_text(encoding="utf-8") == "agents"
    assert manager.host.env_target_path(manager.paths, "base", claude_target).joinpath(".credentials.json").read_text(encoding="utf-8") == "claude"
    state = manager.load_state()
    backup_roots = {record.backup_path for record in state.backups}
    assert len(backup_roots) == 1
    backup_root = Path(next(iter(backup_roots)))
    assert backup_root.parent == manager.paths.backups_root
    assert backup_root.joinpath(".codex", "auth.json").read_text(encoding="utf-8") == "codex"
    assert backup_root.joinpath(".agents", "skills", "demo.txt").read_text(encoding="utf-8") == "agents"
    assert backup_root.joinpath(".claude", ".credentials.json").read_text(encoding="utf-8") == "claude"
    assert state.envs["base"].imported_from == str(backup_root)
    assert manager.load_state().active_env == "base"


def test_initialize_base_fresh_backs_up_existing_targets_without_importing(
    manager: PackagentManager,
) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    claude_target = manager.host.target_by_key("claude-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    claude_home = manager.host.managed_target_path(manager.paths, claude_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")
    codex_home.joinpath("history.jsonl").write_text("history", encoding="utf-8")
    agents_home.joinpath("skills").mkdir(parents=True)
    agents_home.joinpath("skills", "demo.txt").write_text("agents", encoding="utf-8")
    claude_home.mkdir()
    claude_home.joinpath(".credentials.json").write_text("claude", encoding="utf-8")
    claude_home.joinpath("settings.json").write_text("settings", encoding="utf-8")

    result = manager.initialize_base("fresh")

    base_codex = manager.host.env_home_path(manager.paths, "base")
    base_agents = manager.host.env_target_path(manager.paths, "base", agents_target)
    base_claude = manager.host.env_target_path(manager.paths, "base", claude_target)
    assert result.env_name == "base"
    assert codex_home.is_symlink()
    assert agents_home.is_symlink()
    assert claude_home.is_symlink()
    assert codex_home.resolve() == base_codex
    assert not base_codex.joinpath("auth.json").exists()
    assert not base_codex.joinpath("history.jsonl").exists()
    assert not base_agents.joinpath("skills", "demo.txt").exists()
    assert not base_claude.joinpath(".credentials.json").exists()
    assert not base_claude.joinpath("settings.json").exists()
    backup_roots = {record.backup_path for record in manager.load_state().backups}
    assert len(backup_roots) == 1
    backup_root = Path(next(iter(backup_roots)))
    assert backup_root.parent == manager.paths.backups_root
    assert backup_root.joinpath(".codex", "auth.json").read_text(encoding="utf-8") == "codex"
    assert backup_root.joinpath(".agents", "skills", "demo.txt").read_text(encoding="utf-8") == "agents"
    assert backup_root.joinpath(".claude", ".credentials.json").read_text(encoding="utf-8") == "claude"
    assert any(path.name == "auth.json" for path in manager.paths.backups_root.rglob("*"))
    assert any(path.name == ".credentials.json" for path in manager.paths.backups_root.rglob("*"))
    assert {record.reason for record in manager.load_state().backups} == {"fresh_base_directory"}
    assert manager.load_state().init_base_mode == "fresh"


def test_initialize_base_retries_if_unmanaged_home_appears_before_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LateUnmanagedHomeBackend(GlobalSymlinkBackend):
        def __init__(self) -> None:
            self.created_late_home = False

        def activate(self, paths, host, env_name, target=None):  # type: ignore[no-untyped-def]
            resolved_target = target or host.primary_target()
            if not self.created_late_home and resolved_target.key == "codex-home":
                self.created_late_home = True
                home = host.managed_target_path(paths, resolved_target)
                home.mkdir(parents=True)
                home.joinpath("late.txt").write_text("late", encoding="utf-8")
            return super().activate(paths, host, env_name, target)

    monkeypatch.setenv("HOME", str(tmp_path))
    manager = PackagentManager(
        paths=PackagentPaths.discover(),
        backend=LateUnmanagedHomeBackend(),
    )
    agents_target = manager.host.target_by_key("agents-home")
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    agents_home.joinpath("skills").mkdir(parents=True)
    agents_home.joinpath("skills", "demo.txt").write_text("agents", encoding="utf-8")

    result = manager.initialize_base("import")

    codex_home = manager.host.managed_home_path(manager.paths)
    backup_roots = {record.backup_path for record in manager.load_state().backups}
    backup_root = Path(next(iter(backup_roots)))
    assert len(backup_roots) == 1
    assert result.env_name == "base"
    assert codex_home.is_symlink()
    assert codex_home.resolve() == manager.host.env_home_path(manager.paths, "base")
    assert backup_root.joinpath(".agents", "skills", "demo.txt").read_text(encoding="utf-8") == "agents"
    assert backup_root.joinpath(".codex", "late.txt").read_text(encoding="utf-8") == "late"
    assert manager.host.env_home_path(manager.paths, "base").joinpath("late.txt").read_text(encoding="utf-8") == "late"


def test_uninstall_import_mode_can_restore_from_base(manager: PackagentManager) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    claude_target = manager.host.target_by_key("claude-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    claude_home = manager.host.managed_target_path(manager.paths, claude_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    agents_home.joinpath("skills").mkdir(parents=True)
    agents_home.joinpath("skills", "demo.txt").write_text("agents-original", encoding="utf-8")
    claude_home.mkdir()
    claude_home.joinpath("settings.json").write_text("claude-original", encoding="utf-8")

    manager.initialize_base("import")
    manager.host.env_home_path(manager.paths, "base").joinpath("base-only.txt").write_text("base", encoding="utf-8")

    result = manager.uninstall("base")

    assert result.restore_source == "base"
    assert not codex_home.is_symlink()
    assert not agents_home.is_symlink()
    assert not claude_home.is_symlink()
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"
    assert codex_home.joinpath("base-only.txt").read_text(encoding="utf-8") == "base"
    assert agents_home.joinpath("skills", "demo.txt").read_text(encoding="utf-8") == "agents-original"
    assert claude_home.joinpath("settings.json").read_text(encoding="utf-8") == "claude-original"
    assert manager.paths.root.exists()
    assert manager.load_state().last_link_target is None


def test_initialize_base_can_reinit_after_uninstall_restore_from_base(manager: PackagentManager) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    manager.initialize_base("import")
    manager.host.env_home_path(manager.paths, "base").joinpath("base-only.txt").write_text("base", encoding="utf-8")

    manager.uninstall("base")
    result = manager.initialize_base("import")

    assert result.env_name == "base"
    assert codex_home.is_symlink()
    assert codex_home.resolve() == manager.host.env_home_path(manager.paths, "base")
    assert manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"
    assert manager.host.env_home_path(manager.paths, "base").joinpath("base-only.txt").read_text(encoding="utf-8") == "base"


def test_uninstall_backup_after_reinit_uses_current_backup_root(manager: PackagentManager) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("first-original", encoding="utf-8")
    manager.initialize_base("import")
    manager.create_env("omx", clone_from="base")
    first_backup_root = Path(manager.load_state().current_backup_root or "")

    manager.uninstall("base")
    codex_home.joinpath("auth.json").write_text("second-original", encoding="utf-8")
    manager.initialize_base("import")
    second_backup_root = Path(manager.load_state().current_backup_root or "")
    shutil.rmtree(first_backup_root)
    manager.activate_env("omx")

    result = manager.uninstall("backup")

    assert result.restore_source == "backup"
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "second-original"
    codex_result = next(item for item in result.target_results if item.key == "codex-home")
    assert codex_result.source_path == str(second_backup_root / ".codex")


def test_uninstall_backup_after_reinit_does_not_resurrect_old_missing_target(
    manager: PackagentManager,
) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    agents_home.joinpath("skills").mkdir(parents=True)
    agents_home.joinpath("skills", "old.txt").write_text("old-agents", encoding="utf-8")
    manager.initialize_base("import")

    manager.uninstall("base")
    shutil.rmtree(agents_home)
    codex_home.joinpath("auth.json").write_text("current-codex", encoding="utf-8")
    manager.initialize_base("import")

    result = manager.uninstall("backup")

    assert result.restore_source == "backup"
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "current-codex"
    assert not agents_home.exists()
    assert not agents_home.is_symlink()


def test_uninstall_import_mode_can_restore_from_backup(manager: PackagentManager) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    manager.initialize_base("import")
    manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").write_text("base-copy", encoding="utf-8")

    result = manager.uninstall("backup")

    assert result.restore_source == "backup"
    assert not codex_home.is_symlink()
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"


def test_uninstall_fresh_mode_restores_backup_by_default(manager: PackagentManager) -> None:
    claude_target = manager.host.target_by_key("claude-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    claude_home = manager.host.managed_target_path(manager.paths, claude_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    claude_home.mkdir()
    claude_home.joinpath(".credentials.json").write_text("claude-original", encoding="utf-8")
    manager.initialize_base("fresh")

    result = manager.uninstall()

    assert result.restore_source == "backup"
    assert not codex_home.is_symlink()
    assert not claude_home.is_symlink()
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"
    assert claude_home.joinpath(".credentials.json").read_text(encoding="utf-8") == "claude-original"


def test_uninstall_fresh_mode_rejects_base_restore(manager: PackagentManager) -> None:
    manager.initialize_base("fresh")

    with pytest.raises(Exception):
        manager.uninstall("base")


def test_uninstall_backup_restore_leaves_originally_missing_targets_absent(
    manager: PackagentManager,
) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    manager.initialize_base("fresh")

    manager.uninstall()

    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"
    assert not agents_home.exists()
    assert not agents_home.is_symlink()


def test_uninstall_backup_restore_uses_recorded_legacy_backup_path(
    manager: PackagentManager,
) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    manager.initialize_base("fresh")

    state = manager.load_state()
    backup_root = Path(state.backups[0].backup_path)
    legacy_backup_root = manager.paths.root / "backups" / backup_root.name
    legacy_backup_root.parent.mkdir(parents=True)
    backup_root.rename(legacy_backup_root)
    state.backups[0].backup_path = str(legacy_backup_root)
    manager.paths.state_file.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = manager.uninstall()

    assert result.restore_source == "backup"
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"
    codex_result = next(item for item in result.target_results if item.key == "codex-home")
    assert codex_result.source_path == str(legacy_backup_root / ".codex")


def test_uninstall_backup_restore_supports_legacy_symlink_snapshot_path(
    manager: PackagentManager,
) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    source_home = manager.paths.home / "external-codex"
    source_home.mkdir()
    source_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    codex_home.symlink_to(source_home)
    manager.initialize_base("fresh")

    state = manager.load_state()
    codex_record = next(record for record in state.backups if record.target_key == "codex-home")
    backup_root = Path(codex_record.backup_path)
    backup_root.joinpath(".codex").rename(backup_root / "resolved-home")
    backup_root.joinpath(".codex.symlink.json").rename(backup_root / "symlink.json")

    result = manager.uninstall()

    assert result.restore_source == "backup"
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"
    codex_result = next(item for item in result.target_results if item.key == "codex-home")
    assert codex_result.source_path == str(backup_root / "resolved-home")


def test_doctor_fix_migrates_legacy_backup_directory(
    manager: PackagentManager,
) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    manager.initialize_base("import")

    state = manager.load_state()
    backup_root = Path(state.backups[0].backup_path)
    legacy_backup_root = manager.paths.root / "backups" / backup_root.name
    legacy_backup_root.parent.mkdir(parents=True)
    backup_root.rename(legacy_backup_root)
    state.backups[0].backup_path = str(legacy_backup_root)
    state.envs["base"].imported_from = str(legacy_backup_root)
    manager.paths.state_file.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = manager.doctor()

    assert any("legacy backup directory exists" in issue for issue in report.issues)

    fixed = manager.doctor(fix=True)
    migrated = manager.load_state()
    migrated_backup_root = Path(migrated.backups[0].backup_path)

    assert not fixed.issues
    assert any("migrated legacy backups" in repaired for repaired in fixed.repaired)
    assert migrated_backup_root.parent == manager.paths.backups_root
    assert migrated_backup_root.joinpath(".codex", "auth.json").read_text(encoding="utf-8") == "codex-original"
    assert migrated.envs["base"].imported_from == str(migrated_backup_root)
    env_metadata = json.loads(manager.paths.env_metadata_file("base").read_text(encoding="utf-8"))
    assert env_metadata["imported_from"] == str(migrated_backup_root)
    assert not legacy_backup_root.parent.exists()

    result = manager.uninstall("backup")

    assert result.restore_source == "backup"
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"


def test_uninstall_refuses_target_drift_without_partial_restore(manager: PackagentManager) -> None:
    agents_target = manager.host.target_by_key("agents-home")
    codex_home = manager.host.managed_home_path(manager.paths)
    agents_home = manager.host.managed_target_path(manager.paths, agents_target)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    manager.initialize_base("import")
    codex_home.unlink()
    codex_home.mkdir()
    codex_home.joinpath("unmanaged.txt").write_text("drift", encoding="utf-8")

    with pytest.raises(Exception):
        manager.uninstall("base")

    assert codex_home.joinpath("unmanaged.txt").read_text(encoding="utf-8") == "drift"
    assert agents_home.is_symlink()


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


@pytest.mark.parametrize("target_key", ["agents-home", "claude-home"])
def test_doctor_detects_and_repairs_companion_symlink_drift(
    manager: PackagentManager,
    target_key: str,
) -> None:
    target = manager.host.target_by_key(target_key)
    manager.create_env("env-a")
    manager.activate_env("env-a")

    home = manager.host.managed_target_path(manager.paths, target)
    home.unlink()
    home.symlink_to(manager.host.env_target_path(manager.paths, "base", target))

    report = manager.doctor()
    assert any(target_key in issue for issue in report.issues)

    fixed = manager.doctor(fix=True)
    assert not fixed.issues
    assert home.resolve() == manager.host.env_target_path(manager.paths, "env-a", target)


@pytest.mark.parametrize("target_key", ["agents-home", "claude-home"])
def test_doctor_detects_and_repairs_missing_companion_symlink(
    manager: PackagentManager,
    target_key: str,
) -> None:
    target = manager.host.target_by_key(target_key)
    manager.create_env("env-a")
    manager.activate_env("env-a")

    home = manager.host.managed_target_path(manager.paths, target)
    home.unlink()

    report = manager.doctor()
    assert any(target_key in issue for issue in report.issues)

    fixed = manager.doctor(fix=True)
    assert not fixed.issues
    assert home.resolve() == manager.host.env_target_path(manager.paths, "env-a", target)


@pytest.mark.parametrize("target_key", ["agents-home", "claude-home"])
def test_doctor_detects_and_repairs_unmanaged_companion_directory(
    manager: PackagentManager,
    target_key: str,
) -> None:
    target = manager.host.target_by_key(target_key)
    manager.create_env("env-a")
    manager.activate_env("env-a")

    home = manager.host.managed_target_path(manager.paths, target)
    home.unlink()
    home.mkdir()
    home.joinpath("memory.md").write_text("legacy", encoding="utf-8")

    report = manager.doctor()
    assert any(target_key in issue for issue in report.issues)

    fixed = manager.doctor(fix=True)
    assert not fixed.issues
    assert home.resolve() == manager.host.env_target_path(manager.paths, "env-a", target)
    assert manager.host.env_target_path(manager.paths, "base", target).joinpath("memory.md").read_text(encoding="utf-8") == "legacy"


@pytest.mark.parametrize("target_key", ["agents-home", "claude-home"])
def test_doctor_detects_and_repairs_broken_companion_managed_symlink(
    manager: PackagentManager,
    target_key: str,
) -> None:
    target = manager.host.target_by_key(target_key)
    manager.create_env("env-a")
    manager.activate_env("env-a")

    broken_target = manager.host.env_target_path(manager.paths, "missing-env", target)
    home = manager.host.managed_target_path(manager.paths, target)
    home.unlink()
    home.symlink_to(broken_target)

    inspection = manager.backend.inspect(manager.paths, manager.host, target)
    assert inspection.kind == HOME_KIND_BROKEN_MANAGED
    report = manager.doctor()
    assert any(target_key in issue for issue in report.issues)

    fixed = manager.doctor(fix=True)
    assert not fixed.issues
    assert home.resolve() == manager.host.env_target_path(manager.paths, "env-a", target)


def test_schema_v1_state_migrates_to_managed_targets(manager: PackagentManager) -> None:
    state = {
        "schema_version": 1,
        "host": "codex",
        "base_env": "base",
        "active_env": "base",
        "managed_home_path": str(manager.paths.home / ".codex"),
        "managed_root": str(manager.paths.root),
        "manager_name": "packagent",
        "last_link_target": str(manager.paths.env_dir("base") / ".codex"),
        "envs": {},
        "backups": [],
    }
    manager.paths.root.mkdir(parents=True)
    manager.paths.state_file.write_text(json.dumps(state), encoding="utf-8")

    manager.status()
    migrated = manager.load_state()

    assert migrated.schema_version == 2
    assert sorted(migrated.managed_targets) == ["agents-home", "claude-home", "codex-home"]
    assert migrated.managed_targets["codex-home"].last_link_target == str(manager.paths.env_dir("base") / ".codex")
    assert manager.host.env_target_path(manager.paths, "base", manager.host.target_by_key("agents-home")).exists()
    assert manager.host.env_target_path(manager.paths, "base", manager.host.target_by_key("claude-home")).exists()


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
    assert "export CODEX_HOME=" not in output
    assert "export CLAUDE_CONFIG_DIR=" not in output


def test_shell_active_env_tracks_only_consistent_global_activation(
    manager: PackagentManager,
) -> None:
    manager.initialize_base("import")
    manager.create_env("work")

    assert manager.shell_active_env() == "base"

    manager.activate_env("work")
    assert manager.shell_active_env() == "work"

    manager.activate_env("base")
    assert manager.shell_active_env() == "base"

    manager.uninstall("base")
    assert manager.shell_active_env() is None


def test_cli_shell_active_env_prints_blank_after_uninstall(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager.initialize_base("import")

    active_code = main(["shell", "active-env"])
    active_output = capsys.readouterr().out
    manager.uninstall("base")
    inactive_code = main(["shell", "active-env"])
    inactive_output = capsys.readouterr().out

    assert active_code == 0
    assert active_output == "base\n"
    assert inactive_code == 0
    assert inactive_output == ""


def test_cli_create_prints_bare_env_notice(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["create", "-n", "work"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert f"created\twork\t{manager.paths.env_dir('work')}" in output
    assert "You've created an env with bare codex/claude homes except shared auth." in output
    assert "packagent create -n <env-name> --clone <src-env-name>" in output


def test_cli_create_clone_prints_source_env_notice(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["create", "-n", "work", "--clone", "base"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert f"created\twork\t{manager.paths.env_dir('work')}" in output
    assert "You've created an env based on base." in output


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
    assert "shell init bash" in rc_path.read_text(encoding="utf-8")
    assert "==== Initializing packagent ====" in output
    assert "shell: bash" in output
    assert f"rc_file: {rc_path} (updated)" in output
    assert "base_mode: import" in output
    assert "active_env: base" in output
    assert f"source {rc_path}" in output
    assert 'eval "$(packagent shell init bash)"' in output


def test_cli_init_can_target_explicit_rc_file(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc_path = manager.paths.home / ".config" / "packagent-test-zshrc"

    exit_code = main(["init", "--shell", "zsh", "--rc-file", str(rc_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "shell init zsh" in rc_path.read_text(encoding="utf-8")
    assert "shell: zsh" in output
    assert f"rc_file: {rc_path} (updated)" in output


def test_cli_init_base_mode_fresh_creates_bare_base(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")

    exit_code = main(["init", "--shell", "bash", "--base-mode", "fresh"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "base_mode: fresh" in output
    assert codex_home.is_symlink()
    assert not manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").exists()
    assert any(path.name == "auth.json" for path in manager.paths.backups_root.rglob("*"))


def test_cli_init_interactive_prompt_can_choose_fresh(
    manager: PackagentManager,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", TtyInput("fresh\n"))

    exit_code = main(["init", "--shell", "bash"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "base_mode: fresh" in captured.out
    assert "Base mode" in captured.err
    assert not manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").exists()


def test_cli_init_noninteractive_defaults_to_import(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")

    exit_code = main(["init", "--shell", "bash"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "base_mode: import" in output
    assert manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").read_text(encoding="utf-8") == "codex"


def test_cli_uninstall_import_mode_noninteractive_requires_restore_source(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")
    main(["init", "--shell", "bash"])
    capsys.readouterr()

    exit_code = main(["uninstall"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--restore-source base or --restore-source backup" in captured.err
    assert codex_home.is_symlink()


def test_cli_uninstall_interactive_import_mode_can_choose_backup(
    manager: PackagentManager,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    main(["init", "--shell", "bash"])
    manager.host.env_home_path(manager.paths, "base").joinpath("auth.json").write_text("base-copy", encoding="utf-8")
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", TtyInput("backup\n"))

    exit_code = main(["uninstall"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "restore_source: backup" in captured.out
    assert "Restore source" in captured.err
    assert codex_home.joinpath("auth.json").read_text(encoding="utf-8") == "codex-original"


def test_cli_init_can_reinit_after_uninstall_restore_from_base(
    manager: PackagentManager,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex-original", encoding="utf-8")
    main(["init", "--shell", "bash", "--base-mode", "import"])
    manager.host.env_home_path(manager.paths, "base").joinpath("base-only.txt").write_text("base", encoding="utf-8")
    main(["uninstall", "--restore-source", "base", "--shell", "bash"])
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", TtyInput("\n"))

    exit_code = main(["init", "--shell", "bash"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Base mode" in captured.err
    assert "refusing to overwrite unmanaged home directory" not in captured.err
    assert "base_mode: import" in captured.out
    assert codex_home.is_symlink()
    assert manager.host.env_home_path(manager.paths, "base").joinpath("base-only.txt").read_text(encoding="utf-8") == "base"


def test_cli_uninstall_restores_and_removes_managed_shell_block(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc_path = manager.paths.home / ".bashrc"
    codex_home = manager.host.managed_home_path(manager.paths)
    codex_home.mkdir()
    codex_home.joinpath("auth.json").write_text("codex", encoding="utf-8")
    main(["init", "--shell", "bash", "--rc-file", str(rc_path)])
    assert "# >>> packagent initialize >>>" in rc_path.read_text(encoding="utf-8")
    capsys.readouterr()

    exit_code = main(
        [
            "uninstall",
            "--restore-source",
            "base",
            "--shell",
            "bash",
            "--rc-file",
            str(rc_path),
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "restore_source: base" in output
    assert "target\taction\tmanaged_home\tsource" in output
    assert f"rc_file: {rc_path} (updated)" in output
    assert "restart the shell" in output
    assert not codex_home.is_symlink()
    assert "# >>> packagent initialize >>>" not in rc_path.read_text(encoding="utf-8")


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
    assert "export CODEX_HOME=" not in output
    assert "export CLAUDE_CONFIG_DIR=" not in output


def test_cli_list_and_status_include_table_headers(
    manager: PackagentManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager.create_env("work")

    list_code = main(["list"])
    list_output = capsys.readouterr().out
    assert list_code == 0
    assert list_output.splitlines()[0] == "active\tname\tpath"
    assert "false\twork\t" in list_output

    status_code = main(["status"])
    status_output = capsys.readouterr().out
    assert status_code == 0
    assert "active_env=base" in status_output
    assert "home_target=" not in status_output
    assert "expected_target=" not in status_output
    assert "target\tmanaged\tmanaged_home\thome_kind\thome_target\texpected_target" in status_output
    assert "codex-home\t" in status_output
    assert "agents-home\t" in status_output
    assert "claude-home\t" in status_output
