#!/usr/bin/env bash
# check_ask_run.sh — OFFLINE regression pins for tests/ask_run.sh's shell behavior (no container,
# no network). Stubs `docker` and `rsync` on PATH, points HOME at a throwaway dir, and asserts:
#   1. --no-sync is honored ANYWHERE in the args (not just as the 3rd positional), and the alias
#      still resolves to the intended positional (Codex Should-fix #3).
#   2. the pre-delete safety backup prunes to the newest BACKUP_KEEP dirs (Codex Should-fix #4).
#   3. only a nonce-matched artifact can report the dispatched run's verdict.
#   4. suites.py import/invariant failures are reported cleanly, without a Python traceback.
# We stop ask_run.sh right after dispatch by having the `docker` stub write the artifact the wrapper
# then reads — so the whole run stays local and deterministic.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASK_RUN="$HERE/ask_run.sh"
DEV_DIR="$(cd "$HERE/.." && pwd)"
fails=0

run_case() {
  # run_case <label> <sync-expected:yes|no> <expected-alias> -- <ask_run.sh args...>
  local label="$1"; local expect_sync="$2"; local expect_alias="$3"; shift 4
  local sandbox; sandbox="$(mktemp -d)"
  local bin="$sandbox/bin"; mkdir -p "$bin"
  local install="$sandbox/home/.hermes/skills/resumable-script"
  mkdir -p "$install/tests"

  # stub docker: record it ran, then write the ground-truth artifact the wrapper reads back.
  cat > "$bin/docker" <<STUB
#!/usr/bin/env bash
echo "docker \$*" >> "$sandbox/docker.log"
args="\$*"
[[ "\$args" =~ RUN_TIERS_NONCE=([0-9a-f]+) ]] || exit 1
nonce="\${BASH_REMATCH[1]}"
mkdir -p "$install/tests"
cat > "$install/tests/.last_run.json" <<JSON
{"v":1,"overall":"ok","exit":0,"started":"t","finished":"t","nonce":"\$nonce","tiers":[{"name":"tier1-basics","status":"pass","rungs":6}]}
JSON
exit 0
STUB
  # stub rsync: emit a fake dry-run delta (so the backup path triggers) but perform no real copy.
  cat > "$bin/rsync" <<STUB
#!/usr/bin/env bash
for a in "\$@"; do [ "\$a" = "--dry-run" ] && { echo ">f+++++++++ tests/x"; exit 0; }; done
exit 0
STUB
  chmod +x "$bin/docker" "$bin/rsync"

  set +e
  PATH="$bin:$PATH" HOME="$sandbox/home" bash "$ASK_RUN" "$@" >"$sandbox/out.log" 2>&1
  local rc=$?
  set -e

  local ok=1
  [ $rc -eq 0 ] || { echo "  [$label] exit $rc != 0 (out: $(tail -1 "$sandbox/out.log"))"; ok=0; }
  local synced=no
  grep -q "synced dev ->" "$sandbox/out.log" && synced=yes
  [ "$synced" = "$expect_sync" ] || { echo "  [$label] sync=$synced, expected $expect_sync"; ok=0; }
  # the dispatch must pass ask.py the intended ALIAS as its first arg — never the flag string
  if ! grep -Eq "ask\.py $expect_alias( |\$)" "$sandbox/docker.log" 2>/dev/null; then
    echo "  [$label] ask.py did not receive alias '$expect_alias' (log: $(cat "$sandbox/docker.log" 2>/dev/null))"; ok=0; fi
  [ $ok -eq 1 ] && echo "  PASS $label" || fails=$((fails + 1))
  rm -rf "$sandbox"
}

# 1a. --no-sync as a trailing flag after the suite (the case that used to be swallowed as the alias)
run_case "no-sync trailing flag" no qa -- tier1-basics --no-sync
# 1b. --no-sync leading, before positionals
run_case "no-sync leading flag" no qa -- --no-sync tier1-basics
# 1c. no flag -> sync runs; explicit alias still resolves
run_case "explicit alias syncs" yes deepseek -- tier1-basics deepseek

