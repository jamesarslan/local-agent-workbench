---
name: workbench-game-project-for-unreal
description: "Work in an Unreal Engine project. Use in a folder with a .uproject file before changing C++, config or content: Source, Config and Content layout, binary assets, build and test with engine tools."
license: MIT
compatibility: Unreal Engine 5. The build and test steps need the engine and its C++ build tools on this computer. Commands follow the engine documentation; they were not run when this skill was written.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- The folder has a `<Project>.uproject` file and you are about to change C++ code, config files or content.
- You need to compile the project or run its automation tests from the command line.

## Steps

1. Read the project file and find the engine.
   - `ls *.uproject`, then `grep -n -E '"EngineAssociation"|"Name"' *.uproject`. `EngineAssociation` is the engine version, for example `"5.5"`, or an identifier in braces for an engine built from source. The `"Name"` lines under `Modules` are the C++ modules.
   - The engine folder is the one that contains `Engine/`. Take its path from `AGENTS.md`. If it is not there, ask. Do not search the disk and do not use an engine of another version.
   - Inside the engine folder you need three programs. Check each with `ls` before you use it:

     | | Windows | macOS | Linux |
     | --- | --- | --- | --- |
     | Build script | `Engine\Build\BatchFiles\Build.bat` | `Engine/Build/BatchFiles/Mac/Build.sh` | `Engine/Build/BatchFiles/Linux/Build.sh` |
     | Editor for command-line runs | `Engine\Binaries\Win64\UnrealEditor-Cmd.exe` | `Engine/Binaries/Mac/UnrealEditor-Cmd` | `Engine/Binaries/Linux/UnrealEditor` |
     | Automation tool | `Engine\Build\BatchFiles\RunUAT.bat` | `Engine/Build/BatchFiles/RunUAT.sh` | `Engine/Build/BatchFiles/RunUAT.sh` |

   Below, `<UE>` is the engine folder, `<EDITOR>` the full path of the editor from the table, and `<Project>` the name of the `.uproject` file without its ending.
2. Know the folders.

   | Path | What it is | Rule |
   | --- | --- | --- |
   | `Source/<Module>/` | C++ code, usually `Public/` for headers and `Private/` for `.cpp`, plus `<Module>.Build.cs` | Text. Edit. |
   | `Source/*.Target.cs` | Build targets. The file name without `.Target.cs` is the target name | Ask first. |
   | `Config/Default*.ini` | Project settings, such as `DefaultEngine.ini` and `DefaultGame.ini` | Text. Ask first, change one key. |
   | `Content/` | `.uasset` and `.umap` files: meshes, materials, Blueprints, levels | Binary. See step 3. |
   | `Plugins/<Name>/` | A plugin with its own `.uplugin`, `Source/` and `Content/` | Same rules as above. |
   | `Binaries/`, `Intermediate/`, `Saved/`, `DerivedDataCache/` | Generated | Never edit, never commit. Logs are in `Saved/Logs/`. |

   - In code and config, `/Game/Maps/Start` means `Content/Maps/Start.umap` or `.uasset`.
   - No `Source/` folder means a Blueprint-only project: all game logic is inside binary assets. Steps 4 and 5 do not apply, and you cannot change that logic with text edits. Say so, and ask how the user wants the change made.
3. Never touch `.uasset` and `.umap` files with file commands. Do not open them as text, and do not rename, move, copy or delete them with `mv`, `cp`, `rm` or `git mv`. Other assets refer to them by path, and only the editor updates those references. Make such a change with an editor tool if one is connected to this session. Otherwise write down exactly what should change and ask the user to do it in the editor.
4. Change C++ the way the module already does it.
   - New class: copy the shape of an existing class in the same module. Keep `#include "<ClassName>.generated.h"` as the last include, keep the `GENERATED_BODY()` line, and keep the `<MODULE>_API` word before the class name if neighbours have it.
   - An include from another module is not found, or the linker reports an unresolved symbol: add that module's name to `PublicDependencyModuleNames` or `PrivateDependencyModuleNames` in `<Module>.Build.cs`.
   - Renaming a class, property or function that Blueprints or levels use breaks those assets. Ask first.
