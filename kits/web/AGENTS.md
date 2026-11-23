# {{project}}

TODO: one line that says what this site or app is and who uses it.

## Commands

A line that still says `TODO` has no command yet. Find it in `package.json` or the README; do not guess.

- Install: `{{cmd:install}}`
- Test everything: `{{cmd:test}}`
- Test one file: `TODO: add the command`
- Lint: `{{cmd:lint}}`
- Typecheck: `{{cmd:typecheck}}`
- Build: `{{cmd:build}}`

## Verify before finishing

- Run the tests that cover the files you changed, then lint, typecheck and build. Read the output.
- Say which checks you ran, which passed, and which you could not run or could not see in a browser.

## Conventions

- Match the file you are editing: naming, imports, styling, comment style. TODO: list only the choices that differ from the tool defaults.

## Stack

- Framework and version: TODO: name them. Until then, read `dependencies` in `package.json`.
- Package manager: the one in the Install command above. Never add a second lock file or edit one by hand.
- Styling: TODO: name the system (plain CSS, CSS modules, utility classes, a component library). Until then, copy the nearest component.

## Run and check in a browser

- Dev server: `{{cmd:dev}}`. It keeps running: start it in the background and stop it when you finish.
- Address: TODO: add the URL and port. Until then, read it from the dev server's output.
- After a change you can see: open the changed page, read the browser console, try the state you changed, at a narrow and a wide window.

## Components

- Location and naming: TODO: for example `src/components/`, one component per file. Until then, copy the nearest component.
- Search for an existing component before you create one. Reuse it or extend it, and use the project's own styles and design tokens.

## Accessibility

- Target: TODO: for example WCAG 2.2 level AA. Every control has a name, works from the keyboard and shows where focus is.

## Boundaries

Always:
- Read a file before you edit it, and keep changes inside this folder.

Ask first:
- Adding or upgrading a dependency, deleting files, or changing a route, a shared component's props, or build and deployment configuration.

Never:
- Commit secrets, keys or tokens, or put them in code that is sent to the browser.
- Push, force-push or rewrite history unless asked.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
