# TEST_LOG

## Scope

Heavy interaction pass for packagent v1 init/uninstall and shell marker behavior.
All Docker checks were run in disposable containers with copied host configs, so
the real host `~/.codex`, `~/.agents`, `~/.claude`, and `~/.packagent` were not
mutated.

## Reproduced Bugs

### Bug 1: backup restore used stale first backup after re-init

Reproduced in `./scripts/run_docker_sandbox.sh shell bash`:

1. `uv tool install /workspace`
2. `packagent init --shell bash --base-mode import`
3. `source ~/.bashrc`
4. `packagent create -n omx --clone base`
5. `packagent uninstall --restore-source base --shell bash`
6. `packagent init --shell bash --base-mode import`
7. Removed the older backup root under `~/.packagent-backups`
8. `source ~/.bashrc`
9. `packagent activate omx`
10. `packagent uninstall --restore-source backup --shell bash`

Observed failure before the fix:

```text
backup for codex-home is recorded but missing at /home/tester/.packagent-backups/<old>/.codex
```

Fix:

- Added `PackagentState.current_backup_root`.
- Each takeover/import/fresh backup records the current backup root.
- Backup-mode uninstall only consults backup records from the current root.
- Targets absent from the current root are removed instead of being restored from
  an older backup generation.
- Re-import now updates `base.imported_from` to the latest backup root instead
  of preserving stale metadata.

### Bug 2: one shell kept an old marker after another shell activated base

Reproduced with two live bash PTYs in the Docker shell:

1. Shell 1 sourced `~/.bashrc`, created and activated `omx`.
2. Shell 2 started and correctly displayed `omx`.
3. Shell 2 ran `packagent activate base`.
4. Shell 1 refreshed its prompt and still displayed `omx`.
5. `packagent status` in Shell 1 reported `active_env=base`.

Fix:

- Added hidden CLI helper `packagent shell active-env`.
- The helper prints the active env only when all managed symlinks point to the
  state-recorded active env.
- Bash and zsh prompt refresh now call that helper before rendering the marker.
- The rc block exports `PACKAGENT_BIN`, so old shells can keep calling the same
  installed executable even when PATH setup used the `~/.local/bin` fallback.

### Bug 3: another shell kept the marker after uninstall

Reproduced with two live bash PTYs in the Docker shell:

1. Shell 1 ran `packagent uninstall --restore-source base --shell bash`.
2. Shell 1 cleared its marker.
3. Shell 2 refreshed its prompt and still displayed `(base)`.
4. `packagent status` in Shell 2 reported `managed=false` and
   `home_kind=unmanaged_directory`.

Fix:

- The same `packagent shell active-env` reconciliation clears prompt state when
  packagent is no longer managing the target symlinks.
- The existing uninstall wrapper still clears the current shell immediately;
  other shells clear on their next prompt refresh.

## Added Automated Coverage

- Unit regression tests for re-init backup restore with old backup roots deleted.
- Unit regression test that a target missing in the current backup generation is
  not resurrected from an older backup root.
- Unit and CLI tests for `shell_active_env` before activation, after activation,
  and after uninstall.
- Shell hook tests updated to cover prompt reconciliation through
  `shell active-env`.
- Docker smoke script now includes:
  - simultaneous live bash shells that switch `codex-with-demo -> base ->
    codex-with-demo` and verify both shells converge;
  - re-init plus backup restore after deleting all old backup roots except the
    current one;
  - re-init backup restore where `.agents` is absent from the current backup
    generation and must stay absent.

## Alternate Worktree Merge

Compared against `/home/top321902/code/packagent-v1-heavy-main`.

Merged:

- `packagent shell init` now bootstraps an initial marker only when the current
  managed symlinks actually match the recorded active env.
- The interactive uninstall prompt now describes `backup` as the current init
  backup snapshots.
- Added a shell-hook regression that changes the global active-env source while
  the local shell env is stale.
- Added Docker coverage for latest-generation missing targets.

Kept from this branch:

- Explicit `current_backup_root` in `state.json` instead of deriving the
  generation only from the latest matching backup record. This keeps the
  uninstall generation stable and migratable.
- `PACKAGENT_BIN` in the rc block and hook wrapper, so fallback executable
  resolution remains consistent across long-lived shells.

## Verification

- `./.venv/bin/pytest -q`
- `./scripts/run_docker_sandbox.sh test`
- Interactive Docker shell checks for:
  - the three reproduced bugs;
  - multi-shell prompt convergence after activation/deactivation;
  - prompt clearing in another shell after uninstall;
  - repeated import/fresh init and uninstall combinations.
