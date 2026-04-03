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

assert_not_symlink() {
  local path="$1"
  [ ! -L "$path" ] || fail "expected path to be a real directory, not a symlink: $path"
}

wait_for_file() {
  local path="$1"
  local attempts="${2:-100}"
  local i
  for ((i = 0; i < attempts; i += 1)); do
    if [ -e "$path" ]; then
      return 0
    fi
    sleep 0.1
  done
  fail "timed out waiting for file: $path"
}

run_managed_shell() {
  local env_name="$1"
  local command="$2"
  PACKAGENT_TEST_ENV="$env_name" \
  PACKAGENT_TEST_COMMAND="$command" \
  unshare --user --mount --map-root-user --propagation private bash -lc '
    set -euo pipefail
    export PACKAGENT_NAMESPACE_ACTIVE=1
    export PACKAGENT_SHELL_HOOK=1
    export PACKAGENT_SHELL=bash
    eval "$(packagent deactivate)"
    if [ "$PACKAGENT_TEST_ENV" != "base" ]; then
      eval "$(packagent activate "$PACKAGENT_TEST_ENV")"
    fi
    eval "$PACKAGENT_TEST_COMMAND"
  '
}

start_managed_shell_session() {
  local env_name="$1"
  local script_path="$2"
  local ready_path="$3"
  local release_path="$4"
  local log_path="/tmp/packagent-${env_name}.session.log"
  PACKAGENT_TEST_ENV="$env_name" \
  PACKAGENT_SESSION_SCRIPT="$script_path" \
  SESSION_READY="$ready_path" \
  SESSION_RELEASE="$release_path" \
  unshare --user --mount --map-root-user --propagation private bash -lc '
    set -euo pipefail
    export PACKAGENT_NAMESPACE_ACTIVE=1
    export PACKAGENT_SHELL_HOOK=1
    export PACKAGENT_SHELL=bash
    eval "$(packagent deactivate)"
    if [ "$PACKAGENT_TEST_ENV" != "base" ]; then
      eval "$(packagent activate "$PACKAGENT_TEST_ENV")"
    fi
    bash "$PACKAGENT_SESSION_SCRIPT"
  ' >"$log_path" 2>&1 &
  STARTED_SESSION_PID="$!"
}

