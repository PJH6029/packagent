# Architecture

`packagent` is intentionally small and stdlib-first.

## Core pieces

- `CodexHost`: host-specific path rules for the managed home, currently
  `~/.codex` and `envs/<name>/.codex`
- `GlobalSymlinkBackend`: activation backend for the current macOS/global
  symlink model
- `LinuxNamespaceBackend`: activation backend that bind-mounts an env onto
  `~/.codex` inside a shell-local mount namespace
- `PackagentManager`: state loading, takeover, create/clone/remove, activation,
  status, and doctor workflows
- `shell.py`: generated bash/zsh shell hook plus shell command rendering for
  activation and deactivation

## State model

State lives in `~/.packagent-v1/state.json` and records:

- default bootstrap environment
- known environments
- backup history from first-run takeover
- managed home metadata

Each environment also contains a small hidden metadata file at
`envs/<name>/.packagent-env.json`, plus a copy inside
`envs/<name>/.codex/.packagent-env.json` so Linux bind-mounted shells can infer
their current env from `~/.codex`.

On Linux, the current env is shell-local and comes from the namespace-mounted
`~/.codex`, not from persisted state. On macOS, the current env remains the
single global symlink target.

## First-run takeover

When activation happens for the first time, `packagent` inspects `~/.codex`:

- missing path: create the backend-specific managed home representation
- unmanaged directory: move it into `backups/<timestamp>/`, import it into
  `base`, then replace `~/.codex`
- unmanaged symlink: snapshot the resolved target into a backup, import that
  snapshot into `base`, then replace `~/.codex`
- already managed symlink: reconcile state and continue

The deactivated state is always `base`. On Linux, that means binding `base`
onto `~/.codex` in the current shell namespace. On macOS, that means repointing
the global symlink back to `base`.

## Shell model

User-facing shell setup is handled by `packagent init`, which detects `bash` or
`zsh` and writes a managed bootstrap block into the appropriate rc file. That
bootstrap block delegates to the lower-level `packagent shell init` hook.

The hook itself:

- wraps `packagent activate` and `packagent deactivate`
- evaluates shell code printed by the Python CLI
- bootstraps new shells to the default env, currently always `base`
- updates the shell prompt prefix
- exports `CODEX_HOME=~/.codex` and `PACKAGENT_BACKING_HOME=<env-home>`

On Linux, the hook first re-execs the shell into a packagent-managed user+mount
namespace, then mounts the default env on `~/.codex` before re-entering the
interactive shell. Future `activate` and `deactivate` commands mutate that
shell's namespace only.

On macOS, the hook keeps the existing global behavior and mirrors the current
symlink target into shell variables.

Direct `packagent activate` calls fail unless the shell hook is being used.

## Extension seams

The code already separates:

- host-specific behavior through `HostAdapter`
- activation strategy through `ActivationBackend`

That keeps a later `ClaudeHost` or a future per-shell backend from forcing a
rewrite of the manager or state model.
