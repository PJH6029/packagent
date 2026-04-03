from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class EnvMetadata:
    name: str
    host: str
    source: str
    created_at: str
    cloned_from: Optional[str] = None
    imported_from: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "EnvMetadata":
        return cls(
            name=str(data["name"]),
            host=str(data["host"]),
            source=str(data["source"]),
            created_at=str(data["created_at"]),
            cloned_from=str(data["cloned_from"]) if data.get("cloned_from") else None,
            imported_from=str(data["imported_from"]) if data.get("imported_from") else None,
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class BackupRecord:
    created_at: str
    reason: str
    backup_path: str
    original_home: str
    original_target: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "BackupRecord":
        return cls(
            created_at=str(data["created_at"]),
            reason=str(data["reason"]),
            backup_path=str(data["backup_path"]),
            original_home=str(data["original_home"]),
            original_target=str(data["original_target"]) if data.get("original_target") else None,
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class PackagentState:
    schema_version: int
    host: str
    base_env: str
    default_env: str
    managed_home_path: str
    managed_root: str
    manager_name: str = "packagent-v1"
    last_link_target: Optional[str] = None
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
        return cls(
            schema_version=int(data["schema_version"]),
            host=str(data["host"]),
            base_env=str(data["base_env"]),
            default_env=str(data.get("default_env") or data.get("active_env") or "base"),
            managed_home_path=str(data["managed_home_path"]),
            managed_root=str(data["managed_root"]),
            manager_name=str(data.get("manager_name", "packagent-v1")),
            last_link_target=str(data["last_link_target"]) if data.get("last_link_target") else None,
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
    managed_home_path: str
    backing_home_path: str


@dataclass
class StatusReport:
    current_env: Optional[str]
    default_env: str
    managed: bool
    managed_home_path: str
    home_kind: str
    home_target: Optional[str]
    default_target: str


@dataclass
class DoctorReport:
    status: StatusReport
    issues: List[str] = field(default_factory=list)
    repaired: List[str] = field(default_factory=list)
