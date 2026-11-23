#!/bin/sh
# Install Agent Workbench for the current user: fetch the pinned kit, check its SHA-256, unpack it, add a workbench command.
set -eu

VERSION="0.4.0"
REPOSITORY="jamesarslan/local-agent-workbench"

say() {
  printf '%s\n' "$1"
}

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<EOF
Agent Workbench $VERSION installer for macOS (Apple Silicon) and Linux (x86-64).

  sh install.sh                 download the kit for this computer, verify it and install it
  sh install.sh --from DIR      install from release files already in DIR (the kit archive and SHA256SUMS)
  sh install.sh --uninstall     remove the app and the workbench command; your data is kept
  --yes                         answer yes to the question about PATH
  --no-modify-path              never ask about, or change, a shell profile (also WORKBENCH_NO_MODIFY_PATH=1)

Folders:
  the app        ~/.local/lib/agent-workbench    (set WORKBENCH_INSTALL_DIR to change it)
  the command    ~/.local/bin/workbench          (set WORKBENCH_BIN_DIR to change its folder)
  your data      ~/Library/Application Support/Agent Workbench on macOS,
                 \${XDG_DATA_HOME:-~/.local/share}/agent-workbench on Linux
                 (the workbench command uses WORKBENCH_DATA instead when you set it)
No step uses sudo.
EOF
}

target() {
  if [ -n "${WORKBENCH_INSTALL_TARGET:-}" ]; then
    echo "$WORKBENCH_INSTALL_TARGET"
    return
  fi
  system=$(uname -s)
  machine=$(uname -m)
  case "$system/$machine" in
    Darwin/arm64) echo macos-arm64 ;;
    Darwin/x86_64)
      if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
        fail "This Terminal is running under Rosetta. Open a Terminal that is not set to open using Rosetta and run the installer again."
      fi
      fail "Agent Workbench has no kit for Intel Macs. Download the source archive from https://github.com/$REPOSITORY/releases and run it with your own Python 3.12."
      ;;
    Linux/x86_64 | Linux/amd64)
      if ldd --version 2>&1 | grep -qi musl; then
        fail "Agent Workbench kits need glibc, and this system uses musl. Download the source archive from https://github.com/$REPOSITORY/releases and run it with your own Python 3.12."
      fi
      echo linux-x64
      ;;
    *)
      fail "Agent Workbench has kits for macOS on Apple Silicon and Linux on x86-64, not for $system $machine. Download the source archive from https://github.com/$REPOSITORY/releases and run it with your own Python 3.12."
      ;;
  esac
}

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --retry 2 --output "$2" "$1" && return 0
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --https-only --output-document "$2" "$1" && return 0
  else
    fail "Neither curl nor wget was found. Install one of them and run the installer again."
  fi
  fail "Could not download $1. Check the connection to github.com and run the installer again."
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f 1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d ' ' -f 1
  else
    fail "Neither sha256sum nor shasum was found, so the kit cannot be verified. Install one of them and run the installer again."
  fi
}

quote() {
  printf "'%s'" "$(printf %s "$1" | sed "s/'/'\\\\''/g")"
}

confirm() {
  if [ "$yes" = yes ]; then return 0; fi
  if ! (exec </dev/tty) 2>/dev/null; then return 1; fi
  printf '%s [y/N] ' "$1" >/dev/tty
  read -r reply </dev/tty || reply=""
  case "$reply" in y | Y | yes | Yes | YES) return 0 ;; esac
  return 1
}

is_install() {
  [ -f "$1/workbench.sh" ] && [ -f "$1/desktop_app.py" ] && [ ! -e "$1/.git" ]
}

