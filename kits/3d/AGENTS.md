# {{project}}

TODO: one line that says what this project is and names the 3D application with its version.

## Commands

`blender` is the Blender executable. TODO: write its full path here if `blender --version` fails (macOS default: `/Applications/Blender.app/Contents/MacOS/Blender`).

- Run a script on a file: `blender -b <file.blend> --python-exit-code 1 --python <script.py>`
- Render frame 1: `blender -b <file.blend> -o //renders/frame_#### -F PNG -x 1 -f 1`

## Verify before finishing

- After a scripted change, print the name, position and size of what changed and compare them with the request.
- After an export or render, confirm the file exists and is not empty. Open an export again and compare object count and size with the source.
- Say which checks you ran, which passed, and which you could not run.

## Conventions

- Scripts live in `scripts/`. TODO: change the folder if the project differs.
- One task per script. Running it twice must not create duplicates.
- TODO: list only the choices that differ from the application defaults.

## Scene conventions

- Units: TODO: add the unit. Until then, 1 unit is 1 metre. Up axis is Z.
- Objects keep scale 1, 1, 1. Report any object that does not.
- Naming: TODO: add the pattern, for example `Prop_Crate_01`. Never leave default names such as `Cube.001`.
- Collections: TODO: add the layout, for example `Props`, `Lights`, `Cameras`.

## Session

- Application version: TODO. If `blender --version` shows another version, say so before you save a file.
- Before a scripted change, save a copy of the `.blend` and say where it is. In an open window, do not save, open or close files unless asked.

## Assets and export

- Export format and settings: TODO. Until then, use the format the request names, or `.glb` when it names none.
- Exports go to `exports/`, renders to `renders/`. TODO: change these if the project differs.
- Every asset made outside this project has a row in `ASSETS.md`: file, author, source and licence.

## Boundaries

Always:
- Inspect the scene before you change it.
- Write exports and renders to their own folders, never over a source file.

Ask first:
- Deleting objects, collections or files.
- Changing units, frame rate, render engine or resolution.
- Downloading or importing an asset from outside this folder.

Never:
- Overwrite a `.blend` that has no saved copy.
- Import an asset whose licence is unknown.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
