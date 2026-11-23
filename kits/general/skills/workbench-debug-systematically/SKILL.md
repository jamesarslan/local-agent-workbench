---
name: workbench-debug-systematically
description: "Debug a failing test, error or wrong result. Use when the cause is not known yet: reproduce it first, test one hypothesis and make one change at a time, stop and report after two failed fixes."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- A test, build or command fails and you do not know why.
- The program runs but gives a wrong result.
- A fix you made did not work.

Skip this skill when the error message names the exact cause and line, such as a typo or a missing import. Fix that directly and run the command again.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Reproduce. Write down the exact command, what you expected and what happened. Run it and keep the output:

   ```
   <command> > /tmp/repro.log 2>&1; echo "exit=$?"; tail -n 40 /tmp/repro.log
   ```

   If it does not fail, change no code. Report the command you ran and that you could not reproduce the problem.
2. Narrow the command to the smallest one that still fails: one test file, one test name or one input. This is the failing case. Every later step runs it.
3. Read the first error, not the last:

   ```
   grep -n -i -E "error|exception|panic|traceback|fail" /tmp/repro.log | head -n 20
   ```

   In the stack trace, find the first line that points into this project's own code, not into a library. Open that file at that line and read the function around it.
4. Check what changed: `git status --short`, `git --no-pager diff --stat`, `git --no-pager log --oneline -10`. A failure that is new usually comes from a recent change.
5. Write one hypothesis as a sentence: "It fails because <cause>. If so, <value or line> will show <what>."
6. Test the hypothesis before you fix anything. Add one temporary line that prints the value and mark it `DEBUG-TMP`, or run a smaller command that shows it. Run the failing case.
   - The output agrees: go to step 7.
   - The output disagrees: the hypothesis is wrong. Go back to step 5 with what you just saw.
7. Make one change that follows from the confirmed hypothesis. Run the failing case.
   - It passes: go to step 9.
   - Same error: undo the change completely. That is one failed fix. Go back to step 5.
   - A different error: keep the change and start at step 3 with the new error.
8. After two failed fixes, stop. Do not try a third. Give the "Stopped" report below and ask how to continue.
9. Finish:
   - Remove every temporary line. `git grep -n --untracked "DEBUG-TMP"` must print nothing. In a folder without Git use `grep -rn "DEBUG-TMP" .`.
   - If the project has tests, add one that fails without your fix and passes with it.
   - Run the project's full test command. A fix that breaks another test is not done.

## Is the failure older than your work?

Use one of these only when step 4 does not answer the question.

Uncommitted changes. Do this only when `git status --short` printed at least one line:

```
git stash push --include-untracked
<failing case>; echo "exit=$?"
git stash pop --index
```

Run `git stash pop --index` immediately after the command, whatever its result; `--index` puts staged files back as staged. If `git stash push` printed "No local changes to save", do not pop: that would restore some older, unrelated stash. If the pop reports a conflict, stop and tell the user: the changes are still in the stash, and `git stash list` shows it. If the failing case still fails while your changes are stashed, the failure was there before your changes.

Committed changes, when you know an older commit where the failing case passed and `git status --short` prints nothing:

```
git bisect start <bad-commit> <good-commit>
git bisect run <failing case>
git bisect reset
```

The failing case must exit 0 on a good commit and with a code from 1 to 127 on a bad one; 125 means "skip this commit". `git bisect run` prints the hash of the first bad commit. Always run `git bisect reset` afterwards to return to your branch.

## Report

Fixed:

```
Cause: <one sentence with file:line>
Fix: <what you changed>
Proof: <failing case> exits 0; <full test command>: <summary line>
```

Stopped:

```
Reproduce: <command>
Error: <first error line>
Tried: 1. <hypothesis> -> <change> -> <result>
       2. <hypothesis> -> <change> -> <result>
Ruled out: <what you checked and found correct>
Question: <what you need to know to continue>
```

## Gotchas

- Fix the cause, not the symptom. Do not wrap the failing line in a handler that hides the error, do not add a special case for the test's input, and do not change the expected value to whatever the code returns.
- The line that fails is often not the line that is wrong. Follow the bad value back to where it was created.
- Fails only sometimes: run `for i in 1 2 3 4 5; do <failing case> > /dev/null 2>&1; echo "run $i exit=$?"; done`. Mixed results mean it depends on order, timing or shared state. Say so; one green run is not proof of a fix.
- Nothing makes sense: check that the code you edit is the code that runs. Look for a wrong folder, old build output, a cache, or another installed version. `<tool> --version` and a `DEBUG-TMP` print at the top of the file settle it.