5. Build the editor target. `ls Source/*.Target.cs` shows the target names; use the one ending in `Editor`. The example is for macOS. On another system take the build script from step 1 and write `Win64` or `Linux` in place of `Mac`.

   ```
   "<UE>/Engine/Build/BatchFiles/Mac/Build.sh" -Target="<Project>Editor Mac Development" -Project="$PWD/<Project>.uproject" > /tmp/ue-build.log 2>&1; echo "exit=$?"; grep -n -E "error( |:)|Result:" /tmp/ue-build.log | head -n 30
   ```

   Passed only when `exit=0` was printed, `grep` shows no compiler or linker error, and the `Result:` line, if the log has one, says `Result: Succeeded`. A failed build can end with `Result: Failed (<reason>)`. Fix the first error and run the same command again.
6. Run the automation tests. In a C++ project, build first (step 5). Find the test names: `grep -rn -E "IMPLEMENT_(SIMPLE|COMPLEX)_AUTOMATION_TEST|DEFINE_SPEC" Source Plugins 2>/dev/null | head -n 40`. The text in quotes, such as `"MyGame.Inventory.AddItem"`, is the name.

   ```
   "<EDITOR>" "$PWD/<Project>.uproject" -ExecCmds="Automation RunTest <filter>;Quit" -unattended -nullrhi -nosplash -abslog=/tmp/ue-tests.log; echo "exit=$?"
   grep -n -E "Test Completed|TEST COMPLETE|No automation tests matched" /tmp/ue-tests.log | tail -n 40
   ```

   - `<filter>` is a test name or its beginning: `MyGame.Inventory` runs every test below it. Join several with `+`.
   - Passed only when the log has `**** TEST COMPLETE. EXIT CODE: 0 ****`, at least one `Result={Success}` line, and no `Result={Fail}` line.
   - `No automation tests matched`: the filter is wrong. Compare it with the names from the `grep` above.
   - No test names found: the project has no automation tests. Say so. New tests go in `Source/<Module>/Private/Tests/`.
7. Package a build only when asked: `"<UE>/Engine/Build/BatchFiles/RunUAT.sh" BuildCookRun -Build -Cook -Stage -Package -Project="$PWD/<Project>.uproject"`. Add `-Help` after `BuildCookRun` to list its options.

## Report

```
Engine: <EngineAssociation>, found at <UE>
Changed: <files>
Checks
- build <target> <platform> Development: passed | failed (<first error line>) | not run (<reason>)
- tests <filter>: <number> passed, <number> failed | not run (<reason>)
Needs the editor or a person: <asset, Blueprint or level changes you could not make, anything only visible on screen, or "nothing">
```

## Gotchas

- A command answers that it does not know an argument, or the log has none of the lines a step names: stop and report the exact message. Do not try other spellings.
- `Unable to build while Live Coding is active` in the build log means the editor is running with this project. Do not close it yourself. Ask the user to close it or to compile from the editor.
- The build finds source files by itself. After adding a `.cpp` or `.h` you do not need to regenerate project files to compile. Never edit generated `.sln`, `.vcxproj` or Xcode files.
- On Windows use `Build.bat` and `RunUAT.bat` with the same arguments, and give `-abslog` a full Windows path. In PowerShell, write `"exit=$LASTEXITCODE"` in place of `echo "exit=$?"` and `Select-String -Path <file> -Pattern "<text>"` in place of `grep`.
- `-nullrhi` turns rendering off. A test that compares screenshots cannot pass that way; report it as not run.
- `-unattended` suppresses dialogs. Without it the editor can wait for a click that never comes.
- The first build compiles every module of the project and can take a long time. Let it finish; do not start a second build beside it.
- Settings you change belong in `Config/`. Files under `Saved/Config/` are generated and are not part of the project.
