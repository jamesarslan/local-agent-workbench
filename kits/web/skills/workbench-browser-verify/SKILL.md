---
name: workbench-browser-verify
description: "Check a UI change in a real browser. Use after editing a page, component or style: start the dev server, open the changed page, read the console, try the changed state, report what you saw."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- After you changed something a user can see: a page, a component, a style, a form, a route.
- Before you say a UI change works. Passing tests and a clean build do not show what the page does.

Skip it for a change with no visible effect, such as types, tests or build configuration.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Find the dev command and the address. Use the first source that has them:
   1. `AGENTS.md`, section `Run and check in a browser`. A line that still says `TODO` is not a value.
   2. `npm run`, which lists the scripts in `package.json`. Use `dev`; when there is none, use `start`. Use `pnpm`, `yarn` or `bun` in place of `npm` when `pnpm-lock.yaml`, `yarn.lock`, or `bun.lock` or `bun.lockb` exists.
2. When you already know the address, check whether a server is running there:

   ```
   curl -s -o /dev/null -w "%{http_code}\n" <address>
   ```

   - `000`: nothing is listening. Go to step 3.
   - Any other number: a server answers. Use it and go to step 5. It is not yours: do not stop it at the end.
3. Start the dev server in the background and keep its output. It never exits, so a foreground run would block you.

   ```
   <dev command> > /tmp/dev.log 2>&1 &
   echo $! > /tmp/dev.pid
   ```

   If your tools have their own way to run a command in the background, use that instead, and read its output wherever this skill says `/tmp/dev.log`.
4. Wait for the address:

   ```
   sleep 5; tail -n 20 /tmp/dev.log
   ```

   - The log shows an address such as `http://localhost:5173/`: use exactly that one. The port is not always the one you expected, because the server may pick the next free port.
   - No address and no error yet: run the command again, at most 6 times.
   - An error, or no address after 6 tries: stop the server (step 9) and report the first error line of the log.
5. Check that the page answers:

   ```
   curl -s -o /dev/null -w "%{http_code}\n" <address><path of the changed page>
   ```

   - `200`: go on.
   - `000`: wrong host or port, or the server died. Read `tail -n 20 /tmp/dev.log`.
   - `404`: wrong path. Find the route in the code.
   - `500`: the server failed to build or render the page. Read `tail -n 40 /tmp/dev.log`, fix the error, run the command again.
   - `301`, `302`, `307` or `308`: a redirect. Add `-L` to the command to follow it. If it ends at a login page, see Gotchas.
6. Open the page with your browser tools. Use the set you have. A name may carry a prefix, such as the server's name.

   | Action | Playwright MCP | Chrome DevTools MCP |
   | --- | --- | --- |
   | Open the address | `browser_navigate` | `navigate_page` |
   | Read the page as text | `browser_snapshot` | `take_snapshot` |
   | Read the console | `browser_console_messages`, `level` set to `warning` | `list_console_messages` |
   | Click, type, press a key | `browser_click`, `browser_type`, `browser_press_key` | `click`, `fill`, `press_key` |
   | Wait for a text | `browser_wait_for` | `wait_for` |
   | Run JavaScript in the page | `browser_evaluate` | `evaluate_script` |
   | Change the window size | `browser_resize` | `resize_page` |
   | Take a picture | `browser_take_screenshot` | `take_screenshot` |

   No browser tool at all: do "Without a browser tool" below instead of step 7.
7. Check these four things, in this order:
   1. Content. Take a snapshot. Find the element you changed by its text or its role. If it is missing, you are on the wrong page or the change did not load: open the address again.
   2. Console. Read the console. An error that names a file or component you changed is yours: fix it. Any other error: report it and leave it.
   3. Behaviour. Do what a user does with the thing you changed: click it, type into it, submit it. Take a new snapshot and confirm it shows the expected result. Read the console again.
   4. Width. Only when you changed layout or styles. Set the window to 375 by 667, then to 1280 by 800. At each size run this in the page:

      ```
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth
      ```

      `true` means the page scrolls sideways at that width. That is a failure unless the design asks for it. If you can view images, also take a picture at each size and look at it. If you cannot, report "layout not checked by eye".
8. When a check fails, fix the code, open the address again and repeat step 7 from the start. If the same check still fails after two different fixes, stop and report what you tried.
9. Stop the server, but only if you started it in step 3:

   ```
   kill "$(cat /tmp/dev.pid)"
   curl -s -o /dev/null -w "%{http_code}\n" <address>
   ```

   The second command must print `000`. If the server still answers and `lsof` is installed, `lsof -ti tcp:<port>` prints the id of the process that holds the port; stop it with `kill <id>`.

## Without a browser tool

You cannot see the page. Check what can be checked and say what was not.

1. The command in step 5 prints `200`.
2. `curl -s <address><path> | grep -c "<text you expect>"` prints a number above 0 when the server sends that text. `0` proves nothing for a page that JavaScript builds in the browser.
3. `grep -n -i -E "error|warn" /tmp/dev.log | tail -n 20` shows build errors and warnings.
4. Do step 9. In the report write "Not checked in a browser: no browser tool".

## Report

```
Browser check: <address and path>
- Server: started and stopped by me | was already running, left running
- Content: <what the snapshot shows for the changed element>
- Console: no errors | <first error line>
- Behaviour: <what you did> -> <what the page showed>
- Width: no sideways scroll at 375 and 1280 | not checked (<reason>)
Not checked: <what and why, or "nothing">
```

Write only what you saw in this session after your last edit. Never write "should look right".

## Gotchas

- `200` does not prove the page exists. The dev server of a single-page app answers every path with `200` and the same empty shell. Only the snapshot shows the real content.
- Use the address exactly as the log prints it. `localhost` and `127.0.0.1` do not always reach the same server.
- A snapshot that shows an error message in place of your page means the dev server could not build it. The same error is in `/tmp/dev.log`. Fix that first.
- A snapshot taken right after an action can still show the old state while data loads. Use the wait tool with the text you expect, then take the snapshot.
- A page behind a login: do not guess a password and do not create an account. Ask for a test login, or report the page as not checked.
- A dev server left running keeps its port. The next start then lands on another port and you check the wrong server. Always do step 9.
