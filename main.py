"""
Estate sale photo scanner: uses Google Gemini vision (multimodal) with your
GOOGLE_API_KEY from .env / Images/.env — the same key you keep in project settings.
New lot only when colored estate tape (purple, lavender, green, yellow, etc.) shows a readable lot number (SKU); otherwise detail of current lot.
Each lot is written as Auction title + professional description (**brands**) + bulleted item specifications.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import requests

try:
    from dotenv import load_dotenv

    _root = Path(__file__).resolve().parent
    load_dotenv(_root / ".env", override=False)
    load_dotenv(_root / "Images" / ".env", override=True)
except ImportError:
    pass

API_KEY = (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")).strip()
# Set SKIP_TAPE_VERIFY=1 to skip the second vision check (saves API calls; more false SKU risk).
SKIP_TAPE_VERIFY = os.environ.get("SKIP_TAPE_VERIFY", "").strip().lower() in ("1", "true", "yes")


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Markdown Description column: max chars (0 = no truncation). Default 20000.
REPORT_DESC_MAX_CHARS = _read_int_env("REPORT_DESC_MAX_CHARS", 20000)
# Model IDs change; if you get 404, run: python main.py --list-models
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()
API_VERSION = os.environ.get("GEMINI_API_VERSION", "v1beta").strip().strip("/")


def generate_content_url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL}:generateContent"
    )

LISTINGS_DIR = Path("listings")
REPORT_FILE = Path("Inventory_Report.md")
DB_FILE = Path("inventory.db")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Google occasionally returns these; retry with backoff (same loop as 429).
_TRANSIENT_HTTP = frozenset({429, 502, 503, 504})


def _mime_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "image/jpeg"


def _parse_model_json(text: str) -> object:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _coerce_photo_dict(parsed: object, filename: str) -> dict | None:
    """Gemini sometimes returns [{...}] instead of {...}; normalize to one dict."""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
        print(f"\n{filename}: model returned a JSON list with no object; skipping.")
        return None
    print(f"\n{filename}: model returned {type(parsed).__name__}, expected object; skipping.")
    return None


def _retry_after_seconds(response: requests.Response, fallback: float) -> float:
    """Use Retry-After header when Google returns 429."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return min(fallback, 180)
    try:
        return min(180, float(raw))
    except ValueError:
        return min(fallback, 180)


