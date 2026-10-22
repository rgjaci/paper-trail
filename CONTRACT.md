# Paper Trail — build contract

**App:** Paper Trail, a local-first, privacy-first document desk for bills, benefits notices, insurance
denials, and immigration mail. Explain, extract deadlines, answer questions with page citations, and
draft replies. Hosted Gemma 4 API today; local model behind the same adapter tomorrow.

**Event:** Hacktoberfest Hack Day, Astoria Tech Meetup × MLH — Oct 21, 2026.
**Prototype reference:** `..\astoria-lens` (same zero-dependency stdlib-server + vanilla-JS style).

This file is the single source of truth for the app contract. Do not change it without telling the
Lead. File ownership is listed per section.

## 1. Layout

```
paper-trail/
  server.py                     # OWNER: Lead
  static/
    index.html                  # OWNER: frontend
    app.js                      # OWNER: frontend
    style.css                   # OWNER: frontend
    manifest.webmanifest        # OWNER: frontend (optional)
    icon.svg                    # OWNER: frontend
  samples/
    docs.json                   # OWNER: Lead
    responses.json              # OWNER: Lead
    pages/<doc_id>/page-N.png   # OWNER: samples
    README.md                   # OWNER: samples
  tools/
    make_samples.py             # OWNER: samples
    mock_gemini.py              # OWNER: tests
    selftest.py                 # OWNER: tests
    check_api.py                # OWNER: tests
    eval.py                     # OWNER: tests (optional, nice-to-have)
  skills/
    SKILL.md                    # OWNER: Lead
    deadline-extractor/SKILL.md # OWNER: Lead
    explain-notice/SKILL.md     # OWNER: Lead
    appeal-drafter/SKILL.md     # OWNER: Lead
    ask-with-citations/SKILL.md # OWNER: Lead
  data/                         # runtime store, gitignored, created by server
  README.md                     # OWNER: Lead
  DEMO.md                       # OWNER: Lead
  PRIVACY.md                    # OWNER: Lead
  SUBMISSION.md                 # OWNER: Lead (optional)
  .env.example                  # OWNER: Lead
  .gitignore                    # OWNER: Lead
  LICENSE                       # OWNER: Lead
```

Zero dependencies at runtime: **Python 3.9+ standard library only** (`server.py`), vanilla JS/CSS/HTML,
no CDN, no build step, no fonts fetched from the network. `tools/make_samples.py` may use Pillow at
build time only (sample PNGs are committed).

## 2. Run

```powershell
cd paper-trail
$env:GEMINI_API_KEY="..."     # optional: without it the app runs in Demo mode
python server.py              # http://127.0.0.1:8765
```

Server environment variables:

| Var | Default | Meaning |
| --- | --- | --- |
| `PAPERTRAIL_PORT` | `8765` | HTTP port |
| `PAPERTRAIL_HOST` | `127.0.0.1` | bind host |
| `PAPERTRAIL_DATA_DIR` | `data` | local document store (relative to app dir) |
| `PAPERTRAIL_DEMO` | unset | `1` forces demo mode |
| `GEMINI_API_KEY` | unset | hosted Gemini API key (Gemma 4) |
| `PAPERTRAIL_MODEL` | `gemma-4-26b-a4b-it` | hosted model id |
| `PAPERTRAIL_API_BASE` | `https://generativelanguage.googleapis.com` | Gemini-compatible base URL (tests point this at a mock) |
| `PAPERTRAIL_BACKEND` | `gemini` | `gemini` (native REST) or `openai` (local OpenAI-compatible server) |
| `OPENAI_BASE_URL` | `http://127.0.0.1:11434/v1` | local backend base (Ollama/LiteRT-LM/llama.cpp/LM Studio) |
| `OPENAI_MODEL` | `gemma4:12b` | local model id |
| `OPENAI_API_KEY` | `local` | local backend key (usually ignored) |
| `PAPERTRAIL_MAX_FILE_MB` | `12` | per-file upload limit |
| `PAPERTRAIL_TRANSCRIBE` | `1` | transcribe uploaded page images at ingest for citations (live mode only) |
| `PAPERTRAIL_TRANSCRIBE_MAX_PAGES` | `8` | cap on pages transcribed per upload |

