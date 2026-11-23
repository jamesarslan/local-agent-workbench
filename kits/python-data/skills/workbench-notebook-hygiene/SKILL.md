---
name: workbench-notebook-hygiene
description: "Jupyter notebook checks. Use when creating, editing, running or committing a .ipynb file: it runs top to bottom, large outputs are cleared, shared code is in modules, no secrets in cells."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Before you create, edit, run or commit an `.ipynb` file.
- When asked to review or clean up a notebook.

First read `AGENTS.md`, section `Notebooks`. It says whether you may run notebooks and whether committed notebooks keep their outputs. Where it still says `TODO`: ask before running, and leave existing outputs as they are.

## Steps

1. Inspect the saved state. Save this as `/tmp/nb_check.py` (PowerShell: `$env:TEMP\nb_check.py`). It needs only the standard library and changes nothing.

   ```python
   import json, sys

   with open(sys.argv[1], encoding="utf-8") as f:
       nb = json.load(f)
   code = [c for c in nb["cells"] if c["cell_type"] == "code" and "".join(c["source"]).strip()]
   counts = [c.get("execution_count") for c in code]
   print("code cells:", len(code), "never run:", counts.count(None))
   print("saved run is in order 1..n:", counts == list(range(1, len(code) + 1)))
   for number, cell in enumerate(nb["cells"], 1):
       outputs = cell.get("outputs", [])
       size = len(json.dumps(outputs))
       errors = [o.get("ename") for o in outputs if o.get("output_type") == "error"]
       if size > 10000 or errors:
           print("cell", number, "output bytes:", size, "errors:", errors)
   ```

   Run `python3 /tmp/nb_check.py <notebook>` and read the result:

   | Result | Meaning | Do |
   | --- | --- | --- |
   | `never run` equals `code cells` | Outputs are cleared. | Nothing, when the project clears outputs. |
   | `in order 1..n: True` | The saved outputs come from one run, first cell to last. | Nothing. |
   | `in order 1..n: False`, some cells run | Cells were run out of order or run again after an edit. The saved outputs prove nothing. | Step 3. |
   | `cell N ... errors: [...]` | A saved cell output is an error. | Fix that cell, then step 3. |
   | `cell N output bytes:` above 10000 | A large output is stored in the file. | Step 4. |
   | `json.decoder.JSONDecodeError` | The file is not valid JSON, often after a text edit. | Do not repair it by hand. Ask to restore the last good version. |

2. Edit cells safely.
   - Use your notebook editing tool if you have one.
   - Otherwise change the file through Python, never with text search and replace. Cell numbers in step 1 start at 1; the index here starts at 0.

     ```python
     import json

     path = "<notebook>"
     with open(path, encoding="utf-8") as f:
         nb = json.load(f)
     cell = nb["cells"][<index>]
     cell["source"] = "<new cell text>"
     if cell["cell_type"] == "code":
         cell["outputs"], cell["execution_count"] = [], None
     with open(path, "w", encoding="utf-8") as f:
         json.dump(nb, f, indent=1, ensure_ascii=False)
         f.write("\n")
     ```

   - Then run the step 1 script again. It must not raise `JSONDecodeError`.
   - Order the cells so that the notebook reads top to bottom: imports and settings in the first code cell, no cell that needs a name defined further down.
   - Use paths relative to the notebook's folder. Find absolute paths with `grep -n -o -E '(/Users/|/home/|[A-Z]:\\\\)[^"]*' <notebook> | head -n 10` and replace them.

3. Run it top to bottom, only when `AGENTS.md` allows it or the user agreed. This executes into a copy; the notebook itself does not change. Put the run form of the project environment in front (see the `workbench-python-env` skill), for example `uv run`:

   ```
   jupyter nbconvert --to notebook --execute <notebook> --output-dir /tmp/nbcheck --output check > /tmp/nbrun.log 2>&1; echo "exit=$?"; tail -n 15 /tmp/nbrun.log
   ```

   - Pass: `exit=0`, and the log ends with `Writing <n> bytes to /tmp/nbcheck/check.ipynb`.
   - `CellExecutionError`: the log shows the failing cell and the exception. Fix that cell and run the command again.
   - `No such kernel named <x>`: the notebook asks for a kernel this environment does not have. Add `--ExecutePreprocessor.kernel_name=python3` and run again.
   - A cell may hang or run very long: add `--ExecutePreprocessor.timeout=600` to stop any cell after 600 seconds.
   - `jupyter: command not found`, `No module named jupyter` or `Jupyter command jupyter-nbconvert not found`: the environment lacks `nbconvert`. Running also needs `ipykernel`. Ask before you add them.
   - After two failed fixes of the same cell, stop and report the cell number and the exception.

   To store the fresh outputs in the notebook itself, replace `--output-dir /tmp/nbcheck --output check` with `--inplace`. Do that only when the project keeps outputs and you were asked to update them.

4. Deal with outputs according to the project's rule:
   - Outputs are cleared: `jupyter nbconvert --clear-output --inplace <notebook>`, with the same run form in front. Check: the step 1 script prints `never run` equal to `code cells`.
   - Outputs are kept: make large ones small at the source, then run again with `--inplace`. Show `df.head()` instead of a whole table, remove debug prints, and save big figures to files (see the `workbench-plot-to-file` skill).
   - No rule yet: change nothing. Report the cells and their sizes.

5. Move shared code into a module. A function or class that two notebooks use, or that you want to test, goes into a `.py` file in the project's package, and the notebook imports it. Put these two lines in the first code cell so that edits to the module load without a restart:

   ```
   %load_ext autoreload
   %autoreload 2
   ```

   Check: step 3 passes, so the import works in a fresh kernel.

6. Look for secrets, in code and in outputs:

   ```
   grep -n -i -E "(api[_-]?key|secret|token|passw(or)?d)[a-z_]*[\\\\\"' ]*[:=]" <notebook> | head -n 20
   ```

   - A line that reads the value from the environment, such as `os.environ["API_TOKEN"]`, is fine.
   - A line with the value written out is a leak. Replace it with `os.environ["<NAME>"]` and clear that cell's outputs.
   - If the file with the secret was already committed or shared, stop and tell the user. The secret must be replaced; deleting the line does not undo the leak.

7. Before a commit: run the step 1 script once more, then `git --no-pager diff --stat -- <notebook>`. Thousands of changed lines for a small edit mean outputs came along. Go back to step 4.

## Report

```
Notebook: <path>, <n> code cells
Saved state: <cleared | one run in order | out of order>
Run top to bottom: <passed | failed at cell <n>: <exception> | not run: <reason>>
Outputs: <kept | cleared | large outputs in cells <n, n>>
Secrets scan: <clean | found in cell <n>, replaced with an environment variable>
Changed: <cells edited, code moved to <module>>
```

## Gotchas

- `nbconvert --execute` runs the notebook with the notebook's own folder as the working directory. A relative path such as `data/x.csv` is looked up there, not in the project root.
- For the same reason, `import mymodule` finds a file beside the notebook, but not one in the project root, unless the project is installed in its environment.
- A notebook that works in a live session can still fail from the top: the kernel keeps variables from cells that were deleted or changed. Only a run from a fresh kernel (step 3) proves it.
- `--execute --inplace` overwrites every stored output of the notebook.
- The notebook may be set to another kernel than the project environment. Print `sys.executable` in a cell to see which Python ran it.
- A tool that runs code in the user's open Jupyter session changes the variables of that session. Ask before you use it on a notebook someone is working in.
