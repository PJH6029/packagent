# Architecture

`packagent` is intentionally small and stdlib-first.

## Core pieces

- `CodexHost`: host-specific target rules for the managed Codex user layer,
  currently `codex-home` (`CODEX_HOME` when it is already set, otherwise
  `~/.codex`, plus `envs/<name>/.codex`) and `agents-home` (`~/.agents`, plus
  `envs/<name>/.agents`)
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
- backup history from first-run takeover
- managed target metadata

Each environment also contains a small hidden metadata file at
`envs/<name>/.packagent-env.json`. Existing version 1 state is migrated to
schema version 2 by adding `managed_targets` for `codex-home` and `agents-home`
while keeping legacy primary-target fields populated for compatibility.

## First-run takeover

When activation happens for the first time, `packagent` inspects every managed
target path: the Codex home path (`CODEX_HOME` when set, otherwise `~/.codex`)
and `~/.agents`:

- missing path: create a managed symlink
- unmanaged directory: move it into `backups/<timestamp>/`, import it into
  the matching `base` target, then replace the managed target path
- unmanaged symlink: snapshot the resolved target into a backup, import that
  snapshot into the matching `base` target, then replace the managed target path
- already managed symlink: reconcile state and continue

Activation preflights all targets before writing symlinks, then repoints all
managed target paths to the selected environment. The deactivated state is
always `base`.

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

The shell hook does not export `.agents` paths. Tools that read `~/.agents`
continue to use that stable path, which `packagent` switches at activation time.

Direct `packagent activate` calls fail unless the shell hook is being used.

## Extension seams

The code already separates:

- host-specific behavior through `HostAdapter`
- activation strategy through `ActivationBackend`

The current product boundary is still Codex-only. The multi-target model keeps a
later `ClaudeHost`, `GeminiHost`, or future per-shell backend from forcing a
rewrite of the manager or state model.
