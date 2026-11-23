# Installing and releasing Agent Workbench

## Install with one command

These commands fetch release v0.4.0, so they work once that release is published.

macOS on Apple Silicon and Linux on x86-64:

```sh
curl -fsSL https://github.com/jamesarslan/local-agent-workbench/releases/download/v0.4.0/install.sh | sh
```

Windows 11 x64, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/jamesarslan/local-agent-workbench/releases/download/v0.4.0/install.ps1 | iex"
```

To read the script before you run it, download it, open it, then run `sh install.sh` or `powershell -ExecutionPolicy Bypass -File install.ps1`.

The installer downloads the kit for your system and `SHA256SUMS` from the same release, stops unless the kit matches its SHA-256, unpacks it into your user account and adds a `workbench` command. It never asks for administrator rights or `sudo`. The checksum protects against a damaged or swapped archive. It cannot protect against a compromised release, because the script and the checksum file come from the same place; the build attestation below is the stronger check.

| | App | Your data | Command |
| --- | --- | --- | --- |
| macOS | `~/.local/lib/agent-workbench` | `~/Library/Application Support/Agent Workbench` | `~/.local/bin/workbench` |
| Linux | `~/.local/lib/agent-workbench` | `${XDG_DATA_HOME:-~/.local/share}/agent-workbench` | `~/.local/bin/workbench` |
| Windows | `%LOCALAPPDATA%\Agent Workbench\app` | `%LOCALAPPDATA%\Agent Workbench\data` | `%LOCALAPPDATA%\Agent Workbench\bin\workbench.cmd`, plus a Start menu entry |

`workbench` opens the desktop. `workbench doctor` checks this computer, and `workbench agent --workspace .` starts the saved agent in the current folder. Installing a newer version replaces the app folder and leaves your data folder alone. The first start downloads Python packages from pypi.org and the desktop runtime from github.com.

Set `WORKBENCH_INSTALL_DIR` (and on macOS and Linux `WORKBENCH_BIN_DIR`) to an absolute path to install somewhere else. To install from release files you already downloaded, run `sh install.sh --from <folder>` or `powershell -ExecutionPolicy Bypass -File install.ps1 -From <folder>`; the folder must hold `SHA256SUMS` and the kit. Both installers accept checksum lines with two spaces or with ` *` before the file name.

If `~/.local/bin` is not on your `PATH`, the macOS and Linux installer asks before it appends one line to your shell profile, and prints that line if you decline. On Windows the installer adds the command folder to your user `PATH`; set `WORKBENCH_NO_MODIFY_PATH=1` to prevent that.

On Linux, run `sh ~/.local/lib/agent-workbench/setup-linux.sh` once. It lists the system packages the desktop window needs and asks before it uses `sudo`. On systems that restrict user namespaces (Ubuntu 24.04 and later), it also shows the small AppArmor profile, or the two commands for the sandbox helper, that the desktop window needs, and asks before it installs either. Agent Workbench never turns the browser sandbox off.

On Windows, an update first moves the previous version into `%LOCALAPPDATA%\Agent Workbench\.install\previous`. If an earlier install stopped before it could put that folder back, the installer refuses to continue until you move `.install\previous` somewhere safe; it may hold your data.

### Uninstall

- macOS and Linux: `sh ~/.local/lib/agent-workbench/install.sh --uninstall`
- Windows: `powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Agent Workbench\app\install.ps1" -Uninstall`

Both remove the app and the command and keep your data folder; the last lines they print tell you how to delete that too. If you installed with `WORKBENCH_INSTALL_DIR` or `WORKBENCH_BIN_DIR` set, set them to the same folders for the uninstall; the installer prints the exact line when it finishes. On Windows the app folder is renamed to `.uninstall` first and then deleted, so a window that still holds a file open stops the uninstall before anything is removed. Saved API keys stay in the system credential vault under the name Agent Workbench until you delete them there. If the Linux setup installed an AppArmor profile, remove it with the command written at the top of that file in `/etc/apparmor.d/`.

## Device kits without the installer

Each kit is the same folder the installer unpacks. It keeps its data in a `.local` folder inside itself unless you set `WORKBENCH_DATA`.

| Kit | After extracting the whole archive |
| --- | --- |
| Windows x64 | Before extracting, open the zip's Properties, tick **Unblock** and choose Apply. Then double-click **Launch Workbench.cmd** in a folder you can write to. |
| Linux x64 | `sh setup-linux.sh`, which asks before each `sudo` step and then starts the app. Afterwards use `sh workbench.sh`. |
| macOS arm64 | In Terminal: `sh "Launch Workbench.command"`. |

The kits carry no code signature and are not notarized by Apple. A browser download therefore gets the macOS quarantine mark, and macOS refuses to start the bundled Python while that mark is present. `Launch Workbench.command` removes the mark from the Agent Workbench folder, and from nothing else, and says so when it does. Verify the archive's checksum first. A kit installed with `install.sh` never has the mark, because `curl` does not set it, and `install.sh --from` removes it from the unpacked folder after the checksum has passed. On Windows with Smart App Control switched on, downloaded `.cmd` files are blocked unless the zip was unblocked before extracting, and the unsigned bundled `python.exe` may be blocked however it arrives; in that case use the source archive with Python from python.org.

