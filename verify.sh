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
FAST=0; [ "${1:-}" = "--fast" ] && FAST=1

export BATON_FORBID_REAL_DISPATCH=1
export PYTHONDONTWRITEBYTECODE=1
# src layout: the package under test is src/, not the repo root.
export PYTHONPATH="$ROOT/src:$ROOT"

PASS=0; FAIL=0
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/        /'; }
head_() { printf '\n--- %s\n' "$1"; }

# --------------------------------------------------------------------------- #
head_ "1. Sandbox safety (this repo must not be able to touch canonical)"

# The point of this check is that development cannot reach the CANONICAL
# Company OS state DB, which holds irreplaceable runs. On a machine where no
# canonical checkout exists — CI, a contributor's laptop — there is nothing to
# protect, so the check has no subject and skips rather than failing a build for
# a risk that is not present.
case "$ROOT" in
  "$HOME"/unlimited/*) ok "building inside the ~/unlimited sandbox" ;;
  *)
    if [ -e "$HOME/company-os" ] || [ -e "$HOME/.company-os" ]; then
      bad "baton is outside ~/unlimited" "refusing to run — see the design note, trap 7"
    else
      printf '  skip  no canonical Company OS on this machine; nothing to sandbox from\n'
    fi
    ;;
esac

# Exactly two files may CONSTRUCT a canonical path, and both do it only to
# refuse it. Everything else — tests included — imports those constants. That
# makes "could this write to canonical?" a one-line grep instead of a review.
HITS=$(grep -rln --include='*.py' \
         -e 'Path.home() / "company-os"' \
         -e "Path.home() / '.company-os'" \
         -e 'Path.home() / ".company-os"' . 2>/dev/null \
       | grep -v 'company_os/state.py' \
       | grep -v 'company_os/registry.py')
if [ -n "$HITS" ]; then
  bad "a file outside the guards constructs a canonical path" "$HITS"
else
  ok "only the guard modules name the canonical paths"
fi

# --------------------------------------------------------------------------- #
head_ "2. Zero dependencies"

if OUT=$(python3 -m unittest tests.test_isolation 2>&1); then
  ok "isolation: core/providers/adapters layering + zero deps"
else
  bad "isolation" "$(echo "$OUT" | tail -20)"
fi

if grep -q 'dependencies = \[\]' pyproject.toml; then
  ok "pyproject declares zero runtime dependencies"
else
  bad "baton-kernel grew a runtime dependency" "$(grep -A3 '^dependencies' pyproject.toml)"
fi

# tomllib is 3.11+, and baton's floor is 3.10, so parse it only where we can.
# A malformed pyproject means `pip install baton-kernel` fails for everyone.
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  if OUT=$(python3 -c '
import pathlib, tomllib
d = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
assert d["project"]["name"] == "baton-kernel", d["project"]["name"]
assert d["project"]["dependencies"] == [], d["project"]["dependencies"]
assert d["project"]["requires-python"] == ">=3.10"
print("ok")' 2>&1); then
    ok "pyproject parses, names baton-kernel, declares no deps"
  else
    bad "pyproject.toml is malformed" "$(echo "$OUT" | tail -5)"
  fi
else
  printf '  skip  python %s has no tomllib; pyproject parsed on 3.11+ only\n' \
    "$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
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
sys.path.insert(0, "src")
os.environ["BATON_FORBID_REAL_DISPATCH"] = "1"
from baton.adapters.company_os import dispatch as d
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
head_ "5. Tier-2 wiring (dry run only — tier 2 itself is never run here)"

if [ -f "$HOME/.unlimited-os/state.db" ]; then
  if OUT=$(python3 bench/replay.py --selftest 2>&1); then
    ok "bench/replay.py --selftest (stub model, \$0)"
  else
    bad "bench/replay.py --selftest" "$(echo "$OUT" | tail -10)"
  fi
else
  printf '  skip  fork corpus absent — replay wiring unproven\n'
fi

# A replay that runs without an explicit flag is a bill waiting to happen.
if python3 bench/replay.py -n 1 >/dev/null 2>&1; then
  bad "replay ran with no flag" "tier 2 must never start by accident"
else
  ok "replay refuses to run without --dry-run or --confirm-spend"
fi

# --------------------------------------------------------------------------- #
head_ "5b. Docs cannot drift from the code"
if python3 -c '
import sys, pathlib
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import gen_api_docs
sys.exit(0 if pathlib.Path("docs/api.md").read_text() == gen_api_docs.render() else 1)' 2>/dev/null
then ok "docs/api.md matches baton.__all__"
else bad "docs/api.md is stale — run scripts/gen_api_docs.py"; fi

if python3 -c '
import sys, pathlib
sys.path.insert(0, "src")
import baton
doc = pathlib.Path("docs/api.md").read_text()
sys.exit(1 if [n for n in baton.__all__ if f"`{n}`" not in doc] else 0)' 2>/dev/null
then ok "every public symbol appears in the reference"
else bad "a public symbol is missing from docs/api.md"; fi

# This check used to REQUIRE `pip install baton-kernel` in the quickstart -- a
# command that 404s, because the package is not on PyPI. A gate that enforces a
# false instruction is worse than no gate. tests/test_readme_install.py now holds
# the docs and PUBLISHED_TO_PYPI in agreement in both directions; here we only
# assert the quickstart gives the reader SOME command that works today.
if python3 -c '
import pathlib, sys
d = pathlib.Path("docs/quickstart.md")
sys.exit(0 if d.is_file() and "pip install" in d.read_text() else 1)' 2>/dev/null
then ok "the quickstart exists and shows a runnable install line"
else bad "docs/quickstart.md missing or has no install line"; fi

head_ "6. The ARTIFACT, not just the repo"

# Everything above runs with PYTHONPATH=src, which is fast and correct about the
# CODE — and makes every packaging mistake invisible. Users never run src/; they
# run a wheel unpacked somewhere else. This builds that wheel and tests it.
AUDIT="$HOME/.claude/skills/shipping-python-libraries/audit.sh"
if [ "$FAST" = "1" ]; then
  printf '  skip  packaging audit (--fast)\n'
elif [ ! -x "$AUDIT" ] && [ ! -f "$AUDIT" ]; then
  printf '  skip  packaging audit not installed\n'
else
  if OUT=$(bash "$AUDIT" "$ROOT" 2>&1); then
    ok "packaging audit — $(echo "$OUT" | grep -E '^passed' | head -1)"
  else
    bad "packaging audit" "$(echo "$OUT" | grep -E '^  FAIL' | head -10)"
  fi
fi

# --------------------------------------------------------------------------- #
printf '\n=====================================\n'
printf 'passed %d   failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && { printf 'VERIFY: PASS\n'; exit 0; } || { printf 'VERIFY: FAIL\n'; exit 1; }
