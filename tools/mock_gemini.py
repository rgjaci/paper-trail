#!/usr/bin/env python3
"""Offline stand-in for the Gemini ``generateContent`` endpoint.

Used by ``tools/selftest.py`` so the full Paper Trail HTTP API can be tested
without a network or a real API key.

    python tools/mock_gemini.py --port 0
    MOCK_PORT=53124

Endpoints
---------
GET  /health
    Liveness probe plus a small amount of test state.
POST /v1beta/models/<model>:generateContent
    A fake Gemini reply.  The request body is read as Gemini JSON
    (systemInstruction + contents), the prompt text is inspected, and the
    reply text is generated to match what the server asked for.

Environment
-----------
MOCK_MODE=ok        (default) answer normally
MOCK_MODE=badjson   valid Gemini envelope whose text is deliberately not JSON
MOCK_MODE=error     first generateContent request -> HTTP 429 JSON error,
                    every later request answers normally (tests one retry)

Behavior
--------
* Prompt contains ``TRANSCRIBE_PAGE`` -> plain-text transcription, never JSON.
* Prompt contains ``MODE: explain|deadline|ask|respond`` -> that mode's exact
  JSON contract is returned as the text of ``candidates[0].content.parts[0]``.
* Citation quotes are taken from real lines inside the prompt's ``[PAGE n]``
  blocks, so the server's citation validation should mark them verified.
* Missing / malformed request bodies -> 400 with a Gemini-style error object.

Standard library only.  Binds loopback by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOST = "127.0.0.1"
DEFAULT_PORT = 0  # 0 = ask the OS for a free port
MAX_BODY = 64 * 1024 * 1024
GENERATE_RE = re.compile(r"^/v1beta/models/([^/]+):generateContent$")
PAGE_RE = re.compile(r"\[PAGE\s+(\d+)[^\]]*\]", re.IGNORECASE)
MODE_RE = re.compile(r"\bMODE\s*:\s*(explain|deadline|ask|respond)\b", re.IGNORECASE)

# Lines like "MODE: explain", "QUESTION: ..." are prompt scaffolding, not page
# text.  The server appends them after the page blocks, so page extraction must
# stop there or a question could be mistaken for a quotable line.
SCAFFOLD_RE = re.compile(
    r"^(?:USER\s+)?(?:QUESTION|CONTEXT|LANGUAGE|LANG|MODE|TASK|"
    r"OUTPUT(?:\s+FORMAT)?|SYSTEM(?:\s+INSTRUCTION)?|INSTRUCTIONS?)\s*[:\-]",
    re.IGNORECASE,
)

QUOTE_KEYWORDS = ("must", "deadline", "denied", "appeal")
MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2}),\s*(\d{4})\b",
    re.IGNORECASE,
)
SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

STATE = {
    "mode": "ok",
    "requests": 0,
    "error_sent": False,
    "last_path": None,
}
LOCK = threading.Lock()
VERBOSE = False


# ----------------------------------------------------------------------------------
# Prompt inspection
# ----------------------------------------------------------------------------------


def _text_parts(obj):
    """Collect text from a Gemini {parts: [...]} object."""
    found = []
    if not isinstance(obj, dict):
        return found
    parts = obj.get("parts")
    if not isinstance(parts, list):
        return found
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            found.append(part["text"])
    return found


def collect_prompt(body):
    """Return all text the app sent, from the system instruction and contents."""
    chunks = []
    system = body.get("systemInstruction")
    if system is None:
        system = body.get("system_instruction")
    chunks.extend(_text_parts(system))
    contents = body.get("contents")
    if isinstance(contents, list):
        for content in contents:
            chunks.extend(_text_parts(content))
    return "\n".join(chunks)


def extract_pages(prompt):
    """Return [(page_number, page_text), ...] parsed from ``[PAGE n]`` markers."""
    matches = list(PAGE_RE.finditer(prompt or ""))
    pages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        block = prompt[match.end():end]
        kept = []
        for line in block.splitlines():
            if SCAFFOLD_RE.match(line.strip()):
                break
            kept.append(line)
        pages.append((int(match.group(1)), "\n".join(kept).strip()))
    return pages


def choose_quote(text):
    """Pick a verbatim line from page text, preferring decision-ish language."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    # Prefer a real sentence containing a decision/deadline word; fall back to
    # shorter headings, then to any usable line.
    for min_len in (20, 8):
        for line in lines:
            if len(line) >= min_len and any(word in line.casefold() for word in QUOTE_KEYWORDS):
                return line[:300]
    for min_len in (20, 8):
        for line in lines:
            if len(line) >= min_len:
                return line[:300]
    if lines:
        return lines[0][:300]
    return ""


def citations_from_prompt(prompt):
    """One verifiable citation per page that has usable text (at least one)."""
    found = []
    for page_no, text in extract_pages(prompt):
        quote = choose_quote(text)
        if quote:
            found.append({"page": page_no, "quote": quote})
    if not found:
        # The server is supposed to send [PAGE n] blocks; keep a sane fallback
        # so a malformed prompt still produces contract-shaped JSON.
        quote = choose_quote(prompt)
        if quote:
            found.append({"page": 1, "quote": quote})
        else:
            found.append({"page": 1, "quote": "SAMPLE PAGE TEXT"})
    return found


