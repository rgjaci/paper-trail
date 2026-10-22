#!/usr/bin/env python3
"""Render the synthetic sample documents as letter-style page PNGs.

Build-time only: the Paper Trail server never imports this module. It reads
``samples/docs.json`` and writes one 1275 x 1650 PNG (US Letter at 150 dpi) per
page to ``samples/pages/<doc_id>/page-<n>.png``.

Usage (from the ``paper-trail`` app directory)::

    python tools/make_samples.py
    python tools/make_samples.py --only benefits-denial

Pillow is required. Output is deterministic: fixed layout, no timestamps, no
randomness. Fonts come from C:\\Windows\\Fonts (Georgia + Arial, Calibri
fallback) and the rendered images are committed, so the running app needs no
font or image dependency at all.

The pages are deliberately marked "SAMPLE" (footer + diagonal watermark): they
are synthetic documents for software demonstration, not real notices.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # build-time dependency; keep the error actionable
    print(
        "make_samples: Pillow is required to render the sample page images.\n"
        "  Install it for this interpreter:  python -m pip install Pillow\n"
        "  Or run this script with the bundled Python runtime that already has\n"
        "  Pillow (see samples/README.md for the exact path).",
        file=sys.stderr,
    )
    raise SystemExit(2)


APP_ROOT = Path(__file__).resolve().parents[1]
DOCS_JSON = APP_ROOT / "samples" / "docs.json"
PAGES_DIR = APP_ROOT / "samples" / "pages"
FONT_DIR = Path(r"C:\Windows\Fonts")

# --- page geometry (US Letter at 150 dpi) -----------------------------------
PAGE_W = 1275
PAGE_H = 1650
MARGIN_X = 110
MARGIN_TOP = 90
TEXT_W = PAGE_W - 2 * MARGIN_X  # 1055 px
BORDER_INSET = 26
CONTENT_BOTTOM = 1550  # body text must end above this
FOOTER_RULE_Y = 1568
FOOTER_TEXT_Y = 1580

# --- type scale -------------------------------------------------------------
TITLE_SIZE = 46
AGENCY_SIZE = 30
BODY_SIZE = 23
HEAD_SIZE = 25
SMALL_SIZE = 18
DATE_SIZE = 20
SUBJECT_SIZE = 21
FOOTER_SIZE = 16
WATERMARK_SIZE = 140

# --- palette (warm paper, ink, deep green) ----------------------------------
PAPER = (253, 252, 248)
INK = (34, 40, 35)
INK_SOFT = (72, 79, 72)
GREEN = (28, 72, 52)
GREEN_SOFT = (48, 96, 72)
GRAY = (108, 114, 106)
SUBJECT_GRAY = (104, 110, 103)
RULE = (203, 211, 199)
RULE_STRONG = (158, 175, 154)
BORDER = (232, 228, 216)
WATERMARK_GRAY = (128, 128, 128)

FOOTER_LEFT = "SAMPLE \u2014 synthetic document for software demonstration. Not a real notice."
WATERMARK_TEXT = "SAMPLE"
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

FONT_CANDIDATES = {
    "serif_bold": ("georgiab.ttf", "georgia.ttf", "timesbd.ttf", "calibrib.ttf"),
    "sans": ("arial.ttf", "calibri.ttf", "segoeui.ttf"),
    "sans_bold": ("arialbd.ttf", "calibrib.ttf", "segoeuib.ttf"),
    "sans_italic": ("ariali.ttf", "calibrii.ttf", "segoeuii.ttf"),
}

_MEASURE = ImageDraw.Draw(Image.new("RGB", (8, 8), PAPER))


class FontBook:
    """Resolve Windows font files once, then cache ImageFont objects by size."""

    def __init__(self, font_dir: Path):
        self.paths = {}
        self.missing = []
        for role, names in FONT_CANDIDATES.items():
            found = next((font_dir / name for name in names if (font_dir / name).is_file()), None)
            self.paths[role] = found
            if found is None:
                self.missing.append(role)
        self._cache = {}

    def get(self, role: str, size: int):
        size = int(size)
        key = (role, size)
        if key not in self._cache:
            path = self.paths.get(role)
            if path is not None:
                font = ImageFont.truetype(str(path), size)
            else:
                # Pillow >= 10 can scale its bundled fallback font.
                font = ImageFont.load_default(size=size)
            self._cache[key] = font
        return self._cache[key]


# --- text helpers -----------------------------------------------------------

def text_width(text: str, font) -> float:
    return _MEASURE.textlength(text, font=font)


def wrap_text(text: str, font, width: float) -> list:
    """Greedy word wrap; hard-splits a single word that cannot fit."""
    words = text.split()
    if not words:
        return [""]
    lines = []
    current = ""
    for word in words:
        trial = word if not current else current + " " + word
        if text_width(trial, font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    out = []
    for line in lines:
        if text_width(line, font) <= width:
            out.append(line)
            continue
        piece = ""
        for char in line:
            if text_width(piece + char, font) <= width or not piece:
                piece += char
            else:
                out.append(piece)
                piece = char
        if piece:
            out.append(piece)
    return out


def fit_size(fonts: FontBook, role: str, text: str, width: float,
             start: int, minimum: int) -> tuple:
    """Shrink from ``start`` until the text fits on one line (or hit minimum)."""
    size = start
    while size > minimum:
        font = fonts.get(role, size)
        if text_width(text, font) <= width:
            return font, size
        size -= 1
    return fonts.get(role, minimum), minimum


def fit_wrapped(fonts: FontBook, role: str, text: str, width: float,
                start: int, minimum: int, max_lines: int) -> tuple:
    """Shrink from ``start`` until the wrapped text uses at most ``max_lines``."""
    size = start
    while size > minimum:
        font = fonts.get(role, size)
        if len(wrap_text(text, font, width)) <= max_lines:
            return font, size
        size -= 1
    return fonts.get(role, minimum), minimum


def ellipsize(text: str, font, width: float) -> str:
    if text_width(text, font) <= width:
        return text
    trimmed = text
    while trimmed and text_width(trimmed + "\u2026", font) > width:
        trimmed = trimmed[:-1].rstrip()
    return (trimmed + "\u2026") if trimmed else ""


def format_doc_date(value: str) -> str:
    """2026-12-02 -> 'December 2, 2026' (locale independent, deterministic)."""
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{MONTHS[parsed.month - 1]} {parsed.day}, {parsed.year}"


# --- body parsing -----------------------------------------------------------

def split_blocks(text: str) -> list:
    """Split page text on blank lines into blocks of non-empty source lines."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for raw_block in normalized.split("\n\n"):
        lines = [line.rstrip() for line in raw_block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            blocks.append(lines)
    return blocks


def is_caps_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 52:
        return False
    letters = [char for char in stripped if char.isalpha()]
    if len(letters) < 3:
        return False
    return stripped == stripped.upper() and stripped != stripped.lower()


def classify(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "text"
    if is_caps_heading(stripped):
        return "heading"
    if stripped.startswith("- "):
        return "bullet"
    if stripped.startswith("[ ] "):
        return "checkbox"
    if stripped.endswith(":") and len(stripped) <= 60:
        return "lead"  # "What to include in your appeal:" lead-in
    return "text"


# --- layout -----------------------------------------------------------------

def layout_letterhead(ops: list, fonts: FontBook, doc: dict) -> float:
    """Page 1: agency, rule, date, recipient block, title, subject."""
    y = float(MARGIN_TOP)
    agency = (doc.get("agency") or "").strip()
    date_text = format_doc_date(doc.get("doc_date") or "")

    if agency:
        agency_font, agency_size = fit_size(
            fonts, "sans_bold", agency, TEXT_W, AGENCY_SIZE, 20
        )
        agency_lines = wrap_text(agency, agency_font, TEXT_W)
        for line in agency_lines:
            ops.append(("text", (MARGIN_X, y), line, agency_font, GREEN))
            y += round(agency_size * 1.26)
        y += 10
        ops.append(("line", (MARGIN_X, y, PAGE_W - MARGIN_X, y), RULE_STRONG, 2))
        y += 26

    if date_text:
        date_font = fonts.get("sans", DATE_SIZE)
        ops.append((
            "text",
            (PAGE_W - MARGIN_X - text_width(date_text, date_font), y),
            date_text,
            date_font,
            INK_SOFT,
        ))
        y += round(DATE_SIZE * 1.5)

    y += 14
    recipient_font = fonts.get("sans", SMALL_SIZE)
    for line in doc.get("recipient_lines") or []:
        line = str(line).strip()
        if line:
            ops.append(("text", (MARGIN_X, y), line, recipient_font, INK_SOFT))
            y += round(SMALL_SIZE * 1.33)
    y += 22

    title = (doc.get("title") or "").strip()
    if title:
        title_font, title_size = fit_wrapped(
            fonts, "serif_bold", title, TEXT_W, TITLE_SIZE, 36, 2
        )
        for line in wrap_text(title, title_font, TEXT_W):
            ops.append(("text", (MARGIN_X, y), line, title_font, INK))
            y += round(title_size * 1.16)

    subject = (doc.get("subject") or "").strip()
    if subject:
        subject_font = fonts.get("sans_italic", SUBJECT_SIZE)
        y += 12
        for line in wrap_text(subject, subject_font, TEXT_W):
            ops.append(("text", (MARGIN_X, y), line, subject_font, SUBJECT_GRAY))
            y += round(SUBJECT_SIZE * 1.34)

    return y + 24


def layout_compact_header(ops: list, fonts: FontBook, doc: dict) -> float:
    """Page 2+: small agency name left, document title right, thin rule."""
    y = float(MARGIN_TOP)
    agency = (doc.get("agency") or "").strip()
    title = (doc.get("title") or "").strip()

    size = SMALL_SIZE
    while size > 13:
        left_font = fonts.get("sans_bold", size)
        right_font = fonts.get("sans", size)
        if text_width(agency, left_font) + 30 + text_width(title, right_font) <= TEXT_W:
            break
        size -= 1

    left_font = fonts.get("sans_bold", size)
    right_font = fonts.get("sans", size)
    if agency:
        ops.append(("text", (MARGIN_X, y), agency, left_font, GREEN_SOFT))
    if title:
        right = ellipsize(
            title, right_font, max(60.0, TEXT_W - text_width(agency, left_font) - 30)
        )
        ops.append((
            "text",
            (PAGE_W - MARGIN_X - text_width(right, right_font), y),
            right,
            right_font,
            INK_SOFT,
        ))
    y += round(size * 1.55)
    ops.append(("line", (MARGIN_X, y, PAGE_W - MARGIN_X, y), RULE, 2))
    return y + 22


def layout_body(ops: list, fonts: FontBook, page_text: str,
                start_y: float, scale: float) -> tuple:
    """Lay out one page's body. Returns (end_y, rendered_text_row_count).

    ``scale`` shrinks body/heading type and leading together when the page is
    long; headings, bullets, checkboxes and hard line breaks keep their
    structure while everything gets proportionally tighter.
    """
    body_size = max(16, round(BODY_SIZE * scale))
    head_size = max(18, round(HEAD_SIZE * scale))
    f_text = fonts.get("sans", body_size)
    f_bold = fonts.get("sans_bold", body_size)
    f_head = fonts.get("sans_bold", head_size)

    base_lead = max(20, round(body_size * 1.40))
    tight_lead = max(18, round(body_size * 1.22))
    para_gap = max(8, round(body_size * 0.66))
    head_above = max(12, round(head_size * 1.02))
    head_lead = max(20, round(head_size * 1.30))
    head_below = max(4, round(body_size * 0.30))
    item_gap = max(3, round(body_size * 0.26))

    blocks = split_blocks(page_text)
    y = float(start_y)
    text_rows = 0

    for block_index, block in enumerate(blocks):
        first_kind = classify(block[0])
        if block_index:
            y += head_above if first_kind == "heading" else para_gap
        after_line_break = False

        for line_index, raw_line in enumerate(block):
            kind = classify(raw_line)
            line = raw_line.strip()

            if kind == "heading":
                if line_index:
                    y += head_above
                accent = (MARGIN_X - 18, y + 3, MARGIN_X - 11, y + head_lead - 5)
                ops.append(("rect", accent, GREEN_SOFT, None, 0))
                for row in wrap_text(line, f_head, TEXT_W):
                    ops.append(("text", (MARGIN_X, y), row, f_head, GREEN))
                    y += head_lead
                    text_rows += 1
                y += head_below
                after_line_break = False

            elif kind == "bullet":
                if line_index:
                    y += item_gap
                text_x = MARGIN_X + 32
                ops.append((
                    "text",
                    (MARGIN_X + 10, y + round(body_size * 0.04)),
                    "\u2022",
                    f_bold,
                    GREEN_SOFT,
                ))
                for row in wrap_text(line[2:], f_text, TEXT_W - 32):
                    ops.append(("text", (text_x, y), row, f_text, INK))
                    y += base_lead
                    text_rows += 1
                after_line_break = False

            elif kind == "checkbox":
                if line_index:
                    y += item_gap
                text_x = MARGIN_X + 36
                box_size = max(13, round(body_size * 0.70))
                box_top = y + max(2, round(body_size * 0.13))
                box = (MARGIN_X + 2, box_top, MARGIN_X + 2 + box_size, box_top + box_size)
                ops.append(("rect", box, None, INK_SOFT, 2))
                for row in wrap_text(line[4:], f_text, TEXT_W - 36):
                    ops.append(("text", (text_x, y), row, f_text, INK))
                    y += base_lead
                    text_rows += 1
                after_line_break = False

            else:
                font = f_bold if kind == "lead" else f_text
                if after_line_break and line_index:
                    # Hard line break inside a block (address / signature
                    # block): use tighter leading than wrapped continuation.
                    y -= base_lead - tight_lead
                for row in wrap_text(line, font, TEXT_W):
                    ops.append(("text", (MARGIN_X, y), row, font, INK))
                    y += base_lead
                    text_rows += 1
                after_line_break = True

    return y, text_rows


# --- painting ---------------------------------------------------------------

def paint(ops: list, img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    for op in ops:
        if op[0] == "text":
            _, xy, text, font, fill = op
            draw.text(xy, text, font=font, fill=fill)
        elif op[0] == "line":
            _, xy, fill, width = op
            draw.line(xy, fill=fill, width=width)
        else:  # rectangle (accent bar or checkbox)
            _, box, fill, outline, width = op
            draw.rectangle(box, fill=fill, outline=outline, width=width)


def draw_footer(draw, fonts: FontBook, page_number: int, page_total: int) -> None:
    font = fonts.get("sans", FOOTER_SIZE)
    draw.line(
        (MARGIN_X, FOOTER_RULE_Y, PAGE_W - MARGIN_X, FOOTER_RULE_Y),
        fill=RULE,
        width=1,
    )
    draw.text((MARGIN_X, FOOTER_TEXT_Y), FOOTER_LEFT, font=font, fill=GRAY)
    label = f"Page {page_number} of {page_total}"
    draw.text(
        (PAGE_W - MARGIN_X - draw.textlength(label, font=font), FOOTER_TEXT_Y),
        label,
        font=font,
        fill=GRAY,
    )


def build_watermark(fonts: FontBook) -> Image.Image:
    """Large diagonal SAMPLE watermark on a transparent page-sized layer."""
    font = fonts.get("sans_bold", WATERMARK_SIZE)
    left, top, right, bottom = _MEASURE.textbbox((0, 0), WATERMARK_TEXT, font=font)
    pad = 60
    layer = Image.new(
        "RGBA", (right - left + 2 * pad, bottom - top + 2 * pad), (0, 0, 0, 0)
    )
    ImageDraw.Draw(layer).text(
        (pad - left, pad - top),
        WATERMARK_TEXT,
        font=font,
        fill=WATERMARK_GRAY + (255,),
    )
    rotated = layer.rotate(32, resample=Image.BICUBIC, expand=True)

    red, green, blue, alpha = rotated.split()
    alpha = alpha.point(lambda value: int(value * 0.08))  # 8% opacity
    rotated = Image.merge("RGBA", (red, green, blue, alpha))

    canvas = Image.new("RGBA", (PAGE_W, PAGE_H), (0, 0, 0, 0))
    canvas.alpha_composite(
        rotated,
        ((PAGE_W - rotated.width) // 2, (PAGE_H - rotated.height) // 2),
    )
    return canvas


def render_page(doc: dict, page_index: int, fonts: FontBook,
                watermark: Image.Image) -> tuple:
    page = doc["pages"][page_index]
    page_total = len(doc["pages"])
    page_number = page.get("n") or page_index + 1

    img = Image.new("RGB", (PAGE_W, PAGE_H), PAPER)
    border_draw = ImageDraw.Draw(img)
    border_draw.rectangle(
        (BORDER_INSET, BORDER_INSET, PAGE_W - BORDER_INSET - 1, PAGE_H - BORDER_INSET - 1),
        outline=BORDER,
        width=2,
    )

    header_ops = []
    if page_index == 0:
        body_top = layout_letterhead(header_ops, fonts, doc)
    else:
        body_top = layout_compact_header(header_ops, fonts, doc)

    # Fit-to-page pass: shrink body type/leading in small steps and re-layout
    # until everything ends above the footer. Deterministic, no clipping.
    body_ops = []
    end_y = body_top
    chosen_scale = 1.0
    rows = 0
    for step in range(0, 13):
        chosen_scale = max(0.64, round(1.0 - 0.03 * step, 2))
        body_ops = []
        end_y, rows = layout_body(
            body_ops, fonts, page.get("text") or "", body_top, chosen_scale
        )
        if end_y <= CONTENT_BOTTOM:
            break

    paint(header_ops + body_ops, img)
    draw_footer(ImageDraw.Draw(img), fonts, page_number, page_total)

    img = img.convert("RGBA")
    img.alpha_composite(watermark)
    img = img.convert("RGB")

    info = {
        "page": page_number,
        "scale": chosen_scale,
        "end_y": end_y,
        "rows": rows,
        "overflow": end_y > CONTENT_BOTTOM,
    }
    return img, info


# --- CLI --------------------------------------------------------------------

def fail(message: str):
    print(f"make_samples: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def load_docs(only: str) -> list:
    if not DOCS_JSON.is_file():
        fail(
            f"cannot find {DOCS_JSON}. Run this script from a checkout that "
            "contains samples/docs.json."
        )
    try:
        data = json.loads(DOCS_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {DOCS_JSON}: {exc}")

    docs = data.get("docs") if isinstance(data, dict) else None
    if not isinstance(docs, list) or not docs:
        fail(f"{DOCS_JSON} has no 'docs' array")

    cleaned = []
    for index, doc in enumerate(docs):
        if not isinstance(doc, dict):
            fail(f"docs[{index}] is not an object")
        doc_id = doc.get("id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            fail(f"docs[{index}] is missing a string 'id'")
        if any(token in doc_id for token in ("/", "\\", "..")):
            fail(f"docs[{index}] id {doc_id!r} is not a safe directory name")
        pages = doc.get("pages")
        if not isinstance(pages, list) or not pages:
            fail(f"document {doc_id!r} has no pages")
        for page_index, page in enumerate(pages):
            if not isinstance(page, dict):
                fail(f"{doc_id} page {page_index + 1} is not an object")
        cleaned.append(doc)

    if only:
        matches = [doc for doc in cleaned if doc["id"] == only]
        if not matches:
            available = ", ".join(doc["id"] for doc in cleaned)
            fail(f"unknown --only id {only!r}. Available: {available}")
        return matches
    return cleaned


def relative(path: Path) -> str:
    try:
        return path.relative_to(APP_ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render samples/docs.json page text to letter-style PNGs."
    )
    parser.add_argument(
        "--only",
        metavar="DOC_ID",
        help="render only this document id (default: every document)",
    )
    args = parser.parse_args(argv)

    docs = load_docs(args.only)

    fonts = FontBook(FONT_DIR)
    if fonts.missing:
        print(
            "make_samples: warning: missing Windows fonts for "
            f"{', '.join(fonts.missing)}; using Pillow's default font instead.",
            file=sys.stderr,
        )

    print("Paper Trail - sample page renderer")
    print(f"  app root: {APP_ROOT}")
    print(f"  docs:     {relative(DOCS_JSON)}")
    print(f"  output:   {relative(PAGES_DIR)}/")
    resolved = ", ".join(
        f"{role}={path.name}" for role, path in fonts.paths.items() if path is not None
    )
    print(f"  fonts:    {resolved or 'Pillow default'}")
    print("")

    watermark = build_watermark(fonts)
    written = 0
    scaled_pages = []
    overflowed_pages = []

    for doc in docs:
        doc_id = doc["id"]
        out_dir = PAGES_DIR / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        for page_index in range(len(doc["pages"])):
            page = doc["pages"][page_index]
            page_number = page.get("n") or page_index + 1
            path = out_dir / f"page-{page_number}.png"
            img, info = render_page(doc, page_index, fonts, watermark)
            try:
                img.save(path, format="PNG", optimize=True)
            except OSError as exc:
                fail(f"cannot write {path}: {exc}")
            size = path.stat().st_size
            note = ""
            if info["scale"] < 0.999:
                note = f"  body scaled to {info['scale']:.2f}"
                scaled_pages.append(f"{doc_id} page {page_number}")
            print(
                f"  {relative(path)}  {img.width}x{img.height}  "
                f"{size:,} bytes  ({info['rows']} text rows){note}"
            )
            if info["overflow"]:
                overflowed_pages.append(f"{doc_id} page {page_number}")
                print(
                    "make_samples: warning: "
                    f"{doc_id} page {page_number} still overflows at the minimum "
                    "scale; shorten the page text or adjust layout.",
                    file=sys.stderr,
                )
            written += 1

    print("")
    if scaled_pages:
        print(f"  auto-scaled to fit: {', '.join(scaled_pages)}")
    if overflowed_pages:
        print(f"  overflow warnings: {', '.join(overflowed_pages)}", file=sys.stderr)
    print(f"{written} page image(s) written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
