from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import os

from packagent.errors import UserFacingError
from packagent.hosts import HostAdapter
from packagent.paths import PackagentPaths

HOME_KIND_MISSING = "missing"
HOME_KIND_MANAGED = "managed_symlink"
HOME_KIND_BROKEN_MANAGED = "broken_managed_symlink"
HOME_KIND_UNMANAGED_DIRECTORY = "unmanaged_directory"
HOME_KIND_UNMANAGED_SYMLINK = "unmanaged_symlink"
HOME_KIND_UNMANAGED_FILE = "unmanaged_file"


@dataclass
class HomeInspection:
    kind: str
    home_path: str
    raw_target: str | None = None
    resolved_target: str | None = None
    managed_env: str | None = None


class ActivationBackend(ABC):
    @abstractmethod
    def inspect(self, paths: PackagentPaths, host: HostAdapter) -> HomeInspection:
        raise NotImplementedError

    @abstractmethod
    def activate(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        raise NotImplementedError

    @abstractmethod
    def expected_target(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        raise NotImplementedError


class GlobalSymlinkBackend(ActivationBackend):
    def inspect(self, paths: PackagentPaths, host: HostAdapter) -> HomeInspection:
        home_path = host.managed_home_path(paths)
        if not home_path.exists() and not home_path.is_symlink():
            return HomeInspection(kind=HOME_KIND_MISSING, home_path=str(home_path))

        if home_path.is_symlink():
            raw_target = os.readlink(home_path)
            resolved_target = home_path.resolve(strict=False)
            managed_env = self._infer_env_from_target(paths, host, resolved_target)
            if managed_env:
                if resolved_target.exists():
                    return HomeInspection(
                        kind=HOME_KIND_MANAGED,
                        home_path=str(home_path),
                        raw_target=raw_target,
                        resolved_target=str(resolved_target),
                        managed_env=managed_env,
                    )
                return HomeInspection(
                    kind=HOME_KIND_BROKEN_MANAGED,
                    home_path=str(home_path),
                    raw_target=raw_target,
                    resolved_target=str(resolved_target),
                    managed_env=managed_env,
                )
            return HomeInspection(
                kind=HOME_KIND_UNMANAGED_SYMLINK,
                home_path=str(home_path),
                raw_target=raw_target,
                resolved_target=str(resolved_target),
            )

        if home_path.is_dir():
            return HomeInspection(kind=HOME_KIND_UNMANAGED_DIRECTORY, home_path=str(home_path))

        return HomeInspection(kind=HOME_KIND_UNMANAGED_FILE, home_path=str(home_path))

    def activate(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        home_path = host.managed_home_path(paths)
        target = self.expected_target(paths, host, env_name)
        home_path.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        if home_path.is_symlink() or home_path.is_file():
            home_path.unlink()
        elif home_path.exists():
            raise UserFacingError(
                f"refusing to overwrite unmanaged home directory at {home_path}; run 'packagent doctor --fix' first",
            )
        home_path.symlink_to(target)
        return target

    def expected_target(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        return host.env_home_path(paths, env_name)

    def _infer_env_from_target(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        target: Path,
    ) -> str | None:
        try:
            relative = target.relative_to(paths.envs_root)
        except ValueError:
            return None
        if len(relative.parts) != 2:
            return None
        env_name, tail = relative.parts
        if tail != host.home_dir_name:
            return None
        return env_name
