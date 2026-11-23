---
name: workbench-csv
description: "CSV and TSV files. Use to inspect, clean or convert one: find encoding, delimiter, header and row shape from a sample, keep identifiers as text, write a new file and verify the counts."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Before you read, filter, clean, merge or convert a `.csv`, `.tsv` or other delimited text file.
- When a load fails or looks wrong: one wide column, shifted columns, broken accents, lost leading zeros.

Rules that hold for every step:

- Never print or open the whole file. Look at a sample.
- Never write to the source file. Write a new file in the results folder named in `AGENTS.md`.
- Every value is text until the task needs a number or a date. Then convert that one column.

## Steps

1. Measure the file: `wc -c <file>` gives bytes, `wc -l <file>` gives lines. Lines are not records: a quoted field can hold a line break.

2. Save this script outside the data folder, for example as `/tmp/csv_check.py` (PowerShell: `$env:TEMP\csv_check.py`). It needs only the standard library and reads the file without changing it.

   ```python
   import codecs, collections, csv, io, sys

   path = sys.argv[1]
   with open(path, "rb") as f:
       head = f.read(65536)
   if head.startswith((b"\xff\xfe", b"\xfe\xff")):
       encoding = "utf-16"
   else:
       try:
           codecs.getincrementaldecoder("utf-8-sig")().decode(head)
           encoding = "utf-8-sig"
       except UnicodeDecodeError:
           encoding = "cp1252"
   text = head.decode(encoding, errors="replace")


   def score(delimiter):
       rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter) if r]
       rows = rows[:-1] if len(rows) > 1 else rows
       if not rows:
           return (False, 0, 0)
       width, hits = collections.Counter(map(len, rows)).most_common(1)[0]
       return (width > 1, hits / len(rows), width)


   delimiter = max(",;\t|", key=score)
   print("encoding:", encoding, "(guess)" if encoding == "cp1252" else "")
   print("delimiter:", repr(delimiter), "" if score(delimiter)[0] else "(guess: one column found)")
   widths, odd, bad, first = collections.Counter(), [], 0, None
   with open(path, newline="", encoding=encoding, errors="replace") as f:
       reader = csv.reader(f, delimiter=delimiter)
       for number, row in enumerate(reader, 1):
           if number <= 4:
               print("record", number, [value[:40] for value in row])
           first = len(row) if first is None else first
           widths[len(row)] += 1
           bad += any("\ufffd" in value for value in row)
           if len(row) != first and len(odd) < 5:
               odd.append((reader.line_num, len(row)))
   print("records:", sum(widths.values()), "(a header counts as one)")
   print("fields per record:", dict(widths))
   print("first odd records as (file line, fields):", odd)
   print("records with undecodable bytes:", bad)
   ```

3. Run it: `python3 /tmp/csv_check.py <file>` (on Windows, use the `python` of the project environment). Read each line of the result:

   | Line | Good | If not |
   | --- | --- | --- |
   | `encoding` | `utf-8-sig` or `utf-16` | `cp1252 (guess)`: look at a record with accented letters. If they are wrong, ask where the file came from. |
   | `delimiter` | no `(guess)` | The file is not delimited by a comma, semicolon, tab or pipe. Look at the start with `head -c 300 <file>` and ask. |
   | `record 1` | names, not data | The file has no header. Say so. Read it with `csv.reader` and use positions, or pass `fieldnames=[...]` to `DictReader`. |
   | `fields per record` | one entry, for example `{5: 200002}` | Several entries: the file is ragged. An entry for `0` fields is a blank line. Go to step 4 before anything else. |
   | `records with undecodable bytes` | `0` | The encoding is wrong for those records. Do not write an output until it is settled. |

   Write down the three facts you will reuse: encoding, delimiter, number of data records (records minus the header).

4. Ragged file (records with different field counts). Print one odd record with the line number the script gave: `sed -n '<line>p' <file>`.
   - Too many fields: usually an unquoted delimiter inside a value.
   - Too few: usually a cut-off record or a line break inside an unquoted value.
   - Do not guess a repair. Report the count and two examples, and ask whether to drop, repair or keep those records.

