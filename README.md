# packagent

`packagent` is a small Python CLI that isolates user-level Codex and Claude
Code homes under:

- `~/.packagent-v1/envs/<env>/.codex`
- `~/.packagent-v1/envs/<env>/.claude`

It keeps a single globally active environment and repoints the managed home
paths with symlinks so harnesses that expect one user-level home keep working
without leaking files across environments.

If `CODEX_HOME` is already set in your shell, `packagent` uses that path as the
managed Codex home instead of `~/.codex`. If `CLAUDE_CONFIG_DIR` is already
set, it uses that path instead of `~/.claude`. `packagent` does not export or
rewrite either variable for you.

## Why this exists

Agent harnesses often install into provider config homes directly:

- `AGENTS.md`
- `skills/`
- prompts
- hooks
- MCP configuration
- provider settings and auth files
- other host-specific files

When multiple harnesses share the same user home, their files can leak across
sessions. `packagent` gives you a conda-like workflow for the supported
provider home layer.

It does not try to replace Codex or Claude Code, install harnesses for you, or
isolate repo-local `.codex/` or `.claude/` files inside trusted projects.

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
the right rc file, and leaves your shell in `(codex:base)` after you reload it.

Low-level manual hook setup is still available if you want it:

```bash
eval "$(packagent shell init zsh)"
```

Create and activate a Codex-primary environment:

```bash
packagent create -n codex-with-omx
packagent activate codex-with-omx
```

Create and activate a Claude-primary environment:

```bash
packagent create -n oh-my-claude --provider claude
packagent activate oh-my-claude
```

`--provider` sets the environment's primary provider and prompt label. It does
not block the other CLI. In an active Claude-primary environment, both `claude`
and `codex` still use that environment's isolated `~/.claude` and `~/.codex`
homes.

Then install or configure a harness normally:

```bash
npm install -g @openai/codex @anthropic-ai/claude-code oh-my-codex
omx setup
```

All writes to the managed home paths land inside the active environment's
provider-specific directories. By default those managed paths are `~/.codex`
and `~/.claude`, but if you already export `CODEX_HOME` or
`CLAUDE_CONFIG_DIR`, `packagent` manages those paths instead.

Return to the default base environment:

```bash
packagent deactivate
```

`deactivate` switches you back to `(codex:base)` rather than clearing the
packagent prompt state.

## Commands

- `packagent init [--shell {bash|zsh}] [--rc-file PATH]`
- `packagent shell init {bash|zsh}`
- `packagent create -n <env> [--provider {codex|claude}]`
- `packagent create -n <env> --clone <source-env> [--provider {codex|claude}]`
- `packagent activate <env>`
- `packagent deactivate`
- `packagent list`
- `packagent status`
- `packagent remove <env>`
- `packagent doctor`
- `packagent doctor --fix`

## Guarantees and limits

`packagent` v1:

- manages one globally active environment at a time
- repoints both supported provider homes for the active environment
- respects pre-set `CODEX_HOME` and `CLAUDE_CONFIG_DIR` paths instead of
  exporting them itself
- keeps a permanent `base` environment
- backs up existing unmanaged home paths on first takeover
- is designed for macOS and Linux only

`packagent` v1 does not:

- install provider CLIs or harness packages for you
- isolate trusted repo-local `.codex/` or `.claude/` layers
- isolate repo/system instruction files that Codex or Claude Code also loads
- support different active environments in different terminals at the same time

## Development

Create a local virtual environment and install test tools:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
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
- first-run takeover of unmanaged Codex and Claude homes
- creating and activating environments
- writing harness-like files into the active managed homes
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
path mutations and `~/.packagent-v1` mutations stay isolated inside the
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

If you want to try authenticated provider flows manually, pass through your API
keys when starting the container:

```bash
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... ./scripts/run_docker_sandbox.sh shell
```

Notes:

- The Docker sandbox is for safe user-home testing. It does not isolate
  repo-local trusted `.codex/` or `.claude/` layers inside mounted projects,
  just like normal `packagent` behavior.
- The image build and smoke flow require Docker on the machine where you run
  them.

## License

MIT
