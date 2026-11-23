# {{project}}

TODO: one line that says what this game is and which platforms it targets.

## Commands

A line that still says `TODO` has no command yet. The installed `workbench-game-project-for-...` skill shows the usual form for this engine; do not guess paths.

- Editor executable: `TODO: add the full path`
- Build or export: `TODO: add the command`

## Verify before finishing

- Run the compile check, then the tests that cover what you changed, and read the output. Never delete, skip or weaken a test to make a check pass.
- Say which checks you ran, which passed, which you could not run, and what a person must look at on screen.

## Conventions

- Match the file you are editing: naming, node or component structure, signal or event style. TODO: list only the choices that differ from the engine's defaults.

## Engine

- Engine and exact version: TODO: read it from the project file, do not guess. Use only this version's API and never open or save the project with another version.

## Run and test

- Check that scripts compile: `TODO: add the command`
- Run without a window: `TODO: add the command`
- Test everything: `TODO: add the command`
- Test one file or one test: `TODO: add the command`

## Project layout

- Scripts and tests: TODO. Scenes or levels, art, audio and other assets: TODO

## Asset rules

- Never hand-edit binary assets or engine metadata files (`.meta`, `.import`, `.uid`). Move, rename or delete an asset together with its metadata file.
- Change scene, prefab and level files in the editor or with an editor tool. The engine skill says which small text edits are safe.
- Every asset made outside this project has a row in `ASSETS.md`: file, author, source and licence. No known licence, no import.
- Performance budget: frame rate and target device: TODO. Limits per asset (triangles, texture size): TODO. Ask when a value is still TODO; do not invent a budget.

## Roblox Studio

- With the Roblox Studio MCP tools: call `list_roblox_studios` first and pass its `studio_id` to every other tool, read a script before `multi_edit` changes it, and never `save_place` or publish unless asked. In a Rojo project (`*.project.json`) the files on disk are the source: edit them and let Rojo sync them into Studio.

## Boundaries

Always:
- Read a file before you edit it, and keep changes inside this folder.

Ask first:
- Adding a plugin or package; changing project settings, input maps or build targets; deleting or moving assets; starting the engine while the editor has this project open.

Never:
- Commit secrets, signing keys, engine caches or build output, or push or rewrite history unless asked.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
