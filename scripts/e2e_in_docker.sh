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
  rm -rf "$HOME/.packagent-v1" "$HOME/.codex" "$HOME/.claude"

  echo "== seed unmanaged provider homes =="
  mkdir -p "$HOME/.codex" "$HOME/.claude"
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Legacy Codex AGENTS content
EOF
  cat > "$HOME/.claude/settings.json" <<'EOF'
{"theme":"legacy"}
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
  eval "$(packagent shell init bash)"
  [ "${PACKAGENT_ACTIVE_ENV:-}" = "base" ] || fail "base env was not active after shell init"
  [ "${PACKAGENT_ACTIVE_PROVIDER:-}" = "codex" ] || fail "base provider was not exported after shell init"

  echo "== create and activate first env =="
  packagent create -n codex-with-demo >/tmp/packagent-create-codex.txt
  packagent activate codex-with-demo

  local root="$HOME/.packagent-v1"
  local base_codex="$root/envs/base/.codex"
  local base_claude="$root/envs/base/.claude"
  local demo_codex="$root/envs/codex-with-demo/.codex"
  local demo_claude="$root/envs/codex-with-demo/.claude"
  local claude_env_codex="$root/envs/oh-my-claude/.codex"
  local claude_env_claude="$root/envs/oh-my-claude/.claude"

  assert_symlink_target "$HOME/.codex" "$demo_codex"
  assert_symlink_target "$HOME/.claude" "$demo_claude"
  assert_path_exists "$base_codex/AGENTS.md"
  assert_path_exists "$base_claude/settings.json"
  grep -q "Legacy Codex AGENTS content" "$base_codex/AGENTS.md" || fail "base env did not import legacy Codex home"
  grep -q '"theme":"legacy"' "$base_claude/settings.json" || fail "base env did not import legacy Claude home"

  echo "== verify npm global installs work for the sandbox user =="
  [ "$(npm config get prefix)" = "$HOME/.local" ] || fail "npm global prefix is not user-local"
  npm install -g @openai/codex @anthropic-ai/claude-code oh-my-codex >/tmp/packagent-npm-install.txt 2>&1 || {
    cat /tmp/packagent-npm-install.txt >&2
    fail "npm global install failed for sandbox user"
  }
  command -v omx >/dev/null || fail "omx was not installed into the sandbox user's PATH"
  command -v claude >/dev/null || fail "claude was not installed into the sandbox user's PATH"

  echo "== simulate harness writing into active homes =="
  mkdir -p "$HOME/.codex/skills/demo-skill"
  cat > "$HOME/.codex/skills/demo-skill/SKILL.md" <<'EOF'
---
name: demo-skill
description: demo skill
---
demo
EOF
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Active Codex env AGENTS content
EOF
  cat > "$HOME/.claude/settings.json" <<'EOF'
{"profile":"codex-with-demo"}
EOF

  assert_path_exists "$demo_codex/skills/demo-skill/SKILL.md"
  grep -q '"profile":"codex-with-demo"' "$demo_claude/settings.json" || fail "active Claude home did not receive writes"

  echo "== create claude-primary env and verify isolation =="
  packagent create -n oh-my-claude --provider claude >/tmp/packagent-create-claude.txt
  grep -q $'created\toh-my-claude\tclaude\t' /tmp/packagent-create-claude.txt || fail "claude provider was not reported on create"
  packagent activate oh-my-claude
  packagent status >/tmp/packagent-status.txt
  grep -q '^provider=claude$' /tmp/packagent-status.txt || fail "active provider was not claude after activation"
  assert_symlink_target "$HOME/.codex" "$claude_env_codex"
  assert_symlink_target "$HOME/.claude" "$claude_env_claude"
  assert_path_missing "$HOME/.codex/skills/demo-skill/SKILL.md"
  assert_path_missing "$HOME/.claude/settings.json"

  echo "== write into claude-primary env =="
  cat > "$HOME/.claude/settings.json" <<'EOF'
{"profile":"oh-my-claude"}
EOF
  cat > "$HOME/.codex/AGENTS.md" <<'EOF'
Claude-primary env can still isolate Codex files
EOF
  grep -q '"profile":"oh-my-claude"' "$claude_env_claude/settings.json" || fail "claude env write did not land in active Claude home"
  grep -q 'Claude-primary env can still isolate Codex files' "$claude_env_codex/AGENTS.md" || fail "claude env did not isolate Codex home"

  echo "== force doctor repair path =="
  rm -f "$HOME/.codex" "$HOME/.claude"
  ln -s "$base_codex" "$HOME/.codex"
  ln -s "$base_claude" "$HOME/.claude"
  if packagent doctor >/tmp/packagent-doctor-before.txt 2>&1; then
    fail "doctor should have reported drift before repair"
  fi
  packagent doctor --fix >/tmp/packagent-doctor-after.txt
  assert_symlink_target "$HOME/.codex" "$claude_env_codex"
  assert_symlink_target "$HOME/.claude" "$claude_env_claude"

  echo "== deactivate back to base =="
  packagent deactivate
  assert_symlink_target "$HOME/.codex" "$base_codex"
  assert_symlink_target "$HOME/.claude" "$base_claude"
  grep -q "Legacy Codex AGENTS content" "$HOME/.codex/AGENTS.md" || fail "base Codex env was not restored on deactivate"
  grep -q '"theme":"legacy"' "$HOME/.claude/settings.json" || fail "base Claude env was not restored on deactivate"

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
