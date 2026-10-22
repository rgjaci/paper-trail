# Demo script — Paper Trail (90 seconds)

## Pre-demo checklist (10 minutes before)

```powershell
cd paper-trail
python tools/selftest.py          # must print a green summary
python tools/check_api.py         # optional; live path only (needs GEMINI_API_KEY)
python server.py                  # http://127.0.0.1:8765
```

Open <http://127.0.0.1:8765/?sample=benefits-denial> so the insurance-denial sample is loaded before
you start talking. Load the other two samples once (`SNAP recertification`, `Immigration RFE`) so the
sidebar is ready.

Have ready:

- A browser window at 1280×800, one tab, no dev tools.
- The sample pages (synthetic, clearly marked) — do **not** use anyone's real mail.
- A kill switch: if the API fails, the app still runs. In demo mode the recorded answers work with
  Wi-Fi off; say "demo mode" out loud rather than showing an error.

**Timing:** in live mode a 2-page Explain takes roughly 10–20 s on the free tier (measured ~18–20 s),
and Ask ~7 s. For a tight 90-second demo, run in **demo mode** (recorded answers are instant, and the
zero-external-calls story is stronger), or pre-warm one live call and narrate through its latency.
Do not start a live call 5 seconds before you need the answer.

## The 90 seconds

| Time | Do | Say |
| --- | --- | --- |
| 0:00 | Show the app title and the `0 external calls` pill. | "This is Paper Trail. It's a private desk for the paper that runs your life — denials, benefits notices, immigration mail." |
| 0:08 | Click the loaded **Insurance denial** sample, page 1 is visible. | "Here's a real-shaped notice: an MRI denied as 'not medically necessary'. The useful information is buried." |
| 0:15 | Click **Explain**. (#1) | "Explain gives me what actually matters: what it is, what I owe, the appeal deadline, and what to attach." |
| 0:28 | Point at a citation chip (green ✓), click it. | "Every fact has a citation back to the page, and the server re-checked that quote against the page text. Green means verified. It's not a summary I have to trust — I can audit it." |
| 0:38 | Click **Deadline**. (#2) | "Deadline turns the letter into dates: the appeal must be received by January 31, 2027, and here's what happens if it isn't." |
| 0:47 | Click **Ask**, keep the prefilled "What do I need to do?", Analyze. (#3) | "Ask is the question I actually have. It answers from this document only, and tells me what the document does *not* say." |
| 0:58 | Click **Respond**. (#4) | "Respond drafts the appeal letter, the document checklist, where to mail it, and a caution that this isn't legal advice." |
| 1:08 | Switch language to **Español**, run **Explain** again (live mode) — or in demo mode show the recorded English answer and mention the live path. | "Same document, my mother's language. The citations stay in the original script." |
| 1:18 | Switch to the **SNAP** sample and run **Deadline** (or show the third case). | "Same app, different paperwork: recertification interview, document list, the date benefits stop." |
| 1:27 | Cut to `server.py` + `skills/SKILL.md` in an editor; then the privacy strip. | "The whole app is Python standard library plus static files. The task contracts are portable SKILL.md files. Data stays in ./data; there is no telemetry; in demo mode the external-call counter is zero." |

## If the model API fails

1. Say "demo mode" and keep going: sample cases serve recorded answers with no network.
2. Show the external-call counter at **0** — that is the app's honest offline proof.
3. Do not debug the API on stage. The message is the product: *the document stays local, and the
   interface works even when the network doesn't.*

## Judge / audience Q&A

- **Why not just ChatGPT?** Uploading a denial, lab letter, or immigration notice to a chat service is
  the wrong default for the people who need help most. Paper Trail is built around "the document does
  not leave this machine", and the citations are verified against the page, not just generated.
- **Which model?** `gemma-4-26b-a4b-it` on the Gemini API today; Apache 2.0 Gemma 4 weights; the same
  adapter runs a local 12B via LiteRT-LM/Ollama by changing one environment variable.
- **What's actually open source?** The app (MIT), the prompts, the citation verifier, the sample
  documents, the test suite, and the portable `SKILL.md` contracts.
- **How is this different from "chat with your PDF"?** Four task-shaped modes instead of a chat box;
  citations validated against the page; deadlines converted to a sorted action list; a drafted reply
  with a checklist; demo mode that works offline; privacy defaults.
- **What's the hard part?** Restraint and citation discipline: the model must refuse to invent a
  deadline or amount, and the code must prove the quote exists. The eval harness scores exactly that.
- **What would you do with a week?** Multi-document case files and retrieval with EmbeddingGemma 2,
  a 12B local build as the default, community `SKILL.md` packs for local document types, and a
  legal-aid/clinic deployment path.
- **Is it safe for medical/legal documents?** The app explains and drafts; it does not advise and it
  routes high-stakes decisions to professionals. Local mode makes the privacy claim real. The hosted
  API is for prototyping; free-tier prompts may be human-reviewed, so use paid or local for real data.

## Submission blurb (paste-ready)

> Paper Trail is an open-source, local-first document desk for the paperwork that runs your life:
> insurance denials, benefits recertifications, and immigration requests. Drop in a photo, PDF, or
> text file and get a plain-language explanation, a sorted deadline list, cited answers to your
> questions, and a drafted reply with a checklist. Built with Gemma 4 (`gemma-4-26b-a4b-it`) through
> the Gemini API, with a zero-dependency Python stdlib backend and a vanilla-JS UI. Every extracted
> fact carries a page citation that the server re-checks against the page text. Documents are stored
> only in a local folder; no telemetry, no accounts, no cloud storage; a demo mode with recorded
> sample answers runs with the network off. The app is MIT licensed, ships portable `SKILL.md`
> contracts, and swaps to a local Gemma 4 12B via one environment variable.
