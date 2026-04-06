from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class EnvMetadata:
    name: str
    provider: str
    source: str
    created_at: str
    cloned_from: Optional[str] = None
    imported_from: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "EnvMetadata":
        imported_from_raw = data.get("imported_from")
        if isinstance(imported_from_raw, dict):
            imported_from = {str(name): str(path) for name, path in imported_from_raw.items()}
        elif imported_from_raw:
            imported_from = {"codex": str(imported_from_raw)}
        else:
            imported_from = {}
        return cls(
            name=str(data["name"]),
            provider=str(data.get("provider") or data.get("host") or "codex"),
            source=str(data["source"]),
            created_at=str(data["created_at"]),
            cloned_from=str(data["cloned_from"]) if data.get("cloned_from") else None,
            imported_from=imported_from,
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class BackupRecord:
    created_at: str
    reason: str
    backup_path: str
    original_home: str
    provider: str = "codex"
    original_target: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "BackupRecord":
        return cls(
            created_at=str(data["created_at"]),
            reason=str(data["reason"]),
            backup_path=str(data["backup_path"]),
            original_home=str(data["original_home"]),
            provider=str(data.get("provider") or "codex"),
            original_target=str(data["original_target"]) if data.get("original_target") else None,
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class PackagentState:
    schema_version: int
    base_env: str
    active_env: str
    managed_root: str
    manager_name: str = "packagent-v1"
    managed_home_paths: Dict[str, str] = field(default_factory=dict)
    last_link_targets: Dict[str, str] = field(default_factory=dict)
    envs: Dict[str, EnvMetadata] = field(default_factory=dict)
    backups: List[BackupRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "PackagentState":
        envs = {
            name: EnvMetadata.from_dict(value)
            for name, value in dict(data.get("envs", {})).items()
        }
        backups = [
            BackupRecord.from_dict(value)
            for value in list(data.get("backups", []))
        ]
        managed_home_paths_raw = data.get("managed_home_paths")
        if isinstance(managed_home_paths_raw, dict):
            managed_home_paths = {
                str(provider): str(path)
                for provider, path in managed_home_paths_raw.items()
            }
        else:
            managed_home_paths = {}
            if data.get("managed_home_path"):
                managed_home_paths[str(data.get("host") or "codex")] = str(data["managed_home_path"])
        last_link_targets_raw = data.get("last_link_targets")
        if isinstance(last_link_targets_raw, dict):
            last_link_targets = {
                str(provider): str(path)
                for provider, path in last_link_targets_raw.items()
            }
        else:
            last_link_targets = {}
            if data.get("last_link_target"):
                last_link_targets[str(data.get("host") or "codex")] = str(data["last_link_target"])
        return cls(
            schema_version=int(data["schema_version"]),
            base_env=str(data["base_env"]),
            active_env=str(data["active_env"]),
            managed_root=str(data["managed_root"]),
            manager_name=str(data.get("manager_name", "packagent-v1")),
            managed_home_paths=managed_home_paths,
            last_link_targets=last_link_targets,
            envs=envs,
            backups=backups,
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["envs"] = {name: metadata.to_dict() for name, metadata in self.envs.items()}
        payload["backups"] = [record.to_dict() for record in self.backups]
        return payload


@dataclass
class ActivationResult:
    env_name: str
    provider: str


@dataclass
class ProviderStatus:
    provider: str
    managed: bool
    managed_home_path: str
    home_kind: str
    home_target: Optional[str]
    expected_target: str


@dataclass
class StatusReport:
    active_env: str
    provider: str
    providers: Dict[str, ProviderStatus] = field(default_factory=dict)


@dataclass
class DoctorReport:
    status: StatusReport
    issues: List[str] = field(default_factory=list)
    repaired: List[str] = field(default_factory=list)
