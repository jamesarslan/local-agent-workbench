---
name: workbench-review-diff
description: "Review your own diff before calling the work done. Use after the checks pass and before a commit or final report: find wrong logic, missing tests, leftover debug code and secrets."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- After your last edit and after the tests pass, before you commit or report that the work is done.
- Again after any edit you make during the review.

This does not replace running the tests. It finds what tests do not.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Get the whole change:

   ```
   git status --short
   git --no-pager diff HEAD --stat
   git --no-pager diff HEAD
   ```

   - `git diff HEAD` holds staged and unstaged edits. It does not hold new files: list them with `git ls-files --others --exclude-standard` and read each one.
   - Work that is already committed: `git --no-pager diff <base>...HEAD`, where `<base>` is the branch you started from, for example `main`.
   - More than about 400 changed lines in the `--stat` total: review one file at a time with `git --no-pager diff HEAD -- <path>`.
   - No Git repository, or `fatal: ambiguous argument 'HEAD'` (a repository with no commit yet): read every file you edited instead.
2. Read the task again. Then read the diff file by file and answer these for every hunk:
   - Asked for? If the hunk is not needed for the task (reformatting, a rename, an extra fix), take it out.
   - Correct? Walk through it with a normal input, an empty or missing input, the largest input, and the error path. Look for an inverted condition, an off-by-one, the wrong variable.
   - Callers? If you changed a name, a parameter list, a return value or a file path, find every use with `git grep -n "<name>"` and check each one.
   - Errors? No empty error handler and no ignored failure.
   - Complete? No stub, no "not implemented", no commented-out old code.
3. Check the tests. For each behaviour you changed, name the test that would fail without your change. If there is none, add one, or list the behaviour as untested in the report. Then make sure no test was switched off:

   ```
   git --no-pager diff HEAD | grep -n -E '^\+.*(\.skip\(|\.only\(|@pytest\.mark\.skip|@unittest\.skip|#\[ignore\]|t\.Skip\()'
   ```
4. Look for leftovers:

   ```
   git --no-pager diff HEAD | grep -n -E '^\+.*(console\.log|debugger|breakpoint\(|pdb\.set_trace|print\(|dbg!|DEBUG-TMP|TODO|FIXME|XXX)'
   git --no-pager diff HEAD --check
   ```

   Judge each hit of the first command: remove a debug line, keep output the program is meant to produce. The second command lists conflict markers and trailing whitespace and prints nothing when the diff is clean.
5. Look for secrets:

   ```
   git --no-pager diff HEAD | grep -n -i -E '^\+.*(PRIVATE KEY|AKIA[0-9A-Z]+|ghp_[A-Za-z0-9]+|github_pat_|(password|passwd|secret|token|api[_-]?key) *[:=])'
   git status --short | grep -E '\.env|\.pem$|\.key$|id_rsa|id_ed25519'
   ```

   - A hit is a problem when the line holds a real value. A variable name, a placeholder or a read from the environment is fine.
   - Real value found: remove it from the file, read it from an environment variable instead, and tell the user.
   - Real value already committed or pushed: say so plainly. The key must be revoked and replaced; deleting the line does not remove it from history. Do not rewrite history yourself.
   - These patterns catch common forms only. Read new configuration files and test data yourself for passwords, keys and addresses of the form `user:password@host`.
6. Check the file list in the `--stat` output. These should not be there unless the task needs them: a lock file without a dependency change, build output, generated files, binary files (shown as `Bin`), editor or system files.
7. Fix what you found. If you edited code, run the project's checks again and start over at step 1. Stop when a full pass finds nothing.

## Report

```
Reviewed: <number> files
Fixed in review: <what, or "nothing">
Left as is: <file:line, finding, reason>
Untested: <behaviour without a test, or "nothing">
Secrets: <"none found", or the file where one was removed>
```

Never put a secret value in the report.

## Gotchas

- Review the lines in the diff, not your memory of what you meant to write.
- `grep` prints nothing and exits with 1 when there is no match. That is the clean result, not an error.
- Take a hunk out by editing the file back. `git restore <path>` and `git checkout -- <path>` throw away every uncommitted edit in that file.
- A file with only spacing changes: `git --no-pager diff HEAD -w --stat` no longer lists it. Undo such a change unless the task was formatting.
