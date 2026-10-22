#!/usr/bin/env python3
"""Live API smoke test for Paper Trail (the only tool that needs a real key).

Start the server in one shell with a key:

    PowerShell:
        $env:GEMINI_API_KEY="..."; python server.py
    bash:
        GEMINI_API_KEY=... python server.py

Then in a second shell:

    python tools/check_api.py

It loads the bundled ``benefits-denial`` sample through the running server and
asks for an ``explain`` analysis, i.e. it exercises the real hosted-model path
end to end.  It prints the backend, model, HTTP status, latency and the first
line of the answer, and exits non-zero on failure.

Exit codes:
    0 = live analyze worked
    1 = the server or the model call failed
    2 = setup problem (no GEMINI_API_KEY, server unreachable, or demo mode)

Useful on the morning of a demo.  Never prints the API key.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

MODES = ("explain", "deadline", "ask", "respond")
LANGS = ("en", "es", "el", "bn", "zh", "ar")
DEFAULT_HOST = (os.environ.get("PAPERTRAIL_HOST", "127.0.0.1") or "127.0.0.1").strip()
if DEFAULT_HOST in ("0.0.0.0", "::", "[::]"):
    DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = (os.environ.get("PAPERTRAIL_PORT", "8765") or "8765").strip() or "8765"
DEFAULT_BASE = "http://{}:{}".format(DEFAULT_HOST, DEFAULT_PORT)
DEFAULT_DOC = "benefits-denial"


# ----------------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------------


def request_json(base, method, path, payload=None, timeout=120.0):
    """Return (status, parsed_or_None, raw_bytes).  HTTP errors are returned."""
    url = base + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, _parse(raw), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, _parse(raw), raw


def _parse(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None


def first_line(value, limit=240):
    """First non-empty line of a string, for compact console output."""
    if not isinstance(value, str):
        return ""
    for line in value.replace("\r\n", "\n").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def answer_text(mode, data):
    if not isinstance(data, dict):
        return "", ""
    if mode == "explain":
        return first_line(data.get("what_it_is")) or first_line(data.get("title")), "what_it_is"
    if mode == "deadline":
        deadlines = data.get("deadlines")
        if isinstance(deadlines, list) and deadlines and isinstance(deadlines[0], dict):
            item = deadlines[0]
            text = "{} — {}".format(item.get("date") or "no date", item.get("what") or "")
            return first_line(text), "deadlines[0]"
        return first_line(data.get("note")), "note"
    if mode == "ask":
        return first_line(data.get("answer")), "answer"
    if mode == "respond":
        return first_line(data.get("subject")) or first_line(data.get("body")), "subject/body"
    return "", ""


def doc_label(doc):
    if not isinstance(doc, dict):
        return "?"
    return "{} ({} page{})".format(
        doc.get("title") or doc.get("id") or "document",
        doc.get("page_count", "?"),
        "" if doc.get("page_count") == 1 else "s",
    )


# ----------------------------------------------------------------------------------
# Failure hints
# ----------------------------------------------------------------------------------


def hints_for(status, code, message, raw):
    text = "{} {}".format(message or "", (raw or b"").decode("utf-8", "replace")[:800]).lower()
    hints = []

    if code == "invalid_request":
        hints.append(
            "The server rejected the request shape. Check --doc/--mode/--lang, and note "
            "that ask mode requires --question."
        )

    if status == 400 or "http 400" in text or "invalid_argument" in text:
        hints.append(
            "400 from Gemini usually means a bad or legacy API key, or the key's project "
            "cannot call this model. Re-copy the key from https://aistudio.google.com/apikey "
            "(no quotes/spaces) and try PAPERTRAIL_MODEL=gemma-4-26b-a4b-it."
        )
    if status == 402 or "http 402" in text or "billing" in text or "free tier" in text or "free-tier" in text:
        hints.append(
            "402 is a billing/free-tier block. Gemma 4 is free-tier only; create a key in a "
            "NEW AI Studio project and do NOT enable billing. Or unset GEMINI_API_KEY and run "
            "the labelled demo mode instead."
        )
    if status == 429 or "http 429" in text or "rate limit" in text or "rate-limited" in text or "quota" in text:
        hints.append(
            "429 means rate limited or out of quota. Wait about 60 seconds and rerun; if it "
            "repeats, switch keys or models before the demo, not during it."
        )
    if status in (401, 403) or "permission" in text or "unauthenticated" in text:
        hints.append(
            "The key was refused (invalid, revoked, restricted, or the Generative Language "
            "API is not enabled). Create a fresh key in AI Studio and set it in THIS shell."
        )
    if status == 404 or code == "not_found":
        hints.append(
            "Not found. Check that samples/docs.json exists, the sample id is benefits-denial, "
            "and PAPERTRAIL_MODEL is a valid model id (default gemma-4-26b-a4b-it)."
        )
    if code == "no_api_key" or status == 503:
        hints.append(
            "The server has no API key. Set GEMINI_API_KEY in the shell that starts server.py, "
            "restart the server, and rerun this check."
        )
    if code in ("model_format", "upstream_error") and status == 502 and not hints:
        hints.append(
            "The server could not use the model reply. Run server.py with PAPERTRAIL_QUIET=0 "
            "to see its logs; retry once, then try another model/key."
        )
    if status == 500:
        hints.append("The server hit an internal error; check its console output for a traceback.")
    if not hints and status >= 500:
        hints.append(
            "Upstream/server failure. Retry once in a minute; if it persists, check the key, "
            "model id and the server console."
        )
    return hints


def print_http_failure(prefix, status, payload, raw, elapsed=None):
    err = payload.get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    message = err.get("message") if isinstance(err, dict) else None
    print("FAIL  {}".format(prefix))
    if elapsed is not None:
        print("  wait    {:.2f}s".format(elapsed))
    print("  status  HTTP {}".format(status))
    if code:
        print("  code    {}".format(code))
    if message:
        print("  error   {}".format(str(message)[:700]))
    elif raw:
        print("  body    {}".format(raw[:700].decode("utf-8", "replace")))
    for hint in hints_for(status, code, message, raw):
        print("  HINT    {}".format(hint))


# ----------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Live smoke test: load a sample through a running Paper Trail server and analyze it."
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PAPERTRAIL_BASE_URL", DEFAULT_BASE),
        help="running server base URL (default: %(default)s)",
    )
    parser.add_argument("--doc", default=DEFAULT_DOC, help="sample id to load (default: %(default)s)")
    parser.add_argument("--mode", default="explain", choices=MODES)
    parser.add_argument("--lang", default="en", choices=LANGS)
    parser.add_argument(
        "--question",
        default="What do I need to do?",
        help="question used when --mode ask",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait per HTTP request")
    args = parser.parse_args(argv)

    key = os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        print("FAIL  GEMINI_API_KEY is not set in this shell.")
        print('      PowerShell: $env:GEMINI_API_KEY="your-key"; python tools/check_api.py')
        print("      bash:       GEMINI_API_KEY=... python tools/check_api.py")
        print("      Get a free key: https://aistudio.google.com/apikey")
        print("      The server process also needs this variable before it starts.")
        return 2

    base = args.base.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print("FAIL  --base must be an http(s) URL, got {!r}".format(args.base))
        return 2

    print("Paper Trail - live API check")
    print("  base    {}".format(base))
    print("  doc     {}".format(args.doc))
    print("  mode    {}".format(args.mode))
    print("  key     set (not printed)")

    # ---- health ---------------------------------------------------------------
    try:
        status, health, raw = request_json(base, "GET", "/api/health", timeout=min(args.timeout, 20))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print("FAIL  cannot reach the Paper Trail server at {}".format(base))
        print("  error   {}".format(getattr(exc, "reason", exc)))
        print("  HINT    Start the server in another shell first:  python server.py")
        print("          If it is on another port: python tools/check_api.py --base http://127.0.0.1:PORT")
        return 2

    if status != 200 or not isinstance(health, dict) or health.get("ok") is not True:
        print_http_failure("GET /api/health did not return a healthy server", status, health, raw)
        print("  HINT    Is this a Paper Trail server? Check the URL/port and the server console.")
        return 2

    backend = health.get("backend")
    model = health.get("model")
    print("  server  backend={} model={} demoMode={}".format(backend, model, health.get("demoMode")))
    if health.get("demoMode") is True:
        print("FAIL  the server is in demo mode, so no live model call is possible.")
        print("  HINT  Set GEMINI_API_KEY in the shell that starts server.py, restart the server,")
        print("        then rerun this check. (PAPERTRAIL_DEMO=1 also forces demo mode.)")
        return 2
    if health.get("hasKey") is False:
        print("FAIL  the server reports no API key (hasKey=false).")
        print("  HINT  The key must be present in the SERVER process environment, not only here.")
        return 2

    # ---- load sample ----------------------------------------------------------
    try:
        status, loaded, raw = request_json(
            base,
            "POST",
            "/api/samples/load",
            {"sample": args.doc},
            timeout=min(args.timeout, 30),
        )
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print("FAIL  loading sample {!r} failed: {}".format(args.doc, getattr(exc, "reason", exc)))
        return 1

    if status != 200 or not isinstance(loaded, dict) or loaded.get("ok") is not True:
        print_http_failure("POST /api/samples/load failed", status, loaded, raw)
        return 1
    print("  sample  {}".format(doc_label(loaded.get("doc"))))

    # ---- analyze --------------------------------------------------------------
    payload = {"doc_id": args.doc, "mode": args.mode, "lang": args.lang}
    if args.mode == "ask":
        payload["question"] = args.question
    if args.mode == "respond":
        payload["context"] = "I received this notice and want to respond."

    started = time.perf_counter()
    try:
        status, analyzed, raw = request_json(
            base,
            "POST",
            "/api/analyze",
            payload,
            timeout=args.timeout,
        )
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        elapsed = time.perf_counter() - started
        print("FAIL  analyze request did not complete ({:.2f}s)".format(elapsed))
        print("  error   {}".format(getattr(exc, "reason", exc)))
        print("  HINT    Check the network and the server console; retry once. Model calls can take 30-60s.")
        return 1
    wall = time.perf_counter() - started

    if status != 200 or not isinstance(analyzed, dict) or analyzed.get("ok") is not True:
        print_http_failure("POST /api/analyze failed", status, analyzed, raw, elapsed=wall)
        return 1

    if analyzed.get("demo") is True:
        print("FAIL  the analyze response is a recorded demo answer, not a live model call.")
        print("  HINT  Restart the server with GEMINI_API_KEY set and PAPERTRAIL_DEMO unset.")
        return 1

    data = analyzed.get("data") if isinstance(analyzed.get("data"), dict) else {}
    text, field = answer_text(args.mode, data)
    latency = analyzed.get("latency_ms")
    citations = analyzed.get("citations") if isinstance(analyzed.get("citations"), list) else []
    verified = sum(1 for item in citations if isinstance(item, dict) and item.get("verified") is True)

    print("  status  HTTP 200")
    print("  backend {}".format(analyzed.get("backend") or backend))
    print("  model   {}".format(analyzed.get("model") or model))
    if isinstance(latency, int):
        print("  latency {} ms (server), {:.2f}s wall".format(latency, wall))
    else:
        print("  latency {:.2f}s wall".format(wall))
    print("  answer  {}".format(text or "(no text in {} returned)".format(field)))
    print("  cites   {} ({} verified)".format(len(citations), verified))

    if not text:
        print("FAIL  the model reply was valid JSON but no answer text was found.")
        print("  HINT  Inspect the response data: {}".format(repr(data)[:600]))
        return 1

    print("\nRESULT: PASS - live {} analysis worked end to end.".format(args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
