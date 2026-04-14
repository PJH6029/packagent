# Architecture

`packagent` is intentionally small and stdlib-first.

## Core pieces

- `CodexHost`: host-specific target rules for the managed user-level layer,
  currently `codex-home` (`CODEX_HOME` when it is already set, otherwise
  `~/.codex`, plus `envs/<name>/.codex`) and `agents-home` (`~/.agents`, plus
  `envs/<name>/.agents`) and `claude-home` (`CLAUDE_CONFIG_DIR` when it is
  already set, otherwise `~/.claude`, plus `envs/<name>/.claude`). It also
  records auth files that are safe to seed into newly created envs:
  `.codex/auth.json` and `.claude/.credentials.json`.
- `GlobalSymlinkBackend`: activation backend that points each managed target
  path at the same active managed environment
- `PackagentManager`: state loading, takeover, create/clone/remove, activation,
  status, and doctor workflows
- `shell.py`: generated bash/zsh shell hook plus shell command rendering for
  activation and deactivation

## State model

State lives in `~/.packagent/state.json` and records:

- active environment
- known environments
- init base mode, when recorded by current versions
- backup history from first-run takeover
- managed target metadata

Each environment also contains a small hidden metadata file at
`envs/<name>/.packagent-env.json`. Existing version 1 state is migrated to
schema version 2 by adding `managed_targets` for `codex-home`, `agents-home`,
and `claude-home` while keeping legacy primary-target fields populated for
compatibility.

## First-run takeover

When `packagent init` or first activation manages homes for the first time,
`packagent` inspects every managed target path: the Codex home path
(`CODEX_HOME` when set, otherwise `~/.codex`) and `~/.agents`, plus the Claude
home path (`CLAUDE_CONFIG_DIR` when set, otherwise `~/.claude`):

- missing path: create a managed symlink
- unmanaged directory: move it into `backups/<timestamp>/`, import it into
  the matching `base` target, then replace the managed target path
- unmanaged symlink: snapshot the resolved target into a backup, import that
  snapshot into the matching `base` target, then replace the managed target path
- already managed symlink: reconcile state and continue

Activation preflights all targets before writing symlinks, then repoints all
managed target paths to the selected environment. The deactivated state is
always `base`.

`packagent init --base-mode fresh` uses the same backup safety path, but does
not import the backed-up files into `base`. Interactive `init` prompts for the
base mode when unmanaged homes exist; non-interactive `init` defaults to
`import` for compatibility.

## Uninstall rollback

`packagent uninstall` is the inverse of user-level home takeover, not package
removal. It verifies every managed target is still the expected packagent
symlink for the recorded active env before changing anything. If a target has
drifted to an unmanaged path or a different managed env, uninstall refuses and
asks the user to repair or handle the drift first.

For import-mode installs, uninstall can restore either the copied `base` env or
the original backup snapshots. Interactive shells prompt for that choice;
non-interactive shells must pass `--restore-source base` or
`--restore-source backup`. For fresh-mode installs, uninstall always restores
backup snapshots. Targets that had no first-run backup are left absent in
backup mode. The command removes packagent's managed shell rc block but keeps
`~/.packagent` as recoverable data.

## Environment creation

`packagent create -n <env>` creates empty target directories, then copies only
host-declared auth seed files from the active env into the new env. Full
history, logs, caches, sessions, settings, skills, and plugins remain
env-specific. `packagent create -n <env> --clone <source-env>` keeps the older
full-copy behavior.

## Shell model

User-facing shell setup is handled by `packagent init`, which detects `bash` or
`zsh` and writes a managed bootstrap block into the appropriate rc file. That
bootstrap block delegates to the lower-level `packagent shell init` hook.

The hook itself:

- wraps `packagent activate` and `packagent deactivate`
- evaluates shell code printed by the Python CLI
- bootstraps the shell to the manager's current active env, usually `base`
- updates the shell prompt prefix
- leaves any existing `CODEX_HOME` export untouched
- leaves any existing `CLAUDE_CONFIG_DIR` export untouched

The shell hook does not export managed target paths. Tools that read `~/.agents`
or `~/.claude` continue to use those stable paths, which `packagent` switches at
activation time.

Direct `packagent activate` calls fail unless the shell hook is being used.

## Extension seams

The code already separates:

- host-specific behavior through `HostAdapter`
- activation strategy through `ActivationBackend`

The current product boundary is user-level target packaging for Codex, shared
agent files, and Claude. It intentionally does not add a provider selector:
`~/.codex`, `~/.agents`, and `~/.claude` move together with the one active env.
The multi-target model keeps a later `GeminiHost`, additional target, or future
per-shell backend from forcing a rewrite of the manager or state model.
