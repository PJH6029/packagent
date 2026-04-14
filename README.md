# packagent

`packagent` gives CLI agents purpose-built user-home profiles. Switch one
environment and your active `~/.codex`, `~/.agents`, and `~/.claude` layers
switch with it.

Use it like conda for agent homes: keep an OMX-heavy Codex setup in one env,
keep Gmail, Google Drive, Calendar, and other computer-use skills in another,
then switch by task without mixing user-level files.

## Quick Start

`packagent` is not published to a package index yet, so install it from a clone
or from this local checkout:

```bash
git clone https://github.com/pjh6029/packagent.git
cd packagent
uv tool install .
```

From an existing checkout:

```bash
uv tool install /path/to/packagent
```

Install shell integration once:

```bash
packagent init
source ~/.zshrc  # use ~/.bashrc on bash
```

When unmanaged `~/.codex`, `~/.agents`, or `~/.claude` paths already exist,
interactive `init` asks whether `base` should import them or start fresh. Use
`packagent init --base-mode import` or `packagent init --base-mode fresh` in
scripts.

(Example) Create an environment for Codex plus OMX:

```bash
packagent create -n codex-omx
packagent activate codex-omx

npm install -g @openai/codex oh-my-codex
omx setup
```

(Example) Create another environment for computer-use work:

```bash
packagent create -n computer-use
packagent activate computer-use

# Run your normal harness or skill install commands here.
# Example purpose: Gmail, Google Drive, Calendar, browser, and desktop skills.
```

Switch by purpose:

```bash
packagent activate codex-omx
packagent activate computer-use
packagent deactivate  # back to base
```

## Why This Exists

Agent harnesses often install prompts, hooks, MCP config, skills, and local
state into user-level homes. Common targets include:

- `~/.codex`
- `~/.agents`
- `~/.claude`

When multiple harnesses share the same user home, their files can leak across
sessions. `packagent` gives those user-level agent layers a small, predictable
environment manager.

It does not replace Codex or Claude, install harnesses for you, or isolate
trusted repo-local `.codex/`, `.agents/`, or `.claude/` layers inside projects.

## How It Works

Each environment lives under `~/.packagent/envs/<env>/`:

```text
~/.packagent/envs/<env>/.codex
~/.packagent/envs/<env>/.agents
~/.packagent/envs/<env>/.claude
```

Activating an env switches the managed user-level targets to symlinks pointing
at that env. Only one env is globally active at a time.

The shell hook also exposes the active env in your prompt. Plain bash/zsh get a
`(<env>)` prefix, and Oh My Bash Powerline-style themes get a `[pa] <env>`
segment. For custom themes, use `packagent_prompt_info` or
`PACKAGENT_PROMPT_MODIFIER`.

By default, `packagent` manages:

- `~/.codex`
- `~/.agents`
- `~/.claude`

If `CODEX_HOME` is already set, `packagent` manages that Codex path instead of
`~/.codex`. If `CLAUDE_CONFIG_DIR` is already set, it manages that Claude path
instead of `~/.claude`. `packagent` does not export or rewrite those variables
for you.

On first takeover, existing unmanaged target paths are backed up. By default
they are imported into the permanent `base` env; with `--base-mode fresh`,
`base` starts empty after the backup. `base` is the fallback environment and
cannot be removed.

Normal `packagent create -n <env>` starts with separate history, logs, settings,
skills, caches, and sessions, but seeds cumbersome auth files from the active
env when present:

- Codex: `auth.json`
- Claude: `.credentials.json`

Use `packagent create -n <env> --clone <source-env>` when you want a full copy
instead.

## Commands

```bash
packagent init [--shell {bash|zsh}] [--rc-file PATH] [--base-mode {import|fresh}]
packagent shell init {bash|zsh}
packagent create -n <env>
packagent create -n <env> --clone <source-env>
packagent activate <env>
packagent deactivate
packagent list
packagent status
packagent remove <env>
packagent doctor
packagent doctor --fix
```

## Guarantees and Limits

`packagent` v1:

- manages a single global active agent environment
- switches `~/.codex`, `~/.agents`, and `~/.claude` together
- respects pre-set `CODEX_HOME` and `CLAUDE_CONFIG_DIR` paths
- seeds only auth files into newly created envs; history and logs stay separate
- keeps `base` as a permanent fallback environment
- backs up existing unmanaged target paths on first takeover
- is designed for macOS and Linux

`packagent` v1 does not:

- install Codex, Claude, OMX, MCP servers, or skills
- isolate trusted repo-local `.codex/config.toml` layers
- isolate trusted repo-local `.agents/skills` layers
- isolate trusted repo-local `.claude/` layers
- isolate repo or system instruction files that agent tools may also load
- manage non-target homes such as `~/.claude.json` or `~/.gemini`
- offer a provider-specific mode; all managed targets switch with the same env
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

## Safe Docker Sandbox

Use the Docker sandbox for end-to-end testing without touching your real
machine state:

```bash
./scripts/run_docker_sandbox.sh test
```

Open an interactive shell:

```bash
./scripts/run_docker_sandbox.sh shell
```

The image includes Python 3, `uv`, `pipx`, Node.js, npm, `@openai/codex`,
`@anthropic-ai/claude-code`, bash, and zsh. npm global installs use the test
user's `~/.local` prefix, so `npm install -g ...` works without root.

By default, the wrapper copies your host Codex and Claude config directories
into the disposable container before the test or shell starts:

- Codex source: `${CODEX_HOME:-$HOME/.codex}`
- Claude source: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`
- Container targets: `/home/tester/.codex` and `/home/tester/.claude`

The host directories are mounted read-only only for startup copy. A short
bootstrap step copies them, fixes container ownership, and then runs the shell
or test as the `tester` user. Auth checks and packagent experiments mutate only
the copied files, never the host originals.

Disable the copy:

```bash
PACKAGENT_DOCKER_COPY_HOST_CONFIGS=0 ./scripts/run_docker_sandbox.sh shell
```

Override source directories:

```bash
PACKAGENT_DOCKER_CODEX_SOURCE=/path/to/codex-home \
PACKAGENT_DOCKER_CLAUDE_SOURCE=/path/to/claude-home \
./scripts/run_docker_sandbox.sh shell
```

The scripted smoke flow exercises local installation, shell integration,
first-run takeover, auth-only seed copying, env creation and activation,
fresh-base backup behavior, isolation across `.codex`, `.agents`, and
`.claude`, `doctor --fix`, deactivation, env removal, and uninstall.

Run optional real prompt-framework checks for Oh My Bash and Oh My Zsh:

```bash
PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS=1 ./scripts/run_docker_sandbox.sh test
```

When local `~/.oh-my-bash` or `~/.oh-my-zsh` directories exist, the wrapper
mounts them read-only for those optional checks. Otherwise the container clones
the frameworks during the test.

Inside the container, the repo is available at `/workspace`:

```bash
uv tool install /workspace
packagent init
source ~/.bashrc
```

`uv tool install packagent` will only work after a real package publish.

You can still pass API keys explicitly when you want them:

```bash
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... ./scripts/run_docker_sandbox.sh shell
```

The Docker sandbox mirrors normal `packagent` limits: it protects your host
home, but it does not isolate trusted repo-local `.codex/`, `.agents/`, or
`.claude/` layers inside mounted projects.

## License

MIT
