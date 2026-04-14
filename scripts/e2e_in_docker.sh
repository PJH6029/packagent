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
  rm -rf "$HOME/.packagent" "$HOME/.packagent-v1"
  for target in "$HOME/.codex" "$HOME/.agents" "$HOME/.claude"; do
    if [ -L "$target" ]; then
      rm -f "$target"
    fi
  done

  echo "== verify optional copied host config seeds =="
  if [ -n "${PACKAGENT_DOCKER_EXPECT_CODEX_SEED_FILE:-}" ]; then
    assert_path_exists "$HOME/.codex/$PACKAGENT_DOCKER_EXPECT_CODEX_SEED_FILE"
  fi
  if [ -n "${PACKAGENT_DOCKER_EXPECT_CLAUDE_SEED_FILE:-}" ]; then
    assert_path_exists "$HOME/.claude/$PACKAGENT_DOCKER_EXPECT_CLAUDE_SEED_FILE"
  fi

  echo "== seed unmanaged user-level targets =="
  mkdir -p "$HOME/.codex"
  cat > "$HOME/.codex/packagent-e2e-codex-seed.txt" <<'EOF'
packagent e2e codex seed
EOF
  cat > "$HOME/.codex/auth.json" <<'EOF'
{"codex_auth": "shared"}
EOF
  cat > "$HOME/.codex/history.jsonl" <<'EOF'
{"history": "base-only"}
EOF
  mkdir -p "$HOME/.codex/tmp"
  ln -s "$HOME/.codex/missing-tool" "$HOME/.codex/tmp/dangling-tool"
  mkdir -p "$HOME/.agents/skills/legacy-skill"
  cat > "$HOME/.agents/skills/legacy-skill/SKILL.md" <<'EOF'
