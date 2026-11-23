---
name: workbench-web-performance
description: "Measure and fix web performance. Use when a page loads slowly, a bundle is too large or a component re-renders too often: measure first, change one thing, measure again, keep only what helps."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- A page loads slowly, the build output grew, typing or clicking lags, or you are asked to make something faster.
- Before any change whose only purpose is speed or size. No number before, no change.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Pick one metric that matches the complaint:

   | Complaint | Metric | Section |
   | --- | --- | --- |
   | Big download, slow first load, "the bundle is too large" | Bytes of built JavaScript and CSS | A |
   | Slow page load, many or large requests | Number, size and time of requests | B |
   | Typing, clicking or scrolling lags | Render count of the component | C |
2. Measure before you change anything. Write down the number and the exact command or tool call that produced it.
3. Find the largest contributor: the biggest file, the slowest or repeated request, the component with the highest count. Write one sentence: "<number> is high because <cause>."
4. Make one change that follows from that sentence. The table "Usual causes" lists common ones.
5. Measure again, the same way, on the same page, with the same actions.
6. Keep or undo:
   - Bytes and counts are exact. Keep the change when the number went down. Otherwise undo your edit.
   - Times vary between runs. Take three runs before and three after and compare the middle values. Keep the change only when the difference is larger than the spread between the three runs.
7. Repeat from step 3 for the next largest contributor. Stop when the goal is met, or when two changes in a row gave no gain.
8. Clean up and verify:
   - `git grep -n --untracked "DEBUG-TMP"` must print nothing. In a folder without Git use `grep -rn "DEBUG-TMP" --exclude-dir=node_modules .`
   - Run the project's tests, lint and build, as `workbench-verify-change` describes. A faster page that fails a test is not done.

## A. Bundle size

1. Build, and keep the log:

   ```
   <build command> > /tmp/build.log 2>&1; echo "exit=$?"; tail -n 40 /tmp/build.log
   ```

   Go on only when it prints `exit=0`. The log or the framework's config file names the output folder, for example `dist`.
2. List the largest files, in bytes. With more than one file the last line is the total:

   ```
   find <output folder> -type f \( -name "*.js" -o -name "*.css" \) -exec wc -c {} + | sort -n | tail -n 12
   ```

   When the output folder also holds server code, point the command at the sub-folder that is sent to the browser.
3. Size of one file after compression, which is closer to what is downloaded: `gzip -c <file> | wc -c`
4. To see why a file is large, check which packages the code imports. `package.json` lists them under `dependencies`. For each suspect:

   ```
   grep -rn -E "from ['\"]<package>['\"/]" --exclude-dir=node_modules <source folder>
   du -sh node_modules/<package>
   ```

   The first command shows where it is imported. The second shows its size on disk, which is only a hint: the build may include a small part of it.

## B. Requests

Measure a production build, not the dev server. `npm run` lists the scripts; start `preview` or `start` in the background the way `workbench-browser-verify` step 3 starts the dev server. When there is no such script, use the dev server and say so in the report.

- Playwright MCP: `browser_navigate` to the page, then `browser_network_requests` with `static` set to `true`. `browser_network_request` with a number from that list shows one request in full.
- Chrome DevTools MCP: `navigate_page` to the page, then `list_network_requests`. For load metrics call `performance_start_trace` with its defaults: it reloads the page, records for a few seconds, stops by itself and returns a summary.
- No browser tool: this measures one address only, not the files the page loads after it.

  ```
  curl -s -o /dev/null -w "%{http_code} %{size_download} bytes %{time_total}s\n" <address><path>
  ```

Write down: how many requests, the three largest, any that failed, and any address that was requested more than once.

## C. Render count

1. Add this as the first line inside the component function. Keep the mark `DEBUG-TMP`:

   ```
   console.count("render <ComponentName>") // DEBUG-TMP
   ```

   In a Vue component, count updates instead: `onUpdated(() => console.count("update <ComponentName>")) // DEBUG-TMP`, with `onUpdated` imported from `vue`.
2. Open the page, do the slow action once (type five characters, click once), then read the console: `browser_console_messages` or `list_console_messages`. The last line `render <ComponentName>: <n>` holds the count.
3. Do exactly the same action for the measurement after your change.

In development React calls each component function twice when the app is wrapped in `<StrictMode>`. Counts are then doubled, before and after alike. Compare them as they are and leave `<StrictMode>` in place.

## Usual causes

Use this only after step 3 named a contributor.

| Measurement shows | Look for | Usual fix |
| --- | --- | --- |
| One very large JavaScript file | A large package imported at the top of a file that every page loads | Load it with `import()` in the place that needs it, or import only the function you use. |
| Large image requests | An image far bigger than the size it is shown at | Serve a smaller file. Add `loading="lazy"` to images below the first screen, and set `width` and `height`. |
| The same address requested twice or more | A fetch inside a component that appears several times, or inside an effect that runs again | Fetch once in a parent and pass the data down. |
| A high render count | A parent that builds a new object, array or function on every render and passes it down; state kept higher than where it is used | Move the state down. In React, wrap the value in `useMemo` or `useCallback`, or the component in `memo`. |
| A long list that is slow | Hundreds of rows rendered at once | Show pages of rows. A library that renders only visible rows is a new dependency: ask first. |

## Report

```
Metric: <what was measured, and the command or tool>
Before: <number>
Change: <one sentence> (<file>:<line>)
After: <number>
Kept: yes | no, undone
Checks: <tests, lint, build: passed | failed | not run>
Not measured: <what and why, or "nothing">
```

One block per change, including the ones you undid. Never write "faster" without both numbers.

## Gotchas

- Dev server numbers are not production numbers. A dev server sends code that is not minified. Measure sizes on build output.
- Built file names carry a hash that changes with the content. Compare the total and the largest sizes, not names.
- Compare like with like: raw bytes with raw bytes, compressed with compressed, the same page, the same window size.
- A second load is faster than the first because the browser keeps files. Load the page the same way for every measurement.
- Memoising everything is not a fix. It adds code and comparisons. Do it only where the render count shows a problem.
- Do not gain numbers by removing features, tests, error handling or accessibility markup.
- Undo only your own edit. Do not run a command that discards other uncommitted work in the file.
