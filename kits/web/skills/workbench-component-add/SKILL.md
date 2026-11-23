---
name: workbench-component-add
description: "Add a UI component the way this project already does it. Use before creating or extending a component: find where components live, reuse an existing one, copy the naming, props and styling pattern."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- The task needs a piece of UI that may not exist yet: a button variant, a card, a form field, a dialog, a list row.
- Before you create a component file, and before you paste markup that an existing component already produces.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Find where components live and how they are named. Read `AGENTS.md`, section `Components`. When it still says `TODO`, list the folders that hold component files, most files first:

   ```
   git ls-files | grep -E '\.(tsx|jsx|vue|svelte|astro)$' | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -n 10
   ```

   In a folder without Git, list the files instead:

   ```
   find . -path ./node_modules -prune -o -type f \( -name "*.tsx" -o -name "*.jsx" -o -name "*.vue" -o -name "*.svelte" -o -name "*.astro" \) -print | head -n 60
   ```
2. Search for a component that already does the job. Search by file name and by content, with two or three words for the same thing (for a dialog also try `modal` and `overlay`):

   ```
   git ls-files | grep -i "<word>"
   git grep -n -i -l "<word>" -- "*.tsx" "*.jsx" "*.vue" "*.svelte" "*.astro"
   ```

   Without Git: `grep -rn -i -l "<word>" --include="*.tsx" --include="*.jsx" --include="*.vue" --include="*.svelte" --include="*.astro" --exclude-dir=node_modules .`
3. Decide. Take the first line that is true:

   | What you found | Do this |
   | --- | --- |
   | A component that does the job | Use it. Do not create anything. Go to step 7. |
   | A component that nearly does the job | Extend it with one prop or one variant. Keep the default so that existing uses do not change. List those uses first: `git grep -n "<ComponentName"`. Go to step 6. |
   | `components.json` in the project root | Components come from a registry. Follow "Registry components" below. |
   | A UI library in `dependencies` of `package.json` has it | Import it from that library, the way other files in the project do. Go to step 7. |
   | Nothing | Create it. Go to step 4. |
4. Pick a model: the existing component most like the one you need, in the folder from step 1. Read the whole file and run `ls <its folder>`. Copy these things from it:
   - Place and file name: same folder, same letter case, same extension.
   - Export: default or named, the same as the model.
   - Props: how they are declared and typed, how they are named, whether `className`, `children` or other props are passed on.
   - Styles: the same system as the model (CSS module, utility classes, scoped style block, styled helper), and the same helper for joining class names when it uses one.
   - First line: when the model starts with `"use client"`, keep that line in your file if the component uses state, effects, event handlers or browser APIs.
   - Companion files: when the model has a test, a story or a style file beside it, add the same for yours.
5. Write the component:
   - Smallest set of props the task needs. No props "for later".
   - Use a native element for the job: `<button>` for an action, `<a href>` for navigation, `<label>` with a form field.
   - No colour, spacing or font value typed by hand when the project has tokens. See which exist: `grep -rn "var(--" --include="*.css" --exclude-dir=node_modules . | head -n 10`, or read the theme file the model imports.
   - No new dependency. If you think one is needed, stop and ask.
6. Connect it. When the folder has an index file that re-exports its components (`ls <folder>` shows `index.ts` or `index.js`), add yours in the same form. Then use the component where the task needs it.
7. Verify:
   - Run typecheck, lint and the tests, as `workbench-verify-change` describes.
   - Look at it in the browser with `workbench-browser-verify`.
   - Check names, keyboard and contrast with `workbench-accessibility-check`.

## Registry components

`components.json` means the project adds components with the `shadcn` command line tool, which copies their source into the project.

1. `components.json` lists import aliases under `aliases` (`ui`, `components`). Find the folder they point to. If the component is already there, use it.
2. If it is not, ask before you run the tool. `npx -y` downloads the `shadcn` package when the project does not have it, and the add command installs the packages a component needs.
3. Preview what would be added. With `--dry-run` the command writes no project files:

   ```
   npx -y shadcn@latest add <name> --dry-run
   ```
4. Add it. This writes the component files into the project:

   ```
   npx -y shadcn@latest add <name>
   ```
5. Run `git status --short` and read every file the command created or changed.
6. Do not rewrite a file that came from the registry to change one use. Wrap it or pass props.

## Report

```
Component: <name>
Decision: reused <file> | extended <file> with <prop> | created <file> | added from registry
Model: <file you copied the pattern from, or "none">
Files: <each file added or changed>
Checks: <typecheck, lint, tests, browser: passed | failed | not run>
```

## Gotchas

- Letter case matters. An import of `./button` can find `Button.tsx` on macOS or Windows and still fail on Linux, where the build server usually runs. Write the import exactly like the file name.
- Do not move, rename or reformat existing components while you add one.
- Do not copy a component from another project or from memory of a library the project does not use. It brings a different styling system with it.
- An extended component is used elsewhere. After you change it, open one page that used it before and check that it looks the same.
- A component that is used once and is ten lines long may not need its own file. Follow what the project does with similar cases.
