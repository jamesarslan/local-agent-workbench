---
name: workbench-game-project-for-godot
description: "Work in a Godot project. Use in a folder with project.godot before changing scripts, scenes or assets: where files go, what not to edit by hand, how to check scripts and run tests with no window."
license: MIT
compatibility: Godot 4.3 or later. The import, check, test and run steps need the Godot editor executable on this computer. Import, check and run commands checked with Godot 4.7.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- The folder has a `project.godot` file and you are about to change scripts, scenes, resources or assets.
- You need to check a script, run the tests or run the game without a window.

Written for Godot 4.3 or later. If step 1 shows an older version, do steps 1 to 3 only and ask before running anything: some options below do not exist there.

## Steps

1. Find the editor executable and the project's version.
   - Executable: use the path in `AGENTS.md` when it has one. Otherwise try `godot --version`, then on macOS `/Applications/Godot.app/Contents/MacOS/Godot --version`. If neither prints a version, ask for the path. Do not download the engine.
   - Project: `grep -n "config/features" project.godot`. The first value is the version the project was saved with, for example `"4.3"`. No such line means the project is older than Godot 4.
   - The first two numbers must be the same in both. If they differ, run nothing below; report both versions and ask.

   Below, `godot` stands for the executable you found.
2. Learn the layout before you add a file.
   - `res://` is the folder that holds `project.godot`. Every path inside the project is written from there.
   - `grep -n "run/main_scene" project.godot` shows the scene the game starts with. `grep -n -A 12 "^\[autoload\]" project.godot` lists what is loaded globally at start.
   - `git ls-files "*.tscn" "*.gd" "*.cs" | head -n 60` shows where scenes and scripts live. Put a new file beside files of the same kind.
   - Name new files and folders in `snake_case`. A C# file is named after its class in `PascalCase`. Nodes are named in `PascalCase`.
   - `addons/` holds third-party code. Do not edit it.
3. Follow these rules for each kind of file.

   | File | Rule |
   | --- | --- |
   | `.gd`, `.cs`, `.gdshader` | Text. Edit freely. |
   | `.tscn`, `.tres` | Text. Read and search freely. By hand, change only the value on a `name = value` line that already exists. To add or remove a node, a resource, a signal connection, or any line in square brackets, use the editor or an editor tool, or ask the user to do it. |
   | `project.godot` | Ask first. Change one existing value only. Input actions and autoloads are added in the editor. |
   | `*.import`, `*.uid` | Never edit. Keep them in version control. Move, rename or delete each one together with the file it sits beside. |
   | `.godot/` | Generated cache. Never edit, never commit. |
   | `.scn`, `.res`, images, audio, models | Binary. Never open or edit as text. |

4. Import. Do this when the `.godot/` folder is missing (a fresh copy of the project) and after you add or replace an image, sound or model:

   ```
   godot --headless --path . --import > /tmp/godot-import.log 2>&1; echo "exit=$?"; grep -n "ERROR" /tmp/godot-import.log | head -n 20
   ```

   Check: `exit=0`, and each new asset now has a file named `<asset>.import` beside it.
5. Check every `.gd` file you changed, one command per file. If `.godot/` is missing, do step 4 first.

   ```
   godot --headless --path . --check-only --script res://<path/to/file.gd> > /tmp/godot-check.log 2>&1; echo "exit=$?"; grep -n -A 1 -E "SCRIPT ERROR|ERROR:" /tmp/godot-check.log
   ```

   Passed only when `exit=0` was printed and `grep` printed nothing. An error looks like `SCRIPT ERROR: Parse Error: <message>`, and the line after it gives the file and line number. Fix it and run the same command again.

   C# project (a `.csproj` beside `project.godot`): `godot --headless --path . --build-solutions --quit; echo "exit=$?"`. This needs the .NET build of the editor.
6. Run the tests. `ls addons` decides which command:
   - `addons/gut` exists:

     ```
     godot --headless --path . -s addons/gut/gut_cmdln.gd -gexit > /tmp/godot-test.log 2>&1; echo "exit=$?"; tail -n 25 /tmp/godot-test.log
     ```

     With no `.gutconfig.json` in the project, add `-gdir=res://<test folder> -ginclude_subdirs`. One file: add `-gtest=res://<path/to/test_file.gd>`. Tests whose name contains a text: add `-gunit_test_name=<text>`. Passed when `exit=0` and the totals at the end show no failing test. `exit=1` means at least one test failed.
   - `addons/gdUnit4` exists:

     ```
     GODOT_BIN="<full path to the executable>" ./addons/gdUnit4/runtest.sh -a <test folder> -c; echo "exit=$?"
     ```

     `GODOT_BIN` must be a full file path, not a command name. `-c` keeps going after the first failure. `exit=0` passed, `exit=100` tests failed, `exit=101` warnings. It opens a minimised window. On Windows use `addons\gdUnit4\runtest.cmd`.
   - Neither exists: the project has no test framework. Do not install one unasked. Do step 7 and say in the report that there are no tests.
7. Run the game with no window for a short time and read the log:

   ```
   godot --headless --path . --quit-after 300 > /tmp/godot-run.log 2>&1; echo "exit=$?"; grep -n -A 1 -E "SCRIPT ERROR|ERROR:" /tmp/godot-run.log | head -n 30
   ```

   To run one scene instead of the main scene, add its path at the end: `res://<path/to/scene.tscn>`. `--quit-after 300` stops after 300 frames. Passed when `exit=0` and `grep` printed nothing.
8. Export a build only when asked: `godot --headless --path . --export-release "<preset name>" <output file>`. The preset name comes from `export_presets.cfg`. A relative output path starts at the project folder, and its folder must already exist. Export templates for this engine version must be installed; if they are not, report that and stop.

## Report

```
Engine: <version printed by the executable>; project saved with <version>
Changed: <files>
Checks
- <command>: passed | failed (<first error line>) | not run (<reason>)
Needs the editor or a person: <scene edits you could not make, anything only visible on screen, or "nothing">
```

## Gotchas

- The exit code is 0 even when a script has a parse error or fails while the game runs. The `grep` for `SCRIPT ERROR` is the check, not the exit code.
- An option the executable does not know is ignored without any message. If a command seems to do nothing, compare its spelling with `godot --help`.
- Always pass `--headless`, and one of `--quit`, `--quit-after`, `--check-only`, `--import` or `-gexit`. Without them a window opens or the command never returns.
- Never add `-d` or `--debug` in these commands. On a script error it stops at an interactive `debug>` prompt and waits.
- Passing `project.godot` itself or `-e` opens the editor. Do not do that as a check.
- In PowerShell, write the log to `$env:TEMP\godot.log`, use `"exit=$LASTEXITCODE"` in place of `echo "exit=$?"`, and `Select-String -Path <file> -Pattern "<text>"` in place of `grep`.
- `--headless` draws nothing and plays no sound. It proves that scripts load and run, not that the game looks or sounds right. Say so in the report.
- Renaming or moving a script, scene or node breaks every place that names it. Before you rename, run `git grep -n "<old name>" -- "*.tscn" "*.tres" "*.gd" project.godot` and list the hits. Move the `.uid` or `.import` file with the file it belongs to.
- A `.tscn` file refers to scripts and resources through `[ext_resource ... id="..."]` lines and `ExtResource("id")` values. That is why a hand-written node or resource entry breaks so easily.
