#!/usr/bin/env sh
# Prepare a Linux desktop for Agent Workbench: system libraries, then the desktop sandbox. Asks before using sudo.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
proc="${WORKBENCH_PROC_ROOT:-/proc}"
yes=no
mode=all
while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes) yes=yes ;;
    --sandbox) mode=sandbox ;;
    *) break ;;
  esac
  shift
done
if [ "$(id -u)" = 0 ]; then prefix=""; else prefix=sudo; fi

confirm() {
  if [ "$yes" = yes ]; then return 0; fi
  if [ ! -t 0 ]; then return 1; fi
  printf '%s [y/N] ' "$1"
  read -r reply || reply=""
  case "$reply" in y | Y | yes | Yes | YES) return 0 ;; esac
  return 1
}

packages() {
  python="python3 python3-venv python3-tk "
  if [ -x "$here/python/bin/python3.12" ]; then python=""; fi
  if command -v apt-get >/dev/null 2>&1; then
    update="$prefix apt-get update"
    install="$prefix apt-get install -y ${python}libxcursor1 xterm git libgtk-3-0 libnss3 libasound2-dev libgbm1 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2"
  elif command -v dnf >/dev/null 2>&1; then
    update=""
    if [ -n "$python" ]; then python="python3 python3-pip python3-tkinter "; fi
    install="$prefix dnf install -y ${python}libXcursor xterm git gtk3 nss alsa-lib mesa-libgbm libxkbcommon libXcomposite libXdamage libXrandr"
  else
    echo "Install Python 3.12 to 3.14 with its venv and Tk modules, libXcursor, GTK 3, NSS, a terminal emulator and Git with your package manager."
    return 0
  fi
  echo "Agent Workbench needs these system packages for its desktop window and agent terminals:"
  if [ -n "$update" ]; then echo "  $update"; fi
  echo "  $install"
  if confirm "Run this now?"; then
    if [ -n "$update" ] && ! $update; then
      echo "The package lists could not be refreshed, so the install uses the lists this system already has."
    fi
    if ! $install; then
      echo "The system packages could not be installed, so Agent Workbench was not started. Fix the error shown above, then run: sh \"$here/setup-linux.sh\""
      exit 1
    fi
  else
    echo "Nothing was installed. Run the commands above yourself, then start Agent Workbench again."
  fi
}

setting() {
  if [ -r "$1" ]; then cat "$1"; else echo "$2"; fi
}

owned_by_root() {
  [ -n "$(find "$1" -prune -user 0 -perm -4000 2>/dev/null)" ]
}

sandbox() {
  restricted=$(setting "$proc/sys/kernel/apparmor_restrict_unprivileged_userns" 0)
  clone=$(setting "$proc/sys/kernel/unprivileged_userns_clone" 1)
  spaces=$(setting "$proc/sys/user/max_user_namespaces" 1)
  if [ "$restricted" != 1 ] && [ "$clone" != 0 ] && [ "$spaces" != 0 ]; then return 0; fi
  data="${WORKBENCH_DATA:-$here/.local}"
  case "$data" in /*) ;; *) data="$here/$data" ;; esac
  if [ -d "$data" ]; then data=$(CDPATH='' cd -- "$data" && pwd -P); fi
  version=$(sed -n 's/.*"electron": *"\([0-9][0-9.]*\)".*/\1/p' "$here/desktop/package.json" 2>/dev/null | head -n 1)
  helper=""
  for candidate in "$data"/desktop-runtime/linux-${version:-*}/chrome-sandbox; do
    if [ -f "$candidate" ]; then
      helper=$candidate
      if owned_by_root "$candidate"; then return 0; fi
    fi
  done
  profile=/etc/apparmor.d/agent-workbench-$(printf %s "$data" | cksum | cut -d ' ' -f 1)
  plain=yes
  case "$data" in *[!A-Za-z0-9_./@+-]*) plain=no ;; esac
  if [ "$restricted" = 1 ] && [ "$clone" != 0 ] && [ "$spaces" != 0 ] && [ "$plain" = yes ] && command -v apparmor_parser >/dev/null 2>&1; then
    if [ -f "$profile" ] && grep -qF "$data/desktop-runtime/" "$profile"; then return 0; fi
    echo "This system limits user namespaces, which the Agent Workbench desktop window uses for its sandbox."
    temp=$(mktemp)
    cat >"$temp" <<PROFILE
# Written by Agent Workbench (setup-linux.sh). Lets the desktop runtime in
# $data/desktop-runtime/linux-${version:-*} (and later versions beside it)
# create the user namespaces its sandbox needs.
# Remove with: sudo apparmor_parser -R $profile && sudo rm $profile
abi <abi/4.0>,
include <tunables/global>

profile $(basename "$profile") $data/desktop-runtime/linux-*/electron flags=(unconfined) {
  userns,
  include if exists <local/$(basename "$profile")>
}
PROFILE
    echo "The fix is a small AppArmor profile that applies only to the desktop runtime in $data/desktop-runtime:"
    sed 's/^/    /' "$temp"
    echo "It would be installed with:"
    echo "  $prefix install -m 644 $temp $profile"
    echo "  $prefix apparmor_parser -r $profile"
    if confirm "Install this profile now?"; then
      if $prefix install -m 644 "$temp" "$profile"; then
        rm -f "$temp"
        if $prefix apparmor_parser -r "$profile"; then
          echo "The profile is installed."
          return 0
        fi
        $prefix rm -f "$profile" || true
      fi
      rm -f "$temp"
      echo "The profile could not be installed, so the desktop window still cannot open. Fix the error shown above, then run: sh \"$here/setup-linux.sh\" --sandbox"
      return 1
    fi
    rm -f "$temp"
  elif [ -n "$helper" ]; then
    echo "This system limits user namespaces, which the Agent Workbench desktop window uses for its sandbox."
    echo "The fix is to make the runtime's sandbox helper owned by root with the setuid bit:"
    echo "  $prefix chown root:root \"$helper\""
    echo "  $prefix chmod 4755 \"$helper\""
    if confirm "Run these two commands now?"; then
      if $prefix chown root:root "$helper" && $prefix chmod 4755 "$helper"; then
        echo "The sandbox helper is set up."
        return 0
      fi
      echo "The sandbox helper could not be changed, so the desktop window still cannot open. Fix the error shown above, then run: sh \"$here/setup-linux.sh\" --sandbox"
      return 1
    fi
  else
    echo "This system limits user namespaces, which the Agent Workbench desktop window uses for its sandbox."
    echo "The desktop runtime has not been downloaded yet. Start Agent Workbench once, then run: sh \"$here/setup-linux.sh\" --sandbox"
    return 1
  fi
  echo "Nothing was changed. Until this is set up the desktop window cannot open and the classic interface opens instead. To set it up later run: sh \"$here/setup-linux.sh\" --sandbox"
  return 1
}

if [ "$mode" = sandbox ]; then
  sandbox
  exit $?
fi
packages
sandbox || true
WORKBENCH_SANDBOX_CHECKED=1
export WORKBENCH_SANDBOX_CHECKED
exec sh "$here/workbench.sh" "$@"
