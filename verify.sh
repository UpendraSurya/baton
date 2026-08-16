#!/usr/bin/env bash
# verify.sh — the ONE definition of "done" for baton.
#
# Tier 1 only: scripted stub dispatch, deterministic, and it costs $0. Real model
# calls are actively BLOCKED here (BATON_FORBID_REAL_DISPATCH) so no amount of
# accidental wiring can turn the gate into a bill. Tier 2 is bench/replay.py and
# is never run from this script.
#
#   bash verify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

export BATON_FORBID_REAL_DISPATCH=1
export PYTHONDONTWRITEBYTECODE=1

PASS=0; FAIL=0
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/        /'; }
head_() { printf '\n--- %s\n' "$1"; }

# --------------------------------------------------------------------------- #
head_ "1. Sandbox safety (this repo must not be able to touch canonical)"

case "$ROOT" in
  "$HOME"/unlimited/*) ok "building inside the ~/unlimited sandbox" ;;
  *) bad "baton is outside ~/unlimited" "refusing to run — see the design note, trap 7" ;;
esac

HITS=$(grep -rn --include='*.py' -e '\.company-os' -e 'HOME/company-os' . 2>/dev/null \
         | grep -v 'unlimited-os' | grep -v '_CANONICAL' | grep -v 'canonical')
if [ -n "$HITS" ]; then
  bad "source references canonical company-os paths" "$HITS"
else
  ok "no unguarded canonical company-os paths in source"
fi

# --------------------------------------------------------------------------- #
head_ "2. Zero dependencies"

if OUT=$(python3 -m unittest tests.test_kernel_isolation 2>&1); then
  ok "kernel isolation (stdlib + kernel only, no consumer vocabulary)"
else
  bad "kernel isolation" "$(echo "$OUT" | tail -20)"
fi

if [ -f requirements.txt ] || [ -f Pipfile ] || [ -f poetry.lock ]; then
  bad "a dependency manifest appeared" "baton is stdlib-only by design"
else
  ok "no dependency manifest"
fi

# --------------------------------------------------------------------------- #
head_ "3. Tier-1 suite (scripted dispatch, \$0)"

if OUT=$(python3 -m unittest discover -s tests -t . 2>&1); then
  ok "tier-1 suite — $(echo "$OUT" | grep -E '^Ran ' | head -1)"
else
  bad "tier-1 suite" "$(echo "$OUT" | tail -30)"
fi

# --------------------------------------------------------------------------- #
head_ "4. The gate spent nothing"

# The adapter's real dispatch refuses to run under BATON_FORBID_REAL_DISPATCH.
# Prove the refusal is live rather than trusting that no test called it.
if OUT=$(python3 - <<'PY' 2>&1
import os, sys
sys.path.insert(0, ".")
os.environ["BATON_FORBID_REAL_DISPATCH"] = "1"
from adapters.company_os import dispatch as d
try:
    d.real_dispatch(None, None, "prompt")
except RuntimeError as e:
    print("refused:", e); sys.exit(0)
print("NOT REFUSED"); sys.exit(1)
PY
); then
  ok "real dispatch refuses to run under the gate"
else
  bad "real dispatch was NOT blocked" "$OUT"
fi

# --------------------------------------------------------------------------- #
printf '\n=====================================\n'
printf 'passed %d   failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && { printf 'VERIFY: PASS\n'; exit 0; } || { printf 'VERIFY: FAIL\n'; exit 1; }