Each kit includes CPython 3.12.16 and its third-party notices, and a `VERSION` file whose first line is the version and whose second line is the commit it was built from. The source archive needs your own Python 3.12, 3.13 or 3.14. The launchers rebuild their Python environment by themselves if the folder was moved or the first start was interrupted.

## Check what you downloaded

```sh
sha256sum -c SHA256SUMS --ignore-missing        # Linux
shasum -a 256 -c SHA256SUMS --ignore-missing    # macOS
```

```powershell
(Get-FileHash -Algorithm SHA256 agent-workbench-windows-x64.zip).Hash.ToLower()   # compare with SHA256SUMS
```

Releases are built by the `Release kits` workflow in this repository, which records a build attestation for every file named in `SHA256SUMS`. With the GitHub CLI:

```sh
gh attestation verify agent-workbench-linux-x64.tar.gz --repo jamesarslan/local-agent-workbench
```

An attestation shows which workflow run and commit produced a file. It does not show that the file is safe.

## Python dependencies

`requirements.txt` lists what Agent Workbench imports. `requirements.lock` pins those packages and everything they depend on, with SHA-256 hashes, for CPython 3.12 to 3.14 on Windows, Linux and macOS. The launchers and CI install only from the lock, with `pip install --require-hashes --only-binary :all:`. After changing `requirements.txt`, regenerate the lock from the repository root with uv 0.13.9 or newer and commit both files:

```sh
uv pip compile requirements.txt --universal --generate-hashes --python-version 3.12 -o requirements.lock
```

Add `--upgrade` to move the indirect dependencies to their newest versions as well.

## The release file list

`release/public-files.txt` names every file a release contains, one per line, sorted by code point. It is generated from the checkout, never edited by hand:

```sh
python release/build.py --write-manifest
```

This lists every file Git tracks or would track (untracked files that `.gitignore` does not exclude), leaves out the entries of `EXCLUDED` in `release/build.py` (`private/` and the promotional drafts in `docs/launch-posts.md`), prints each added and removed line, and exits with an error when a listed page, script or Python module refers to a file that is not listed. Read every added line before you commit the file. A file that must stay out of releases goes into `EXCLUDED`, never silently missing from the list. `python -m unittest tests.test_release` fails when the list and the checkout disagree, when a listed file is missing, or when a listed module imports a file that is not listed.

## Build the release files

```sh
python release/build.py --output dist
```

The builder needs Python 3.11 or newer and Git. It packs exactly the files named in `release/public-files.txt` from the commit you name with `--ref` (default `HEAD`), adds a `VERSION` file, downloads the pinned CPython archives and checks their SHA-256 digests, and writes `agent-workbench-source.tar.gz`, one kit per system, `install.sh`, `install.ps1` and `SHA256SUMS`. Every archive member carries the commit's date, no owner and mode 644 or 755 whatever your Git configuration says, so two builds of the same commit with the same Python and zlib give the same bytes; the release workflow builds twice and compares.

- `--target linux-x64` (repeatable) builds only the named kits.
- `--worktree` packs the listed files as they are on disk instead of from a commit, without writing to the repository's index or object store. Use it to try a change before committing; never publish its output.
- `--cache <folder>` keeps the downloaded CPython archives (default `.local/release-cache`). A cached archive is checked on every build and deleted if it does not match its pinned digest. If the download stops with a certificate error, as it does with a python.org Python on macOS until its `Install Certificates.command` has run, run that command or download the three archives named in `release/build.py` into the cache folder yourself; the builder still checks them.

To try the installer against local files:

```sh
python release/build.py --worktree --target macos-arm64 --output /tmp/awb-dist
HOME=/tmp/awb-home sh /tmp/awb-dist/install.sh --from /tmp/awb-dist
```

On Windows: `powershell -ExecutionPolicy Bypass -File dist\install.ps1 -From dist`, with `WORKBENCH_INSTALL_DIR` set to a scratch folder.

## Publish a release

1. Set the version in `release/build.py`, `install.sh`, `install.ps1` and `desktop/package.json`; the tests fail if they differ.
2. Push a tag named `v` plus that version on a commit that is already on `main`. The `Release kits` workflow stops unless the tag matches the version and the commit is on `main`, runs the whole `Workbench tests` workflow on that commit, then builds the files twice, compares the two results, attests `SHA256SUMS` and creates a draft release.
3. Download the draft's files, run the checks above, replace the placeholder notes, then publish the draft by hand.

Turn on release immutability in the repository settings before the first release. Import `.github/main-ruleset.json` only after the three `test (...)` checks have each reported once on a pull request; importing it earlier blocks every merge.

[CPython distributions and licenses](https://github.com/astral-sh/python-build-standalone/releases/tag/20261110) · [Security and data handling](../SECURITY.md)
