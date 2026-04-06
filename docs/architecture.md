# Architecture

`packagent` is intentionally small and stdlib-first.

## Core pieces

- `HostAdapter`: provider-specific path rules for managed homes
- `CodexHost`: `CODEX_HOME` when it is already set, otherwise `~/.codex`
- `ClaudeHost`: `CLAUDE_CONFIG_DIR` when it is already set, otherwise
  `~/.claude`
- `GlobalSymlinkBackend`: activation backend that points each managed provider
  home path at one managed environment at a time
- `PackagentManager`: state loading, takeover, create/clone/remove, activation,
  status, and doctor workflows
- `shell.py`: generated bash/zsh shell hook plus shell command rendering for
  activation and deactivation

## State model

State lives in `~/.packagent-v1/state.json` and records:

- the active environment
- known environments and each environment's primary provider
- backup history from first-run takeover, keyed by provider
- managed home metadata per provider

Each environment also contains a small hidden metadata file at
`envs/<name>/.packagent-env.json`.

On disk, every environment contains one directory per supported provider:

- `envs/<name>/.codex`
- `envs/<name>/.claude`

The deactivated state is always `base`.

## First-run takeover

On activation, `packagent` inspects each managed provider home independently:

- Codex: `CODEX_HOME` when set, otherwise `~/.codex`
- Claude Code: `CLAUDE_CONFIG_DIR` when set, otherwise `~/.claude`

For each provider home:

- missing path: create a managed symlink on activation
- unmanaged directory: move it into `backups/<timestamp>/`, import it into
  `base`, then replace the managed home path
- unmanaged symlink: snapshot the resolved target into a backup, import that
  snapshot into `base`, then replace the managed home path
- already managed symlink: reconcile state and continue

## Shell model

User-facing shell setup is handled by `packagent init`, which detects `bash` or
`zsh` and writes a managed bootstrap block into the appropriate rc file. That
bootstrap block delegates to the lower-level `packagent shell init` hook.

The hook itself:

- wraps `packagent activate` and `packagent deactivate`
- evaluates shell code printed by the Python CLI
- bootstraps the shell to the manager's current active env, usually `base`
- updates the shell prompt prefix to `(provider:env)`
- leaves any existing `CODEX_HOME` or `CLAUDE_CONFIG_DIR` export untouched

Direct `packagent activate` calls fail unless the shell hook is being used.

`packagent` does not wrap or block the `codex` or `claude` commands. The active
environment repoints both managed homes, and the prompt shows the environment's
primary provider.

## Extension seams

The code separates:

- provider-specific behavior through `HostAdapter`
- activation strategy through `ActivationBackend`

That keeps later providers from forcing a rewrite of the manager or state
model, while preserving the v1 rule that only one global environment is active
at a time.
