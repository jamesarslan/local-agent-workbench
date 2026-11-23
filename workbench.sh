#!/usr/bin/env sh
# Create or repair the Python environment beside this script, then run a Workbench script with it.
set -eu

fail() {
  echo "$1" >&2
  exit 1
}

supported='import sys; sys.exit(not (3, 12) <= sys.version_info[:2] <= (3, 14))'
ready="import importlib.util as u, os, sys; s = u.find_spec('pip'); d = os.path.dirname(os.path.dirname(s.origin)) if s and s.origin else None; sys.exit(not ((3, 12) <= sys.version_info[:2] <= (3, 14) and d and any(n.startswith('pip-') and os.path.isfile(os.path.join(d, n, 'INSTALLER')) for n in os.listdir(d))))"
unfinished="import importlib.util as u, os, shutil, sys; d = os.path.dirname(os.path.dirname(u.find_spec('pip').origin)); [shutil.rmtree(os.path.join(d, n), True) for n in os.listdir(d) if os.path.commonpath([d, sys.prefix]) == sys.prefix and (n[0] == '~' or n.endswith('.dist-info') and not os.path.isfile(os.path.join(d, n, 'INSTALLER')))]"

WORKBENCH_CALLER_CWD=$PWD
export WORKBENCH_CALLER_CWD
CDPATH='' cd -- "$(dirname -- "$0")"
if [ ! -f requirements.lock ] || [ ! -f desktop_app.py ]; then
  fail "Start workbench.sh from the Agent Workbench folder itself; a link to it or a copy of it cannot find its files."
fi

venv="${WORKBENCH_VENV:-.venv-linux}"
if [ ! -x "$venv/bin/python" ] || ! "$venv/bin/python" -c "$ready" >/dev/null 2>&1; then
  interpreter=""
  if [ -e ./python/bin/python3.12 ]; then
    if ! ./python/bin/python3.12 -c "$supported" >/dev/null 2>&1; then
      if [ "$(uname -s)" = Darwin ]; then
        fail "The Python bundled with this kit could not start. If this folder came from a browser download, start it with: sh \"Launch Workbench.command\""
      fi
      fail "The Python bundled with this kit cannot run on this computer ($(uname -sm)). Download the kit made for this system and start again."
    fi
    interpreter=./python/bin/python3.12
  elif [ -n "${WORKBENCH_BOOTSTRAP_PYTHON:-}" ]; then
    "$WORKBENCH_BOOTSTRAP_PYTHON" -c "$supported" >/dev/null 2>&1 ||
      fail "WORKBENCH_BOOTSTRAP_PYTHON ($WORKBENCH_BOOTSTRAP_PYTHON) is not Python 3.12, 3.13 or 3.14. Point it at one of those and start again."
    interpreter=$WORKBENCH_BOOTSTRAP_PYTHON
  else
    for candidate in python3.12 python3.13 python3.14 python3 python; do
      if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "$supported" >/dev/null 2>&1; then
        interpreter=$candidate
        break
      fi
    done
    [ -n "$interpreter" ] ||
      fail "Agent Workbench needs Python 3.12, 3.13 or 3.14. Install one of them and start again."
  fi
  if [ -e "$venv" ] || [ -L "$venv" ]; then
    [ -f "$venv/pyvenv.cfg" ] ||
      fail "$venv exists but is not a Python environment made by Agent Workbench. Move it away, or set WORKBENCH_VENV to another folder, and start again."
    echo "Rebuilding the Python environment in $venv because it was incomplete, moved, or made by another Python."
    rm -rf "$venv"
  fi
  if ! "$interpreter" -m venv "$venv"; then
    if [ -f "$venv/pyvenv.cfg" ]; then rm -rf "$venv"; fi
    fail "Python could not create its environment in $venv. Make the Agent Workbench folder writable; on Debian or Ubuntu also run: sudo apt-get install python3-venv"
  fi
fi
if ! cmp -s requirements.lock "$venv/.workbench-requirements"; then
  [ -w "$venv" ] ||
    fail "The Python environment in $venv needs new packages, but this user cannot write to it. Make the Agent Workbench folder writable for this user, then start Agent Workbench again."
  echo "Installing Python packages from pypi.org (first start and after an update)."
  # A package whose install was cut off has no INSTALLER file; pip would call it installed, so forget it first.
  "$venv/bin/python" -I -c "$unfinished" >/dev/null 2>&1 || true
  "$venv/bin/python" -m pip install --disable-pip-version-check --no-input --require-hashes --only-binary :all: -r requirements.lock ||
    fail "The Python packages could not be installed. Check the connection to pypi.org and files.pythonhosted.org, then start Agent Workbench again."
  cp requirements.lock "$venv/.workbench-requirements"
fi
if [ "$#" -eq 0 ]; then set -- desktop_app.py; fi
if [ "$1" = desktop_app.py ] && [ "$(uname -s)" = Linux ] && [ -z "${WORKBENCH_SANDBOX_CHECKED:-}" ] && [ -f ./setup-linux.sh ]; then
  sh ./setup-linux.sh --sandbox || true
fi
exec "$venv/bin/python" "$@"
