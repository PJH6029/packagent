from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from pathlib import Path

from packagent.paths import PackagentPaths


@dataclass(frozen=True)
class HostAdapter(ABC):
    name: str
    home_dir_name: str

    def managed_home_path(self, paths: PackagentPaths) -> Path:
        return paths.home / self.home_dir_name

    def env_home_path(self, paths: PackagentPaths, env_name: str) -> Path:
        return paths.env_dir(env_name) / self.home_dir_name


class CodexHost(HostAdapter):
    def __init__(self) -> None:
        super().__init__(name="codex", home_dir_name=".codex")