Demo mode is active when `PAPERTRAIL_DEMO=1` **or** (`PAPERTRAIL_BACKEND=gemini` and no
`GEMINI_API_KEY`). In demo mode, `/api/health.demoMode` is `true`, no external requests are made, and
analyze works only for bundled sample docs (recorded answers in `samples/responses.json`).

## 3. HTTP API

All JSON responses are UTF-8. Success responses have `"ok": true`; errors have
`"ok": false, "error": {"code": "...", "message": "..."}` with a matching HTTP status. Never return
secrets or raw API keys. Server binds to loopback only by default.

### GET `/`
Serves `static/index.html`.

### GET `/static/<path>` and GET `/samples/pages/<path>`
Serve static files with correct content types (`html/css/js/svg/json/png/jpeg/webp`). Reject path
traversal (no `..`, absolute paths, or backslashes outside the static roots). `Cache-Control:
no-store` for `/api/*`; `Cache-Control: no-cache` for static assets (local app).

### GET `/api/health`

```json
{
  "ok": true,
  "app": "Paper Trail",
  "version": "1.0.0",
  "backend": "gemini",
  "model": "gemma-4-26b-a4b-it",
  "demoMode": false,
  "hasKey": true,
  "dataDir": "C:\\...\\paper-trail\\data",
  "docs": 2,
  "externalCalls": 0,
  "bytesOut": 0,
  "uptimeSec": 42
}
```

`backend` is `"gemini"` or `"openai"`; `model` is the active model id; `demoMode` follows §2.
`externalCalls` counts all outbound model HTTP requests (transcribe + analyze + retries), and
`bytesOut` counts request body bytes. In demo mode both stay `0`. These power the honesty counter in
the UI.

### GET `/api/stats`

```json
{\"ok\": true, \"externalCalls\": 3, \"bytesOut\": 48211, \"docs\": 2, \"demoMode\": false,
 \"backend\": \"gemini\", \"model\": \"gemma-4-26b-a4b-it\", \"uptimeSec\": 130}
```

### GET `/api/docs`

```json
{\"ok\": true, \"docs\": [DocSummary, ...]}
```

DocSummary:
```json
{\"id\": \"benefits-denial\", \"title\": \"Notice of Adverse Benefit Determination\",
 \"doc_type\": \"insurance denial\", \"page_count\": 2, \"source\": \"sample\",
 \"created_at\": \"2026-10-21T20:30:00Z\", \"preview_text\": \"This letter is our adverse benefit...\",
 \"demoResponses\": true}
```

`source` is `"sample"` or `"upload"`. `demoResponses` is true when `samples/responses.json` has any
recorded response for this doc id (sample docs). Sorted newest first.

### POST `/api/samples/load`

Body: `{\"sample\": \"benefits-denial\"}` — must be one of the ids in `samples/docs.json`.
Copies/references the sample into the local store and returns:

```json
{\"ok\": true, \"doc\": DocDetail, \"replaced\": false}
```

If the same sample id already exists, replace it and set `replaced: true`. Sample docs are read-only
content; pages are served from `/samples/pages/<doc_id>/page-N.png`.

### GET `/api/docs/<id>`

```json
{\"ok\": true, \"doc\": DocDetail}
```

DocDetail:
```json
{
  \"id\": \"benefits-denial\",
  \"title\": \"Notice of Adverse Benefit Determination\",
  \"doc_type\": \"insurance denial\",
  \"agency\": \"Hudson Valley Health Plan\",
  \"doc_date\": \"2026-12-02\",
  \"created_at\": \"2026-10-21T20:30:00Z\",
  \"source\": \"sample\",
  \"page_count\": 2,
  \"preview_text\": \"...\",
  \"warnings\": [],
  \"pages\": [
    {\"n\": 1, \"text\": \"full page text ...\", \"image_url\": \"/samples/pages/benefits-denial/page-1.png\", \"kind\": \"image\"},
    {\"n\": 2, \"text\": \"full page text ...\", \"image_url\": \"/samples/pages/benefits-denial/page-2.png\", \"kind\": \"image\"}
  ]
}
```

