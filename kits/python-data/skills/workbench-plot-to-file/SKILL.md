---
name: workbench-plot-to-file
description: "Charts and plots saved to files. Use for any figure or chart: render without a display, label axes with units, save to the results folder, check the file exists and report its path."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Any task that asks for a chart, plot, figure or graph.
- When a plotting script opens a window, hangs, or prints a warning about a display or a non-interactive backend.

You have no screen. A chart exists only when it is a file on disk. Never call `plt.show()`.

## Steps

1. Choose the library. Use the one the project's code already imports:

   ```
   grep -r -l -E "matplotlib|seaborn|plotly" --include="*.py" --include="*.ipynb" --exclude-dir=.venv . | head -n 5
   ```

   If it prints nothing, check which of them is installed, inside the project environment (see the `workbench-python-env` skill):

   ```
   python -c "import importlib.util as u; print([m for m in ('matplotlib', 'seaborn', 'plotly') if u.find_spec(m)])"
   ```

   Use matplotlib when it is in the list. An empty list: ask before you add a dependency.

2. Choose the output path.
   - Folder: the results folder in `AGENTS.md`, section `Reproducibility`. If that still says `TODO`, use `outputs/figures/` and say so in the report. Never the data folder.
   - Name: what the chart shows, lower case with underscores, for example `sales_by_month.png`.
   - Format: `.png` for reports and screens, `.svg` or `.pdf` when the user wants to scale or print it. matplotlib picks the format from the file extension.

3. Write the plot as a script that follows this pattern. Keep `matplotlib.use("Agg")` above the `pyplot` import.

   ```python
   from pathlib import Path

   import matplotlib

   matplotlib.use("Agg")
   import matplotlib.pyplot as plt

   out = Path("outputs/figures/sales_by_month.png")
   out.parent.mkdir(parents=True, exist_ok=True)

   months = ["2026-01", "2026-02", "2026-03"]
   sales = [12.5, 14.1, 9.8]
   assert len(sales) > 0, "no rows to plot"

   fig, ax = plt.subplots(figsize=(8, 5))
   ax.plot(months, sales, marker="o", label="Sales")
   ax.set_title("Sales by month")
   ax.set_xlabel("Month")
   ax.set_ylabel("Sales (thousand EUR)")
   ax.legend()
   fig.savefig(out, dpi=150, bbox_inches="tight")
   plt.close(fig)
   print("wrote", out, out.stat().st_size, "bytes,", len(sales), "points")
   ```

   Replace the two example lists with the real data, and keep the `assert` on the values you plot. Keep the rest.

4. Label it. Every chart has:
   - a title that says what is shown, and for which period or group;
   - an x label and a y label, each with its unit in brackets: `Time (s)`, `Revenue (EUR)`, `Share (%)`;
   - a legend when there is more than one series;
   - real names, not column codes: `Customers`, not `cust_cnt`.

5. Run the script inside the project environment and check the result:
   - The script exited with 0 and printed `wrote <path> <n> bytes, <n> points`. The number of points is the one you expect from the data.
   - `ls -l <path>` shows the file, with a size above zero.
   - The file check alone is not enough: a chart with no data in it is still a valid image of empty axes. That is what the `assert` is for.
   - If you can open images, look at the file. Check that labels do not overlap and no series is missing.

6. When a check fails:
   - `AssertionError: no rows to plot`: the data is empty, usually after a filter or a join. Print the row count after each step to find where it drops to zero.
   - A window opens, or the script never returns: `matplotlib.use("Agg")` is missing, or a `plt.show()` is left in. Add the first and remove the second.
   - Date or category labels overlap: add `fig.autofmt_xdate()` before saving, or widen the figure with `figsize=(12, 5)`.
   - After two failed attempts, stop and report the error line.

## Other libraries

| You have | Save with |
| --- | --- |
| pandas: `ax = df.plot(...)` | `ax.figure.savefig(out, dpi=150, bbox_inches="tight")` |
| seaborn, one chart: `ax = sns.lineplot(...)` | `ax.figure.savefig(out, dpi=150, bbox_inches="tight")` |
| seaborn, a grid: `g = sns.relplot(...)`, `catplot`, `displot` | `g.savefig(out, dpi=150)` |
| plotly: `fig = px.line(...)` | `fig.write_html("<name>.html")` |

- pandas and seaborn draw with matplotlib. Keep the `matplotlib.use("Agg")` lines at the top and set labels on the `ax` they return.
- plotly writes a PNG with `fig.write_image("<name>.png")` only when the `kaleido` package is installed. Without it the call fails with an error that names `kaleido`. Write HTML instead, and ask before you add a dependency.
- `fig.write_html` embeds the plotly library, so each file is several megabytes. `include_plotlyjs="cdn"` makes it a few kilobytes, but the file then needs a network connection to display.

## Report

```
Chart: <path> (<format>, <bytes> bytes)
Shows: <one sentence: what, for which period or group>
Data: <source file or query>, <n> rows plotted
Script: <path of the script that makes it>
Checked: <file exists | looked at the image | could not view the image>
```

## Gotchas

- On macOS the default matplotlib backend opens windows. Setting `Agg` in the script, or the environment variable `MPLBACKEND=Agg`, avoids that on every system.
- With `Agg`, `plt.show()` shows nothing and warns `FigureCanvasAgg is non-interactive, and thus cannot be shown`.
- Close every figure with `plt.close(fig)`. matplotlib warns once more than 20 figures are open, and they all stay in memory.
- `bbox_inches="tight"` crops the image to its content, so the size in pixels is not exactly `figsize` times `dpi`.
- Save the chart from a script, not only from a notebook cell, so that it can be made again from the same data.
- If the plot uses a random sample of the data, set the seed and state the sample size in the title or the report.
