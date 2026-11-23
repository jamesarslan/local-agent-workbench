---
name: workbench-3d-export
description: "Export 3D models from a .blend file to glTF, FBX, USD or STL with checked scale and axes. Use when asked to export, convert or hand off a model for a game engine, the web or printing."
license: MIT
compatibility: Blender. Scripts and commands checked with Blender 5.3.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- A model or scene must leave the `.blend` file as `.glb`, `.gltf`, `.fbx`, `.usd`, `.usda`, `.usdc`, `.usdz` or `.stl`.
- An earlier export arrived with the wrong size, rotation or missing parts.

## Steps

1. Choose the format. Use the first rule that applies.
   1. The request names a format or file extension: use it.
   2. `AGENTS.md`, section `Assets and export`, names a format: use it. A line that still says `TODO` names nothing.
   3. The target asks for FBX: `.fbx`.
   4. The target is a USD pipeline: `.usdc`, or `.usdz` when one packed file is wanted.
   5. The target is a 3D printer or slicer: `.stl`.
   6. Anything else, including the web and real-time viewers: `.glb`. Say that you chose it.
2. Inspect the scene with `workbench-scene-inspect`. Write down the exact names of the objects to export.
   - `scale_length` is not `1.0`: stop and ask what real size is meant. FBX applies the unit scale and the other formats ignore it, so the same scene exports at different sizes.
   - An object to export is `HIDDEN`: ask whether to show it or leave it out.
3. Save this script as `scripts/export_asset.py` (or in the scripts folder `AGENTS.md` names). It selects the named objects, applies rotation and scale to plain meshes whose scale is not 1, exports by file extension and prints the size. It never saves the `.blend`, so the source keeps its transforms.

   ```python
   import os
   import sys

   import bpy
   from mathutils import Vector

   args = sys.argv[sys.argv.index("--") + 1:]
   out, names = os.path.abspath(args[0]), args[1:]
   layer = bpy.context.view_layer
   chosen = [bpy.data.objects[n] for n in names]
   if not names:
       chosen = [o for o in layer.objects if o.type == "MESH" and o.visible_get()]
   if not chosen:
       raise RuntimeError("no object to export")
   for ob in layer.objects:
       ob.select_set(ob in chosen)
   missed = [o.name for o in chosen if o.name not in layer.objects or not o.select_get()]
   if missed:
       raise RuntimeError(f"hidden or not in the view layer: {missed}")

   for ob in chosen:
       if all(abs(s - 1.0) < 1e-4 for s in ob.scale):
           continue
       if ob.type != "MESH" or ob.animation_data or ob.find_armature():
           print("NOT APPLIED", ob.name, "scale", [round(s, 4) for s in ob.scale])
           continue
       with bpy.context.temp_override(active_object=ob, selected_editable_objects=[ob]):
           bpy.ops.object.transform_apply(location=False, rotation=True, scale=True, isolate_users=True)
       print("APPLIED rotation and scale:", ob.name)

   ext = os.path.splitext(out)[1].lower()
   os.makedirs(os.path.dirname(out), exist_ok=True)
   if ext in (".glb", ".gltf"):
       kind = "GLB" if ext == ".glb" else "GLTF_SEPARATE"
       bpy.ops.export_scene.gltf(filepath=out, export_format=kind, use_selection=True, export_apply=True, export_yup=True)
   elif ext == ".fbx":
       bpy.ops.export_scene.fbx(filepath=out, use_selection=True, axis_forward="-Z", axis_up="Y")
   elif ext in (".usd", ".usda", ".usdc", ".usdz"):
       bpy.ops.wm.usd_export(filepath=out, selected_objects_only=True)
   elif ext == ".stl":
       bpy.ops.wm.stl_export(filepath=out, export_selected_objects=True)
   else:
       raise RuntimeError("unsupported extension " + ext)

   layer.update()
   meshes = [o for o in chosen if o.type == "MESH"]
   corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
   size = [round(max(c[i] for c in corners) - min(c[i] for c in corners), 4) for i in range(3)]
   print("EXPORTED", out, "| bytes", os.path.getsize(out), "| meshes", len(meshes), "| size_xyz", size)
   ```

4. Export. `blender` means the Blender executable: the path in `AGENTS.md` if it gives one, otherwise `blender`, or on macOS `/Applications/Blender.app/Contents/MacOS/Blender`. Give the output path first, then the object names. With no names, every visible mesh is exported.

   ```
   blender -b <file.blend> --python-exit-code 1 --python scripts/export_asset.py -- exports/crate.glb Prop_Crate_01 Prop_Lid_01; echo "exit=$?"
   ```

   Write to the export folder `AGENTS.md` names, never beside or over a source file. Keep the `EXPORTED` line.
