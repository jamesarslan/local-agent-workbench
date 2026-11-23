---
name: workbench-asset-provenance
description: "Record the source, author and licence of every imported 3D asset, texture or HDRI. Use before downloading or importing any model, texture or other file made outside this project."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Before you download, copy in or import a model, texture, HDRI, font, sound or `.blend` library that this project did not make.
- When an asset search, import or generation tool is about to bring a file into the scene. That is an import too.
- When asked which assets a project uses, or whether it can be published or sold.

## Steps

1. Find the record file. Use the one `AGENTS.md` names in `Assets and export`; otherwise `ASSETS.md` in the project folder. If it does not exist, create it with exactly this content:

   ```
   # Asset sources

   | File | Title | Author | Source URL | Licence | Retrieved | SHA-256 | Changes |
   | --- | --- | --- | --- | --- | --- | --- | --- |
   ```

2. Before importing, read the asset's own page and collect four facts. A search result, a preview or a repost is not the asset's page.
   - Title and author (person, studio or account name).
   - Source URL of that page.
   - Licence name and version, for example `CC BY 4.0`, and the URL of the licence text.
   - Any extra condition written on the page.
3. Decide with the first row that matches the licence you read.

   | Licence on the page | Decision |
   | --- | --- |
   | `CC0`, or an explicit public-domain dedication | Import. Record it. |
   | `CC BY` | Import. Credit is required: record the author and licence exactly. |
   | `CC BY-SA` | Ask first. Adaptations must be shared under the same terms. |
   | `CC BY-NC` (also `-NC-SA`, `-NC-ND`) | Ask first. Only non-commercial use is permitted. |
   | `CC BY-ND` | Ask first. Adaptations are not permitted. |
   | A shop, marketplace or site licence, "royalty free", "editorial use", "personal use" | Ask first. Quote the condition and give the URL of the terms. |
   | Made by an AI generation service | Ask first. Record the service, the date and the prompt; give the URL of the service terms. |
   | Made by the user or their team | Import. Record author `project` and licence `own work`. |
   | No licence stated, or you could not open the page | Do not import. Say which fact is missing. |

   "Free", "free download" and "no copyright" are not licences. Treat them as the last row.
4. Keep the downloaded file unchanged. Compute its hash:

   ```
   python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>
   ```

   If `python3` is not found, try `python`. If neither exists, write `not computed` in the SHA-256 column.
5. A `.glb` or `.gltf` file can carry its own copyright line. Print it:

   ```
   python3 -c "import json,struct,sys; d=open(sys.argv[1],'rb').read(); d=d[20:20+struct.unpack('<I',d[12:16])[0]] if d[:4]==b'glTF' else d; print(json.loads(d).get('asset'))" <file.glb>
   ```

   If the output has a `copyright` value that disagrees with the page, stop and ask.
6. Add one row per file to the record. Dates are `YYYY-MM-DD`. Write the path relative to the project folder. Example:

   ```
   | assets/crate/crate.glb | Wooden Crate | Jane Example | https://example.com/models/wooden-crate | CC BY 4.0 | 2026-01-15 | 4c1fe9ebda4e909a28b41f70c48dfeb5d89cbf59e0e7a39675bf832217b73453 | none |
   ```

   The hash is the full 64 characters from step 4. In `Changes` write what you altered (`none`, `rescaled`, `retextured`), and update it when you alter the asset later.
7. Import the asset only after its row is written.
8. Check the record:
   - Every row has no empty cell. A fact you could not find is written as `UNKNOWN`, never guessed.
   - Count the rows and the asset files you brought in; the numbers match.
   - Any row with `UNKNOWN` in `Licence` is listed in your report, and that file is not imported.
9. When a model that contains `CC BY` material is exported to glTF, carry the credit into the file by adding this argument to the export call: `export_copyright="CC BY 4.0, Jane Example"`.

## Audit an existing project

1. List the files a scene uses from outside itself. `blender` means the Blender executable (the path in `AGENTS.md`, or `blender`, or on macOS `/Applications/Blender.app/Contents/MacOS/Blender`).

   ```
   blender -b <file.blend> --python-expr "import bpy; [print('USES', i.filepath) for i in bpy.data.images if i.source=='FILE']; [print('USES', l.filepath) for l in bpy.data.libraries]; print('USES end')"
   ```

   Paths that start with `//` are relative to the `.blend` file. Only the line `USES end` means the scene uses no outside files.
2. List asset files in the project folder: models (`.glb`, `.gltf`, `.fbx`, `.obj`, `.usd`, `.usdz`, `.stl`), textures and HDRIs (`.png`, `.jpg`, `.exr`, `.hdr`).
3. For each file with no row, add a row with what is known and `UNKNOWN` for the rest. Do not delete the file. Report the list and ask where each came from.

## Report

```
Record: <path to the record file>
Recorded: <file> - <licence> - <author>
Waiting for an answer: <file> - <the condition you quoted>
Not imported: <file> - <the missing fact>
```

## Gotchas

- The licence can differ between assets on the same site. Read the page of the asset itself, every time.
- A model can contain textures by someone else under another licence. If the page lists them, each gets its own row.
- Never write a licence you did not read on the source page. Write `UNKNOWN` and ask.
- A licence name without a version is incomplete when the page gives one: `CC BY 4.0`, not `CC BY`.
- The hash identifies the file you downloaded. If the hash of a file no longer matches its row, the file was replaced or edited: update `Changes` or ask.
- Conditions such as NC, SA and ND can also cover exports and renders made from the asset. Ask before publishing those.
- Do not remove or edit a credit, licence file or readme that came with an asset.
