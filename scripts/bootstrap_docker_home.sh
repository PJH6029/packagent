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

chown_for_tester() {
  local path="$1"
  if [ "$(id -u)" -eq 0 ] && id tester >/dev/null 2>&1; then
    chown -R tester:tester "$path"
  fi
}

install_prompt_framework() {
  local label="$1"
  local git_url="$2"
  local target_dir="$3"
  local log_file="/tmp/packagent-${label// /-}-install.txt"

  echo "== installing ${label} into ${target_dir} =="
  rm -rf "$target_dir"
  git clone --depth 1 "$git_url" "$target_dir" >"$log_file" 2>&1 || {
    cat "$log_file" >&2
    echo "failed to install ${label}" >&2
    exit 1
  }
  chown_for_tester "$target_dir"
  chmod -R u+rwX "$target_dir" 2>/dev/null || true
}

append_rc_block() {
  local rc_file="$1"
  local start_marker="$2"
  local end_marker="$3"
  local content_file="$4"

  touch "$rc_file"
  if grep -Fq "$start_marker" "$rc_file"; then
    return 0
  fi
  {
    printf '\n%s\n' "$start_marker"
    cat "$content_file"
    printf '%s\n' "$end_marker"
  } >>"$rc_file"
  chown_for_tester "$rc_file"
}

configure_oh_my_bash() {
  local target_dir="$HOME/.oh-my-bash"

  install_prompt_framework \
    "Oh My Bash" \
    "https://github.com/ohmybash/oh-my-bash.git" \
    "$target_dir"

  local block_file
  block_file="$(mktemp)"
  cat >"$block_file" <<'EOF'
export OSH="$HOME/.oh-my-bash"
OSH_THEME=powerline
completions=()
aliases=()
plugins=()
source "$OSH/oh-my-bash.sh"
EOF
  append_rc_block \
    "$HOME/.bashrc" \
    "# >>> packagent docker oh-my-bash >>>" \
    "# <<< packagent docker oh-my-bash <<<" \
    "$block_file"
  rm -f "$block_file"
}

configure_oh_my_zsh() {
  local target_dir="$HOME/.oh-my-zsh"

  install_prompt_framework \
    "Oh My Zsh" \
    "https://github.com/ohmyzsh/ohmyzsh.git" \
    "$target_dir"

  cat >"$HOME/.zshrc" <<'EOF'
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME=agnoster
plugins=()
source "$ZSH/oh-my-zsh.sh"
EOF
  chown_for_tester "$HOME/.zshrc"
}

configure_prompt_framework() {
  if [ "${PACKAGENT_DOCKER_ENABLE_PROMPT_FRAMEWORKS:-0}" = "0" ]; then
    if [ "${PACKAGENT_DOCKER_INTERACTIVE_SHELL:-}" = "zsh" ]; then
      touch "$HOME/.zshrc"
      chown_for_tester "$HOME/.zshrc"
    fi
    return 0
  fi

  case "${PACKAGENT_DOCKER_INTERACTIVE_SHELL:-}" in
    bash)
      configure_oh_my_bash
      ;;
    zsh)
      configure_oh_my_zsh
      ;;
  esac
}

main() {
  export HOME="${HOME:-/home/tester}"

  copy_seed_home "Codex home" \
    "/tmp/packagent-host-config/codex" \
    "$HOME/.codex"

  copy_seed_home "Claude config" \
    "/tmp/packagent-host-config/claude" \
    "$HOME/.claude"

  configure_prompt_framework

  if [ "$(id -u)" -eq 0 ] && id tester >/dev/null 2>&1; then
    exec runuser -u tester -- "$@"
  fi

  exec "$@"
}

main "$@"
