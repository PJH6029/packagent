#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

assert_path_exists() {
  local path="$1"
  [ -e "$path" ] || fail "expected path to exist: $path"
}

assert_path_missing() {
  local path="$1"
  [ ! -e "$path" ] || fail "expected path to be missing: $path"
}

assert_symlink_target() {
  local path="$1"
  local expected="$2"
  [ -L "$path" ] || fail "expected symlink: $path"
  local resolved
  resolved="$(readlink -f "$path")"
  [ "$resolved" = "$expected" ] || fail "expected $path -> $expected, got $resolved"
}

main() {
  export HOME="${HOME:-/home/tester}"
  export PATH="$HOME/.local/bin:$PATH"

  echo "== tool versions =="
  python3 --version
  uv --version
  npm --version
  codex --version || true

  echo "== clean previous state =="
  rm -rf "$HOME/.packagent-v1" "$HOME/.codex"

  echo "== seed unmanaged Codex home =="
  mkdir -p "$HOME/.codex"
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Legacy AGENTS content
EOF

  echo "== install packagent via uv tool =="
  uv tool install /workspace
  command -v packagent >/dev/null || fail "packagent command was not installed"

  echo "== initialize shell hook =="
  eval "$(packagent shell init bash)"

  echo "== create and activate first env =="
  packagent create -n codex-with-demo
  packagent activate codex-with-demo

  local root="$HOME/.packagent-v1"
  local base_home="$root/envs/base/.codex"
  local demo_home="$root/envs/codex-with-demo/.codex"
  local second_home="$root/envs/second/.codex"

  assert_symlink_target "$HOME/.codex" "$demo_home"
  assert_path_exists "$base_home/AGENTS.md"
  grep -q "Legacy AGENTS content" "$base_home/AGENTS.md" || fail "base env did not import legacy home"

  echo "== simulate harness writing into active home =="
  mkdir -p "$HOME/.codex/skills/demo-skill"
  cat > "$HOME/.codex/skills/demo-skill/SKILL.md" <<'EOF'
---
name: demo-skill
description: demo skill
---
demo
EOF
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Active env AGENTS content
EOF

  assert_path_exists "$demo_home/skills/demo-skill/SKILL.md"

  echo "== create second env and verify isolation =="
  packagent create -n second
  packagent activate second
  assert_symlink_target "$HOME/.codex" "$second_home"
  assert_path_missing "$HOME/.codex/skills/demo-skill/SKILL.md"

  echo "== force doctor repair path =="
  rm -f "$HOME/.codex"
  ln -s "$base_home" "$HOME/.codex"
  if packagent doctor >/tmp/packagent-doctor-before.txt 2>&1; then
    fail "doctor should have reported drift before repair"
  fi
  packagent doctor --fix >/tmp/packagent-doctor-after.txt
  assert_symlink_target "$HOME/.codex" "$second_home"

  echo "== deactivate back to base =="
  packagent deactivate
  assert_symlink_target "$HOME/.codex" "$base_home"
  grep -q "Legacy AGENTS content" "$HOME/.codex/AGENTS.md" || fail "base env was not restored on deactivate"

  echo "== remove non-active env and uninstall packagent =="
  packagent remove codex-with-demo
  assert_path_missing "$root/envs/codex-with-demo"

  uv tool uninstall packagent
  hash -r
  if command -v packagent >/dev/null 2>&1; then
    fail "packagent command still exists after uninstall"
  fi

  echo "E2E docker smoke test passed."
}

main "$@"

