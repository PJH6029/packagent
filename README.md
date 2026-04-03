# packagent

`packagent` is a small Python CLI that isolates user-level Codex homes under
`~/.packagent-v1/envs/<env>/.codex` and switches the real `~/.codex` path with a
managed symlink. The goal is compatibility with harnesses that still assume a
single `~/.codex` home, while keeping those harness-specific files separated by
environment.

## Why this exists

Agent harnesses often install into `~/.codex` directly:

- `AGENTS.md`
- `skills/`
- prompts
- hooks
- MCP configuration
- other host-specific files

When multiple harnesses share the same user home, their files can leak across
sessions. `packagent` gives you a conda-like workflow for the Codex home layer.

It does not try to replace Codex, install harnesses for you, or isolate
repo-local `.codex/` files inside trusted projects.

## Install

Recommended:

```bash
uv tool install packagent
```

Fallback:

```bash
pipx install packagent
```

## Quick start

Install shell integration once:

```bash
packagent init
source ~/.zshrc  # use ~/.bashrc on bash
```

`packagent init` detects `bash` or `zsh`, writes a managed bootstrap block into
the right rc file, and leaves your shell in `(base)` after you reload it.

Low-level manual hook setup is still available if you want it:

```bash
eval "$(packagent shell init zsh)"
```

Create and activate an environment:

```bash
packagent create -n codex-with-omx
packagent activate codex-with-omx
```

Then install or configure a harness normally:

```bash
npm install -g @openai/codex oh-my-codex
omx setup
```

All writes to `~/.codex` land inside the active environment's
`~/.packagent-v1/envs/codex-with-omx/.codex`.

Return to the default base environment:

```bash
packagent deactivate
```

`deactivate` switches you back to `(base)` rather than clearing the packagent
prompt state.

## Commands

- `packagent init [--shell {bash|zsh}] [--rc-file PATH]`
- `packagent shell init {bash|zsh}`
- `packagent create -n <env>`
- `packagent create -n <env> --clone <source-env>`
- `packagent activate <env>`
- `packagent deactivate`
- `packagent list`
- `packagent status`
- `packagent remove <env>`
- `packagent doctor`
- `packagent doctor --fix`

## Guarantees and limits

`packagent` v1:

- manages `~/.codex` with a single global active environment
- keeps a permanent `base` environment
- backs up an existing unmanaged `~/.codex` on first takeover
- is designed for macOS and Linux only

`packagent` v1 does not:

- install Codex or harness packages
- isolate trusted repo-local `.codex/config.toml` layers
- isolate repo/system instruction files that Codex also loads
- support different active environments in different terminals at the same time

## Development

Create a local virtual environment and install test tools:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m build
```

## Safe Docker sandbox

If you want to test `packagent` end to end without touching your real machine
state, this repo includes a disposable Docker sandbox.

What the sandbox image includes:

- Python 3
- `uv`
- `pipx`
- Node.js + npm
- `@openai/codex`
- bash and zsh

Inside the sandbox, npm global installs are configured to use the test user's
`~/.local` prefix, so commands like `npm install -g oh-my-codex` work without
root.

Run the scripted end-to-end smoke flow:

```bash
./scripts/run_docker_sandbox.sh test
```

That flow exercises:

- installing `packagent` with `uv tool install`
- installing shell integration with `packagent init`
- first-run takeover of an unmanaged `~/.codex`
- creating and activating environments
- writing harness-like files into the active `~/.codex`
- switching envs and verifying isolation
- `doctor --fix`
- deactivation
- environment removal
- uninstalling `packagent`

Open an interactive shell for manual testing:

```bash
./scripts/run_docker_sandbox.sh shell
```

Inside the container, the test user home is `/home/tester`, so all `~/.codex`
and `~/.packagent-v1` mutations stay isolated inside the container.

The repo itself is copied into the container at `/workspace`. Because
`packagent` is not published to a package index yet, install the local checkout
from there:

```bash
uv tool install /workspace
packagent init
source ~/.bashrc
```

`uv tool install packagent` will only work after a real package publish.

If you want to try authenticated Codex flows manually, pass through your API key
when starting the container:

```bash
OPENAI_API_KEY=... ./scripts/run_docker_sandbox.sh shell
```

Notes:

- The Docker sandbox is for safe user-home testing. It does not isolate
  repo-local trusted `.codex/` layers inside mounted projects, just like normal
  `packagent` behavior.
- This repo's current workspace does not include Docker, so the shell scripts
  are provided and syntax-checked here, but the image build itself must be run
  on a machine with Docker installed.

## License

MIT
