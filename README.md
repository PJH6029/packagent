# packagent

`packagent` is a small Python CLI that isolates user-level agent packaging
targets under `~/.packagent/envs/<env>/` and switches the active managed
targets with symlinks. Each environment contains `.codex`, `.agents`, and
`.claude`, so Codex configuration, user-level agent skills, and Claude
configuration stay separated by environment.

If `CODEX_HOME` is already set in your shell, `packagent` uses that path as the
managed Codex home instead of `~/.codex`. It does not export or rewrite
`CODEX_HOME` for you. If `CLAUDE_CONFIG_DIR` is already set, `packagent` uses
that path as the managed Claude home instead of `~/.claude`. It does not export
or rewrite `CLAUDE_CONFIG_DIR`, and `~/.agents` remains the managed user-level
agents target.

## Why this exists

Agent harnesses often install into user-level home layers:

- `AGENTS.md`
- `skills/`
- prompts
- hooks
- MCP configuration
- other host-specific files
- user-level skills and workflow files under `~/.agents`
- Claude settings and local state under `~/.claude`

When multiple harnesses share the same user home, their files can leak across
sessions. `packagent` gives you a conda-like workflow for user-level agent
packaging layers.

It does not try to replace Codex or Claude, install harnesses for you, or
isolate repo-local `.codex/`, `.agents/`, or `.claude/` files inside trusted
projects.

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
npm install -g @openai/codex @anthropic-ai/claude-code oh-my-codex
omx setup
```

All writes to the managed Codex home path land inside the active environment's
`~/.packagent/envs/codex-with-omx/.codex`. Writes under `~/.agents` land
inside `~/.packagent/envs/codex-with-omx/.agents`. Writes under the managed
Claude home path land inside `~/.packagent/envs/codex-with-omx/.claude`. By
default the managed Codex home path is `~/.codex`, but if you already export
`CODEX_HOME`, `packagent` will manage that Codex path instead. By default the
managed Claude home path is `~/.claude`, but if you already export
`CLAUDE_CONFIG_DIR`, `packagent` will manage that Claude path instead.

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

- manages a single global active agent environment
- switches the user-level targets `~/.codex`, `~/.agents`, and `~/.claude`
  together
- respects a pre-set `CODEX_HOME` path for the Codex home target instead of
  exporting one itself
- respects a pre-set `CLAUDE_CONFIG_DIR` path for the Claude home target
  instead of exporting one itself
- keeps a permanent `base` environment
- backs up existing unmanaged target paths on first takeover
- is designed for macOS and Linux only

`packagent` v1 does not:

- install Codex, Claude, or harness packages
- isolate trusted repo-local `.codex/config.toml` layers
- isolate trusted repo-local `.agents/skills` layers
- isolate trusted repo-local `.claude/` layers
- isolate repo/system instruction files that agent tools also load
- manage non-target homes such as `~/.claude.json` or `~/.gemini`
- offer a `--provider` mode; all managed targets switch with the same env
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
- `@anthropic-ai/claude-code`
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
- first-run takeover of unmanaged user-level target paths
- creating and activating environments
- writing harness-like files into the active managed target paths
- switching envs and verifying isolation
- `doctor --fix`
- deactivation
- environment removal
- uninstalling `packagent`

Open an interactive shell for manual testing:

```bash
./scripts/run_docker_sandbox.sh shell
```

Inside the container, the test user home is `/home/tester`, so all managed home
path mutations and `~/.packagent` mutations stay isolated inside the
container.

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

For authenticated Claude flows, pass through `ANTHROPIC_API_KEY` the same way:

```bash
ANTHROPIC_API_KEY=... ./scripts/run_docker_sandbox.sh shell
```

Notes:

- The Docker sandbox is for safe user-home testing. It does not isolate
  repo-local trusted `.codex/`, `.agents/`, or `.claude/` layers inside mounted
  projects, just like normal `packagent` behavior.
- The image build itself requires a machine with Docker installed.

## License

MIT
