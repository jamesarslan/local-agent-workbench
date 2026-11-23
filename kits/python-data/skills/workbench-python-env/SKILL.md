---
name: workbench-python-env
description: "Python environment for a project. Use before running Python, tests or tools, on ModuleNotFoundError, or to add a package: find the project's tool, run inside it, never install globally."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Before the first Python command in a project: a script, a test run, a linter, a notebook run.
- When a command fails with `ModuleNotFoundError`, `No module named pip` or `externally-managed-environment`.
- Before you add, upgrade or remove a package.

If `AGENTS.md` names the environment tool or gives a command, that wins over the tables below.

## Steps

1. Find the environment tool. Run `ls -a` in the project folder and take the first row that matches:

   | File you see | Tool | Create or refresh the environment | Run a command inside it |
   | --- | --- | --- | --- |
   | `uv.lock` | uv | `uv sync --locked` | `uv run <command>` |
   | `poetry.lock` | Poetry | `poetry install` | `poetry run <command>` |
   | `Pipfile` | pipenv | `pipenv sync --dev` | `pipenv run <command>` |
   | `environment.yml` | conda | `conda env create -f environment.yml` | `conda run -n <name> <command>` |
   | `requirements.txt`, or `pyproject.toml` with none of the above | venv and pip | step 2 | `.venv/bin/python <arguments>` |

   - On Windows the venv interpreter is `.venv\Scripts\python.exe`, wherever this skill writes `.venv/bin/python`.
   - conda: `<name>` is the `name:` line of `environment.yml`. Run `conda env list` first. If the name is listed, the environment exists; do not create it again.
   - Nothing matches, or the tool is not installed (`command not found`): stop and ask how the environment is built. Do not install a tool yourself.

2. venv and pip only. Test for the environment: `.venv/bin/python --version` (Windows: `.venv\Scripts\python.exe --version`).
   - It prints a version: go to step 3.
   - No such file: ask before you create one. Then run `python3 -m venv .venv` and install:
     - with `requirements.txt`: `.venv/bin/python -m pip install -r requirements.txt`
     - with only `pyproject.toml`: run `grep -n "build-system" pyproject.toml`. If it prints a line, run `.venv/bin/python -m pip install -e .`. If it prints nothing, do not guess; ask how the dependencies are installed.
   - `.venv/bin/python -m pip` answers `No module named pip`: the environment was made by uv. Install with `uv pip install -r requirements.txt`, run from the project folder; it installs into `.venv`.

3. Prove you are inside the environment. Put the "run a command" form from step 1 in front of this:

   ```
   python -c "import sys; print(sys.prefix != sys.base_prefix); print(sys.executable)"
   ```

   For example `uv run python -c "..."`, or `.venv/bin/python -c "..."`.
   - Pass: the first line is `True`.
   - conda: the first line is `False`, because a conda environment is not a venv. Pass when the path on the second line contains the environment name.
   - Fail: `False` with a path such as `/usr/bin/python3`. You are on the system Python. Go back to step 1.

4. Run everything through the environment. Take the command from `AGENTS.md` and put the run form in front:

   ```
   uv run python scripts/report.py
   uv run pytest tests/test_load.py
   .venv/bin/python -m pytest tests/test_load.py
   ```

   Do not use `source .venv/bin/activate`. Each command you run may start a new shell, and the activation is gone in the next one.

5. Add a package. Ask first; `AGENTS.md` lists this under "Ask first". Then use the row for your tool:

   | Tool | Package the code needs | Package only for tests or tooling |
   | --- | --- | --- |
   | uv | `uv add <package>` | `uv add --dev <package>` |
   | Poetry | `poetry add <package>` | `poetry add --group dev <package>` |
   | pipenv | `pipenv install <package>` | `pipenv install --dev <package>` |
   | conda | add it under `dependencies:` in `environment.yml`, then `conda env update -f environment.yml` | the same |
   | venv and pip | add a line to `requirements.txt`, pinned the way the other lines are, then `.venv/bin/python -m pip install -r requirements.txt` | the same, in the project's development requirements file if it has one |

   Check both:
   - In a Git repository, `git status --short` shows the manifest changed, and the lock file when the tool has one. Nothing else changed.
   - The import works inside the environment: `<run form> python -c "import <module>"` prints nothing and exits with 0.

6. uv only: to run a tool once without adding it to the project, use `uv run --with <package> <command>`. It changes neither `pyproject.toml` nor `uv.lock`. It downloads the package, so ask first.

## When a check fails

- `ModuleNotFoundError: No module named 'x'`. Work through these in order:
  1. Run step 3. If you were outside the environment, run the command again inside it.
  2. Run the refresh command from step 1, then try again.
  3. Still missing: the package is not declared. Go to step 5.

  The package name can differ from the import name: `sklearn` is `scikit-learn`, `cv2` is `opencv-python`, `PIL` is `pillow`, `yaml` is `pyyaml`, `bs4` is `beautifulsoup4`, `dotenv` is `python-dotenv`.
- `error: externally-managed-environment`: you ran pip with the system Python. Do not add `--break-system-packages`. Use the environment from step 1.
- `uv sync --locked` says the lockfile `needs to be updated`: `pyproject.toml` and `uv.lock` disagree. Report it. Run `uv lock` only when asked, because it rewrites the lock file.
- `pipenv sync` says `Pipfile.lock not found`: ask before you run `pipenv lock`, which creates it.
- `poetry install` ends with `The current project could not be installed`: the project is not a package. Run `poetry install --no-root`, which installs the dependencies only.
- The tool says the Python version does not fit: report the version the project asks for (`grep -n "requires-python" pyproject.toml`, or the file `.python-version`) and the one you have (`python3 --version`). Do not edit the requirement.
- After two failed attempts at the same problem, stop and report the exact error line.

## Never

- `pip install` outside the environment, `sudo pip`, `pip install --user`, `--break-system-packages`, `uv pip install --system`.
- Delete or rebuild `.venv` without asking.
- Edit a lock file by hand.

## Report

```
Environment: <tool>, Python <version>, <path printed in step 3>
Ran: <commands, each with its run form>
Dependencies: <added or changed packages and the files that changed, or "none">
Not done: <what failed or needs approval, with the error line>
```

## Gotchas

- `uv run` and `uv sync` create `.venv` and `uv.lock` when they are missing, and rewrite `uv.lock` when it is out of date. In a project that has no `uv.lock`, do not use them without asking: they add a file. With `--locked` they fail instead of writing `uv.lock`.
- An environment made by `uv venv` has no pip. `grep -n "^uv" .venv/pyvenv.cfg` prints a line for such an environment.
- Outside an environment, macOS and Linux often have `python3` but no `python`. Inside a venv both names work.
- `pip install -e .` fails with `Multiple top-level packages discovered in a flat-layout` when the project is a folder of scripts and data, not a package. Do not restructure the project to fix it; ask.
