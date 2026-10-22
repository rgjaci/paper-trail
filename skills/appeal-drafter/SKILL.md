---
name: appeal-drafter
description: Draft a ready-to-send reply to a denial, benefits notice, or request for evidence, plus the checklist of documents to attach and where to send it. Use when the user needs to respond to a document and wants a starting letter with placeholders.
version: 1.0.0
license: MIT
model: gemma-4-26b-a4b-it
---

# Appeal / response drafter

A letter the user can edit and send, grounded in the document's own instructions.

## Use when

- The document invites an appeal, response, recertification, or request for evidence.
- The user needs a starting draft and a list of what to attach.
- The document shows where and how to send the response.

## Inputs

`pages` (images and/or text), `lang`, optional `context` (the person's situation - job, dates, what
they can attach, what they cannot find).

## Output

```json
{
  "to": "recipient name/office from the document, or null",
  "subject": "subject line",
  "body": "plain-text letter with greeting, 2-4 short paragraphs, and a signature block with placeholders",
  "checklist": ["document or piece of information to gather/attach"],
  "send_via": "mail / fax / online portal / phone, with the address or number from the document",
  "caution": "verify address, deadline and facts; not legal advice",
  "citations": [{"page": 1, "quote": "exact quote"}]
}
```

## Rules

1. Cite the document's own instructions: the address, the deadline, required documents, the
   receipt/member/case number, the phone number.
2. Use placeholders like `[Your name]`, `[Phone]`, `[Date]` for facts about the sender - never invent
   them.
3. Keep the letter short and factual: who I am, what I received, what I am asking for, what I enclose.
4. The checklist must match the document's requirements; add a "keep a copy" item.
5. The caution line is mandatory and must say this is not legal, medical, or immigration advice.
6. Do not promise an outcome or threaten; write in the tone of a reasonable person asking for a
   review.
