# Sample documents (synthetic)

These are **synthetic sample documents for software demonstration only**. They are not real
notices, and nothing here is legal, medical, benefits, or immigration advice. The agencies,
people, member IDs, receipt numbers, addresses, phone numbers, and dollar amounts are invented
for the demo. Every rendered page is marked `SAMPLE` in the footer and with a light diagonal
watermark so a screenshot cannot be mistaken for a real notice.

The app uses these files in demo mode and as the "load a sample case" content. They are
read-only reference content; `docs.json` is the source of truth for the page text.

## The three cases

| UI label | `id` | Title | Doc type | Pages | What the sample exercises |
| --- | --- | --- | --- | --- | --- |
| Insurance denial | `benefits-denial` | Notice of Adverse Benefit Determination | insurance denial | 2 | Denial reasoning, internal appeal deadline, expedited appeal, external review, mailing address and phone/fax block |
| SNAP recertification | `snap-recertification` | SNAP Recertification Notice | benefits recertification | 2 | Recertification interview and document deadline, bullet list of required documents, a checklist with checkboxes, submission options |
| Immigration RFE | `immigration-rfe` | Request for Evidence | immigration request | 2 | Response deadline, evidence list, "this is not a denial" framing, response checklist, where to send a package |

Each document's exact page text lives in [`docs.json`](docs.json); the rendered images are
generated from that text, not hand-authored.

## Regenerating the page images

From the `paper-trail` app directory:

```powershell
cd paper-trail
python tools/make_samples.py                     # all three docs -> 6 PNGs
python tools/make_samples.py --only benefits-denial   # one doc
```

Output (committed to the repo):

```
samples/pages/<doc_id>/page-<n>.png
```

Each page is **1275 x 1650 px** (US Letter at 150 dpi), warm paper `#fdfcf8`, optimized PNG.
The renderer is deterministic: no randomness, no timestamps, so re-running it produces
byte-identical files for the same Pillow/font versions. The script prints every file it writes,
and exits non-zero with a clear message if Pillow or `samples/docs.json` is missing.

### Pillow and fonts (build time only)

The running app has **zero dependencies**: it only serves the committed PNGs. Only
`tools/make_samples.py` needs Pillow, and only when you regenerate images.

1. Check the interpreter you plan to use:

   ```powershell
   python -c "import PIL; print(PIL.__version__)"
   ```

   On this workstation, system `python` is 3.14 and already has Pillow (11.3.0), so the plain
   command above works.

2. If that prints `ModuleNotFoundError`, either install Pillow for that interpreter
   (`python -m pip install Pillow`) or use the bundled runtime that ships with Pillow 12.3:

   ```powershell
   & "C:\Users\r_gja.QW\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" tools\make_samples.py
   ```

   Do not add Pillow to the server's requirements; it is a build-time tool only.

3. Fonts come from `C:\Windows\Fonts` and are verified at run time:

   - `georgiab.ttf` (Georgia Bold) for the document title; `georgia.ttf`/Times fallback
   - `arial.ttf`, `arialbd.ttf`, `ariali.ttf` for agency name, body, headings, subject, footer
   - `calibri.ttf` / `calibrib.ttf` fallback if Arial is unavailable
   - If no Windows font is found, the script warns and falls back to Pillow's bundled font.

   Type sizes: title 46 px, agency 30 px, body 23 px, section headings 25 px bold, date 20 px,
   subject 21 px italic, footer 16 px, recipient/small 18 px. No fonts are downloaded.

### Rendering notes

- Page 1 has a letterhead (agency, rule, right-aligned date, recipient block), the document
  title, and an italic subject line; page 2 has a compact agency/title header.
- Body blocks are split on blank lines. Short ALL-CAPS lines become green section headings with
  an accent bar, `- ` lines become hanging-indent bullets, `[ ] ` lines become checkbox rows,
  short lines ending in `:` become bold lead-ins, and single newlines inside a block (address and
  signature blocks) are preserved with tighter leading. Long lines wrap to the 1055 px text width.
- If a body would collide with the footer, the renderer shrinks body/heading type and leading in
  small deterministic steps and prints the scale it used. All six current pages fit at 100%
  (scale 1.00). Pages are never clipped.
- Page 2 of each sample is intentionally short, like the second sheet of a real two-page letter.

## Where the page images are used

- Served read-only at `/samples/pages/<doc_id>/page-<n>.png` (CONTRACT.md section 3).
- `GET /api/docs/<id>` returns `pages[].image_url` pointing at those paths for sample docs.
- The UI page viewer (page n/n, prev/next) shows them; citation chips jump to a page and show the
  quoted line. The sidebar "load a sample case" buttons and `?sample=benefits-denial` rely on the
  ids above.
- The UI labels shown to users are **Insurance denial**, **SNAP recertification**, and
  **Immigration RFE**; keep the `id` values stable because they are the contract.
