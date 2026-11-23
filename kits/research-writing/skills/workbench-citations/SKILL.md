---
name: workbench-citations
description: "Cite sources and keep the bibliography. Use when adding a citation or a reference entry: follow the project's style, take entry data from the source, never invent a reference, mark unverified claims."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You add, change or check a citation, a footnote reference or a bibliography entry.
- You write a claim that needs a source.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Find out how this project cites. `AGENTS.md` decides if it names a style. Otherwise look:

   ```
   find . -name '*.bib' -not -path './.git/*'
   grep -rn --include='*.md' --include='*.qmd' --include='*.yml' -E '^(bibliography|csl):' .
   grep -rn --include='*.tex' -E '\\(bibliography|addbibresource)\{' .
   grep -rn --include='*.typ' -F '#bibliography(' .
   ```

   Use the first row that matches:

   | You find | Cite in the text | Entries live in |
   | --- | --- | --- |
   | Markdown or Quarto with a `bibliography:` line or a `.bib` file | `[@key]`, `[@key, p. 12]`, `[@a; @b]`; a bare `@key` inside a sentence names the author in the text | The `.bib` file |
   | LaTeX | The command this document already uses, such as `\cite{key}`, `\citep{key}` or `\parencite{key}` | The `.bib` file |
   | Typst with `#bibliography("...")` | `@key`, `@key[p. 12]` | The `.bib` or `.yml` file it names |
   | None of these | The form the document already uses. If it has none: numbers like `[1]` and a `## References` list at the end | That list |

2. Look for the work before you add it. Search the bibliography for the first author's surname and for a word from the title:

   ```
   grep -n -i "<surname>" <file.bib>
   ```

   If the work is there, use its key. Never add a second entry for the same work.
3. Take a new entry from the source, never from memory. Use the first rule that fits:
   - The source shows a DOI: ask the DOI registry for the entry.

     ```
     curl -sS -f -L --max-time 30 -H "Accept: application/x-bibtex" https://doi.org/<DOI>
     ```

     Output that holds `@article{`, `@book{` or a similar entry start is the entry. An error with `404` means this DOI is not registered: read the DOI on the source page again. Do not change digits until it works.
   - The source is an arXiv paper: `curl -sS -L --max-time 30 https://arxiv.org/bibtex/<id>`, where `<id>` looks like `2602.11988`.
   - Anything else (web page, report, book): write the entry by hand from the page you have open. Copy the entry type and field names from a similar entry already in the file. If there is none:

     ```
     @misc{<key>,
       author = {<Surname>, <First name> and <Surname>, <First name>},
       title = {<Title as the page shows it>},
       year = {<year>},
       url = {<address>},
       urldate = {<YYYY-MM-DD>}
     }
     ```

     `urldate` is the day you read it; get it with `date +%Y-%m-%d`. When the author is an organisation, use double braces, `author = {{Name of the Organisation}}`; with single braces the last word is taken for a surname. Leave a field out when the page does not state it. Do not fill it with a guess.
4. Compare every fetched entry with the source: title, first author and year must match. If they do not, you have the wrong DOI or id.
5. Give the entry a key in the pattern the file already uses. In an empty file use surname, year and first title word, lower case, letters and digits only, like `smith2020rivers`. Change a key the registry made up, such as `LeCun_2015`, to that pattern.
6. Cite only what the source states. Give the page number for a quotation or a specific figure when the source has pages.
7. No source for a claim: do not cite. Put `[unverified]` right after the claim and list it in your report. Never attach a real reference to a claim it does not make.
8. Check. Run every check that applies and fix what it shows.
   - Markdown, Quarto or LaTeX with a `.bib` file, when `pandoc --version` works:

     ```
     pandoc -f markdown <file.md or file.qmd> --citeproc --bibliography=<file.bib> -t plain -o /tmp/cite-check.txt
     pandoc <file.tex> --citeproc --bibliography=<file.bib> -t plain -o /tmp/cite-check.txt
     ```

     Each line `[WARNING] Citeproc: citation <key> not found` names a key that is cited but not in the bibliography. Fix the key or add the entry, then run it again until no such line is printed. `tail -n 30 /tmp/cite-check.txt` shows the reference list as it will be printed: read it for missing authors, years and titles.
   - Duplicate keys. This must print nothing:

     ```
     grep -o -E '^@[A-Za-z]+\{[^,]+' <file.bib> | sed 's/^@[A-Za-z]*{//' | sort | uniq -d
     ```

   - LaTeX without Pandoc: build with the command in `AGENTS.md`, then `grep -n "Citation" <main>.log`. A line with `undefined` names a missing key.
   - Typst: build with `typst compile <file>.typ` and fix every error it prints.
   - Numbered references by hand: `grep -o -E '\[[0-9]+\]' <file> | sort -u` lists the numbers used. Each one needs exactly one entry in the list, and each entry must be cited.

## Report

```
Style: <what the project uses and where you found that out>
Entries added: <key>: <DOI lookup | arXiv | by hand from <address>>
Citations added or changed: <file:line> <key>
Checks: <command>: <result>
Still unverified: <file:line> <claim>
```

Find the last group with `grep -n -F "[unverified]" <file>`.

## Gotchas

- Reference details written from memory are often wrong even when the work exists: the year, the pages, the co-authors, the DOI. Fetch them.
- The registry's entry is raw data. Check its title, author names and page range against the source page, and correct them from that page, not from memory.
- An entry in someone else's reference list is not a source you have read. Open the work before you cite it.
- A preprint and the published article are different entries. Cite the one you read.
- When a Markdown file with `[@key]` citations is converted without `--citeproc`, the citations come out as plain text like `[@key]` and no reference list is made.
- Pandoc does not warn about two entries with the same key. It uses one and ignores the other. Run the duplicate check.
- A reference list may not print `urldate`; that depends on the style. Keep the field anyway.
