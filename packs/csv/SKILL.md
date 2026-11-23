---
name: workbench-csv
description: Inspect and validate local CSV and TSV data with the Python standard library.
---

Use a bounded sample to establish encoding, delimiter, headers, quoting and row shape. In Python, open the file with explicit encoding and newline="" and use csv.reader or csv.DictReader. Validate the guessed dialect on several records.

Keep identifiers as text so leading zeros survive. Distinguish missing fields from blank strings. Use Decimal when currency must round-trip exactly. Preserve intentional multiline fields. Write a separate output file and verify its headers, record count and identifiers before replacing an original.

Adapted from the workstation OMP CSV skill. No external software or account is required.
