from __future__ import annotations

from dataclasses import dataclass
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
    HomeInspection,
)
from packagent.errors import UserFacingError
from packagent.hosts import CodexHost, HostAdapter, ManagedTarget
from packagent.locking import mutation_lock
from packagent.models import (
    ActivationResult,
    BackupRecord,
    DoctorReport,
    EnvMetadata,
    ManagedTargetState,
    PackagentState,
    StatusReport,
    TargetStatusReport,
    TargetUninstallResult,
    UninstallResult,
)
from packagent.paths import PackagentPaths
from packagent.util import copy_directory, remove_path, timestamp_slug, utc_now_iso, write_json
from packagent.validation import validate_env_name


BASE_MODE_FRESH = "fresh"
BASE_MODE_IMPORT = "import"
BASE_MODES = (BASE_MODE_IMPORT, BASE_MODE_FRESH)
RESTORE_SOURCE_BASE = "base"
RESTORE_SOURCE_BACKUP = "backup"
RESTORE_SOURCES = (RESTORE_SOURCE_BASE, RESTORE_SOURCE_BACKUP)
TAKEOVER_BACKUP_REASONS = {
    "takeover_directory",
    "takeover_symlink",
    "takeover_file",
}
FRESH_BACKUP_REASONS = {
    "fresh_base_directory",
    "fresh_base_symlink",
    "fresh_base_file",
}
BACKUP_REASONS_BY_MODE = {
    BASE_MODE_IMPORT: TAKEOVER_BACKUP_REASONS,
    BASE_MODE_FRESH: FRESH_BACKUP_REASONS,
}
UNMANAGED_HOME_KINDS = {
    HOME_KIND_UNMANAGED_DIRECTORY,
    HOME_KIND_UNMANAGED_FILE,
    HOME_KIND_UNMANAGED_SYMLINK,
}


