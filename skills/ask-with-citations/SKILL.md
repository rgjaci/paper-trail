---
name: ask-with-citations
description: Answer one question about a document using only the document's text, with page citations and an explicit statement of what the document does not answer. Use for questions like "does this mean I lost coverage?" or "is this a denial or a request for more information?".
version: 1.0.0
license: MIT
model: gemma-4-26b-a4b-it
---

# Ask a document, with citations

A narrow, honest answer to one question - not a summary of the whole page.

## Use when

- The user has a specific question about one document.
- A wrong answer has a cost (coverage, deadline, status, money).
- The user needs to know both the answer *and* whether the document actually answers it.

## Inputs

`pages` (images and/or text), `question` (required, <= 1000 chars), `lang`, optional `context`.

## Output

```json
{
  "answer": "direct answer, 2-6 plain sentences",
  "citations": [{"page": 1, "quote": "exact quote"}],
  "missing_info": "what the document does not say, or null"
}
```

## Rules

1. Answer the question asked. Do not pad with a summary of the document.
2. Use only the provided pages. If the answer is not there, say so plainly and set `missing_info`.
3. Quote the passage the answer comes from, verbatim. If the answer is an inference from two
   passages, cite both.
4. Do not translate citation quotes; write the answer in `lang`.
5. If the question is actually about eligibility, medical advice, or a legal conclusion, explain what
   the document says and recommend a professional - do not decide for the user.
6. Never claim the document says something it does not.
