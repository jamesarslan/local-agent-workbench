# Starter kits

A starter kit gives a project folder three things in one step: an `AGENTS.md` template, a few skills, and a short list of MCP servers to choose from. Agent Workbench shows a preview first and writes only what you confirm. An `AGENTS.md` or skill file you already have is kept as it is.

This page describes the kit format. The code that reads it is `starter_kits.py`; the checks are in `tests/test_starter_kits.py`.

## Layout

```
kits/<id>/kit.json
kits/<id>/AGENTS.md
kits/<id>/skills/<skill-name>/SKILL.md      (plus optional files beside it, one folder level deep)
```

The kits are listed in this order: `general`, `web`, `python-data`, `3d`, `research-writing`, `devops`, `gamedev`, `blank`. Any other valid kit folder follows, sorted by id.

Kits are named after the people they are for. Product names appear only in `works_with`, in descriptions and in a skill's `compatibility` line.

## kit.json

Every key below is required. Any other key is an error.

| Key | Rule |
| --- | --- |
| `schema` | `1` |
| `id` | The folder name: lowercase letters, digits and dashes, at most 32 characters |
| `label` | Shown in the kit list, at most 60 characters |
| `summary` | One sentence, at most 200 characters |
| `works_with` | Product names, at most 120 characters; may be empty |
| `version` | Three numbers, for example `0.4.0` |
| `priority` | 0 to 100; decides between kits that both match a folder strongly |
| `detect` | `{"strong": [rule], "weak": [rule]}`, at most 24 rules in each list |
| `skills` | At most 6 entries `{"name": "workbench-…", "when": rule}`; `when` is optional |
| `mcp` | At most 12 server entries; may be empty |

## Server entries

A server entry describes one MCP server the kit offers. The launch fields follow exactly the rules of `.agent-workbench/project.json`, because that is where a selected server is written.

| Key | Rule |
| --- | --- |
| `name` | Letters, digits, dashes or underscores; not the name of a built-in tool |
| `label`, `publisher` | Text of at most 60 characters |
| `source` | An `https://` address of at most 200 characters |
| `license` | At most 40 characters |
| `version` | The pinned version, at most 40 characters; `hosted` for a remote service |
| `checked` | `registry` or `launched`, see below |
| `transport` | `stdio` or `http` |
| `command`, `args` | For `stdio`: a program name found on PATH (`npx`, `uvx`, `docker`) and its arguments, with the version pinned. Leave both out when `platforms` gives a command per system |
| `env` | For `stdio`: fixed, non-secret environment values |
| `env_from` | For `stdio`: names of environment variables whose values you supply; the values never enter a project file |
| `url` | For `http`: an `https://` address, or `http://` on this computer only |
| `header_env`, `bearer_env` | For `http`: names of environment variables that hold a header value or a bearer token |
| `requires` | Any of `node`, `uv`, `docker`: the tools the launch command needs (`npx`, `uvx`, `docker`); optional |
| `default` | `true` when the entry is preselected in the preview |
| `when` | Optional rule; a `default` entry is preselected only when the rule matches the folder |
| `network` | What the server contacts, at most 200 characters |
| `warning` | What you should know before switching it on, at most 200 characters; may be empty |
| `platforms` | Optional. A list, a subset of `darwin`, `linux`, `win32`: the entry is hidden elsewhere. Or an object that maps some of those systems to `{"command": …, "args": […]}` for a program each system installs in its own place; the command may then be a full path, and a path may start with `~/`, `%LOCALAPPDATA%`, `%APPDATA%` or `%USERPROFILE%`. An object entry is listed on every system, and on one it does not name it is off and says where it runs |
| `installed` | Optional: `{"app": "Roblox Studio", "darwin": path, "win32": path}`, the file that shows the application is installed on each system. A `default` entry is preselected only when that path is a file on this computer; otherwise the preview says "Install Roblox Studio to use this." The path follows the same rules as a command in `platforms`, and only the variables named there are expanded, without a shell |
| `runs_code` | Optional `true` when the server can run code the agent writes; the preview marks it |

