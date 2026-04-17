from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import os

from packagent.errors import UserFacingError
from packagent.hosts import HostAdapter, ManagedTarget
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
    target_key: str | None = None


class ActivationBackend(ABC):
    @abstractmethod
    def inspect(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        target: ManagedTarget | None = None,
    ) -> HomeInspection:
        raise NotImplementedError

    @abstractmethod
    def activate(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        env_name: str,
        target: ManagedTarget | None = None,
    ) -> Path:
        raise NotImplementedError

    @abstractmethod
    def expected_target(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        env_name: str,
        target: ManagedTarget | None = None,
    ) -> Path:
        raise NotImplementedError


class GlobalSymlinkBackend(ActivationBackend):
    def inspect(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        target: ManagedTarget | None = None,
    ) -> HomeInspection:
        resolved_target_config = target or host.primary_target()
        home_path = host.managed_target_path(paths, resolved_target_config)
        if not home_path.exists() and not home_path.is_symlink():
            return HomeInspection(
                kind=HOME_KIND_MISSING,
                home_path=str(home_path),
                target_key=resolved_target_config.key,
            )

        if home_path.is_symlink():
            raw_target = os.readlink(home_path)
            resolved_target = home_path.resolve(strict=False)
            managed_env = self._infer_env_from_target(
                paths,
                host,
                resolved_target_config,
                resolved_target,
            )
            if managed_env:
                if resolved_target.exists():
                    return HomeInspection(
                        kind=HOME_KIND_MANAGED,
                        home_path=str(home_path),
                        target_key=resolved_target_config.key,
                        raw_target=raw_target,
                        resolved_target=str(resolved_target),
                        managed_env=managed_env,
                    )
                return HomeInspection(
                    kind=HOME_KIND_BROKEN_MANAGED,
                    home_path=str(home_path),
                    target_key=resolved_target_config.key,
                    raw_target=raw_target,
                    resolved_target=str(resolved_target),
                    managed_env=managed_env,
                )
            return HomeInspection(
                kind=HOME_KIND_UNMANAGED_SYMLINK,
                home_path=str(home_path),
                target_key=resolved_target_config.key,
                raw_target=raw_target,
                resolved_target=str(resolved_target),
            )

        if home_path.is_dir():
            return HomeInspection(
                kind=HOME_KIND_UNMANAGED_DIRECTORY,
                home_path=str(home_path),
                target_key=resolved_target_config.key,
            )

        return HomeInspection(
            kind=HOME_KIND_UNMANAGED_FILE,
            home_path=str(home_path),
            target_key=resolved_target_config.key,
        )

    def activate(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        env_name: str,
        target: ManagedTarget | None = None,
    ) -> Path:
        resolved_target_config = target or host.primary_target()
        home_path = host.managed_target_path(paths, resolved_target_config)
        expected = self.expected_target(paths, host, env_name, resolved_target_config)
        home_path.parent.mkdir(parents=True, exist_ok=True)
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.mkdir(parents=True, exist_ok=True)
        if home_path.is_symlink() or home_path.is_file():
            home_path.unlink()
        elif home_path.exists():
            raise UserFacingError(
                f"refusing to overwrite unmanaged home directory at {home_path}; run 'packagent doctor --fix' first",
            )
        home_path.symlink_to(expected)
        return expected

    def expected_target(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        env_name: str,
        target: ManagedTarget | None = None,
    ) -> Path:
        resolved_target_config = target or host.primary_target()
        return host.env_target_path(paths, env_name, resolved_target_config)

    def _infer_env_from_target(
        self,
        paths: PackagentPaths,
        host: HostAdapter,
        target_config: ManagedTarget,
        target: Path,
    ) -> str | None:
        try:
            relative = target.relative_to(paths.envs_root)
        except ValueError:
            return None
        if not relative.parts:
            return None
        env_name = relative.parts[0]
        tail = Path(*relative.parts[1:])
        if tail != Path(target_config.home_dir_name):
            return None
        return env_name
