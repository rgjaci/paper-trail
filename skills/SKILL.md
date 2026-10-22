---
name: paper-trail
description: A local-first document desk for bills, benefits notices, insurance denials, and immigration mail. Use when someone needs a real paper document explained, its deadlines extracted with page citations, questions answered from the document, or a reply drafted - without uploading sensitive documents to a cloud service.
version: 1.0.0
license: MIT
model: gemma-4-26b-a4b-it
---

# Paper Trail

Paper Trail turns a folder of paper into a small set of decisions: *what is this, what is the
deadline, what do I do, and what do I send back?* It is built for the documents people avoid -
insurance denials, benefits recertifications, immigration requests, leases, bills - and for the
people who help them: family members, social workers, legal-aid clinics, librarians.

The app runs locally (`python server.py`), stores documents under `./data`, and calls Gemma 4 through
the hosted Gemini API for prototyping today. The same contract runs against a local model later
(LiteRT-LM, Ollama, llama.cpp) or a local OpenAI-compatible server.

## When to use this skill

Use it when **all** of these are true:

- The input is a real-world document: a notice, letter, bill, denial, lease, government mail.
- The user needs the meaning, a deadline, a required action, or a reply - not a summary of pixels.
- The document is sensitive enough that "upload it to a chat service" is the wrong answer.

Do **not** use it for: general OCR transcription, contracts that need attorney review, medical
diagnosis, or deciding eligibility on someone's behalf. It explains and drafts; it does not advise.

## Inputs

| Field | Required | Notes |
| --- | --- | --- |
| `pages` | yes | Page images (JPEG/PNG/WebP, downscaled to <= 1600 px) **and/or** extracted page text. |
| `mode` | yes | `explain`, `deadline`, `ask`, `respond`. |
| `lang` | no | `en`, `es`, `el`, `bn`, `zh`, `ar`. Default `en`. |
| `question` | for `ask` | Free text, <= 1000 chars. |
| `context` | no | The person's situation, <= 2000 chars, used for tone and targeting only. |

## Modes

- **explain** - what the paper is, 3-5 facts that change what the person does, one next action.
- **deadline** - every date that matters, the consequence of missing it, and the action to take;
  ISO dates, sorted soonest first.
- **ask** - the direct answer to one question, with page citations, and an explicit "what this
  document does not say" field.
- **respond** - a ready-to-send reply, a checklist of documents to gather, where to send it, and a
  caution line.

## Output contract

The model returns one JSON object per mode. Every extracted fact carries:

```json
{"page": 1, "quote": "text copied verbatim from that page"}
```

The server re-checks each quote against the stored page text and marks it `verified: true/false/null`.
Quotes are always in the document's original language; the surrounding answer is in `lang`.

## Rules

1. Never invent a date, amount, address, phone number, or consequence. If the page does not say it,
   put it in `unreadable` or `missing_info` and say so.
2. Lead with the practical meaning: the deadline, the fee, the required document, the phone number.
3. Plain language, roughly 6th-grade reading level. No jargon, no legalese unless quoted.
4. Explain and draft. Do not give legal, medical, or immigration advice, do not promise outcomes, and
   say when a professional or legal-aid organization should be involved.
5. On a missing or unreadable page, say which part is missing; do not fill the gap from memory.
6. The document never leaves the machine it is opened on. The app makes no analytics calls.

## How it is packaged

- **Local app**: `python server.py`, then <http://127.0.0.1:8765>. Upload a photo/PDF/TXT, pick a
  mode, get cards with citations.
- **Agent tool**: expose `analyze(doc_id, mode, lang, question, context)` over the app's
  `POST /api/analyze`. Any harness that can call an HTTP tool can use the same contract.
- **Companion skills**: `explain-notice`, `deadline-extractor`, `ask-with-citations`,
  `appeal-drafter` are narrower versions for harnesses that route by task.

## Local-first path

| Layer | Today | Later |
| --- | --- | --- |
| Model | `gemma-4-26b-a4b-it` via Gemini API | 12B Unified on a 16 GB laptop, E4B on a phone (LiteRT-LM) |
| Embeddings | not required for a single doc | EmbeddingGemma 2 for multi-document retrieval |
| Storage | SQLite-free JSON + files under `./data` | same, encrypted at rest |
| Demo | recorded sample answers, no network | local model, no network |

The `SKILL.md` file itself is portable Markdown. The model, server and UI are MIT-licensed.
