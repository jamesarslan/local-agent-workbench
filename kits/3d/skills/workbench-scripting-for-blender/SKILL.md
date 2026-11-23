---
name: workbench-scripting-for-blender
description: "Change a Blender scene with small, repeatable Python (bpy) scripts. Use when asked to add, move, rename, delete or modify objects, collections or modifiers in a .blend file."
license: MIT
compatibility: Blender. Script and commands checked with Blender 5.3.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Any change to a scene made by code: add, move, rename, parent, delete, add a modifier, set a keyframe.
- Not for looking only (`workbench-scene-inspect`), exporting (`workbench-3d-export`) or rendering (`workbench-headless-render`).

## Steps

1. Choose how the script runs. Use exactly one way for a file.
   - **Open window**: the user has the scene open and a connected tool runs Python inside it (a tool named like `execute_blender_code`). Send the script text through that tool. What it prints comes back.
   - **Background**: no such tool, or the file is not open. Save the script as a file and run it from the shell (step 5).
   - `blender` below means the Blender executable. Use the path in `AGENTS.md` if it gives one; otherwise `blender`; on macOS, if that is not found, `/Applications/Blender.app/Contents/MacOS/Blender`. If none works, ask for the path.
2. Inspect first. Run `workbench-scene-inspect`, or at least print the object names. Use only names you have seen printed.
3. Write one script for one task, in `scripts/` (or the folder `AGENTS.md` names). Start from this template. It saves a copy, makes the change so that a second run changes nothing more, prints the result and saves.

   ```python
   import os
   import time

   import bpy

   path = bpy.data.filepath
   if not path:
       raise RuntimeError("save the scene as a .blend file first")
   copy = path.removesuffix(".blend") + time.strftime(".before-%Y%m%d-%H%M%S.blend")
   if not os.path.exists(copy):
       bpy.ops.wm.save_as_mainfile(filepath=copy, copy=True)
   print("COPY", copy)

   scene = bpy.context.scene
   name = "Prop_Crate_01"
   ob = bpy.data.objects.get(name)
   if ob is None:
       mesh = bpy.data.meshes.new(name)
       mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
       mesh.update()
       ob = bpy.data.objects.new(name, mesh)
       scene.collection.objects.link(ob)
   ob.location = (0.0, 0.0, 1.0)

   bpy.context.view_layer.update()
   print("RESULT", ob.name, "| loc", [round(v, 3) for v in ob.matrix_world.translation], "| size", [round(v, 3) for v in ob.dimensions], "| objects", len(scene.objects))
   bpy.ops.wm.save_mainfile()
   ```

   - Keep the first block (the copy) and the `RESULT` line in every script. An existing copy is never overwritten.
   - **Open window**: delete the last line. Do not save the user's file; tell them the change is unsaved.
   - **Background**: keep the last line. Without it the change is lost when the process exits.
4. Write the change with data access, not with operators that depend on what is selected.

   | Task | Code |
   | --- | --- |
   | Find an object | `ob = bpy.data.objects.get("Name")` gives `None` when it is missing |
   | New empty | `ob = bpy.data.objects.new("Name", None)` then link it |
   | New camera | `ob = bpy.data.objects.new("Name", bpy.data.cameras.new("Name"))`, link it, then `scene.camera = ob` |
   | New light | `ob = bpy.data.objects.new("Name", bpy.data.lights.new("Name", type="SUN"))` then link it |
   | Link to the scene | `scene.collection.objects.link(ob)` |
   | New collection | `col = bpy.data.collections.new("Props")` then `scene.collection.children.link(col)` |
   | Move to a collection | `for old in ob.users_collection: old.objects.unlink(ob)` then `col.objects.link(ob)` |
   | Move, rotate, scale | `ob.location = (x, y, z)`, `ob.rotation_euler = (0.0, 0.0, math.radians(90))`, `ob.scale = (1.0, 1.0, 1.0)` |
   | Rename | `ob.name = "Prop_Box_01"`, then read `ob.name` back |
   | Add a modifier | `mod = ob.modifiers.new("Bevel", "BEVEL")` |
   | Parent, keep position | `child.parent = parent` then `child.matrix_parent_inverse = parent.matrix_world.inverted()` |
   | Keyframe | `ob.keyframe_insert("location", frame=1)` |
   | Delete an object | `bpy.data.objects.remove(ob, do_unlink=True)` |

   When only an operator can do the job, name the object it acts on:

   ```python
   with bpy.context.temp_override(active_object=ob, selected_editable_objects=[ob]):
       bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
   with bpy.context.temp_override(object=ob):
       bpy.ops.object.modifier_apply(modifier="Bevel")
   ```

5. Run it in the background. The order of the arguments matters: file, exit-code flag, script.

   ```
   blender -b <file.blend> --python-exit-code 1 --python scripts/<task>.py; echo "exit=$?"
   ```

   Values for the script go after `--`: `... --python scripts/<task>.py -- crate 2`. Read them with `sys.argv[sys.argv.index("--") + 1:]`.
6. Check the result. The change is done only when all of these are true:
   - `exit=0`, and no `Traceback` or `Error:` in the output.
   - The `RESULT` line shows the name, position and size the task asked for.
   - Background: the output has a `Saved "<file>"` line.
   - A second run prints the same `RESULT` line and the same object count.
7. When the check fails:
   - `KeyError ... not found`: the name is wrong. Inspect again and use the printed name.
   - The name ends in `.001`: an object with that name already existed. Look it up with `.get()` before you create it.
   - `poll() failed, context is incorrect` or `Context missing active object`: the operator needs a selection or a window. Use the table in step 4, or `temp_override`.
   - `Cannot apply to a multi user`: several objects share the mesh. Pass `isolate_users=True` to `transform_apply`.
   - Position or size is wrong: fix the script and run again. A background script that failed before its last line did not change the file.
   - Still wrong after two different fixes: stop and report what you tried.
8. To undo, copy the `.before-` file printed on the `COPY` line back over the original. Ask first. Delete old `.before-` files only when asked.

## Report

```
Script: <path>
Copy: <path from the COPY line>
Changed: <object names and what changed>
Check: <the RESULT line>
Saved: <yes, or "no - unsaved in the open window">
```

## Gotchas

- Without `--python-exit-code 1` a script that raises an error still exits 0.
- `blender -b --python s.py file.blend` runs the script before the file is loaded. The file goes first.
- In the background the active object and the selection are whatever was saved in the file. Never rely on `bpy.context.active_object` or `bpy.context.selected_objects`.
- Operators under `bpy.ops.view3d` need a 3D viewport and fail in the background.
- An object that is not linked to a collection is not in the scene and will not be saved with it.
- Rotation values are radians. Use `math.radians(degrees)`.
- `matrix_world` is stale after you set location, rotation, scale or a parent. Call `bpy.context.view_layer.update()` before you read it.
- Open window: if `bpy.context.mode` is not `"OBJECT"`, run `bpy.ops.object.mode_set(mode="OBJECT")` first. Mesh data written while the object is in Edit Mode is thrown away.
- Open window: never call `bpy.ops.wm.open_mainfile` or `bpy.ops.wm.read_factory_settings`. They discard the user's unsaved work.
- Applying rotation and scale changes the mesh data. Do it only when the task asks, and not on rigged or animated objects without asking.
