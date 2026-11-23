# {{project}}

TODO: one line that says what this project is, with the Python version and the main libraries.

## Commands

A line that still says `TODO` has no command yet. Find it in the README or the project files; do not guess.

- Create or refresh the environment: `TODO: add the command`
- Test everything: `TODO: add the command`
- Test one file: `TODO: add the command`
- Lint and format: `TODO: add the command`

## Verify before finishing

- Run the tests that cover the files you changed, inside the project environment, and read the output.
- After a data change, compare the row count and the key column of the result with the input.
- Say which checks you ran, which passed and which you could not run, with the row counts you compared.

## Conventions

- Match the file you are editing: naming, imports, type hints, docstring style. TODO: add the choices that differ from the tool defaults.

## Environment

- Environment tool: TODO (uv, venv and pip, Poetry, pipenv or conda). Run every Python command through it, never the system Python.
- Add a package through the project's manifest and lock file, never with a bare `pip install` or `sudo`.

## Data

- Source data is in: TODO: add the folder. It is read-only. Write results to a new file in another folder.
- Never commit: TODO: add the paths (raw data, exports, personal data) and the size limit in MB.
- Check a file's size and read a sample before you load or print it. Never print a whole data file.

## Notebooks

- Running notebooks: TODO: allowed, or ask first. While this says TODO, ask first.
- Outputs in committed notebooks: TODO: kept or cleared. While this says TODO, leave outputs as they are.
- A notebook runs from the first cell to the last in a fresh kernel. Code used twice goes in a module.

## Reproducibility

- Results (tables, figures, models) go to: TODO: add the folder. Give the path of every file you write.
- Set a fixed seed for anything random and name it in the result. The same input gives the same output.

## Boundaries

Always:
- Read a file before you edit it, and keep changes inside this folder.

Ask first:
- Adding or upgrading a dependency, deleting files, downloading data, or starting a job that runs for more than a few minutes.

Never:
- Change, overwrite, move or delete source data.
- Commit secrets, keys, tokens or data files.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
