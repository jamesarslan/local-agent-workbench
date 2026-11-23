---
name: workbench-headless-render
description: "Render images or animation frames from the command line with no window. Use when asked to render a .blend file, make a preview image or batch-render frames."
license: MIT
compatibility: Blender. Commands checked with Blender 5.3 on macOS.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- A `.blend` file must become an image or a frame sequence and nobody will press Render in a window.
- A render came out black, in the wrong folder or at the wrong size.

## Steps

1. Find the executable. `blender` below means the Blender executable: the path in `AGENTS.md` if it gives one, otherwise `blender`, or on macOS `/Applications/Blender.app/Contents/MacOS/Blender`. If none works, ask for the path.
2. Read what the file is set to. This changes nothing.

   ```
   blender -b <file.blend> --python-expr "import bpy; s=bpy.context.scene; r=s.render; print('RENDERINFO', r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage, s.camera and s.camera.name, s.frame_start, s.frame_end, s.cycles.samples, s.eevee.taa_render_samples)"
   ```

   The line reads: engine, width, height, percentage, camera, first frame, last frame, Cycles samples, EEVEE samples. If the camera is `None`, stop: the scene has no active camera. Ask which camera to use.
3. Fix five values before you render. Take each from the request, then from `AGENTS.md`, then from the `RENDERINFO` line. Do not invent them.
   - Engine: `CYCLES`, `BLENDER_EEVEE` or `BLENDER_WORKBENCH`. `blender -b -E help` lists the names this version accepts.
   - Width and height in pixels.
   - Samples.
   - Frame, or first and last frame.
   - Output path. Start it with `//` to mean the folder of the `.blend` file. `####` becomes the frame number.
4. Render a small preview first: a quarter of the size and few samples.

   ```
   blender -b <file.blend> -E CYCLES -o //renders/preview_#### -F PNG -x 1 --python-expr "import bpy; s=bpy.context.scene; s.render.resolution_percentage=25; s.cycles.samples=16" -f 1 > /tmp/render.log 2>&1; echo "exit=$?"; grep -E "Saved:|Error|ERROR" /tmp/render.log
   ```

   Keep this order: file, `-E`, `-o`, `-F`, `-x`, `--python-expr`, and `-f` last. Blender runs the arguments in order and renders at `-f`.
5. Check the preview (step 7). Then render at full size with every value written out. Replace the numbers below with the values from step 3.

   ```
   blender -b <file.blend> -E CYCLES -o //renders/shot_#### -F PNG -x 1 --python-expr "import bpy; s=bpy.context.scene; r=s.render; r.resolution_x=1920; r.resolution_y=1080; r.resolution_percentage=100; s.cycles.samples=128" -f 1 > /tmp/render.log 2>&1; echo "exit=$?"; grep -E "Saved:|Error|ERROR" /tmp/render.log
   ```

   - EEVEE: use `-E BLENDER_EEVEE` and `s.eevee.taa_render_samples=64` in place of `s.cycles.samples=128`.
   - Several frames: replace `-f 1` with `-s 1 -e 48 -a` (first frame, last frame, render the range). One file is written per frame.
   - Other formats for `-F`: `JPEG`, `OPEN_EXR`, `TIFF`. `-x 1` adds the file extension.
6. A long render can outlast your command time limit. Estimate first: preview seconds times 16 for the size, times the ratio of the sample counts, times the number of frames. If that is more than your limit, run the command as a background job and read `/tmp/render.log` until a `Saved:` line appears for the last frame.
7. Check the result. The render passed only when all of these are true:
   - `exit=0`.
   - One `Saved: '<path>'` line for each frame, and no `Error` line.
   - Each file exists and is not empty: `ls -l <path>`.
   - The image has the size you asked for and is not black:

     ```
     blender -b --factory-startup --python-exit-code 1 --python-expr "import bpy,sys; im=bpy.data.images.load(sys.argv[-1]); w,h=im.size; im.scale(64,64); px=im.pixels[:]; rgb=[v for i,v in enumerate(px) if i%4<3]; print('IMAGE', w, h, 'mean_rgb', round(sum(rgb)/len(rgb),3))" -- <path to the image>
     ```

     `mean_rgb` runs from 0 (black) to 1 (white). Below 0.01 the frame is black.
   - If you can open images, look at the preview as well and say what it shows.
8. When the check fails:

   | What you see | Cause | Do |
   | --- | --- | --- |
   | `No camera found in scene`, no `Saved:` line, `exit=0` | No active camera | Ask which camera to use. Do not add one unasked. |
   | `engine not found` | Wrong engine name | Run `blender -b -E help` and use a listed name. |
   | `Found no Cycles device of the specified type` | The `--cycles-device` value does not exist here | Use `-- --cycles-device CPU`, or remove the option. |
   | `Saved:` shows another folder | `-o` is missing or comes after `-f` | Fix the order from step 4. |
   | `Cannot read file` | Wrong `.blend` path | List the folder and use the real name. |
   | `mean_rgb` below 0.01 | No light, or the camera sees nothing | Inspect lights and camera with `workbench-scene-inspect` and report. |
   | No `Saved:` line after minutes at `Loading render kernels` | First render on a graphics device compiles kernels | Wait; it happens once. Or add `-- --cycles-device CPU`. |
   | EEVEE fails to start on a machine without a display or graphics device | EEVEE draws with the graphics device | Use `-E CYCLES` with `-- --cycles-device CPU`. |

   After two different failed fixes, stop and report the last 20 lines of `/tmp/render.log`.

## Report

```
Render: <output path(s)>
Engine: <name>, <width> x <height>, <samples> samples, frame(s) <n>
Check: Saved line present, <bytes> bytes, IMAGE <w> <h> mean_rgb <value>
Time: <the Time: value from the log>
Not checked: <for example "did not look at the image">
```

## Gotchas

- The exit code is 0 even when nothing was rendered because the camera is missing. Always look for the `Saved:` line.
- Without `-o` the file goes to the path saved in the scene, which is often a temporary folder.
- `-o` placed before the `.blend` file is overwritten when the file loads.
- Settings changed with `--python-expr` last for this run only. The `.blend` file is not saved, so the source keeps its own settings.
- Cycles options go at the very end after a lone `--`: `... -f 1 -- --cycles-device CPU`. Device names are listed under "Cycles Render Options" in `blender --help`.
- The render log is long. Send it to a file and `grep` it; do not print it.
- Do not render over an existing file without saying so. Change the name in `-o` to keep the old image.
- `/tmp/render.log` and `echo "exit=$?"` are for sh, bash and zsh. In another shell, write the log to a temporary file and print the exit code that shell's way.
