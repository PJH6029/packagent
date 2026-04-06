# Architecture

`packagent` is intentionally small and stdlib-first.

## Core pieces

- `CodexHost`: host-specific path rules for the managed home, currently
  `CODEX_HOME` when it is already set, otherwise `~/.codex`, plus
  `envs/<name>/.codex`
- `GlobalSymlinkBackend`: activation backend that points the managed Codex home
  path at one managed environment at a time
- `PackagentManager`: state loading, takeover, create/clone/remove, activation,
  status, and doctor workflows
- `shell.py`: generated bash/zsh shell hook plus shell command rendering for
  activation and deactivation

## State model

State lives in `~/.packagent-v1/state.json` and records:

- active environment
- known environments
- backup history from first-run takeover
- managed home metadata

Each environment also contains a small hidden metadata file at
`envs/<name>/.packagent-env.json`.

## First-run takeover

When activation happens for the first time, `packagent` inspects the managed
Codex home path (`CODEX_HOME` when set, otherwise `~/.codex`):

- missing path: create a managed symlink
- unmanaged directory: move it into `backups/<timestamp>/`, import it into
  `base`, then replace the managed home path
- unmanaged symlink: snapshot the resolved target into a backup, import that
  snapshot into `base`, then replace the managed home path
- already managed symlink: reconcile state and continue

The deactivated state is always `base`.

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

Direct `packagent activate` calls fail unless the shell hook is being used.

## Extension seams

The code already separates:

- host-specific behavior through `HostAdapter`
- activation strategy through `ActivationBackend`

That keeps a later `ClaudeHost` or a future per-shell backend from forcing a
rewrite of the manager or state model.
