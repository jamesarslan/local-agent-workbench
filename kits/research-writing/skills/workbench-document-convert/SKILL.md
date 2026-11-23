---
name: workbench-document-convert
description: "Convert a document between Markdown, DOCX, PDF, LaTeX and HTML. Use when asked to export, import or change a file's format: use the tools installed, then check headings, tables and links survived."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You are asked to turn a document into another format, to export it, or to bring a DOCX or PDF into the project as text.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Check the converter: `pandoc --version`. If the command is not found, stop and say that Pandoc is needed (https://pandoc.org/installing.html). Do not install it without asking. Reading a PDF needs `pdftotext` instead; see step 5.
2. Protect the source. The output is always a new file with a new name. Run `ls <output>` first; if the file exists, ask before you replace it.
3. Change into the folder that holds the source file and run the command for your pair there. Images with relative paths are found only from that folder; from elsewhere Pandoc prints `[WARNING] Could not fetch resource` and puts the image's description in its place.

   | From | To | Command |
   | --- | --- | --- |
   | Markdown | DOCX | `pandoc in.md -o out.docx` |
   | DOCX | Markdown | `pandoc in.docx -t gfm --wrap=none --extract-media=. -o out.md` |
   | Markdown | LaTeX | `pandoc in.md -s -o out.tex` |
   | LaTeX | Markdown | `pandoc in.tex -t gfm --wrap=none -o out.md` |
   | LaTeX | DOCX | `pandoc in.tex -o out.docx` |
   | Markdown | HTML | `pandoc in.md -s -o out.html` |
   | HTML | Markdown | `pandoc in.html -t gfm --wrap=none -o out.md` |
   | Any of these | PDF | Step 4 |
   | PDF | Text | Step 5 |

   - `-s` writes a complete file. Without it, LaTeX and HTML output is a fragment with no preamble or head.
   - `--extract-media=.` saves the pictures of a DOCX in a folder `media` in the current folder and links them.
   - The source has `[@key]` citations: add `--citeproc`, and `--bibliography=<file.bib>` unless the file's own header names one. Without it the citations stay as plain `[@key]` text.
   - The DOCX must look like an existing Word file: add `--reference-doc=<that file.docx>` to take its styles.
   - A `.qmd` file: add `-f markdown`, or Pandoc warns that it cannot tell the format.
4. To PDF. Use the first rule that fits:
   - `AGENTS.md` has a build command: run it.
   - A `.tex` file: `latexmk -pdf <main>.tex`.
   - A `.typ` file: `typst compile <file>.typ <out>.pdf`.
   - A `.qmd` file: `quarto render <file>.qmd --to pdf`. This needs a TeX installation.
   - Anything else: Pandoc needs a separate PDF program. See which are installed; the command prints the path of each one it finds:

     ```
     command -v typst xelatex lualatex pdflatex tectonic weasyprint
     ```

     Then run `pandoc in.md -o out.pdf --pdf-engine=<one of the names printed>`.
   - Nothing is printed: there is no PDF program. Stop and report that. Offer DOCX or HTML instead. Do not install one without asking.
5. From PDF. Pandoc cannot read PDF.

   ```
   pdfinfo in.pdf
   pdftotext -layout in.pdf out.txt
   wc -w out.txt
   ```

   - `pdfinfo` prints `Pages:` and `Encrypted:`. If these commands are not found, stop and say that the Poppler tools are needed.
   - `-layout` keeps the page layout, which keeps table columns apart. For running text in two columns, leave it out so each column is read in turn.
   - `-f 3 -l 5` converts only pages 3 to 5.
   - Zero words, or very few, from a file that has pages: the pages are images. It needs text recognition, which this skill does not cover. Stop and report it.
   - The result is plain text. Headings, tables and links are no longer marked. Say so; rebuild them only when asked.
   - If you have a tool named `convert_to_markdown`, you can give it `file:///<absolute path>/in.pdf` instead and get Markdown back.
6. Check the structure. Run this on the source and on the output and compare the two lists:

   ```
   pandoc <file> -t json | grep -o -E '"t":"(Header|Table|Link|Image|Note|Cite)"' | sort | uniq -c
   ```

   `Header`, `Table`, `Image` and `Note` (footnotes) must have the same counts. `Link` must not be lower. Known differences that are not errors:
   - A Markdown output counts fewer `Table` or `Image` because some were written as HTML. Count those with `grep -c -E '<table|<img' out.md`; the two numbers together must match the source. Converting with `-t markdown` instead of `-t gfm` keeps them as Markdown.
   - `Cite` is 0 after `--citeproc` and `Link` can go up, because citations became text and a reference list. Check that the list is there: `pandoc <output> -t plain | tail -n 20`.
   - An HTML file made with `-s` from a document that has a title counts one more `Header`: the title.

   When a count is lower for any other reason, find what is missing: list the headings of both with `pandoc <file> -t gfm | grep -n '^#'`, and search the output for the first words of each table.
7. Check a PDF you made: `pdfinfo out.pdf` shows `Pages:` of at least 1, and the first and the last heading of the source are in it:

   ```
   pdftotext out.pdf - | grep -n -F "<heading text>"
   ```

8. Look at the start of a text output yourself: `head -n 40 out.md`. The counts do not show garbled characters or broken lists.

## Report

```
Command: <the command you ran>
Output: <path>
Structure: headers <source>/<output>, tables <s>/<o>, links <s>/<o>, images <s>/<o>, notes <s>/<o>
Lost or changed: <what did not survive, or "nothing found">
Not checked: <for example the look of the page>
```

## Gotchas

- Pandoc leaves out LaTeX commands and environments it does not know, such as a drawing or a custom macro, with no warning and exit code 0. After converting from LaTeX, compare the text of each section, not only the counts.
- A DOCX with tracked changes is read as if every change were accepted, and comments are dropped. Add `--track-changes=all` to keep them.
- A conversion keeps structure, not appearance. Fonts, page breaks, column widths and the position of pictures change. Do not promise the same look.
- Converting there and back does not give the original file. Keep editing the source format and convert again.
- Exit code 0 does not mean nothing was lost. Read every `[WARNING]` line and do the check in step 6.
