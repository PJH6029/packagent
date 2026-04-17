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

Uninstall packagent's home management before uninstalling the executable:

```bash
packagent uninstall --restore-source base  # or --restore-source backup
uv tool uninstall packagent
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

The shell hook also exposes the globally active env in your prompt. Plain
bash/zsh get a `(<env>)` prefix, generic zsh themes with an existing right
prompt place the marker in `RPROMPT`, Powerlevel10k gets a native `packagent`
segment, Spaceship gets a native `packagent` section, and Oh My Bash
Powerline-style themes get a `[pa] <env>` segment. Each prompt refresh
reconciles with the managed symlinks, so other open shells update after another
shell activates, deactivates, or uninstalls packagent. For custom themes, use
`packagent_prompt_info` or `PACKAGENT_PROMPT_MODIFIER`.

Powerlevel10k and Spaceship users can move the native zsh marker with
`PACKAGENT_ZSH_PROMPT_POSITION` (`auto`, `right`, or `left`). Powerlevel10k
defaults to readable light text on a blue segment and renders `<env> pa`; style
it through
`POWERLEVEL9K_PACKAGENT_*` or the `PACKAGENT_POWERLEVEL_FOREGROUND` and
`PACKAGENT_POWERLEVEL_BACKGROUND` defaults. Set `PACKAGENT_POWERLEVEL_SUFFIX`
to change or remove the trailing ` pa`. Spaceship uses `SPACESHIP_PACKAGENT_*`
section options.

Starship is config-file driven, so `packagent init` does not rewrite
`starship.toml`. Use a Starship custom module that calls
`packagent shell active-env` when you want a native Starship segment.

By default, `packagent` manages:

- `~/.codex`
- `~/.agents`
- `~/.claude`

If `CODEX_HOME` is already set, `packagent` manages that Codex path instead of
`~/.codex`. If `CLAUDE_CONFIG_DIR` is already set, it manages that Claude path
instead of `~/.claude`. `packagent` does not export or rewrite those variables
for you.

On first takeover, existing unmanaged target paths are backed up together under
one timestamp root, for example
`~/.packagent-backups/<timestamp>/{.codex,.agents,.claude}`. By default they are
imported into the permanent `base` env; with `--base-mode fresh`, `base` starts
empty after the backup. `base` is the fallback environment and cannot be
removed.

`packagent uninstall` removes the managed shell startup block and replaces the
managed symlinks with normal user-level paths again. If `init` used import
mode, interactive uninstall asks whether to restore from the copied `base` env
or the original backup snapshots; non-interactive uninstall requires
`--restore-source base` or `--restore-source backup`. If `init` used fresh
mode, uninstall restores backup snapshots. Backup restore uses the backup root
from the current init/takeover generation, so older retained backups are not
used after a re-init. `~/.packagent` and
`~/.packagent-backups` are kept as recoverable data; remove the executable
separately with `uv tool uninstall packagent`.

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
packagent uninstall [--restore-source {base,backup}] [--shell {bash|zsh}] [--rc-file PATH]
packagent doctor
packagent doctor --fix
```

`packagent doctor --fix` also migrates legacy backup snapshots from
`~/.packagent/backups` to `~/.packagent-backups` and rewrites recorded backup
paths in state.

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

Open zsh in the same sandbox image:

```bash
./scripts/run_docker_sandbox.sh shell zsh
```

The image includes Python 3, `uv`, `pipx`, Node.js, npm, `@openai/codex`,
`@anthropic-ai/claude-code`, bash, and zsh. npm global installs use the test
user's `~/.local` prefix, so `npm install -g ...` works without root.
The default interactive shell is bash on every host; pass `shell zsh` when you
want to test zsh on the same machine.

Interactive shell mode also installs the matching prompt framework inside the
disposable container. `shell bash` installs Oh My Bash into `~/.oh-my-bash` and
uses the `powerline` theme; `shell zsh` installs Oh My Zsh into `~/.oh-my-zsh`
and uses `agnoster`. Disable this setup with:

```bash
PACKAGENT_DOCKER_ENABLE_PROMPT_FRAMEWORKS=0 ./scripts/run_docker_sandbox.sh shell zsh
```

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

Run optional real prompt-framework checks for Oh My Bash, Oh My Zsh,
Powerlevel10k, and Spaceship:

```bash
PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS=1 ./scripts/run_docker_sandbox.sh test
```

Those optional checks clone fresh framework checkouts inside the container.
Interactive shell mode does the same instead of copying host prompt framework
directories.

Inside the container, the repo is available at `/workspace`:

```bash
uv tool install /workspace
packagent init --shell bash  # or: packagent init --shell zsh
source ~/.bashrc             # or: source ~/.zshrc
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