main() {
  export HOME="${HOME:-/home/tester}"
  export PATH="$HOME/.local/bin:$PATH"

  echo "== tool versions =="
  python3 --version
  uv --version
  npm --version
  codex --version || true
  unshare --version || true

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
  local packagent_bin
  packagent_bin="$(command -v packagent)"

  echo "== install shell integration =="
  packagent init --shell bash >/tmp/packagent-init.txt
  grep -q $'initialized\tbash\t' /tmp/packagent-init.txt || fail "packagent init did not report bash setup"
  grep -q 'eval "$(packagent shell init bash)"' "$HOME/.bashrc" || fail "bashrc was not updated by packagent init"
  packagent shell init bash >/tmp/packagent-shell-init.txt
  grep -q 'packagent shell enter bash' /tmp/packagent-shell-init.txt || fail "shell init did not emit the Linux namespace enter path"
  grep -q "PACKAGENT_NAMESPACE_ACTIVE" /tmp/packagent-shell-init.txt || fail "shell init did not gate activation on namespace state"

  echo "== create environments =="
  packagent create -n codex-with-demo
  packagent create -n second

  local root="$HOME/.packagent-v1"
  local base_home="$root/envs/base/.codex"
  local demo_home="$root/envs/codex-with-demo/.codex"
  local second_home="$root/envs/second/.codex"

  echo "== bootstrap first managed shell and takeover =="
  run_managed_shell "codex-with-demo" '
    [ "$PACKAGENT_ACTIVE_ENV" = "codex-with-demo" ] || exit 1
    [ "$CODEX_HOME" = "$HOME/.codex" ] || exit 1
    [ "$PACKAGENT_BACKING_HOME" = "'"$demo_home"'" ] || exit 1
  '
  assert_not_symlink "$HOME/.codex"
  assert_path_exists "$base_home/AGENTS.md"
  grep -q "Legacy AGENTS content" "$base_home/AGENTS.md" || fail "base env did not import legacy home"

  echo "== verify npm global installs work for the sandbox user =="
  [ "$(npm config get prefix)" = "$HOME/.local" ] || fail "npm global prefix is not user-local"
  npm install -g @openai/codex oh-my-codex >/tmp/packagent-npm-install.txt 2>&1 || {
    cat /tmp/packagent-npm-install.txt >&2
    fail "npm global install failed for sandbox user"
  }
  command -v omx >/dev/null || fail "omx was not installed into the sandbox user's PATH"

  echo "== verify concurrent per-shell ~/.codex activation =="
  local session_one_ready="/tmp/packagent-session-one.ready"
  local session_two_ready="/tmp/packagent-session-two.ready"
  local session_one_release="/tmp/packagent-session-one.release"
  local session_two_release="/tmp/packagent-session-two.release"
  local session_one_script="/tmp/packagent-session-one.sh"
  local session_two_script="/tmp/packagent-session-two.sh"
  rm -f "$session_one_ready" "$session_two_ready" "$session_one_release" "$session_two_release"
  mkfifo "$session_one_release" "$session_two_release"

  cat > "$session_one_script" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[ "$PACKAGENT_ACTIVE_ENV" = "codex-with-demo" ] || exit 1
[ "$CODEX_HOME" = "$HOME/.codex" ] || exit 1
mkdir -p "$HOME/.codex/skills/demo-skill"
cat > "$HOME/.codex/skills/demo-skill/SKILL.md" <<'SKILL'
demo
SKILL
printf 'ready\n' > "$SESSION_READY"
read -r _ < "$SESSION_RELEASE"
EOF
  chmod +x "$session_one_script"

  cat > "$session_two_script" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[ "$PACKAGENT_ACTIVE_ENV" = "second" ] || exit 1
[ "$CODEX_HOME" = "$HOME/.codex" ] || exit 1
[ ! -e "$HOME/.codex/skills/demo-skill/SKILL.md" ] || exit 1
cat > "$HOME/.codex/AGENTS.md" <<'AGENTS'
Second env AGENTS content
AGENTS
printf 'ready\n' > "$SESSION_READY"
read -r _ < "$SESSION_RELEASE"
EOF
  chmod +x "$session_two_script"

  start_managed_shell_session "codex-with-demo" "$session_one_script" "$session_one_ready" "$session_one_release"
  session_one_pid="$STARTED_SESSION_PID"
  start_managed_shell_session "second" "$session_two_script" "$session_two_ready" "$session_two_release"
  session_two_pid="$STARTED_SESSION_PID"

  wait_for_file "$session_one_ready"
  wait_for_file "$session_two_ready"
  assert_path_exists "$demo_home/skills/demo-skill/SKILL.md"
  assert_path_exists "$second_home/AGENTS.md"
  assert_path_missing "$second_home/skills/demo-skill/SKILL.md"
  grep -q "Second env AGENTS content" "$second_home/AGENTS.md" || fail "second shell write did not stay isolated"

  printf 'release\n' > "$session_one_release"
  printf 'release\n' > "$session_two_release"
  wait "$session_one_pid"
  wait "$session_two_pid"

  echo "== force doctor repair in a managed shell =="
  run_managed_shell "second" '
    umount "$HOME/.codex"
    if packagent doctor >/tmp/packagent-doctor-before.txt 2>&1; then
      exit 1
    fi
    packagent doctor --fix >/tmp/packagent-doctor-after.txt
    grep -q "current_env=second" /tmp/packagent-doctor-after.txt
    ! grep -q "^issue=" /tmp/packagent-doctor-after.txt
  '

  echo "== deactivate back to base inside a managed shell =="
  run_managed_shell "second" '
    eval "$(packagent deactivate)"
    [ "$PACKAGENT_ACTIVE_ENV" = "base" ] || exit 1
    grep -q "Legacy AGENTS content" "$HOME/.codex/AGENTS.md" || exit 1
  '

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
