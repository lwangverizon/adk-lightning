#!/usr/bin/env bash
# Offline component checks; native runtime contracts and Flutter run separately.
set -u
cd "$(dirname "$0")"

# The uv environment when there is one, so the litellm-backed checks in
# test_gateway.py actually run instead of skipping. $PYTHON overrides; a bare
# "$PY" is the fallback and is enough for everything except those.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
echo "interpreter: $PY"
echo

fail=0
check() {
  "$@" || fail=1
}
if [ "$#" -eq 0 ]; then
  check "$PY" -m pytest tests --ignore=tests/runtime --ignore=tests/tools -q
elif [ "$#" -eq 2 ] && [ "$1" = "--junit" ]; then
  check "$PY" -m pytest tests --ignore=tests/runtime --ignore=tests/tools --junitxml="$2" -q
else
  echo "usage: bash run_tests.sh [--junit report.xml]" >&2
  exit 2
fi
echo
echo "validating maintained examples:"
check "$PY" -m scripts.check_examples
exit $fail