5. Read with the facts from step 3. Standard library:

   ```python
   import csv

   with open("<file>", newline="", encoding="<encoding>") as f:
       for row in csv.DictReader(f, delimiter="<delimiter>"):
           ...  # row is a dict of text values
   ```

   With a data library, switch type guessing off so identifiers keep their leading zeros:

   | Library | Read every column as text |
   | --- | --- |
   | pandas | `pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=False)` |
   | Polars | `pl.read_csv(path, separator=";", infer_schema=False)` |
   | DuckDB | `SELECT * FROM read_csv('<file>', delim = ';', all_varchar = true)` |

   To try a read on a sample first, add `nrows=1000` (pandas), `n_rows=1000` (Polars) or `LIMIT 1000` (DuckDB).

6. Write a new file. Open the output with mode `"x"`: it fails with `FileExistsError` instead of overwriting.

   ```python
   import csv

   SRC, OUT = "<source file>", "<results folder>/<name>.csv"
   with open(SRC, newline="", encoding="<encoding>") as src, open(OUT, "x", newline="", encoding="utf-8") as out:
       reader = csv.DictReader(src, delimiter="<delimiter>")
       writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
       writer.writeheader()
       for row in reader:
           # change values here
           writer.writerow(row)
   ```

   With pandas: `df.to_csv(OUT, index=False, mode="x")`.

7. Verify the output against the source. Fill in the two paths, their encodings and delimiters, and the identifier column:

   ```python
   import csv


   def column(path, encoding, delimiter, name):
       with open(path, newline="", encoding=encoding) as f:
           reader = csv.DictReader(f, delimiter=delimiter)
           return reader.fieldnames, [row[name] for row in reader]


   head_in, ids_in = column("<source file>", "<encoding>", "<delimiter>", "<id column>")
   head_out, ids_out = column("<output file>", "utf-8", ",", "<id column>")
   print("header:", head_in, "->", head_out)
   print("records:", len(ids_in), "->", len(ids_out))
   print("ids lost:", len(set(ids_in) - set(ids_out)), "ids new:", len(set(ids_out) - set(ids_in)))
   print("same ids in the same order:", ids_in == ids_out)
   ```

   Pass when every difference is one the task asked for. A filter must lose exactly the records it was meant to remove. `ids new` must be `0`. If a number is not what you expected, the output is wrong: find the cause, write a new output and verify again. To write again, give the output a new name, or ask before you delete the failed one.

## Report

```
Source: <path>, <bytes> bytes, encoding <encoding>, delimiter <delimiter>, <n> data records, <n> columns
Problems found: <ragged records, undecodable bytes, no header, or "none">
Output: <path>, <n> data records
Check: records <in> -> <out>, ids lost <n>, ids new <n>, order kept <yes or no>
Not done: <what needs a decision>
```

## Gotchas

- `csv.Sniffer` picks `,` for a `;` file that has no header row and numbers with a decimal comma (`13,44`). Use the script in step 2 instead.
- Encoding `utf-8-sig` reads UTF-8 with or without a byte order mark. Plain `utf-8` leaves the mark stuck to the first column name.
- Without `dtype=str`, pandas and Polars turn `00123` into `123`. In pandas, a whole-number column with one missing value becomes decimal (`1` becomes `1.0`).
- Without `keep_default_na=False`, pandas reads the texts `NA`, `null` and the empty string as missing.
- The `csv` module returns `''` for both an empty field and a quoted empty field (`,,` and `,"",`). A short record gives `None` for the missing columns in `DictReader`.
- `DictWriter` raises `ValueError: dict contains fields not in fieldnames: None` on a record with too many fields. That is a ragged file: go to step 4.
- `_csv.Error: field larger than field limit (131072)` nearly always means a wrong delimiter or an unclosed quote. Do not raise the limit; go back to step 3.
- A `;` file often uses a decimal comma. `float("13,44")` fails. Convert with `Decimal(value.replace(",", "."))` only when the file has no thousands separators.
- For money, use `decimal.Decimal`, not `float`: `0.1 + 0.2` is `0.30000000000000004` as floats.
- Always pass `newline=""` to `open`, for reading and for writing. Without it, line breaks inside quoted fields are not handled correctly.