For uploaded text/PDF pages without a rendered image, `image_url` is `null` and `kind` is `"text"`.
Uploaded image pages have `image_url` `/api/docs/<id>/pages/<n>` and `kind` `"image"`.

### GET `/api/docs/<id>/pages/<n>`
Serves that page image for an uploaded doc. 404 if no image.

### POST `/api/ingest`

JSON body (base64, not multipart — keeps the stdlib server simple):

```json
{\"files\": [{\"name\": \"letter.png\", \"mime\": \"image/png\", \"data_b64\": \"iVBORw0...\"}]}
```

Rules:
- Max 20 files per request; max `PAPERTRAIL_MAX_FILE_MB` per file (decoded); 413 `too_large`.
- Accepted: `image/png`, `image/jpeg`, `image/webp`, `text/plain`, `text/markdown`,
  `application/pdf`, plus `.txt/.md/.pdf` inferred from name when mime missing.
- Each file becomes one doc. Title = filename without extension, prettified (underscores/dashes →
  spaces, title case) unless a better first line/title is found. `doc_type = "document"`.
- Images: stored as one page each; if live mode and `PAPERTRAIL_TRANSCRIBE=1`, call the model once per
  page to transcribe the visible text (plain text, no commentary) and store it as `page.text`. If
  transcription fails, keep `page.text = ""` and add a warning `"page N could not be transcribed"`;
  the analyze call still receives the page image. In demo mode, uploads return
  `409 demo_uploads` ("Demo mode: uploads are disabled; load a sample case or set an API key.").
- Text/Markdown: split into pages on form feed (`\f`); if no form feed, split into ~4000-char chunks
  at paragraph boundaries. `kind = "text"`, `image_url = null`.
- PDF: best-effort stdlib text extraction (`zlib` FlateDecode + text operators, `Tj`/`TJ`). If text is
  found, pages split as above (`kind = "text"`). If no text is extracted, doc is created with one
  placeholder page (`text = ""`, `warnings += "PDF appears to be scanned; upload page images for
  best results."`).
- 415 `unsupported_type` for other types. 400 `invalid_request` if `files` missing/empty/malformed.
- If the same title exists, append `" (2)"`, `" (3)"` — never overwrite silently.
- Response: `{\"ok\": true, \"docs\": [DocDetail, ...]}`.

### POST `/api/analyze`

```json
{\"doc_id\": \"benefits-denial\", \"mode\": \"explain\", \"lang\": \"en\",
 \"question\": \"Does this mean I lost coverage?\", \"context\": \"I am the patient and I cannot afford the MRI.\"}
```

- `mode` ∈ `explain | deadline | ask | respond` (400 `invalid_request` otherwise).
- `lang` ∈ `en | es | el | bn | zh | ar` (default `en`; 400 otherwise).
- `question` required (non-empty, ≤ 1000 chars) for `ask`; optional otherwise. `context` optional,
  ≤ 2000 chars, used by `respond` and `ask`.