@dataclass
class _TargetRestorePlan:
    target: ManagedTarget
    destination: Path
    action: str
    restore_source: str
    source_path: Optional[Path] = None
    staging_path: Optional[Path] = None


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
                self._ensure_env_targets(name, wipe=False)
                metadata = EnvMetadata(
                    name=name,
                    host=self.host.name,
                    source="cloned",
                    created_at=utc_now_iso(),
                    cloned_from=source_name,
                )
            else:
                self._ensure_env_targets(name, wipe=True)
                self._seed_shared_files_from_active_env(state, name)
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
            self._ensure_managed_targets(state)
            target_homes = self._activate_targets(env_name)
            primary_target = self.host.primary_target()
            primary_home = target_homes[primary_target.key]
            state.active_env = env_name
            self._record_target_links(state, target_homes)
            self._save_state(state)
            return ActivationResult(
                env_name=env_name,
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                codex_home=primary_home,
                target_homes=target_homes,
            )

    def deactivate_env(self) -> ActivationResult:
        with mutation_lock(self.paths):
            state = self._ensure_state()
            self._ensure_managed_targets(state)
            target_homes = self._activate_targets(state.base_env)
            primary_target = self.host.primary_target()
            primary_home = target_homes[primary_target.key]
            state.active_env = state.base_env
            self._record_target_links(state, target_homes)
            self._save_state(state)
            return ActivationResult(
                env_name=state.base_env,
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                codex_home=primary_home,
                target_homes=target_homes,
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
        return self._build_status_report(state)

    def base_init_prompt_needed(self) -> bool:
        with mutation_lock(self.paths):
            self._ensure_state()
            return any(
                inspection.kind in UNMANAGED_HOME_KINDS
                for inspection in self._inspect_targets().values()
            )

    def initialize_base(self, base_mode: str = BASE_MODE_IMPORT) -> ActivationResult:
        if base_mode not in BASE_MODES:
            raise UserFacingError(f"unsupported base mode '{base_mode}'")
        with mutation_lock(self.paths):
            state = self._ensure_state()
            state.init_base_mode = base_mode
            if base_mode == BASE_MODE_IMPORT:
                self._ensure_managed_targets(state)
            else:
                self._backup_unmanaged_targets_without_import(state)
            target_homes = self._activate_targets(state.base_env)
            primary_target = self.host.primary_target()
            primary_home = target_homes[primary_target.key]
            state.active_env = state.base_env
            self._record_target_links(state, target_homes)
            self._save_state(state)
            return ActivationResult(
                env_name=state.base_env,
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                codex_home=primary_home,
                target_homes=target_homes,
            )

    def uninstall_base_mode(self) -> str:
        with mutation_lock(self.paths):
            state = self._load_existing_state_for_uninstall()
            self._sync_state_targets(state)
            return self._infer_init_base_mode(state)

    def uninstall(self, restore_source: Optional[str] = None) -> UninstallResult:
        if restore_source is not None and restore_source not in RESTORE_SOURCES:
            raise UserFacingError(f"unsupported restore source '{restore_source}'")
        with mutation_lock(self.paths):
            state = self._load_existing_state_for_uninstall()
            self._sync_state_targets(state)
            base_mode = self._infer_init_base_mode(state)
            if base_mode == BASE_MODE_FRESH:
                if restore_source == RESTORE_SOURCE_BASE:
                    raise UserFacingError("fresh-mode init can only uninstall from backup")
                selected_restore_source = RESTORE_SOURCE_BACKUP
            else:
                if restore_source is None:
                    raise UserFacingError(
                        "import-mode uninstall requires --restore-source base or --restore-source backup",
                    )
                selected_restore_source = restore_source

            inspections = self._inspect_targets()
            self._preflight_uninstall_targets(state, inspections)
            plans = self._build_uninstall_plans(state, selected_restore_source, base_mode)
            self._stage_uninstall_plans(plans)
            target_results = self._apply_uninstall_plans(plans)

            state.active_env = state.base_env
            state.init_base_mode = base_mode
            state.last_link_target = None
            for target_state in state.managed_targets.values():
                target_state.last_link_target = None
            self._save_state(state)
            return UninstallResult(
                restore_source=selected_restore_source,
                target_results=target_results,
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
            inspections = self._inspect_targets()
            issues = self._collect_doctor_issues(state, inspections)
            repaired: List[str] = []
            if fix and issues:
                repaired.extend(self._repair_state_and_targets(state, inspections))
                inspections = self._inspect_targets()
                issues = self._collect_doctor_issues(state, inspections)
            return DoctorReport(status=self._build_status_report(state), issues=issues, repaired=repaired)

    def load_state(self) -> PackagentState:
        payload = json.loads(self.paths.state_file.read_text(encoding="utf-8"))
        return PackagentState.from_dict(payload)

    def _load_existing_state_for_uninstall(self) -> PackagentState:
        if not self.paths.state_file.exists():
            raise UserFacingError("packagent is not initialized")
        return self.load_state()

    def _infer_init_base_mode(self, state: PackagentState) -> str:
        if state.init_base_mode in BASE_MODES:
            return state.init_base_mode
        if any(record.reason in FRESH_BACKUP_REASONS for record in state.backups):
            return BASE_MODE_FRESH
        base_metadata = state.envs.get(state.base_env)
        if base_metadata and base_metadata.source == "imported-home":
            return BASE_MODE_IMPORT
        return BASE_MODE_IMPORT

    def _ensure_state(self) -> PackagentState:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.envs_root.mkdir(parents=True, exist_ok=True)
        self.paths.backups_root.mkdir(parents=True, exist_ok=True)
        if self.paths.state_file.exists():
            state = self.load_state()
        else:
            state = PackagentState(
                schema_version=2,
                host=self.host.name,
                base_env="base",
                active_env="base",
                managed_home_path=str(self.host.managed_home_path(self.paths)),
                managed_root=str(self.paths.root),
            )
        changed = False
        if self._sync_state_targets(state):
            changed = True
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
            self._ensure_env_targets(state.base_env, wipe=False)
            self._write_env_metadata(metadata)
            changed = True
        else:
            self._ensure_env_targets(state.base_env, wipe=False)
            self._write_env_metadata(state.envs[state.base_env])
        for env_name, metadata in list(state.envs.items()):
            self._ensure_env_targets(env_name, wipe=False)
            self._write_env_metadata(metadata)
        if state.active_env not in state.envs:
            state.active_env = state.base_env
            changed = True
        if self._sync_state_targets(state):
            changed = True
        if changed or not self.paths.state_file.exists():
            self._save_state(state)
        return state

    def _save_state(self, state: PackagentState) -> None:
        write_json(self.paths.state_file, state.to_dict())

    def _write_env_metadata(self, metadata: EnvMetadata) -> None:
        write_json(self.paths.env_metadata_file(metadata.name), metadata.to_dict())

    def _ensure_env_home(self, env_name: str, *, wipe: bool) -> Path:
        self._ensure_env_targets(env_name, wipe=wipe)
        return self.host.env_home_path(self.paths, env_name)

    def _ensure_env_targets(self, env_name: str, *, wipe: bool) -> None:
        env_dir = self.paths.env_dir(env_name)
        if wipe and env_dir.exists():
            shutil.rmtree(env_dir)
        env_dir.mkdir(parents=True, exist_ok=True)
        for target in self.host.targets:
            self.host.env_target_path(self.paths, env_name, target).mkdir(parents=True, exist_ok=True)

    def _sync_state_targets(self, state: PackagentState) -> bool:
        changed = False
        if state.schema_version != 2:
            state.schema_version = 2
            changed = True
        for target in self.host.targets:
            managed_path = str(self.host.managed_target_path(self.paths, target))
            target_state = state.managed_targets.get(target.key)
            if not target_state:
                last_link_target = state.last_link_target if target.primary else None
                target_state = ManagedTargetState(
                    key=target.key,
                    managed_home_path=managed_path,
                    last_link_target=last_link_target,
                )
                state.managed_targets[target.key] = target_state
                changed = True
            elif target_state.managed_home_path != managed_path:
                target_state.managed_home_path = managed_path
                changed = True
        known_target_keys = {target.key for target in self.host.targets}
        for key in list(state.managed_targets):
            if key not in known_target_keys:
                state.managed_targets.pop(key)
                changed = True
        primary_target = self.host.primary_target()
        primary_state = state.managed_targets[primary_target.key]
        if state.managed_home_path != primary_state.managed_home_path:
            state.managed_home_path = primary_state.managed_home_path
            changed = True
        if primary_state.last_link_target and state.last_link_target != primary_state.last_link_target:
            state.last_link_target = primary_state.last_link_target
            changed = True
        return changed

    def _clone_env(self, state: PackagentState, source_name: str, target_name: str) -> None:
        source_dir = self.paths.env_dir(source_name)
        target_dir = self.paths.env_dir(target_name)
        if target_dir.exists():
            raise UserFacingError(f"target environment '{target_name}' already exists")
        copy_directory(source_dir, target_dir)
        metadata_path = self.paths.env_metadata_file(target_name)
        if metadata_path.exists():
            metadata_path.unlink()

    def _seed_shared_files_from_active_env(self, state: PackagentState, target_name: str) -> None:
        source_name = state.active_env if state.active_env in state.envs else state.base_env
        for target in self.host.targets:
            source_target = self.host.env_target_path(self.paths, source_name, target)
            target_target = self.host.env_target_path(self.paths, target_name, target)
            for relative_name in target.shared_seed_files:
                source_file = source_target / relative_name
                if source_file.is_symlink() or not source_file.is_file():
                    continue
                target_file = target_target / relative_name
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_file)

    def _inspect_targets(self) -> Dict[str, HomeInspection]:
        return {
            target.key: self.backend.inspect(self.paths, self.host, target)
            for target in self.host.targets
        }

    def _build_status_report(self, state: PackagentState) -> StatusReport:
        target_statuses: List[TargetStatusReport] = []
        for target in self.host.targets:
            inspection = self.backend.inspect(self.paths, self.host, target)
            target_statuses.append(
                TargetStatusReport(
                    key=target.key,
                    managed=inspection.kind in {HOME_KIND_MANAGED, HOME_KIND_BROKEN_MANAGED},
                    managed_home_path=str(self.host.managed_target_path(self.paths, target)),
                    home_kind=inspection.kind,
                    home_target=inspection.resolved_target,
                    expected_target=str(
                        self.backend.expected_target(self.paths, self.host, state.active_env, target),
                    ),
                ),
            )
        primary_target = self.host.primary_target()
        primary_status = next(status for status in target_statuses if status.key == primary_target.key)
        return StatusReport(
            active_env=state.active_env,
            managed=primary_status.managed,
            managed_home_path=primary_status.managed_home_path,
            home_kind=primary_status.home_kind,
            home_target=primary_status.home_target,
            expected_target=primary_status.expected_target,
            target_statuses=target_statuses,
        )

    def _ensure_managed_targets(self, state: PackagentState) -> None:
        inspections = self._inspect_targets()
        self._preflight_managed_targets(inspections)
        for target in self.host.targets:
            self._ensure_managed_target(state, target, inspections[target.key])

    def _preflight_managed_targets(
        self,
        inspections: Dict[str, HomeInspection],
        *,
        action: str = "import",
    ) -> None:
        for target in self.host.targets:
            inspection = inspections[target.key]
            if inspection.kind != HOME_KIND_UNMANAGED_SYMLINK:
                continue
            home_path = self.host.managed_target_path(self.paths, target)
            if not inspection.resolved_target:
                raise UserFacingError(f"cannot {action} broken unmanaged symlink at {home_path}")
            resolved_target = Path(inspection.resolved_target)
            if not resolved_target.exists() or not resolved_target.is_dir():
                raise UserFacingError(
                    f"cannot {action} unmanaged symlink at {home_path}; resolved target is not a directory",
                )

    def _ensure_managed_target(
        self,
        state: PackagentState,
        target: ManagedTarget,
        inspection: HomeInspection,
    ) -> None:
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
            self._import_directory_target(state, target)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_SYMLINK:
            self._import_symlink_target(state, target, inspection)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_FILE:
            self._backup_file_target(state, target)
            return
        raise UserFacingError(f"cannot handle home state '{inspection.kind}'")

    def _backup_unmanaged_targets_without_import(self, state: PackagentState) -> None:
        inspections = self._inspect_targets()
        self._preflight_managed_targets(inspections, action="back up")
        for target in self.host.targets:
            self._backup_unmanaged_target_without_import(state, target, inspections[target.key])

    def _backup_unmanaged_target_without_import(
        self,
        state: PackagentState,
        target: ManagedTarget,
        inspection: HomeInspection,
    ) -> None:
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
            self._backup_directory_target_without_import(state, target)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_SYMLINK:
            self._backup_symlink_target_without_import(state, target, inspection)
            return
        if inspection.kind == HOME_KIND_UNMANAGED_FILE:
            self._backup_file_target_without_import(state, target)
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
        self._ensure_env_targets(managed_env, wipe=False)
        if state.active_env not in state.envs:
            state.active_env = managed_env

    def _activate_targets(self, env_name: str) -> Dict[str, str]:
        target_homes: Dict[str, str] = {}
        for target in self.host.targets:
            activated = self.backend.activate(self.paths, self.host, env_name, target)
            target_homes[target.key] = str(activated)
        return target_homes

    def _record_target_links(self, state: PackagentState, target_homes: Dict[str, str]) -> None:
        for target in self.host.targets:
            target_state = state.managed_targets[target.key]
            target_state.last_link_target = target_homes[target.key]
            if target.primary:
                state.last_link_target = target_homes[target.key]

    def _preflight_uninstall_targets(
        self,
        state: PackagentState,
        inspections: Dict[str, HomeInspection],
    ) -> None:
        if state.active_env not in state.envs:
            raise UserFacingError(
                f"active environment '{state.active_env}' is missing from state; run 'packagent doctor --fix' first",
            )
        for target in self.host.targets:
            inspection = inspections[target.key]
            home_path = self.host.managed_target_path(self.paths, target)
            if inspection.kind != HOME_KIND_MANAGED:
                raise UserFacingError(
                    f"cannot uninstall while {home_path} is {inspection.kind}; run 'packagent doctor --fix' or restore it manually",
                )
            expected_target = str(
                self.backend.expected_target(self.paths, self.host, state.active_env, target),
            )
            if inspection.resolved_target != expected_target:
                raise UserFacingError(
                    f"cannot uninstall while {home_path} points to {inspection.resolved_target}; expected {expected_target}; run 'packagent doctor --fix' first",
                )

    def _build_uninstall_plans(
        self,
        state: PackagentState,
        restore_source: str,
        base_mode: str,
    ) -> List[_TargetRestorePlan]:
        plans: List[_TargetRestorePlan] = []
        for target in self.host.targets:
            destination = self.host.managed_target_path(self.paths, target)
            if restore_source == RESTORE_SOURCE_BASE:
                source_path = self.host.env_target_path(self.paths, state.base_env, target)
                if not source_path.exists() or not source_path.is_dir():
                    raise UserFacingError(
                        f"cannot restore {target.key} from base; missing base target at {source_path}",
                    )
                plans.append(
                    _TargetRestorePlan(
                        target=target,
                        destination=destination,
                        action="restored",
                        restore_source=restore_source,
                        source_path=source_path,
                    ),
                )
                continue

            source_path = self._backup_source_for_target(state, target, base_mode)
            if source_path is None:
                plans.append(
                    _TargetRestorePlan(
                        target=target,
                        destination=destination,
                        action="removed",
                        restore_source=restore_source,
                    ),
                )
                continue
            plans.append(
                _TargetRestorePlan(
                    target=target,
                    destination=destination,
                    action="restored",
                    restore_source=restore_source,
                    source_path=source_path,
                ),
            )
        return plans

    def _backup_source_for_target(
        self,
        state: PackagentState,
        target: ManagedTarget,
        base_mode: str,
    ) -> Optional[Path]:
        backup_reasons = BACKUP_REASONS_BY_MODE[base_mode]
        for record in state.backups:
            if record.reason not in backup_reasons:
                continue
            if not self._backup_record_matches_target(record, target):
                continue
            source_path = self._backup_snapshot_path(record, target)
            if source_path.exists() or source_path.is_symlink():
                return source_path
            raise UserFacingError(
                f"backup for {target.key} is recorded but missing at {source_path}",
            )
        return None

    def _backup_record_matches_target(self, record: BackupRecord, target: ManagedTarget) -> bool:
        if record.target_key:
            return record.target_key == target.key
        current_home = self.host.managed_target_path(self.paths, target)
        if record.original_home == str(current_home):
            return True
        if Path(record.original_home).name == target.home_dir_name:
            return True
        backup_root = Path(record.backup_path)
        return record.reason.endswith("_directory") and (backup_root / target.home_dir_name).exists()

    def _backup_snapshot_path(self, record: BackupRecord, target: ManagedTarget) -> Path:
        backup_root = Path(record.backup_path)
        if record.reason.endswith("_directory"):
            return backup_root / target.home_dir_name
        if record.reason.endswith("_symlink"):
            return backup_root / "resolved-home"
        if record.reason.endswith("_file"):
            return backup_root / "unexpected-home-file"
        raise UserFacingError(f"unsupported backup reason '{record.reason}'")

    def _stage_uninstall_plans(self, plans: List[_TargetRestorePlan]) -> None:
        staged_paths: List[Path] = []
        try:
            for plan in plans:
                if plan.source_path is None:
                    continue
                plan.staging_path = self._stage_restore_source(plan.source_path, plan.destination)
                staged_paths.append(plan.staging_path)
        except Exception:
            for staged_path in staged_paths:
                remove_path(staged_path)
            raise

    def _stage_restore_source(self, source_path: Path, destination: Path) -> Path:
        staging_path = self._allocate_restore_staging_path(destination)
        if source_path.is_dir() and not source_path.is_symlink():
            copy_directory(source_path, staging_path)
        else:
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, staging_path, follow_symlinks=False)
        return staging_path

    def _allocate_restore_staging_path(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidate = destination.parent / f".{destination.name}.packagent-uninstall-{timestamp_slug()}"
        suffix = 1
        while candidate.exists() or candidate.is_symlink():
            candidate = destination.parent / f".{destination.name}.packagent-uninstall-{timestamp_slug()}-{suffix}"
            suffix += 1
        return candidate

    def _apply_uninstall_plans(self, plans: List[_TargetRestorePlan]) -> List[TargetUninstallResult]:
        target_results: List[TargetUninstallResult] = []
        try:
            for plan in plans:
                if plan.destination.is_symlink() or plan.destination.is_file():
                    plan.destination.unlink()
                elif plan.destination.exists():
                    raise UserFacingError(
                        f"cannot uninstall because {plan.destination} is no longer a managed symlink",
                    )
                if plan.staging_path is not None:
                    plan.staging_path.rename(plan.destination)
                    plan.staging_path = None
                target_results.append(
                    TargetUninstallResult(
                        key=plan.target.key,
                        managed_home_path=str(plan.destination),
                        action=plan.action,
                        restore_source=plan.restore_source,
                        source_path=str(plan.source_path) if plan.source_path else None,
                    ),
                )
        finally:
            for plan in plans:
                if plan.staging_path is not None and (
                    plan.staging_path.exists() or plan.staging_path.is_symlink()
                ):
                    remove_path(plan.staging_path)
        return target_results

    def _import_directory_target(self, state: PackagentState, target: ManagedTarget) -> None:
        home_path = self.host.managed_target_path(self.paths, target)
        backup_root = self._allocate_backup_dir()
        backup_home = backup_root / target.home_dir_name
        shutil.move(str(home_path), str(backup_home))
        self._replace_base_target_with_snapshot(target, backup_home)
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_directory",
                backup_path=str(backup_root),
                original_home=str(home_path),
                target_key=target.key,
            ),
        )
        self._mark_base_imported(state, str(backup_root))

    def _backup_directory_target_without_import(
        self,
        state: PackagentState,
        target: ManagedTarget,
    ) -> None:
        home_path = self.host.managed_target_path(self.paths, target)
        backup_root = self._allocate_backup_dir()
        backup_home = backup_root / target.home_dir_name
        shutil.move(str(home_path), str(backup_home))
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="fresh_base_directory",
                backup_path=str(backup_root),
                original_home=str(home_path),
                target_key=target.key,
            ),
        )
        state.init_base_mode = BASE_MODE_FRESH

    def _import_symlink_target(
        self,
        state: PackagentState,
        target: ManagedTarget,
        inspection: HomeInspection,
    ) -> None:
        home_path = self.host.managed_target_path(self.paths, target)
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
        self._replace_base_target_with_snapshot(target, snapshot_dir)
        home_path.unlink()
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_symlink",
                backup_path=str(backup_root),
                original_home=str(home_path),
                original_target=inspection.raw_target,
                target_key=target.key,
            ),
        )
        self._mark_base_imported(state, str(backup_root))

    def _backup_symlink_target_without_import(
        self,
        state: PackagentState,
        target: ManagedTarget,
        inspection: HomeInspection,
    ) -> None:
        home_path = self.host.managed_target_path(self.paths, target)
        if not inspection.resolved_target:
            raise UserFacingError(f"cannot back up broken unmanaged symlink at {home_path}")
        resolved_target = Path(inspection.resolved_target)
        if not resolved_target.exists() or not resolved_target.is_dir():
            raise UserFacingError(
                f"cannot back up unmanaged symlink at {home_path}; resolved target is not a directory",
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
        home_path.unlink()
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="fresh_base_symlink",
                backup_path=str(backup_root),
                original_home=str(home_path),
                original_target=inspection.raw_target,
                target_key=target.key,
            ),
        )
        state.init_base_mode = BASE_MODE_FRESH

    def _backup_file_target(self, state: PackagentState, target: ManagedTarget) -> None:
        home_path = self.host.managed_target_path(self.paths, target)
        backup_root = self._allocate_backup_dir()
        shutil.move(str(home_path), str(backup_root / "unexpected-home-file"))
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="takeover_file",
                backup_path=str(backup_root),
                original_home=str(home_path),
                target_key=target.key,
            ),
        )
        state.init_base_mode = BASE_MODE_IMPORT

    def _backup_file_target_without_import(self, state: PackagentState, target: ManagedTarget) -> None:
        home_path = self.host.managed_target_path(self.paths, target)
        backup_root = self._allocate_backup_dir()
        shutil.move(str(home_path), str(backup_root / "unexpected-home-file"))
        state.backups.append(
            BackupRecord(
                created_at=utc_now_iso(),
                reason="fresh_base_file",
                backup_path=str(backup_root),
                original_home=str(home_path),
                target_key=target.key,
            ),
        )
        state.init_base_mode = BASE_MODE_FRESH

    def _replace_base_target_with_snapshot(self, target: ManagedTarget, snapshot_dir: Path) -> None:
        base_target = self.host.env_target_path(self.paths, "base", target)
        if base_target.exists():
            shutil.rmtree(base_target)
        copy_directory(snapshot_dir, base_target)

    def _mark_base_imported(self, state: PackagentState, backup_root: str) -> None:
        current_metadata = state.envs[state.base_env]
        base_metadata = EnvMetadata(
            name=state.base_env,
            host=self.host.name,
            source="imported-home",
            created_at=current_metadata.created_at,
            imported_from=current_metadata.imported_from or backup_root,
        )
        state.envs[state.base_env] = base_metadata
        state.init_base_mode = BASE_MODE_IMPORT
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
        inspections: Dict[str, HomeInspection],
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
        for target in self.host.targets:
            issues.extend(self._collect_target_doctor_issues(state, target, inspections[target.key]))
        return issues

    def _collect_target_doctor_issues(
        self,
        state: PackagentState,
        target: ManagedTarget,
        inspection: HomeInspection,
    ) -> List[str]:
        issues: List[str] = []
        prefix = "" if target.primary else f"{target.key}: "
        managed_home = self.host.managed_target_path(self.paths, target)
        if inspection.kind == HOME_KIND_MISSING:
            issues.append(f"{prefix}{managed_home} is not managed yet")
        elif inspection.kind == HOME_KIND_UNMANAGED_DIRECTORY:
            issues.append(f"{prefix}{managed_home} is an unmanaged directory")
        elif inspection.kind == HOME_KIND_UNMANAGED_SYMLINK:
            issues.append(f"{prefix}{managed_home} is an unmanaged symlink")
        elif inspection.kind == HOME_KIND_UNMANAGED_FILE:
            issues.append(f"{prefix}{managed_home} is an unmanaged file")
        elif inspection.kind == HOME_KIND_BROKEN_MANAGED:
            issues.append(f"{prefix}{managed_home} points to a missing managed target")
        elif inspection.kind == HOME_KIND_MANAGED:
            expected_target = str(
                self.backend.expected_target(self.paths, self.host, state.active_env, target),
            )
            if inspection.resolved_target != expected_target:
                if inspection.managed_env != state.active_env:
                    issues.append(
                        f"{prefix}managed symlink points to '{inspection.managed_env}' while state expects '{state.active_env}'",
                    )
                else:
                    issues.append(
                        f"{prefix}managed symlink target does not match the expected active environment path",
                    )
        return issues

    def _repair_state_and_targets(
        self,
        state: PackagentState,
        inspections: Dict[str, HomeInspection],
    ) -> List[str]:
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
        self._ensure_env_targets(state.base_env, wipe=False)
        if state.active_env not in state.envs:
            adoption_candidate = self._managed_env_adoption_candidate(inspections)
            if adoption_candidate and self.paths.env_dir(adoption_candidate).exists():
                self._reconcile_managed_state(state, adoption_candidate)
                state.active_env = adoption_candidate
                repaired.append(f"adopted '{adoption_candidate}' as the active environment")
            else:
                state.active_env = state.base_env
                repaired.append("reset active environment to 'base'")
        self._ensure_env_targets(state.active_env, wipe=False)
        self._ensure_managed_targets(state)
        target_homes = self._activate_targets(state.active_env)
        self._record_target_links(state, target_homes)
        self._save_state(state)
        repaired.append(f"repointed managed targets to '{state.active_env}'")
        return repaired

    def _managed_env_adoption_candidate(self, inspections: Dict[str, HomeInspection]) -> Optional[str]:
        primary_target = self.host.primary_target()
        primary_inspection = inspections.get(primary_target.key)
        if primary_inspection and primary_inspection.managed_env:
            return primary_inspection.managed_env
        for inspection in inspections.values():
            if inspection.managed_env:
                return inspection.managed_env
        return None
