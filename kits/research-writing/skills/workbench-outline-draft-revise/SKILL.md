---
name: workbench-outline-draft-revise
description: "Write or rewrite a document in passes. Use for a report, paper, article or long section: outline first, draft one section at a time, then revise separately for structure, clarity and style."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You are asked to write a document or a section longer than about 300 words.
- You are asked to revise, edit, tighten or restructure existing text.

Where to start:

| The request | Start at |
| --- | --- |
| Write something new | Step 1 |
| Revise or restructure existing text | Step 4 |
| Proofread or polish only | Step 4, then only step 7. Report structural problems; do not fix them |

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Read `AGENTS.md`: Purpose and audience, Style, Files. You need three facts: who reads this, what they should know or decide afterwards, and the length. If one is missing and the user did not say, ask once before you write. If you cannot ask, choose, and state your choice in the first line of your report.
2. Write the outline to a file beside the draft, `outline.md` unless `AGENTS.md` names a place:

   ```
   Claim: <the one sentence the reader should take away>
   Reader: <who>   Length: <words>
   1. <Heading> | <the point of this section as a full sentence> | <words> | sources: <keys or "none yet">
   2. <Heading> | <point> | <words> | sources: <...>
   ```

   - A point is a sentence that could be wrong, such as "Costs fell because of X". "Background" or "Costs" is a topic, not a point.
   - The word numbers add up to the length.
   - Check: read only the points, top to bottom. They must make the argument on their own. Fix the outline until they do.
   - Longer than 1,000 words: show the outline and wait for a yes before drafting. Shorter: go on.
3. Draft one section at a time, in outline order.
   - Start each paragraph with the sentence that states its point.
   - Use only facts from your source notes or the user's material. A missing fact becomes `[TODO: <what is missing>]`. A claim without a source gets `[unverified]`. Do not invent and do not stop to research in the middle of a section.
   - Do not polish and do not go back to earlier sections.
   - After each section, count: `wc -w <draft>` for Markdown, `pandoc <draft> -t plain | wc -w` for LaTeX or DOCX. More than 20% over the section's number: cut before you write the next section.
4. Save the state before revising, so you can compare later:

   ```
   cp <draft> /tmp/draft-before-revision.md
   ```

5. Pass 1, structure. Move, add and cut. Do not reword.
   - List the headings: `grep -n '^#' <draft>` for Markdown, `grep -n -E '\\(sub)*section\*?\{' <draft>` for LaTeX, `grep -n '^=' <draft>` for Typst.
   - For each section, write down in one sentence the point the text actually makes. Compare with the outline, or with the request when there is no outline.
   - No point, or a point already made: cut the section. Two points: split it. A point the argument needs is missing: add `[TODO: <point>]`, or draft it as in step 3. Wrong order: move it.
   - The opening states the claim. The ending answers it and brings no new material.
   - Check: the heading list matches the outline and the word count is within 10% of the length.
6. Pass 2, clarity. Change sentences. Do not move anything. Go paragraph by paragraph:
   - The first sentence states the paragraph's point.
   - One name for one thing. Pick the term and find the others with `grep -n -i "<other term>" <draft>`.
   - Every abbreviation and technical term is explained where it first appears.
   - Split a sentence that is longer than about 30 words.
   - "This", "it" and "they" point to something named in the sentence before.
   - Every number has a unit and, where it matters, a date.
7. Pass 3, style. Change words and punctuation only.
   - Apply `AGENTS.md` Style: language variant, tone, date and number formats.
   - Find words that add nothing and delete them or the phrase around them:

     ```
     grep -n -i -E '\b(very|really|quite|basically|actually|in order to|it should be noted|it is important to note)\b' <draft>
     ```

   - Make headings, list punctuation and quotation marks consistent with each other.
8. Check that revising changed no facts:

   ```
   grep -o -E '[0-9]([0-9.,]*[0-9])?%?' /tmp/draft-before-revision.md | sort > /tmp/numbers-before.txt
   grep -o -E '[0-9]([0-9.,]*[0-9])?%?' <draft> | sort > /tmp/numbers-after.txt
   diff /tmp/numbers-before.txt /tmp/numbers-after.txt
   ```

   No output: no number changed. Each line it prints is a number that was added (`>`) or removed (`<`). Put back every one you did not mean to change. With Pandoc installed, also run `pandoc <file> -t json | grep -o '"t":"Cite"' | wc -l` on both files: the count of citations should be the same unless you cut a passage on purpose.
9. List what is still open: `grep -n -E 'TODO|\[unverified\]' <draft>`. Leave the marks in the text. Then run the build command from `AGENTS.md` if there is one.

One pass, one kind of change. If you see a structural problem during pass 2 or 3, note it, finish the pass, then repeat step 5 for that section only.

## Report

```
File: <path>, <words> words (limit <n>)
Structure: <what you moved, cut or added>
Clarity: <the main changes>
Style: <the main changes>
Open: <line>: <TODO or [unverified] text>
Checks: numbers <same | changed on purpose: ...>; build <passed | failed | none>
Questions: <what you need from the user>
```

## Gotchas

- Revision adds no facts. A new claim needs a source first.
- Do not reword quotations, change citation keys or round numbers while editing for clarity or style.
- When you revise someone else's text, make the smallest change that fixes the problem and keep their wording elsewhere.
- Long file, small context window: revise one section at a time. Find its lines with the heading list, read only that range, and run steps 8 and 9 on the whole file at the end.
- `wc -w` counts markup, tables and reference entries too. Compare like with like.
