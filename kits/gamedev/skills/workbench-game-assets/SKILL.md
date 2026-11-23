---
name: workbench-game-assets
description: "Add, replace, move or delete game assets such as models, textures and audio. Use to record the licence, follow naming and the engine's import step, check size budgets and keep caches out of Git."
license: MIT
compatibility: Examples cover Godot 4, Unity and Unreal Engine 5. Uses git, python3 and the file command.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You are about to add, replace, move, rename or delete a model, texture, sound, font or other asset.
- You are about to commit and the change includes asset files.

## Steps

1. Read the project's rules: the sections `Asset rules`, `Performance budget` and `Project layout` in `AGENTS.md`. Find the engine: `project.godot` means Godot, `ProjectSettings/ProjectVersion.txt` means Unity, a `.uproject` file means Unreal Engine.
2. Settle the licence before the file enters the project. This applies to every asset that was not made for this project. An asset the user or their team made for it needs no licence check and no row.
   - Read the asset's own page or licence file and collect: title, author, the address of that page, and the licence name exactly as written there.
   - Decide with the first line that matches:
     - No licence stated, or only "free" or "free download": do not add the asset. Say which fact is missing.
     - Share-alike, non-commercial, no changes allowed, a shop or marketplace licence, or made by an AI generation service: ask first and quote the condition.
     - Public domain, `CC0` or `CC BY`: add it and record it. `CC BY` requires a credit; say so in the report.
3. Record it in `ASSETS.md` in the project folder, one row per file, before you import.
   - The file exists: add a row that fills its columns.
   - The file does not exist: create it with exactly this content, then add the row.

     ```
     # Asset sources

     | File | Title | Author | Source URL | Licence | Retrieved | SHA-256 | Changes |
     | --- | --- | --- | --- | --- | --- | --- | --- |
     ```

   - `File` is the path from the project folder. `Retrieved` is today's date as `YYYY-MM-DD`. `Changes` is `none` until you alter the file.
   - `SHA-256`: `python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>`
   - Leave no cell empty. Write `UNKNOWN` for a fact you could not find; never guess. A row with `UNKNOWN` in `Licence` means the file is not imported.
4. Choose the name and the folder.
   - Copy the neighbours: `git ls-files | grep -i -E "\.(png|jpg|webp|wav|ogg|mp3|glb|gltf|fbx|ttf)$" | head -n 40` shows how existing assets are named and where they live.
   - With no neighbours to copy: lower-case letters, digits and underscores only, no spaces, for example `player_idle.png`. In Unreal Engine use the type prefix: `T_` texture, `SM_` static mesh, `SK_` skeletal mesh, `M_` material, `MI_` material instance, `BP_` Blueprint.
   - Never create two names that differ only in upper and lower case.
5. Measure the asset and compare it with `Performance budget`.
   - File size: `ls -l <file>`.
   - Image size in pixels: `file <image>`. For a PNG the size follows `PNG image data,`. For a JPEG it is the `<width>x<height>` pair just before `components`.
   - Triangles in a `.glb` or `.gltf` model: run the script under `Count triangles` below.
   - Faces in an `.obj` model: `grep -c "^f " <model.obj>`. A face with four corners is two triangles.
   - Any other model format: use a connected 3D tool if this session has one. Otherwise ask the user for the triangle count.
   - Within budget: go on. Over budget: do not import; report the numbers and ask. Budget still `TODO`: report the numbers and ask for a limit. Never shrink, re-encode or simplify an asset unasked.
6. Bring the file in through the engine.

   | Engine | Do this | Check |
   | --- | --- | --- |
   | Godot | Copy the file into the project folder, then run the import step of the Godot project skill. Never put files in `.godot/`. | `<file>.import` exists beside the file. |
   | Unity | Copy the file under `Assets/`, then run the compile check of the Unity project skill. Never put files in `Library/`. | `<file>.meta` exists beside the file. |
   | Unreal Engine | Importing creates a `.uasset` and happens only in the editor. Use a connected editor tool, or ask the user to import the file and name the target folder under `Content/`. | The user or the tool confirms the new asset path. |

   Import settings such as compression, filtering and scale are stored in the `.import` or `.meta` file. Change them in the editor, not by editing that file.
