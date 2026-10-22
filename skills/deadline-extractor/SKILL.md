---
name: deadline-extractor
description: Extract every deadline, amount, and required action from a notice or letter, with page citations and the consequence of missing each date. Use when the user asks "what do I need to do and by when?" about a real document.
version: 1.0.0
license: MIT
model: gemma-4-26b-a4b-it
---

# Deadline extractor

Turns bureaucratic dates into a short, sorted, cited list.

## Use when

- The document contains dates, due dates, appeal windows, interview times, or renewal periods.
- Missing a date has a concrete cost (denied appeal, stopped benefits, missed filing).
- The user needs a checklist, not a narrative.

## Inputs

`pages` (images and/or text), `lang`, optional `context`.

## Output

```json
{
  "deadlines": [
    {"date": "YYYY-MM-DD or null",
     "what": "what is due or what happens",
     "consequence": "what happens if it is missed",
     "action": "what to do before then",
     "citation": {"page": 1, "quote": "exact quote"}}
  ],
  "note": "one line on urgency, uncertainty, or missing dates"
}
```

## Rules

1. ISO `YYYY-MM-DD` for every stated calendar date. If the document states a relative window
   ("within 60 days of this notice") and the notice date is visible, compute the date and explain the
   basis in `what`; if the date cannot be computed, use `null` and say so in `note`.
2. Sort soonest first. Include time-of-day in `what` when the document states it.
3. One deadline per concrete event (interview, filing, appeal, recertification). Do not merge.
4. Consequences must come from the page or be marked as "the notice does not say".
5. If there are no deadlines, return an empty list and say that in `note` - do not invent urgency.
