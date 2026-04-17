from __future__ import annotations

import pytest

from packagent.models import BackupRecord, EnvMetadata, ManagedTargetState, PackagentState
from packagent.validation import validate_env_name


def test_validate_env_name_accepts_expected_patterns() -> None:
    assert validate_env_name("codex-with-omx") == "codex-with-omx"
    assert validate_env_name("v1.2_env") == "v1.2_env"


@pytest.mark.parametrize("name", ["", ".", "..", "base", "bad/name", "-oops"])
def test_validate_env_name_rejects_invalid_names(name: str) -> None:
    with pytest.raises(Exception):
        validate_env_name(name)


def test_state_serialization_round_trip() -> None:
    state = PackagentState(
        schema_version=2,
        host="codex",
        base_env="base",
        active_env="work",
        managed_home_path="/tmp/home/.codex",
        managed_root="/tmp/home/.packagent",
        last_link_target="/tmp/home/.packagent/envs/work/.codex",
        init_base_mode="import",
        current_backup_root="/tmp/home/.packagent/backups/20260402T000000Z",
        envs={
            "base": EnvMetadata(
                name="base",
                host="codex",
                source="imported-home",
                created_at="2026-04-02T00:00:00Z",
                imported_from="/tmp/home/.packagent/backups/20260402T000000Z",
            ),
            "work": EnvMetadata(
                name="work",
                host="codex",
                source="cloned",
                created_at="2026-04-02T00:01:00Z",
                cloned_from="base",
            ),
        },
        backups=[
            BackupRecord(
                created_at="2026-04-02T00:00:00Z",
                reason="takeover_directory",
                backup_path="/tmp/home/.packagent/backups/20260402T000000Z",
                original_home="/tmp/home/.codex",
                target_key="codex-home",
            ),
        ],
        managed_targets={
            "codex-home": ManagedTargetState(
                key="codex-home",
                managed_home_path="/tmp/home/.codex",
                last_link_target="/tmp/home/.packagent/envs/work/.codex",
            ),
            "agents-home": ManagedTargetState(
                key="agents-home",
                managed_home_path="/tmp/home/.agents",
                last_link_target="/tmp/home/.packagent/envs/work/.agents",
            ),
            "claude-home": ManagedTargetState(
                key="claude-home",
                managed_home_path="/tmp/home/.claude",
                last_link_target="/tmp/home/.packagent/envs/work/.claude",
            ),
            "opencode-config-home": ManagedTargetState(
                key="opencode-config-home",
                managed_home_path="/tmp/home/.config/opencode",
                last_link_target="/tmp/home/.packagent/envs/work/.config/opencode",
            ),
            "opencode-data-home": ManagedTargetState(
                key="opencode-data-home",
                managed_home_path="/tmp/home/.local/share/opencode",
                last_link_target="/tmp/home/.packagent/envs/work/.local/share/opencode",
            ),
        },
    )

    round_tripped = PackagentState.from_dict(state.to_dict())

    assert round_tripped == state