- Doc must exist (404 `not_found`).
- If demo mode:
  - sample doc + recorded response exists for this doc/mode/lang → return it with `"demo": true`.
  - sample doc but no recorded lang → 409 `demo_lang` ("Demo recordings are English-only; set
    GEMINI_API_KEY for other languages.").
  - uploaded doc → 409 `demo_uploads`.
- If live mode: build prompt (§4), call model (retry once on 429/5xx), parse JSON, validate citations
  (§5), return.
- Response:

```json
{
  \"ok\": true,
  \"mode\": \"deadline\",
  \"lang\": \"en\",
  \"demo\": false,
  \"backend\": \"gemini\",
  \"model\": \"gemma-4-26b-a4b-it\",
  \"latency_ms\": 4210,
  \"data\": { ...mode-specific... },
  \"citations\": [{\"page\": 1, \"quote\": \"...\", \"verified\": true, \"location\": \"deadline[0]\"}],
  \"warnings\": [\"citation on page 2 could not be verified against the page text\"]
}
```

Mode-specific `data` contracts (the model must return exactly these keys; unknown keys are kept but
ignored by the UI; missing required keys → 502 `model_format` after one retry):

`explain`:
```json
{\"title\": \"...\", \"doc_type\": \"insurance denial\", \"what_it_is\": \"2-3 plain sentences\",
 \"key_points\": [{\"text\": \"...\", \"citation\": {\"page\": 1, \"quote\": \"exact visible text\"}}],
 \"next_action\": \"one concrete next step\", \"unreadable\": [\"...\"]}
```

`deadline`:
```json
{\"deadlines\": [{\"date\": \"2027-01-31\", \"what\": \"Internal appeal deadline\",
  \"consequence\": \"what happens if missed\", \"action\": \"what to do\",
  \"citation\": {\"page\": 1, \"quote\": \"exact visible text\"}}],
 \"note\": \"one line about urgency or uncertainty\"}
```
Dates must be ISO `YYYY-MM-DD` when the page states a calendar date; if no date is stated use `null`
and say so in `note`. Sort soonest first.

`ask`:
```json
{\"answer\": \"direct answer, 2-6 plain sentences\", \"citations\": [{\"page\": 2, \"quote\": \"...\"}],
 \"missing_info\": \"what the document does not say, or null\"}
```

`respond`:
```json
{\"to\": \"Appeals Department, Hudson Valley Health Plan\", \"subject\": \"...\",
 \"body\": \"ready-to-send letter, plain text with greeting and signature line\",
 \"checklist\": [\"document to gather\", \"...\"], \"send_via\": \"mail / fax / online portal on the notice\",
 \"caution\": \"not medical/legal advice; confirm the address and deadline on the notice\",
 \"citations\": [{\"page\": 1, \"quote\": \"...\"}]}
```

All modes: output JSON first, no markdown fences. Body text in the requested `lang`. Citation quotes
must be copied from the page in the original language (not translated).

Error codes: 400 `invalid_request`, 404 `not_found`, 409 `demo_mode` / `demo_lang` / `demo_uploads`,
413 `too_large`, 415 `unsupported_type`, 429 `upstream_rate_limited`, 502 `model_format` /
`upstream_error`, 503 `no_api_key` (when `PAPERTRAIL_DEMO=0` and no key), 500 `internal`.

### DELETE `/api/docs/<id>` → `{\"ok\": true, \"deleted\": 1}`
### DELETE `/api/docs` → `{\"ok\": true, \"deleted\": N}` (delete all docs + stored files)
### GET `/api/export/<id>.md` (optional, nice-to-have)
Downloadable markdown packet: doc title, pages with page numbers, and the last analysis per mode cached
in memory (not required for MVP; skip if it complicates).

## 4. Model prompting rules (Lead owns; tests may assert contract)

System instruction (summarize): "You are Paper Trail, a careful document explainer. You only state what
is visible in the provided pages. Never invent dates, amounts, addresses, or legal/medical facts. Every
extracted fact must carry a citation {page, quote} where quote is copied verbatim from that page's
text. If the page does not say it, put it in unreadable/missing_info and say so. Plain language, short.
Output JSON only, no markdown fences and no commentary."

User message: mode instruction + language name + page blocks (`[PAGE 1]` + text + page image parts)
+ optional user question/context. Cap pages sent to 10 per call; if more, send first 6 + last 4 and
add a warning. Downscale nothing server-side (browser sends ≤1600px images; samples are 1275px wide).
Use `thinkingConfig.thinkingLevel = "minimal"`, `temperature 0.2`, `maxOutputTokens 2048`.

For `ask`, prefer the pages that contain the question's keywords (simple lexical score) plus all pages
if ≤ 6; still include images for those pages. For `explain`/`deadline`/`respond`, use all pages up to
the cap.

## 5. Citation validation (server-side, always on)

Flatten citations from the mode-specific paths above. For each citation:
- `page` must be an int in `1..page_count`; otherwise `verified = false` and warning.
- Normalize text: Unicode NFKC, lower-case, collapse all whitespace, strip punctuation except
  alphanumerics/spaces; also try matching the quote with `...` and `[ ]` stripped.
- Quote must appear in that page's stored text. If page text is empty (failed transcription / scanned
  PDF), `verified = null` and warning "could not verify citation: page text unavailable".