folders() {
  app="${WORKBENCH_INSTALL_DIR:-$HOME/.local/lib/agent-workbench}"
  bin="${WORKBENCH_BIN_DIR:-$HOME/.local/bin}"
  if [ "$(uname -s)" = Darwin ]; then
    data="$HOME/Library/Application Support/Agent Workbench"
    entry="Launch Workbench.command"
  else
    case "${XDG_DATA_HOME:-}" in
      /*) data="$XDG_DATA_HOME/agent-workbench" ;;
      *) data="$HOME/.local/share/agent-workbench" ;;
    esac
    entry="workbench.sh"
  fi
  case "$app" in /*) ;; *) fail "WORKBENCH_INSTALL_DIR must be an absolute path." ;; esac
  case "$bin" in /*) ;; *) fail "WORKBENCH_BIN_DIR must be an absolute path." ;; esac
}

write_command() {
  mkdir -p "$bin" && cat >"$bin/workbench.new" <<EOF && chmod 755 "$bin/workbench.new" && mv -f "$bin/workbench.new" "$bin/workbench"
#!/bin/sh
# Agent Workbench $VERSION command, written by install.sh. Delete this file to remove the command.
if [ -z "\${WORKBENCH_DATA:-}" ]; then WORKBENCH_DATA=$(quote "$data"); fi
export WORKBENCH_DATA
case "\${1:-}" in
  "") set -- desktop_app.py ;;
  *.py | -m | -c) ;;
  *) set -- cli.py "\$@" ;;
esac
exec sh $(quote "$app/$entry") "\$@"
EOF
}

path_step() {
  case ":$PATH:" in *":$bin:"*) return 0 ;; esac
  line="export PATH=$(quote "$bin"):\"\$PATH\""
  case "$(basename "${SHELL:-sh}")" in
    zsh) rc="${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash) if [ "$(uname -s)" = Darwin ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
    *) rc="" ;;
  esac
  if [ -n "$rc" ] && [ -f "$rc" ] && grep -qF "$bin" "$rc"; then
    say "$bin is already named in $rc; open a new terminal to use the workbench command."
    return 0
  fi
  if [ "$modify" = yes ] && [ -n "$rc" ] && confirm "$bin is not on your PATH. Add it by appending one line to $rc?"; then
    if { printf '\n# Added by the Agent Workbench installer\n%s\n' "$line" >>"$rc"; } 2>/dev/null; then
      say "Added $bin to PATH in $rc. Open a new terminal to use the workbench command."
      return 0
    fi
    say "$rc could not be changed, so PATH was left alone."
  fi
  say "$bin is not on your PATH. To use the workbench command, add this line to your shell profile:"
  say "  $line"
  say "Until then, start it with: $(quote "$bin/workbench")"
}

uninstall() {
  folders
  found=no
  if [ -e "$app" ]; then
    is_install "$app" ||
      fail "$app does not look like an Agent Workbench install, so it was left alone. Remove it yourself if it is no longer needed."
    if [ -d "$app/.local" ]; then
      fail "$app/.local holds settings or models from running the app without the workbench command. Move that folder somewhere safe, then run the uninstall again."
    fi
    rm -rf "$app"
    say "Removed $app."
    found=yes
  fi
  if [ -f "$bin/workbench" ] && grep -q "Agent Workbench" "$bin/workbench"; then
    rm -f "$bin/workbench"
    say "Removed $bin/workbench."
    found=yes
  fi
  if [ "$found" = no ]; then
    say "No Agent Workbench install was found in $app. If it was installed with WORKBENCH_INSTALL_DIR or WORKBENCH_BIN_DIR set, set them to the same folders and run the uninstall again."
  fi
  say "Your settings, models and downloads are kept in: $data"
  say "To delete them as well: rm -rf $(quote "$data")"
  say "Saved API keys stay in the system credential vault under the name Agent Workbench until you delete them there."
}

setup() {
  kit=$(target)
  folders
  archive="agent-workbench-$kit.tar.gz"
  case "$kit" in macos-arm64 | linux-x64) ;; *) fail "There is no kit named $kit. Use macos-arm64 or linux-x64." ;; esac
  for tool in tar awk sed mktemp; do
    command -v "$tool" >/dev/null 2>&1 || fail "The installer needs the $tool command. Install it and run the installer again."
  done
  if [ -e "$app" ] && ! is_install "$app"; then
    fail "$app exists and is not an Agent Workbench install. Move it away, or set WORKBENCH_INSTALL_DIR to another folder, and run the installer again."
  fi
  if [ -e "$bin/workbench" ] && ! grep -q "Agent Workbench" "$bin/workbench" 2>/dev/null; then
    fail "$bin/workbench exists and was not written by this installer. Move it away, or set WORKBENCH_BIN_DIR to another folder, and run the installer again."
  fi
  parent=$(dirname "$app")
  if ! mkdir -p "$parent" || ! stage=$(mktemp -d "$parent/.agent-workbench-install.XXXXXX"); then
    fail "The installer could not create a folder in $parent. Make it writable, or set WORKBENCH_INSTALL_DIR to another folder, and run the installer again."
  fi
  trap 'rm -rf "$stage"' EXIT
  trap 'exit 1' INT TERM HUP
  if [ -n "$from" ]; then
    [ -f "$from/SHA256SUMS" ] && [ -f "$from/$archive" ] ||
      fail "$from must contain SHA256SUMS and $archive. Point --from at the folder that holds the release files."
    sums="$from/SHA256SUMS"
    pack="$from/$archive"
  else
    base="https://github.com/$REPOSITORY/releases/download/v$VERSION"
    say "Downloading Agent Workbench $VERSION for $kit from $base"
    sums="$stage/SHA256SUMS"
    pack="$stage/$archive"
    fetch "$base/SHA256SUMS" "$sums"
    fetch "$base/$archive" "$pack"
  fi
  expected=$(awk -v name="$archive" '$2 == name || $2 == "*" name { print $1 }' "$sums" | head -n 1)
  case "$expected" in
    "" | *[!0-9a-f]*) fail "SHA256SUMS has no usable entry for $archive, so nothing was installed. Download the release files again." ;;
  esac
  [ "${#expected}" -eq 64 ] ||
    fail "SHA256SUMS has no usable entry for $archive, so nothing was installed. Download the release files again."
  actual=$(sha256 "$pack")
  [ "$actual" = "$expected" ] ||
    fail "$archive does not match its published SHA-256, so nothing was installed. Download it again; if it fails twice, report it at https://github.com/$REPOSITORY/issues"
  say "SHA-256 verified: $expected"
  tar -xzf "$pack" -C "$stage" || fail "$archive could not be unpacked into $parent. Check free disk space and that the folder is writable, then run the installer again."
  fresh="$stage/Agent-Workbench"
  [ -f "$fresh/workbench.sh" ] && [ -f "$fresh/requirements.lock" ] && [ -x "$fresh/python/bin/python3.12" ] ||
    fail "$archive is not a complete Agent Workbench kit, so nothing was installed. Download the release files again."
  if [ "$(uname -s)" = Darwin ] && [ -x /usr/bin/xattr ]; then
    /usr/bin/xattr -drs com.apple.quarantine "$fresh" 2>/dev/null || true
  fi
  trap '' INT TERM HUP
  if [ -e "$app" ]; then
    mv "$app" "$stage/previous" ||
      fail "The new version could not be moved into $app, so the previous version was kept. Check that the folder is writable and run the installer again."
    if ! mv "$fresh" "$app"; then
      if ! mv "$stage/previous" "$app"; then
        trap - EXIT
        fail "The new version could not be installed and the previous one could not be put back. It is in $stage/previous; move it to $app yourself."
      fi
      fail "The new version could not be moved into $app, so the previous version was kept. Check that the folder is writable and run the installer again."
    fi
    if [ -d "$stage/previous/.local" ] && ! mv "$stage/previous/.local" "$app/.local"; then
      trap - EXIT
      fail "The new version is installed, but the data folder of the previous one could not be moved back. It is in $stage/previous/.local; move it to $app/.local yourself."
    fi
    say "Replaced the previous version in $app."
  else
    mv "$fresh" "$app" ||
      fail "The new version could not be moved into $app, so nothing was installed. Check that $parent is writable and run the installer again."
  fi
  trap 'exit 1' INT TERM HUP
  if [ ! -d "$data" ]; then
    (umask 077 && mkdir -p "$data") ||
      fail "The app is installed in $app, but the data folder $data could not be created. Make its parent folder writable and run the installer again."
  fi
  write_command ||
    fail "The app is installed in $app, but the workbench command could not be written to $bin. Make that folder writable, or set WORKBENCH_BIN_DIR to another folder, and run the installer again."
  say "Agent Workbench $VERSION is installed."
  say "  App:      $app"
  say "  Data:     $data"
  say "  Command:  $bin/workbench"
  path_step
  if [ "$kit" = linux-x64 ]; then
    say "Linux desktops need a few system libraries. To list them and install them with your consent, run: sh $(quote "$app/setup-linux.sh")"
  fi
  say "Start it with the workbench command. The first start downloads Python packages from pypi.org and the desktop runtime from github.com."
  remove="sh $(quote "$app/install.sh") --uninstall"
  if [ -n "${WORKBENCH_BIN_DIR:-}" ]; then remove="WORKBENCH_BIN_DIR=$(quote "$bin") $remove"; fi
  if [ -n "${WORKBENCH_INSTALL_DIR:-}" ]; then remove="WORKBENCH_INSTALL_DIR=$(quote "$app") $remove"; fi
  say "To uninstall: $remove"
}

main() {
  from="${WORKBENCH_INSTALL_FROM:-}"
  yes=no
  modify=yes
  if [ -n "${WORKBENCH_NO_MODIFY_PATH:-}" ]; then modify=no; fi
  action=install
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --from)
        [ "$#" -ge 2 ] && [ -n "$2" ] || fail "--from needs a folder. Example: sh install.sh --from ./dist"
        from=$2
        shift
        ;;
      --yes) yes=yes ;;
      --no-modify-path) modify=no ;;
      --uninstall) action=uninstall ;;
      --help | -h)
        usage
        exit 0
        ;;
      *) fail "Unknown option $1. Run: sh install.sh --help" ;;
    esac
    shift
  done
  case "$from" in "" | /*) ;; *) from="./$from" ;; esac
  [ -n "${HOME:-}" ] || fail "HOME is not set, so there is no per-user folder to install into. Set HOME and run the installer again."
  if [ "$action" = uninstall ]; then uninstall; else setup; fi
}

main "$@"
