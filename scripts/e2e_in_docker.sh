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

prepare_prompt_framework() {
  local label="$1"
  local git_url="$2"
  local target_dir="$3"

  rm -rf "$target_dir"

  echo "== cloning ${label} =="
  git clone --depth 1 "$git_url" "$target_dir" >/tmp/packagent-${label// /-}-clone.txt 2>&1 || {
    cat "/tmp/packagent-${label// /-}-clone.txt" >&2
    fail "failed to clone ${label}"
  }
}

run_real_prompt_framework_tests() {
  local omb_dir="/tmp/packagent-real-oh-my-bash"
  local omz_dir="/tmp/packagent-real-oh-my-zsh"

  prepare_prompt_framework \
    "Oh My Bash" \
    "https://github.com/ohmybash/oh-my-bash.git" \
    "$omb_dir"
  prepare_prompt_framework \
    "Oh My Zsh" \
    "https://github.com/ohmyzsh/ohmyzsh.git" \
    "$omz_dir"

  echo "== verify real oh-my-bash powerline prompt =="
  OSH="$omb_dir" bash --norc -i -c '
    set +u
    set -e
    OSH_THEME=powerline
    completions=()
    aliases=()
    plugins=()
    source "$OSH/oh-my-bash.sh"
    PACKAGENT_ACTIVE_ENV=base
    eval "$(packagent shell init bash)"
    PACKAGENT_ACTIVE_ENV=codex-omx
    eval "$PROMPT_COMMAND"
    case "$(declare -p PROMPT_COMMAND 2>/dev/null)" in
      *"_omb_util_prompt_command_hook"*) ;;
      *) echo "PROMPT_COMMAND missing Oh My Bash hook: $(declare -p PROMPT_COMMAND 2>/dev/null || true)" >&2; exit 1 ;;
    esac
    case "$(declare -p _omb_util_prompt_command 2>/dev/null)" in
      *"_packagent_prompt_command"*) ;;
      *) echo "Oh My Bash hook list missing packagent: $(declare -p _omb_util_prompt_command 2>/dev/null || true)" >&2; exit 1 ;;
    esac
    case " $POWERLINE_PROMPT " in
      *" packagent "*) ;;
      *) echo "POWERLINE_PROMPT missing packagent: $POWERLINE_PROMPT" >&2; exit 1 ;;
    esac
    case "$PS1" in
      *"[pa] codex-omx"*) ;;
      *) echo "PS1 missing packagent segment: $PS1" >&2; exit 1 ;;
    esac
  ' >/tmp/packagent-real-omb.txt 2>&1 || {
    cat /tmp/packagent-real-omb.txt >&2
    fail "real Oh My Bash prompt test failed"
  }

  echo "== verify real oh-my-zsh prompt composition =="
  ZSH="$omz_dir" zsh -fic '
    unsetopt nounset 2>/dev/null || true
    ZSH_THEME=agnoster
    plugins=()
    source "$ZSH/oh-my-zsh.sh" || exit 1
    PACKAGENT_ACTIVE_ENV=base
    eval "$(packagent shell init zsh)" || exit 1
    PACKAGENT_ACTIVE_ENV=codex-omx
    _packagent_prompt_command
    [ "$(packagent_prompt_info)" = "(codex-omx) " ] || exit 1
    case "$PROMPT" in
      "(codex-omx) "*) ;;
      *) echo "PROMPT missing packagent prefix: $PROMPT" >&2; exit 1 ;;
    esac
  ' >/tmp/packagent-real-omz.txt 2>&1 || {
    cat /tmp/packagent-real-omz.txt >&2
    fail "real Oh My Zsh prompt test failed"
  }
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
  grep -q 'shell init bash' "$HOME/.bashrc" || fail "bashrc was not updated by packagent init"
  grep -Fq '"$HOME/.local/bin/packagent"' "$HOME/.bashrc" || fail "bashrc missing local packagent fallback"
  echo "== verify bash rc can be sourced repeatedly =="
  bash --rcfile "$HOME/.bashrc" -i -c \
    'source "$HOME/.bashrc"; source "$HOME/.bashrc"; _packagent_prompt_command; [ "${PACKAGENT_ACTIVE_ENV:-}" = "base" ]' \
    >/tmp/packagent-bashrc-resource.txt 2>&1 || {
    cat /tmp/packagent-bashrc-resource.txt >&2
    fail "bashrc repeated source failed"
  }
  echo "== verify oh-my-bash style prompt composition =="
  bash -lc '
    set -euo pipefail
    _omb_util_prompt_command=()
    _omb_util_add_prompt_command() {
      local hook
      for hook in "${_omb_util_prompt_command[@]}"; do
        [ "$hook" = "$1" ] && return 0
      done
      _omb_util_prompt_command+=("$1")
      PROMPT_COMMAND="_omb_util_prompt_command_hook"
    }
    _omb_util_prompt_command_hook() {
      local hook
      for hook in "${_omb_util_prompt_command[@]}"; do
        "$hook"
      done
    }
    _omb_theme_PROMPT_COMMAND() {
      PS1="theme$ "
    }
    _omb_util_add_prompt_command _omb_theme_PROMPT_COMMAND
    PACKAGENT_ACTIVE_ENV=base
    eval "$(packagent shell init bash)"
    [ "${PROMPT_COMMAND-}" = "_omb_util_prompt_command_hook" ]
    [ "${_omb_util_prompt_command[0]}" = "_omb_theme_PROMPT_COMMAND" ]
    [ "${_omb_util_prompt_command[1]}" = "_packagent_prompt_command" ]
    _omb_util_prompt_command_hook
    [ "$PS1" = "(base) theme$ " ]
  ' >/tmp/packagent-omb-prompt.txt 2>&1 || {
    cat /tmp/packagent-omb-prompt.txt >&2
    fail "oh-my-bash style prompt composition failed"
  }
  echo "== verify oh-my-bash powerline prompt segment =="
  bash -lc '
    set -euo pipefail
    _omb_util_prompt_command=()
    _omb_util_add_prompt_command() {
      local hook
      for hook in "${_omb_util_prompt_command[@]}"; do
        [ "$hook" = "$1" ] && return 0
      done
      _omb_util_prompt_command+=("$1")
      PROMPT_COMMAND="_omb_util_prompt_command_hook"
    }
    _omb_util_prompt_command_hook() {
      local hook
      for hook in "${_omb_util_prompt_command[@]}"; do
        "$hook"
      done
    }
    POWERLINE_PROMPT="user_info scm cwd"
    PYTHON_VENV_THEME_PROMPT_COLOR=35
    __powerline_user_info_prompt() { printf "user|32\n"; }
    __powerline_scm_prompt() { return 1; }
    __powerline_cwd_prompt() { printf "~/code|240\n"; }
    _omb_theme_PROMPT_COMMAND() {
      local segment info
      PS1=""
      for segment in $POWERLINE_PROMPT; do
        info=""
        if command -v "__powerline_${segment}_prompt" >/dev/null 2>&1; then
          info="$("__powerline_${segment}_prompt")" || true
        fi
        [ -n "$info" ] && PS1="${PS1}${info} "
      done
    }
    _omb_util_add_prompt_command _omb_theme_PROMPT_COMMAND
    PACKAGENT_ACTIVE_ENV=base
    eval "$(packagent shell init bash)"
    [ "$POWERLINE_PROMPT" = "user_info scm packagent cwd" ]
    _omb_util_prompt_command_hook
    case "$PS1" in
      *"[pa] base|35"*) ;;
      *) echo "missing packagent powerline segment: $PS1" >&2; exit 1 ;;
    esac
    case "$PS1" in
      "(base) "*) echo "unexpected fallback prefix: $PS1" >&2; exit 1 ;;
    esac
  ' >/tmp/packagent-omb-powerline.txt 2>&1 || {
    cat /tmp/packagent-omb-powerline.txt >&2
    fail "oh-my-bash powerline prompt segment failed"
  }
  echo "== verify zsh prompt hook composition =="
  zsh -fc '
    set -e
    PROMPT="theme%# "
    _theme_precmd() {
      PROMPT="theme%# "
    }
    precmd_functions=(_theme_precmd)
    PACKAGENT_ACTIVE_ENV=base
    eval "$(packagent shell init zsh)"
    _theme_precmd
    _packagent_prompt_command
    [ "$PROMPT" = "(base) theme%# " ]
    [ "$(packagent_prompt_info)" = "(base) " ]
    PACKAGENT_ACTIVE_ENV=work
    _theme_precmd
    _packagent_prompt_command
    [ "$PROMPT" = "(work) theme%# " ]
    _right_theme_precmd() {
      PROMPT="theme%# "
      RPROMPT="conda kube"
    }
    PACKAGENT_ACTIVE_ENV=base
    _right_theme_precmd
    _packagent_prompt_command
    [ "$PROMPT" = "theme%# " ]
    [ "$RPROMPT" = "(base) conda kube" ]
    PACKAGENT_ACTIVE_ENV=work
    _right_theme_precmd
    _packagent_prompt_command
    [ "$PROMPT" = "theme%# " ]
    [ "$RPROMPT" = "(work) conda kube" ]
  ' >/tmp/packagent-zsh-prompt.txt 2>&1 || {
    cat /tmp/packagent-zsh-prompt.txt >&2
    fail "zsh prompt hook composition failed"
  }
  if [ "${PACKAGENT_DOCKER_PROMPT_FRAMEWORK_TESTS:-0}" != "0" ]; then
    run_real_prompt_framework_tests
  fi
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
  find "$fresh_home/.packagent-backups" -name auth.json -print -quit | grep -q . || fail "fresh mode did not back up Codex auth"
  find "$fresh_home/.packagent-backups" -name .credentials.json -print -quit | grep -q . || fail "fresh mode did not back up Claude auth"
  HOME="$fresh_home" packagent uninstall --shell bash --rc-file "$fresh_home/.bashrc" >/tmp/packagent-fresh-uninstall.txt
  grep -q 'restore_source: backup' /tmp/packagent-fresh-uninstall.txt || fail "fresh uninstall did not use backup restore source"
  [ ! -L "$fresh_home/.codex" ] || fail "fresh uninstall left Codex home as a symlink"
  [ ! -L "$fresh_home/.claude" ] || fail "fresh uninstall left Claude home as a symlink"
  grep -q '"codex_auth": "fresh-backup-only"' "$fresh_home/.codex/auth.json" || fail "fresh uninstall did not restore Codex backup"
  grep -q '"history": "fresh-backup-only"' "$fresh_home/.codex/history.jsonl" || fail "fresh uninstall did not restore Codex history backup"
  grep -q '"claude_auth": "fresh-backup-only"' "$fresh_home/.claude/.credentials.json" || fail "fresh uninstall did not restore Claude backup"
  assert_path_missing "$fresh_home/.agents"
  if grep -q '# >>> packagent initialize >>>' "$fresh_home/.bashrc"; then
    fail "fresh uninstall did not remove shell init block"
  fi

  echo "== verify zsh rc install path =="
  local zsh_home
  zsh_home="$(mktemp -d)"
  mkdir -p "$zsh_home/.codex" "$zsh_home/.agents" "$zsh_home/.claude"
  cat > "$zsh_home/.codex/auth.json" <<'EOF'
{"codex_auth": "zsh-import"}
EOF
  HOME="$zsh_home" SHELL=/bin/zsh packagent init --shell zsh --base-mode import --rc-file "$zsh_home/.zshrc" >/tmp/packagent-zsh-init.txt
  grep -q 'shell: zsh' /tmp/packagent-zsh-init.txt || fail "packagent init did not report zsh shell"
  grep -q "source $zsh_home/.zshrc" /tmp/packagent-zsh-init.txt || fail "packagent init did not report zsh source command"
  grep -q 'shell init zsh' "$zsh_home/.zshrc" || fail "zshrc was not updated by packagent init"
  grep -Fq '"$HOME/.local/bin/packagent"' "$zsh_home/.zshrc" || fail "zshrc missing local packagent fallback"
  assert_symlink_target "$zsh_home/.codex" "$zsh_home/.packagent/envs/base/.codex"
  grep -q '"codex_auth": "zsh-import"' "$zsh_home/.packagent/envs/base/.codex/auth.json" || fail "zsh init did not import Codex auth"
  HOME="$zsh_home" SHELL=/bin/zsh zsh -fc '
    source "$HOME/.zshrc"
    source "$HOME/.zshrc"
    _packagent_prompt_command
    [ "${PACKAGENT_ACTIVE_ENV:-}" = "base" ] || exit 1
    [ "$(packagent_prompt_info)" = "(base) " ] || exit 1
  ' >/tmp/packagent-zshrc-source.txt 2>&1 || {
    cat /tmp/packagent-zshrc-source.txt >&2
    fail "zshrc repeated source failed"
  }

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
  if grep -q '# >>> packagent initialize >>>' "$HOME/.bashrc"; then
    fail "uninstall did not remove shell init block"
  fi

  echo "== verify re-init after base restore =="
  packagent init --shell bash --base-mode import >/tmp/packagent-reinit-after-uninstall.txt
  grep -q 'base_mode: import' /tmp/packagent-reinit-after-uninstall.txt || fail "re-init did not report import base mode"
  assert_symlink_target "$HOME/.codex" "$root/envs/base/.codex"
  assert_symlink_target "$HOME/.agents" "$root/envs/base/.agents"
  assert_symlink_target "$HOME/.claude" "$root/envs/base/.claude"
  grep -q "packagent e2e codex seed" "$root/envs/base/.codex/packagent-e2e-codex-seed.txt" || fail "re-init did not preserve restored Codex home"
  packagent uninstall --restore-source base --shell bash >/tmp/packagent-reuninstall.txt
  grep -q 'restore_source: base' /tmp/packagent-reuninstall.txt || fail "second uninstall did not report base restore source"
  [ ! -L "$HOME/.codex" ] || fail "second uninstall left Codex home as a symlink"

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