def _parse_date(text):
    """Best-effort ISO date extraction from a line of page text."""
    for match in ISO_DATE_RE.finditer(text or ""):
        try:
            return date.fromisoformat(match.group(1)).isoformat()
        except ValueError:
            continue
    match = MONTH_RE.search(text or "")
    if match:
        month = MONTHS.get(match.group(1).lower().rstrip("."))
        day = int(match.group(2))
        year = int(match.group(3))
        try:
            return date(year, month, day).isoformat()
        except (TypeError, ValueError):
            pass
    match = SLASH_DATE_RE.search(text or "")
    if match:
        try:
            return date(int(match.group(3)), int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            pass
    return None


def find_deadline_date(prompt):
    """Find the most deadline-looking date in the page blocks."""
    pages = extract_pages(prompt)
    markers = (
        "deadline",
        "must be",
        "must submit",
        "no later",
        "due by",
        "due on",
        "submit by",
        "received by",
        "respond by",
    )
    for _, text in pages:
        for line in text.splitlines():
            low = line.casefold()
            if any(marker in low for marker in markers):
                parsed = _parse_date(line)
                if parsed:
                    return parsed
    for _, text in pages:
        parsed = _parse_date(text)
        if parsed:
            return parsed
    return None


def _citation(citations, index):
    item = citations[index % len(citations)]
    return {"page": item["page"], "quote": item["quote"]}


def build_mode_data(mode, prompt):
    """Return the exact mode-specific data object required by CONTRACT.md §3."""
    citations = citations_from_prompt(prompt)
    primary = _citation(citations, 0)
    secondary = _citation(citations, 1)

    if mode == "explain":
        return {
            "title": "Mock sample notice",
            "doc_type": "document",
            "what_it_is": (
                "This is a sample notice created for offline testing. It describes a decision "
                "and the steps the reader can take next."
            ),
            "key_points": [
                {
                    "text": "The notice states a decision or requirement that affects what the reader should do.",
                    "citation": dict(primary),
                },
                {
                    "text": "The reader can respond or act by following the instructions printed on the notice.",
                    "citation": dict(secondary),
                },
            ],
            "next_action": "Read the notice carefully, confirm the deadline, and follow its instructions.",
            "unreadable": [],
        }

    if mode == "deadline":
        deadline_date = find_deadline_date(prompt)
        if deadline_date:
            note = "Mock response for offline testing; confirm this date on the notice."
        else:
            note = "No calendar date was found in the page text; confirm any deadline with the agency."
        return {
            "deadlines": [
                {
                    "date": deadline_date,
                    "what": "Date or deadline stated in the notice",
                    "consequence": "Missing the deadline may affect the reader's rights or benefits.",
                    "action": "Respond using the instructions on the notice before the date.",
                    "citation": dict(primary),
                }
            ],
            "note": note,
        }

    if mode == "ask":
        return {
            "answer": (
                "The notice explains the decision and what the reader can do next. "
                "The cited page contains the passage the answer is based on. "
                "This is a mock answer for offline testing."
            ),
            "citations": [dict(primary)],
            "missing_info": "The notice may not answer every detail; confirm with the agency.",
        }

    if mode == "respond":
        return {
            "to": "Appeals Department",
            "subject": "Response to the enclosed notice",
            "body": (
                "Dear Appeals Department,\n\n"
                "I am writing in response to the notice I received. Please find the requested "
                "information and documents enclosed.\n\n"
                "Please contact me if you need anything else.\n\n"
                "Sincerely,\n[Your name]\n[Phone]"
            ),
            "checklist": [
                "A copy of the notice",
                "The documents the notice asks for",
                "Your contact information",
            ],
            "send_via": "Use the address, fax number, or online portal printed on the notice.",
            "caution": (
                "This is a mock draft for testing only; it is not legal or medical advice. "
                "Confirm the address and deadline on the notice."
            ),
            "citations": [dict(primary)],
        }

    raise ValueError("unknown mode: {}".format(mode))


# ----------------------------------------------------------------------------------
# Reply helpers
# ----------------------------------------------------------------------------------


def gemini_envelope(text, model, prompt_chars=0):
    """A realistic Gemini generateContent response envelope."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": text}],
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": max(1, prompt_chars // 4),
            "candidatesTokenCount": max(1, len(text) // 4),
            "totalTokenCount": max(1, prompt_chars // 4) + max(1, len(text) // 4),
        },
        "modelVersion": model or "mock-model",
    }


def error_envelope(code, message, status):
    return {"error": {"code": code, "message": message, "status": status}}


def validate_body(body):
    if not isinstance(body, dict):
        return "Request body must be a JSON object."
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents:
        return "contents must be a non-empty array."
    for content in contents:
        if not isinstance(content, dict):
            return "contents entries must be objects."
        if not isinstance(content.get("parts"), list):
            return "contents[].parts must be an array."
    return None


# ----------------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperTrailMockGemini/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib API
        if VERBOSE:
            sys.stderr.write("[mock] " + (fmt % args) + "\n")

    def _send_json(self, payload, status=200, extra_headers=None):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, status, code, message, gemini_status):
        self._send_json(error_envelope(code, message, gemini_status), status)

    # -- GET -----------------------------------------------------------------------

    def do_GET(self):  # noqa: N802 - stdlib API
        path = urlsplit(self.path).path
        if path == "/health":
            with LOCK:
                payload = {
                    "ok": True,
                    "mock": True,
                    "mode": STATE["mode"],
                    "requests": STATE["requests"],
                    "errorSent": STATE["error_sent"],
                    "lastPath": STATE["last_path"],
                }
            return self._send_json(payload)
        return self._send_error(404, 404, "Not found: {}".format(path), "NOT_FOUND")

    # -- POST ----------------------------------------------------------------------

    def do_POST(self):  # noqa: N802 - stdlib API
        path = urlsplit(self.path).path
        match = GENERATE_RE.match(path)
        if not match:
            return self._send_error(
                404,
                404,
                "Not found: {} (expected /v1beta/models/<model>:generateContent)".format(path),
                "NOT_FOUND",
            )

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self._send_error(400, 400, "Empty request body.", "INVALID_ARGUMENT")
        if length > MAX_BODY:
            return self._send_error(413, 413, "Request body too large.", "INVALID_ARGUMENT")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._send_error(
                400, 400, "Invalid JSON payload received.", "INVALID_ARGUMENT"
            )
        problem = validate_body(body)
        if problem:
            return self._send_error(400, 400, problem, "INVALID_ARGUMENT")

        model = match.group(1)
        prompt = collect_prompt(body)
        with LOCK:
            STATE["requests"] += 1
            STATE["last_path"] = path
            mode = STATE["mode"]
            send_error = mode == "error" and not STATE["error_sent"]
            if send_error:
                STATE["error_sent"] = True

        if send_error:
            return self._send_json(
                error_envelope(
                    429,
                    "Resource exhausted: mock MOCK_MODE=error returns 429 on the first request.",
                    "RESOURCE_EXHAUSTED",
                ),
                429,
                {"Retry-After": "0"},
            )

        # Transcription is always plain text, never JSON.
        if "TRANSCRIBE_PAGE" in prompt:
            text = ""
            pages = extract_pages(prompt)
            if pages:
                text = pages[0][1].strip()
            if not text:
                text = "SAMPLE PAGE TEXT"
            return self._send_json(gemini_envelope(text, model, len(prompt)))

        if mode == "badjson":
            text = (
                "This is a plain-English answer, not JSON. "
                "MOCK_MODE=badjson is active so this reply cannot be parsed."
            )
            return self._send_json(gemini_envelope(text, model, len(prompt)))

        mode_match = MODE_RE.search(prompt)
        if not mode_match:
            # Keep the endpoint useful for probes / unknown prompts.
            return self._send_json(gemini_envelope("SAMPLE PAGE TEXT", model, len(prompt)))

        try:
            data = build_mode_data(mode_match.group(1).lower(), prompt)
        except ValueError as exc:  # pragma: no cover - guarded by regex
            return self._send_error(500, 500, str(exc), "INTERNAL")
        return self._send_json(gemini_envelope(json.dumps(data, ensure_ascii=False), model, len(prompt)))


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ----------------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------------


def main(argv=None):
    global VERBOSE
    parser = argparse.ArgumentParser(
        description="Offline mock of the Gemini generateContent endpoint."
    )
    parser.add_argument("--host", default=os.environ.get("MOCK_HOST", HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MOCK_PORT", str(DEFAULT_PORT)) or 0),
        help="0 (default) picks a free port; the chosen port is printed",
    )
    parser.add_argument(
        "--mode",
        choices=("ok", "badjson", "error"),
        default=None,
        help="override MOCK_MODE (ok|badjson|error)",
    )
    parser.add_argument("--verbose", action="store_true", help="log each request")
    args = parser.parse_args(argv)

    mode = args.mode or os.environ.get("MOCK_MODE", "ok").strip().lower()
    if mode not in ("ok", "badjson", "error"):
        sys.stderr.write(
            "mock_gemini: unknown MOCK_MODE {!r}; using 'ok'\n".format(mode)
        )
        mode = "ok"
    STATE["mode"] = mode
    VERBOSE = bool(args.verbose)

    try:
        server = MockServer((args.host, args.port), Handler)
    except OSError as exc:
        sys.stderr.write(
            "mock_gemini: could not bind {}:{} - {}\n".format(args.host, args.port, exc)
        )
        return 1

    port = server.server_address[1]
    print("MOCK_PORT={}".format(port), flush=True)
    if VERBOSE:
        sys.stderr.write(
            "mock_gemini: listening on http://{}:{} (mode={})\n".format(args.host, port, mode)
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