7. Replace, move or delete with care.
   - Replace: keep the same file name and keep its `.import` or `.meta` file. References then stay valid. Update `SHA-256` and `Changes` in `ASSETS.md`.
   - Before you move or delete, find what uses the asset.
     - Godot: `git grep -n "<file name>" -- "*.tscn" "*.tres" "*.gd"`.
     - Unity: `grep -n "^guid:" <file>.meta`, then `grep -rl "<guid>" Assets ProjectSettings`.
     - Unreal Engine: assets are binary, so you cannot search them. Ask the user to check the references in the editor.
   - If something uses it, list the users and ask before you go on.
   - Move or delete the metadata file together with the asset, and update the `File` cell in `ASSETS.md`. In Unreal Engine, never move or delete `.uasset` or `.umap` files with file commands.
8. Check Git before you commit.
   - `git status --short`: every new asset appears together with its `.import` or `.meta` file, and `ASSETS.md` is listed when you added an outside asset.
   - No engine cache is tracked. This must print nothing:

     ```
     git ls-files -- .godot Library Temp Obj UserSettings Binaries Intermediate Saved DerivedDataCache | head -n 5
     ```

     If it prints paths, do not remove them yourself. Report them. The fix is a `.gitignore` entry and `git rm -r --cached <folder>`, which the user must approve.
   - `git check-ignore -v <cache folder>/x` prints the ignore rule for that folder. No output means the folder is not ignored: say so.
   - Large files: `git check-attr filter -- <file>` prints `filter: lfs` when Git LFS handles the file. If `.gitattributes` uses `filter=lfs` for other file types but not for this one, ask before you run `git lfs track "*.<ending>"`.

## Count triangles

Run this from a POSIX shell. It reads the file and changes nothing.

```
python3 - <model file> <<'EOF'
import json, struct, sys
data = open(sys.argv[1], "rb").read()
if data[:4] == b"glTF":
    size = struct.unpack("<I", data[12:16])[0]
    data = data[20:20 + size]
doc = json.loads(data)
tris = 0
for mesh in doc.get("meshes", []):
    for prim in mesh["primitives"]:
        if prim.get("mode", 4) == 4:
            key = prim.get("indices", prim["attributes"]["POSITION"])
            tris += doc["accessors"][key]["count"] // 3
print("triangles:", tris, "materials:", len(doc.get("materials", [])), "images:", len(doc.get("images", [])))
EOF
```

It prints one line such as `triangles: 236 materials: 1 images: 0`. A mesh that the scene shows several times is counted once. In PowerShell, save the lines between the two `EOF` markers to a file outside the project and run `python <that file> <model file>`.

## Report

```
Asset: <path> (<file size>, <pixels or triangles>)
Budget: within | over (<limit> allowed) | no budget set
Licence: <name>, by <author>, from <address>; conditions: <list or "none">
Recorded in ASSETS.md: yes | not needed (made for this project)
Imported: yes (<metadata file>) | waiting for the editor (<what the user must do>)
Git: <files to commit>; caches tracked: none | <paths>
```

## Gotchas

- The engine converts and compresses an asset itself when it imports it. Keep the source file as it came; do not compress it first.
- In Unity every folder under `Assets/` has a `.meta` file too. A new folder gets one on the next import; commit it.
- In Godot the `.import` file belongs in Git and the `.godot/` folder does not.
- An asset file copied by hand from another Unreal Engine project does not bring the assets it depends on. Moving assets between projects is done in the editor.
- Do not download assets from the internet unless the user asked for that exact asset.
