# {{project}}

TODO: one line that says what this project is and names the main language and framework with versions.

## Commands

A line that still says `TODO` has no command yet. Find it in the README or the build files; do not guess.

- Install: `{{cmd:install}}`
- Run: `{{cmd:dev}}`
- Test everything: `{{cmd:test}}`
- Test one file: `TODO: add the command`
- Lint and format: `{{cmd:lint}}`
- Build or typecheck: `{{cmd:build}}`

## Verify before finishing

- Run the tests that cover the files you changed and read the output.
- Run lint and the build when you touched code they check.
- Say which checks you ran, which passed, and which you could not run.
- Never delete, skip or weaken a test to make a check pass.

## Conventions

- Match the file you are editing: naming, imports, error handling, comment style.
- TODO: list only the choices that differ from the tool defaults.

## Git workflow

- Commit only when asked. Stage files by name and read `git diff --staged` first.
- Branch names: TODO: add the pattern. Until then, stay on the current branch.
- Commit messages: TODO: add the format. Until then, copy the style of `git log --oneline -10`.
- Before a pull request: tests and lint pass.

## Boundaries

Always:
- Read a file before you edit it.
- Keep changes inside this folder.
- Make the smallest change that does the task.

Ask first:
- Adding or upgrading a dependency.
- Deleting files or changing a public interface.
- Changing CI, build or deployment configuration.

Never:
- Commit secrets, keys or tokens.
- Push, force-push or rewrite history unless asked.
- Edit generated files or lock files by hand.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
