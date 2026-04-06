from __future__ import annotations

from pathlib import Path
import json
import shutil
from typing import Dict, List, Mapping, Optional

from packagent.activation import (
    ActivationBackend,
    GlobalSymlinkBackend,
    HOME_KIND_BROKEN_MANAGED,
    HOME_KIND_MANAGED,
    HOME_KIND_MISSING,
    HOME_KIND_UNMANAGED_DIRECTORY,
    HOME_KIND_UNMANAGED_FILE,
    HOME_KIND_UNMANAGED_SYMLINK,
    HomeInspection,
)
from packagent.errors import UserFacingError
from packagent.hosts import HostAdapter, default_host_map
from packagent.locking import mutation_lock
from packagent.models import (
    ActivationResult,
    BackupRecord,
    DoctorReport,
    EnvMetadata,
    PackagentState,
    ProviderStatus,
    StatusReport,
)
from packagent.paths import PackagentPaths
from packagent.util import copy_directory, remove_path, timestamp_slug, utc_now_iso, write_json
from packagent.validation import validate_env_name


class PackagentManager:
    def __init__(
        self,
        paths: Optional[PackagentPaths] = None,
        hosts: Optional[Mapping[str, HostAdapter]] = None,
        backend: Optional[ActivationBackend] = None,
    ) -> None:
        self.paths = paths or PackagentPaths.discover()
        self.hosts = dict(hosts or default_host_map())
        if not self.hosts:
            raise ValueError("at least one provider host must be configured")
        self.backend = backend or GlobalSymlinkBackend()
        self.default_provider = "codex" if "codex" in self.hosts else next(iter(self.hosts))
        self.host = self.hosts[self.default_provider]

    def create_env(
        self,
        name: str,
        clone_from: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> EnvMetadata:
        validate_env_name(name)
        with mutation_lock(self.paths):
            state = self._ensure_state()
            if name in state.envs:
                raise UserFacingError(f"environment '{name}' already exists")

            source_metadata: Optional[EnvMetadata] = None
            source_name: Optional[str] = None
            if clone_from:
                source_name = validate_env_name(clone_from, allow_base=True)
                if source_name not in state.envs:
                    raise UserFacingError(f"environment '{source_name}' does not exist")
                source_metadata = state.envs[source_name]

            env_provider = self._resolve_new_env_provider(provider, source_metadata)
            if source_name:
                self._clone_env(state, source_name, name)
                metadata = EnvMetadata(
                    name=name,
                    provider=env_provider,
                    source="cloned",
                    created_at=utc_now_iso(),
                    cloned_from=source_name,
                )
            else:
                self._ensure_env_homes(name, wipe=True)
                metadata = EnvMetadata(
                    name=name,
                    provider=env_provider,
                    source="created",
                    created_at=utc_now_iso(),
                )

            state.envs[name] = metadata
            self._write_env_metadata(metadata)
            self._save_state(state)
            return metadata

    def activate_env(self, name: str) -> ActivationResult:
        env_name = validate_env_name(name, allow_base=True)
        with mutation_lock(self.paths):
            state = self._ensure_state()
            if env_name not in state.envs:
                raise UserFacingError(f"environment '{env_name}' does not exist")

            self._ensure_managed_homes(state)
            state.active_env = env_name
            for host in self.hosts.values():
                target = self.backend.activate(self.paths, host, env_name)
                state.last_link_targets[host.name] = str(target)
                state.managed_home_paths[host.name] = str(host.managed_home_path(self.paths))
            self._save_state(state)
            return ActivationResult(env_name=env_name, provider=self._env_provider(state, env_name))

    def deactivate_env(self) -> ActivationResult:
        with mutation_lock(self.paths):
            state = self._ensure_state()
            self._ensure_managed_homes(state)
            state.active_env = state.base_env
            for host in self.hosts.values():
                target = self.backend.activate(self.paths, host, state.base_env)
                state.last_link_targets[host.name] = str(target)
                state.managed_home_paths[host.name] = str(host.managed_home_path(self.paths))
            self._save_state(state)
            return ActivationResult(env_name=state.base_env, provider=self._env_provider(state, state.base_env))

    def list_envs(self) -> List[Dict[str, str]]:
        state = self._ensure_state()
        rows: List[Dict[str, str]] = []
        for name in sorted(state.envs):
            rows.append(
                {
                    "name": name,
                    "provider": self._env_provider(state, name),
                    "active": "true" if name == state.active_env else "false",
                    "path": str(self.paths.env_dir(name)),
                },
            )
        return rows

    def status(self) -> StatusReport:
        state = self._ensure_state()
        return self._build_status_report(state, self._inspect_homes(state))

    def remove_env(self, name: str) -> None:
        env_name = validate_env_name(name, allow_base=True)
        with mutation_lock(self.paths):
            state = self._ensure_state()
            if env_name == state.base_env:
                raise UserFacingError("cannot remove the built-in 'base' environment")
            if env_name == state.active_env:
                raise UserFacingError(f"cannot remove the active environment '{env_name}'")
            if env_name not in state.envs:
                raise UserFacingError(f"environment '{env_name}' does not exist")

            remove_path(self.paths.env_dir(env_name))
            state.envs.pop(env_name, None)
            self._save_state(state)

    def doctor(self, *, fix: bool = False) -> DoctorReport:
        with mutation_lock(self.paths):
            state = self._ensure_state()
            inspections = self._inspect_homes(state)
            issues = self._collect_doctor_issues(state, inspections)
            repaired: List[str] = []
            if fix and issues:
                repaired.extend(self._repair_state_and_homes(state, inspections))
                inspections = self._inspect_homes(state)
                issues = self._collect_doctor_issues(state, inspections)
            return DoctorReport(
                status=self._build_status_report(state, inspections),
                issues=issues,
                repaired=repaired,
            )

    def load_state(self) -> PackagentState:
        payload = json.loads(self.paths.state_file.read_text(encoding="utf-8"))
        return PackagentState.from_dict(payload)

    def _ensure_state(self) -> PackagentState:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.envs_root.mkdir(parents=True, exist_ok=True)
        self.paths.backups_root.mkdir(parents=True, exist_ok=True)

        if self.paths.state_file.exists():
            state = self.load_state()
        else:
            state = PackagentState(
                schema_version=2,
                base_env="base",
                active_env="base",
                managed_root=str(self.paths.root),
            )

        changed = False
        if state.schema_version != 2:
            state.schema_version = 2
            changed = True
        if state.managed_root != str(self.paths.root):
            state.managed_root = str(self.paths.root)
            changed = True

        for host in self.hosts.values():
            managed_home_path = str(host.managed_home_path(self.paths))
            if state.managed_home_paths.get(host.name) != managed_home_path:
                state.managed_home_paths[host.name] = managed_home_path
                changed = True

        if state.base_env not in state.envs:
            metadata = EnvMetadata(
                name=state.base_env,
                provider=self.default_provider,
                source="created",
                created_at=utc_now_iso(),
            )
            state.envs[state.base_env] = metadata
            changed = True

        for env_name, metadata in list(state.envs.items()):
            normalized = self._normalize_env_metadata(metadata)
            if normalized != metadata:
                state.envs[env_name] = normalized
                metadata = normalized
                changed = True
            self._ensure_env_homes(env_name, wipe=False)
            self._write_env_metadata(metadata)

        if state.active_env not in state.envs:
            state.active_env = state.base_env
            changed = True

        if changed or not self.paths.state_file.exists():
            self._save_state(state)
        return state

    def _save_state(self, state: PackagentState) -> None:
        write_json(self.paths.state_file, state.to_dict())

    def _write_env_metadata(self, metadata: EnvMetadata) -> None:
        write_json(self.paths.env_metadata_file(metadata.name), metadata.to_dict())

    def _ensure_env_homes(self, env_name: str, *, wipe: bool) -> None:
        env_dir = self.paths.env_dir(env_name)
        if wipe and env_dir.exists():
            shutil.rmtree(env_dir)
        env_dir.mkdir(parents=True, exist_ok=True)
        for host in self.hosts.values():
            env_home = host.env_home_path(self.paths, env_name)
            env_home.mkdir(parents=True, exist_ok=True)

    def _clone_env(self, state: PackagentState, source_name: str, target_name: str) -> None:
        source_dir = self.paths.env_dir(source_name)
        target_dir = self.paths.env_dir(target_name)
        if target_dir.exists():
            raise UserFacingError(f"target environment '{target_name}' already exists")
        copy_directory(source_dir, target_dir)
        metadata_path = self.paths.env_metadata_file(target_name)
        if metadata_path.exists():
            metadata_path.unlink()

    def _ensure_managed_homes(self, state: PackagentState) -> None:
        for host in self.hosts.values():
            self._ensure_managed_home(state, host)

    def _ensure_managed_home(self, state: PackagentState, host: HostAdapter) -> None:
        inspection = self.backend.inspect(self.paths, host)
        if inspection.kind == HOME_KIND_MISSING:
            return
        if inspection.kind == HOME_KIND_MANAGED:
            self._reconcile_managed_state(state, inspection.managed_env, host.name)
            return
        if inspection.kind == HOME_KIND_BROKEN_MANAGED:
            if inspection.managed_env:
                self._reconcile_managed_state(state, inspection.managed_env, host.name)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_DIRECTORY:
            self._import_directory_home(state, host)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_SYMLINK:
            self._import_symlink_home(state, host, inspection)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_FILE:
            self._backup_file_home(state, host)
            return
        raise UserFacingError(f"cannot handle home state '{inspection.kind}' for provider '{host.name}'")

    def _reconcile_managed_state(self, state: PackagentState, managed_env: Optional[str], provider: str) -> None:
        if not managed_env:
            return
        if managed_env not in state.envs:
            metadata = EnvMetadata(
                name=managed_env,
                provider=provider if provider in self.hosts else self.default_provider,
                source="recovered",
                created_at=utc_now_iso(),
            )
            state.envs[managed_env] = metadata
            self._ensure_env_homes(managed_env, wipe=False)
            self._write_env_metadata(metadata)
        if state.active_env not in state.envs:
            state.active_env = managed_env

    def _import_directory_home(self, state: PackagentState, host: HostAdapter) -> None:
        home_path = host.managed_home_path(self.paths)
        backup_root = self._allocate_backup_dir()
        backup_home = backup_root / host.home_dir_name
        shutil.move(str(home_path), str(backup_home))
        self._replace_base_home_with_snapshot(host, backup_home)
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_directory",
                backup_path=str(backup_root),
                original_home=str(home_path),
                provider=host.name,
            ),
        )
        self._mark_base_imported(state, host.name, str(backup_root))

    def _import_symlink_home(self, state: PackagentState, host: HostAdapter, inspection: HomeInspection) -> None:
        home_path = host.managed_home_path(self.paths)
        if not inspection.resolved_target:
            raise UserFacingError(f"cannot import broken unmanaged symlink at {home_path}")
        resolved_target = Path(inspection.resolved_target)
        if not resolved_target.exists() or not resolved_target.is_dir():
            raise UserFacingError(
                f"cannot import unmanaged symlink at {home_path}; resolved target is not a directory",
            )

        backup_root = self._allocate_backup_dir()
        snapshot_dir = backup_root / "resolved-home"
        copy_directory(resolved_target, snapshot_dir)
        write_json(
            backup_root / "symlink.json",
            {
                "created_at": utc_now_iso(),
                "original_home": str(home_path),
                "provider": host.name,
                "raw_target": inspection.raw_target,
                "resolved_target": inspection.resolved_target,
            },
        )
        self._replace_base_home_with_snapshot(host, snapshot_dir)
        home_path.unlink()
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_symlink",
                backup_path=str(backup_root),
                original_home=str(home_path),
                provider=host.name,
                original_target=inspection.raw_target,
            ),
        )
        self._mark_base_imported(state, host.name, str(backup_root))

    def _backup_file_home(self, state: PackagentState, host: HostAdapter) -> None:
        home_path = host.managed_home_path(self.paths)
        backup_root = self._allocate_backup_dir()
        shutil.move(str(home_path), str(backup_root / f"unexpected-{host.name}-home-file"))
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_file",
                backup_path=str(backup_root),
                original_home=str(home_path),
                provider=host.name,
            ),
        )

    def _replace_base_home_with_snapshot(self, host: HostAdapter, snapshot_dir: Path) -> None:
        base_home = host.env_home_path(self.paths, "base")
        if base_home.exists():
            shutil.rmtree(base_home)
        copy_directory(snapshot_dir, base_home)

    def _mark_base_imported(self, state: PackagentState, provider: str, backup_root: str) -> None:
        base_metadata = state.envs[state.base_env]
        imported_from = dict(base_metadata.imported_from)
        imported_from[provider] = backup_root
        updated = EnvMetadata(
            name=state.base_env,
            provider=base_metadata.provider,
            source="imported-home",
            created_at=base_metadata.created_at,
            cloned_from=base_metadata.cloned_from,
            imported_from=imported_from,
        )
        state.envs[state.base_env] = updated
        self._write_env_metadata(updated)

    def _allocate_backup_dir(self) -> Path:
        candidate = self.paths.backups_root / timestamp_slug()
        suffix = 1
        while candidate.exists():
            candidate = self.paths.backups_root / f"{timestamp_slug()}-{suffix}"
            suffix += 1
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate

    def _inspect_homes(self, state: PackagentState) -> Dict[str, HomeInspection]:
        inspections: Dict[str, HomeInspection] = {}
        for host in self.hosts.values():
            state.managed_home_paths[host.name] = str(host.managed_home_path(self.paths))
            inspections[host.name] = self.backend.inspect(self.paths, host)
        return inspections

    def _build_status_report(
        self,
        state: PackagentState,
        inspections: Mapping[str, HomeInspection],
    ) -> StatusReport:
        providers: Dict[str, ProviderStatus] = {}
        for host in self.hosts.values():
            inspection = inspections[host.name]
            providers[host.name] = ProviderStatus(
                provider=host.name,
                managed=inspection.kind in {HOME_KIND_MANAGED, HOME_KIND_BROKEN_MANAGED},
                managed_home_path=str(host.managed_home_path(self.paths)),
                home_kind=inspection.kind,
                home_target=inspection.resolved_target,
                expected_target=str(self.backend.expected_target(self.paths, host, state.active_env)),
            )
        return StatusReport(
            active_env=state.active_env,
            provider=self._env_provider(state, state.active_env),
            providers=providers,
        )

    def _collect_doctor_issues(
        self,
        state: PackagentState,
        inspections: Mapping[str, HomeInspection],
    ) -> List[str]:
        issues: List[str] = []
        if state.base_env != "base":
            issues.append("base environment marker drifted from 'base'")
        if state.base_env not in state.envs:
            issues.append("base environment is missing from state")
        if not self.paths.env_dir(state.base_env).exists():
            issues.append("base environment directory is missing")
        if state.active_env not in state.envs:
            issues.append(f"active environment '{state.active_env}' is missing from state")

        for host in self.hosts.values():
            base_home = host.env_home_path(self.paths, state.base_env)
            if not base_home.exists():
                issues.append(f"base environment is missing the {host.name} home")

            inspection = inspections[host.name]
            home_path = host.managed_home_path(self.paths)
            if inspection.kind == HOME_KIND_MISSING:
                issues.append(f"{host.name} managed home {home_path} is not managed yet")
            elif inspection.kind == HOME_KIND_UNMANAGED_DIRECTORY:
                issues.append(f"{host.name} managed home {home_path} is an unmanaged directory")
            elif inspection.kind == HOME_KIND_UNMANAGED_SYMLINK:
                issues.append(f"{host.name} managed home {home_path} is an unmanaged symlink")
            elif inspection.kind == HOME_KIND_UNMANAGED_FILE:
                issues.append(f"{host.name} managed home {home_path} is an unmanaged file")
            elif inspection.kind == HOME_KIND_BROKEN_MANAGED:
                issues.append(f"{host.name} managed home {home_path} points to a missing managed target")
            elif inspection.kind == HOME_KIND_MANAGED:
                expected_target = str(self.backend.expected_target(self.paths, host, state.active_env))
                if inspection.resolved_target != expected_target:
                    if inspection.managed_env != state.active_env:
                        issues.append(
                            f"{host.name} managed symlink points to '{inspection.managed_env}' while state expects '{state.active_env}'",
                        )
                    else:
                        issues.append(
                            f"{host.name} managed symlink target does not match the expected active environment path",
                        )
        return issues

    def _repair_state_and_homes(
        self,
        state: PackagentState,
        inspections: Mapping[str, HomeInspection],
    ) -> List[str]:
        repaired: List[str] = []
        if state.base_env not in state.envs:
            metadata = EnvMetadata(
                name=state.base_env,
                provider=self.default_provider,
                source="recovered",
                created_at=utc_now_iso(),
            )
            state.envs[state.base_env] = metadata
            self._write_env_metadata(metadata)
            repaired.append("recreated base environment state")

        self._ensure_env_homes(state.base_env, wipe=False)
        if state.active_env not in state.envs:
            managed_candidates = {
                inspection.managed_env: provider
                for provider, inspection in inspections.items()
                if inspection.managed_env and self.paths.env_dir(inspection.managed_env).exists()
            }
            if len(managed_candidates) == 1:
                managed_env, provider = next(iter(managed_candidates.items()))
                self._reconcile_managed_state(state, managed_env, provider)
                state.active_env = managed_env
                repaired.append(f"adopted '{managed_env}' as the active environment")
            else:
                state.active_env = state.base_env
                repaired.append("reset active environment to 'base'")

        self._ensure_managed_homes(state)
        for host in self.hosts.values():
            target = self.backend.activate(self.paths, host, state.active_env)
            state.last_link_targets[host.name] = str(target)
            state.managed_home_paths[host.name] = str(host.managed_home_path(self.paths))
            repaired.append(f"repointed {host.name} managed home to '{state.active_env}'")
        self._save_state(state)
        return repaired

    def _normalize_env_metadata(self, metadata: EnvMetadata) -> EnvMetadata:
        provider = metadata.provider if metadata.provider in self.hosts else self.default_provider
        imported_from = {
            provider_name: path
            for provider_name, path in metadata.imported_from.items()
            if provider_name in self.hosts and path
        }
        return EnvMetadata(
            name=metadata.name,
            provider=provider,
            source=metadata.source,
            created_at=metadata.created_at,
            cloned_from=metadata.cloned_from,
            imported_from=imported_from,
        )

    def _resolve_new_env_provider(
        self,
        provider: Optional[str],
        source_metadata: Optional[EnvMetadata],
    ) -> str:
        if provider is None:
            if source_metadata:
                return self._env_provider_from_metadata(source_metadata)
            return self.default_provider
        if provider not in self.hosts:
            supported = ", ".join(sorted(self.hosts))
            raise UserFacingError(f"unknown provider '{provider}'; choose one of: {supported}")
        return provider

    def _env_provider(self, state: PackagentState, env_name: str) -> str:
        metadata = state.envs.get(env_name)
        if metadata is None:
            return self.default_provider
        return self._env_provider_from_metadata(metadata)

    def _env_provider_from_metadata(self, metadata: EnvMetadata) -> str:
        if metadata.provider in self.hosts:
            return metadata.provider
        return self.default_provider
