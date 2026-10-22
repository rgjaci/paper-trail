#!/usr/bin/env python3
"""End-to-end offline selftest for Paper Trail.

    python tools/selftest.py

Starts ``tools/mock_gemini.py`` on a free loopback port and ``server.py`` as a
subprocess with a temporary data directory.  No network access and no real API
key are needed.  Exercises:

  * live mode: health, samples, docs, all four analyze modes, Ask, Spanish,
    stats counters, text ingest, deletes, and the 400/404/413/415 error paths;
  * static files and path-traversal rejection;
  * demo mode: a second server with no key and PAPERTRAIL_DEMO=1, recorded
    answers, demo_lang, demo_uploads, zero external calls, delete-all;
  * cleanup of both subprocesses and the temporary data directory.

Prints one PASS/FAIL line per check and exits non-zero if anything fails.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SERVER_PATH = ROOT / "server.py"
MOCK_PATH = ROOT / "tools" / "mock_gemini.py"
SAMPLES_PATH = ROOT / "samples" / "docs.json"
RESPONSES_PATH = ROOT / "samples" / "responses.json"

DOC = "benefits-denial"
MODEL = "test-model"
DUMMY_KEY = "test"
MODES = ("explain", "deadline", "ask", "respond")
LANGS = ("en", "es", "el", "bn", "zh", "ar")
MODE_KEYS = {
    "explain": ("title", "doc_type", "what_it_is", "key_points", "next_action", "unreadable"),
    "deadline": ("deadlines", "note"),
    "ask": ("answer", "citations", "missing_info"),
    "respond": ("to", "subject", "body", "checklist", "send_via", "caution", "citations"),
}

RESULTS = []
_UNSET = object()


# ----------------------------------------------------------------------------------
# Check bookkeeping
# ----------------------------------------------------------------------------------


def check(name, ok, detail=""):
    ok = bool(ok)
    text = "" if ok else str(detail).replace("\r", " ").replace("\n", " / ")[:500]
    RESULTS.append((name, ok, text))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "  <- " + text))
    return ok


def section(title):
    print("\n-- " + title + " --")


# ----------------------------------------------------------------------------------
# HTTP helpers (http.client keeps the path literal so traversal can be tested)
# ----------------------------------------------------------------------------------


def http_request(port, method, path, payload=_UNSET, raw=None, timeout=30.0):
    headers = {}
    body = None
    if raw is not None:
        body = raw
        headers["Content-Type"] = "application/json"
    elif payload is not _UNSET:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, response_headers, data
    finally:
        conn.close()


def call_json(port, method, path, payload=_UNSET, raw=None, timeout=30.0):
    status, headers, data = http_request(port, method, path, payload=payload, raw=raw, timeout=timeout)
    try:
        parsed = json.loads(data.decode("utf-8")) if data else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    return status, parsed, data, headers


def wait_json(port, path, timeout, process=None):
    deadline = time.time() + timeout
    last = "no response"
    while time.time() < deadline:
        if process is not None and process.returncode is not None:
            return None, "process exited with code {} before {} answered".format(
                process.returncode, path
            )
        try:
            status, parsed, raw, _ = call_json(port, "GET", path, timeout=2)
            if status == 200 and isinstance(parsed, dict):
                return parsed, None
            last = "HTTP {} {}".format(status, raw[:160])
        except (OSError, http.client.HTTPException) as exc:
            last = "{}: {}".format(type(exc).__name__, exc)
        time.sleep(0.2)
    return None, last


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ----------------------------------------------------------------------------------
# Subprocess management
# ----------------------------------------------------------------------------------


class ManagedProcess:
    def __init__(self, name, argv, env):
        self.name = name
        self._lines = deque(maxlen=400)
        self.proc = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._thread = threading.Thread(target=self._drain, name=name + "-output", daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            for line in self.proc.stdout:
                self._lines.append(line.rstrip())
        except (ValueError, OSError):
            pass

    @property
    def returncode(self):
        return self.proc.poll()

    def output(self, limit=25):
        lines = list(self._lines)
        return "\n".join(lines[-limit:])

    def stop(self, timeout=8.0):
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
        try:
            self._thread.join(timeout=1.0)
        except RuntimeError:
            pass


def dump_output(process, label):
    if process is None:
        return
    tail = process.output()
    if tail:
        print("      {} output (tail):".format(label))
        for line in tail.splitlines():
            print("        " + line)


# ----------------------------------------------------------------------------------
# Environment builders
# ----------------------------------------------------------------------------------


def live_env(port, data_dir, mock_port):
    env = os.environ.copy()
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "PAPERTRAIL_DEMO"):
        env.pop(name, None)
    env.update(
        {
            "PAPERTRAIL_HOST": "127.0.0.1",
            "PAPERTRAIL_PORT": str(port),
            "PAPERTRAIL_DATA_DIR": str(data_dir),
            "PAPERTRAIL_BACKEND": "gemini",
            "PAPERTRAIL_MODEL": MODEL,
            "PAPERTRAIL_API_BASE": "http://127.0.0.1:{}".format(mock_port),
            "PAPERTRAIL_MAX_FILE_MB": "1",
            "PAPERTRAIL_TRANSCRIBE": "0",
            "PAPERTRAIL_TRANSCRIBE_MAX_PAGES": "1",
            "PAPERTRAIL_DEMO": "0",
            "PAPERTRAIL_QUIET": "0",
            "GEMINI_API_KEY": DUMMY_KEY,
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def demo_env(port, data_dir, mock_port):
    env = live_env(port, data_dir, mock_port)
    env.pop("GEMINI_API_KEY", None)
    env["PAPERTRAIL_DEMO"] = "1"
    return env


# ----------------------------------------------------------------------------------
# JSON / response helpers
# ----------------------------------------------------------------------------------


def error_info(parsed):
    if not isinstance(parsed, dict):
        return None
    err = parsed.get("error")
    return err if isinstance(err, dict) else None


def error_check(name, status, parsed, raw, expected_status, expected_code):
    err = error_info(parsed)
    ok = (
        status == expected_status
        and isinstance(parsed, dict)
        and parsed.get("ok") is False
        and isinstance(err, dict)
        and err.get("code") == expected_code
        and isinstance(err.get("message"), str)
        and bool(err["message"].strip())
    )
    return check(
        name,
        ok,
        "HTTP {} body={!r} (wanted {} {})".format(status, raw[:300], expected_status, expected_code),
    )


def nested_citations(mode, data):
    refs = []
    if not isinstance(data, dict):
        return refs
    if mode == "explain":
        for point in data.get("key_points") or []:
            if isinstance(point, dict) and isinstance(point.get("citation"), dict):
                refs.append(point["citation"])
    elif mode == "deadline":
        for item in data.get("deadlines") or []:
            if isinstance(item, dict) and isinstance(item.get("citation"), dict):
                refs.append(item["citation"])
    elif mode in ("ask", "respond"):
        for citation in data.get("citations") or []:
            if isinstance(citation, dict):
                refs.append(citation)
    return refs


def check_analyze(port, tag, payload, expected_mode, expected_lang="en"):
    status, resp, raw, _ = call_json(port, "POST", "/api/analyze", payload, timeout=90)
    http_ok = status == 200 and isinstance(resp, dict) and resp.get("ok") is True
    if not check(
        tag + ".http",
        http_ok,
        "HTTP {} body={!r}".format(status, raw[:350]),
    ):
        return None

    shape_ok = (
        resp.get("mode") == expected_mode
        and resp.get("lang") == expected_lang
        and resp.get("demo") is False
        and resp.get("backend") == "gemini"
        and resp.get("model") == MODEL
        and isinstance(resp.get("latency_ms"), int)
        and resp.get("latency_ms") >= 0
        and isinstance(resp.get("data"), dict)
        and isinstance(resp.get("citations"), list)
        and isinstance(resp.get("warnings"), list)
    )
    check(
        tag + ".shape",
        shape_ok,
        {
            key: resp.get(key)
            for key in ("mode", "lang", "demo", "backend", "model", "latency_ms", "data", "citations", "warnings")
        },
    )

    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    missing = [key for key in MODE_KEYS[expected_mode] if key not in data]
    check(tag + ".data_keys", not missing, "missing keys: {} data={}".format(missing, list(data)))

    citations = resp.get("citations") if isinstance(resp.get("citations"), list) else []
    flattened_ok = bool(citations) and all(
        isinstance(item, dict)
        and isinstance(item.get("page"), int)
        and isinstance(item.get("quote"), str)
        and bool(item["quote"].strip())
        and "verified" in item
        for item in citations
    )
    check(tag + ".citations_shape", flattened_ok, "citations={!r}".format(citations[:3]))
    check(
        tag + ".citations_verified",
        any(item.get("verified") is True for item in citations if isinstance(item, dict)),
        "verified flags: {}".format([item.get("verified") for item in citations if isinstance(item, dict)]),
    )

    nested = nested_citations(expected_mode, data)
    nested_ok = (
        bool(nested)
        and all(isinstance(item, dict) and "verified" in item for item in nested)
        and any(item.get("verified") is True for item in nested)
    )
    check(tag + ".nested_verified", nested_ok, "nested citations={!r}".format(nested[:3]))
    return resp


# ----------------------------------------------------------------------------------
# Live-mode checks
# ----------------------------------------------------------------------------------


def check_live_health(port, data_dir):
    section("live mode: health")
    status, health, raw, _ = call_json(port, "GET", "/api/health", timeout=10)
    if not check(
        "live.health.http",
        status == 200 and isinstance(health, dict) and health.get("ok") is True,
        "HTTP {} body={!r}".format(status, raw[:300]),
    ):
        return

    required = (
        "app",
        "version",
        "backend",
        "model",
        "demoMode",
        "hasKey",
        "dataDir",
        "docs",
        "externalCalls",
        "bytesOut",
        "uptimeSec",
    )
    missing = [key for key in required if key not in health]
    check("live.health.keys", not missing, "missing keys: {} health={}".format(missing, health))
    check(
        "live.health.identity",
        health.get("app") == "Paper Trail"
        and isinstance(health.get("version"), str)
        and bool(health["version"])
        and health.get("backend") == "gemini"
        and health.get("model") == MODEL,
        {key: health.get(key) for key in ("app", "version", "backend", "model")},
    )
    check(
        "live.health.live_mode",
        health.get("demoMode") is False and health.get("hasKey") is True,
        {key: health.get(key) for key in ("demoMode", "hasKey")},
    )
    try:
        same_data_dir = Path(str(health.get("dataDir"))).resolve() == Path(data_dir).resolve()
    except (OSError, ValueError):
        same_data_dir = False
    check(
        "live.health.data_dir",
        same_data_dir,
        "health.dataDir={!r} expected={!r}".format(health.get("dataDir"), str(data_dir)),
    )
    check(
        "live.health.fresh_counters",
        health.get("docs") == 0
        and health.get("externalCalls") == 0
        and health.get("bytesOut") == 0
        and isinstance(health.get("uptimeSec"), int)
        and health["uptimeSec"] >= 0,
        {key: health.get(key) for key in ("docs", "externalCalls", "bytesOut", "uptimeSec")},
    )


def check_static(port):
    section("static files and traversal")
    status, headers, raw = http_request(port, "GET", "/", timeout=10)
    check(
        "static.root",
        status == 200 and b"Paper Trail" in raw,
        "HTTP {} bytes={} head={!r}".format(status, len(raw), raw[:120]),
    )

    status, headers, raw = http_request(port, "GET", "/static/app.js", timeout=10)
    content_type = headers.get("content-type", "")
    check(
        "static.app_js",
        status == 200 and "javascript" in content_type.lower(),
        "HTTP {} content-type={!r} bytes={}".format(status, content_type, len(raw)),
    )

    status, headers, raw = http_request(port, "GET", "/static/../server.py", timeout=10)
    check(
        "static.traversal_rejected",
        status in (400, 404),
        "HTTP {} bytes={} (must not serve server.py)".format(status, len(raw)),
    )


def check_sample_and_docs(port, samples):
    section("live mode: samples and documents")
    sample = samples.get(DOC) or {}
    status, body, raw, _ = call_json(
        port, "POST", "/api/samples/load", {"sample": DOC}, timeout=30
    )
    loaded = (
        status == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and isinstance(body.get("doc"), dict)
    )
    if not check("live.sample.load", loaded, "HTTP {} body={!r}".format(status, raw[:400])):
        return None

    doc = body["doc"]
    pages = doc.get("pages") if isinstance(doc.get("pages"), list) else []
    check(
        "live.sample.load_doc",
        doc.get("id") == DOC
        and doc.get("source") == "sample"
        and doc.get("title") == sample.get("title")
        and doc.get("doc_type") == sample.get("doc_type")
        and doc.get("page_count") == len(sample.get("pages") or [])
        and len(pages) == doc.get("page_count"),
        {
            key: doc.get(key)
            for key in ("id", "source", "title", "doc_type", "page_count")
        },
    )
    check("live.sample.replaced_false", body.get("replaced") is False, body.get("replaced"))

    sample_page_urls_ok = bool(pages) and all(
        isinstance(page, dict)
        and page.get("kind") == "image"
        and isinstance(page.get("image_url"), str)
        and page["image_url"].startswith("/samples/pages/{}/".format(DOC))
        and isinstance(page.get("text"), str)
        and bool(page["text"].strip())
        for page in pages
    )
    check("live.sample.pages", sample_page_urls_ok, "pages={!r}".format(pages[:2]))

    status, listing, raw, _ = call_json(port, "GET", "/api/docs", timeout=10)
    docs = listing.get("docs") if isinstance(listing, dict) and isinstance(listing.get("docs"), list) else []
    summary = next((item for item in docs if isinstance(item, dict) and item.get("id") == DOC), None)
    check(
        "live.docs.list",
        status == 200 and isinstance(listing, dict) and listing.get("ok") is True and summary is not None,
        "HTTP {} body={!r}".format(status, raw[:300]),
    )
    if summary is not None:
        check(
            "live.docs.summary",
            summary.get("page_count") == len(sample.get("pages") or [])
            and summary.get("source") == "sample"
            and isinstance(summary.get("demoResponses"), bool)
            and isinstance(summary.get("preview_text"), str),
            summary,
        )

    status, detail_body, raw, _ = call_json(port, "GET", "/api/docs/" + DOC, timeout=10)
    detail = detail_body.get("doc") if isinstance(detail_body, dict) else None
    detail_pages = detail.get("pages") if isinstance(detail, dict) and isinstance(detail.get("pages"), list) else []
    check(
        "live.docs.detail",
        status == 200
        and isinstance(detail_body, dict)
        and detail_body.get("ok") is True
        and isinstance(detail, dict)
        and detail.get("id") == DOC
        and detail.get("page_count") == len(detail_pages)
        and len(detail_pages) == len(sample.get("pages") or [])
        and isinstance(detail.get("warnings"), list),
        "HTTP {} body={!r}".format(status, raw[:300]),
    )
    # Contract §3 lists preview_text on DocDetail as well as on DocSummary.
    preview = detail.get("preview_text") if isinstance(detail, dict) else None
    check(
        "live.docs.detail_preview",
        isinstance(preview, str) and bool(preview.strip()),
        "DocDetail.preview_text={!r} (contract §3)".format(preview),
    )
    return detail or doc


def check_live_analysis(port):
    section("live mode: analyze")
    base = {"doc_id": DOC, "lang": "en"}
    check_analyze(port, "live.explain", dict(base, mode="explain"), "explain")
    check_analyze(port, "live.deadline", dict(base, mode="deadline"), "deadline")
    check_analyze(
        port,
        "live.ask",
        dict(base, mode="ask", question="What must I do to appeal?"),
        "ask",
    )
    check_analyze(
        port,
        "live.respond",
        dict(base, mode="respond", context="I am the patient and cannot afford the MRI."),
        "respond",
    )
    check_analyze(
        port,
        "live.spanish",
        {"doc_id": DOC, "mode": "explain", "lang": "es"},
        "explain",
        expected_lang="es",
    )


def check_stats(port):
    section("live mode: stats")
    status, stats, raw, _ = call_json(port, "GET", "/api/stats", timeout=10)
    if not check(
        "live.stats.http",
        status == 200 and isinstance(stats, dict) and stats.get("ok") is True,
        "HTTP {} body={!r}".format(status, raw[:300]),
    ):
        return
    check(
        "live.stats.external_calls",
        isinstance(stats.get("externalCalls"), int) and stats["externalCalls"] > 0,
        stats.get("externalCalls"),
    )
    check(
        "live.stats.bytes_out",
        isinstance(stats.get("bytesOut"), int) and stats["bytesOut"] > 0,
        stats.get("bytesOut"),
    )
    check(
        "live.stats.shape",
        stats.get("demoMode") is False
        and stats.get("backend") == "gemini"
        and stats.get("model") == MODEL
        and isinstance(stats.get("docs"), int),
        {key: stats.get(key) for key in ("demoMode", "backend", "model", "docs")},
    )


def check_ingest(port):
    section("live mode: ingest, analyze, delete")
    note = (
        "SELFTEST NOTICE\n\n"
        "Your recertification deadline is January 15, 2027. "
        "You must submit the form by then.\n\n"
        "Keep a copy of everything you send.\n"
    )
    encoded = base64.b64encode(note.encode("utf-8")).decode("ascii")
    payload = {
        "files": [
            {
                "name": "selftest-note.txt",
                "mime": "text/plain",
                "data_b64": encoded,
            }
        ]
    }
    status, body, raw, _ = call_json(port, "POST", "/api/ingest", payload, timeout=30)
    docs = body.get("docs") if isinstance(body, dict) and isinstance(body.get("docs"), list) else []
    created = docs[0] if docs else None
    if not check(
        "live.ingest.http",
        status == 200 and isinstance(body, dict) and body.get("ok") is True and isinstance(created, dict),
        "HTTP {} body={!r}".format(status, raw[:400]),
    ):
        return

    pages = created.get("pages") if isinstance(created.get("pages"), list) else []
    page_text = pages[0].get("text", "") if pages and isinstance(pages[0], dict) else ""
    check(
        "live.ingest.text_doc",
        created.get("source") == "upload"
        and bool(created.get("id"))
        and created.get("page_count") == len(pages)
        and len(pages) >= 1
        and pages[0].get("kind") == "text"
        and pages[0].get("image_url") is None
        and isinstance(page_text, str)
        and "deadline" in page_text
        and isinstance(created.get("title"), str)
        and bool(created["title"].strip()),
        {
            "id": created.get("id"),
            "source": created.get("source"),
            "page_count": created.get("page_count"),
            "page0": pages[0] if pages else None,
        },
    )

    uploaded_id = created.get("id")
    if uploaded_id:
        check_analyze(
            port,
            "live.upload_explain",
            {"doc_id": uploaded_id, "mode": "explain", "lang": "en"},
            "explain",
        )

        status, del_body, raw, _ = call_json(port, "DELETE", "/api/docs/" + uploaded_id, timeout=15)
        check(
            "live.delete.one",
            status == 200
            and isinstance(del_body, dict)
            and del_body.get("ok") is True
            and del_body.get("deleted") == 1,
            "HTTP {} body={!r}".format(status, raw[:300]),
        )
        status, gone, raw, _ = call_json(port, "GET", "/api/docs/" + uploaded_id, timeout=10)
        err = error_info(gone)
        check(
            "live.delete.one_gone",
            status == 404 and isinstance(err, dict) and err.get("code") == "not_found",
            "HTTP {} body={!r}".format(status, raw[:300]),
        )


def check_live_errors(port):
    section("live mode: error paths")

    status, parsed, raw, _ = call_json(
        port,
        "POST",
        "/api/analyze",
        {"doc_id": DOC, "mode": "bogus", "lang": "en"},
        timeout=15,
    )
    error_check("live.error.invalid_mode", status, parsed, raw, 400, "invalid_request")

    status, parsed, raw, _ = call_json(
        port,
        "POST",
        "/api/analyze",
        {"doc_id": DOC, "mode": "explain", "lang": "fr"},
        timeout=15,
    )
    error_check("live.error.invalid_lang", status, parsed, raw, 400, "invalid_request")

    status, parsed, raw, _ = call_json(
        port,
        "POST",
        "/api/analyze",
        {"doc_id": "selftest-no-such-doc", "mode": "explain", "lang": "en"},
        timeout=15,
    )
    error_check("live.error.unknown_doc", status, parsed, raw, 404, "not_found")

    py_b64 = base64.b64encode(b"print('not a document')\n").decode("ascii")
    status, parsed, raw, _ = call_json(
        port,
        "POST",
        "/api/ingest",
        {"files": [{"name": "script.py", "mime": "text/x-python", "data_b64": py_b64}]},
        timeout=15,
    )
    error_check("live.error.unsupported_type", status, parsed, raw, 415, "unsupported_type")

    big_b64 = base64.b64encode(b"A" * (2 * 1024 * 1024)).decode("ascii")
    status, parsed, raw, _ = call_json(
        port,
        "POST",
        "/api/ingest",
        {"files": [{"name": "oversize.txt", "mime": "text/plain", "data_b64": big_b64}]},
        timeout=120,
    )
    error_check("live.error.too_large", status, parsed, raw, 413, "too_large")

    status, parsed, raw, _ = call_json(
        port,
        "POST",
        "/api/analyze",
        raw=b'{"doc_id": "benefits-denial",',
        timeout=15,
    )
    error_check("live.error.malformed_analyze", status, parsed, raw, 400, "invalid_request")


def check_delete_all(port):
    section("live mode: delete all")
    status, body, raw, _ = call_json(port, "DELETE", "/api/docs", timeout=15)
    check(
        "live.delete.all",
        status == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and isinstance(body.get("deleted"), int)
        and body["deleted"] >= 1,
        "HTTP {} body={!r}".format(status, raw[:300]),
    )
    status, listing, raw, _ = call_json(port, "GET", "/api/docs", timeout=10)
    docs = listing.get("docs") if isinstance(listing, dict) else None
    check(
        "live.delete.all_empty",
        status == 200 and docs == [],
        "HTTP {} body={!r}".format(status, raw[:300]),
    )


# ----------------------------------------------------------------------------------
# Demo-mode checks
# ----------------------------------------------------------------------------------


def load_demo_recordings():
    if not RESPONSES_PATH.is_file():
        return {}, "missing file: {}".format(RESPONSES_PATH)
    try:
        payload = json.loads(RESPONSES_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, "could not read {}: {}".format(RESPONSES_PATH, exc)
    responses = payload.get("responses") if isinstance(payload, dict) else None
    if not isinstance(responses, dict):
        return {}, "responses.json has no 'responses' object"
    return responses, None


def choose_demo_key(responses, sample_ids):
    preferred = "{}|explain|en".format(DOC)
    if preferred in responses:
        return preferred
    for key in sorted(responses):
        parts = key.split("|")
        if len(parts) == 3 and parts[0] in sample_ids and parts[1] in MODES and parts[2] == "en":
            return key
    for key in sorted(responses):
        parts = key.split("|")
        if len(parts) == 3 and parts[0] in sample_ids and parts[1] in MODES and parts[2] in LANGS:
            return key
    return None


def mock_request_count(mock_port):
    try:
        status, parsed, _, _ = call_json(mock_port, "GET", "/health", timeout=5)
    except (OSError, http.client.HTTPException):
        return None
    if status == 200 and isinstance(parsed, dict) and isinstance(parsed.get("requests"), int):
        return parsed["requests"]
    return None


def check_demo_health(port):
    section("demo mode: health")
    status, health, raw, _ = call_json(port, "GET", "/api/health", timeout=10)
    if not check(
        "demo.health.http",
        status == 200 and isinstance(health, dict) and health.get("ok") is True,
        "HTTP {} body={!r}".format(status, raw[:300]),
    ):
        return
    check(
        "demo.health.demo_mode",
        health.get("demoMode") is True and health.get("hasKey") is False,
        {key: health.get(key) for key in ("demoMode", "hasKey")},
    )
    check(
        "demo.health.identity",
        health.get("app") == "Paper Trail" and isinstance(health.get("version"), str),
        {key: health.get(key) for key in ("app", "version")},
    )
    check(
        "demo.health.zero_calls",
        health.get("externalCalls") == 0
        and health.get("bytesOut") == 0
        and health.get("docs") == 0,
        {key: health.get(key) for key in ("externalCalls", "bytesOut", "docs")},
    )


def check_demo_analysis(port, key, responses):
    doc_id, mode, lang = key.split("|")
    recording = responses.get(key) if isinstance(responses.get(key), dict) else {}
    recorded_data = recording.get("data") if isinstance(recording.get("data"), dict) else {}

    payload = {"doc_id": doc_id, "mode": mode, "lang": lang}
    if mode == "ask" and not payload.get("question"):
        payload["question"] = "What do I need to do?"
    status, resp, raw, _ = call_json(port, "POST", "/api/analyze", payload, timeout=30)
    if not check(
        "demo.analyze.http",
        status == 200 and isinstance(resp, dict) and resp.get("ok") is True,
        "HTTP {} body={!r}".format(status, raw[:350]),
    ):
        return

    check(
        "demo.analyze.flags",
        resp.get("mode") == mode
        and resp.get("lang") == lang
        and resp.get("demo") is True
        and resp.get("backend") == "demo"
        and resp.get("model") == "recorded"
        and resp.get("latency_ms") == 0,
        {
            key: resp.get(key)
            for key in ("mode", "lang", "demo", "backend", "model", "latency_ms")
        },
    )

    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    missing = [name for name in MODE_KEYS[mode] if name not in data]
    check("demo.analyze.data_keys", not missing, "missing keys: {} data={}".format(missing, list(data)))

    mismatched = []
    for name, value in recorded_data.items():
        if name not in data:
            mismatched.append(name + " (missing)")
        elif isinstance(value, (str, int, float, bool)) or value is None:
            if data.get(name) != value:
                mismatched.append(name)
    check(
        "demo.analyze.recorded_data",
        bool(recorded_data) and not mismatched and not missing,
        "mismatched/missing: {} data={}".format(mismatched, data if len(str(data)) < 400 else list(data)),
    )
    check(
        "demo.analyze.citations_list",
        isinstance(resp.get("citations"), list) and isinstance(resp.get("warnings"), list),
        {key: type(resp.get(key)).__name__ for key in ("citations", "warnings")},
    )


def check_demo_lang(port, key, responses):
    doc_id, mode, _lang = key.split("|")
    es_key = "{}|{}|es".format(doc_id, mode)
    payload = {"doc_id": doc_id, "mode": mode, "lang": "es"}
    if mode == "ask":
        payload["question"] = "¿Qué debo hacer?"
    status, resp, raw, _ = call_json(port, "POST", "/api/analyze", payload, timeout=30)
    if es_key in responses:
        check(
            "demo.es.recording",
            status == 200
            and isinstance(resp, dict)
            and resp.get("demo") is True
            and resp.get("lang") == "es",
            "HTTP {} body={!r}".format(status, raw[:350]),
        )
        return
    err = error_info(resp)
    check(
        "demo.es.demo_lang",
        status == 409
        and isinstance(err, dict)
        and err.get("code") == "demo_lang"
        and isinstance(err.get("message"), str)
        and bool(err["message"].strip()),
        "HTTP {} body={!r}".format(status, raw[:350]),
    )


def check_demo_uploads(port):
    encoded = base64.b64encode(b"demo upload test\n").decode("ascii")
    payload = {"files": [{"name": "demo-note.txt", "mime": "text/plain", "data_b64": encoded}]}
    status, resp, raw, _ = call_json(port, "POST", "/api/ingest", payload, timeout=15)
    error_check("demo.upload.demo_uploads", status, resp, raw, 409, "demo_uploads")


def check_demo_stats(port, mock_port, mock_calls_before):
    section("demo mode: stats, no external calls")
    status, stats, raw, _ = call_json(port, "GET", "/api/stats", timeout=10)
    check(
        "demo.stats.zero_calls",
        status == 200
        and isinstance(stats, dict)
        and stats.get("ok") is True
        and stats.get("externalCalls") == 0
        and stats.get("bytesOut") == 0
        and stats.get("demoMode") is True,
        "HTTP {} body={!r}".format(status, raw[:300]),
    )
    mock_calls_after = mock_request_count(mock_port)
    check(
        "demo.stats.no_mock_traffic",
        mock_calls_before is not None
        and mock_calls_after is not None
        and mock_calls_after == mock_calls_before,
        "mock requests before={} after={}".format(mock_calls_before, mock_calls_after),
    )


def check_demo_delete_all(port):
    status, body, raw, _ = call_json(port, "DELETE", "/api/docs", timeout=15)
    check(
        "demo.delete_all",
        status == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and isinstance(body.get("deleted"), int)
        and body["deleted"] >= 1,
        "HTTP {} body={!r}".format(status, raw[:300]),
    )
    status, listing, raw, _ = call_json(port, "GET", "/api/docs", timeout=10)
    docs = listing.get("docs") if isinstance(listing, dict) else None
    check(
        "demo.delete_all_empty",
        status == 200 and docs == [],
        "HTTP {} body={!r}".format(status, raw[:300]),
    )


# ----------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------


def summary(started):
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(name, detail) for name, ok, detail in RESULTS if not ok]
    elapsed = time.perf_counter() - started
    print("\n" + "-" * 72)
    if failed:
        print("failed checks:")
        for name, detail in failed:
            print("  x {}{}".format(name, ("  [{}]".format(detail) if detail else "")))
        print("selftest: FAIL - {} passed, {} failed in {:.1f}s".format(passed, len(failed), elapsed))
        return 1
    print("selftest: PASS - {} checks in {:.1f}s".format(passed, elapsed))
    return 0


def main():
    started = time.perf_counter()
    print("Paper Trail offline selftest")
    print("  python: {}".format(sys.version.split()[0]))
    print("  repo:   {}".format(ROOT))

    missing = [str(path) for path in (SERVER_PATH, MOCK_PATH) if not path.is_file()]
    if missing:
        print("FAIL  setup: required file(s) missing:")
        for path in missing:
            print("        " + path)
        print("      server.py is written by the Lead in parallel; nothing was started.")
        print("selftest: BLOCKED - backend not available yet")
        return 2

    try:
        samples_payload = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print("FAIL  setup: cannot read sample metadata {}: {}".format(SAMPLES_PATH, exc))
        print("selftest: BLOCKED - bundled samples unavailable")
        return 2
    samples = {
        doc["id"]: doc
        for doc in (samples_payload.get("docs") or [])
        if isinstance(doc, dict) and doc.get("id")
    }
    if DOC not in samples:
        print("FAIL  setup: samples/docs.json does not contain {!r}".format(DOC))
        print("selftest: BLOCKED - bundled samples unavailable")
        return 2

    ports = set()
    while len(ports) < 3:
        ports.add(free_port())
    mock_port, app_port, demo_port = sorted(ports)

    temp_dir = Path(tempfile.mkdtemp(prefix="papertrail-selftest-"))
    live_data = temp_dir / "live-data"
    demo_data = temp_dir / "demo-data"
    live_data.mkdir(parents=True, exist_ok=True)
    demo_data.mkdir(parents=True, exist_ok=True)

    mock = None
    live = None
    demo = None
    try:
        # ------------------------------------------------------------------ mock
        section("setup")
        mock_env = os.environ.copy()
        mock_env["MOCK_MODE"] = "ok"
        mock_env["PYTHONUNBUFFERED"] = "1"
        try:
            mock = ManagedProcess(
                "mock_gemini.py",
                [PY, str(MOCK_PATH), "--port", str(mock_port)],
                mock_env,
            )
        except OSError as exc:
            check("setup.mock_start", False, exc)
        else:
            mock_health, mock_error = wait_json(mock_port, "/health", 12, process=mock)
            if not check(
                "setup.mock_health",
                mock_health is not None and mock_health.get("ok") is True,
                mock_error,
            ):
                dump_output(mock, "mock_gemini.py")
                return summary(started)
        if mock is None:
            print("selftest: BLOCKED - offline mock did not start")
            return summary(started)

        # ------------------------------------------------------------------ live
        try:
            live = ManagedProcess(
                "server.py",
                [PY, str(SERVER_PATH)],
                live_env(app_port, live_data, mock_port),
            )
        except OSError as exc:
            check("setup.server_start", False, exc)
            live = None
        else:
            live_health, live_error = wait_json(app_port, "/api/health", 20, process=live)
            if not check(
                "setup.server_health",
                live_health is not None and live_health.get("ok") is True,
                live_error,
            ):
                dump_output(live, "server.py")
                live.stop()
                live = None

        if live is not None and live.returncode is None:
            check_live_health(app_port, live_data)
            check_static(app_port)
            doc = check_sample_and_docs(app_port, samples)
            if doc is not None:
                check_live_analysis(app_port)
                check_stats(app_port)
                check_ingest(app_port)
                check_live_errors(app_port)
                check_delete_all(app_port)
            else:
                print("      skipping dependent live checks: sample did not load")

        # Stop the live server before demo mode; free the port and reset the store.
        if live is not None:
            live.stop()
            print("cleanup: server.py (live) stopped")
            live = None

        # ------------------------------------------------------------------ demo
        section("demo mode: setup")
        responses, responses_error = load_demo_recordings()
        check(
            "demo.recordings.load",
            responses_error is None and bool(responses),
            responses_error or "responses.json has no recordings",
        )
        demo_key = choose_demo_key(responses, set(samples))
        check(
            "demo.recordings.usable",
            demo_key is not None,
            "no recording keys match the bundled sample ids",
        )
        if demo_key is None:
            demo_key_for_load = "{}|explain|en".format(DOC)
        else:
            demo_key_for_load = demo_key
        demo_sample_id = demo_key_for_load.split("|")[0]

        mock_calls_before_demo = mock_request_count(mock_port)

        try:
            demo = ManagedProcess(
                "server.py (demo)",
                [PY, str(SERVER_PATH)],
                demo_env(demo_port, demo_data, mock_port),
            )
        except OSError as exc:
            check("demo.server_start", False, exc)
            demo = None
        else:
            demo_health, demo_error = wait_json(demo_port, "/api/health", 20, process=demo)
            if not check(
                "demo.server_health",
                demo_health is not None and demo_health.get("ok") is True,
                demo_error,
            ):
                dump_output(demo, "server.py (demo)")
                demo.stop()
                demo = None

        if demo is not None and demo.returncode is None:
            check_demo_health(demo_port)
            status, body, raw, _ = call_json(
                demo_port, "POST", "/api/samples/load", {"sample": demo_sample_id}, timeout=30
            )
            sample_loaded = check(
                "demo.sample.load",
                status == 200
                and isinstance(body, dict)
                and body.get("ok") is True
                and isinstance(body.get("doc"), dict),
                "HTTP {} body={!r}".format(status, raw[:400]),
            )
            if sample_loaded and demo_key is not None:
                check_demo_analysis(demo_port, demo_key, responses)
                check_demo_lang(demo_port, demo_key, responses)
            check_demo_uploads(demo_port)
            check_demo_stats(demo_port, mock_port, mock_calls_before_demo)
            if sample_loaded:
                check_demo_delete_all(demo_port)

        print("\n-- cleanup --")
        if demo is not None:
            demo.stop()
            print("cleanup: server.py (demo) stopped")
            demo = None
        if live is not None:
            live.stop()
            print("cleanup: server.py stopped")
            live = None
        if mock is not None:
            mock.stop()
            print("cleanup: mock_gemini.py stopped")
            mock = None

    except Exception as exc:  # noqa: BLE001 - keep teardown and summary intact
        import traceback

        traceback.print_exc()
        check(
            "selftest.unexpected_error",
            False,
            "{}: {}".format(type(exc).__name__, exc),
        )

    finally:
        # Never leak child processes or temp files, even when a check throws.
        for process, label in ((demo, "demo"), (live, "live"), (mock, "mock")):
            if process is not None:
                try:
                    process.stop()
                except Exception:  # noqa: BLE001 - teardown must not mask failures
                    pass
        removed = False
        for _ in range(6):
            shutil.rmtree(temp_dir, ignore_errors=True)
            if not temp_dir.exists():
                removed = True
                break
            time.sleep(0.25)
        if not removed:
            print("WARN  could not fully remove temp dir: {}".format(temp_dir))
        else:
            print("cleanup: temporary data directory removed")

    return summary(started)


if __name__ == "__main__":
    raise SystemExit(main())
