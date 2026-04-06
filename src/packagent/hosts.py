from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict, Tuple

from packagent.paths import PackagentPaths


@dataclass(frozen=True)
class HostAdapter(ABC):
    name: str
    home_dir_name: str
    home_env_var: str | None = None

    def managed_home_path(self, paths: PackagentPaths) -> Path:
        if self.home_env_var:
            configured_home = os.environ.get(self.home_env_var)
            if configured_home:
                return Path(configured_home).expanduser()
        return paths.home / self.home_dir_name

    def env_home_path(self, paths: PackagentPaths, env_name: str) -> Path:
        return paths.env_dir(env_name) / self.home_dir_name


class CodexHost(HostAdapter):
    def __init__(self) -> None:
        super().__init__(name="codex", home_dir_name=".codex", home_env_var="CODEX_HOME")


class ClaudeHost(HostAdapter):
    def __init__(self) -> None:
        super().__init__(name="claude", home_dir_name=".claude", home_env_var="CLAUDE_CONFIG_DIR")


def default_hosts() -> Tuple[HostAdapter, ...]:
    return (CodexHost(), ClaudeHost())


def default_host_map() -> Dict[str, HostAdapter]:
    return {host.name: host for host in default_hosts()}


SUPPORTED_PROVIDERS = tuple(host.name for host in default_hosts())
