# Resolve an interpreter new enough to run this codebase.
#
# Sourced by doctor.sh and deploy.sh; sets PY to an absolute interpreter path.
#
# 3.11 is the floor, and it is not arbitrary: the code stores UTC timestamps
# with a trailing "Z" and reads them back with datetime.fromisoformat, which
# only learned to accept that suffix in 3.11. On an older interpreter the
# suite fails six tests inside approvals — far enough from the cause to cost
# an afternoon. macOS still ships 3.9 as /usr/bin/python3, so the default
# `python3` on a Mac is below the floor.
#
# Lambda runs 3.12 and the layer is built for 3.12, so preferring 3.12 here
# also means the tests run on the interpreter that production uses.
PY_MIN_MAJOR=3
PY_MIN_MINOR=11

_py_ok() {
  [ -n "$1" ] && command -v "$1" >/dev/null 2>&1 && "$1" -c "
import sys
sys.exit(0 if sys.version_info >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)
" >/dev/null 2>&1
}

resolve_python() {
  # A project venv wins: it is the one place the dev dependencies are known
  # to be installed.
  for cand in "$ROOT/.venv/bin/python" python3.13 python3.12 python3.11 python3; do
    if _py_ok "$cand"; then
      PY="$(command -v "$cand" 2>/dev/null || echo "$cand")"
      PY_VERSION="$("$PY" --version 2>&1)"
      return 0
    fi
  done
  PY=""
  PY_VERSION=""
  return 1
}

python_floor_message() {
  echo "Python ${PY_MIN_MAJOR}.${PY_MIN_MINOR}+ is required and none was found."
  echo "        Found: $(python3 --version 2>&1 || echo 'no python3')"
  echo "        macOS ships 3.9; install a newer one with:"
  echo "            brew install python@3.12"
}
