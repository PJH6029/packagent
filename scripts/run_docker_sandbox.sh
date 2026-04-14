#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_docker_sandbox.sh test
  ./scripts/run_docker_sandbox.sh shell [bash|zsh]

Modes:
  test   Build the sandbox image and run the scripted end-to-end smoke test.
  shell  Build the sandbox image and open an interactive shell for manual testing.

Environment:
  IMAGE_NAME                         Override the docker image tag (default: packagent-e2e)
  PACKAGENT_DOCKER_SHELL             Interactive shell for shell mode (bash or zsh; default: bash)
  PACKAGENT_DOCKER_ENABLE_PROMPT_FRAMEWORKS
                                     Set to 0 to skip Oh My Bash / Oh My Zsh setup in shell mode (default: 1)
  OPENAI_API_KEY                     Passed through to the container if set
  ANTHROPIC_API_KEY                  Passed through to the container if set
  PACKAGENT_DOCKER_COPY_HOST_CONFIGS Set to 0 to skip host config copy (default: 1)
  PACKAGENT_DOCKER_CODEX_SOURCE      Override Codex source dir (default: CODEX_HOME or ~/.codex)
  PACKAGENT_DOCKER_CLAUDE_SOURCE     Override Claude source dir (default: CLAUDE_CONFIG_DIR or ~/.claude)
  PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS
                                     Set to 1 to run optional real Oh My Bash / Oh My Zsh prompt tests.
EOF
}

require_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required but was not found on PATH" >&2
    exit 1
  }
}

expand_home_path() {
  local path="$1"
  case "$path" in
    "~")
      printf '%s\n' "$HOME"
      ;;
    "~/"*)
      printf '%s\n' "$HOME/${path#~/}"
      ;;
    *)
      printf '%s\n' "$path"
      ;;
  esac
}

resolve_existing_dir() {
  local raw_path="$1"
  local path
  path="$(expand_home_path "$raw_path")"
  case "$path" in
    /*) ;;
    *) path="$PWD/$path" ;;
  esac

  [ -d "$path" ] || return 1

  local parent
  local name
  parent="$(dirname "$path")"
  name="$(basename "$path")"
  (cd "$parent" && cd -P "$name" && pwd)
}

add_host_config_mount() {
  local label="$1"
  local source_path="$2"
  local mount_path="$3"

  local resolved_path
  if resolved_path="$(resolve_existing_dir "$source_path")"; then
    echo "== will copy host ${label}: ${resolved_path} =="
    docker_args+=(-v "${resolved_path}:${mount_path}:ro")
  else
    echo "== no host ${label} found at ${source_path}; skipping copy =="
  fi
}

main() {
  local mode="${1:-}"
  local requested_shell="${2:-${PACKAGENT_DOCKER_SHELL:-bash}}"
  case "$mode" in
    test|shell) ;;
    *)
      usage
      exit 1
      ;;
  esac
  if [ "$mode" = "test" ] && [ "$#" -gt 1 ]; then
    usage
    exit 1
  fi
  if [ "$mode" = "shell" ] && [ "$#" -gt 2 ]; then
    usage
    exit 1
  fi
  if [ "$mode" = "shell" ]; then
    case "$requested_shell" in
      bash|zsh) ;;
      *)
        echo "unsupported interactive shell: ${requested_shell}" >&2
        usage
        exit 1
        ;;
    esac
  fi

  require_docker

  local enable_prompt_frameworks=0
  if [ "$mode" = "shell" ]; then
    enable_prompt_frameworks="${PACKAGENT_DOCKER_ENABLE_PROMPT_FRAMEWORKS:-1}"
  fi

  local image_name="${IMAGE_NAME:-packagent-e2e}"
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  echo "== building docker image: ${image_name} =="
  docker build -f "$repo_root/docker/Dockerfile" -t "$image_name" "$repo_root"

  local docker_args=(--rm --user root)
  if [ -n "${OPENAI_API_KEY:-}" ]; then
    docker_args+=(-e "OPENAI_API_KEY=${OPENAI_API_KEY}")
  fi
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    docker_args+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
  fi
  if [ -n "${PACKAGENT_DOCKER_EXPECT_CODEX_SEED_FILE:-}" ]; then
    docker_args+=(
      -e "PACKAGENT_DOCKER_EXPECT_CODEX_SEED_FILE=${PACKAGENT_DOCKER_EXPECT_CODEX_SEED_FILE}"
    )
  fi
  if [ -n "${PACKAGENT_DOCKER_EXPECT_CLAUDE_SEED_FILE:-}" ]; then
    docker_args+=(
      -e "PACKAGENT_DOCKER_EXPECT_CLAUDE_SEED_FILE=${PACKAGENT_DOCKER_EXPECT_CLAUDE_SEED_FILE}"
    )
  fi
  if [ -n "${PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS:-}" ]; then
    docker_args+=(
      -e "PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS=${PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS}"
    )
  fi
  if [ "$mode" = "shell" ]; then
    docker_args+=(
      -e "PACKAGENT_DOCKER_INTERACTIVE_SHELL=${requested_shell}"
      -e "PACKAGENT_DOCKER_ENABLE_PROMPT_FRAMEWORKS=${enable_prompt_frameworks}"
    )
  fi

  if [ "${PACKAGENT_DOCKER_COPY_HOST_CONFIGS:-1}" != "0" ]; then
    local codex_source="${PACKAGENT_DOCKER_CODEX_SOURCE:-${CODEX_HOME:-$HOME/.codex}}"
    local claude_source="${PACKAGENT_DOCKER_CLAUDE_SOURCE:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}"
    add_host_config_mount "Codex home" "$codex_source" "/tmp/packagent-host-config/codex"
    add_host_config_mount "Claude config" "$claude_source" "/tmp/packagent-host-config/claude"
  else
    echo "== host config copy disabled =="
  fi

  if [ "$mode" = "test" ]; then
    docker run "${docker_args[@]}" "$image_name" \
      bash /workspace/scripts/bootstrap_docker_home.sh \
      bash /workspace/scripts/e2e_in_docker.sh
  else
    local requested_shell_path="/bin/${requested_shell}"
    local requested_rc_file="~/.bashrc"
    if [ "$requested_shell" = "zsh" ]; then
      requested_rc_file="~/.zshrc"
    fi
    cat <<'EOF'
== opening interactive sandbox ==
The repository is available inside the container at /workspace.

If present, your host Codex and Claude config directories were mounted
read-only for startup and copied into the container user's home. The running
container uses those copied files, so packagent experiments do not mutate the
host originals.

Install the local checkout with:
  uv tool install /workspace
EOF
    cat <<EOF
  packagent init --shell ${requested_shell}
  source ${requested_rc_file}
EOF
    cat <<'EOF'

This sandbox also configures npm global installs under ~/.local, so
`npm install -g ...` works without root.

`uv tool install packagent` will fail here until packagent is published to a
package index.
EOF
    docker run "${docker_args[@]}" -it "$image_name" \
      bash /workspace/scripts/bootstrap_docker_home.sh \
      env "SHELL=${requested_shell_path}" "$requested_shell"
  fi
}

main "$@"
