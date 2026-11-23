# Contributing

This page covers setting up Agent Workbench from source, running the tests, the rules code and documents follow, and the release checklist.

## Before you start

- Send one user-facing change or one reproducible fix per pull request, against `main`.
- Open an issue before you write code that adds a dependency, a network request or a platform-specific branch, so the approach can be agreed first.
- Security problems go through [SECURITY.md](SECURITY.md#reporting-a-vulnerability), not through issues.
- Use a sample project when you share screenshots, traces or Doctor output. Remove user names, host names and keys.

## Set up from source

A source checkout needs Python 3.12, 3.13 or 3.14; the launchers refuse other versions with a sentence that says so. Git is needed for the Strata engine source, and Node with npm for installing Pi, Codex or opencode from the app and for kit servers started with `npx`.

| System | Start the app from the repository folder |
| --- | --- |
| macOS on Apple Silicon | `sh "Launch Workbench.command"` |
| Linux x64 | `sh setup-linux.sh` once (it lists the system packages it needs and asks before it uses sudo), then `sh workbench.sh` |
| Windows x64 | Double-click `Launch Workbench.cmd`, or run `Run.cmd desktop_app.py` |

The launchers create a Python environment beside the scripts (`.venv-linux` on macOS and Linux, `.venv` on Windows) and install the packages in `requirements.lock` with their SHA-256 hashes. If the environment is incomplete, was moved, or was made by another Python, the launcher rebuilds it. To run a command-line verb from a checkout, use `sh workbench.sh cli.py <verb>` or `Run.cmd cli.py <verb>`.

## Tests

Run the Python unit tests from the repository root:

```sh
python -m unittest discover -s tests
```

Run one module with `python -m unittest tests.test_<name>`, for example `python -m unittest tests.test_docs`. Unit tests run offline and need no GPU, credential vault or writes to your home folder; they use temporary folders and `WORKBENCH_DATA`. Keep each module under two seconds.

The renderer and bridge-client tests need Node 22 or newer:

```sh
node --test tests/bridge-client.test.cjs tests/ui-static.test.cjs tests/ui-readiness.test.cjs tests/ui-format.test.cjs tests/ui-store.test.cjs tests/ui-palette.test.cjs
```

The desktop smoke test starts the real Electron app. Install Electron with `npm ci --prefix desktop` followed by `node desktop/node_modules/electron/install.js` (newer npm releases skip Electron's own download step), point `WORKBENCH_PYTHON` at the project's Python and run `node tests/desktop-smoke.cjs`. On a Linux machine without a display, prefix it with `xvfb-run -a`.

`.github/workflows/test.yml` defines these jobs: `test` on `windows-latest`, `ubuntu-latest` and `macos-latest` with Python 3.12 (unit tests, Node tests and the desktop smoke test); `lock` on the same three systems with Python 3.13 and 3.14; `kit`, which builds the kit for each system and installs it with `install.sh` or `install.ps1`; and `lint` for the shell scripts, the workflow files and the PowerShell scripts. These workflows have not run on GitHub for 0.4.0, so run the commands above on your own machine before you open a pull request.

## Rules for code

1. No telemetry, analytics, update check or account. Every new host the code contacts goes into the outbound-hosts table in `SECURITY.md`, with when and why.
2. Keys live in the system credential vault or in a child process's environment. Never on a command line, in a generated project file or in a log.
3. Anything read from a project folder is untrusted. Check its type and value, and never let it reach a shell. Use argument lists.
4. Never overwrite or delete a user's file without a plan the user confirmed. Existing content wins.
5. Every platform-specific branch is deliberate and covered by a unit test that runs on any system.
6. Python standard library plus what `requirements.txt` already pins. The renderer stays plain JavaScript and CSS, with no framework, bundler or CDN, under the existing Content-Security-Policy.
7. Network probes run in the Python backend. Discovery never scans a network: it probes this computer, an address the user typed, or a paired SSH machine's own loopback.
8. Every bridge action is namespaced and listed in `desktop/actions.json`. Every error a user sees is one plain sentence that says what to do next.
9. Match the surrounding code: small functions, a docstring line at the top of each module, no comments that narrate a change.

## Documentation claims

Documents make three kinds of statement, and each has its own source:

| Kind | Example | Allowed when |
| --- | --- | --- |
| Behaviour: what the software does | "Workbench stores hosted keys in the system credential vault." | The code that does it ships in this release and a test or a recorded run covers it |
| Evidence: what was run, on what, with what result | "OMP wrote a file against the local model on macOS 27.2." | `docs/verification.json` has a row for it, or a file under `results/` measures it |
| External: a statement about another project or an operating system | "`ollama launch` starts Claude Code against an Ollama model." | You opened the primary page on the day of writing; link it beside the statement |

In short:

1. Behaviour statements never use the words tested, verified, validated, confirmed, works on or supported on. Those belong to evidence, which appears in the README's tested-configurations table, `CHANGELOG.md` and `MEASUREMENTS.md`.
2. Speed, quality and cost numbers of model runs appear in `MEASUREMENTS.md` and nowhere else. Other pages link to it.
3. No superlatives, and no claim that the project is first or unique. Say what it does, and name the better tool for neighbouring jobs.
4. Steps that were not run on a system for this release carry the sentence `These steps were not run for 0.4.0.` above them.
5. macOS is called a preview until every macOS check listed in `tests/test_docs.py` (`MAC_GATE`) has passed.
6. Interface labels are written in bold and must exist in the interface source.
7. Public addresses of this project come from `docs/site.json`. Screenshots are the files under `docs/images/` named in `tests/test_docs.py`.

To record a run, add one row to `results` in `docs/verification.json`: the system id, a check id from `CHECKS` in `tests/test_docs.py`, `passed`, `failed` or `partial`, the date, one line of evidence (the command or action and what happened, at most 200 characters, no personal paths or keys) and a note. Add a system first if it is new, labelled with its OS version and the hardware that matters. Then run `python -m unittest tests.test_docs`; it must pass.

## Reports from your hardware

Speed and quality reports are welcome as files under `results/`, named `results/<machine>-<slug>.json` with no user or host names. A useful report gives the quantisation, engine version, context, prompt and output lengths, reasoning settings, concurrency, and prefill and decode separately.

## Changes made with a coding agent

Say in the pull request if a coding agent wrote part of the change. You must have read, understood and run every line you submit. Do not add lines that credit a tool to commits or files.

## Licence of contributions

Keep James Arslan's copyright notice and the MIT permission notice with redistributed copies or substantial portions of Agent Workbench, and keep the separate notices of third-party code. You keep the copyright in your contributions and submit them under the repository's [MIT licence](LICENSE).

## Releasing (maintainer)

Publication is a manual step. Nothing in this list runs automatically.

1. Build the public tree from `release/public-files.txt` into a fresh history. Confirm it contains no `private/` path and no `docs/launch-posts.md`.
2. Set `repo` and `site` in `docs/site.json` to the final repository and run `python -m unittest tests.test_docs`. Change the name everywhere the failures list.
3. Recompute the status sentences and the tested-configurations table from `docs/verification.json`.
4. In the public repository: turn on private vulnerability reporting, turn on Pages from `main` and `/docs`, turn on release immutability, and import `.github/main-ruleset.json` after the three `test (...)` checks have each reported once on a pull request.
5. Push the tag `v0.4.0`. The `Release kits` workflow builds the four archives, `SHA256SUMS`, `install.sh` and `install.ps1` into a draft release. Check the files, paste the `SHA256SUMS` content and the `CHANGELOG.md` entry into the release notes, and publish the draft.
6. Open the Pages address and every link on it once. Re-open the pages of the other projects the landing page names and check that what it says about them still holds.
7. Set the release date in `CHANGELOG.md`.