Legacy skill content
EOF
  mkdir -p "$HOME/.claude"
  cat > "$HOME/.claude/packagent-e2e-claude-seed.json" <<'EOF'
{"packagent_e2e_claude_seed": true}
EOF
  cat > "$HOME/.claude/.credentials.json" <<'EOF'
{"claude_auth": "shared"}
EOF
  cat > "$HOME/.claude/settings.json" <<'EOF'
{"history": "base-only"}
EOF

  echo "== install packagent via uv tool =="
  uv tool install /workspace
  command -v packagent >/dev/null || fail "packagent command was not installed"
  local packagent_bin
  packagent_bin="$(command -v packagent)"

  echo "== install shell integration =="
  packagent init --shell bash >/tmp/packagent-init.txt
  grep -q '==== Initializing packagent ====' /tmp/packagent-init.txt || fail "packagent init did not report init header"
  grep -q 'shell: bash' /tmp/packagent-init.txt || fail "packagent init did not report shell"
  grep -q 'base_mode: import' /tmp/packagent-init.txt || fail "packagent init did not report import base mode"
  grep -q 'active_env: base' /tmp/packagent-init.txt || fail "packagent init did not report active base env"
  grep -q 'source /home/tester/.bashrc' /tmp/packagent-init.txt || fail "packagent init did not report source command"
  grep -q 'eval "$(packagent shell init bash)"' "$HOME/.bashrc" || fail "bashrc was not updated by packagent init"
  echo "== verify bash rc can be sourced repeatedly =="
  bash --rcfile "$HOME/.bashrc" -i -c \
    'source "$HOME/.bashrc"; source "$HOME/.bashrc"; _packagent_prompt_command; [ "${PACKAGENT_ACTIVE_ENV:-}" = "base" ]' \
    >/tmp/packagent-bashrc-resource.txt 2>&1 || {
    cat /tmp/packagent-bashrc-resource.txt >&2
    fail "bashrc repeated source failed"
  }
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
  assert_path_exists "$base_home/packagent-e2e-codex-seed.txt"
  assert_path_exists "$base_home/auth.json"
  [ -L "$base_home/tmp/dangling-tool" ] || fail "base env did not preserve Codex symlink"
  assert_path_exists "$base_agents/skills/legacy-skill/SKILL.md"
  assert_path_exists "$base_claude/packagent-e2e-claude-seed.json"
  assert_path_exists "$base_claude/.credentials.json"
  grep -q "packagent e2e codex seed" "$base_home/packagent-e2e-codex-seed.txt" || fail "base env did not import legacy home"
  grep -q '"codex_auth": "shared"' "$base_home/auth.json" || fail "base env did not import Codex auth"
  grep -q "Legacy skill content" "$base_agents/skills/legacy-skill/SKILL.md" || fail "base env did not import legacy agents home"
  grep -q '"packagent_e2e_claude_seed": true' "$base_claude/packagent-e2e-claude-seed.json" || fail "base env did not import legacy Claude home"
  grep -q '"claude_auth": "shared"' "$base_claude/.credentials.json" || fail "base env did not import Claude auth"
  assert_path_exists "$demo_home/auth.json"
  assert_path_exists "$demo_claude/.credentials.json"
  assert_path_missing "$demo_home/history.jsonl"
  assert_path_missing "$demo_claude/settings.json"
  grep -q '"codex_auth": "shared"' "$demo_home/auth.json" || fail "new env did not seed Codex auth"
  grep -q '"claude_auth": "shared"' "$demo_claude/.credentials.json" || fail "new env did not seed Claude auth"

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
  assert_path_exists "$HOME/.codex/auth.json"
  assert_path_exists "$HOME/.claude/.credentials.json"

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
  grep -q "packagent e2e codex seed" "$HOME/.codex/packagent-e2e-codex-seed.txt" || fail "base env was not restored on deactivate"
  grep -q "Legacy skill content" "$HOME/.agents/skills/legacy-skill/SKILL.md" || fail "base agents env was not restored on deactivate"
  grep -q '"packagent_e2e_claude_seed": true' "$HOME/.claude/packagent-e2e-claude-seed.json" || fail "base Claude env was not restored on deactivate"

  echo "== verify fresh base mode backs up without import =="
  local fresh_home
  fresh_home="$(mktemp -d)"
  mkdir -p "$fresh_home/.codex" "$fresh_home/.claude"
  cat > "$fresh_home/.codex/auth.json" <<'EOF'
{"codex_auth": "fresh-backup-only"}
EOF
  cat > "$fresh_home/.codex/history.jsonl" <<'EOF'
{"history": "fresh-backup-only"}
EOF
  cat > "$fresh_home/.claude/.credentials.json" <<'EOF'
{"claude_auth": "fresh-backup-only"}
EOF
  HOME="$fresh_home" packagent init --shell bash --base-mode fresh --rc-file "$fresh_home/.bashrc" >/tmp/packagent-fresh-init.txt
  grep -q 'base_mode: fresh' /tmp/packagent-fresh-init.txt || fail "packagent init did not report fresh base mode"
  assert_symlink_target "$fresh_home/.codex" "$fresh_home/.packagent/envs/base/.codex"
  assert_symlink_target "$fresh_home/.claude" "$fresh_home/.packagent/envs/base/.claude"
  assert_path_missing "$fresh_home/.packagent/envs/base/.codex/auth.json"
  assert_path_missing "$fresh_home/.packagent/envs/base/.codex/history.jsonl"
  assert_path_missing "$fresh_home/.packagent/envs/base/.claude/.credentials.json"
  find "$fresh_home/.packagent/backups" -name auth.json -print -quit | grep -q . || fail "fresh mode did not back up Codex auth"
  find "$fresh_home/.packagent/backups" -name .credentials.json -print -quit | grep -q . || fail "fresh mode did not back up Claude auth"
  HOME="$fresh_home" packagent uninstall --shell bash --rc-file "$fresh_home/.bashrc" >/tmp/packagent-fresh-uninstall.txt
  grep -q 'restore_source: backup' /tmp/packagent-fresh-uninstall.txt || fail "fresh uninstall did not use backup restore source"
  [ ! -L "$fresh_home/.codex" ] || fail "fresh uninstall left Codex home as a symlink"
  [ ! -L "$fresh_home/.claude" ] || fail "fresh uninstall left Claude home as a symlink"
  grep -q '"codex_auth": "fresh-backup-only"' "$fresh_home/.codex/auth.json" || fail "fresh uninstall did not restore Codex backup"
  grep -q '"history": "fresh-backup-only"' "$fresh_home/.codex/history.jsonl" || fail "fresh uninstall did not restore Codex history backup"
  grep -q '"claude_auth": "fresh-backup-only"' "$fresh_home/.claude/.credentials.json" || fail "fresh uninstall did not restore Claude backup"
  assert_path_missing "$fresh_home/.agents"
  if grep -q 'eval "$(packagent shell init bash)"' "$fresh_home/.bashrc"; then
    fail "fresh uninstall did not remove shell init block"
  fi

  echo "== remove non-active env and uninstall packagent =="
  packagent remove codex-with-demo
  assert_path_missing "$root/envs/codex-with-demo"
  packagent uninstall --restore-source base --shell bash >/tmp/packagent-uninstall.txt
  grep -q 'restore_source: base' /tmp/packagent-uninstall.txt || fail "uninstall did not report base restore source"
  grep -q $'target\taction\tmanaged_home\tsource' /tmp/packagent-uninstall.txt || fail "uninstall did not print target table header"
  [ ! -L "$HOME/.codex" ] || fail "uninstall left Codex home as a symlink"
  [ ! -L "$HOME/.agents" ] || fail "uninstall left agents home as a symlink"
  [ ! -L "$HOME/.claude" ] || fail "uninstall left Claude home as a symlink"
  grep -q "packagent e2e codex seed" "$HOME/.codex/packagent-e2e-codex-seed.txt" || fail "uninstall did not restore base Codex home"
  grep -q "Legacy skill content" "$HOME/.agents/skills/legacy-skill/SKILL.md" || fail "uninstall did not restore base agents home"
  grep -q '"packagent_e2e_claude_seed": true' "$HOME/.claude/packagent-e2e-claude-seed.json" || fail "uninstall did not restore base Claude home"
  if grep -q 'eval "$(packagent shell init bash)"' "$HOME/.bashrc"; then
    fail "uninstall did not remove shell init block"
  fi

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
