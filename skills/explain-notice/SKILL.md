---
name: explain-notice
description: Explain a notice, denial letter, bill, or government mail in plain language: what it is, the facts that change what the reader does, and one concrete next action. Use when the reader has a document but does not understand what it wants from them.
version: 1.0.0
license: MIT
model: gemma-4-26b-a4b-it
---

# Explain a notice

One document in, one plain-language explanation out.

## Use when

- The document is confusing, bureaucratic, in a second language, or written for someone else.
- The reader needs the *meaning*, not an OCR dump: what it is, what it wants, what to do next.
- The stakes are real: money, housing, benefits, immigration status, health coverage.

## Inputs

`pages` (images and/or text), `lang` (default `en`), optional `context`.

## Output

```json
{
  "title": "short human title",
  "doc_type": "insurance denial | benefits recertification | immigration request | bill | lease | other",
  "what_it_is": "2-3 plain sentences",
  "key_points": [
    {"text": "a fact that changes what the person should do",
     "citation": {"page": 1, "quote": "exact quote"}}
  ],
  "next_action": "one concrete next step",
  "unreadable": ["parts that could not be read"]
}
```

## Rules

1. 3-5 key points, prioritizing deadlines, amounts, appeal/response rights, required documents,
   contact details, and consequences of doing nothing.
2. Every key point carries a citation copied verbatim from the page. No citation, no claim.
3. If the page is unreadable, say which part; never guess.
4. Name the document type honestly. If it mixes types, say so.
5. End with one action the person can actually take today.
6. Not legal, medical, or immigration advice. Say so when the decision is high-stakes.
