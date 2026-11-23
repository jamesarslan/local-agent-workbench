---
name: workbench-verify-change
description: "Verify a code change before reporting done. Use after any edit to run the project's test, lint and build commands, read the real output and report what passed, failed or was not run."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- After every edit to code, configuration or build files, before you say the work is done.
- Before a commit or a pull request.
- Again after any further edit. A check counts only if it ran after your last edit.

## Steps

1. Find the project's commands. Do not guess. Use the first source that has them:
   1. `AGENTS.md`, section `Commands`. A line that still says `TODO` is not a command.
   2. `README.md` or `CONTRIBUTING.md`.
   3. The task runner. `npm run` lists the scripts in `package.json`. `grep -n -E '^[A-Za-z0-9_.-]+:' Makefile` lists the make targets.
   4. The CI file. `grep -n "run:" .github/workflows/*.yml` shows the commands CI runs.
   5. Only if all four give nothing, the default for the file that exists:

   | File in the project | Tests | Static check | Build |
   | --- | --- | --- | --- |
   | `package.json` | `npm test` | `npm run lint` | `npm run build` |
   | `pyproject.toml`, `requirements.txt` | `python3 -m pytest`, or `python3 -m unittest discover` when the project does not use pytest | none | `python3 -m py_compile <changed file>` |
   | `Cargo.toml` | `cargo test` | `cargo clippy` | `cargo check` |
   | `go.mod` | `go test ./...` | `go vet ./...` | `go build ./...` |
   | `*.csproj`, `*.sln` | `dotnet test` | none | `dotnet build` |
   | `Package.swift` | `swift test` | none | `swift build` |

   For `package.json`, run only scripts that `npm run` lists. Use `pnpm`, `yarn` or `bun` in place of `npm` when `pnpm-lock.yaml`, `yarn.lock`, or `bun.lock` or `bun.lockb` exists.
2. List what you changed: `git status --short`. If the folder is not a Git repository, list the files you edited. Pick the tests that cover those files.
3. Run the checks one at a time, in this order: the narrowest test for your change, the full test command, lint, then typecheck or build. Run each one in this form, which keeps the exit code and shows only the end of the output:

   ```
   <command> > /tmp/check.log 2>&1; echo "exit=$?"; tail -n 40 /tmp/check.log
   ```

   In PowerShell: `<command> *> $env:TEMP\check.log; "exit=$LASTEXITCODE"; Get-Content $env:TEMP\check.log -Tail 40`
4. Read the result. A check passed only when all of these are true:
   - The line `exit=0` was printed.
   - The output has a summary that shows work was done: a number of tests above zero, or no errors reported.
   - The summary includes your change. If you added a test, its name appears or the count went up.

   "0 tests", "no tests ran" or "no test files found" means not run, whatever the exit code is.
5. When a check fails:
   - Find the first error, not the last: `grep -n -i -E "error|fail" /tmp/check.log | head -n 20`.
   - If your change caused it, fix the code and run the same command again. When it passes, run the earlier checks again.
   - If it is in code you did not touch and your change cannot affect it, leave it alone. Report it as failed, with its first error line, and say why your change is not the cause.
   - If the same check still fails after two different fixes, stop and report what you tried.
6. When a command cannot run (tool not installed, needs a network, a database or credentials), do not replace it with a guess and do not install anything without asking. Report it as not run, with the reason.

Never make a check pass by deleting or skipping a test, loosening an assertion, adding an ignore comment, lowering a lint rule, or appending `|| true`. If a test asserts behaviour the task told you to change, update the test and say so in the report.

## Report

Use this form, one line for each command you ran or could not run:

```
Checks
- <command>: passed (<summary line from the output>)
- <command>: failed (<first error line>)
- <command>: not run (<reason>)
Not verified: <what no check covers, or "nothing">
```

Write "passed" only for a command whose output you read in this session after your last edit. Never write "should work".

## Gotchas

- A pipe hides the exit code: `<command> | tail` reports the exit code of `tail`, which is 0. Use the log file form from step 3.
- A command that never returns (watch mode, a development server) is not a check. Stop it and use the run-once form the tool's `--help` shows, for example `vitest run`.
- Run each command from the folder the instructions name, normally the one that holds the manifest file. In another folder a test runner may find no tests.
- A formatter, or a linter run with a fix option, edits files. Run `git status --short` afterwards, and run the tests again if anything changed.
- When no command can be found at all, say so in the report and ask for the commands to be added to `AGENTS.md`.
