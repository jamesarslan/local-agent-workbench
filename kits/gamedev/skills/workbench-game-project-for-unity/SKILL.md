---
name: workbench-game-project-for-unity
description: "Work in a Unity project. Use in a folder with Assets and ProjectSettings/ProjectVersion.txt before changing scripts or assets: folder layout, .meta file rules, batch-mode compile check, tests, build."
license: MIT
compatibility: Unity Editor installed through Unity Hub. The test step needs the Unity Test Framework package in the project. Commands follow the Unity manual; they were not run when this skill was written.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- The folder has `Assets/`, `Packages/manifest.json` and `ProjectSettings/ProjectVersion.txt`, and you are about to change scripts, assets or packages.
- You need to check that scripts compile, run the tests or make a build from the command line.

## Steps

1. Find the editor that matches the project.
   - `cat ProjectSettings/ProjectVersion.txt` prints the version, for example `m_EditorVersion: 6000.0.69f1`.
   - Use the editor path in `AGENTS.md` when it has one. Otherwise the Unity Hub default is:
     - macOS: `/Applications/Unity/Hub/Editor/<version>/Unity.app/Contents/MacOS/Unity`
     - Windows: `C:\Program Files\Unity\Hub\Editor\<version>\Editor\Unity.exe`
   - Check that the file exists with `ls`. If that exact version is not installed, run nothing below; say which version is missing and ask. Never open the project with a different version.

   Below, `<UNITY>` stands for that full path. Keep the quotes around it.
2. Know the folders.

   | Path | What it is | Rule |
   | --- | --- | --- |
   | `Assets/` | Everything the game is made of, scripts included | Work here. Every file and folder has a `.meta` file beside it. |
   | `Packages/manifest.json` | The list of packages | Ask first. Add or change one line under `dependencies`. |
   | `Packages/packages-lock.json` | Lock file | Never edit by hand. |
   | `ProjectSettings/` | Project settings | Ask first. |
   | `Library/`, `Temp/`, `Obj/`, `Logs/`, `UserSettings/` | Generated or personal | Never edit, never commit. |
   | `*.csproj`, `*.sln` in the project root | Generated for code editors | Never edit. They are not what compiles the game. |

   - A folder named `Editor` holds editor-only code that is left out of builds. Code outside `Editor` folders must not use the `UnityEditor` namespace unless it is inside `#if UNITY_EDITOR`.
   - `find Assets -name "*.asmdef"` lists the assemblies. A script belongs to the `.asmdef` in its own folder or the nearest parent folder. Put a new script where its neighbours are.
   - Name a script file after the class in it: `PlayerController.cs` holds `class PlayerController`.
3. Follow these rules for each kind of file.

   | File | Rule |
   | --- | --- |
   | `.cs`, `.shader` | Text. Edit freely. |
   | `.asmdef` | Text. Ask before you change one. |
   | `.unity`, `.prefab`, `.asset`, `.mat`, `.anim`, `.controller` | Saved objects. Read and search them, but do not edit them by hand: objects point at each other with `fileID` and `guid` values. Change them in the Editor or with an editor tool, or ask the user to. |
   | `.meta` | Never write or edit by hand. It holds the `guid` other assets use to find this one, and the import settings. |
   | Images, audio, models | Binary. Never open or edit as text. |

4. Keep every asset and its `.meta` together.
   - New file made outside the Editor: it has no `.meta` yet. Step 5 makes Unity create it. Then keep both files. Never invent a `guid`.
   - Move or rename: `git mv <old> <new>` and `git mv <old>.meta <new>.meta`. The `guid` stays, so references keep working.
   - Delete: delete both.
   - Copy: copy the file only, never its `.meta`. Two assets must not share a `guid`.
   - Check with these two commands. After step 5 has run, both must print nothing:

     ```
     find Assets -mindepth 1 -not -name '*.meta' -not -name '.*' | while IFS= read -r f; do [ -e "$f.meta" ] || echo "no meta: $f"; done
     find Assets -name '*.meta' | while IFS= read -r m; do [ -e "${m%.meta}" ] || echo "orphan meta: $m"; done
     ```

     `no meta` before step 5 is normal for a file you just added. `orphan meta` means an asset was moved or deleted without its `.meta`: fix that now.
