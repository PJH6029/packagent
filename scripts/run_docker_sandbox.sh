#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_docker_sandbox.sh test
  ./scripts/run_docker_sandbox.sh shell

Modes:
  test   Build the sandbox image and run the scripted end-to-end smoke test.
  shell  Build the sandbox image and open an interactive shell for manual testing.

Environment:
  IMAGE_NAME        Override the docker image tag (default: packagent-e2e)
  OPENAI_API_KEY    Passed through to the container if set
EOF
}

require_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required but was not found on PATH" >&2
    exit 1
  }
}

main() {
  local mode="${1:-}"
  case "$mode" in
    test|shell) ;;
    *)
      usage
      exit 1
      ;;
  esac

  require_docker

  local image_name="${IMAGE_NAME:-packagent-e2e}"
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  echo "== building docker image: ${image_name} =="
  docker build -f "$repo_root/docker/Dockerfile" -t "$image_name" "$repo_root"

  local docker_args=(--rm)
  if [ -n "${OPENAI_API_KEY:-}" ]; then
    docker_args+=(-e "OPENAI_API_KEY=${OPENAI_API_KEY}")
  fi

  if [ "$mode" = "test" ]; then
    docker run "${docker_args[@]}" "$image_name" bash /workspace/scripts/e2e_in_docker.sh
  else
    cat <<'EOF'
== opening interactive sandbox ==
The repository is available inside the container at /workspace.

Install the local checkout with:
  uv tool install /workspace
  packagent init
  source ~/.bashrc

`uv tool install packagent` will fail here until packagent is published to a
package index.
EOF
    docker run "${docker_args[@]}" -it "$image_name" bash
  fi
}

main "$@"
