---
name: workbench-accessibility-check
description: "Accessibility check for changed UI. Use after adding or editing buttons, links, forms, dialogs or images: check names, roles, keyboard order, visible focus and contrast, and report per element."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- After you added or changed something a user reads or operates: buttons, links, form fields, dialogs, menus, images, headings, text colours.
- Check only the elements you changed. Do not audit the whole page unless asked.

## Steps

1. List the elements. Read `git --no-pager diff`, or the files you edited, and write down every element you added or changed, with file and line. These two commands help; the second one lists `div` and `span` elements that have a click handler:

   ```
   grep -n -E "<(a|button|input|select|textarea|label|img|dialog|h[1-6])([ >/]|$)" <changed file>
   grep -n -E "<(div|span)[^>]*(onClick|@click|on:click|onclick)" <changed file>
   ```

   They do not list the project's own components, such as `<Button>`. Add those from the diff by hand.
2. Check the source of each element against this table:

   | Element | Rule | Fix |
   | --- | --- | --- |
   | Anything the user clicks | It is a `<button>` when it does something and an `<a href>` when it goes somewhere. Not a `div` or `span` with a click handler. | Change the element. |
   | Button or link | It has text. With only an icon it has `aria-label`. | Add text, or `aria-label` that says what it does. |
   | `input`, `select`, `textarea` | A `<label>` belongs to it: the label's `for` (`htmlFor` in JSX) equals the field's `id`, or the label wraps the field. `placeholder` is not a label. | Add the label. Use `aria-label` only when the design has no visible label. |
   | Hint or error text of a field | The field has `aria-describedby` with the `id` of that text. | Add the attribute. |
   | `img` | It has `alt`: what the image shows, or `alt=""` when it is decoration. | Add `alt`. |
   | Heading | Levels go down one step at a time: `h2` under `h1`, `h3` under `h2`. | Change the level. Set the size with CSS. |
   | Any element | No `tabindex` above 0. Find them: `grep -n -i -E "tabindex=[\"{]?[1-9]" <changed file>` | Remove it and put the markup in the right order. |
3. Run the project's lint command from `AGENTS.md`. Read every warning that comes from an accessibility rule, for example a rule named `jsx-a11y/...`. Do not install a lint plugin to get such warnings.
4. Open the page in a browser tool. `workbench-browser-verify` steps 1 to 6 do that. With no browser tool, go to step 8 and report steps 5 to 7 as not checked.
5. Name and role. Take a snapshot (`browser_snapshot` or `take_snapshot`). Every element on your list must be there with the right role and a name that says what it does, for example `button "Save changes"`. It fails when the name is empty, is a word like "button" or "click here", or when a thing you can click shows no role such as `button` or `link`.
6. Keyboard and focus. Open the address again so that focus starts at the top. Press `Tab` (`browser_press_key` or `press_key`). After each press run this in the page (`browser_evaluate` or `evaluate_script`):

   ```
   () => { const e = document.activeElement; if (!e || e === document.body) return 'nothing focused'; const s = getComputedStyle(e); return e.tagName + ' "' + (e.getAttribute('aria-label') || e.innerText || e.value || '').trim().slice(0, 40) + '" | outline: ' + s.outlineStyle + ' ' + s.outlineWidth + ' | box-shadow: ' + s.boxShadow; }
   ```

   Go on until every control on your list has had focus, at most 40 presses. Then check:
   - Reach: every control on your list got focus. A control that never did fails.
   - Order: focus moved in reading order, top to bottom. A jump backwards fails.
   - Visible focus: it fails when `outline` starts with `none` or ends with `0px`, and `box-shadow` is `none`. Exception: the stylesheet has a `:focus` or `:focus-visible` rule for this control that changes its border or background. Name that rule in the report.
   - Keys: with focus on the control, `Enter` works on a link or button and `Space` on a button or checkbox, the same as a click. `Escape` closes a dialog or menu. Run the function again: focus must be back on the control that opened it.
7. Contrast. For each text you added or recoloured, and the text inside each changed control, run this in the page. Replace `SELECTOR` with a CSS selector that matches the element, for example `#email` or `.card h2`:

   ```
   () => {
     const el = document.querySelector('SELECTOR');
     if (!el) return 'no element matches';
     const cv = document.createElement('canvas'); cv.width = cv.height = 1;
     const cx = cv.getContext('2d', { willReadFrequently: true });
     const rgba = (c) => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = c; cx.fillRect(0, 0, 1, 1); return [...cx.getImageData(0, 0, 1, 1).data]; };
     const lum = (p) => p.slice(0, 3).map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
     const s = getComputedStyle(el); const fg = rgba(s.color);
     let bg = [0, 0, 0, 0];
     for (let n = el; n && bg[3] === 0; n = n.parentElement) bg = rgba(getComputedStyle(n).backgroundColor);
     if (bg[3] === 0) bg = [255, 255, 255, 255];
     const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
     const note = fg[3] < 255 || bg[3] < 255 ? ' (see-through colour: not exact)' : '';
     return ((hi + 0.05) / (lo + 0.05)).toFixed(2) + ':1, ' + s.fontSize + ', weight ' + s.fontWeight + note;
   }
   ```

   It returns, for example, `4.54:1, 16px, weight 400`. Compare the first number:

   | Text | Passes at |
   | --- | --- |
   | 24px or larger, or 18.66px or larger with weight 700 or more | 3:1 or more |
   | All other text | 4.5:1 or more |

   Report "contrast not measured" when the result says `see-through colour`, or when the text sits on an image or a gradient.

   With no browser tool, take the two colours from the stylesheet as six-digit hex values and run:

   ```
   node -e 'const L=h=>{const n=parseInt(h.slice(-6),16);return[n>>16&255,n>>8&255,n&255].map(v=>{v/=255;return v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[0.2126,0.7152,0.0722][i],0)};const[a,b]=process.argv.slice(1).map(L).sort((x,y)=>y-x);console.log(((a+0.05)/(b+0.05)).toFixed(2))' "#767676" "#ffffff"
   ```

   This example prints `4.54`. Keep the quotes around each colour.
8. Fix every failure, then repeat the step that failed. If one element still fails after two different fixes, stop and report it as not fixed.

## Report

One line per element:

```
Accessibility check: <page or component>
- <element> (<file>:<line>): name "<name>", role <role>, keyboard ok, focus visible, contrast <n>:1 ok
- <element> (<file>:<line>): FAIL <rule> -> fixed: <what you changed>
- <element> (<file>:<line>): FAIL <rule> -> not fixed: <why>
Not checked: <check and reason, or "nothing">
```

Write `ok` only for a check you ran in this session after your last edit. These checks cover the changed elements only. Never write that the page "is accessible" or "is compliant".

## Gotchas

- Use the native element first. `<button>`, `<a href>`, `<input>`, `<select>` and `<dialog>` bring their role, focus and keys with them. A `div` with `role="button"` has none of that until you add `tabindex="0"` and key handlers for `Enter` and `Space` yourself.
- `aria-label` replaces the element's own text as its name. Do not put one on a control whose visible text is already a good name.
- `aria-hidden="true"` on a control that can take focus, or on a parent of one, is a failure.
- `nothing focused` after a `Tab` means focus left the page at the end of the order. The next `Tab` starts from the top again.
- Test focus with real key presses only. After a `.focus()` call from a script the browser may not show the keyboard focus style, and the function then reports a failure that is not real.
- A dark theme has its own colours. When the project has one, measure contrast in both themes.
- The numbers in step 7 are the WCAG level AA values. When `AGENTS.md` names another target under `Accessibility`, say in the report that only these were checked.
