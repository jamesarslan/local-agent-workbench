#!/bin/sh
# Start Agent Workbench on an Apple Silicon Mac.
set -eu
here=$(dirname -- "$0")
if [ "$(uname -sm)" != "Darwin arm64" ]; then
  echo "Agent Workbench for macOS needs Apple Silicon and a Terminal that is not running under Rosetta." >&2
  exit 1
fi
# A browser download marks every extracted file, and macOS then refuses to start the bundled Python.
for marked in "$0" "$here/python/bin/python3.12"; do
  if /usr/bin/xattr -p com.apple.quarantine "$marked" >/dev/null 2>&1; then
    echo "Removing the macOS download mark (quarantine) from $here so the bundled Python can start."
    /usr/bin/xattr -drs com.apple.quarantine "$here/" 2>/dev/null || true
    break
  fi
done
exec sh "$here/workbench.sh" "$@"
