---
name: workbench-ci-failure-triage
description: "Find out why a CI run failed. Use when a pipeline, workflow or check is red: read the log from the first error, reproduce it locally, tell flaky from real, fix the cause."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- A CI run, pipeline, workflow or pull request check failed and you are asked why, or asked to fix it.
- A check passes on your computer and fails in CI.

The commands use `gh`, the GitHub command-line tool. For any other CI system, or when `gh` says you are not logged in, ask the user to save the log of the failed job to a file, copy it to `/tmp/ci.log` and start at step 4.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Find the run.
   - The branch has a pull request: `gh pr checks`. Each line is one check: name, `pass`, `fail`, `pending`, `skipping` or `cancel`, and a link. Take a line that says `fail`; the number after `/runs/` in its link is the run id. A link without `/runs/` belongs to another CI system: see the note above.
   - Otherwise:

     ```
     gh run list --branch "$(git branch --show-current)" --limit 5 --json databaseId,workflowName,conclusion,headSha,createdAt
     ```

     `databaseId` is the run id. Take the newest entry whose `conclusion` is `failure`.
2. Look at the run:

   ```
   gh run view <id>
   gh run view <id> --json workflowName,headBranch,headSha,event,attempt
   ```

   The first command lists jobs and steps; `X` marks the ones that failed. Note the job name, the step name and `headSha`.
   If it says `This run likely failed because of a workflow file issue`, no job started and there is no log. The error is in the workflow file under `.github/workflows/`: read it and fix the syntax or the wrong key. Only a new run can check that fix, so say so in the report and go to step 9.
3. Save the log of the failed steps and strip the colour codes:

   ```
   gh run view <id> --log-failed > /tmp/ci-raw.log 2>&1; echo "exit=$?"
   tr -d '\033' < /tmp/ci-raw.log | sed -E 's/(\^\[)?\[[0-9;]+m//g' > /tmp/ci.log; wc -l /tmp/ci.log
   ```

   Each line is: job, tab, step, tab, then the time and the text. Do not print the whole file.
4. Find the first error, not the last:

   ```
   grep -n -F '##[error]' /tmp/ci.log | head -n 5
   grep -n -w -i -E "error|errors|failed|failure|fatal|panic|traceback|denied|killed|timeout" /tmp/ci.log | head -n 20
   ```

   - A `##[error]Process completed with exit code <n>.` line marks where a step ended. It is not the cause.
   - The cause is the first hit of the second command that is a message from a tool. Skip hits that are only a package name or a file name.
   - Read 10 lines before it and 20 after. For a hit on line 727: `sed -n '717,747p' /tmp/ci.log | cut -f3-`.

   Write down: the job, the step, the command that failed and the first error line.
5. Find the command CI ran: `grep -rn -F "<step name>" .github/workflows/`. Open that file at that line and read the `run:` text of the step, plus the `env:`, `with:` and `strategy:` settings near it.
6. Reproduce it locally.
   - `git rev-parse HEAD` should print `headSha`. If it does not, say so in the report. Do not switch branches or discard work to make it match.
   - Run the same command: `<command> > /tmp/local.log 2>&1; echo "exit=$?"; tail -n 40 /tmp/local.log`.
   - Compare the first error with the one from step 4.
   - If the command needs CI secrets, a service or another operating system, do not fake them. Write "not reproduced" with the reason and work from the log.
7. Decide what kind of failure it is. Use the first row that fits:

   | Evidence | Kind | What to do |
   | --- | --- | --- |
   | Same error locally, in a file or test this branch changed | Real, from this change | Fix the code |
   | The same job fails with the same error on the default branch (commands below) | Real, not from this change | Report it. Fix it here only when asked |
   | Fails in CI, passes locally, and fails again on the same commit | Environment difference | Compare tool versions, operating system, environment variables and file name case between the workflow file and your computer. Fix the difference, for example by pinning the version |
   | The error is a network timeout, a failed download, a rate limit or a lost runner, in a step this change did not touch | Probably flaky | Ask before a re-run. Re-run once at most. If it passes, report "flaky" with the error line. Do not call it fixed |
   | Only one cell of a matrix fails | Depends on that version or system | Reproduce with that version, then use the rows above |

   Runs of the same workflow on the default branch:

   ```
   gh repo view --json defaultBranchRef --jq .defaultBranchRef.name
   gh run list --workflow "<workflowName>" --branch <default branch> --limit 5 --json conclusion,headSha,createdAt
   ```
8. Fix the cause, then run the command from step 6 again and read the result. Never make CI green by any of these unless the user asks for exactly that: deleting or skipping a test, adding a retry loop, `continue-on-error: true`, `|| true`, a longer timeout with no evidence that time is the cause, or removing the job.
9. Do not push and do not re-run unless asked. Both start pipelines.
   - Re-run when asked: `gh run rerun <id> --failed`.
   - After a push that was asked for: `gh pr checks`, or `gh run watch <new id> --exit-status`, which waits until the run ends and exits non-zero when it fails.

## Report

```
Run: <workflow>, id <id>, commit <short sha>, attempt <n>
Failed: job <job>, step <step>
First error: <one line from the log>
Kind: <real, from this change | real, not from this change | environment difference | probably flaky | workflow file>
Evidence: <what shows it>
Reproduced locally: <yes | no: <reason>>
Fix: <what you changed, or none>
Checked: <command>: <passed | failed | not run>
Pushed or re-run: <no | what was asked for>
```

## Gotchas

- `--log-failed` holds only the failed steps. When the cause is in an earlier step, such as a dependency install that printed a warning, get everything with `gh run view <id> --log > /tmp/ci-full.log 2>&1` and search that file. It is large; never print it.
- When one job of a matrix fails, the others are often cancelled. Look at the job whose result is `failure`, not at the cancelled ones.
- A re-run uses the same commit. It tests nothing you changed since.
- A plain `grep error` also matches package names such as `error-utils`. That is why step 4 reads the hits instead of counting them.
- `gh pr checks` exits with code 8 while checks are still pending. That is not a failure.
- Logs can hold tokens and internal addresses. Quote single lines in the report, never whole sections.
