#!/usr/bin/env sh
set -eu
vllm_version="0.34.0"
uv_version="0.13.9"
if [ "$(uname -s)" != "Linux" ]; then
  echo "vLLM setup runs on Linux or inside WSL."
  exit 1
fi
venv="${WORKBENCH_VLLM_VENV:-$HOME/.local/share/agent-workbench/vllm}"
if [ -d /opt/agent-workbench/vllm ]; then venv=/opt/agent-workbench/vllm; fi
package="vllm==$vllm_version"
if [ "${1:-}" = "--update" ]; then package="vllm"; fi
if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
  if [ "$(id -u)" = 0 ] && command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3-venv
  else
    echo "Install the Python venv module (Debian or Ubuntu: sudo apt-get install python3-venv), then run vLLM setup again."
    exit 1
  fi
fi
python3 -m venv "$venv"
"$venv/bin/python" -m pip install "uv==$uv_version"
"$venv/bin/uv" pip install --python "$venv/bin/python" --torch-backend=auto --upgrade "$package"
mkdir -p "$HOME/.local/bin"
link="$HOME/.local/bin/vllm"
if [ -e "$link" ] && [ ! -L "$link" ]; then
  echo "Kept your existing $link; Agent Workbench uses $venv/bin/vllm."
else
  ln -sfn "$venv/bin/vllm" "$link"
fi
