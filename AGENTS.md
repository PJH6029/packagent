# AGENTS.md

This repository implements `packagent`, a small Python CLI for managing
isolated agent user-level home environments under `~/.packagent`.

These instructions apply to the entire repository.

## Project scope

- Keep v1 focused on **user-level agent home layers** only.
- Do not expand the current product to install harness packages, install native
  CLIs, or manage package registries unless the user explicitly asks for that
  scope change.
- Treat user-level `~/.codex`, `~/.agents`, and `~/.claude` support as the
  current product boundary. The code should stay ready for future hosts, but v1
  behavior should not quietly turn into a multi-provider implementation.
- Preserve the intentional v1 limitation that only **one globally active env**
  exists at a time through the managed user-level symlinks.

## Architecture expectations

- Runtime code lives in `src/packagent/`.
- Keep the runtime **stdlib-first**. Avoid adding runtime dependencies unless
  they remove substantial complexity and are clearly justified.
- Preserve the current separation of concerns:
  - `HostAdapter` for host-specific path rules
  - `ActivationBackend` for activation strategy
  - `PackagentManager` for stateful workflows
  - `shell.py` for generated shell integration
- Keep the on-disk contract stable unless the user explicitly asks to change it:
  - `~/.packagent/envs/<env>/.codex`
  - `~/.packagent/envs/<env>/.agents`
  - `~/.packagent/envs/<env>/.claude`
  - `~/.packagent/state.json`
  - `~/.codex`, `~/.agents`, and `~/.claude` as managed symlink targets for the
    active env
- When changing activation or takeover logic, preserve the safety model:
  - backup/import unmanaged homes before takeover
  - keep `base` as the permanent fallback environment
  - refuse destructive removal of `base` or the active environment
  - use the file lock around mutating operations

## Testing rules

- Use `pytest`.
- Add or update tests for any behavior change in:
  - takeover/import
  - activation/deactivation
  - doctor/repair
  - shell hook rendering
  - environment validation or state serialization
- Filesystem tests must use a temporary `HOME`. Never write tests that touch the
  real user `~/.codex`, `~/.agents`, `~/.claude`, or `~/.packagent`.
- For shell behavior, test both bash and zsh output when changing the shell hook
  contract.
- Treat the prepared Docker sandbox as the default end-to-end verification lane
  whenever user-facing CLI flows, shell integration, Docker setup, or harness
  onboarding behavior changes.
- When Docker is available and the change is substantial, run
  `./scripts/run_docker_sandbox.sh test` before finishing.
- In addition to the scripted Docker test, run an interactive Docker check with
  `./scripts/run_docker_sandbox.sh shell` when changing or regressing
  user-facing flows that depend on terminal interaction or real shell state,
  such as newly implemented or refactored `init`, activation/deactivation,
  shell bootstrap, prompt handling, backup/takeover prompts, deletion flows, or
  Docker onboarding behavior.
- Keep `scripts/e2e_in_docker.sh` aligned with the current supported workflow.
  Expand it when behavior changes so the Docker smoke test covers the relevant
  real-user path instead of relying only on unit tests.
- Docker scenarios should cover, as relevant to the change:
  - installing `packagent`
  - running `packagent init` / shell bootstrap
  - creating environments
  - activating an environment
  - switching between environments
  - verifying env isolation through writes under `~/.codex`, `~/.agents`, and
    `~/.claude`
  - deactivating back to `base`
  - removing non-active environments
  - doctor/repair behavior when link drift matters
  - uninstalling `packagent`
  - harness-style or npm-based setup steps when sandbox/tooling changes could
    affect them
- For exploratory debugging or manual scenario checks, prefer
  `./scripts/run_docker_sandbox.sh shell` over touching the real machine state.

## Development workflow

- Prefer Python for project work. Do not introduce Node/TS for the manager
  itself unless the user explicitly asks for a language shift.
- Prefer `uv` for package manager.
- Keep code compatible with Python 3.9+.
- Update docs when changing user-facing commands or guarantees:
  - `README.md` for usage and CLI behavior
  - `docs/architecture.md` for design changes
- Before finishing substantial code changes, run:
  - `./.venv/bin/pytest -q` if the local venv exists
  - `./.venv/bin/python -m build` when packaging behavior changes
  - `./scripts/run_docker_sandbox.sh test` when the change affects user-facing
    flows and Docker is available
  - `./scripts/run_docker_sandbox.sh shell` for an interactive smoke check when
    the change affects terminal-facing flows, prompts, or shell behavior
- On machines with older `pip`, editable installs may not work with the current
  `pyproject.toml` backend. In that case, use direct test-tool installation or a
  wheel install for smoke checks instead of rewriting packaging just to satisfy
  old editable-install behavior.

## Change hygiene

- Keep commits focused and use clear commit messages.
- Do not overwrite unrelated user changes in the worktree.
- When documenting limitations, be explicit that `packagent` does **not**
  isolate trusted repo-local `.codex/`, `.agents/`, or `.claude/` layers or
  repo/system instruction files that agent tools may also load.
