---
name: workbench-git-workflow
description: "Commit, branch, push or open a pull request with Git. Use when asked to do one of these: read status and diff first, make small commits with clear messages, never rewrite shared history."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You are asked to commit, create a branch, push or open a pull request.
- You are about to run a Git command that changes history or discards files.

Commit, push and open pull requests only when the user or `AGENTS.md` asks for it. Finishing a task is not a request to commit.

## Steps

1. Check where you are:

   ```
   git rev-parse --is-inside-work-tree
   git status --short --branch
   ```

   - `fatal: not a git repository`: stop. Do not run `git init` unless asked.
   - The first line of the status is the branch, for example `## main...origin/main [ahead 1]`. `## HEAD (no branch)` means no branch is checked out: stop and ask.
   - Lines for files you did not change belong to the user. Leave them out of your commit and do not revert them.
2. Choose the branch. If `AGENTS.md` gives a branch pattern or the user asked for a branch, create it: `git switch -c <name>`. Otherwise stay on the current branch.
3. Read the whole change: `git --no-pager diff`. New files are not in that output; they are the `??` lines of the status, so read each of them too. Remove debug lines and edits that do not belong to the task.
4. Stage files by name: `git add <path> <path>`. Do not use `git add -A`, `git add .` or `git commit -a`; they take files you have not checked. Do not use `git add -p`; it waits for keyboard input.
   Never stage `.env` files, keys, credentials, logs, build output or dependency folders. If one shows up as `??`, leave it and mention it in your report.
5. Check what is staged: `git --no-pager diff --staged --stat`, then `git --no-pager diff --staged`. This is exactly what the commit will contain. To take a file out again without losing its content: `git restore --staged <path>`.
6. Keep one purpose per commit. If the staged diff holds two unrelated changes, unstage one and commit it separately.
7. Write the message. Use the format in `AGENTS.md`. If it gives none, copy the style of `git --no-pager log --oneline -10`. If the history is empty or has no clear style:
   - Subject: what the commit does, as a command, at most 72 characters, no full stop. Example: `Fix page count when the list is empty`.
   - Body, only when the reason is not obvious: why the change was needed.

   ```
   git commit -m "<subject>" -m "<body>"
   ```

   Leave out the second `-m` when there is no body.
8. Check the commit: `git --no-pager log -1 --stat` shows your subject and only your files, and `git status --short` no longer lists them.
9. Push only when asked: `git push -u origin <branch>` for the first push of a branch, `git push` after that. If the push is rejected, stop and report the message. Do not force it.
10. Open a pull request only when asked: `gh pr create --title "<title>" --body "<body>"`. Add `--base <branch>` when the target is not the default branch and `--draft` when the work is not ready for review. If `gh` reports that you are not logged in, stop and say so; `gh auth status` shows the state.

## When something goes wrong

- The commit prints hook errors and exits with an error: no commit was made and your files are still staged. Fix what the hook reports, `git add` the fixed files and run the same `git commit` again. Do not add `--no-verify`. Do not add `--amend`; that would rewrite the previous commit.
- You need to correct the commit you just made: run `git branch -r --contains HEAD`. If it prints nothing, the commit is on no remote branch, and `git commit --amend` is safe for a commit you made in this session. If it prints a branch, make a new commit instead.
- You staged or committed the wrong thing and are not sure how to undo it: stop and ask. Do not experiment with reset commands.

## Never without an explicit request

| Command | What it destroys |
| --- | --- |
| `git push --force`, `git push --force-with-lease` | Commits on the remote that other people may have |
| `git reset --hard` | All uncommitted changes |
| `git clean -f` | Untracked files, with no way back |
| `git restore <path>`, `git checkout -- <path>` | Uncommitted edits in that file |
| `git rebase`, `git commit --amend` on pushed commits | History that others have already pulled |
| `git stash drop`, `git stash clear`, `git branch -D` | Saved work |
| `git commit --no-verify` | The project's own checks |
| `git config` | The user's settings |

## Report

```
Branch: <name>
Commit: <short hash> <subject>
Files: <paths>
Left out: <changed files you did not commit, and why>
Pushed: <yes or no>
```

## Gotchas

- Write `git --no-pager` before `diff`, `log` and `show` so the output never waits for a key press.
- `git --no-pager diff --check` lists leftover conflict markers and trailing whitespace. Run it when a merge or rebase was involved.
- An empty `git status --short` means there is nothing to commit. Do not create an empty commit.
