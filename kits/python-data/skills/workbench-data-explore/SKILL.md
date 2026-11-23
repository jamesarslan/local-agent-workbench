---
name: workbench-data-explore
description: "Explore or profile a dataset (CSV, Parquet, DuckDB file). Use for a first look: report shape, column types, missing values and duplicates from a sample, without changing the source."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- First look at a data file or table, before analysis, cleaning, joining or modelling.
- Questions such as "what is in this file", "is this data clean", "which column is the key".

Rules that hold for every step:

- Read only. Run no statement and call no function that writes into the data folder.
- Never print more than 20 rows. Print one line per column instead.
- Say for every number whether it comes from the whole file or from a sample.

## Steps

1. Measure the file: `wc -c <file>`. Note the number of bytes.
   - `.csv`, `.tsv` or other delimited text: first do steps 1 to 3 of the `workbench-csv` skill. You need its encoding, delimiter and record count.
   - Above 500 MB: profile a sample of 100,000 rows, never the whole file in pandas.

2. Pick the engine. Take the first that is available:
   1. A DuckDB tool that runs SQL (the DuckDB server in this kit names it `execute_query`): use the SQL in step 3. Give files as absolute paths.
   2. A library in the project environment. This prints the ones that are installed:

      ```
      python -c "import importlib.util as u; print([m for m in ('duckdb', 'polars', 'pandas', 'pyarrow') if u.find_spec(m)])"
      ```

      Run it inside the environment (see the `workbench-python-env` skill). Use `duckdb` (step 3), else `polars` (step 4), else `pandas` (step 5).
   3. Nothing is installed: for delimited text, the `workbench-csv` script gives records and columns; report that much. For other formats, ask before you add a dependency.

3. DuckDB. Replace `F` with the file path. In a SQL tool, run the statements one at a time.

   ```sql
   SELECT count(*) FROM 'F';
   DESCRIBE SELECT * FROM 'F';
   SELECT * FROM 'F' LIMIT 5;
   SUMMARIZE SELECT * FROM 'F';
   SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT * FROM 'F')) AS duplicate_rows FROM 'F';
   ```

   - `SUMMARIZE` gives one row per column: `column_type`, `min`, `max`, `approx_unique`, `avg`, `std`, `q25`, `q50`, `q75`, `count`, `null_percentage`.
   - Large file: profile a random sample with `SUMMARIZE SELECT * FROM 'F' USING SAMPLE 100000 ROWS;`
   - Delimited text that DuckDB reads wrongly: replace `'F'` with `read_csv('F', delim = ';', header = true, all_varchar = true)`, using the delimiter you found.
   - A candidate key column: `SELECT <column>, count(*) AS n FROM 'F' GROUP BY <column> HAVING n > 1 ORDER BY n DESC LIMIT 10;` No rows means the column is unique.
   - Most common values of a column: `SELECT <column>, count(*) AS n FROM 'F' GROUP BY <column> ORDER BY n DESC LIMIT 10;`
   - A DuckDB database file. In a SQL tool: `ATTACH '<file>' AS src (READ_ONLY);` then `SHOW ALL TABLES;` then `SUMMARIZE src.<table>;`

   In Python:

   ```python
   import duckdb

   con = duckdb.connect()  # in memory; for a database file: duckdb.connect("<file>", read_only=True)
   for row in con.sql("SUMMARIZE SELECT * FROM '<file>'").fetchall():
       print(row)
   ```

4. Polars. It scans the file lazily, so the row count costs little memory.

   ```python
   import polars as pl

   lf = pl.scan_parquet("<file>")  # delimited text: pl.scan_csv("<file>", separator="<delimiter>")
   print("rows:", lf.select(pl.len()).collect().item())
   sample = lf.head(100_000).collect()
   for name, dtype in sample.schema.items():
       col = sample[name]
       line = [name, str(dtype), "missing", col.null_count(), "distinct", col.n_unique(), "example", col.drop_nulls().head(1).to_list()]
       if dtype.is_numeric():
           line += ["min", col.min(), "max", col.max()]
       print(*line)
   print("duplicate rows in sample:", sample.height - sample.unique().height)
   ```

