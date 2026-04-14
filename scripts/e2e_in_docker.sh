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
  claude --version || true

  echo "== clean previous state =="
  rm -rf "$HOME/.packagent" "$HOME/.packagent-v1" "$HOME/.codex" "$HOME/.agents" "$HOME/.claude"

  echo "== seed unmanaged user-level targets =="
  mkdir -p "$HOME/.codex"
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Legacy AGENTS content
EOF
  mkdir -p "$HOME/.agents/skills/legacy-skill"
  cat > "$HOME/.agents/skills/legacy-skill/SKILL.md" <<'EOF'
Legacy skill content
EOF
  mkdir -p "$HOME/.claude"
  cat > "$HOME/.claude/settings.json" <<'EOF'
{"legacy": true}
EOF

  echo "== install packagent via uv tool =="
  uv tool install /workspace
  command -v packagent >/dev/null || fail "packagent command was not installed"
  local packagent_bin
  packagent_bin="$(command -v packagent)"

  echo "== install shell integration =="
  packagent init --shell bash >/tmp/packagent-init.txt
  grep -q $'initialized\tbash\t' /tmp/packagent-init.txt || fail "packagent init did not report bash setup"
  grep -q 'eval "$(packagent shell init bash)"' "$HOME/.bashrc" || fail "bashrc was not updated by packagent init"
  # Bootstrap the current non-interactive test shell after verifying rc-file installation.
  eval "$(packagent shell init bash)"
  [ "${PACKAGENT_ACTIVE_ENV:-}" = "base" ] || fail "base env was not active after shell init"

  echo "== create and activate first env =="
  packagent create -n codex-with-demo
  packagent activate codex-with-demo

  local root="$HOME/.packagent"
  local base_home="$root/envs/base/.codex"
  local base_agents="$root/envs/base/.agents"
  local base_claude="$root/envs/base/.claude"
  local demo_home="$root/envs/codex-with-demo/.codex"
  local demo_agents="$root/envs/codex-with-demo/.agents"
  local demo_claude="$root/envs/codex-with-demo/.claude"
  local second_home="$root/envs/second/.codex"
  local second_agents="$root/envs/second/.agents"
  local second_claude="$root/envs/second/.claude"

  assert_symlink_target "$HOME/.codex" "$demo_home"
  assert_symlink_target "$HOME/.agents" "$demo_agents"
  assert_symlink_target "$HOME/.claude" "$demo_claude"
  assert_path_exists "$base_home/AGENTS.md"
  assert_path_exists "$base_agents/skills/legacy-skill/SKILL.md"
  assert_path_exists "$base_claude/settings.json"
  grep -q "Legacy AGENTS content" "$base_home/AGENTS.md" || fail "base env did not import legacy home"
  grep -q "Legacy skill content" "$base_agents/skills/legacy-skill/SKILL.md" || fail "base env did not import legacy agents home"
  grep -q '"legacy": true' "$base_claude/settings.json" || fail "base env did not import legacy Claude home"

  echo "== verify npm global installs work for the sandbox user =="
  [ "$(npm config get prefix)" = "$HOME/.local" ] || fail "npm global prefix is not user-local"
  npm install -g @openai/codex @anthropic-ai/claude-code oh-my-codex >/tmp/packagent-npm-install.txt 2>&1 || {
    cat /tmp/packagent-npm-install.txt >&2
    fail "npm global install failed for sandbox user"
  }
  command -v omx >/dev/null || fail "omx was not installed into the sandbox user's PATH"
  command -v claude >/dev/null || fail "claude was not installed into the sandbox user's PATH"

  echo "== simulate harness writing into active targets =="
  mkdir -p "$HOME/.codex/skills/demo-skill"
  cat > "$HOME/.codex/skills/demo-skill/SKILL.md" <<'EOF'
---
name: demo-skill
description: demo skill
---
demo
EOF
  mkdir -p "$HOME/.agents/skills/user-skill"
  cat > "$HOME/.agents/skills/user-skill/SKILL.md" <<'EOF'
---
name: user-skill
description: user skill
---
user
EOF
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Active env AGENTS content
EOF
  cat > "$HOME/.claude/settings.json" <<'EOF'
{"active": "codex-with-demo"}
EOF

  assert_path_exists "$demo_home/skills/demo-skill/SKILL.md"
  assert_path_exists "$demo_agents/skills/user-skill/SKILL.md"
  assert_path_exists "$demo_claude/settings.json"

  echo "== create second env and verify isolation =="
  packagent create -n second
  packagent activate second
  assert_symlink_target "$HOME/.codex" "$second_home"
  assert_symlink_target "$HOME/.agents" "$second_agents"
  assert_symlink_target "$HOME/.claude" "$second_claude"
  assert_path_missing "$HOME/.codex/skills/demo-skill/SKILL.md"
  assert_path_missing "$HOME/.agents/skills/user-skill/SKILL.md"
  assert_path_missing "$HOME/.claude/settings.json"

  echo "== force doctor repair path =="
  rm -f "$HOME/.codex"
  ln -s "$base_home" "$HOME/.codex"
  rm -f "$HOME/.agents"
  ln -s "$base_agents" "$HOME/.agents"
  rm -f "$HOME/.claude"
  ln -s "$base_claude" "$HOME/.claude"
  if packagent doctor >/tmp/packagent-doctor-before.txt 2>&1; then
    fail "doctor should have reported drift before repair"
  fi
  packagent doctor --fix >/tmp/packagent-doctor-after.txt
  assert_symlink_target "$HOME/.codex" "$second_home"
  assert_symlink_target "$HOME/.agents" "$second_agents"
  assert_symlink_target "$HOME/.claude" "$second_claude"

  echo "== deactivate back to base =="
  packagent deactivate
  assert_symlink_target "$HOME/.codex" "$base_home"
  assert_symlink_target "$HOME/.agents" "$base_agents"
  assert_symlink_target "$HOME/.claude" "$base_claude"
  grep -q "Legacy AGENTS content" "$HOME/.codex/AGENTS.md" || fail "base env was not restored on deactivate"
  grep -q "Legacy skill content" "$HOME/.agents/skills/legacy-skill/SKILL.md" || fail "base agents env was not restored on deactivate"
  grep -q '"legacy": true' "$HOME/.claude/settings.json" || fail "base Claude env was not restored on deactivate"

  echo "== remove non-active env and uninstall packagent =="
  packagent remove codex-with-demo
  assert_path_missing "$root/envs/codex-with-demo"

  uv tool uninstall packagent
  [ ! -x "$packagent_bin" ] || fail "packagent executable still exists after uninstall"
  unset -f packagent || true
  hash -r
  if command -v packagent >/dev/null 2>&1; then
    fail "packagent command still exists after uninstall"
  fi

  echo "E2E docker smoke test passed."
}

main "$@"