- Set `verified` on both the nested citation object and the top-level flattened copy; add a warning
  for each unverified citation (max 3 warnings). Never drop a citation from the payload.
- `latency_ms` = wall time of the model call (not including ingest).
- If the model output is not valid JSON or is missing required keys, retry once with a stricter
  "JSON only, this exact schema" message; then return 502 `model_format` with `error.message`
  including a short raw snippet (≤300 chars).

## 6. Demo recordings

`samples/responses.json` is written by the Lead. Shape:

```json
{\"version\": 1, \"responses\": {\"benefits-denial|explain|en\": {\"data\": {...}, \"warnings\": []}, ...}}
```

Server merges `demo: true`, `mode`, `lang`, `backend: \"demo\"`, `model: \"recorded\"`,
`latency_ms: 0`, validated citations, and returns the same Analyze response shape. Never include raw
recordings from uploaded docs.

## 7. Frontend requirements (for the frontend owner)

Static only: `index.html`, `app.js`, `style.css`, plus optional `manifest.webmanifest` and `icon.svg`.
No build step, no external URLs, no frameworks. Must pass `node --check static/app.js`.

The UI is a calm "private desk" (paper palette: warm off-white background, ink text, deep green
accent, amber warnings; serif display headings via `Georgia, 'Times New Roman', serif`; system sans
body). It must be usable at 1280×800 and on a phone.

Required behavior:
- Header: **Paper Trail** + tagline; status pills from `/api/health`: backend (`Demo mode` amber,
  `Gemma 4 · hosted` green, `Local model` blue), and an external-calls counter from `/api/stats`
  (`0 external calls` green, otherwise grey). Buttons: `Upload`, `Delete all`.
- Sidebar: document list from `/api/docs` (title, doc_type, page count, sample badge, active state,
  per-doc delete ×). Empty state offers 3 sample-case buttons: `Insurance denial`, `SNAP
  recertification`, `Immigration RFE` (ids from contract). Footer note "Data stays in ./data".
- Empty main: welcome card with the privacy promise, "Load a sample case" buttons, and a drop zone.
- Document view: page viewer (page image; prev/next; page n/n; if `image_url` is null render the page
  text in a `<pre>`; citation clicks jump to page and show the quoted line in a "Cited from page N"
  bar under the viewer), doc meta (source, doc_type, pages), and analysis panel.