`checked` says how far an entry was verified, and the app shows it:

- `registry`: version, licence and program name were read from the package registry and the publisher's documentation. The server was not started.
- `launched`: the server was started and listed its tools.

Every server in the kits shipped here is marked `registry` except Roblox Studio MCP. None of the `registry` servers was started while these kits were prepared. Roblox Studio MCP is marked `launched`: it ships inside Roblox Studio, and it was started from an installed Roblox Studio and listed its tools.

A built-in tool of Agent Workbench is offered with a shorter entry. Selecting it switches that tool on for the folder instead of writing a server:

```json
{"builtin": "search", "label": "Web search", "requires": [], "default": false,
 "network": "Public search engines.", "warning": "Public search engines receive the query."}
```

`builtin` is one of `search`, `firecrawl`, `blender`, `desktop`. Only `builtin`, `label`, `default`, `when`, `requires`, `network` and `warning` are allowed in this form.

The same server may appear in several kits. Its launch fields must then be identical in each.

Servers are not bundled. A selected server is downloaded from its publisher by `npx`, `uvx` or `docker` the first time an agent starts it, under the publisher's own licence. Roblox Studio MCP is the exception: it is part of Roblox Studio, under the Roblox Terms of Use, and Workbench starts the program Studio installed.

The Game development kit's Roblox entry shows the per-system form:

```json
{"name": "roblox-studio", "transport": "stdio",
 "platforms": {"darwin": {"command": "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP"},
               "win32": {"command": "cmd.exe", "args": ["/c", "%LOCALAPPDATA%\\Roblox\\mcp.bat"]}},
 "installed": {"app": "Roblox Studio",
               "darwin": "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP",
               "win32": "%LOCALAPPDATA%\\Roblox\\mcp.bat"},
 "default": true, "runs_code": true, "…": "…"}
```

These are the commands Roblox documents. The selected entry is written to `.agent-workbench/project.json` with every system's command, so the folder works on a Mac and on Windows. `%LOCALAPPDATA%` stays as written there and is expanded only when Workbench starts a session, so a user folder whose name has a space still works through `cmd.exe /c`.

## Rules

`detect` and `when` use one small rule language. Every path and pattern is relative to the project folder; it may not contain `..` or start with `/`.

| Rule | True when |
| --- | --- |
| `{"file": "rel/path"}` | That path is a file |
| `{"dir": "rel/path"}` | That path is a folder |
| `{"glob": "pattern"}` | A scanned file matches: by file name when the pattern has no `/`, otherwise by its relative path. Matching is case-sensitive |
| `{"package_dep": ["a", "b"], "min_major": 16}` | `package.json` lists one of the names under `dependencies` or `devDependencies`; with `min_major`, the first number in that version is at least it |
| `{"mostly": [".md", ".tex"], "share": 0.6, "min_files": 3}` | At least `min_files` files were scanned and at least `share` of them end in one of the suffixes |
| `{"all": [rule]}`, `{"any": [rule]}`, `{"none": [rule]}` | All, at least one, or none of the inner rules hold; rules nest at most 3 deep |

The scan reads file names in the folder, its child folders and their child folders. It follows no links and stops after 5000 entries. It skips `node_modules`, `.git`, `.venv`, `venv`, `Library`, `Temp`, `__pycache__`, `dist`, `build`, `target`, `.next`, `.agent-workbench`, and every other folder whose name starts with a dot except `.github` and `.obsidian`. The only file whose content is read is `package.json`.

How a kit is suggested for a folder:

1. Kits with at least one strong match are ranked by `priority`, then by the number of strong matches, then weak matches.
2. If no kit has a strong match, kits with at least two weak matches are ranked by the number of weak matches, then `priority`.
3. If nothing matches, the suggestion is `blank`.

## Skills

