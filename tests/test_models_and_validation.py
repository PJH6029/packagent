from __future__ import annotations

import pytest

from packagent.models import BackupRecord, EnvMetadata, PackagentState
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
        base_env="base",
        active_env="work",
        managed_root="/tmp/home/.packagent-v1",
        managed_home_paths={
            "codex": "/tmp/home/.codex",
            "claude": "/tmp/home/.claude",
        },
        last_link_targets={
            "codex": "/tmp/home/.packagent-v1/envs/work/.codex",
            "claude": "/tmp/home/.packagent-v1/envs/work/.claude",
        },
        envs={
            "base": EnvMetadata(
                name="base",
                provider="codex",
                source="imported-home",
                created_at="2026-04-02T00:00:00Z",
                imported_from={
                    "codex": "/tmp/home/.packagent-v1/backups/20260402T000000Z",
                    "claude": "/tmp/home/.packagent-v1/backups/20260402T000100Z",
                },
            ),
            "work": EnvMetadata(
                name="work",
                provider="claude",
                source="cloned",
                created_at="2026-04-02T00:01:00Z",
                cloned_from="base",
            ),
        },
        backups=[
            BackupRecord(
                created_at="2026-04-02T00:00:00Z",
                reason="takeover_directory",
                backup_path="/tmp/home/.packagent-v1/backups/20260402T000000Z",
                original_home="/tmp/home/.codex",
                provider="codex",
            ),
            BackupRecord(
                created_at="2026-04-02T00:01:00Z",
                reason="takeover_directory",
                backup_path="/tmp/home/.packagent-v1/backups/20260402T000100Z",
                original_home="/tmp/home/.claude",
                provider="claude",
            ),
        ],
    )

    round_tripped = PackagentState.from_dict(state.to_dict())

    assert round_tripped == state


def test_state_migrates_v1_host_fields_to_provider_maps() -> None:
    payload = {
        "schema_version": 1,
        "host": "codex",
        "base_env": "base",
        "active_env": "work",
        "managed_home_path": "/tmp/home/.codex",
        "managed_root": "/tmp/home/.packagent-v1",
        "last_link_target": "/tmp/home/.packagent-v1/envs/work/.codex",
        "envs": {
            "base": {
                "name": "base",
                "host": "codex",
                "source": "imported-home",
                "created_at": "2026-04-02T00:00:00Z",
                "imported_from": "/tmp/home/.packagent-v1/backups/20260402T000000Z",
            },
            "work": {
                "name": "work",
                "host": "codex",
                "source": "created",
                "created_at": "2026-04-02T00:01:00Z",
            },
        },
        "backups": [
            {
                "created_at": "2026-04-02T00:00:00Z",
                "reason": "takeover_directory",
                "backup_path": "/tmp/home/.packagent-v1/backups/20260402T000000Z",
                "original_home": "/tmp/home/.codex",
            },
        ],
    }

    migrated = PackagentState.from_dict(payload)

    assert migrated.managed_home_paths == {"codex": "/tmp/home/.codex"}
    assert migrated.last_link_targets == {"codex": "/tmp/home/.packagent-v1/envs/work/.codex"}
    assert migrated.envs["base"].provider == "codex"
    assert migrated.envs["base"].imported_from == {
        "codex": "/tmp/home/.packagent-v1/backups/20260402T000000Z",
    }
    assert migrated.backups[0].provider == "codex"