- Analysis panel: mode tabs `Explain · Deadline · Ask · Respond`; language `<select>` with
  English/Español/Ελληνικά/বাংলা/中文/العربية; question input when `ask` (prefill "What do I need to
  do?"); optional context textarea for `respond`; `Analyze` button with loading state; result
  renders per mode:
  - explain: what_it_is, key points with citation chips, next_action callout, unreadable list.
  - deadline: cards sorted soonest with date, what, consequence, action, citation chips, note.
  - ask: answer, citation chips, missing_info.
  - respond: To/Subject, letter body in `<pre>` with `Copy` and `Download .txt` buttons, checklist,
    send_via, caution, citation chips.
  Every citation chip: `p.N` plus a small verified (✓ green) / unverified (! amber) mark, and
  `title` attribute containing the quote; clicking sets viewer page and shows quote bar.
  Show `Read aloud` for the result (SpeechSynthesis; no external TTS).
- Demo mode: visible amber banner "Demo mode — bundled sample cases with recorded answers. Set
  GEMINI_API_KEY for live analysis." and disable uploads with a clear message; language selector may
  stay enabled, but on 409 show the server message.
- Errors: inline error card with the server `error.message`, plus Retry for analyze. Never alert().
- Upload: drag-and-drop over the main area + file input (accept `.png,.jpg,.jpeg,.webp,.txt,.md,.pdf`);
  show per-file progress/failed state; after success select the first new doc and refresh the list.
  Base64 encode client-side (FileReader) and POST `/api/ingest`.
- Privacy footer/strip: "Local store: ./data · No telemetry · External calls: N · Delete everything"
  and a link/button `How it works` opening a small dialog explaining hosted API now / local model
  later, citations, and the "model reads, code checks" split.
- Keyboard: `1`–`4` switch modes, `Ctrl/Cmd+Enter` analyze, `Escape` closes the how-it-works dialog.
- URL `?sample=benefits-denial` auto-loads that sample on boot; `?demo=1` is a no-op.
- Optional PWA: manifest + icon; no service worker required.
- Accessibility: real buttons/labels, `aria-live="polite"` for results and errors, focus-visible
  styles, no color-only meaning.

Never fetch external resources; no inline third-party scripts. All API paths are relative
(`/api/...`). Assume the server is same-origin.

## 8. Tests (for the tests owner)

`tools/mock_gemini.py`: stdlib HTTP server. Accepts
`POST /v1beta/models/<model>:generateContent` with `x-goog-api-key` header. Reads the prompt text and:
- if it contains `TRANSCRIBE_PAGE` → return a plain-text transcription (echo the known page text from
  the request if provided, else "SAMPLE PAGE TEXT").
- if it contains `MODE: explain` etc. → return valid JSON text for that mode, using quotes that appear
  in the prompt (so citation validation passes). Put the JSON in
  `candidates[0].content.parts[0].text` as a JSON string.
- `MOCK_MODE=badjson` env → return a non-JSON string; `MOCK_MODE=error` → return HTTP 429/502.
- Support a `/health` endpoint for the test harness. Print the chosen port when bound (or accept
  `--port`).

`tools/selftest.py` (stdlib, prints `PASS`/`FAIL` per check, exits non-zero on failure) must:
1. Start `mock_gemini.py` on a free port; start `server.py` as a subprocess with a temporary
   `PAPERTRAIL_DATA_DIR`, `GEMINI_API_KEY=test`, `PAPERTRAIL_API_BASE=http://127.0.0.1:<mockport>`,
   `PAPERTRAIL_MODEL=test-model`, random free `PAPERTRAIL_PORT`, wait for `/api/health`.
2. Live mode: health shape + demoMode false; load each sample via `/api/samples/load`; GET docs;
   analyze all four modes on `benefits-denial` (English), assert required keys, citations flattened,
   `verified === true` for known quotes, no `demo`; analyze `ask` with a question; analyze `es`; check
   externalCalls/bytesOut > 0; ingest a `.txt` file via base64; analyze explain; delete it; delete all;
   invalid mode/lang/doc/oversize/bad-JSON checks (400/404/413/415).
3. Demo mode: stop server; restart with no key and `PAPERTRAIL_DEMO=1`; health demoMode true; load
   sample; analyze explain returns `demo: true` with recorded data; analyze `es` returns 409
   `demo_lang`; upload returns 409 `demo_uploads`; stats externalCalls == 0.
4. Static: GET / contains "Paper Trail"; GET /static/app.js 200; traversal `/static/../server.py`
   returns 400/404.
5. Always terminate both subprocesses and remove the temp data dir. Print a summary and total time.

`tools/check_api.py`: makes one real hosted analyze call end-to-end (load sample → explain). Prints
model, HTTP status, latency, first line of the answer. Handles 400/402/429 with actionable hints
(legacy key, billing, rate limit) and exits non-zero on failure.

`tools/eval.py` (optional if time): load `eval/gold.json` (Lead writes) and compare extracted
deadlines/amounts to gold on the sample docs with live API; print a table and pass/fail.

## 9. Acceptance criteria (Lead will verify)

- `python server.py` with no key: loads, warns demo mode in health, samples load and all four modes
  return recorded answers; upload returns a clear 409; no external calls.
- With mock key: selftest passes all checks.
- `node --check static/app.js` passes.
- No file writes outside `paper-trail/data/` at runtime; deleting all docs removes files.
- README quickstart works from a clean clone; `tools/check_api.py` is the only thing that needs a key.
- The sample pages visibly say "SAMPLE — synthetic document" and the UI repeats it.
- No telemetry, no external fetch in the frontend, API key never returned to the client.