5. Save this script as `scripts/check_export.py`. It opens the exported file in an empty scene and prints what it holds.

   ```python
   import os
   import sys

   import bpy
   from mathutils import Vector

   path = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
   ext = os.path.splitext(path)[1].lower()
   bpy.ops.wm.read_factory_settings(use_empty=True)
   if ext in (".glb", ".gltf"):
       bpy.ops.import_scene.gltf(filepath=path)
   elif ext == ".fbx":
       bpy.ops.import_scene.fbx(filepath=path)
   elif ext in (".usd", ".usda", ".usdc", ".usdz"):
       bpy.ops.wm.usd_import(filepath=path)
   elif ext == ".stl":
       bpy.ops.wm.stl_import(filepath=path)
   else:
       raise RuntimeError("unsupported extension " + ext)

   bpy.context.view_layer.update()
   meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
   if not meshes:
       raise RuntimeError("the file holds no mesh")
   corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
   size = [round(max(c[i] for c in corners) - min(c[i] for c in corners), 4) for i in range(3)]
   print("IMPORTED", path, "| meshes", len(meshes), "| size_xyz", size)
   for ob in meshes:
       slots = [s.material.name if s.material else None for s in ob.material_slots]
       print("MESH", ob.name, "| faces", len(ob.data.polygons), "| materials", slots)
   ```

6. Verify in a fresh process, with no `.blend` on the command line:

   ```
   blender -b --factory-startup --python-exit-code 1 --python scripts/check_export.py -- exports/crate.glb; echo "exit=$?"
   ```

   The export passed only when all of these are true:
   - `exit=0` for both commands.
   - `meshes` on the `IMPORTED` line equals `meshes` on the `EXPORTED` line. STL is the exception: it always comes back as one mesh.
   - Each of the three `size_xyz` numbers is within 1% of the `EXPORTED` line.
   - The `MESH` lines list the materials you expect. STL carries no materials.
7. When the check fails:

   | What you see | Cause | Do |
   | --- | --- | --- |
   | Size is 100 times or 0.01 times the source | The scene unit scale is not 1 | Go back to step 2 and ask. |
   | `hidden or not in the view layer` | The object cannot be selected | Ask whether to show it; do not unhide it yourself. |
   | `KeyError ... not found` | A name is wrong | Inspect again and use the printed name. |
   | `NOT APPLIED` line | Rigged, animated or not a mesh; left as it was | Report it. Do not apply by hand without asking. |
   | Fewer meshes than expected | Names left out, or objects that are not meshes | Compare the names with the inspect output. |
   | `unsupported extension` | The script does not handle that format | Ask for one of the formats in step 1. |

   After two different failed fixes, stop and report what you tried.

## What each format writes

Measured with the script above.

| Format | Up axis in the file | Scene unit scale | Modifiers | Other files written |
| --- | --- | --- | --- | --- |
| `.glb` | Y (`export_yup=True`) | ignored | applied (`export_apply=True`) | none |
| `.gltf` | Y | ignored | applied | a `.bin` file and the texture images beside it |
| `.fbx` | Y, forward -Z | applied | applied | none; textures stay as file references |
| `.usd`, `.usda`, `.usdc` | Z (`upAxis = "Z"`) | ignored (`metersPerUnit = 1`) | applied | a `textures` folder beside it |
| `.usdz` | Z | ignored | applied | none; one packed file |
| `.stl` | Z | ignored | applied | none |

## Report

```
Export: <path> (<bytes> bytes)
Objects: <names>
Rotation and scale applied in the export only: <names, or none>
Check: source size <x y z>, re-imported size <x y z>, meshes <n> of <n>
Not tested: opening the file in <target program>
```

## Gotchas

- The check re-imports with the same application. It proves the file opens and that count and size survive. It does not prove how another program reads the axes; say so in the report.
- `export_apply=True` applies modifiers but drops shape keys. For a model with shape keys set it to `False` and report that modifiers were not applied.
- `size_xyz` on both lines is measured in the Z-up scene after import, so the numbers compare directly even though the file itself may be Y up.
- glTF triangulates, so `faces` after re-import is higher than in the source. Compare size and mesh count, not face count.
- Names on the `MESH` lines can differ from the source after USD and STL. Compare count and size, not names.
- A relative output path is resolved from the folder the command runs in, not from the `.blend` file.
- glTF and FBX include animation by default. The USD call above does not; add `export_animation=True` when animation is wanted.
- Engine-specific import settings belong in `AGENTS.md`. Do not invent axis or scale options for a program you cannot test.
