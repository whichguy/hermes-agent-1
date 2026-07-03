#!/usr/bin/env bash
# ask_run.sh — run the test suites THROUGH THE ASK SKILL (the sanctioned agent-mode QA pattern):
# a real in-container Hermes agent executes the suite; the verdict comes from the ground-truth
# artifact tests/.last_run.json with the host-minted dispatch nonce, never from the agent's
# narrative (and never from ask.py's exit code, which reflects dispatch success only).
#
#   ./tests/ask_run.sh                       # climb all tiers (simplest first), alias 'qa'
#   ./tests/ask_run.sh tier1-basics          # one suite (any tests/suites.py name)
#   ./tests/ask_run.sh tiers deepseek        # full climb on a different model alias
#   ./tests/ask_run.sh tier2-routing --no-sync   # skip the dev->installed sync (flag, any position)
set -euo pipefail

# --no-sync is a FLAG accepted anywhere; the remaining positionals are [suite] [alias].
NOSYNC=""
POS=()
for arg in "$@"; do
  if [ "$arg" = "--no-sync" ]; then NOSYNC="--no-sync"; else POS+=("$arg"); fi
done
SUITE="${POS[0]:-tiers}"
ALIAS="${POS[1]:-qa}"
BACKUP_KEEP=5                                        # newest N safety backups retained

DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="$HOME/.hermes/skills/resumable-script"
CONTAINER_DIR="/opt/data/skills/resumable-script"
ASK_PY="/opt/data/skills/productivity/ask/scripts/ask.py"
ARTIFACT="$INSTALL_DIR/tests/.last_run.json"
EXCLUDES=(--exclude .git --exclude evals/.venv --exclude 'evals/author_flow_eval/artifacts'
          --exclude 'evals/author_flow_eval/fixtures' --exclude '__pycache__'
          --exclude 'tests/.last_run.json')

# 1. validate the suite argument BEFORE anything is dispatched (never interpolate unvalidated text)
errf=$(mktemp)
if ! SUITE_OK=$(cd "$DEV_DIR/tests" && python3 -c "
import sys
try:
    from suites import SUITES
except Exception as e:
    print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
    sys.exit(1)
name = '$SUITE'
print('ok' if name == 'tiers' or name in SUITES else 'bad')" 2>"$errf"); then
  echo "cannot load tests/suites.py (import/invariant error): $(head -n 1 "$errf")" >&2
  rm -f "$errf"
  exit 2
fi
rm -f "$errf"
if [ "$SUITE_OK" != "ok" ]; then
  echo "unknown suite: '$SUITE' (use 'tiers' or a name from tests/suites.py)" >&2
  exit 2
fi

# 2. safe sync dev -> installed (divergence check + backup before rsync --delete)
if [ "$NOSYNC" != "--no-sync" ]; then
  if [ -d "$INSTALL_DIR" ]; then
    DELTA=$(rsync -a --delete --dry-run --itemize-changes "${EXCLUDES[@]}" "$DEV_DIR/" "$INSTALL_DIR/" | head -50)
    if [ -n "$DELTA" ]; then
      BAK="$HOME/.hermes/skills/.resumable-script.bak-$(date +%Y%m%d-%H%M%S).$$"
      echo "installed copy differs from dev (delta below); backing it up to $BAK before sync"
      echo "$DELTA" | head -10
      mkdir -p "$BAK"
      rsync -a "${EXCLUDES[@]}" "$INSTALL_DIR/" "$BAK/"     # same excludes as the sync
      # retention: keep the newest BACKUP_KEEP (timestamp prefix sorts chronologically). BSD-portable
      # (no GNU `head -n -N` / `xargs -r`): newest-first, drop everything past the keep count.
      ls -d "$HOME/.hermes/skills/".resumable-script.bak-* 2>/dev/null | sort -r \
        | tail -n +"$((BACKUP_KEEP + 1))" | while IFS= read -r d; do rm -rf "$d"; done || true
    fi
  fi
  mkdir -p "$INSTALL_DIR"
  rsync -a --delete "${EXCLUDES[@]}" "$DEV_DIR/" "$INSTALL_DIR/"
  echo "synced dev -> $INSTALL_DIR"
fi

# 3. dispatch through ask (agent mode, file+terminal toolsets, in-container cwd)
NONCE=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
mkdir -p "$HOME/.hermes/tmp"
rm -f "$ARTIFACT"
if [ "$SUITE" = "tiers" ]; then
  CMD="cd $CONTAINER_DIR && TMPDIR=/opt/data/tmp RUN_TIERS_NONCE=$NONCE python3 tests/run_tiers.py"
else
  CMD="cd $CONTAINER_DIR && TMPDIR=/opt/data/tmp RUN_TIERS_NONCE=$NONCE python3 tests/run_tiers.py --only $SUITE"
fi
echo "dispatching via ask ($ALIAS): $CMD"
PROMPT="Run this exact command and report the outcome:

$CMD

Report each tier's PASS/FAIL, the final exit code, and any failing rung names verbatim. Do not modify any files."
set +e
docker exec hermes python3 "$ASK_PY" "$ALIAS" \
  --prompt "$PROMPT" \
  --mode agent --toolsets file,terminal --cwd "$CONTAINER_DIR" \
  --timeout 3600 --max-turns 15
DISPATCH_RC=$?
set -e
[ $DISPATCH_RC -ne 0 ] && echo "(ask dispatch exited $DISPATCH_RC — checking the artifact anyway)"

# 4. ground truth: the artifact the suite itself wrote (host-visible via the ~/.hermes mount)
if [ ! -f "$ARTIFACT" ]; then
  echo "FAIL: no ground-truth artifact at $ARTIFACT — the dispatched agent never ran the suite." >&2
  exit 1
fi
echo ""
echo "--- ground truth ($ARTIFACT) ---"
python3 - "$ARTIFACT" "$NONCE" <<'PYEOF'
import json, sys
a = json.load(open(sys.argv[1]))
expected = sys.argv[2]
got = a.get("nonce")
if got != expected:
    print("FAIL: artifact nonce mismatch — the reported run is not the one we dispatched (stale or fabricated)",
          file=sys.stderr)
    sys.exit(1)
for t in a["tiers"]:
    print("%-18s %s (%d rungs)" % (t["name"], t["status"], t["rungs"]))
print("overall:", a["overall"], "| started:", a["started"], "| finished:", a["finished"])
sys.exit(a.get("exit", 1))
PYEOF