def list_models_for_key() -> None:
    """Print model ids this API key can use with generateContent (helps fix 404 NOT_FOUND)."""
    if not API_KEY:
        print("Set GOOGLE_API_KEY or GEMINI_API_KEY first (e.g. in Images/.env).")
        return
    base = f"https://generativelanguage.googleapis.com/{API_VERSION}/models"
    headers = {"x-goog-api-key": API_KEY}
    page_token: str | None = None
    print(f"Models with generateContent (API {API_VERSION}):")
    while True:
        params: dict[str, str | int] = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(base, headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            print(f"List models failed: {r.status_code} {r.text[:500]}")
            return
        body = r.json()
        for m in body.get("models", []):
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/") :]
            print(f"  {name}")
        page_token = body.get("nextPageToken")
        if not page_token:
            break
    print(f"\nSet in Images/.env: GEMINI_MODEL=<one-of-the-above>  (current default is {MODEL!r})")


def _cell(value: object, max_len: int = 500) -> str:
    """Single markdown-table cell: no pipes or newlines. max_len <= 0 means do not truncate."""
    if value is None:
        return ""
    text = str(value).replace("|", "/").replace("\n", " ").strip()
    if max_len > 0 and len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _join_list_field(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_cell(x, max_len=120) for x in value if x is not None and str(x).strip()]
        return "; ".join(parts)
    return _cell(value)


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


def tape_sku_opens_new_lot(sku: object) -> bool:
    """
    True only when the model returned a real lot number (digits) for tape SKU.
    Estate tape without a readable number must use SKU null — this catches stray text.
    """
    if sku is None:
        return False
    raw = str(sku).strip()
    if not raw or raw.lower() in ("null", "none", "n/a", "unknown", "undefined"):
        return False
    digits = re.sub(r"\D", "", raw)
    return 1 <= len(digits) <= 12


def merge_lot_catalog_fields(lot: dict, detail: dict) -> tuple[list[str], list[str]]:
    """
    Merge tag reads from a detail frame into the open lot.
    Returns (new_brands, new_labels) that were not already present (case-insensitive).
    """
    new_brands: list[str] = []
    new_labels: list[str] = []

    def _merge_key(key: str, bucket: list[str]) -> None:
        seen: set[str] = set()
        merged: list[str] = []
        for item in _as_str_list(lot.get(key)):
            low = item.lower()
            if low not in seen:
                seen.add(low)
                merged.append(item)
        for item in _as_str_list(detail.get(key)):
            low = item.lower()
            if low not in seen:
                seen.add(low)
                merged.append(item)
                bucket.append(item)
        lot[key] = merged

    _merge_key("Brands", new_brands)
    _merge_key("LabelsAndTags", new_labels)

    seen_s: set[str] = set()
    merged_s: list[str] = []
    for item in _as_str_list(lot.get("SpecificationLines")):
        low = item.lower()
        if low not in seen_s:
            seen_s.add(low)
            merged_s.append(item)
    for item in _as_str_list(detail.get("SpecificationLines")):
        low = item.lower()
        if low not in seen_s:
            seen_s.add(low)
            merged_s.append(item)
    lot["SpecificationLines"] = merged_s

    return new_brands, new_labels


_MATERIALISH = re.compile(
    r"\b(cotton|polyester|silk|wool|linen|spandex|nylon|rayon|bamboo|cashmere|acrylic|"
    r"leather|suede|denim|gore-tex|viscose|modal|fleece|velvet|canvas|mesh|%\s*cotton|%\s*poly|"
    r"made in|imported|rn\s*\d|fabric|fiber|material)\b",
    re.I,
)


def _sentence_mostly_material(s: str) -> bool:
    return bool(_MATERIALISH.search(s))


def _extract_fresh_sentences(base: str, detail_desc: object, max_sentences: int = 2) -> list[str]:
    """Pull 1–2 sentences from the detail photo's Description that are not redundant with base."""
    if not detail_desc or not str(detail_desc).strip():
        return []
    text = str(detail_desc).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    base_low = base.lower()
    base_words = set(re.findall(r"[a-z0-9']+", base_low))
    out: list[str] = []
    for raw in parts:
        s = raw.strip()
        if len(s) < 12:
            continue
        if _sentence_mostly_material(s):
            continue
        sl = s.lower()
        if sl in base_low:
            continue
        words = [w for w in re.findall(r"[a-z0-9']+", sl) if len(w) > 2]
        if not words:
            continue
        overlap = sum(1 for w in words if w in base_words)
        if overlap / len(words) > 0.55:
            continue
        punct = s[-1] if s else ""
        if punct not in ".!?":
            s = s + "."
        out.append(s)
        if len(out) >= max_sentences:
            break
    return out


def _brand_appears_in_prose(brand: str, text: str) -> bool:
    if not brand or not text:
        return False
    if f"**{brand}**".lower() in text.lower():
        return True
    try:
        if re.search(r"\b" + re.escape(brand) + r"\b", text, re.I):
            return True
    except re.error:
        pass
    return False


def _brands_named_in_text(brands: list[str], text: str) -> list[str]:
    """Brands not already written (as word or **bold**) in the prose."""
    return [b for b in brands if b and not _brand_appears_in_prose(b, text)]


def enrich_description_from_detail(
    lot: dict,
    new_brands: list[str],
    new_labels: list[str],
    detail_description: object,
) -> None:
    """Append brand/size sentences into the lot's professional prose field."""
    key = _listing_prose_key(lot)
    base = (lot.get(key) or "").strip()
    blocks: list[str] = []

    missing = _brands_named_in_text(new_brands, base)
    if missing:
        if len(missing) == 1:
            blocks.append(
                f"Same lot — a closer label shot in this sequence confirms the brand {missing[0]}."
            )
        else:
            joined = ", ".join(missing[:-1]) + f", and {missing[-1]}"
            blocks.append(
                "Same lot — closer label shots in this sequence confirm these brands: " + joined + "."
            )

    if new_labels:
        blocks.append(
            "Sizes/fits seen on tags in other frames of this same lot: " + ", ".join(new_labels) + "."
        )

    fresh = _extract_fresh_sentences(base, detail_description, max_sentences=2)
    if fresh:
        blocks.append(" ".join(fresh))

    if not blocks:
        return

    suffix = " " + " ".join(blocks)
    combined = (base + suffix).strip() if base else " ".join(blocks).strip()
    cap = _read_int_env("LOT_DESCRIPTION_MAX_CHARS", 12000)
    if cap > 0 and len(combined) > cap:
        combined = combined[: cap - 1] + "…"
    lot[key] = combined

    if new_labels:
        lot.setdefault("SpecificationLines", [])
        existing = {x.lower() for x in _as_str_list(lot["SpecificationLines"])}
        for lab in new_labels:
            line = f"Size / tag (from another frame in this lot): {lab}"
            if line.lower() not in existing:
                existing.add(line.lower())
                lot["SpecificationLines"].append(line)


def weave_brands_into_description(data: dict) -> None:
    """If brands are not named in prose, prepend them as **bold** markdown."""
    brands = _as_str_list(data.get("Brands"))
    if not brands:
        return
    ensure_listing_shape(data)
    key = _listing_prose_key(data)
    desc = (data.get(key) or "").strip()
    missing = [b for b in brands if b and not _brand_appears_in_prose(b, desc)]
    if not missing:
        return
    if len(missing) == 1:
        prefix = f"**{missing[0]}** — "
    else:
        bolded = ", ".join(f"**{m}**" for m in missing[:-1])
        prefix = f"{bolded}, and **{missing[-1]}** — "
    data[key] = (prefix + desc).strip()


def report_table_has_catalog_columns() -> bool:
    if not REPORT_FILE.exists():
        return True
    try:
        text = REPORT_FILE.read_text(encoding="utf-8")
    except OSError:
        return True
    for line in text.splitlines():
        if line.startswith("| SKU"):
            return "Listing" in line
    return True


def _listing_prose_key(data: dict) -> str:
    if (data.get("ProfessionalDescription") or "").strip():
        return "ProfessionalDescription"
    return "Description"


def ensure_listing_shape(data: dict) -> None:
    """Fill AuctionTitle / ProfessionalDescription / SpecificationLines from legacy keys if needed."""
    if not (data.get("AuctionTitle") or "").strip():
        data["AuctionTitle"] = (data.get("Title") or "").strip()
    if not (data.get("ProfessionalDescription") or "").strip():
        data["ProfessionalDescription"] = (data.get("Description") or "").strip()
    sl = data.get("SpecificationLines")
    if isinstance(sl, list) and sl:
        return
    lines: list[str] = []
    for b in _as_str_list(data.get("Brands")):
        lines.append(f"Brand: {b}")
    for t in _as_str_list(data.get("LabelsAndTags")):
        lines.append(f"Size / tag: {t}")
    data["SpecificationLines"] = lines


def format_listing_cell(data: dict) -> str:
    """Markdown-friendly cell: Auction title + professional description + bulleted specs."""
    ensure_listing_shape(data)
    title = (data.get("AuctionTitle") or "").strip()
    prof = (data.get("ProfessionalDescription") or "").strip()
    lines = _as_str_list(data.get("SpecificationLines"))
    prof_safe = prof.replace("|", "/")
    title_safe = title.replace("|", "/")
    out_parts = [
        f"**Auction Title:** {title_safe}",
        "",
        f"**Professional Description:** {prof_safe}",
        "",
        "**Item Specifications:**",
    ]
    if lines:
        for line in lines:
            out_parts.append("- " + str(line).replace("|", "/").strip())
    else:
        out_parts.append("- (none listed)")
    joined = "<br>".join(out_parts)
    if REPORT_DESC_MAX_CHARS > 0 and len(joined) > REPORT_DESC_MAX_CHARS:
        joined = joined[: REPORT_DESC_MAX_CHARS - 1] + "…"
    return joined


def list_photos(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        found = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    else:
        found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(found, key=lambda p: str(p).lower())


def _gemini_generate_content_text(
    payload: dict, *, log_label: str, timeout: int = 120
) -> str | None:
    """POST generateContent; return first candidate text or None on failure."""
    if not API_KEY:
        return None
    headers = {"Content-Type": "application/json", "x-goog-api-key": API_KEY}
    backoff = 8.0
    for _attempt in range(12):
        try:
            response = requests.post(
                generate_content_url(), json=payload, headers=headers, timeout=timeout
            )
            if response.status_code in _TRANSIENT_HTTP:
                wait = _retry_after_seconds(response, backoff)
                tag = f"{response.status_code}"
                if response.status_code == 429:
                    tag = "429 quota/RPM"
                elif response.status_code in (502, 503, 504):
                    tag = f"{response.status_code} server busy"
                print(f" ({tag}, sleep {wait:.0f}s)", end=" ", flush=True)
                time.sleep(wait)
                if response.status_code == 429:
                    backoff = min(backoff * 2, 180)
                else:
                    backoff = min(max(backoff, 4.0) * 1.5, 90.0)
                continue
            response.raise_for_status()
            body = response.json()
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (requests.HTTPError, KeyError, IndexError) as e:
            if isinstance(e, requests.HTTPError) and e.response is not None:
                if e.response.status_code in _TRANSIENT_HTTP:
                    wait = _retry_after_seconds(e.response, backoff)
                    time.sleep(wait)
                    if e.response.status_code == 429:
                        backoff = min(backoff * 2, 180)
                    else:
                        backoff = min(max(backoff, 4.0) * 1.5, 90.0)
                    continue
                if e.response.status_code == 404:
                    print(
                        f"\n404: model {MODEL!r} not found for API {API_VERSION!r}. "
                        "Run: python main.py --list-models\n"
                        "Then set GEMINI_MODEL=<printed-id> in Images/.env"
                    )
                    return None
            print(f"\nError on {log_label}: {e}")
            return None
        except requests.RequestException as e:
            print(f"\nNetwork error on {log_label}: {e}")
            return None
    print(f"\nGiving up on {log_label} after repeated transient errors (429/502/503/504).")
    return None


def _tape_digits_equal(a: str, b: str) -> bool:
    da = re.sub(r"\D", "", a)
    db = re.sub(r"\D", "", b)
    if not da or not db:
        return False
    try:
        return int(da) == int(db)
    except ValueError:
        return da == db


def verify_tape_sku_for_image(img_path: Path, claimed_digits: str) -> bool | None:
    """
    Second vision pass: True only if colored estate masking tape (purple, lavender, green, or yellow) with this lot # is on the tape.
    False = do not open a new lot from SKU. None = could not verify (caller keeps primary SKU).
    """
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    audit = (
        "You are a strict visual auditor for estate-sale software. Look at ONLY this photo.\n"
        "Answer with one JSON object and these exact keys:\n"
        '- "new_lot_from_tape": boolean — true ONLY if you clearly see purple, lavender, bright green, or bright yellow '
        "MASKING-TAPE (painter's tape) used as a lot marker AND a handwritten or printed lot number "
        "ON THE SURFACE OF THAT TAPE (not a yellow measuring tape with inch/cm scale only).\n"
        '- "lot_digits_on_tape": string or null — digits only as read from the tape (no #); null if none.\n'
        "Set new_lot_from_tape to FALSE if: there is no such tape, tape has no readable number, the number "
        "is only on paper/card/other stickers, or you are unsure.\n"
        "Also FALSE for mistaking: zippers or pulls, hanger clips, metal hooks, shadows, wrinkles, "
        "tiny background specks, colored clothing (not tape), or any digit not clearly ON that estate tape.\n"
        f"The catalog model guessed lot digits: {claimed_digits!r}. "
        "new_lot_from_tape must be false unless those exact digits (leading zeros optional) are what you read ON the tape.\n"
        "Respond with JSON only, no markdown fences."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": audit},
                    {"inline_data": {"mime_type": _mime_for_path(img_path), "data": img_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    text = _gemini_generate_content_text(payload, log_label=f"{img_path.name} (tape verify)", timeout=90)
    if text is None:
        return None
    try:
        obj = _parse_model_json(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    flag = obj.get("new_lot_from_tape")
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("true", "1", "yes")
    elif flag is not True and flag is not False:
        return None
    if flag is not True:
        return False
    on_tape = obj.get("lot_digits_on_tape")
    if on_tape is None or str(on_tape).strip().lower() in ("null", "none", ""):
        return False
    tape_digits = re.sub(r"\D", "", str(on_tape))
    if not tape_digits:
        return False
    return _tape_digits_equal(tape_digits, claimed_digits)


def analyze_photo(img_path: Path) -> dict | None:
    """Call Gemini vision: returns parsed JSON or None on failure."""
    if not API_KEY:
        print("Missing GOOGLE_API_KEY / GEMINI_API_KEY (e.g. in Images/.env next to this project).")
        return None

    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        "You are an expert estate liquidator reviewing one photo for a sale catalog.\n"
        "Rules:\n"
        "- START A NEW LOT ONLY when BOTH are true: (1) you see this sale's colored estate painter's tape "
        "(purple, lavender, bright green, or bright yellow) marking the lot, AND (2) a CLEAR lot number is written or printed ON "
        "that tape. Set 'SKU' to that number as digits only (strip # and spaces).\n"
        "- NEVER invent a lot number from: zippers or metal pulls, hanger clips, hooks, shadows, "
        "backdrop specks, fabric the same color as tape (clothing is not tape), blur, or digits on tags/paper "
        "unless they are clearly printed ON that colored estate painter's tape.\n"
        "- Example of a valid NEW-lot opener: colored tape on a hanger or rack with a handwritten or "
        "printed lot number ON the tape (e.g. #002); a wide shot may show several garments together. "
        "Read SKU only from that tape — not from price stickers elsewhere. Brands or sizes may be "
        "invisible in that same photo; use empty Brands/LabelsAndTags if so, but still set SKU from the tape.\n"
        "- If there is estate tape but NO readable number on the tape, or tape is not visible, or you "
        "are unsure: set 'SKU' to null — never invent a lot number. That frame is still part of the "
        "current lot (detail, angle, or context shot).\n"
        "- If there is no estate tape with a number (detail shot, alternate angle, close-up), "
        "set 'SKU' to null. This image belongs to the same physical lot as the previous hero shot. "
        "For such frames, prioritize readable BRANDS and SIZE/FIT text in Brands, LabelsAndTags, "
        "SpecificationLines, and a short ProfessionalDescription of what this frame clarifies.\n"
        "- Visually search for BRAND NAMES and SIZE/FIT text: logos, printed brand text, woven labels, "
        "hang tags, and size stickers. Ignore care icons unless needed to read size.\n"
        "- 'Brands': JSON array of strings—each string one brand or designer name clearly visible "
        "(empty array if none). Do not guess.\n"
        "- 'LabelsAndTags': JSON array of strings—ONLY sizes and fit reads from tags "
        "(e.g. 'M', 'Size 8', '32x30', 'OSFA', 'Petite P'). Exclude the estate tape SKU number. Use [] if none.\n"
        "- 'AuctionTitle': ONE line for the auction listing title—include each garment piece, brand names, "
        "and sizes (e.g. 'Spartina 449 Boutique Tunic & Chico's Travelers Pleated Trousers Set - Size XL/2').\n"
        "- 'ProfessionalDescription': ONE polished paragraph (4–6 sentences). Describe colors, cut, "
        "hardware, pockets, silhouette, condition, styling. Wrap every brand name in **double asterisks** "
        "the first time it appears in the paragraph (e.g. **Spartina 449**). Put fiber / blend / "
        "\"made in\" facts in SpecificationLines, not long lists in the paragraph (one short clause is OK).\n"
        "- 'SpecificationLines': JSON array of strings; each string ONE bullet line without a leading hyphen. "
        "Use clear labels when several garments appear, e.g. 'Tunic Brand: Spartina 449 (Made in Peru)', "
        "'Tunic Size: XL', 'Pant Brand: Chico's Travelers (Made in USA)', 'Pant Size: Chico's Size 2 (US Large/12 equivalent)', "
        "'Materials: high-quality knit and acetate/spandex blend'. Adapt labels to what is in the photo "
        "(single item → 'Brand:', 'Size:').\n"
        "- 'Condition': short honest phrase (wear, stains, chips if visible).\n"
        "- 'StartingPrice': number only, USD, realistic for estate retail.\n"
        "Return exactly one JSON object with keys: SKU, AuctionTitle, ProfessionalDescription, "
        "SpecificationLines, Brands, LabelsAndTags, Condition, StartingPrice. Do not wrap in an array.\n"
        "Respond with JSON only, no markdown fences."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": _mime_for_path(img_path), "data": img_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    text = _gemini_generate_content_text(payload, log_label=img_path.name, timeout=120)
    if text is None:
        return None
    try:
        parsed = _parse_model_json(text)
        return _coerce_photo_dict(parsed, img_path.name)
    except json.JSONDecodeError as e:
        print(f"\nError on {img_path.name}: {e}")
        return None


def save_to_report(data: dict, photo_count: int) -> None:
    weave_brands_into_description(data)
    sku = _cell(data.get("SKU", "Unknown"), max_len=40)
    listing = format_listing_cell(data)
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write(
            f"| {sku} | {listing} | "
            f"${_cell(data.get('StartingPrice'))} | {_cell(data.get('Condition'), max_len=80)} | {photo_count} |\n"
        )


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def init_db() -> None:
    """Create local SQLite store for structured lots data."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT,
                auction_title TEXT,
                professional_description TEXT,
                specification_lines_json TEXT,
                brands_json TEXT,
                labels_and_tags_json TEXT,
                starting_price REAL,
                condition_text TEXT,
                photo_count INTEGER,
                model_name TEXT,
                api_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lots_sku_created_at ON lots(sku, created_at)"
        )
        conn.commit()


def save_to_db(data: dict, photo_count: int) -> None:
    ensure_listing_shape(data)
    row = (
        _cell(data.get("SKU", "Unknown"), max_len=40),
        _cell(data.get("AuctionTitle"), max_len=0),
        _cell(data.get("ProfessionalDescription"), max_len=0),
        json.dumps(_as_str_list(data.get("SpecificationLines")), ensure_ascii=True),
        json.dumps(_as_str_list(data.get("Brands")), ensure_ascii=True),
        json.dumps(_as_str_list(data.get("LabelsAndTags")), ensure_ascii=True),
        _to_float(data.get("StartingPrice")),
        _cell(data.get("Condition"), max_len=0),
        int(photo_count),
        MODEL,
        API_VERSION,
    )
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO lots (
                sku, auction_title, professional_description, specification_lines_json,
                brands_json, labels_and_tags_json, starting_price, condition_text,
                photo_count, model_name, api_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        conn.commit()


def save_lot_outputs(data: dict, photo_count: int) -> None:
    save_to_report(data, photo_count)
    save_to_db(data, photo_count)


def pick_folder_interactive() -> Path | None:
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askdirectory(title="Select folder of estate sale photos")
    root.destroy()
    return Path(path) if path else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan estate photos with Gemini vision + your API key.")
    parser.add_argument(
        "-f",
        "--folder",
        type=Path,
        help="Folder of images (if omitted, a folder picker opens)",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Include subfolders when scanning for images",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Wait SEC seconds after each API call (spreads load; helps RPM/token burst limits). Example: --delay 2",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List Gemini model ids your API key can call (use if you see 404 NOT_FOUND).",
    )
    args = parser.parse_args()

    if args.list_models:
        list_models_for_key()
        sys.exit(0)

    if args.folder:
        images_dir = args.folder.resolve()
        if not images_dir.is_dir():
            print(f"Not a directory: {images_dir}")
            return
    else:
        picked = pick_folder_interactive()
        if not picked:
            return
        images_dir = picked

    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    if not REPORT_FILE.exists():
        REPORT_FILE.write_text(
            "# Estate Inventory\n\n"
            "| SKU | Listing | Price | Condition | Photos |\n"
            "| :-- | :-- | :-- | :-- | :-- |\n",
            encoding="utf-8",
        )

    photos = list_photos(images_dir, args.recursive)
    if not photos:
        print(f"No images found in {images_dir} ({', '.join(sorted(IMAGE_EXTS))}).")
        return

    current_lot: dict | None = None
    photo_count = 0

    print(
        f"Using model {MODEL!r} (API {API_VERSION}); {len(photos)} image(s). "
        f"Key loaded: {bool(API_KEY)}."
    )
    if not report_table_has_catalog_columns():
        print(
            "Note: Inventory_Report.md has an older table header. Delete or rename that file "
            "to regenerate the Listing column (auction title + professional description + specs)."
        )
    if args.delay > 0:
        print(f"Pacing: {args.delay}s delay after each API call.")
    for photo in photos:
        print(f"Processing {photo.name} ...", end=" ", flush=True)
        data = analyze_photo(photo)
        if args.delay > 0:
            time.sleep(args.delay)
        if not data:
            continue

        weave_brands_into_description(data)

        sku = data.get("SKU")
        if tape_sku_opens_new_lot(sku):
            digits = re.sub(r"\D", "", str(sku).strip())
            data["SKU"] = digits
            if not SKIP_TAPE_VERIFY:
                ok = verify_tape_sku_for_image(photo, digits)
                if ok is False:
                    data["SKU"] = None
                    print(" [SKU cleared: tape+number not confirmed]", end=" ")
                elif ok is None:
                    print(" [tape verify inconclusive — keeping SKU]", end=" ")

        if tape_sku_opens_new_lot(data.get("SKU")):
            digits = re.sub(r"\D", "", str(data.get("SKU")).strip())
            data["SKU"] = digits
            if current_lot:
                save_lot_outputs(current_lot, photo_count)
            current_lot = data
            photo_count = 1
            print(f"[NEW SKU: {digits}]")
        elif current_lot:
            new_brands, new_labels = merge_lot_catalog_fields(current_lot, data)
            enrich_description_from_detail(
                current_lot,
                new_brands,
                new_labels,
                data.get("ProfessionalDescription") or data.get("Description"),
            )
            weave_brands_into_description(current_lot)
            photo_count += 1
            print("[detail for current lot]")
        else:
            print(
                "[skipped: no open lot yet — need colored estate tape with a readable lot number to start]"
            )

    if current_lot:
        save_lot_outputs(current_lot, photo_count)
    print(f"\nDone. Report: {REPORT_FILE.resolve()} | Database: {DB_FILE.resolve()}")


if __name__ == "__main__":
    main()
