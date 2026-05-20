from __future__ import annotations

import asyncio
import atexit
import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import requests
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.config import settings
from app.core.lotting import (
    MERGED_PRO_DESCRIPTION_MAX_CHARS,
    PROFESSIONAL_DESCRIPTION_MAX_WORDS,
    _broaden_auction_title_for_components,
    _drop_prose_sentences_conflicting_with_components,
    _ensure_multi_piece_description,
    _trim_prose_at_limit,
    _trim_prose_to_max_words,
    dedupe_specification_lines_preserve_order,
    infer_brands_from_label_context,
    normalize_brand_list,
    sanitize_description_unknowns,
    sku_digits,
    tape_marking_canonical,
)
from app.services.image_preprocess import prepare_gemini_image_file

TRANSIENT_HTTP = frozenset({429, 502, 503, 504})
TRANSIENT_GENAI_CODES = frozenset({408, 429, 502, 503, 504})

_tape_verify_pool: ThreadPoolExecutor | None = None


def _get_tape_verify_pool() -> ThreadPoolExecutor:
    global _tape_verify_pool
    if _tape_verify_pool is None:
        _tape_verify_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tape_verify")
    return _tape_verify_pool


def _shutdown_tape_verify_pool() -> None:
    global _tape_verify_pool
    if _tape_verify_pool is not None:
        _tape_verify_pool.shutdown(wait=False, cancel_futures=False)
        _tape_verify_pool = None


atexit.register(_shutdown_tape_verify_pool)


def _tape_verify_prompt_extra() -> str:
    extra = (settings.tape_verify_prompt_suffix or "").strip()
    if not extra:
        return ""
    return f"\n\nAdditional instructions:\n{extra}"

# --- google-genai structured row: manifest table + estate catalog extensions ---


