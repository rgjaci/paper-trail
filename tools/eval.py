#!/usr/bin/env python3
"""Tiny live-API evaluation for Paper Trail.

    python tools/eval.py                 # server must already be running
    python tools/eval.py --base http://127.0.0.1:8765

For each sample document listed in ``eval/gold.json`` this loads the sample
through the running server, asks for the ``deadline`` analysis, and compares
the extracted ISO dates against gold.  If the gold entry lists amounts, it also
asks for ``explain`` once and looks for those amounts anywhere in that analysis
(the deadline contract has no dedicated amount field).

Prints a compact table and exits non-zero if a gold fact is missing.  Requires
a live server (not demo mode); it does not need GEMINI_API_KEY in this shell
because the server owns the model call.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "eval" / "gold.json"
DEFAULT_HOST = (os.environ.get("PAPERTRAIL_HOST", "127.0.0.1") or "127.0.0.1").strip()
if DEFAULT_HOST in ("0.0.0.0", "::", "[::]"):
    DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = (os.environ.get("PAPERTRAIL_PORT", "8765") or "8765").strip() or "8765"
DEFAULT_BASE = "http://{}:{}".format(DEFAULT_HOST, DEFAULT_PORT)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------------


def request_json(base, method, path, payload=None, timeout=120.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
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


def server_error(payload, raw):
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        return str(err.get("message") or err)[:500]
    return (raw or b"").decode("utf-8", "replace")[:500]


# ----------------------------------------------------------------------------------
# Gold comparison
# ----------------------------------------------------------------------------------


def normalize_amounts(text):
    """$2,140.00 and 2140.00 compare equal; letters/spaces do not hide matches."""
    return re.sub(r"[,$\s]", "", str(text))


def extract_dates(data):
    dates = []
    deadlines = data.get("deadlines") if isinstance(data, dict) else None
    if not isinstance(deadlines, list):
        return dates
    for item in deadlines:
        if not isinstance(item, dict):
            continue
        value = item.get("date")
        if isinstance(value, str) and DATE_RE.match(value.strip()):
            dates.append(value.strip())
    return dates


def amounts_in(*blobs):
    text = "\n".join(
        json.dumps(blob, ensure_ascii=False) if not isinstance(blob, str) else blob
        for blob in blobs
    )
    return normalize_amounts(text)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare live deadline/amount extraction against eval/gold.json."
    )
    parser.add_argument("--base", default=os.environ.get("PAPERTRAIL_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--lang", default="en", choices=("en", "es", "el", "bn", "zh", "ar"))
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    gold_path = Path(args.gold).expanduser()
    try:
        gold_payload = json.loads(gold_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print("FAIL  cannot read gold file {}: {}".format(gold_path, exc))
        return 2
    gold_docs = gold_payload.get("docs") if isinstance(gold_payload, dict) else None
    if not isinstance(gold_docs, dict) or not gold_docs:
        print("FAIL  gold file {} has no 'docs' object".format(gold_path))
        return 2

    base = args.base.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print("FAIL  --base must be an http(s) URL, got {!r}".format(args.base))
        return 2

    print("Paper Trail - gold evaluation")
    print("  base  {}".format(base))
    print("  gold  {}".format(gold_path))

    try:
        status, health, raw = request_json(base, "GET", "/api/health", timeout=min(args.timeout, 20))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print("FAIL  cannot reach the server at {}: {}".format(base, getattr(exc, "reason", exc)))
        print("      Start it first: python server.py")
        return 2
    if status != 200 or not isinstance(health, dict) or health.get("ok") is not True:
        print("FAIL  /api/health failed: HTTP {} {}".format(status, server_error(health, raw)))
        return 2
    if health.get("demoMode") is True:
        print("FAIL  the server is in demo mode; eval needs live model calls.")
        print("      Set GEMINI_API_KEY in the shell that starts server.py and restart it.")
        return 2
    print("  model {} / {}".format(health.get("backend"), health.get("model")))
    print("")

    header = "  {:<22} {:<24} {:<28} {:<30} {}".format(
        "doc", "gold date(s)", "extracted date(s)", "gold amount(s)", "result"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    all_ok = True
    checked = 0
    failed_docs = 0
    for doc_id, spec in gold_docs.items():
        spec = spec if isinstance(spec, dict) else {}
        gold_dates = [str(value) for value in (spec.get("deadlines") or [])]
        gold_amounts = [str(value) for value in (spec.get("amounts") or [])]

        try:
            status, loaded, raw = request_json(
                base,
                "POST",
                "/api/samples/load",
                {"sample": doc_id},
                timeout=min(args.timeout, 30),
            )
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            print("  {:<22} ERROR loading sample: {}".format(doc_id, exc))
            all_ok = False
            failed_docs += 1
            continue
        if status != 200 or not isinstance(loaded, dict) or loaded.get("ok") is not True:
            print(
                "  {:<22} ERROR load: HTTP {} {}".format(
                    doc_id, status, server_error(loaded, raw)
                )
            )
            all_ok = False
            failed_docs += 1
            continue

        try:
            status, analyzed, raw = request_json(
                base,
                "POST",
                "/api/analyze",
                {"doc_id": doc_id, "mode": "deadline", "lang": args.lang},
                timeout=args.timeout,
            )
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            print("  {:<22} ERROR analyze: {}".format(doc_id, exc))
            all_ok = False
            failed_docs += 1
            continue
        if status != 200 or not isinstance(analyzed, dict) or analyzed.get("ok") is not True:
            print(
                "  {:<22} ERROR analyze: HTTP {} {}".format(
                    doc_id, status, server_error(analyzed, raw)
                )
            )
            all_ok = False
            failed_docs += 1
            continue

        deadline_data = analyzed.get("data") if isinstance(analyzed.get("data"), dict) else {}
        seen_dates = sorted(set(extract_dates(deadline_data)))
        missing_dates = [value for value in gold_dates if value not in seen_dates]

        amount_blob = amounts_in(deadline_data)
        amount_note = ""
        explain_ok = True
        if gold_amounts:
            try:
                status, explained, raw = request_json(
                    base,
                    "POST",
                    "/api/analyze",
                    {"doc_id": doc_id, "mode": "explain", "lang": args.lang},
                    timeout=args.timeout,
                )
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                explained = None
                explain_ok = False
                amount_note = " explain call failed: {}".format(exc)
            else:
                if status != 200 or not isinstance(explained, dict) or explained.get("ok") is not True:
                    explain_ok = False
                    amount_note = " explain call failed: HTTP {}".format(status)
                else:
                    amount_blob += "\n" + amounts_in(explained.get("data") or {})

        missing_amounts = [
            value for value in gold_amounts if normalize_amounts(value) not in amount_blob
        ]
        checked += 1

        row_ok = not missing_dates and not missing_amounts and explain_ok
        if not row_ok:
            all_ok = False
            failed_docs += 1
        print(
            "  {:<22} {:<24} {:<28} {:<30} {}".format(
                doc_id[:22],
                ", ".join(gold_dates) or "-",
                ", ".join(seen_dates) or "-",
                ", ".join(gold_amounts) or "-",
                "PASS" if row_ok else "FAIL",
            )
        )
        if missing_dates:
            print("      missing deadline date(s): {}".format(", ".join(missing_dates)))
        if missing_amounts:
            print(
                "      missing amount(s): {}{}".format(
                    ", ".join(missing_amounts), amount_note
                )
            )
        elif amount_note:
            print("      note:{}".format(amount_note))

    print("")
    if all_ok:
        print("eval: PASS - {} document(s) matched gold".format(checked))
        return 0
    print(
        "eval: FAIL - {} of {} document(s) failed gold comparison".format(
            failed_docs, len(gold_docs)
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