# 2. wrong nonce: a well-formed PASS artifact cannot claim the dispatched run's verdict.
nonce_mismatch_case() {
  local sandbox; sandbox="$(mktemp -d)"
  local bin="$sandbox/bin"; mkdir -p "$bin"
  local install="$sandbox/home/.hermes/skills/resumable-script"
  mkdir -p "$install/tests"
  cat > "$bin/docker" <<STUB
#!/usr/bin/env bash
cat > "$install/tests/.last_run.json" <<JSON
{"v":1,"overall":"ok","exit":0,"started":"t","finished":"t","nonce":"deadbeef","tiers":[{"name":"tier1-basics","status":"pass","rungs":6}]}
JSON
exit 0
STUB
  chmod +x "$bin/docker"
  set +e
  PATH="$bin:$PATH" HOME="$sandbox/home" bash "$ASK_RUN" tier1-basics --no-sync >"$sandbox/out.log" 2>&1
  local rc=$?
  set -e
  if [ $rc -eq 1 ] && grep -q "artifact nonce mismatch" "$sandbox/out.log"; then
    echo "  PASS wrong nonce rejects fabricated PASS"
  else
    echo "  [wrong nonce] exit $rc or diagnostic mismatch (out: $(tail -1 "$sandbox/out.log"))"
    fails=$((fails + 1))
  fi
  rm -rf "$sandbox"
}
nonce_mismatch_case

# 3. client-side suites.py import failures are concise and do not leak a traceback.
clean_validation_case() {
  local sandbox; sandbox="$(mktemp -d)"
  mkdir -p "$sandbox/repo"
  cp -R "$HERE" "$sandbox/repo/tests"
  printf 'raise RuntimeError("broken suite invariants")\n' > "$sandbox/repo/tests/suites.py"
  set +e
  HOME="$sandbox/home" bash "$sandbox/repo/tests/ask_run.sh" tier1-basics >"$sandbox/out.log" 2>&1
  local rc=$?
  set -e
  if [ $rc -eq 2 ] \
      && grep -q "cannot load tests/suites.py (import/invariant error): RuntimeError: broken suite invariants" "$sandbox/out.log" \
      && ! grep -q "Traceback (most recent call last)" "$sandbox/out.log"; then
    echo "  PASS clean suites.py validation failure"
  else
    echo "  [clean validation] exit $rc or output mismatch (out: $(tail -2 "$sandbox/out.log"))"
    fails=$((fails + 1))
  fi
  rm -rf "$sandbox"
}
clean_validation_case

# 4. backup retention: seed >BACKUP_KEEP fake backups + a divergent install, run once, assert prune.
retention_case() {
  local sandbox; sandbox="$(mktemp -d)"
  local bin="$sandbox/bin"; mkdir -p "$bin"
  local skills="$sandbox/home/.hermes/skills"
  local install="$skills/resumable-script"
  mkdir -p "$install/tests"
  # 8 pre-existing backups (older timestamps) — retention keeps newest 5, so 8 -> 5 after one run.
  for i in $(seq -w 1 8); do mkdir -p "$skills/.resumable-script.bak-2020010$i-000000.0"; done
  cat > "$bin/docker" <<STUB
#!/usr/bin/env bash
args="\$*"
[[ "\$args" =~ RUN_TIERS_NONCE=([0-9a-f]+) ]] || exit 1
nonce="\${BASH_REMATCH[1]}"
mkdir -p "$install/tests"
echo "{\"v\":1,\"overall\":\"ok\",\"exit\":0,\"started\":\"t\",\"finished\":\"t\",\"nonce\":\"\$nonce\",\"tiers\":[]}" > "$install/tests/.last_run.json"
exit 0
STUB
  cat > "$bin/rsync" <<STUB
#!/usr/bin/env bash
for a in "\$@"; do [ "\$a" = "--dry-run" ] && { echo ">f+++++++++ tests/x"; exit 0; }; done
exit 0
STUB
  chmod +x "$bin/docker" "$bin/rsync"
  set +e
  PATH="$bin:$PATH" HOME="$sandbox/home" bash "$ASK_RUN" tier1-basics >/dev/null 2>&1
  set -e
  local n; n=$(ls -d "$skills/".resumable-script.bak-* 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" = "5" ]; then echo "  PASS backup retention (8 -> 5)";
  else echo "  [backup retention] kept $n, expected 5"; fails=$((fails + 1)); fi
  rm -rf "$sandbox"
}
retention_case

if [ $fails -eq 0 ]; then echo "check_ask_run: ALL OK"; else echo "check_ask_run: $fails FAILED" >&2; fi
exit $fails
