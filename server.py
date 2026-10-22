#!/usr/bin/env python3
"""Paper Trail - a local-first, privacy-first document desk.

Explain bills, benefits notices, insurance denials and immigration mail; extract
deadlines with page citations; ask questions; draft replies. Runs against the
hosted Gemini API (Gemma 4) today and an OpenAI-compatible local model later.

Standard library only. No telemetry. Documents are stored under PAPERTRAIL_DATA_DIR.
"""

import base64
import copy
import json
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

APP_DIR = Path(__file__).resolve().parent
APP_NAME = "Paper Trail"
APP_VERSION = "1.0.0"

MODES = ("explain", "deadline", "ask", "respond")
LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "el": "Greek",
    "bn": "Bengali",
    "zh": "Chinese (Simplified)",
    "ar": "Arabic",
}

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}

IMAGE_MIMES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}
MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_FILES_PER_INGEST = 20
MAX_QUESTION_CHARS = 1000
MAX_CONTEXT_CHARS = 2000
PAGE_TEXT_LIMIT = 20000

SYSTEM_PROMPT = """You are Paper Trail, a careful paper-document explainer for ordinary people.

Rules:
1. Only state what is visible in the pages you are given. Never invent a date, amount, address,
   phone number, deadline, consequence, or legal/medical fact.
2. Every extracted fact must carry a citation: {"page": N, "quote": "..."} where the quote is copied
   VERBATIM from that page's text, in the original language. Do not translate quotes.
3. If the page does not answer something, say so plainly (in unreadable or missing_info) instead of
   guessing. If text is unreadable, name which part.
4. Write in plain language at roughly a 6th-grade reading level. Lead with the practical meaning.
5. You are not a lawyer, doctor, or immigration advisor. Explain the document and what it asks for;
   suggest talking to a professional for high-stakes decisions. Never guarantee an outcome.
6. Output JSON only. No markdown fences, no commentary before or after the JSON object."""

MODE_TASKS = {
    "explain": """Explain this document to the person who received it.
Return exactly this JSON shape:
{
  "title": "short human title of the document",
  "doc_type": "e.g. insurance denial, benefits recertification, immigration request, bill, lease",
  "what_it_is": "2-3 plain sentences: what this paper is and what it wants",
  "key_points": [
    {"text": "a fact that changes what the person should do", "citation": {"page": 1, "quote": "exact quote"}}
  ],
  "next_action": "one concrete next step the person can take",
  "unreadable": ["parts you could not read, or an empty list"]
}
Use 3 to 5 key_points. Prioritize deadlines, amounts, appeal/response rights, required documents,
contact details, and consequences of doing nothing. If the page states money amounts, include every
stated amount the reader may owe or pay in one key point where possible - the total/allowed amount,
the member cost share, and any fee or penalty - using the exact figures from the page. Never round or
omit a stated amount.""",
    "deadline": """Extract every deadline and time-sensitive consequence.
Return exactly this JSON shape:
{
  "deadlines": [
    {"date": "YYYY-MM-DD or null",
     "what": "what is due / what happens on this date",
     "consequence": "what happens if it is missed",
     "action": "what to do before then",
     "citation": {"page": 1, "quote": "exact quote"}}
  ],
  "note": "one line on urgency, uncertainty, or missing dates - or an empty string"
}
Dates must be ISO YYYY-MM-DD when the page states a calendar date. If the page says "within 60 days
of this notice" and a notice date is visible, compute the date and explain the basis in "what".
Sort soonest first. If there are no deadlines, return an empty list and say so in note.""",
    "ask": """Answer the user's question using only the provided pages.
Return exactly this JSON shape:
{
  "answer": "direct answer in the requested language, 2-6 plain sentences",
  "citations": [{"page": 1, "quote": "exact quote"}],
  "missing_info": "what the document does not say, or null"
}
If the pages do not contain the answer, say so in answer, set missing_info accordingly, and cite
nothing or only the closest passage. Do not pad the answer with a summary of the whole document.""",
    "respond": """Draft a reply the person can send about this document.
Return exactly this JSON shape:
{
  "to": "recipient name/office from the document, or null",
  "subject": "subject line",
  "body": "ready-to-send plain-text letter with greeting, 2-4 short paragraphs, and a signature line with placeholders",
  "checklist": ["document or piece of information to gather/attach"],
  "send_via": "mail / fax / online portal / phone, with the address or number if the document shows it",
  "caution": "one line reminding the sender to verify the address, deadline and facts, and that this is not legal/medical advice",
  "citations": [{"page": 1, "quote": "exact quote"}]
}
Cite the document's own instructions (address, deadline, required documents, receipt number). Do not
invent facts about the sender; use placeholders like [Your name] and [Phone] where needed.""",
}

REQUIRED_KEYS = {
    "explain": ("title", "what_it_is", "key_points", "next_action"),
    "deadline": ("deadlines", "note"),
    "ask": ("answer", "citations"),
    "respond": ("subject", "body", "checklist", "caution"),
}

TRANSCRIBE_SYSTEM = """You are an exact document transcriber. Transcribe the visible text of the page
in reading order, preserving line breaks between paragraphs and list items. Do not explain, summarize,
translate, or add commentary. If a word is illegible, write [illegible]. If the page has no text, say
"[no text visible]".""" 

TRANSCRIBE_USER = """TRANSCRIBE_PAGE
Transcribe the visible text on this page exactly. Output only the transcription."""


# --------------------------------------------------------------------------------------
# Errors and small utilities
# --------------------------------------------------------------------------------------


class RequestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ModelError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_text(value):
    """Normalize for citation matching: NFKC, lower, alnum-ish, single spaces."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).lower()
    value = re.sub(r"[^0-9a-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff\u0600-\u06ff\u0980-\u09ff\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def quote_matches(quote, page_text):
    """True/False if the quote can be found in the page text; None if no page text to check."""
    if not isinstance(quote, str) or not quote.strip():
        return False
    if not page_text:
        return None
    quote_n = normalize_text(quote)
    text_n = normalize_text(page_text)
    if not quote_n:
        return False
    if quote_n in text_n:
        return True
    # Tolerate ellipses, bracketed edits and page-break hyphens in model quotes.
    variant = re.sub(r"\.{2,}|\u2026|\[[^\]]*\]", " ", quote)
    variant = re.sub(r"-\s+", "", variant)
    variant_n = normalize_text(variant)
    return bool(variant_n) and variant_n in text_n


def clamp_text(value, limit):
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.strip()
    return value[:limit]


def json_error(status, code, message):
    return status, {"ok": False, "error": {"code": code, "message": message}}


def parse_json_body(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError(400, "invalid_request", "Request body must be a JSON object.")


def parse_model_json(text):
    """Pull the first JSON object out of a model reply."""
    if not isinstance(text, str):
        raise ValueError("model reply is not text")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    return json.loads(cleaned[start : end + 1])


def pretty_title_from_name(filename):
    stem = Path(filename).stem
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    if not stem:
        return "Untitled document"
    return " ".join(word.capitalize() if word.islower() else word for word in stem.split())


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


class Config:
    def __init__(self):
        self.host = os.environ.get("PAPERTRAIL_HOST", "127.0.0.1").strip() or "127.0.0.1"
        try:
            self.port = int(os.environ.get("PAPERTRAIL_PORT", "8765"))
        except ValueError:
            self.port = 8765
        self.static_dir = APP_DIR / "static"
        self.samples_dir = APP_DIR / "samples"
        self.data_dir = Path(os.environ.get("PAPERTRAIL_DATA_DIR", str(APP_DIR / "data"))).expanduser().resolve()
        self.backend = os.environ.get("PAPERTRAIL_BACKEND", "gemini").strip().lower()
        if self.backend not in ("gemini", "openai"):
            self.backend = "gemini"
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.model = os.environ.get("PAPERTRAIL_MODEL", "gemma-4-26b-a4b-it").strip() or "gemma-4-26b-a4b-it"
        self.api_base = os.environ.get("PAPERTRAIL_API_BASE", "https://generativelanguage.googleapis.com").rstrip("/")
        self.openai_base = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        self.openai_model = os.environ.get("OPENAI_MODEL", "gemma4:12b").strip() or "gemma4:12b"
        self.openai_key = os.environ.get("OPENAI_API_KEY", "local").strip() or "local"
        try:
            self.max_file_mb = max(1, int(os.environ.get("PAPERTRAIL_MAX_FILE_MB", "12")))
        except ValueError:
            self.max_file_mb = 12
        self.transcribe = os.environ.get("PAPERTRAIL_TRANSCRIBE", "1").strip() != "0"
        try:
            self.transcribe_max_pages = max(0, int(os.environ.get("PAPERTRAIL_TRANSCRIBE_MAX_PAGES", "8")))
        except ValueError:
            self.transcribe_max_pages = 8
        self.demo_forced = os.environ.get("PAPERTRAIL_DEMO", "").strip() == "1"
        self.demo_mode = self.demo_forced or (self.backend == "gemini" and not self.api_key)
        self.quiet = os.environ.get("PAPERTRAIL_QUIET", "").strip() == "1"
        self.started = time.time()

    @property
    def effective_model(self):
        return self.openai_model if self.backend == "openai" else self.model

    @property
    def has_key(self):
        if self.backend == "openai":
            return True
        return bool(self.api_key)


# --------------------------------------------------------------------------------------
# Usage counters
# --------------------------------------------------------------------------------------


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.external_calls = 0
        self.bytes_out = 0

    def add_call(self, nbytes):
        with self.lock:
            self.external_calls += 1
            self.bytes_out += int(nbytes)

    def snapshot(self):
        with self.lock:
            return {"externalCalls": self.external_calls, "bytesOut": self.bytes_out}


# --------------------------------------------------------------------------------------
# Model client (Gemini native or OpenAI-compatible local)
# --------------------------------------------------------------------------------------


class ModelClient:
    def __init__(self, cfg, counters):
        self.cfg = cfg
        self.counters = counters

    def generate(self, system, user_text, images, max_tokens=2048):
        """Returns (text, latency_ms). images is a list of (bytes, mime)."""
        if self.cfg.demo_mode:
            raise RequestError(409, "demo_mode", "Demo mode is on; no model calls are made.")
        if self.cfg.backend == "openai":
            return self._openai(system, user_text, images, max_tokens)
        return self._gemini(system, user_text, images, max_tokens)

    # -- hosted Gemini API (Gemma 4) ---------------------------------------------------

    def _gemini(self, system, user_text, images, max_tokens):
        url = "{}/v1beta/models/{}:generateContent".format(self.cfg.api_base, self.cfg.model)
        parts = []
        for data, mime in images:
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
        parts.append({"text": user_text})
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": int(max_tokens),
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
        text = None
        latency = None
        try:
            text, latency = self._post_gemini(url, payload)
        except ModelError as exc:
            # Some deployments reject thinkingConfig; retry once without it.
            if "thinking" in exc.message.lower():
                payload["generationConfig"].pop("thinkingConfig", None)
                text, latency = self._post_gemini(url, payload)
            else:
                raise
        return text, latency

    def _post_gemini(self, url, payload):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.cfg.api_key}
        raw = self._request_with_retry(url, body, headers)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelError(502, "upstream_error", "The model API returned a non-JSON response.")
        candidates = obj.get("candidates") or []
        if not candidates:
            feedback = obj.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or obj.get("error", {}).get("message") or "no candidates"
            raise ModelError(502, "upstream_error", "The model returned no answer ({}).".format(str(reason)[:200]))
        content = candidates[0].get("content") or {}
        text = "".join(part.get("text", "") for part in (content.get("parts") or []) if isinstance(part, dict))
        if not text.strip():
            finish = candidates[0].get("finishReason") or "empty response"
            raise ModelError(502, "upstream_error", "The model returned an empty answer ({}).".format(finish))
        return text.strip(), None

    # -- local OpenAI-compatible endpoint (Ollama / LiteRT-LM / llama.cpp / LM Studio) --

    def _openai(self, system, user_text, images, max_tokens):
        url = "{}/chat/completions".format(self.cfg.openai_base)
        content = [{"type": "text", "text": user_text}]
        for data, mime in images:
            uri = "data:{};base64,{}".format(mime, base64.b64encode(data).decode("ascii"))
            content.append({"type": "image_url", "image_url": {"url": uri}})
        payload = {
            "model": self.cfg.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
            "max_tokens": int(max_tokens),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": "Bearer {}".format(self.cfg.openai_key)}
        raw = self._request_with_retry(url, body, headers)
        try:
            obj = json.loads(raw.decode("utf-8"))
            text = obj["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise ModelError(502, "upstream_error", "The local model returned an unexpected response.")
        if isinstance(text, list):  # some servers return content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        if not isinstance(text, str) or not text.strip():
            raise ModelError(502, "upstream_error", "The local model returned an empty answer.")
        return text.strip(), None

    def _request_with_retry(self, url, body, headers):
        last = None
        for attempt in range(2):
            start = time.time()
            self.counters.add_call(len(body))
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    raw = resp.read()
                self.last_latency_ms = int((time.time() - start) * 1000)
                return raw
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:500]
                except Exception:
                    pass
                last = (exc.code, detail)
                if exc.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0)
                    continue
                break
            except urllib.error.URLError as exc:
                last = (0, str(exc.reason if hasattr(exc, "reason") else exc))
                if attempt == 0:
                    time.sleep(0.8)
                    continue
                break
            except TimeoutError:
                last = (0, "timeout")
                if attempt == 0:
                    time.sleep(0.8)
                    continue
                break
        code, detail = last or (0, "unknown")
        if self.cfg.backend == "openai" and code == 0:
            raise ModelError(502, "upstream_error", "Local model server not reachable at {}. Start Ollama/LiteRT-LM or change OPENAI_BASE_URL.".format(self.cfg.openai_base))
        if code == 429:
            raise ModelError(429, "upstream_rate_limited", "The model API is rate-limited. Wait a moment and retry.")
        snippet = re.sub(r"\s+", " ", detail or "").strip()[:300]
        raise ModelError(502, "upstream_error", "The model API request failed (HTTP {}). {}".format(code or "network", snippet))


# --------------------------------------------------------------------------------------
# Citation validation + page selection
# --------------------------------------------------------------------------------------


def collect_citation_refs(mode, data):
    """Return list of (location, citation_dict) for the mode-specific places citations live."""
    refs = []
    if mode == "explain":
        for index, point in enumerate(data.get("key_points") or []):
            if isinstance(point, dict) and isinstance(point.get("citation"), dict):
                refs.append(("key_points[{}]".format(index), point["citation"]))
    elif mode == "deadline":
        for index, item in enumerate(data.get("deadlines") or []):
            if isinstance(item, dict) and isinstance(item.get("citation"), dict):
                refs.append(("deadlines[{}]".format(index), item["citation"]))
    elif mode in ("ask", "respond"):
        for index, citation in enumerate(data.get("citations") or []):
            if isinstance(citation, dict):
                refs.append(("citations[{}]".format(index), citation))
    return refs


def validate_citations(doc, mode, data, warnings):
    pages = {int(page["n"]): page for page in doc.get("pages", [])}
    flattened = []
    unverified = 0
    unavailable = 0
    for location, citation in collect_citation_refs(mode, data):
        page_no = citation.get("page")
        quote = citation.get("quote", "")
        verified = False
        try:
            page_no = int(page_no)
        except (TypeError, ValueError):
            page_no = None
        if page_no not in pages:
            citation["verified"] = False
            unverified += 1
        else:
            status = quote_matches(quote, pages[page_no].get("text", ""))
            citation["verified"] = status
            if status is True:
                pass
            elif status is None:
                unavailable += 1
            else:
                unverified += 1
        flattened.append(
            {
                "page": page_no,
                "quote": clamp_text(quote, 600),
                "verified": citation.get("verified"),
                "location": location,
            }
        )
    if unverified:
        warnings.append(
            "{} citation(s) could not be verified against the page text. Treat those facts with care.".format(unverified)
        )
    if unavailable:
        warnings.append(
            "{} citation(s) could not be verified because the page has no readable text.".format(unavailable)
        )
    return flattened


def select_pages(doc, mode, question):
    pages = list(doc.get("pages", []))
    cap = 10
    warnings = []
    if len(pages) <= cap:
        return pages, warnings
    if mode == "ask":
        terms = [term for term in re.findall(r"[0-9a-z]+", (question or "").lower()) if len(term) > 2]
        scored = []
        for page in pages:
            text = (page.get("text") or "").lower()
            score = sum(text.count(term) for term in terms)
            scored.append((score, page["n"], page))
        scored.sort(key=lambda item: (-item[0], item[1]))
        chosen = [item[2] for item in scored[:6]]
        chosen.sort(key=lambda page: page["n"])
    else:
        chosen = pages[:6] + pages[-4:]
    warnings.append(
        "This document has {} pages; only {} key pages were sent to the model. For full coverage, split the document.".format(
            len(pages), len(chosen)
        )
    )
    return chosen, warnings


def build_analyze_request(cfg, doc, mode, lang, question, context, pages):
    language = LANGUAGES.get(lang, "English")
    page_blocks = []
    for page in pages:
        text = (page.get("text") or "").strip()
        if not text:
            text = "(no extracted text on this page - read the page image)"
        page_blocks.append("[PAGE {}]\n{}".format(page["n"], text[:PAGE_TEXT_LIMIT]))
    header = (
        "DOCUMENT: {title}\nDOCUMENT TYPE: {doc_type}\nISSUER: {agency}\nDOCUMENT DATE: {doc_date}\n"
        "PAGES PROVIDED: {nums} of {total}\n"
    ).format(
        title=doc.get("title") or "Untitled document",
        doc_type=doc.get("doc_type") or "document",
        agency=doc.get("agency") or "unknown",
        doc_date=doc.get("doc_date") or "unknown",
        nums=", ".join(str(page["n"]) for page in pages),
        total=len(doc.get("pages", [])),
    )
    user = header + "\n" + "\n\n".join(page_blocks) + "\n\nMODE: {}\nOUTPUT LANGUAGE: {}\n".format(mode, language)
    if mode == "ask":
        user += "\nQUESTION: {}\n".format(clamp_text(question, MAX_QUESTION_CHARS))
    if context:
        user += "\nUSER CONTEXT (the person's situation, for tone and targeting only): {}\n".format(
            clamp_text(context, MAX_CONTEXT_CHARS)
        )
    user += "\nTASK:\n" + MODE_TASKS[mode] + "\n"
    user += (
        "\nWrite the answer in {language}. Citation quotes stay in the document's original language. "
        "Return one JSON object only.".format(language=language)
    )
    return user


def load_page_images(cfg, doc, pages, warnings):
    images = []
    for page in pages:
        data = read_page_image(cfg, doc, page)
        if data is None:
            if doc.get("source") == "upload" and page.get("kind") == "image":
                warnings.append("Page {} image could not be read; analyzed from stored text only.".format(page["n"]))
            continue
        images.append(data)
    return images


def read_page_image(cfg, doc, page):
    if doc.get("source") == "sample":
        path = cfg.samples_dir / "pages" / doc["id"] / "page-{}.png".format(page["n"])
        if path.is_file():
            try:
                return path.read_bytes(), "image/png"
            except OSError:
                return None
        return None
    image_file = page.get("image_file")
    if not image_file:
        return None
    path = cfg.data_dir / "docs" / doc["id"] / image_file
    mime = MIME_BY_EXT.get(path.suffix.lower(), "image/png")
    if not path.is_file():
        return None
    try:
        return path.read_bytes(), mime
    except OSError:
        return None


# --------------------------------------------------------------------------------------
# PDF text extraction (best-effort, stdlib only)
# --------------------------------------------------------------------------------------


def _pdf_unescape(value):
    out = []
    i = 0
    mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "(": "(", ")": ")", "\\": "\\"}
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt.isdigit():
                octal = ""
                j = i + 1
                while j < len(value) and len(octal) < 3 and value[j] in "01234567":
                    octal += value[j]
                    j += 1
                try:
                    out.append(chr(int(octal, 8)))
                except ValueError:
                    pass
                i = j
                continue
            if nxt in ("\n", "\r"):
                i += 2
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _pdf_decode_stream(blob):
    if blob.startswith(b"\xfe\xff"):
        try:
            return blob[2:].decode("utf-16-be", "replace")
        except UnicodeDecodeError:
            return ""
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return blob.decode("latin-1", "replace")


def extract_pdf_text(data):
    """Best-effort text extraction for simple text PDFs. Returns a string (maybe empty)."""
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = match.group(1)
        blobs = [raw]
        try:
            blobs.append(zlib.decompress(raw))
        except zlib.error:
            pass
        for blob in blobs:
            if b"Tj" not in blob and b"TJ" not in blob:
                continue
            text = _pdf_decode_stream(blob)
            strings = re.findall(r"\((?:\\.|[^\\()])*\)", text, re.S)
            if not strings:
                continue
            decoded = " ".join(_pdf_unescape(item[1:-1]) for item in strings)
            if decoded.strip():
                chunks.append(decoded)
    return "\n\n".join(chunks)


# --------------------------------------------------------------------------------------
# Local document store
# --------------------------------------------------------------------------------------


class Store:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.index_path = cfg.data_dir / "docs.json"
        self.docs = {}
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if not self.index_path.is_file():
            return
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            docs = payload.get("docs") if isinstance(payload, dict) else None
            if isinstance(docs, dict):
                self.docs = docs
        except (OSError, json.JSONDecodeError):
            backup = self.index_path.with_suffix(".corrupt-{}.json".format(int(time.time())))
            try:
                shutil.copy2(self.index_path, backup)
            except OSError:
                pass
            self.docs = {}

    def _save(self):
        payload = json.dumps({"version": 1, "docs": self.docs}, ensure_ascii=False).encode("utf-8")
        tmp = self.index_path.with_suffix(".tmp")
        with open(tmp, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, self.index_path)

    def list_summaries(self):
        with self.lock:
            docs = list(self.docs.values())
        docs.sort(key=lambda doc: doc.get("created_at") or "", reverse=True)
        summaries = []
        for doc in docs:
            preview = ""
            pages = doc.get("pages") or []
            if pages:
                preview = re.sub(r"\s+", " ", (pages[0].get("text") or "")).strip()[:180]
            summaries.append(
                {
                    "id": doc["id"],
                    "title": doc.get("title") or "Untitled document",
                    "doc_type": doc.get("doc_type") or "document",
                    "page_count": len(pages),
                    "source": doc.get("source") or "upload",
                    "created_at": doc.get("created_at"),
                    "preview_text": preview,
                    "demoResponses": doc.get("source") == "sample",
                }
            )
        return summaries

    def get(self, doc_id):
        with self.lock:
            doc = self.docs.get(doc_id)
            return copy.deepcopy(doc) if doc else None

    def detail(self, doc_id):
        with self.lock:
            doc = self.docs.get(doc_id)
            if not doc:
                return None
            out = copy.deepcopy(doc)
        for page in out.get("pages", []):
            if page.get("kind") == "image":
                if out.get("source") == "sample":
                    page["image_url"] = "/samples/pages/{}/page-{}.png".format(out["id"], page["n"])
                else:
                    page["image_url"] = "/api/docs/{}/pages/{}".format(out["id"], page["n"])
            else:
                page["image_url"] = None
            page.pop("image_file", None)
            page["n"] = int(page["n"])
            page["text"] = page.get("text") or ""
        pages = out.get("pages") or []
        preview = re.sub(r"\s+", " ", (pages[0].get("text") or "")).strip()[:180] if pages else ""
        out["preview_text"] = preview
        out.setdefault("warnings", [])
        out["page_count"] = len(out.get("pages", []))
        return out

    def add(self, doc):
        with self.lock:
            doc_id = doc["id"]
            if doc_id in self.docs:
                doc["id"] = "{}-{}".format(doc_id, uuid.uuid4().hex[:6])
            self.docs[doc["id"]] = doc
            self._save()
            return copy.deepcopy(doc)

    def replace(self, doc_id, doc):
        with self.lock:
            if doc_id in self.docs:
                self._remove_files(doc_id)
            doc["id"] = doc_id
            self.docs[doc_id] = doc
            self._save()
            return copy.deepcopy(doc)

    def unique_title(self, title):
        with self.lock:
            existing = {(doc.get("title") or "").strip().lower() for doc in self.docs.values()}
        candidate = title
        index = 2
        while candidate.strip().lower() in existing:
            candidate = "{} ({})".format(title, index)
            index += 1
        return candidate

    def delete(self, doc_id):
        with self.lock:
            if doc_id not in self.docs:
                return False
            del self.docs[doc_id]
            self._remove_files(doc_id)
            self._save()
            return True

    def delete_all(self):
        with self.lock:
            count = len(self.docs)
            for doc_id in list(self.docs):
                self._remove_files(doc_id)
            self.docs = {}
            self._save()
            return count

    def _remove_files(self, doc_id):
        path = self.cfg.data_dir / "docs" / doc_id
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    def page_image_path(self, doc_id, page_no):
        doc = self.get(doc_id)
        if not doc:
            return None
        for page in doc.get("pages", []):
            if int(page.get("n", 0)) == int(page_no) and page.get("image_file"):
                return self.cfg.data_dir / "docs" / doc_id / page["image_file"]
        return None


# --------------------------------------------------------------------------------------
# Samples + demo responses
# --------------------------------------------------------------------------------------


def load_samples(cfg):
    path = cfg.samples_dir / "docs.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    samples = {}
    for doc in payload.get("docs", []):
        if isinstance(doc, dict) and doc.get("id"):
            samples[doc["id"]] = doc
    return samples


def load_demo_responses(cfg):
    path = cfg.samples_dir / "responses.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    responses = payload.get("responses") if isinstance(payload, dict) else None
    return responses if isinstance(responses, dict) else {}


def sample_to_doc(cfg, sample):
    pages = []
    for page in sample.get("pages", []):
        pages.append({"n": int(page.get("n", 1)), "text": page.get("text") or "", "kind": "image"})
    return {
        "id": sample["id"],
        "title": sample.get("title") or "Sample document",
        "doc_type": sample.get("doc_type") or "document",
        "agency": sample.get("agency") or "",
        "doc_date": sample.get("doc_date") or "",
        "created_at": utc_now_iso(),
        "source": "sample",
        "pages": pages,
        "warnings": ["Synthetic sample document for demonstration only - not a real notice."],
    }


# --------------------------------------------------------------------------------------
# Application state
# --------------------------------------------------------------------------------------


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.counters = Counters()
        self.store = Store(cfg)
        self.samples = load_samples(cfg)
        self.demo_responses = load_demo_responses(cfg)
        self.model = ModelClient(cfg, self.counters)

    def analyze(self, payload):
        doc_id = payload.get("doc_id")
        mode = payload.get("mode")
        lang = payload.get("lang") or "en"
        question = payload.get("question") or ""
        context = payload.get("context") or ""
        if not isinstance(doc_id, str) or not doc_id:
            raise RequestError(400, "invalid_request", "doc_id is required.")
        if mode not in MODES:
            raise RequestError(400, "invalid_request", "mode must be one of: {}.".format(", ".join(MODES)))
        if lang not in LANGUAGES:
            raise RequestError(400, "invalid_request", "Unsupported language. Use one of: {}.".format(", ".join(LANGUAGES)))
        if mode == "ask":
            if not isinstance(question, str) or not question.strip():
                raise RequestError(400, "invalid_request", "A question is required for ask mode.")
            if len(question) > MAX_QUESTION_CHARS:
                raise RequestError(400, "invalid_request", "Question is too long (max {} characters).".format(MAX_QUESTION_CHARS))
        if not isinstance(context, str) or len(context) > MAX_CONTEXT_CHARS:
            raise RequestError(400, "invalid_request", "Context must be a string of at most {} characters.".format(MAX_CONTEXT_CHARS))
        doc = self.store.get(doc_id)
        if not doc:
            raise RequestError(404, "not_found", "Document not found.")

        if self.cfg.demo_mode:
            return self._analyze_demo(doc, mode, lang)

        warnings = []
        pages, page_warnings = select_pages(doc, mode, question)
        warnings.extend(page_warnings)
        images = load_page_images(self.cfg, doc, pages, warnings)
        user_text = build_analyze_request(self.cfg, doc, mode, lang, question, context, pages)
        start = time.time()
        text, _ = self.model.generate(SYSTEM_PROMPT, user_text, images, max_tokens=2048)
        try:
            data = parse_model_json(text)
            validate_mode_shape(mode, data)
        except (ValueError, json.JSONDecodeError):
            retry_user = user_text + (
                "\n\nYour previous reply could not be parsed as JSON. Return ONLY a single JSON object "
                "matching the required shape, with no markdown fences and no commentary."
            )
            text, _ = self.model.generate(SYSTEM_PROMPT, retry_user, images, max_tokens=2048)
            try:
                data = parse_model_json(text)
                validate_mode_shape(mode, data)
            except (ValueError, json.JSONDecodeError):
                raise ModelError(
                    502,
                    "model_format",
                    "The model did not return valid JSON. Raw reply: {}".format(re.sub(r"\s+", " ", text)[:300]),
                )
        latency_ms = int((time.time() - start) * 1000)
        normalize_mode_data(mode, data)
        citations = validate_citations(doc, mode, data, warnings)
        return {
            "ok": True,
            "mode": mode,
            "lang": lang,
            "demo": False,
            "backend": self.cfg.backend,
            "model": self.cfg.effective_model,
            "latency_ms": latency_ms,
            "data": data,
            "citations": citations,
            "warnings": warnings,
        }

    def _analyze_demo(self, doc, mode, lang):
        if doc.get("source") != "sample":
            raise RequestError(409, "demo_uploads", "Demo mode: uploads are disabled. Load a sample case or set GEMINI_API_KEY.")
        key = "{}|{}|{}".format(doc["id"], mode, lang)
        recording = self.demo_responses.get(key)
        if not isinstance(recording, dict):
            if lang != "en":
                raise RequestError(
                    409,
                    "demo_lang",
                    "Demo recordings are English-only. Set GEMINI_API_KEY for other languages.",
                )
            raise RequestError(
                409,
                "demo_mode",
                "No recorded demo answer for this case and mode. Set GEMINI_API_KEY for live analysis.",
            )
        data = copy.deepcopy(recording.get("data") or {})
        warnings = list(recording.get("warnings") or [])
        normalize_mode_data(mode, data)
        citations = validate_citations(doc, mode, data, warnings)
        return {
            "ok": True,
            "mode": mode,
            "lang": lang,
            "demo": True,
            "backend": "demo",
            "model": "recorded",
            "latency_ms": 0,
            "data": data,
            "citations": citations,
            "warnings": warnings,
        }

    def load_sample(self, sample_id):
        sample = self.samples.get(sample_id)
        if not sample:
            raise RequestError(404, "not_found", "Unknown sample case.")
        doc = sample_to_doc(self.cfg, sample)
        replaced = self.store.get(sample_id) is not None
        self.store.replace(sample_id, doc)
        return {"ok": True, "doc": self.store.detail(sample_id), "replaced": replaced}

    def ingest(self, payload):
        if self.cfg.demo_mode:
            raise RequestError(409, "demo_uploads", "Demo mode: uploads are disabled. Load a sample case or set GEMINI_API_KEY.")
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise RequestError(400, "invalid_request", "files must be a non-empty list.")
        if len(files) > MAX_FILES_PER_INGEST:
            raise RequestError(413, "too_large", "Too many files in one request (max {}).".format(MAX_FILES_PER_INGEST))
        created = []
        for item in files:
            created.append(self._ingest_one(item))
        return {"ok": True, "docs": [self.store.detail(doc["id"]) for doc in created]}

    def _ingest_one(self, item):
        if not isinstance(item, dict):
            raise RequestError(400, "invalid_request", "Each file must be an object.")
        name = str(item.get("name") or "document")
        mime = str(item.get("mime") or "").lower().split(";")[0].strip()
        data_b64 = item.get("data_b64")
        if not isinstance(data_b64, str) or not data_b64:
            raise RequestError(400, "invalid_request", "Each file needs data_b64.")
        try:
            raw = base64.b64decode(data_b64, validate=False)
        except (ValueError, TypeError):
            raise RequestError(400, "invalid_request", "Invalid base64 file data.")
        if len(raw) > self.cfg.max_file_mb * 1024 * 1024:
            raise RequestError(413, "too_large", "File is larger than {} MB.".format(self.cfg.max_file_mb))
        if not raw:
            raise RequestError(400, "invalid_request", "Empty file.")
        suffix = Path(name).suffix.lower()
        if not mime:
            if suffix in (".png", ".jpg", ".jpeg", ".webp"):
                mime = "image/png" if suffix == ".png" else ("image/webp" if suffix == ".webp" else "image/jpeg")
            elif suffix in (".txt", ".md", ".markdown"):
                mime = "text/plain"
            elif suffix == ".pdf":
                mime = "application/pdf"
        if mime not in IMAGE_MIMES and mime not in TEXT_MIMES and mime != "application/pdf":
            raise RequestError(415, "unsupported_type", "Unsupported file type: {}.".format(mime or suffix or "unknown"))

        doc_id = "{}-{}".format(re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-") or "doc", uuid.uuid4().hex[:6])
        title = self.store.unique_title(pretty_title_from_name(name))
        pages = []
        warnings = []
        if mime in IMAGE_MIMES:
            ext = IMAGE_MIMES[mime]
            doc_dir = self.cfg.data_dir / "docs" / doc_id
            doc_dir.mkdir(parents=True, exist_ok=True)
            image_file = "page-1{}".format(ext)
            (doc_dir / image_file).write_bytes(raw)
            text = ""
            if self.cfg.transcribe and self.cfg.transcribe_max_pages > 0:
                try:
                    text, _ = self.model.generate(
                        TRANSCRIBE_SYSTEM,
                        TRANSCRIBE_USER,
                        [(raw, mime)],
                        max_tokens=1500,
                    )
                    text = clamp_text(text, PAGE_TEXT_LIMIT)
                except (ModelError, RequestError) as exc:
                    warnings.append("Page 1 could not be transcribed: {}".format(getattr(exc, "message", str(exc))[:200]))
            else:
                warnings.append("Page 1 was not transcribed; citations cannot be verified for it.")
            pages.append({"n": 1, "text": text, "kind": "image", "image_file": image_file})
            doc_type = "photo of a document"
        elif mime == "application/pdf":
            text = extract_pdf_text(raw)
            chunks = split_text_pages(text)
            if chunks:
                for index, chunk in enumerate(chunks, start=1):
                    pages.append({"n": index, "text": chunk, "kind": "text"})
                doc_type = "PDF document"
            else:
                pages.append({"n": 1, "text": "", "kind": "text"})
                warnings.append("PDF appears to be scanned; page images would give better results.")
                doc_type = "scanned PDF"
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", "replace")
            chunks = split_text_pages(text)
            for index, chunk in enumerate(chunks, start=1):
                pages.append({"n": index, "text": chunk, "kind": "text"})
            doc_type = "text document"

        doc = {
            "id": doc_id,
            "title": title,
            "doc_type": doc_type,
            "agency": "",
            "doc_date": "",
            "created_at": utc_now_iso(),
            "source": "upload",
            "pages": pages,
            "warnings": warnings,
        }
        return self.store.add(doc)


def split_text_pages(text, max_chars=4000):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    if "\f" in text:
        parts = [part.strip() for part in text.split("\f") if part.strip()]
        if parts:
            return parts
    paragraphs = re.split(r"\n\s*\n", text)
    pages = []
    current = []
    current_len = 0
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and current_len + len(paragraph) + 2 > max_chars:
            pages.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph) + 2
    if current:
        pages.append("\n\n".join(current))
    return pages or [text]


def validate_mode_shape(mode, data):
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    for key in REQUIRED_KEYS[mode]:
        if key not in data:
            raise ValueError("missing key: {}".format(key))
    if mode == "explain":
        if not isinstance(data.get("key_points"), list) or not isinstance(data.get("title"), str):
            raise ValueError("explain shape invalid")
    elif mode == "deadline":
        if not isinstance(data.get("deadlines"), list):
            raise ValueError("deadline shape invalid")
    elif mode == "ask":
        if not isinstance(data.get("answer"), str):
            raise ValueError("ask shape invalid")
    elif mode == "respond":
        if not isinstance(data.get("body"), str) or not isinstance(data.get("checklist"), list):
            raise ValueError("respond shape invalid")


def normalize_mode_data(mode, data):
    """Fill optional fields and coerce lists so the UI never sees undefined."""
    if mode == "explain":
        data.setdefault("doc_type", "document")
        data.setdefault("unreadable", [])
        if not isinstance(data.get("unreadable"), list):
            data["unreadable"] = [str(data["unreadable"])]
        points = data.get("key_points")
        data["key_points"] = points if isinstance(points, list) else []
    elif mode == "deadline":
        deadlines = data.get("deadlines")
        data["deadlines"] = deadlines if isinstance(deadlines, list) else []
        data.setdefault("note", "")
        cleaned = []
        for item in data["deadlines"]:
            if isinstance(item, dict):
                cleaned.append(
                    {
                        "date": item.get("date"),
                        "what": item.get("what") or "",
                        "consequence": item.get("consequence") or "",
                        "action": item.get("action") or "",
                        "citation": item.get("citation") if isinstance(item.get("citation"), dict) else None,
                    }
                )
        data["deadlines"] = cleaned
    elif mode == "ask":
        data.setdefault("missing_info", None)
        data["citations"] = data.get("citations") if isinstance(data.get("citations"), list) else []
    elif mode == "respond":
        data.setdefault("to", None)
        data.setdefault("send_via", "")
        data.setdefault("caution", "This is not legal, medical, or immigration advice. Verify the details on the notice.")
        data["checklist"] = data.get("checklist") if isinstance(data.get("checklist"), list) else []
        data["citations"] = data.get("citations") if isinstance(data.get("citations"), list) else []


# --------------------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperTrail/" + APP_VERSION
    app = None  # set in main()

    # -- helpers -----------------------------------------------------------------------

    def log_message(self, fmt, *args):
        if self.app.cfg.quiet:
            return
        sys.stderr.write("[paper-trail] {} {}\n".format(self.command, self.path))

    def _cache_control(self, value):
        self.send_header("Cache-Control", value)
        self.send_header("X-Content-Type-Options", "nosniff")

    def _send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cache_control("no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_bytes(self, status, raw, content_type, cache="no-cache"):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._cache_control(cache)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_json(self, status, code, message):
        self._send_json(status, {"ok": False, "error": {"code": code, "message": message}})

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise RequestError(400, "invalid_request", "Invalid Content-Length.")
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "too_large", "Request body too large.")
        if length <= 0:
            raise RequestError(400, "invalid_request", "Empty request body.")
        return self.rfile.read(length)

    def _read_json(self):
        return parse_json_body(self._read_body())

    @staticmethod
    def _safe_file(base, relative):
        if not relative or "\x00" in relative:
            return None
        try:
            candidate = (base / unquote(relative)).resolve()
        except (OSError, ValueError):
            return None
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    # -- HTTP verbs --------------------------------------------------------------------

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/":
                return self._serve_static(self.app.cfg.static_dir / "index.html")
            if path == "/favicon.ico":
                icon = self.app.cfg.static_dir / "icon.svg"
                if icon.is_file():
                    return self._serve_static(icon)
                return self._send_error_json(404, "not_found", "Not found.")
            if path.startswith("/static/"):
                return self._serve_static_safe(self.app.cfg.static_dir, path[len("/static/"):])
            if path.startswith("/samples/pages/"):
                return self._serve_static_safe(self.app.cfg.samples_dir / "pages", path[len("/samples/pages/"):])
            if path == "/api/health":
                return self._health()
            if path == "/api/stats":
                return self._stats()
            if path == "/api/docs":
                return self._send_json(200, {"ok": True, "docs": self.app.store.list_summaries()})
            match = re.match(r"^/api/docs/([A-Za-z0-9._-]+)/pages/(\d+)$", path)
            if match:
                return self._serve_page_image(match.group(1), int(match.group(2)))
            match = re.match(r"^/api/docs/([A-Za-z0-9._-]+)$", path)
            if match:
                detail = self.app.store.detail(match.group(1))
                if not detail:
                    raise RequestError(404, "not_found", "Document not found.")
                return self._send_json(200, {"ok": True, "doc": detail})
            raise RequestError(404, "not_found", "Not found.")
        except RequestError as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            if not self.app.cfg.quiet:
                import traceback

                traceback.print_exc()
            self._send_error_json(500, "internal", "Internal server error.")

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/api/samples/load":
                payload = self._read_json()
                sample_id = payload.get("sample") if isinstance(payload, dict) else None
                if not isinstance(sample_id, str) or not sample_id:
                    raise RequestError(400, "invalid_request", "sample is required.")
                return self._send_json(200, self.app.load_sample(sample_id))
            if path == "/api/ingest":
                return self._send_json(200, self.app.ingest(self._read_json()))
            if path == "/api/analyze":
                return self._send_json(200, self.app.analyze(self._read_json()))
            raise RequestError(404, "not_found", "Not found.")
        except RequestError as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except ModelError as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            if not self.app.cfg.quiet:
                import traceback

                traceback.print_exc()
            self._send_error_json(500, "internal", "Internal server error.")

    def do_DELETE(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/api/docs":
                count = self.app.store.delete_all()
                return self._send_json(200, {"ok": True, "deleted": count})
            match = re.match(r"^/api/docs/([A-Za-z0-9._-]+)$", path)
            if match:
                deleted = self.app.store.delete(match.group(1))
                if not deleted:
                    raise RequestError(404, "not_found", "Document not found.")
                return self._send_json(200, {"ok": True, "deleted": 1})
            raise RequestError(404, "not_found", "Not found.")
        except RequestError as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            if not self.app.cfg.quiet:
                import traceback

                traceback.print_exc()
            self._send_error_json(500, "internal", "Internal server error.")

    # -- endpoint implementations ------------------------------------------------------

    def _health(self):
        cfg = self.app.cfg
        stats = self.app.counters.snapshot()
        payload = {
            "ok": True,
            "app": APP_NAME,
            "version": APP_VERSION,
            "backend": "demo" if cfg.demo_mode else cfg.backend,
            "model": "recorded" if cfg.demo_mode else cfg.effective_model,
            "demoMode": cfg.demo_mode,
            "hasKey": cfg.has_key,
            "dataDir": str(cfg.data_dir),
            "docs": len(self.app.store.list_summaries()),
            "externalCalls": stats["externalCalls"],
            "bytesOut": stats["bytesOut"],
            "uptimeSec": int(time.time() - cfg.started),
        }
        self._send_json(200, payload)

    def _stats(self):
        cfg = self.app.cfg
        stats = self.app.counters.snapshot()
        payload = {
            "ok": True,
            "externalCalls": stats["externalCalls"],
            "bytesOut": stats["bytesOut"],
            "docs": len(self.app.store.list_summaries()),
            "demoMode": cfg.demo_mode,
            "backend": "demo" if cfg.demo_mode else cfg.backend,
            "model": "recorded" if cfg.demo_mode else cfg.effective_model,
            "uptimeSec": int(time.time() - cfg.started),
        }
        self._send_json(200, payload)

    def _serve_static(self, path):
        if not path.is_file():
            return self._send_error_json(404, "not_found", "Not found.")
        content_type = MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
        try:
            raw = path.read_bytes()
        except OSError:
            return self._send_error_json(404, "not_found", "Not found.")
        self._send_bytes(200, raw, content_type)

    def _serve_static_safe(self, base, relative):
        path = self._safe_file(base, relative)
        if not path:
            return self._send_error_json(404, "not_found", "Not found.")
        content_type = MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
        try:
            raw = path.read_bytes()
        except OSError:
            return self._send_error_json(404, "not_found", "Not found.")
        self._send_bytes(200, raw, content_type)

    def _serve_page_image(self, doc_id, page_no):
        doc = self.app.store.get(doc_id)
        if not doc:
            raise RequestError(404, "not_found", "Document not found.")
        path = self.app.store.page_image_path(doc_id, page_no)
        if not path or not path.is_file():
            raise RequestError(404, "not_found", "Page image not found.")
        content_type = MIME_BY_EXT.get(path.suffix.lower(), "image/png")
        try:
            raw = path.read_bytes()
        except OSError:
            raise RequestError(404, "not_found", "Page image not found.")
        self._send_bytes(200, raw, content_type)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Paper Trail local server")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--demo", action="store_true", help="Force demo mode (no model calls)")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    if args.port is not None:
        os.environ["PAPERTRAIL_PORT"] = str(args.port)
    if args.host:
        os.environ["PAPERTRAIL_HOST"] = args.host
    if args.demo:
        os.environ["PAPERTRAIL_DEMO"] = "1"
    if args.data_dir:
        os.environ["PAPERTRAIL_DATA_DIR"] = args.data_dir

    cfg = Config()
    app = App(cfg)
    Handler.app = app
    try:
        httpd = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    except OSError as exc:
        sys.stderr.write("Could not bind {}:{} - {}\n".format(cfg.host, cfg.port, exc))
        return 1
    httpd.daemon_threads = True
    mode = "DEMO (recorded sample answers, no network)" if cfg.demo_mode else "LIVE ({} / {})".format(cfg.backend, cfg.effective_model)
    print("{} v{}".format(APP_NAME, APP_VERSION))
    print("  mode:  {}".format(mode))
    print("  url:   http://{}:{}".format(cfg.host, cfg.port))
    print("  data:  {}".format(cfg.data_dir))
    print("  samples loaded: {}".format(len(app.samples)))
    if not cfg.demo_mode and cfg.backend == "gemini" and not cfg.api_key:
        print("  warning: GEMINI_API_KEY is not set; running in demo mode.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Paper Trail.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
