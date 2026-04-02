from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PackagentPaths:
    home: Path
    root: Path
    envs_root: Path
    backups_root: Path
    state_file: Path
    lock_file: Path

    @classmethod
    def discover(cls, home: Optional[Path] = None) -> "PackagentPaths":
        user_home = Path(home).expanduser() if home else Path.home()
        root = user_home / ".packagent-v1"
        return cls(
            home=user_home,
            root=root,
            envs_root=root / "envs",
            backups_root=root / "backups",
            state_file=root / "state.json",
            lock_file=root / "state.lock",
        )

    def env_dir(self, name: str) -> Path:
        return self.envs_root / name

    def env_metadata_file(self, name: str) -> Path:
        return self.env_dir(name) / ".packagent-env.json"

