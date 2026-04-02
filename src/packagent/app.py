from __future__ import annotations

from pathlib import Path
import json
import shutil
from typing import Dict, List, Optional

from packagent.activation import (
    ActivationBackend,
    GlobalSymlinkBackend,
    HOME_KIND_BROKEN_MANAGED,
    HOME_KIND_MANAGED,
    HOME_KIND_MISSING,
    HOME_KIND_UNMANAGED_DIRECTORY,
    HOME_KIND_UNMANAGED_FILE,
    HOME_KIND_UNMANAGED_SYMLINK,
)
from packagent.errors import UserFacingError
from packagent.hosts import CodexHost, HostAdapter
from packagent.locking import mutation_lock
from packagent.models import (
    ActivationResult,
    BackupRecord,
    DoctorReport,
    EnvMetadata,
    PackagentState,
    StatusReport,
)
from packagent.paths import PackagentPaths
from packagent.util import copy_directory, remove_path, timestamp_slug, utc_now_iso, write_json
from packagent.validation import validate_env_name


class PackagentManager:
    def __init__(
        self,
        paths: Optional[PackagentPaths] = None,
        host: Optional[HostAdapter] = None,
        backend: Optional[ActivationBackend] = None,
    ) -> None:
        self.paths = paths or PackagentPaths.discover()
        self.host = host or CodexHost()
        self.backend = backend or GlobalSymlinkBackend()

    def create_env(self, name: str, clone_from: Optional[str] = None) -> EnvMetadata:
        validate_env_name(name)
        with mutation_lock(self.paths):
            state = self._ensure_state()
            if name in state.envs:
                raise UserFacingError(f"environment '{name}' already exists")
            if clone_from:
                source_name = validate_env_name(clone_from, allow_base=True)
                if source_name not in state.envs:
                    raise UserFacingError(f"environment '{source_name}' does not exist")
                self._clone_env(state, source_name, name)
                metadata = EnvMetadata(
                    name=name,
                    host=self.host.name,
                    source="cloned",
                    created_at=utc_now_iso(),
                    cloned_from=source_name,
                )
            else:
                self._ensure_env_home(name, wipe=True)
                metadata = EnvMetadata(
                    name=name,
                    host=self.host.name,
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
            self._ensure_managed_home(state)
            target = self.backend.activate(self.paths, self.host, env_name)
            state.active_env = env_name
            state.last_link_target = str(target)
            self._save_state(state)
            return ActivationResult(
                env_name=env_name,
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                codex_home=str(target),
            )

    def deactivate_env(self) -> ActivationResult:
        with mutation_lock(self.paths):
            state = self._ensure_state()
            self._ensure_managed_home(state)
            target = self.backend.activate(self.paths, self.host, state.base_env)
            state.active_env = state.base_env
            state.last_link_target = str(target)
            self._save_state(state)
            return ActivationResult(
                env_name=state.base_env,
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                codex_home=str(target),
            )

    def list_envs(self) -> List[Dict[str, str]]:
        state = self._ensure_state()
        rows: List[Dict[str, str]] = []
        for name in sorted(state.envs):
            rows.append(
                {
                    "name": name,
                    "active": "true" if name == state.active_env else "false",
                    "path": str(self.paths.env_dir(name)),
                },
            )
        return rows

    def status(self) -> StatusReport:
        state = self._ensure_state()
        inspection = self.backend.inspect(self.paths, self.host)
        expected_target = str(self.backend.expected_target(self.paths, self.host, state.active_env))
        return StatusReport(
            active_env=state.active_env,
            managed=inspection.kind in {HOME_KIND_MANAGED, HOME_KIND_BROKEN_MANAGED},
            managed_home_path=str(self.host.managed_home_path(self.paths)),
            home_kind=inspection.kind,
            home_target=inspection.resolved_target,
            expected_target=expected_target,
        )

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
            inspection = self.backend.inspect(self.paths, self.host)
            issues = self._collect_doctor_issues(state, inspection.kind, inspection.managed_env, inspection.resolved_target)
            repaired: List[str] = []
            if fix and issues:
                repaired.extend(self._repair_state_and_home(state, inspection))
                inspection = self.backend.inspect(self.paths, self.host)
                issues = self._collect_doctor_issues(
                    state,
                    inspection.kind,
                    inspection.managed_env,
                    inspection.resolved_target,
                )
            status = StatusReport(
                active_env=state.active_env,
                managed=inspection.kind in {HOME_KIND_MANAGED, HOME_KIND_BROKEN_MANAGED},
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                home_kind=inspection.kind,
                home_target=inspection.resolved_target,
                expected_target=str(self.backend.expected_target(self.paths, self.host, state.active_env)),
            )
            return DoctorReport(status=status, issues=issues, repaired=repaired)

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
                schema_version=1,
                host=self.host.name,
                base_env="base",
                active_env="base",
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                managed_root=str(self.paths.root),
            )
        changed = False
        if state.managed_home_path != str(self.host.managed_home_path(self.paths)):
            state.managed_home_path = str(self.host.managed_home_path(self.paths))
            changed = True
        if state.managed_root != str(self.paths.root):
            state.managed_root = str(self.paths.root)
            changed = True
        if state.base_env not in state.envs:
            metadata = EnvMetadata(
                name=state.base_env,
                host=self.host.name,
                source="created",
                created_at=utc_now_iso(),
            )
            state.envs[state.base_env] = metadata
            self._ensure_env_home(state.base_env, wipe=False)
            self._write_env_metadata(metadata)
            changed = True
        else:
            self._ensure_env_home(state.base_env, wipe=False)
            self._write_env_metadata(state.envs[state.base_env])
        for env_name, metadata in list(state.envs.items()):
            self._ensure_env_home(env_name, wipe=False)
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

    def _ensure_env_home(self, env_name: str, *, wipe: bool) -> Path:
        env_dir = self.paths.env_dir(env_name)
        if wipe and env_dir.exists():
            shutil.rmtree(env_dir)
        env_dir.mkdir(parents=True, exist_ok=True)
        env_home = self.host.env_home_path(self.paths, env_name)
        env_home.mkdir(parents=True, exist_ok=True)
        return env_home

    def _clone_env(self, state: PackagentState, source_name: str, target_name: str) -> None:
        source_dir = self.paths.env_dir(source_name)
        target_dir = self.paths.env_dir(target_name)
        if target_dir.exists():
            raise UserFacingError(f"target environment '{target_name}' already exists")
        copy_directory(source_dir, target_dir)
        metadata_path = self.paths.env_metadata_file(target_name)
        if metadata_path.exists():
            metadata_path.unlink()

    def _ensure_managed_home(self, state: PackagentState) -> None:
        inspection = self.backend.inspect(self.paths, self.host)
        if inspection.kind == HOME_KIND_MISSING:
            return
        if inspection.kind == HOME_KIND_MANAGED:
            self._reconcile_managed_state(state, inspection.managed_env)
            return
        if inspection.kind == HOME_KIND_BROKEN_MANAGED:
            if inspection.managed_env:
                self._reconcile_managed_state(state, inspection.managed_env)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_DIRECTORY:
            self._import_directory_home(state)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_SYMLINK:
            self._import_symlink_home(state, inspection)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_FILE:
            self._backup_file_home(state)
            return
        raise UserFacingError(f"cannot handle home state '{inspection.kind}'")

    def _reconcile_managed_state(self, state: PackagentState, managed_env: Optional[str]) -> None:
        if not managed_env:
            return
        if managed_env not in state.envs:
            metadata = EnvMetadata(
                name=managed_env,
                host=self.host.name,
                source="recovered",
                created_at=utc_now_iso(),
            )
            state.envs[managed_env] = metadata
            self._write_env_metadata(metadata)
        if state.active_env not in state.envs:
            state.active_env = managed_env

    def _import_directory_home(self, state: PackagentState) -> None:
        home_path = self.host.managed_home_path(self.paths)
        backup_root = self._allocate_backup_dir()
        backup_home = backup_root / self.host.home_dir_name
        shutil.move(str(home_path), str(backup_home))
        self._replace_base_home_with_snapshot(backup_home)
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_directory",
                backup_path=str(backup_root),
                original_home=str(home_path),
            ),
        )
        self._mark_base_imported(state, str(backup_root))

    def _import_symlink_home(self, state: PackagentState, inspection) -> None:
        home_path = self.host.managed_home_path(self.paths)
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
                "raw_target": inspection.raw_target,
                "resolved_target": inspection.resolved_target,
            },
        )
        self._replace_base_home_with_snapshot(snapshot_dir)
        home_path.unlink()
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_symlink",
                backup_path=str(backup_root),
                original_home=str(home_path),
                original_target=inspection.raw_target,
            ),
        )
        self._mark_base_imported(state, str(backup_root))

    def _backup_file_home(self, state: PackagentState) -> None:
        home_path = self.host.managed_home_path(self.paths)
        backup_root = self._allocate_backup_dir()
        shutil.move(str(home_path), str(backup_root / "unexpected-home-file"))
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_file",
                backup_path=str(backup_root),
                original_home=str(home_path),
            ),
        )

    def _replace_base_home_with_snapshot(self, snapshot_dir: Path) -> None:
        base_home = self.host.env_home_path(self.paths, "base")
        if base_home.exists():
            shutil.rmtree(base_home)
        copy_directory(snapshot_dir, base_home)

    def _mark_base_imported(self, state: PackagentState, backup_root: str) -> None:
        base_metadata = EnvMetadata(
            name=state.base_env,
            host=self.host.name,
            source="imported-home",
            created_at=state.envs[state.base_env].created_at,
            imported_from=backup_root,
        )
        state.envs[state.base_env] = base_metadata
        self._write_env_metadata(base_metadata)

    def _allocate_backup_dir(self) -> Path:
        candidate = self.paths.backups_root / timestamp_slug()
        suffix = 1
        while candidate.exists():
            candidate = self.paths.backups_root / f"{timestamp_slug()}-{suffix}"
            suffix += 1
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate

    def _collect_doctor_issues(
        self,
        state: PackagentState,
        home_kind: str,
        managed_env: Optional[str],
        home_target: Optional[str],
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
        if home_kind == HOME_KIND_MISSING:
            issues.append(f"{self.host.managed_home_path(self.paths)} is not managed yet")
        elif home_kind == HOME_KIND_UNMANAGED_DIRECTORY:
            issues.append(f"{self.host.managed_home_path(self.paths)} is an unmanaged directory")
        elif home_kind == HOME_KIND_UNMANAGED_SYMLINK:
            issues.append(f"{self.host.managed_home_path(self.paths)} is an unmanaged symlink")
        elif home_kind == HOME_KIND_UNMANAGED_FILE:
            issues.append(f"{self.host.managed_home_path(self.paths)} is an unmanaged file")
        elif home_kind == HOME_KIND_BROKEN_MANAGED:
            issues.append(f"{self.host.managed_home_path(self.paths)} points to a missing managed target")
        elif home_kind == HOME_KIND_MANAGED:
            expected_target = str(self.backend.expected_target(self.paths, self.host, state.active_env))
            if home_target != expected_target:
                if managed_env != state.active_env:
                    issues.append(
                        f"managed symlink points to '{managed_env}' while state expects '{state.active_env}'",
                    )
                else:
                    issues.append("managed symlink target does not match the expected active environment path")
        return issues

    def _repair_state_and_home(self, state: PackagentState, inspection) -> List[str]:
        repaired: List[str] = []
        if state.base_env not in state.envs:
            metadata = EnvMetadata(
                name=state.base_env,
                host=self.host.name,
                source="recovered",
                created_at=utc_now_iso(),
            )
            state.envs[state.base_env] = metadata
            self._write_env_metadata(metadata)
            repaired.append("recreated base environment state")
        self._ensure_env_home(state.base_env, wipe=False)
        if state.active_env not in state.envs:
            if inspection.managed_env and self.paths.env_dir(inspection.managed_env).exists():
                self._reconcile_managed_state(state, inspection.managed_env)
                state.active_env = inspection.managed_env
                repaired.append(f"adopted '{inspection.managed_env}' as the active environment")
            else:
                state.active_env = state.base_env
                repaired.append("reset active environment to 'base'")
        self._ensure_managed_home(state)
        target = self.backend.activate(self.paths, self.host, state.active_env)
        state.last_link_target = str(target)
        self._save_state(state)
        repaired.append(f"repointed managed home to '{state.active_env}'")
        return repaired
