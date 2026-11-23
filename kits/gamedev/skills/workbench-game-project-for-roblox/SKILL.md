---
name: workbench-game-project-for-roblox
description: "Work on a Roblox experience through Roblox Studio's MCP server or a Rojo project. Use before reading or changing Luau scripts, instances or a place file, and before a playtest."
license: MIT
compatibility: Roblox Studio on Windows or macOS with "Enable Studio as MCP server" switched on. Rojo, Wally, Selene and StyLua are needed only when the project uses them.
metadata:
  source: agent-workbench
  version: "0.5.0"
---

## When to use

- The session has the Roblox Studio MCP tools (`list_roblox_studios`, `script_read`, `multi_edit` and the rest), or the folder has a Rojo project (`default.project.json`), a place file (`.rbxl`, `.rbxlx`) or `.luau` scripts.
- You are about to read or change a script or an instance, or to playtest.

## Steps

1. Find out how the project is kept.
   - A `*.project.json` file with a `"tree"` key is a Rojo project. The files on disk are the source; Rojo copies them into Studio. Edit the files, never the synced scripts in Studio: Rojo overwrites those, and a change made only in Studio is lost. Read the `"tree"` to see which folder becomes which service, for example `src/server` → `ServerScriptService`.
   - No Rojo project: the scripts live only inside the place in Studio. Read and change them through the MCP tools.
   - In a Rojo folder, the file name sets the script type: `name.server.luau` is a `Script`, `name.client.luau` a `LocalScript`, any other `name.luau` a `ModuleScript`. `init.server.luau` and the like make the folder itself the script.
2. Pick the Studio window. Call `list_roblox_studios` before any other Studio tool. Every other tool needs the `studio_id` it returns. Several places can be open: choose by `name`, and when more than one could be meant, ask. `get_studio_state` says whether Studio is in edit or play mode and which data models (`Edit`, `Server`, `Client`) the tools can reach.
3. Read before you change anything.
   - Find a script by name with `script_search`, or by its text with `script_grep`.
   - Read a whole script with `script_read`, using the dot path such as `game.ServerScriptService.Round`.
   - Look at instances with `search_game_tree` (start with a small `max_depth`) and `inspect_instance` for one instance's properties and attributes.
4. Change scripts.
   - Rojo project: edit the file on disk. With `rojo serve` running and the Rojo plugin connected in Studio, the change appears in Studio by itself. If it does not, ask the user to check the plugin; do not paste the change into Studio.
   - No Rojo project: use `multi_edit` with the exact `old_string` from `script_read`; several edits to one script go in one call. To create a script, give `className` and start with an empty `old_string`.
   - Use `execute_luau` only for changes the script tools cannot make, such as creating or moving instances. It runs with Studio's full rights and freezes Studio until it yields: keep it short, and ask first before anything that deletes or moves many instances.
5. Playtest and read the output.
   - `start_stop_play` with `is_start: true`, then `get_console_output` for errors and your `print` lines. `get_studio_state` shows when play has started.
   - `screen_capture` shows what a player sees. Use it when the change is visible.
   - Always stop play again with `is_start: false`. Changes made while playing are thrown away when play stops.
6. Saving and publishing belong to the user.
   - Never call `save_place` unless the user asked you to save. A place that came from Roblox gets a new version in its history on every save.
   - Never publish, upload images or assets, or call Open Cloud unless the user asked for it.
   - `insert_asset` and the `generate_*` tools add content to the open place and use Roblox services. Ask first.
7. Check the code from the command line when the project has the tools.
   - `selene src` when there is a `selene.toml`.
   - `stylua --check src` when there is a `stylua.toml` or `.stylua.toml`; run `stylua src` only when asked to format.
   - `wally install` after changing `wally.toml`. It rewrites the `Packages` folder; never edit files in it.
   - `rojo build default.project.json -o build.rbxl` checks that the project builds. Do not commit the built file.

## Report

```
Studio: <place name from list_roblox_studios> | not connected (<reason>)
Source: Rojo files on disk | scripts in Studio
Changed: <files or script paths>
Checks
- selene / stylua: passed | failed (<first message>) | not run (<reason>)
- playtest: <what you ran>, console: <errors or "no errors">
Saved: no | yes, because the user asked
Needs a person: <anything only visible on screen or that you did not do, or "nothing">
```

## Gotchas

- `list_roblox_studios` returns no studios: Studio is closed, or "Enable Studio as MCP server" is off (Assistant → … → Manage MCP Servers). Say so and stop. Do not try to start Studio yourself.
- A tool says the data model is not available: check `get_studio_state`. Edits need the `Edit` data model, so stop play first.
- Paths use dots and start at `game`, for example `game.ReplicatedStorage.Shared.Config`. Names with spaces or dots cannot be split this way; find them with `search_game_tree`.
- `script_grep` and `script_search` cap their results. No match is not proof the text is absent; narrow the search.
- Code on the server cannot see a `LocalScript`'s variables, and the other way round. Data crosses with `RemoteEvent` or `RemoteFunction` in `ReplicatedStorage`. Never trust values a client sends.
- In a Rojo project, a script that exists in Studio but has no file is not managed by Rojo. Ask before you move it into the files.