Each skill is a folder that holds `SKILL.md` and, optionally, a few files it refers to. The format is the open Agent Skills format described at https://agentskills.io/specification; kits use only the parts every supported agent reads.

```markdown
---
name: workbench-verify-change
description: "Verify a code change before reporting done. Use after any edit."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use
## Steps
## Report
## Gotchas
```

Rules the loader enforces:

- The folder name equals `name`, and `name` looks like `workbench-some-task` (lowercase letters, digits, single dashes), at most 64 characters.
- `description` has 1 to 200 characters. Put the words that should trigger the skill first. Quote the value when it contains a colon followed by a space, or YAML will not read it.
- The only front-matter keys are `name`, `description`, `license`, `compatibility` and `metadata`. `license` is `MIT`.
- The text after the front matter has at most 500 lines.
- A skill has at most 8 files, each UTF-8 text of at most 32 KiB, beside `SKILL.md` or one folder below it. File names use letters, digits, dots, dashes and underscores. Files whose name starts with a dot are ignored.
- Every folder under `skills/` is listed in `kit.json`.

The test suite adds one rule the loader cannot see from inside a single kit: a skill used by two kits is a byte-identical copy in both.

Selected skills are written to `.agents/skills/<name>/` in the project. When the agent is Claude Code they are also copied to `.claude/skills/<name>/`, because that is the only place it looks.

## AGENTS.md template

Plain Markdown that every agent can read: no `@` import lines, 40 to 60 lines (the `blank` kit about 12), and well under the limits of the agents that cut long files.

Placeholders, filled when the kit is applied:

| Placeholder | Becomes |
| --- | --- |
| `{{project}}` | The folder's name |
| `{{cmd:install}}` | `<package manager> install` when the folder has a `package.json` |
| `{{cmd:dev}}`, `{{cmd:test}}`, `{{cmd:lint}}`, `{{cmd:build}}`, `{{cmd:typecheck}}` | `<package manager> run <name>` when `package.json` has a script of that name |

The package manager is `pnpm`, `yarn` or `bun` when its lock file is present, otherwise `npm`. Anything that cannot be filled becomes `TODO: add the command`. Only the fixed script names are used; the content of a script is never copied. Workbench does not guess commands for other ecosystems. No other `{{…}}` may appear in a template.

Sections, in this order: the title and one line about the project, `## Commands`, `## Verify before finishing`, `## Conventions`, the kit's own sections, `## Boundaries` with `Always:`, `Ask first:` and `Never:` lists, and `## Gotchas`. The `blank` kit has only the title, `## Commands` and `## Boundaries`.

## What applying a kit does

1. Workbench builds a plan: `AGENTS.md`, the selected skills, a `CLAUDE.md` pointer when Claude Code needs one, and the selected servers in `.agent-workbench/project.json`.
2. You see every file with its action (`create`, `update` or `skip`) and choose what to write.
3. An existing `AGENTS.md`, `CLAUDE.md` or skill file of yours is kept. Such a file is updated only when Workbench wrote it earlier and it has not changed since. Selected servers are added to `.agent-workbench/project.json`; a server you already defined under the same name is kept. When your definition differs from the kit's, the preview says so and shows the command that will run.
4. The kit id, the selected built-in tools and the selected servers are remembered for the folder on this computer.

A kit folder that has no `AGENTS.md` or is missing a listed skill still loads; the missing parts are simply not offered. Once any kit has its text, the test suite fails until every kit has its `AGENTS.md` and every listed skill with valid front matter.

## Adding or changing a kit

1. Edit or create `kits/<id>/kit.json`, `AGENTS.md` and the skill folders.
2. Pin every launch command to an exact version and set `checked` honestly.
3. Run `python -m unittest tests.test_starter_kits -v` from the repository root.

All kit text is written for this project under the MIT licence. Nothing is copied from other skill or prompt collections. Product names are trademarks of their owners; Agent Workbench is not affiliated with or endorsed by them.