class InventoryTableEntry(BaseModel):
    """Response schema for `google-genai` JSON mode; maps to LotOutput.from_model keys."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    manifest_id: str = ""
    item_name: str = Field(
        default="",
        description=(
            "Single-line auction title: **lead with the confirmed brand name** when readable on a label (required for "
            "apparel/shoes with neck or care tags). Then era/style + material/form + garment type — **only subjects clearly "
            "visible**. Never use catalog titles like 'Detail with Zippered Pocket' or 'Close-up of Cuff' without the maker. "
            "For several distinct pieces in one shot, use a compound "
            "title that states quantity and type (e.g. 'Seven ceramic Disney character figurines') rather than naming "
            "only one figure. **Bad** when the table shows many forms: 'Brass Two-Piece Incense Censer' (names only one "
            "subset). **Good**: 'Vintage brass lot: censers, ewer, bell, dragon stand' or 'Brass decorative lot (multiple "
            "forms)'. Never add animals or object types you do not see."
        ),
    )
    brand: str = Field(
        default="",
        description=(
            "Primary maker on the product or label, plain text — **required** when neck/care label text is legible "
            "(e.g. copy 'St. John's Bay' from 'AUTHENTIC ST. JOHN'S BAY ESTABLISHED 1984'). Also copy exact label "
            "phrases into **labels_and_tags**. Leave empty only when no maker name is readable — never use Unknown, "
            "Unidentified, Unbranded, N/A, or similar placeholders."
        ),
    )
    material: str = Field(
        default="",
        description=(
            "Primary material visible for the lot. For **warm-toned cast, spun, or lathe-turned decorative metal** "
            "(e.g. censers, bells, ewers, dragon stands, strikers in one brass-style grouping), use **Brass** or **Bronze** "
            "when that matches the color and form — **do not** default those to Pewter. Use **Pewter** only with legible "
            "pewter/zinn/étain marks or clear cool gray pewter luster. Must match **item_name**, **professional_description**, "
            "and any **Material:** line in **specification_lines**."
        ),
    )
    condition_notes: str = Field(
        default="",
        validation_alias=AliasChoices("condition_notes", "ConditionNotes", "Condition"),
        description=(
            "Exactly **one** allowed condition tier for the whole lot: NEW, LIKE NEW, VERY GOOD, GOOD, or FAIR — "
            "per the rubric in the system prompt. This is the **official** condition field for the listing; do **not** "
            "replace it with a long prose condition report in **professional_description**."
        ),
    )
    era: str = ""
    sku: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku", "SKU"),
        description=(
            "Handwritten or printed lot code on colored estate adhesive tape: PURPLE, LAVENDER, bright NEON GREEN, "
            "or bright YELLOW masking/painter tape (lot marker — not yellow measuring tape). Read sku from focused crop "
            "if provided. Never use stamped manufacturer numbers (e.g. 1420) or silverplate pattern numbers as SKU."
        ),
    )
    sku_read_confidence: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku_read_confidence", "SkuConfidence"),
    )
    professional_description: str = Field(
        default="",
        validation_alias=AliasChoices("professional_description", "ProfessionalDescription"),
        description=(
            "Warm, human estate-sale prose: inviting and readable, **max ~50 words** (**exactly one short paragraph** — "
            "no second paragraph, no alternate 'draft' of the same text). Lead with what the shopper gets and how it displays. "
            "**Do not** write detailed condition here (no patina/oxidation/verdigris essays, scratch inventories, or "
            "re-stating the grade — that belongs in **condition_notes** plus optional brief **Condition note:** in "
            "**specification_lines**). For multi-piece lots give **one** short **lot-level** overview that reflects "
            "**Components** / **Item n:** (e.g. censer + bell together) — **not** two sentences that only deep-dive one "
            "object type that is not the sole piece in the lot, and **not** a second sentence that spotlights **one** coin "
            "after a lot summary when **Components** already lists several dated pieces (common on map + mint-set photos). "
            "For **several cents each on its own dealer card**, one paragraph may summarize the **whole group** (folders + "
            "listed holder dates) — every sentence must match **Components**, not a different coin than the title. "
            "Lead with **what the buyer actually receives** (sellable units). "
            "Per-piece nitpicks and measurements go in **specification_lines**. "
            "**Bold** confirmed makers once when legible. Omit unknown makers."
        ),
    )
    specification_lines: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("specification_lines", "SpecificationLines"),
        description=(
            "Bullet facts as 'Label: value'. For photos with several distinct salable pieces (figurines in a row, "
            "multiple coins/medals, pairs, or a set), add separate lines per distinct item or subgroup — e.g. "
            "'Item A (left): …', 'Item B (center): …', 'Finish (item A): …', 'Visible mark (item B): …'. "
            "**Components:** must list **every** salient separate object (including bells alongside censers/ewers). "
            "**At most one** 'Condition note:' line — one combined sentence for general wear. "
            "For **coin/medal lots**, list **deliverable units first** (map folder, sealed set count, loose groups); then "
            "**denominations you can see** (quarters, dimes, half dollars, cents, dollars) and **visible dates** (single years, "
            "comma list, or ~year–year range). **Do not** treat coins shown only **in map slots or album pages** as separate "
            "**Item n:** lines. **Do** list **each** dated coin on its **own** retail flip / identification card (Littleton-style) "
            "when multiple images show different cards — distinct pieces; read dates from coin **and** cardstock. "
            "If types are unclear, say 'assorted U.S. coinage' rather than inventing. "
            "Include dimension lines when a yellow measuring tape or ruler clearly shows a span (~ inches). "
            "Do not invent numbers without a visible scale."
        ),
    )
    labels_and_tags: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("labels_and_tags", "LabelsAndTags"),
        description=(
            "Verbatim short strings from sewn-in or hang tags (e.g. 'AUTHENTIC ST. JOHN'S BAY ESTABLISHED 1984', "
            "'L/G', 'MADE IN CHINA'). Required when any label is readable; used to set **brand**."
        ),
    )
    starting_price_usd: float | None = Field(
        default=None,
        validation_alias=AliasChoices("starting_price_usd", "StartingPrice"),
    )
    lot_boundary_blank_page: bool = Field(
        default=False,
        validation_alias=AliasChoices("lot_boundary_blank_page", "lotBoundaryBlankPage"),
        description="Deprecated: server ignores blank-divider detection; always leave false.",
    )


class TapeSkuAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    new_lot_from_tape: bool = Field(default=False)
    lot_digits_on_tape: str | None = Field(default=None)


SYSTEM_INSTRUCTION = (
    "You are an AI assistant for Caring Transitions of Enterprise-Las Vegas, owned by Julie Zorn. "
    "Your goal is to analyze photos and generate professional manifest descriptions.\n\n"
    "### THE SINGLE-BRAND RULE:\n"
    "- Identify exactly ONE primary brand. No brand-soup or competing guesses.\n"
    "- 'Czechoslovakia' alone is country-of-origin text, not a maker. Do not default brand to Ditmar-Urbach from origin text alone.\n"
    "- Only list brands or names explicitly visible in the photos or on the hallmarks. Do not include 'related' brands.\n"
    "- If multiple brand names are identified, only list the one that appears most clearly on the physical hallmark. "
    "Do not list 'similar' or 'related' brands.\n"
    "- If no manufacturer mark is clearly visible or confirmed, do not include a brand rather than speculating.\n"
    "- **Apparel / sewn-in labels:** The brand is the name on the **woven or printed neck/care label** (e.g. St. John's Bay, Levi's). "
    "**Do not** use zipper pull engravings, snap vendors, or generic hardware as the brand unless that same name is on the garment label.\n\n"
    "### VISIBLE SUBJECT ONLY (CRITICAL — NO HALLUCINATED KEYWORDS):\n"
    "- In **item_name** and **professional_description**, describe only what is **clearly visible** in the image(s). "
    "Do not add extra animals, figures, or object-type words to sound like a catalog category.\n"
    "- **Flower frog** is a specific ceramic form (flower-arranging frog / pin holder). Use that term **only** if the "
    "piece is visibly that type. A **bird** on a branch with a small cup or well is **not** a frog — say bird figurine, "
    "spill vase, or posy-style form as fits; **never** append 'frog'.\n"
    "- If the exact ceramic term is uncertain, use neutral wording (e.g. 'ceramic bird figurine') rather than guessing.\n\n"
    "### AUCTION TITLE (item_name):\n"
    "- When the maker is confirmed, **start the title with the brand** as the first keywords shoppers search (same spelling as brand).\n"
    "- Typical shape: **Brand** + era or style + material + garment/type (e.g. 'St. John's Bay Vintage Cotton-Blend Zip-Up Jacket').\n"
    "- **Never** title a row from a macro/detail frame alone (e.g. 'Woven Utility Jacket Detail with Zippered Pocket') — "
    "use the **garment + brand** from the full item and label photos.\n"
    "- Example: 'Art Deco Ditmar-Urbach Lusterware Bird Figurine' (not '... Bird Flower Frog' unless a real flower frog is visible).\n"
    "- **Multi-object lots:** the title must be **inclusive** of the main distinct forms on the table (or explicitly say "
    "**lot** / **group** / **multiple forms**) — do **not** name only one piece type (e.g. a two-part censer) when an "
    "ewer, bell, dragon stand, striker, or other salient objects are **clearly** in the same lot.\n\n"
    "### DESCRIPTION LOGIC:\n"
    "- Do **not** transcribe stamps, mold numbers, incised digits, or bottom-mark details in **specification_lines**.\n"
    "- **professional_description**: appearance, style, and **why the lot is appealing** — not a condition report "
    "(see **CONDITION VS PROSE** below).\n\n"
    "### CONDITION VS PROSE (CRITICAL):\n"
    "- **condition_notes** must be exactly **one** of: NEW, LIKE NEW, VERY GOOD, GOOD, FAIR — that single token is the "
    "**official** condition tier for the listing.\n"
    "- **professional_description** must **not** narrate condition in detail: **no** multi-sentence patina/oxidation/"
    "verdigris/\"character wear\"/scratch or dent inventories, and **no** re-explaining the grade. Shoppers have photos "
    "and the tier field.\n"
    "- At most **one short** optional clause in prose (e.g. \"ready to display\") **or** skip wear talk entirely. "
    "General wear (oxidation, tarnish, patina) → **at most one** **Condition note:** line in **specification_lines** "
    "with a **single combined** sentence (e.g. surface oxidation and tarnish consistent with age) — not several separate "
    "Condition note bullets.\n"
    "- **Coins on dealer / Littleton-style cards:** If **Condition note:** quotes the **card's** letter grade (e.g. Very Good (VG)), "
    "do **not** add a **second** conflicting standard grade (e.g. Fine) in the same line — describe toning or circulation "
    "**without** contradicting the card grade.\n\n"
    "### ESTATE SALE VOICE & LENGTH (CRITICAL):\n"
    "- Write like a friendly estate specialist on CTBids: **warm, clear, and scannable** — not a lab report or appraisal.\n"
    "- **professional_description** hard cap: **~50 words max** (**one paragraph only**). Do **not** stack multiple "
    "paragraphs or restate the same ewer/censer/bell/stand details in different wording — say it once, warmly, then stop. "
    "If you have more to say, put it in **specification_lines** (Components, per-item lines, Finish, etc.).\n"
    "- **Do not** pack micro-surface or wear detail into prose (lathe rings, every scratch, grain, patina layers) — "
    "that belongs in **specification_lines** if at all, not in **professional_description**.\n"
    "- Prefer **you / we / this lot** sparingly or neutral third person; avoid stiff phrases like 'The present object exhibits'.\n"
    "- Opening hook: what it is + why it is appealing; material/era in plain English — **not** a second hook that "
    "re-describes the same objects or their wear.\n\n"
    "### METAL IDENTIFICATION (HALLMARK-FIRST):\n"
    "- Read hallmark/stamp text from the item bottom/underside when visible and use that as primary evidence.\n"
    "- If stamp shows '925', 'STERLING', or Lion Passant: identify as Sterling Silver.\n"
    "- If stamp shows 'EPNS', 'Silver Plate', or 'Plate': identify as Silver Plate.\n"
    "- If stamp shows 'Pewter' or 'Zinn': identify as Pewter.\n"
    "- If no hallmark is clearly visible or confirmable, do **not** call it Sterling; use 'silver-tone metal' or "
    "**pewter** only when a pewter/zinn mark or unmistakable **cool gray pewter** luster is visible — **not** as a guess "
    "for dark satin **brass/bronze** table metal.\n"
    "- Luster clues: mirror-like often suggests plate; soft/dull often suggests pewter; warm-white can support sterling "
    "only with hallmark confirmation.\n"
    "- Condition clues: pitting can indicate pewter; base-metal bleed/wear-through can indicate plate.\n"
    "- **Brass / bronze:** warm gold-brown castings, spun or lathe-turned **brass/bronze** tableware often read darker in "
    "photos than shiny yellow brass — do **not** default those lots to pewter. Say **brass**, **bronze**, or "
    "**brass-tone / dark metal** when that is what you see; reserve **pewter** for softer gray-pewter luster or legible "
    "pewter/zinn marks.\n\n"
    "### MATERIAL CONSISTENCY (CRITICAL):\n"
    "- **item_name**, **professional_description**, and **Material:** in **specification_lines** must agree on the base "
    "metal or material (e.g. do not write 'vintage brassware' in prose and **Material: Pewter** in specs).\n"
    "- Pick **one** reading from visible color, weight cues, and any marks; use the same wording (or clear synonyms) "
    "everywhere.\n"
    "- If unsure between brass and pewter, use neutral **metalware**, **dark-toned metal**, or **non-precious metal** "
    "instead of contradicting yourself.\n\n"
    "### MULTI-PIECE LOT COVERAGE (CRITICAL):\n"
    "- When a lot includes multiple distinct forms, cover ALL major visible components in **item_name** + **specification_lines** "
    "(and a short warm **professional_description** overview — not a blow-by-blow of every piece).\n"
    "- **professional_description** must **match the same inventory** as **Components:** / **Item n:** — if those list "
    "a censer and a hand bell, the prose must summarize **that grouping** (or neutrally say 'this brass lot' and nod to "
    "both), **not** a long passage about an ewer or another form that is **not** in the Components line.\n"
    "- Do not collapse the lot to only one repeated subset (e.g., cups/tumblers) if larger or unique pieces are present.\n"
    "- Prioritize the dominant/structural pieces in wording: e.g., flagon/jug, serving tray/platter, then cups/goblets.\n"
    "- Add one concise components line in specification_lines, such as: "
    "'Components: 1 flagon/jug, 1 tray, 6 cups'. Use only clearly visible counts.\n\n"
    "### LACE, LINEN, TEXTILES, AND MULTI-SWATCH LISTINGS (CRITICAL):\n"
    "- When **several** catalog images show **different** lace runners, dresser scarves, valances, trims, doilies, or "
    "panels laid flat with **yellow measuring tapes** that read **different lengths or widths**, this row is a **group lot** "
    "— **item_name** must use inclusive wording (e.g. 'Vintage cotton lace textile lot — multiple runners & pieces'), "
    "**not** a singular 'one textile' title.\n"
    "- **professional_description** must read as a **warm overview of the whole spread** (cream tones, mixed openwork "
    "patterns, who would love it), **not** as if only one rectangle exists when the photos clearly show **separate shapes** "
    "or **non-overlapping tape measurements**.\n"
    "- **Components:** should separate what you can tell apart (e.g. long floral runner; scalloped-edge runner; medallion "
    "panel; curved valance; folded stack). Add **Dimensions:** lines when the tape applies to **that** frame's piece; "
    "different numbers across frames usually mean **different pieces**.\n\n"
    "### MULTI-DISTINCT ITEMS IN ONE FRAME (figurines, coins, jewelry rows):\n"
    "- If several separate salable pieces appear (e.g. a row of character figurines, multiple medals, pairs of earrings), "
    "you MUST NOT summarize as a single generic subject. Name or describe each distinct piece or position "
    "(left / center / right, or count by type).\n"
    "- **brand** field: use the ONE maker that applies to the whole lot only when every piece is clearly the same line; "
    "otherwise leave brand empty and put per-piece marks in **specification_lines** "
    "(e.g. 'Visible mark (3rd from left): …' only if legible).\n"
    "- Never invent a single brand to cover different sculpts, eras, or mismatched bases.\n\n"
    "### COINS, MEDALS, AND GROUPED NUMISMATIC LOTS (CRITICAL):\n"
    "- Prefer **denominations and coin types** you can verify (cent through dollar, commemoratives, foreign pieces only "
    "when visible) plus **visible dates** (from mint envelopes, folder titles, readable obverses/reverses) over "
    "**exact headcounts** when coins overlap, sit in piles, spill from rolls, or accumulate across merged photos — "
    "wrong totals create unhappy buyers.\n"
    "- Say **what is clearly packaged or identifiable** (e.g. '2003 U.S. Mint uncirculated sets (P and D sleeves visible)', "
    "'1999-2008 State Quarters collector map', 'group of loose Sacagawea dollars and state quarters') rather than "
    "'exactly 47 coins' unless every piece is **fully visible and countable** in the images.\n"
    "- When loose coins show **mixed dates** but not every year is readable, you may summarize with a **range** or **list** "
    "of observed years (only from legible coin faces or labels), e.g. 'loose U.S. coins with dates spanning ~1970–1989' or "
    "'dates visible on loose coins include 1970, 1987, 1999' — use **~** when the span is approximate; **do not** invent "
    "years you cannot see.\n"
    "- **Still list dates whenever they are legible** (set year on the envelope, map range like 1999–2008, or coin dates): "
    "put them in **title**, **Era:**, **Components:**, and/or **professional_description** **along with** denominations — "
    "do not drop dates and only name coin types.\n"
    "- When **denominations are clearly identifiable** (size/rim, readable legends, portrait series, or mint-set windows), "
    "**name them** in **Components:** and/or a **Denomination:** line and mirror them briefly in prose/title — e.g. "
    "cents/pennies, nickels, dimes, quarters, half dollars, dollars (e.g. Sacagawea, Eisenhower), **plus** any foreign or "
    "commemorative types you can actually see. If the pile is too mixed to tell, say 'assorted U.S. coinage' instead of "
    "guessing specific types.\n"
    "- **item_name**, **professional_description**, and **Components:** / **Item n:** must **agree on years and set names** "
    "when those appear on packaging — do not title one year, describe another, and list a third in specs without evidence.\n"
    "- Use **one** coherent story for the lot: sealed government sets vs loose change vs map/folder are different **lines** "
    "in **Components:**; do not invent a vintage date set (e.g. 1961) unless that date is legible on the coins or packaging in the photos.\n"
    "- **Dealer / retail flip cards (e.g. Littleton Coin Company blanks):** When a photo shows **one coin** in a window or "
    "cut-out with **printed or handwritten date, grade, mint mark, and/or price** on the cardstock behind it, treat that as "
    "**one salable coin per card** — and when several catalog images each show a different card, those are usually **different "
    "physical cents** (different dates, Indian Head vs Lincoln Wheat, mint marks like **-S**). Read identifiers from **both** "
    "the coin and the card. **Transcribe the handwritten or printed date on each card** as a full **four-digit year** "
    "(and **mint mark** exactly as written, e.g. **1910-S**) into **Components:** or **Item n** for that frame — "
    "every distinct holder photo in the batch must contribute its date so merged listings list **all** cents (e.g. "
    "1904, 1906, 1908, 1910-S). Across merged images, list **every** legible date and type in **Components:** (semicolon or "
    "comma list) or use **Item 1..n** per holder; **item_name**, **professional_description**, and **Components:** must "
    "**agree** on the same set of visible dates — **never** title or open prose with only the first frame's coin if later "
    "frames clearly show other dated holders.\n"
    "- **Deliverables (what the buyer receives):** Open **Components:** (and the first half of **professional_description**) "
    "with the **actual sellable units** the shopper gets — e.g. '1 × 1999–2008 State Quarters collector map folder', "
    "'2 × sealed 2003 U.S. Mint uncirculated sets (P and D sleeves)', then **only if clearly separate** add loose piles "
    "(e.g. 'group of loose dollar coins as shown'). Denominations **inside** a sealed mint set belong in **one** sub-line "
    "(e.g. 'Denominations in sealed sets: cent, nickel, dime, quarter, half, Sacagawea dollar') — **not** five **Item n:** "
    "rows that each mirror one coin from the set.\n"
    "- **Map / folder / album lots:** coins visible **in map slots**, binder pages, or display windows are usually **part of "
    "that product**, not extra sellable pieces — **do not** create **Item n:** lines for a 2001 Rhode Island quarter, etc., "
    "unless that coin is **loose and included outside** the map/sets. Use packaging wording: say **collector's map** / "
    "**folder** as printed — **do not** relabel a State Quarters map as a vague **commemorative folder**.\n"
    "- **professional_description:** one **continuous** short overview — **do not** tack on a second sentence that "
    "spotlights a single date/state coin after the lot summary (reads like a merge glitch). Fold loose extras into the "
    "same paragraph or leave specifics to **Components:**.\n"
    "- **Style:** for map + mint-set lots, use broad **U.S. Mint** / **State Quarters** / **numismatic** style wording — "
    "**avoid** locking **Style:** to one specific quarter obverse (e.g. 'Crossing the Delaware') unless that coin clearly "
    "**is** the entire lot.\n\n"
    "### BRASS / BRONZE METALWARE — SAME TABLE, MANY OBJECTS (CRITICAL):\n"
    "- In these **multi-object brass-lot** table scenes (censers, bells, ewers, dragon stands, strikers together), "
    "**brass or bronze is the usual correct material** — set the JSON **material** field, **item_name**, prose, and "
    "**Material:** spec line all to that (or 'Brass / bronze') unless you **see** pewter/zinn text or true pewter-gray metal.\n"
    "- A group shot may show an ornate **dragon / serpent stand**, **incense burners / censers** (lid + bowl), **bells**, "
    "**ewers / pitchers**, **striker or mallet rods**, and small lids or bases **all at once**. These are **different "
    "inventory lines**, not one object.\n"
    "- **Never** write the listing as only 'dragon piece' or 'dragon stand' when other distinct shapes are clearly "
    "present (elongated spouts, bell domes, vented lids, separate bowls).\n"
    "- **Bells** (hand bell with handle, temple / sphere / crotal bell, bell with clapper) are **separate inventory** from "
    "censers and ewers. If a bell is visible, include it in **Components:**, **item_name**, and the **professional_description** "
    "overview — do **not** drop it because censers or an ewer dominate the photo.\n"
    "- **item_name** for the hero frame must signal quantity or variety: e.g. "
    "'Vintage brass lot: dragon-form stand, incense censers, bell, ewer, striker' — or "
    "'Collection of decorative brass table pieces (multiple forms)'.\n"
    "- **professional_description**: one **warm overview** of the **whole visible lot** (what is on the table together, "
    "the vibe, who might love it) — name-check the **main mix** that matches **Components** (e.g. etched censer + hand bell), "
    "not a zoom-in on one object type only. **Do not** write a separate mini-paragraph for every single piece, and **do not** "
    "paste another full condition pass — use **specification_lines** + **Components:** for inventory and **condition_notes** "
    "for the tier.\n"
    "- **specification_lines**: required **Components:** line listing **every** salient separate object "
    "(e.g. 'Components: 1 dragon-form brass stand with striker, 2 small footed incense censers with pierced lids, "
    "1 bell with clapper, 1 slender spouted ewer, 1 thin brass rod'). Count only what you see.\n"
    "- **Layered outfit / wardrobe lots:** If several photos show the **same hanger stack**, inventory is the "
    "**number of separate garments** (typically 2–6), not the number of photos. Use **Item 1..n** only for **n physical "
    "pieces** — never restart **Item 1:** on each angle. **No Components line** for clothing/shoe sets — Item rows are enough.\n"
    "- **One inventory pass only:** if you add **Item 1:** … **Item n:** bullets (or a full **Components:** list), "
    "do **not** start a second block that re-lists the same pieces (no duplicate Item rows, no extra **Subject:** / "
    "**Form:** lines that only regroup the same inventory — use Subject/Form only for a true lot-wide theme not already "
    "covered by those bullets).\n"
    "- Detail-only frames (inside of bell, lid next to bowl): still add facts about **that** component; "
    "do not assume the reader knows it belongs with the stand from another photo.\n\n"
    "### FINISHES AND SURFACES (DO NOT DEFAULT TO DECOR STYLES):\n"
    "- **Finish** means physical surface: glazed ceramic, satin metal, polished chrome, textured enamel, patinated brass, "
    "matte paint — describe what you see (sheen, texture, wear), not a style movement unless the whole piece clearly fits.\n"
    "- Use **Lusterware**, **Faux Bois**, or **Geometric Art Deco** only when that finish or decoration is **visibly** "
    "present (iridescent metallic glaze, wood-grain slip, strong stepped geometry). If unsure, say 'glazed ceramic' or "
    "'painted ceramic' instead of inventing a collectible finish name.\n"
    "- For metals: finish (polished / brushed / satin) is separate from whether it is sterling vs plate — follow hallmark evidence.\n\n"
    "### DIMENSIONS FROM PHOTOS (specification_lines):\n"
    "- If a **yellow measuring tape**, metal ruler, or yardstick appears and the readout is legible, add one line per "
    "clear measurement to **specification_lines** (not colored estate lot tape — that is SKU only).\n"
    "- Use 'Label: value' with units (prefer inches: ~10 in., ~4.5 in.). Prefix with ~ for estimates from the photo.\n"
    "- Name what was measured: e.g. 'Overall width (set): ~10 in. (tape along front)', "
    "'Height (tallest figurine): ~4.5 in. (tape vertical)', 'Height (center piece): ~3.5 in.'\n"
    "- For multi-piece lots, separate lines for overall span vs individual heights when the tape shows them.\n"
    "- If no scale is visible or numbers are unreadable, omit dimension lines — do not guess.\n\n"
    "### CERAMICS & POTTERY ACCURACY RULES (STRICT):\n"
    "- STRIKE RULE: If the item is marked 'Czechoslovakia', you are FORBIDDEN from using the brand 'Royal Bonn' or origin 'Germany'. These are mutually exclusive.\n"
    "- If 'CM-R' or 'CMHR' is present: This is C.M. HUTSCHENREUTHER ownership. NEVER identify as 'Carl-Moritz Reichenbach'.\n"
    "- 'Star' mark = Haas & Czjzek (Schlaggenwald).\n"
    "- 'Shield' mark = Utilitarian Schlaggenwald.\n"
    "- Assign DITMAR-URBACH only when hallmark evidence supports it (e.g., known red circular Ditmar-style stamp with matching wording/mark structure), not from 'Czechoslovakia' text alone.\n"
    "- 'MZ Altrohlau' = Moritz Zdekauer factory.\n"
    "- DECISIVENESS: Return exactly ONE primary brand. Do not provide a list of competing guesses (no Brand Soup).\n\n"
    "Identify brand, model, material, the **condition tier** (condition_notes), and era from the image. Tone: **warm estate-sale listing**, concise, "
    "never word-splurging. "
    "When two images are provided, image 1 is the full frame; image 2 is a high-contrast crop of colored estate lot tape "
    "(purple, lavender, neon green, or yellow masking tape) — prioritize image 2 for SKU. "
    "SKU must appear on that estate tape only (not yellow measuring tape, not rulers). "
    "Never use sewn tags, size tags, or stamped numbers on the item body as SKU. "
    "Condition must use exactly one of: NEW, LIKE NEW, VERY GOOD, GOOD, FAIR. "
    "Output must match JSON schema only."
)


def _user_prompt(*, manifest_tag: str) -> str:
    tag_line = f"Manifest / batch tag: {manifest_tag.strip()}\n" if manifest_tag.strip() else ""
    return (
        tag_line
        + "Analyze this catalog photo for an estate inventory row.\n"
        + "If several distinct salable pieces are visible, put the **inventory breakdown** in **specification_lines** "
        "(Components + short per-item bullets). In **professional_description** give only a **tight, warm overview** "
        "(~50 words max, **one paragraph**) — do not repeat every bullet in prose or add a second paragraph.\n"
        + "Surface / finish: describe visible sheen and material (e.g. glazed ceramic, polished vs satin metal). "
        "Only call out **Lusterware**, **Faux Bois**, or strong **Art Deco** styling when clearly visible; "
        "otherwise use neutral accurate terms — do not guess a decorative finish label.\n"
        + "RULES:\n"
        "- item_name: One-line auction title — **brand first** when a sewn-in or product label is legible (apparel, shoes, "
        "handbags). For **multiple brass/metal objects on one surface**, list object categories (stand, censer, bell, ewer…) — "
        "do **not** title after only the largest silhouette. **Do not** use 'Detail with …' / 'Close-up of …' as the title. "
        "Confirmed maker required in title when the label name is readable. Pattern: **Brand** + material + garment/type.\n"
        "- brand: ONE primary maker when a single branded piece dominates; for **coordinated multi-brand outfits** "
        "(jacket + cardigan + shoes from different labels), leave **brand** empty and list each maker on the matching "
        "**Item n:** line; all makers may appear in **Brands** when the schema allows.\n"
        "- **Apparel:** Brand = **sewn-in neck/care label** text only — never Prada/Gucci/etc. from a zipper pull unless the same "
        "name is on the garment label. **Copy the maker into the JSON ``brand`` field** and repeat the label line in "
        "**labels_and_tags** (e.g. AUTHENTIC ST. JOHN'S BAY ESTABLISHED 1984). Add **Brand:** in **specification_lines** "
        "when the label name is readable. **Multi-brand wardrobe lots:** add **Brand: Tribal; Alfred Dunner; White Mountain** "
        "(semicolon-separated) when several distinct labels are legible — not only the first jacket brand.\n"
        "- **Apparel condition:** Use the NEW…FAIR tier in **condition_notes**; do not write oxidation/tarnish/patina essays "
        "(those are for metal/coins). **Components** for jackets: closure, pockets, lining, collar — not coin-holder parts.\n"
        "- **Apparel / shoes wardrobe lots:** Use **Item 1:** … **Item n:** only (one row per **salable** piece — "
        "one jacket, one cardigan, one pair of shoes, one pair of trousers). **Never** list a single left/right shoe "
        "as its own Item when the pair is in the lot. **Do not** add **Components:** or extra **Size:** spec lines "
        "(sizes belong in Item text and **labels_and_tags**). Put label text in **labels_and_tags** only.\n"
        "- **Footwear style:** Call open-toe perforated shoes **clogs**, **mules**, or **wedge clogs** when visible — "
        "**not** Mary Jane (Mary Jane has a closed strap across the instep; peep-toe / open-toe is different).\n"
        "- Treat 'Czechoslovakia' / country text as origin unless a specific maker hallmark is visible.\n"
        "- If no manufacturer mark is clearly visible or confirmed, leave brand empty (do not guess).\n"
        "- For metal items, read and quote visible hallmark text in specs; metal type must follow hallmark-first rules.\n"
        "- **Coins / medals / tokens:** state **what ships as a unit** (sets, map folder, rolls) before loose piles; when "
        "denominations are clear, **list them** with **readable dates** — date range or discrete years as allowed above; "
        "**do not** guess exact pile counts. **Littleton-style (or similar) flip cards:** one coin per card per photo — when "
        "dates differ across images, list **all** legible dates/types in **Components** (or **Item n:**); do not title only "
        "the first coin. **Do not** add **Item n:** rows for coins visible only inside a map/binder unless "
        "they are loose outside it. One **professional_description** paragraph — no contradictory second sentence naming a "
        "different date than **Components**. If types are unclear, use 'assorted U.S. coinage'. Keep **title, prose, and "
        "Components** aligned on every visible holder date.\n"
        "- **material** (JSON field): For the usual **warm-toned brass-lot** layouts (censers, bells, ewers, stands), "
        "put **Brass** or **Bronze** when that matches what you see — **not Pewter** unless you read pewter/zinn or see "
        "clear cool-gray pewter metal. Same value must match **Material:** in specification_lines.\n"
        "- **Material:** must match **item_name** and **professional_description** (no brass prose + pewter Material line). "
        "Brass/bronze decorative metalware is not pewter unless marks or clear pewter-gray luster say so.\n"
        "- professional_description: Estate-sale warmth; **~50 words max**, **one paragraph only**. **Lot-level** overview "
        "that matches **Components** / **Item n:** (e.g. mention censer + bell together if that is the lot) — **not** only "
        "one object type. For **coins**, lead with what the buyer **actually receives** (map, sets, loose groups, dated holder "
        "cards) and **do not** end with a sentence about a coin/date that **Components** does not list. **not** detailed condition (no patina/oxidation/wear essays; that is "
        "**condition_notes** + optional **Condition note:** spec bullet). **bold** makers only when confirmed. Inventory → "
        "specification_lines.\n"
        "- condition_notes: Exactly **one** value from NEW, LIKE NEW, VERY GOOD, GOOD, FAIR — the official grade; do not "
        "duplicate that story again in professional_description.\n"
        "  * NEW: no signs of wear; may include tags/packaging.\n"
        "  * LIKE NEW: clearly unused, but tags may be missing.\n"
        "  * VERY GOOD: minor signs of wear (light scuffs/scratches).\n"
        "  * GOOD: moderate signs of wear (moderate scuffs/scratches).\n"
        "  * FAIR: heavy wear and visible scratches/damage.\n"
        "- specification_lines: 'Label: value' — Material, Style, **Finish** (surface sheen/texture), Era, **at most one** "
        "**Condition note:** (one combined sentence for general wear, e.g. oxidation/tarnish consistent with age), **and dimensions** "
        "from a visible yellow tape or ruler. "
        "Never use colored **estate** tape numbers as dimensions. "
        "For multi-piece lots: include 'Components: …' AND at most **one** line per distinct piece when you use numbered "
        "items (e.g. 'Item 1: …', 'Item 2: …' — do not re-run Item 1..n or add Subject/Form rows that only repeat that list). "
        "If you skip per-item lines, you may use one Subject and/or one Form for the lot — not both a full item list and "
        "a second pass under other headings. "
        "Never include a Markings line or stamp/mold-number transcription.\n"
        "- sku: Lot marking on purple, lavender, bright green, or bright yellow estate masking tape ONLY "
        "(same code you would read from image 2 when provided). null if not on tape.\n"
    )


def _normalize_condition_label(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "GOOD"
    norm = re.sub(r"\s+", " ", text).strip().lower()

    if norm == "new":
        return "NEW"
    if norm in {"like new", "likenew"}:
        return "LIKE NEW"
    if norm in {"very good", "verygood"}:
        return "VERY GOOD"
    if norm == "good":
        return "GOOD"
    if norm == "fair":
        return "FAIR"

    if "new with tag" in norm or "new w/" in norm:
        return "NEW"
    if "like new" in norm or "unused" in norm:
        return "LIKE NEW"
    if "very good" in norm or "minor" in norm or "light scuff" in norm or "light scratch" in norm:
        return "VERY GOOD"
    if "fair" in norm or "heavy wear" in norm or "heavy scratch" in norm:
        return "FAIR"
    if "good" in norm or "moderate" in norm:
        return "GOOD"

    return "GOOD"


_BRASS_NARRATIVE_RE = re.compile(
    r"\b(brass|brassware|bronze|ormolu|gilt\s*metal|spelter)\b",
    re.I,
)
_PEWTER_TOKEN_RE = re.compile(r"\b(pewter|zinn|étain|etain)\b", re.I)


def _spec_lines_without_material_label(specs: list[str]) -> list[str]:
    return [s for s in specs if not re.match(r"^\s*material\s*:", s.strip(), re.I)]


def _infer_metal_material(
    *, material: str, specs: list[str], description: str, title: str
) -> str | None:
    """
    Infer a single Material: upsert value. Excludes existing ``Material:`` spec lines from the blob so a wrong
    model-only pewter bullet cannot self-reinforce; resolves brass-in-prose vs pewter-in-schema contradictions when
    pewter is not repeated on non-Material spec lines.
    """
    narrative = f"{description} {title}".strip().lower()
    specs_non_mat = _spec_lines_without_material_label(specs)
    body_no_mat_specs = " ".join(specs_non_mat).lower()
    mat_low = (material or "").strip().lower()

    brass_in_narrative = bool(_BRASS_NARRATIVE_RE.search(narrative))
    pewter_in_non_material_specs = bool(_PEWTER_TOKEN_RE.search(body_no_mat_specs))
    pewter_in_material_field = bool(_PEWTER_TOKEN_RE.search(mat_low))
    pewter_in_material_spec_lines = any(
        _PEWTER_TOKEN_RE.search(s)
        for s in specs
        if re.match(r"^\s*material\s*:", s.strip(), re.I)
    )

    if brass_in_narrative and (pewter_in_material_field or pewter_in_material_spec_lines) and not pewter_in_non_material_specs:
        # Pewter only came from the structured material field and/or a ``Material:`` line — prose/title says brass.
        return "Brass"

    evidence = " ".join([material, description, title, *specs_non_mat]).lower()
    has_pewter = bool(_PEWTER_TOKEN_RE.search(evidence))
    has_plate = any(k in evidence for k in ("silver plate", "silverplate", "epns"))
    has_sterling = any(k in evidence for k in (" sterling", "sterling ", " 925", "925 "))

    # Hallmark-first preference: explicit pewter/zinn/etain should override loose silver-plate wording.
    if has_pewter:
        return "Pewter"
    if has_sterling:
        return "Sterling Silver"
    if has_plate:
        return "Silver Plate"
    return None


def _upsert_material_spec(specs: list[str], material_value: str | None) -> list[str]:
    out = [s for s in specs if not re.match(r"^\s*material\s*:", s.strip(), re.I)]
    if material_value:
        out.append(f"Material: {material_value}")
    return out


def inventory_table_entry_to_lot_payload(row: InventoryTableEntry) -> dict[str, Any]:
    sku_clean = tape_marking_canonical(row.sku)
    title = (row.item_name.strip() or (f"Estate lot #{sku_clean}" if sku_clean else "Estate catalog item"))
    specs = [
        s
        for s in row.specification_lines
        if s.strip() and not re.match(r"^\s*markings?\s*:", s.strip(), re.I)
    ]
    desc = row.professional_description.strip()
    metal_material = _infer_metal_material(
        material=row.material.strip(),
        specs=specs,
        description=desc,
        title=title,
    )
    material_value = metal_material or row.material.strip() or None
    if not material_value and _BRASS_NARRATIVE_RE.search(f"{desc} {title}".strip().lower()):
        material_value = "Brass"
    specs = _upsert_material_spec(specs, material_value)
    if row.era.strip() and not any(s.lower().startswith("era:") for s in specs):
        specs.append(f"Era: {row.era.strip()}")
    specs = dedupe_specification_lines_preserve_order(specs)
    title = _broaden_auction_title_for_components(title, specs)
    desc = sanitize_description_unknowns(desc)
    desc = _drop_prose_sentences_conflicting_with_components(desc, specs)
    desc = _trim_prose_to_max_words(desc, max(1, PROFESSIONAL_DESCRIPTION_MAX_WORDS - 8))
    desc = _ensure_multi_piece_description(desc, specs)
    desc = _trim_prose_to_max_words(desc, PROFESSIONAL_DESCRIPTION_MAX_WORDS)
    desc = _trim_prose_at_limit(desc, MERGED_PRO_DESCRIPTION_MAX_CHARS)

    desc = desc or title
    brand_list = normalize_brand_list([row.brand.strip()] if row.brand.strip() else [])
    brand_list = normalize_brand_list(
        brand_list
        + infer_brands_from_label_context(row.labels_and_tags, specs, title, desc)
    )
    return {
        "SKU": sku_clean or None,
        "AuctionTitle": title,
        "ProfessionalDescription": desc,
        "SpecificationLines": specs,
        "Brands": brand_list,
        "brand": brand_list[0] if brand_list else "",
        "LabelsAndTags": row.labels_and_tags,
        "Condition": _normalize_condition_label(row.condition_notes),
        "StartingPrice": row.starting_price_usd,
        "SkuConfidence": row.sku_read_confidence,
        "lot_boundary_blank_page": row.lot_boundary_blank_page,
    }


_genai_client: Any = None


def _get_genai_client() -> Any | None:
    global _genai_client
    key = (settings.google_api_key or "").strip()
    if not key or _genai_client: return _genai_client
    from google import genai
    _genai_client = genai.Client(api_key=key)
    return _genai_client


def _analyze_photo_sdk_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    client = _get_genai_client()
    if not client: return None
    from google.genai import types as genai_types
    
    focus_note = (
        "\nA second image follows: high-contrast crop of colored estate lot tape. Read SKU from this crop."
        if focus_jpeg
        else ""
    )
    contents = [
        _user_prompt(manifest_tag=manifest_tag) + focus_note,
        genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)),
    ]
    if focus_jpeg:
        contents.append(genai_types.Part(inline_data=genai_types.Blob(mime_type="image/jpeg", data=focus_jpeg)))

    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=InventoryTableEntry,
        system_instruction=SYSTEM_INSTRUCTION,
    )

    try:
        response = client.models.generate_content(model=settings.gemini_model, contents=contents, config=config)
        if response.parsed:
            row = InventoryTableEntry.model_validate(response.parsed)
            return inventory_table_entry_to_lot_payload(row)
    except Exception:
        return None
    return None


def _model_url() -> str:
    return f"https://generativelanguage.googleapis.com/{settings.gemini_api_version}/models/{settings.gemini_model}:generateContent"


def _generate_content(payload: dict[str, Any], timeout: int = 120) -> str | None:
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.google_api_key}
    for _ in range(3): # Shorter retry loop for responsiveness
        try:
            response = requests.post(_model_url(), headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            time.sleep(2)
    return None


def _analyze_photo_rest_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = _user_prompt(manifest_tag=manifest_tag) + "\nReturn JSON matching schema. Decisiveness required."
    parts = [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": img_b64}}]
    if focus_jpeg:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(focus_jpeg).decode("utf-8")}})
    
    payload = {"contents": [{"parts": parts}], "generationConfig": {"response_mime_type": "application/json"}}
    text = _generate_content(payload)
    if not text: return None
    try:
        parsed = json.loads(re.sub(r"```json|```", "", text).strip())
        row = InventoryTableEntry.model_validate(parsed)
        return inventory_table_entry_to_lot_payload(row)
    except Exception:
        return None


def analyze_photo_from_bytes(image_bytes: bytes, mime_type: str, **kwargs) -> dict[str, Any] | None:
    return _analyze_photo_sdk_from_bytes(image_bytes, mime_type, **kwargs) or _analyze_photo_rest_from_bytes(image_bytes, mime_type, **kwargs)


def analyze_path_with_retries(photo_path: Path, *, manifest_tag: str = "") -> dict[str, Any] | None:
    from app.services.tape_focus import estate_tape_focus_jpeg_from_path
    focus_jpeg = None
    if settings.tape_focus_enabled:
        try: focus_jpeg = estate_tape_focus_jpeg_from_path(photo_path)
        except Exception: pass

    img_bytes, mime = prepare_gemini_image_file(photo_path, max_edge=settings.gemini_image_max_edge, quality=settings.gemini_jpeg_quality)
    return analyze_photo_from_bytes(img_bytes, mime, manifest_tag=manifest_tag, focus_jpeg=focus_jpeg)


async def analyze_photos_parallel_async(
    photo_paths: list[Path],
    *,
    manifest_tag: str = "",
    limit: int | None = None,
    on_each: Callable[[int, int, Path, dict[str, Any] | None], None] | None = None,
) -> list[dict[str, Any] | None]:
    sem = asyncio.Semaphore(max(1, settings.gemini_parallelism if limit is None else limit))
    total = len(photo_paths)

    async def _one(idx: int, path: Path) -> tuple[int, dict[str, Any] | None]:
        result: dict[str, Any] | None = None
        try:
            async with sem:
                result = await asyncio.to_thread(
                    analyze_path_with_retries,
                    path,
                    manifest_tag=manifest_tag,
                )
        except Exception:
            result = None
        if on_each is not None:
            try:
                on_each(idx, total, path, result)
            except Exception:
                pass
        return idx, result

    pairs = await asyncio.gather(*[_one(i, p) for i, p in enumerate(photo_paths)])
    pairs.sort(key=lambda x: x[0])
    return [p[1] for p in pairs]


def analyze_photo(photo_path: Path, **kwargs) -> dict[str, Any] | None:
    return analyze_path_with_retries(photo_path, **kwargs)

# --- Tape Verification logic follows standard audit rules ---
def verify_tape_sku(photo_path: Path, claimed_marking: str) -> bool | None:
    timeout = float(settings.tape_verify_total_timeout_seconds)
    fut = _get_tape_verify_pool().submit(_verify_tape_sku_inner, photo_path, claimed_marking)
    try: return fut.result(timeout=timeout)
    except Exception: return None

def _normalize_tape_audit_reading(raw: object) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", ""):
        return ""
    return (tape_marking_canonical(s) or sku_digits(s) or "").strip().upper()


def _verify_tape_sku_inner(photo_path: Path, claimed_marking: str) -> bool | None:
    claim = (claimed_marking or "").strip()
    if not claim:
        return None
    prompt = (
        f"Audit: Is the lot marking {claim!r} clearly visible on estate lot tape? "
        "Tape may be PURPLE, LAVENDER, bright NEON GREEN, or bright YELLOW adhesive masking/painter tape "
        "with handwriting or print on its surface. "
        "Set new_lot_from_tape false if the marking is only on a yellow MEASURING tape (inch/cm scale), ruler, "
        "sticker, tag, or item body. "
        "Return JSON {\"new_lot_from_tape\": bool, \"lot_digits_on_tape\": string}. "
        "lot_digits_on_tape: exact marking read from the tape (letters+digits as seen), or null if none/unreadable."
    )
    prompt = prompt + _tape_verify_prompt_extra()
    img_bytes, mime = prepare_gemini_image_file(photo_path, max_edge=1536, quality=85)
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(img_bytes).decode("utf-8")}}]}]
    }
    text = _generate_content(payload)
    if not text: return None
    try:
        res = json.loads(re.sub(r"```json|```", "", text).strip())
        if res.get("new_lot_from_tape") is not True:
            return False
        read_norm = _normalize_tape_audit_reading(res.get("lot_digits_on_tape"))
        claim_norm = _normalize_tape_audit_reading(claim)
        return bool(claim_norm) and read_norm == claim_norm
    except Exception: return None