from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys

from packagent.errors import UserFacingError
from packagent.hosts import HostAdapter
from packagent.paths import PackagentPaths

HOME_KIND_MISSING = "missing"
HOME_KIND_MANAGED = "managed_symlink"
HOME_KIND_BROKEN_MANAGED = "broken_managed_symlink"
HOME_KIND_MANAGED_DIRECTORY = "managed_directory"
HOME_KIND_MANAGED_MOUNT = "managed_mount"
HOME_KIND_UNMANAGED_DIRECTORY = "unmanaged_directory"
HOME_KIND_UNMANAGED_SYMLINK = "unmanaged_symlink"
HOME_KIND_UNMANAGED_FILE = "unmanaged_file"
MANAGED_HOME_MARKER = ".packagent-home.json"
ENV_HOME_MARKER = ".packagent-env.json"


@dataclass
class HomeInspection:
    kind: str
    home_path: str
    raw_target: str | None = None
    resolved_target: str | None = None
    managed_env: str | None = None


class ActivationBackend(ABC):
    shell_scoped = False
    managed_kinds = {HOME_KIND_MANAGED, HOME_KIND_BROKEN_MANAGED}

    @abstractmethod
    def inspect(self, paths: PackagentPaths, host: HostAdapter) -> HomeInspection:
        raise NotImplementedError

    @abstractmethod
    def activate(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        raise NotImplementedError

    @abstractmethod
    def expected_target(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        raise NotImplementedError

    def persist_default_env_on_activate(self) -> bool:
        return True


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


class LinuxNamespaceBackend(ActivationBackend):
    shell_scoped = True
    managed_kinds = {HOME_KIND_MANAGED_DIRECTORY, HOME_KIND_MANAGED_MOUNT}

    def inspect(self, paths: PackagentPaths, host: HostAdapter) -> HomeInspection:
        home_path = host.managed_home_path(paths)
        if not home_path.exists() and not home_path.is_symlink():
            return HomeInspection(kind=HOME_KIND_MISSING, home_path=str(home_path))

        if home_path.is_symlink():
            raw_target = os.readlink(home_path)
            resolved_target = home_path.resolve(strict=False)
            return HomeInspection(
                kind=HOME_KIND_UNMANAGED_SYMLINK,
                home_path=str(home_path),
                raw_target=raw_target,
                resolved_target=str(resolved_target),
            )

        if home_path.is_dir():
            env_name = _read_marker_env_name(home_path / ENV_HOME_MARKER)
            if env_name:
                return HomeInspection(
                    kind=HOME_KIND_MANAGED_MOUNT,
                    home_path=str(home_path),
                    resolved_target=str(host.env_home_path(paths, env_name)),
                    managed_env=env_name,
                )
            if (home_path / MANAGED_HOME_MARKER).is_file():
                return HomeInspection(
                    kind=HOME_KIND_MANAGED_DIRECTORY,
                    home_path=str(home_path),
                )
            return HomeInspection(kind=HOME_KIND_UNMANAGED_DIRECTORY, home_path=str(home_path))

        return HomeInspection(kind=HOME_KIND_UNMANAGED_FILE, home_path=str(home_path))

    def activate(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        home_path = host.managed_home_path(paths)
        target = self.expected_target(paths, host, env_name)
        target.mkdir(parents=True, exist_ok=True)
        self._ensure_mountpoint(home_path)
        if home_path.is_mount():
            _run_command(["umount", str(home_path)], "failed to unmount the current per-shell ~/.codex bind mount")
        _run_command(
            ["mount", "--bind", str(target), str(home_path)],
            f"failed to bind {target} onto {home_path}",
        )
        return target

    def expected_target(self, paths: PackagentPaths, host: HostAdapter, env_name: str) -> Path:
        return host.env_home_path(paths, env_name)

    def persist_default_env_on_activate(self) -> bool:
        return False

    def _ensure_mountpoint(self, home_path: Path) -> None:
        if home_path.is_symlink() or home_path.is_file():
            raise UserFacingError(
                f"refusing to use {home_path} as a Linux mountpoint because it is not a directory",
            )
        home_path.mkdir(parents=True, exist_ok=True)
        marker_path = home_path / MANAGED_HOME_MARKER
        if not marker_path.exists():
            marker_path.write_text(
                json.dumps({"manager": "packagent-v1", "type": "mountpoint"}) + "\n",
                encoding="utf-8",
            )


def default_backend(platform_name: str | None = None) -> ActivationBackend:
    platform_name = platform_name or sys.platform
    if platform_name.startswith("linux"):
        return LinuxNamespaceBackend()
    return GlobalSymlinkBackend()


def linux_namespace_support_error() -> str | None:
    if not sys.platform.startswith("linux"):
        return "per-shell ~/.codex activation is only available on Linux"
    if shutil.which("unshare") is None:
        return "linux per-shell ~/.codex activation requires the 'unshare' command"
    if shutil.which("mount") is None or shutil.which("umount") is None:
        return "linux per-shell ~/.codex activation requires both 'mount' and 'umount'"
    try:
        probe = subprocess.run(
            ["unshare", "--user", "--mount", "--map-root-user", "/bin/sh", "-c", "true"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        return f"linux per-shell ~/.codex activation failed to start 'unshare': {exc}"
    if probe.returncode == 0:
        return None
    stderr = (probe.stderr or "").strip()
    if stderr:
        return (
            "linux per-shell ~/.codex activation requires unprivileged user and mount namespaces; "
            f"unshare failed with: {stderr}"
        )
    return "linux per-shell ~/.codex activation requires unprivileged user and mount namespaces"


def _read_marker_env_name(marker_path: Path) -> str | None:
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    env_name = payload.get("name")
    if not env_name:
        return None
    return str(env_name)


def _run_command(command: list[str], failure_message: str) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise UserFacingError(f"{failure_message}: {exc}") from exc
    if completed.returncode == 0:
        return
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    details = stderr or stdout or f"exit status {completed.returncode}"
    raise UserFacingError(f"{failure_message}: {details}")
