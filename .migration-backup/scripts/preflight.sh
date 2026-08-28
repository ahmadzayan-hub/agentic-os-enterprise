#!/usr/bin/env bash
# Pre-push checks, in the order CI runs them.
#
# Written because the same verification mistake was made twice: `gitleaks
# detect` scans *committed history*, so running it before committing checks
# everything except the file you just wrote. A secret-shaped literal in a new
# test passed local review twice and failed in CI both times.
#
#   scripts/preflight.sh          # working tree
#   scripts/preflight.sh --staged # what is about to be committed
#
# Every check is the same one CI runs. A tool that is not installed is
# reported as skipped rather than silently passing — a skipped check must
# never look like a passing one.

set -uo pipefail
cd "$(dirname "$0")/.."

status=0
skipped=()

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1"; status=1; }
pass() { printf '\033[32mok\033[0m   %s\n' "$1"; }

python_bin() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi
}
PY="$(python_bin)"

step "ruff lint"
if "$PY" -m ruff check . -q; then pass "no lint findings"; else fail "ruff check"; fi

step "ruff format"
if "$PY" -m ruff format --check . >/dev/null; then pass "formatted"; else fail "ruff format --check"; fi

step "secret scan"
if command -v gitleaks >/dev/null 2>&1; then
  # Both halves matter. `protect --staged` sees the change you are about to
  # commit; `detect` sees the history you are about to push. Neither alone
  # would have caught the literal that got through.
  if [ "${1:-}" = "--staged" ]; then
    if gitleaks protect --staged --source . --no-banner --redact >/dev/null 2>&1; then
      pass "staged diff clean"
    else
      fail "gitleaks found a secret in the staged diff"
    fi
  fi
  if gitleaks detect --source . --no-banner --redact >/dev/null 2>&1; then
    pass "history clean"
  else
    fail "gitleaks found a secret in committed history"
  fi
else
  skipped+=("gitleaks (not installed)")
fi

step "bandit"
if "$PY" -m bandit --version >/dev/null 2>&1; then
  if "$PY" -m bandit -c pyproject.toml -r packages/agentic_os/src/agentic_os -ll -q >/dev/null 2>&1; then
    pass "no medium or high findings"
  else
    fail "bandit"
  fi
else
  skipped+=("bandit (pip install 'bandit[toml]')")
fi

step "tests"
# Bare pytest, as CI runs it — `python -m pytest` adds the working directory
# to sys.path and would hide an import error that CI will not.
#
# Services are required only when they are actually reachable. Demanding them
# unconditionally would make this script unusable without a local Redis, and
# reporting a skip as a pass is exactly what the gate exists to prevent — so
# any service that is not required gets named below rather than passing
# silently.
require=""
if "$PY" - <<'PROBE' 2>/dev/null; then require="db"; fi
import sys
sys.path.insert(0, ".")
from tests.conftest import service_available
sys.exit(0 if service_available("db") else 1)
PROBE
if "$PY" - <<'PROBE' 2>/dev/null; then require="${require:+$require,}redis"; fi
import sys
sys.path.insert(0, ".")
from tests.conftest import service_available
sys.exit(0 if service_available("redis") else 1)
PROBE

if command -v pytest >/dev/null 2>&1 || [ -x .venv/bin/pytest ]; then
  PYTEST=$([ -x .venv/bin/pytest ] && echo .venv/bin/pytest || echo pytest)
  if AGENTIC_REQUIRE_SERVICES="$require" "$PYTEST" tests -q; then
    pass "suite green${require:+ (required: $require)}"
  else
    fail "pytest"
  fi
  for service in db redis; do
    case ",$require," in
      *",$service,"*) ;;
      *) skipped+=("tests needing $service (service unreachable)") ;;
    esac
  done
else
  skipped+=("pytest (not installed)")
fi

if [ ${#skipped[@]} -gt 0 ]; then
  printf '\n\033[33mskipped:\033[0m %s\n' "${skipped[*]}"
  printf 'A skipped check is not a passing one — CI will run it.\n'
fi

printf '\n'
if [ "$status" -eq 0 ]; then
  printf '\033[32mpreflight passed\033[0m\n'
else
  printf '\033[31mpreflight failed\033[0m\n'
fi
exit "$status"
