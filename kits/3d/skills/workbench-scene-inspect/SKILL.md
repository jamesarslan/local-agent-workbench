---
name: workbench-scene-inspect
description: "Inspect a 3D scene before changing it: list objects, collections, materials, modifiers, units and scale. Use first on any .blend file, or when asked what a scene contains."
license: MIT
compatibility: Blender. Script and commands checked with Blender 5.3.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Before any change, export or render of a `.blend` file you have not inspected in this session.
- When asked what is in a scene, or why an export or render looks wrong.
- Again after someone else changed the scene. Never work from object names you remember or guess.

## Steps

1. Find the executable. `blender` below means the Blender executable.
   - Use the path given in `AGENTS.md`, section `Commands`, if there is one.
   - Otherwise run `blender --version`. If the command is not found on macOS, use `/Applications/Blender.app/Contents/MacOS/Blender`.
   - If nothing works, ask for the path. Do not install anything.
2. Save this script as `scripts/inspect_scene.py` (or in the scripts folder `AGENTS.md` names). It only reads; it never saves the file.

   ```python
   import os
   import sys

   import bpy

   find = sys.argv[sys.argv.index("--") + 1].lower() if "--" in sys.argv[:-1] else ""
   LIMIT = 40
   scene = bpy.context.scene
   units, render = scene.unit_settings, scene.render
   print("FILE", bpy.data.filepath or "(not saved)", "| app", bpy.app.version_string)
   print("SCENE", scene.name, "| all scenes", [s.name for s in bpy.data.scenes])
   print("UNITS", units.system, "| scale_length", round(units.scale_length, 6))
   print("FRAMES", scene.frame_start, "to", scene.frame_end, "| fps", render.fps)
   print("RENDER", render.engine, "|", render.resolution_x, "x", render.resolution_y, "at", render.resolution_percentage, "%")
   print("CAMERA", scene.camera.name if scene.camera else "NONE")


   def tree(collection, depth=0):
       print("COLLECTION", "  " * depth + collection.name, "| objects", len(collection.objects))
       for child in collection.children:
           tree(child, depth + 1)


   tree(scene.collection)
   counts = {}
   for ob in scene.objects:
       counts[ob.type] = counts.get(ob.type, 0) + 1
   print("COUNT", len(scene.objects), "objects", counts)
   shown = [ob for ob in scene.objects if find in ob.name.lower()]
   for ob in shown[:LIMIT]:
       row = [ob.name, ob.type, "| parent", ob.parent.name if ob.parent else "-"]
       row += ["| loc", [round(v, 3) for v in ob.location]]
       row += ["| scale", [round(v, 3) for v in ob.scale]]
       row += ["| size", [round(v, 3) for v in ob.dimensions]]
       if ob.type == "MESH":
           row += ["| faces", len(ob.data.polygons)]
       row += ["| modifiers", [m.type for m in ob.modifiers]]
       row += ["| materials", [s.material.name if s.material else None for s in ob.material_slots]]
       if not ob.visible_get():
           row += ["| HIDDEN"]
       print("OBJECT", *row)
   if len(shown) > LIMIT:
       print("MORE", len(shown) - LIMIT, "objects not shown")
   for mat in bpy.data.materials[:LIMIT]:
       print("MATERIAL", mat.name, "| users", mat.users)
   for img in bpy.data.images[:LIMIT]:
       if img.source == "FILE":
           found = bool(img.packed_file) or os.path.exists(bpy.path.abspath(img.filepath))
           print("IMAGE", img.name, "|", img.filepath, "| found", found)
   for lib in bpy.data.libraries:
       print("LIBRARY", lib.filepath)
   ```

3. Run it. Put the file before the script: arguments run in the order given.

   ```
   blender -b <file.blend> --python-exit-code 1 --python scripts/inspect_scene.py; echo "exit=$?"
   ```

   - The scene is open in a Blender window and a connected tool runs Python inside it (a tool named like `execute_blender_code`): send the script text through that tool instead. What the script prints comes back.
   - A `MORE` line appeared: run again with part of a name after `--` to list only matching objects, for example `... --python scripts/inspect_scene.py -- crate`.
4. Check the run. It worked when `exit=0` and the output has a `FILE` line that shows the path you asked for and a `COUNT` line. If not:
   - `Cannot read file`: the path is wrong. List the folder and use the real file name.
   - `No module named '_bpy_types'`: `blender` is a symbolic link to the macOS application binary, which cannot run that way. Use the full path from step 1.
   - `FILE (not saved)`: the script ran before the file loaded, or no file was given. Fix the argument order.
   - A Python traceback: read its last line. If it names an attribute this version does not have, remove that one `print` and run again.
5. Read the output against this table and note every line that matches.

   | Output | Meaning | Do |
   | --- | --- | --- |
   | `scale_length` is not `1.0` | One unit is not one metre | Report it. Ask before any export. |
   | `CAMERA NONE` | The scene has no active camera | A render will fail. Report it. |
   | `scale` is not `[1.0, 1.0, 1.0]` | Unapplied scale | Report it. The export skill applies it in the exported copy. |
   | A negative number in `scale` | Mirrored object | Faces may point inward after export. Report it. |
   | Names like `Cube`, `Cube.001`, `Plane.002` | Default names | Report them. Rename only if asked or if `AGENTS.md` sets a pattern. |
   | `found False` on an `IMAGE` line | A texture file is missing | Report the path. Do not render or export until it is found. |
   | `LIBRARY` lines | Data linked from other `.blend` files | Those files must stay at the printed paths. |
   | `MATERIAL ... users 0` | Unused material | Leave it. Delete only if asked. |
   | `HIDDEN` | Hidden, or in a collection excluded from the view layer | It will not be exported by selection. |

6. Compare with `AGENTS.md`, section `Scene conventions` (units, naming, collection layout). List each difference. Do not fix differences unless the task asks for it.

## Report

```
Scene: <file> (application <version>)
Units: <system>, scale_length <value>
Objects: <count by type>
Camera: <name or NONE>
Findings:
- <line from the table that matched, with the object name>
Not checked: <what the output did not show, or "nothing">
```

Quote names exactly as printed. If there are no findings, write "Findings: none".

## Gotchas

- Scenes in this application are Z up. There is no up-axis setting to read; exporters convert the axis.
- `size` is the bounding box after modifiers, in scene units. `faces` counts the base mesh before modifiers.
- `loc` and `scale` are relative to the parent. An object with a parent can sit somewhere else in the world.
- The script prints at most 40 objects, 40 materials and 40 images. Use the name filter instead of raising the limit.
- A file may hold several scenes. The script reads the active one; the others are named on the `SCENE` line.
- Inspecting never needs a save. If you see `Saved` in the output, the wrong script ran.
