from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
import os
from pathlib import Path

from packagent.paths import PackagentPaths


@dataclass(frozen=True)
class ManagedTarget:
    key: str
    home_dir_name: str
    home_env_var: str | None = None
    primary: bool = False
    shared_seed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class HostAdapter(ABC):
    name: str
    targets: tuple[ManagedTarget, ...]

    @property
    def home_dir_name(self) -> str:
        return self.primary_target().home_dir_name

    @property
    def home_env_var(self) -> str | None:
        return self.primary_target().home_env_var

    def primary_target(self) -> ManagedTarget:
        for target in self.targets:
            if target.primary:
                return target
        return self.targets[0]

    def target_by_key(self, key: str) -> ManagedTarget:
        for target in self.targets:
            if target.key == key:
                return target
        raise KeyError(key)

    def managed_home_path(self, paths: PackagentPaths) -> Path:
        return self.managed_target_path(paths, self.primary_target())

    def managed_target_path(self, paths: PackagentPaths, target: ManagedTarget) -> Path:
        if target.home_env_var:
            configured_home = os.environ.get(target.home_env_var)
            if configured_home:
                return Path(configured_home).expanduser()
        return paths.home / target.home_dir_name

    def env_home_path(self, paths: PackagentPaths, env_name: str) -> Path:
        return self.env_target_path(paths, env_name, self.primary_target())

    def env_target_path(self, paths: PackagentPaths, env_name: str, target: ManagedTarget) -> Path:
        return paths.env_dir(env_name) / target.home_dir_name


class CodexHost(HostAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="codex",
            targets=(
                ManagedTarget(
                    key="codex-home",
                    home_dir_name=".codex",
                    home_env_var="CODEX_HOME",
                    primary=True,
                    shared_seed_files=("auth.json",),
                ),
                ManagedTarget(key="agents-home", home_dir_name=".agents"),
                ManagedTarget(
                    key="claude-home",
                    home_dir_name=".claude",
                    home_env_var="CLAUDE_CONFIG_DIR",
                    shared_seed_files=(".credentials.json",),
                ),
            ),
        )
