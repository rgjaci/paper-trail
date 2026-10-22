# Privacy

Paper Trail exists because some documents should never be handed to a chat service. This page says
exactly what the app does with your data, what it sends where, and where the honest limits are.

## The short version

- **Documents stay in a local folder**: `paper-trail/data/`. The app has no cloud storage, no account,
  no sync, and no analytics.
- **The UI makes no external requests.** No CDNs, no fonts fetched from the network, no tracking
  pixels. Everything is served by `server.py` from `static/`.
- **There is exactly one kind of outbound request**: a model call, and only when you click Analyze or
  upload a photo in live mode. The external-call counter in the UI is read from the server, not
  guessed.
- **Demo mode** (no API key) makes **zero** network calls: the bundled sample cases serve recorded
  answers. That is the offline proof.
- **Delete everything** removes the local index and all uploaded page files. Sample cases are
  read-only synthetic content and are restored only if you load them again.

## What is stored locally

| Data | Where | Notes |
| --- | --- | --- |
| Document index | `data/docs.json` | Titles, types, page text, timestamps |
| Uploaded page images | `data/docs/<doc_id>/page-N.png` | Only files you upload |
| Model answers | in memory only | Not written to disk by the server |
| Sample cases | `samples/` | Synthetic; no personal data |
| Settings | browser localStorage | Last selected language only |

There is no database server, no background sync, and no file written outside `data/` and the
browser's own storage. Deleting `data/` (or clicking **Delete everything**) resets the app.

## What is sent to the model (live mode only)

When you analyze a document, the server sends that document's page text and page images (up to 10
pages) plus your question/context to the configured model endpoint:

- **Hosted default:** Google Gemini API, model `gemma-4-26b-a4b-it`. The request carries your API key
  in an `x-goog-api-key` header. The key never reaches the browser.
- **Local option:** any OpenAI-compatible server you point it at (`PAPERTRAIL_BACKEND=openai`), e.g.
  Ollama or LiteRT-LM on your own machine. In that case no document data leaves the machine.

**Do not analyze other people's sensitive documents on a free API tier.** Free-tier Gemini API
prompts and outputs may be human-reviewed and used to improve products; paid-tier data is not, and
some regions are paid-only. For real medical, legal, immigration, or client documents, use the local
model path (or a paid, contractual endpoint) and get consent where appropriate.

The app never sends: your other documents, filenames, the local store, analytics events, or anything
from the sample cases you did not open.

## What the app does not do

- No telemetry, crash reporting, analytics SDKs, or "anonymous usage data".
- No accounts, sign-in, or cloud backup.
- No automatic uploads. A document is read by the model only when you click an analyze action.
- No third-party scripts in the UI, no remote assets, no service worker phoning home.
- No selling, sharing, or retaining data beyond the local folder.

## Security caveats (be honest)

- **Local files are not encrypted by default.** The app relies on the operating system: use full-disk
  encryption (BitLocker/FileVault/LUKS) and a locked screen. Encryption at rest is on the roadmap.
- **The server has no authentication** and binds to `127.0.0.1` by default. Do not expose it to a
  network or the internet. Anyone who can use the machine can read `data/`.
- **The model provider sees the pages you analyze in live mode.** That is inherent to hosted
  inference; the local backend is the way to avoid it.
- **Citations are verified, not infallible.** The server proves a quote appears in the stored page
  text; it cannot prove the interpretation is correct or that the document is authentic.
- **No legal/medical/immigration advice.** Paper Trail explains documents and drafts replies. High-
  stakes decisions belong with a professional or a legal-aid organization.

## Data deletion

- Single document: hover the document in the sidebar and click **×**.
- Everything: **Delete all** in the header (or `DELETE /api/docs`).
- Manual: stop the server and delete `paper-trail/data/`.

## For auditors

- The only outbound call sites are in `ModelClient` (`server.py`): Gemini native and OpenAI-compatible.
- Counters are incremented immediately before each request; `/api/stats` exposes the totals.
- The UI has no external `src`, `href`, `url()`, or `fetch` other than same-origin `/api/*`.
- Run `grep -RIn "http" static/` and `grep -n "urlopen" server.py` to inspect.