5. pandas.

   ```python
   import pandas as pd

   sample = pd.read_csv("<file>", sep="<delimiter>", encoding="<encoding>", nrows=100_000)
   print("rows in sample:", len(sample))
   for name in sample.columns:
       col = sample[name]
       line = [name, str(col.dtype), "missing", int(col.isna().sum()), "distinct", col.nunique(), "example", col.dropna().head(1).tolist()]
       if col.dtype.kind in "iuf":
           line += ["min", col.min(), "max", col.max()]
       print(*line)
   print("duplicate rows in sample:", int(sample.duplicated().sum()))
   ```

   Parquet: `pd.read_parquet` loads the whole file. For a sample and the true row count, use `pyarrow`:

   ```python
   import pyarrow.parquet as pq

   pf = pq.ParquetFile("<file>")
   print("rows:", pf.metadata.num_rows, "columns:", pf.metadata.num_columns)
   sample = next(pf.iter_batches(batch_size=100_000)).to_pandas()
   ```

6. Read the numbers and note each of these that applies:
   - A column that is all missing, or has one distinct value.
   - A column that should be the key but has fewer distinct values than rows.
   - A number or date column typed as text. Look at its most common values for the cause: decimal comma, currency sign, placeholders such as `N/A` or `-`.
   - An identifier typed as a number. Leading zeros are already lost in this load; read that column as text (see `workbench-csv`, step 5).
   - Placeholder numbers at the edge of the range, such as `-999`, `9999` or the date `1970-01-01`.
   - The same category written several ways (case, spaces).

7. Check the source is untouched: `wc -c <file>` prints the same number as in step 1. In a Git repository, `git status --short` lists no data file you did not mean to create.

## When a check fails

- Out of memory, or the load does not return: stop it. Use a sample (step 1), or move to DuckDB or Polars, which can count rows without loading the whole file.
- DuckDB `Conversion Error` on a delimited file: a column changes type after the rows DuckDB sampled. Read with `all_varchar = true`.
- pandas `ParserError` with `Expected 3 fields in line 4, saw 4` or similar numbers: the file is ragged. Go to the `workbench-csv` skill, step 4.
- `UnicodeDecodeError`: the encoding is wrong. Take it from the `workbench-csv` script.

## Report

```
File: <path>, <bytes> bytes, <format>
Rows: <n> (whole file | first <n> rows | random sample of <n>)
Columns: <n>
| column | type | missing | distinct | example | note |
Duplicate rows: <n> (whole file | sample)
Key candidate: <column, or "none found">
Problems: <list from step 6, or "none found">
Not checked: <what the sample or the tools did not cover>
```

## Gotchas

- `head` and `nrows` take the first rows, not a random sample. In a file sorted by date they show one period only. Say so, or sample with DuckDB `USING SAMPLE`.
- Missing and duplicate counts from a sample are estimates. For exact totals run the count on the whole file with DuckDB or Polars.
- `approx_unique` in `SUMMARIZE` is an estimate. For an exact number use `SELECT count(DISTINCT <column>) FROM 'F';`
- Polars `n_unique` counts missing as one value. pandas `nunique` leaves missing out.
- In DuckDB 2, the result of `con.sql(...)` has no `fetchone()`. Use `fetchall()`.
- The DuckDB server in this kit runs an in-memory database that is not read-only, and its SQL can write files. Run only `SELECT`, `DESCRIBE`, `SUMMARIZE`, `SHOW` and `ATTACH ... (READ_ONLY)`. Never `COPY ... TO`, `EXPORT`, `INSERT`, `UPDATE`, `DELETE` or `DROP` on source data.
- That server returns at most 1024 rows and 50,000 characters per query by default. Add `LIMIT` and select only the columns you need.
- Parquet is compressed. Loaded into memory it can need several times its file size.