5. Compile check. This opens the project with no window, imports new files and compiles all scripts:

   ```
   "<UNITY>" -batchmode -nographics -quit -projectPath "$PWD" -logFile /tmp/unity-compile.log; echo "exit=$?"; grep -n -E "error CS[0-9]+" /tmp/unity-compile.log | head -n 20
   ```

   Passed only when `exit=0` was printed and `grep` printed nothing. A compile error looks like `Assets/Scripts/Player.cs(12,5): error CS0103: ...`: file, line and column come first. The first run on a fresh copy imports every asset and can take many minutes. Let it finish.
6. Run the tests. Do not add `-quit` here: with `-runTests` it stops the Editor before the tests finish.

   ```
   "<UNITY>" -batchmode -runTests -projectPath "$PWD" -testPlatform EditMode -testResults /tmp/unity-editmode.xml -logFile /tmp/unity-editmode.log; echo "exit=$?"
   grep -o -E '<test-run [^>]*' /tmp/unity-editmode.xml | grep -o -E '(result|total|passed|failed|skipped)="[^"]*"'
   ```

   - Play Mode tests: run it again with `-testPlatform PlayMode` and other file names.
   - One test: add `-testFilter "<Namespace.Class.Method>"`. One assembly: add `-assemblyNames "<assembly name>"`.
   - Passed only when the second command prints `result="Passed"`, `failed="0"` and a `total` above 0. Do not judge by the exit code alone.
   - Failed tests: `grep -n -A 4 -E '<test-case [^>]*result="Failed' /tmp/unity-editmode.xml | head -n 60`.
   - No `.xml` file was written: the run did not complete. Read `grep -n -E "error CS[0-9]+|Exception" /tmp/unity-editmode.log | head -n 20`.
   - `total="0"`: the project has no tests for that platform. Say so. To add the first test folder, ask the user to create it in the Editor (Assets > Create > Testing > Test Assembly Folder), then add test files there.
7. Build only when asked. Find the project's build method with `grep -rn "BuildPipeline.BuildPlayer" Assets --include="*.cs"`, then run it:

   ```
   "<UNITY>" -batchmode -quit -projectPath "$PWD" -buildTarget <target> -executeMethod <Class.Method> -logFile /tmp/unity-build.log; echo "exit=$?"
   ```

   `<target>` is a name such as `win64`, `osxuniversal` or `linux64`. Run one build target per command. If the project has no build method, say so and ask.

## Report

```
Editor: <version from ProjectVersion.txt>, found at <path>
Changed: <files, each asset listed with its .meta>
Checks
- compile: passed | failed (<first error line>) | not run (<reason>)
- tests <EditMode or PlayMode>: <total> run, <failed> failed | not run (<reason>)
Needs the Editor or a person: <scene or prefab changes you could not make, anything only visible on screen, or "nothing">
```

## Gotchas

- Batch mode cannot open a project that the Editor already has open. If the user has it open, do not run steps 5 to 7. Ask them to close it, or to tell you what the Console and the Test Runner window show.
- A `.cs` file you wrote is not compiled until Unity runs. Until step 5 has passed, say "not compiled yet", not "done".
- `-nographics` writes no log unless `-logFile` is given. Always pass `-logFile` and read the file.
- `-quit` can hide an error on screen. The log file still has it.
- In PowerShell, write the log to `$env:TEMP\unity.log`, use `"exit=$LASTEXITCODE"` in place of `echo "exit=$?"`, and `Select-String -Path <file> -Pattern "<text>"` in place of `grep`.
- A missing `.meta` is not harmless: Unity makes a new one with a new `guid`, and every scene, prefab and material that used the asset loses its link to it.
