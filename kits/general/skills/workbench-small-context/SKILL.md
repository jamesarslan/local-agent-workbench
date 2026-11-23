---
name: workbench-small-context
description: "Work inside a small context window. Use when a task spans several files or commands print long output: search before reading, read line ranges, keep notes in a file, never print large outputs."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- The model has a small context window, or the session is already long.
- Before you open a file you have not measured, or run a command that may print a lot.
- Right after the conversation was shortened or summarised: read your notes file first.

The limit to keep in mind: no single step should bring more than about 200 lines into the conversation.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Start a notes file, `.agent-notes.md` in the project folder, with at most 40 lines:

   ```
   Goal: <one sentence>
   Done when: <the check that proves it>
   Facts: <path:line - what is there>
   Decisions: <what and why>
   Done: <finished steps>
   Next: <the one next step>
   ```

   In a Git repository, keep it out of commits: `echo ".agent-notes.md" >> "$(git rev-parse --git-path info/exclude)"`. That file is local to this copy of the repository and changes nothing that is tracked.
2. Search before you read. Find the place first, then read only that place.
   - Use your search tool if you have one. Otherwise:
   - Text in tracked files: `git grep -n "<text>" | head -n 30`. Limit to a file type with `git grep -n "<text>" -- '*.py' | head -n 30`.
   - Only the names of files that contain it: `git grep -l "<text>" | head -n 30`.
   - A file by name: `git ls-files | grep -i "<part of the name>" | head -n 30`.
   - No Git repository: `grep -rn "<text>" . --exclude-dir=node_modules --exclude-dir=.venv | head -n 30`.
   - If the list was cut off at 30 lines, make the pattern more specific. Do not raise the limit.
3. Measure before you read: `wc -l <file>`.
   - Up to 200 lines: read the whole file.
   - More: read a range around the line the search found, with your read tool's line range or `sed -n '120,180p' <file>`.
   - To see what a long file contains: `grep -n -E "^(def |class |function |export |func |fn |pub )" <file> | head -n 60` lists its top-level definitions.
4. Send long output to a file and look at the end:

   ```
   <command> > /tmp/out.log 2>&1; echo "exit=$?"; tail -n 30 /tmp/out.log
   ```

   Then pull out only what you need: `grep -n -i -E "error|fail" /tmp/out.log | head -n 20`, or a range with `sed -n '<from>,<to>p' /tmp/out.log`.
5. Keep Git output small: `git status --short`, `git --no-pager diff --stat`, then `git --no-pager diff -- <path>` for one file, and `git --no-pager log --oneline -n 10`.
6. After every finished step, update the notes: move the step to `Done`, add new facts as `path:line`, write the next step. When you are unsure what to do, or the conversation was shortened, run `cat .agent-notes.md` before anything else.
7. Do one step at a time. Finish and check one change before you read files for the next one. Do not read a file "just in case", and do not read one again unless it changed; take the fact from your notes.
8. When the task is done, delete the notes file: `rm .agent-notes.md`.

## Never print these

- Lock files, minified or generated files, build output, dependency folders such as `node_modules`.
- Binary and data files. Check an unknown file's size first with `wc -c <file>`.
- A whole log, a whole test run or a whole diff of many files.

If you need one fact from such a file, search for it with `grep -n "<text>" <file> | head -n 10`.

## Report

Give results as `path:line` and one sentence each. Do not paste file content the user already has. Say which parts of the project you did not look at.

## Gotchas

- A pipe hides the exit code: `<command> | tail` reports the exit code of `tail`. Use the log file form from step 4.
- `rg "<text>"` without a path can wait for input and never return when the shell is not interactive. Always name a path: `rg -n "<text>" . | head -n 30`.
- `sed -n` with a range past the end of the file prints nothing and reports no error. Check the length with `wc -l` first.
- `git grep` searches tracked files only. Add `--untracked` to include new files.
- Do not quote paths, names or numbers from memory of a file you read long ago. Read those few lines again.
