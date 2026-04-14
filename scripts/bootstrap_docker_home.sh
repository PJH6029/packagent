#!/usr/bin/env bash
set -euo pipefail

copy_seed_home() {
  local label="$1"
  local source_dir="$2"
  local target_dir="$3"

  if [ ! -d "$source_dir" ]; then
    return 0
  fi

  echo "== copying host ${label} into ${target_dir} =="
  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  cp -a "$source_dir/." "$target_dir/"
  if [ "$(id -u)" -eq 0 ] && id tester >/dev/null 2>&1; then
    chown -R tester:tester "$target_dir"
  fi
  chmod -R u+rwX "$target_dir" 2>/dev/null || true
}

main() {
  export HOME="${HOME:-/home/tester}"

  copy_seed_home "Codex home" \
    "/tmp/packagent-host-config/codex" \
    "$HOME/.codex"

  copy_seed_home "Claude config" \
    "/tmp/packagent-host-config/claude" \
    "$HOME/.claude"

  if [ "$(id -u)" -eq 0 ] && id tester >/dev/null 2>&1; then
    exec runuser -u tester -- "$@"
  fi

  exec "$@"
}

main "$@"
