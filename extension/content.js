/**
 * Heuristic autofill for CT Seller (ctbids.com) from Estate API lot shape.
 * Tries name/id/placeholder/aria-label/data-testid against keyword sets, then
 * sets value in a way React-controlled inputs usually notice.
 */

function blob(el) {
  return [
    el.name,
    el.id,
    el.className,
    el.placeholder,
    el.getAttribute("aria-label"),
    el.getAttribute("data-testid"),
    el.getAttribute("for")
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function scoreField(el, keywords) {
  const b = blob(el);
  if (!b) return 0;
  let s = 0;
  for (const kw of keywords) {
    if (b.includes(kw)) s += kw.length + 2;
  }
  return s;
}

function setReactFriendlyValue(el, value) {
  if (value == null || value === "") return false;
  const str = String(value);
  const tag = el.tagName.toLowerCase();
  if (tag === "select") {
    el.value = str;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  if (tag !== "input" && tag !== "textarea") return false;

  const proto = Object.getPrototypeOf(el);
  const desc = Object.getOwnPropertyDescriptor(proto, "value");
  if (desc?.set) {
    desc.set.call(el, str);
  } else {
    el.value = str;
  }
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  return true;
}

/** Step tabs must not treat ``51`` / ``103`` as step ``1`` / ``3`` (substring ``includes`` on digits). */
function isolatedStepDigit(text, digit) {
  const d = String(digit);
  return new RegExp(`(?:^|[^0-9])${d}(?:[^0-9]|$)`).test(text);
}

function detectCtSellerStep() {
  const stepNodes = Array.from(document.querySelectorAll("button, a, div, span"));
  let best = { step: "unknown", score: 0 };
  for (const node of stepNodes) {
    const t = normalizeText(node.textContent || "");
    if (!t) continue;
    const cls = normalizeText(node.className || "");
    const activeHint = cls.includes("active") || cls.includes("current") || cls.includes("selected");
    const bonus = activeHint ? 3 : 0;
    const d1 = isolatedStepDigit(t, "1") || /\bstep\s*1\b/i.test(t);
    const d2 = isolatedStepDigit(t, "2") || /\bstep\s*2\b/i.test(t);
    const d3 = isolatedStepDigit(t, "3") || /\bstep\s*3\b/i.test(t);
    if (d1 && t.includes("detail") && bonus + 1 > best.score) best = { step: "detail", score: bonus + 1 };
    if (d2 && t.includes("setting") && bonus + 1 > best.score) best = { step: "settings", score: bonus + 1 };
    if (d3 && t.includes("media") && bonus + 1 > best.score) best = { step: "media", score: bonus + 1 };
  }
  if (best.step !== "unknown") return best.step;
  const pageText = normalizeText(document.body?.innerText || "");
  if (pageText.includes("media library") || pageText.includes("upload image")) return "media";
  if (pageText.includes("lot #(sku)") || pageText.includes("select category")) return "detail";
  if (pageText.includes("save") && pageText.includes("setting")) return "settings";
  return "unknown";
}

/** Best-effort click on CT Seller step tabs (same heuristics as ``detectCtSellerStep``). */
function clickCtSellerTabStep(targetWant) {
  const want = String(targetWant || "").toLowerCase();
  if (!["detail", "settings", "media"].includes(want)) {
    return { ok: false, error: `Invalid step: ${want}` };
  }
  const stepNodes = Array.from(document.querySelectorAll("button, a, div, span, li"));
  let best = { el: null, score: 0 };
  for (const node of stepNodes) {
    const t = normalizeText(node.textContent || "");
    if (!t || t.length > 140) continue;
    const cls = normalizeText(node.className || "");
    const activeHint = cls.includes("active") || cls.includes("current") || cls.includes("selected");
    const bonus = activeHint ? 3 : 0;
    const d1 = isolatedStepDigit(t, "1") || /\bstep\s*1\b/i.test(t);
    const d2 = isolatedStepDigit(t, "2") || /\bstep\s*2\b/i.test(t);
    const d3 = isolatedStepDigit(t, "3") || /\bstep\s*3\b/i.test(t);
    let hit = 0;
    if (want === "detail" && d1 && t.includes("detail")) hit = bonus + 1;
    if (want === "settings" && d2 && t.includes("setting")) hit = bonus + 1;
    if (want === "media" && d3 && t.includes("media")) hit = bonus + 1;
    if (hit > 0 && hit >= best.score) {
      const clickable = node.closest("button, a, [role='tab'], [role='button'], li") || node;
      if (clickable && typeof clickable.click === "function") {
        best = { el: clickable, score: hit };
      }
    }
  }
  if (!best.el) return { ok: false, error: `Could not find ${want} step tab` };
  best.el.click();
  return { ok: true };
}

const saveGate = {
  armed: false,
  lotId: "",
  armedAt: 0,
  savedAt: 0
};

function isSaveLikeClickTarget(target) {
  const btn = target?.closest?.("button, [role='button'], input[type='button'], input[type='submit'], a");
  if (!btn) return false;
  const text = normalizeText(
    btn.textContent ||
      btn.getAttribute("aria-label") ||
      btn.getAttribute("title") ||
      btn.getAttribute("value") ||
      ""
  );
  return text === "save" || text.startsWith("save ") || text.includes(" save ") || text.endsWith(" save");
}

document.addEventListener(
  "click",
  (event) => {
    if (!saveGate.armed) return;
    if (detectCtSellerStep() !== "media") return;
    if (!isSaveLikeClickTarget(event.target)) return;
    saveGate.savedAt = Date.now();
  },
  true
);

function selectOptionMatches(optionText, candidate) {
  const o = normalizeText(optionText);
  const c = normalizeText(candidate);
  if (!c || !o) return false;
  if (o === c) return true;
  if (o.startsWith(`${c} `) || o.startsWith(`${c}*`)) return true;
  // Short labels (e.g. "Art") must match exactly — never "art" inside "miscellaneous".
  if (c.length <= 4) return false;
  if (c.length >= 5 && (o.startsWith(c) || c.startsWith(o))) return true;
  return o.includes(c) || c.includes(o);
}

function setSelectByTextOrValue(el, candidates) {
  if (!el || el.tagName.toLowerCase() !== "select") return false;
  const list = (Array.isArray(candidates) ? candidates : [candidates])
    .map((x) => String(x || "").trim())
    .filter(Boolean);
  if (!list.length) return false;
  const options = Array.from(el.options || []);
  const norm = (s) => normalizeText(String(s || ""));
  for (const cand of list) {
    const c = norm(cand);
    let match = options.find((o) => norm(o.text) === c || norm(o.value) === c);
    if (!match) {
      match = options.find((o) => selectOptionMatches(o.text, cand) || selectOptionMatches(o.value, cand));
    }
    if (match) {
      el.value = match.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new Event("blur", { bubbles: true }));
      return true;
    }
  }
  return false;
}

function getExactSelect(fieldName) {
  const esc = (window.CSS && CSS.escape) ? CSS.escape(fieldName) : fieldName;
  return (
    document.querySelector(`select[name="${esc}"]`) ||
    document.querySelector(`select#${esc}`) ||
    null
  );
}

function pickBestField(keywords, minScore = 4) {
  const nodes = document.querySelectorAll("input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='submit']):not([type='button']), textarea, select");
  let best = null;
  let bestScore = minScore;
  for (const el of nodes) {
    if (el.disabled || el.readOnly) continue;
    const sc = scoreField(el, keywords);
    if (sc > bestScore) {
      bestScore = sc;
      best = el;
    }
  }
  return best;
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Strip markdown-style bullets from API text before we add our own ``- `` prefix for display. */
function specLinePlainForQuill(item) {
  return String(item || "")
    .trim()
    .replace(/^\s*[-•*·◇►]+\s*/, "");
}

/** Lowercase blob of brand-like RHS text from spec lines (Key Details). */
function specLinesBrandValuesBlobLower(specItems) {
  const bits = [];
  for (const raw of specItems) {
    const line = specLinePlainForQuill(raw);
    let m = /^brands?\s*:\s*(.+)$/i.exec(line);
    if (!m) m = /^maker\s*:\s*(.+)$/i.exec(line);
    if (!m) m = /^manufacturers?\s*:\s*(.+)$/i.exec(line);
    if (!m) m = /^origin\s*:\s*(.+)$/i.exec(line);
    if (m) bits.push(m[1].trim());
  }
  if (!bits.length) return "";
  return bits.join("; ").toLowerCase();
}

/** Omit ``lot.brands`` entries already covered by a ``Brand:`` / ``Brands:`` spec line (avoids duplicate footer). */
function filterBrandsForFooter(brands, specItems) {
  const blob = specLinesBrandValuesBlobLower(specItems);
  if (!blob) return brands;
  return brands.filter((b) => {
    const t = String(b || "").trim().toLowerCase();
    if (!t) return false;
    return !blob.includes(t);
  });
}

/** Quill often drops ``<br>`` inside one ``<p>``; tight ``<p>`` per row keeps line breaks without list margins. */
const CT_DESC_P = ' style="margin:0;padding:0;line-height:1.35"';
/** One blank line under prose when Key Details follows (``margin-bottom`` ≈ one line at this line-height). */
const CT_DESC_BODY_BEFORE_DETAILS = ' style="margin:0 0 1.35em 0;padding:0;line-height:1.35"';

/**
 * Plain text for textareas + Quill HTML: one blank line under prose when Key Details follows; ``Key Details:`` and
 * each ``-`` spec in its own tight ``<p>`` (avoids Quill collapsing ``<br>`` into a single run-on block).
 * @returns {{ plain: string, html: string }}
 */
function buildDescriptionPayload(lot) {
  const body = String(lot?.professional_description || "").replace(/\s+/g, " ").trim();
  const rawSpecs = Array.isArray(lot?.specification_lines) ? lot.specification_lines : [];
  const specItems = [];
  const seen = new Set();
  for (const line of rawSpecs) {
    const chunks = String(line || "")
      .split(/[|•]/g)
      .map((x) => x.trim())
      .filter(Boolean);
    for (const chunk of chunks) {
      const cleaned = chunk.replace(/\s+/g, " ").trim();
      const key = cleaned.toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      specItems.push(cleaned);
      if (specItems.length >= 18) break;
    }
    if (specItems.length >= 18) break;
  }

  const brandsRaw = Array.isArray(lot?.brands) ? lot.brands.map((x) => String(x || "").trim()).filter(Boolean) : [];
  const brands = filterBrandsForFooter(brandsRaw, specItems);

  const plainSections = [];
  if (body) plainSections.push(body);
  if (specItems.length) {
    if (plainSections.length) plainSections.push("");
    plainSections.push(["Key Details:", ...specItems.map((item) => `- ${item}`)].join("\n"));
  }
  if (brands.length) {
    plainSections.push(`Brands: ${brands.join(", ")}`);
  }
  const plain = plainSections.join("\n").trim();

  const htmlParts = [];
  if (body) {
    const bodyStyle = specItems.length ? CT_DESC_BODY_BEFORE_DETAILS : CT_DESC_P;
    htmlParts.push(`<p${bodyStyle}>${escapeHtml(body)}</p>`);
  }
  if (specItems.length) {
    htmlParts.push(`<p${CT_DESC_P}>Key Details:</p>`);
    for (const raw of specItems) {
      const line = specLinePlainForQuill(raw);
      if (!line) continue;
      htmlParts.push(`<p${CT_DESC_P}>- ${escapeHtml(line)}</p>`);
    }
  }
  if (brands.length) {
    htmlParts.push(`<p${CT_DESC_P}>${escapeHtml(`Brands: ${brands.join(", ")}`)}</p>`);
  }
  const html = htmlParts.join("");

  return { plain, html };
}


const CATEGORY_RULES = [
  { category: "Handbags & Wallets", keywords: ["handbag", "purse", "wallet", "crossbody", "tote", "satchel", "clutch"] },
  { category: "Shoes", keywords: ["shoe", "heel", "loafer", "sneaker", "boot", "sandals", "rockport", "cole haan"] },
  { category: "Women's", keywords: ["women", "dress", "blouse", "sweater", "cardigan", "skirt", "knit"] },
  { category: "Men's", keywords: ["men", "mens", "necktie", "tie", "fedora", "hat", "akubra"] },
  { category: "Earrings", keywords: ["earring", "stud earring", "hoop earring"] },
  { category: "Bracelets", keywords: ["bracelet", "bangle", "cuff bracelet", "figaro"] },
  {
    category: "Art",
    keywords: [
      "colored pencil",
      "colour pencil",
      "prismacolor",
      "sketchpad",
      "sketch pad",
      "sketchbook",
      "drawing pencil",
      "watercolor pencil",
      "art supplies",
      "art supply",
      "moleskine",
      "charcoal pencil",
      "pastel pencil"
    ]
  },
  { category: "Rings", keywords: ["ring", "band ring", "engagement ring"] },
  { category: "Jewelry Other", keywords: ["jewelry", "brooch", "necklace", "pendant", "tie clip", "cufflink"] },
  { category: "Costume Jewelry", keywords: ["rhinestone", "costume jewelry", "clip-on"] },
  { category: "Luxury and Designer Items", keywords: ["louis vuitton", "gucci", "prada", "michael kors", "coach", "designer"] },
  {
    category: "Books",
    keywords: [
      "hardcover book",
      "paperback book",
      "hardcover",
      "paperback",
      "softcover",
      "first edition",
      "coffee table book",
      "children's book",
      "childrens book",
      "picture book",
      "novel",
      "textbook",
      "cookbook",
      "biography",
      "autobiography",
      "yearbook",
      "magazine",
      "comic book",
      "comics",
      "book set",
      "book lot",
      "book"
    ]
  },
  { category: "Dolls", keywords: ["doll", "barbie", "matryoshka"] },
  { category: "Figures & Figurines", keywords: ["figurine", "figure", "disney", "collectible figure"] },
  { category: "Paintings", keywords: ["painting", "canvas", "acrylic", "oil on", "watercolor", "batik", "signed art"] },
  { category: "Photography", keywords: ["camera", "photography", "lens", "nikon", "canon"] },
  { category: "Clocks", keywords: ["clock", "timepiece", "mantel clock", "wall clock"] },
  { category: "Coins", keywords: ["coin", "coins", "peso", "mint", "bullion", "numismatic"] },
  {
    category: "Crystal",
    keywords: [
      "cut crystal",
      "lead crystal",
      "crystal glass",
      "crystal stemware",
      "crystal vase",
      "waterford",
      "baccarat"
    ]
  },
  {
    category: "Glassware",
    keywords: [
      "glassware",
      "art glass",
      "glass vase",
      "stained glass",
      "pressed glass",
      "depression glass",
      "stemware",
      "wine glass",
      "champagne flute",
      "highball",
      "tumbler",
      "goblet",
      "decanter",
      "drinking glass",
      "barware",
      "crystal",
      "glass plate",
      "glass bowl",
      "glass dish"
    ]
  },
  {
    category: "Dining Room",
    keywords: [
      "dining",
      "china set",
      "dinnerware",
      "place setting",
      "tableware",
      "serving platter",
      "charger plate",
      "salad plate",
      "soup bowl",
      "teacup",
      "saucer",
      "flatware",
      "silverware",
      "portmeirion",
      "spode",
      "wedgwood",
      "lenox",
      "noritake",
      "mikasa",
      "fitz and floyd",
      "botanic garden",
      "pomona"
    ]
  },
  {
    category: "Kitchen & Home",
    keywords: [
      "kitchen",
      "cookware",
      "bakeware",
      "baking dish",
      "casserole",
      "ramekin",
      "pie dish",
      "roasting pan",
      "dutch oven",
      "dish",
      "plate",
      "platter",
      "bowl",
      "mug",
      "teapot",
      "coffee mug",
      "ceramic",
      "porcelain",
      "stoneware",
      "earthenware",
      "china",
      "pottery",
      "oven-safe",
      "oven safe",
      "vase",
      "portmeirion",
      "pyrex",
      "corningware",
      "fiestaware"
    ]
  },
  { category: "Bedroom Goods", keywords: ["bedding", "linen", "bedspread", "comforter", "sheet", "pillowcase"] },
  { category: "Music Other", keywords: ["speaker", "stereo", "record player", "turntable", "audio", "music"] },
  { category: "Art Other", keywords: ["framed art", "wall art", "print", "decor art", "fine art"] },
  {
    category: "Decorative Collectibles",
    keywords: ["decorative collectible", "display piece", "novelty figurine", "knickknack", "knick knack"]
  },
  { category: "Decor", keywords: ["home decor", "wall decor", "shelf decor", "centerpiece"] },
  { category: "Antiques Other", keywords: ["antique", "heirloom", "collectible antique"] },
  { category: "Accessories", keywords: ["belt", "scarf", "hat", "accessory"] },
  { category: "Household Goods Other", keywords: ["household", "home goods", "furniture", "lamp", "nightstand", "dresser"] }
];

const SPECIFIC_OVER_HOUSEHOLD = new Set([
  "Books",
  "Glassware",
  "Crystal",
  "Dining Room",
  "Kitchen & Home",
  "Decor",
  "Decorative Collectibles",
  "Art",
  "Art Other",
  "Paintings",
  "Figures & Figurines"
]);

const WEAK_CATCHALL_CATEGORIES = new Set([
  "Decorative Collectibles",
  "Household Goods Other",
  "Antiques Other",
  "Decor"
]);

function hayKeywordMatch(hay, kw) {
  const k = String(kw || "").trim().toLowerCase();
  if (!k) return false;
  if (/\s/.test(k)) return hay.includes(k);
  const escaped = k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b${escaped}\\b`, "i").test(hay);
}

function inferCategory(lot, description) {
  const specs = Array.isArray(lot?.specification_lines) ? lot.specification_lines.join(" ") : "";
  const tags = Array.isArray(lot?.labels_and_tags) ? lot.labels_and_tags.join(" ") : "";
  const hay = `${lot.auction_title || ""}\n${description || ""}\n${(lot.brands || []).join(" ")}\n${specs}\n${tags}`.toLowerCase();
  const scored = [];
  for (const rule of CATEGORY_RULES) {
    let score = 0;
    for (const kw of rule.keywords) {
      if (hayKeywordMatch(hay, kw)) score += kw.length;
    }
    if (score > 0) scored.push({ category: rule.category, score });
  }
  scored.sort((a, b) => b.score - a.score);
  if (!scored.length || scored[0].score < 6) {
    if (
      /\b(chest|dresser|cabinet|nightstand|bookshelf|bookcase|hutch|console|table|desk|armoire|wardrobe|bombay|trunk)\b/i.test(
        hay
      )
    ) {
      return "Household Goods Other";
    }
    return "";
  }
  let best = scored[0];
  if (best.category === "Household Goods Other") {
    const preferred = scored.find((s) => SPECIFIC_OVER_HOUSEHOLD.has(s.category) && s.score >= 5);
    if (preferred) best = preferred;
  }
  if (WEAK_CATCHALL_CATEGORIES.has(best.category)) {
    const books = scored.find((s) => s.category === "Books" && s.score >= 4);
    if (books) best = books;
  }
  return best.category;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeText(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function labelMatches(text, label) {
  const t = normalizeText(text);
  const l = normalizeText(label);
  return t === l || t.startsWith(`${l} `) || t.includes(`${l}*`) || t.includes(`${l} *`);
}

function findFieldRootByLabel(labelText) {
  const want = normalizeText(labelText);
  const labelNodes = document.querySelectorAll("label, mat-label, .mdc-floating-label, span, div");
  for (const node of labelNodes) {
    const txt = normalizeText(node.textContent || "");
    if (!txt) continue;
    if (txt === want || txt.startsWith(`${want} `) || txt.includes(`${want}*`) || txt.includes(`${want} *`)) {
      const root = node.closest("mat-form-field, .mat-mdc-form-field, .mat-form-field, .form-group, .field, div");
      if (root) return root;
    }
  }
  return null;
}

function findFormFieldContainerByLabel(labelText) {
  const labelNodes = document.querySelectorAll("label, mat-label, .mdc-floating-label, .mat-mdc-floating-label, span, div");
  for (const node of labelNodes) {
    const txt = node.textContent || "";
    if (!labelMatches(txt, labelText)) continue;
    const container = node.closest("mat-form-field, .mat-mdc-form-field, .mat-form-field, .field, .form-group, div");
    if (container) return container;
  }
  return null;
}

function findComboboxForLabel(labelText) {
  const want = normalizeText(labelText);
  const candidates = Array.from(
    document.querySelectorAll("[role='combobox'], mat-select, .mat-mdc-select, .mat-select, .mat-mdc-select-trigger")
  );
  let best = null;
  let bestScore = 0;
  for (const el of candidates) {
    const root = el.closest("mat-form-field, .mat-mdc-form-field, .mat-form-field, .form-group, .field, div");
    const context = normalizeText(`${root?.textContent || ""} ${el.getAttribute("aria-label") || ""}`);
    if (!context) continue;
    let score = 0;
    if (context.includes(want)) score += 5;
    if (context.includes(`${want}*`) || context.includes(`${want} *`)) score += 3;
    if (score > bestScore) {
      best = el;
      bestScore = score;
    }
  }
  return bestScore > 0 ? best : null;
}

function findDropdownTriggerByLabel(labelText) {
  const container = findFormFieldContainerByLabel(labelText);
  if (container) {
    const direct = container.querySelector(
      "[role='combobox'], .mat-mdc-select-trigger, .mat-select-trigger, .mat-mdc-select-value, .mdc-select__anchor"
    );
    if (direct) return direct;
  }
  const combo = findComboboxForLabel(labelText);
  if (combo) {
    return (
      combo.querySelector(".mat-mdc-select-trigger, .mat-select-trigger, .mat-mdc-select-value, .mdc-select__anchor") ||
      combo
    );
  }
  const root = findFieldRootByLabel(labelText);
  if (!root) return null;
  return (
    root.querySelector(
      "[role='combobox'], .mat-mdc-select-trigger, .mat-select-trigger, .mat-mdc-select-value, .mdc-select__anchor"
    ) || root.querySelector("input, textarea, select")
  );
}

async function setDropdownByLabel(labelText, candidates, warnings) {
  const trigger = findDropdownTriggerByLabel(labelText);
  if (!trigger) {
    warnings.push(`no ${labelText.toLowerCase()} dropdown`);
    return false;
  }
  const list = (Array.isArray(candidates) ? candidates : [candidates])
    .map((x) => String(x || "").trim())
    .filter(Boolean);
  if (!list.length) return false;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    trigger.click();
    trigger.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    await sleep(220);

    const options = Array.from(
      document.querySelectorAll(
        ".cdk-overlay-container [role='option'], .cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, [role='option'], mat-option, .mat-mdc-option"
      )
    );
    if (!options.length) continue;

    const normalizedOptions = options.map((el) => ({
      el,
      text: normalizeText(el.textContent || el.getAttribute("aria-label") || "")
    }));

    for (const cand of list) {
      const c = normalizeText(cand);
      let hit = normalizedOptions.find((o) => o.text === c);
      if (!hit) hit = normalizedOptions.find((o) => selectOptionMatches(o.text, cand));
      if (hit) {
        hit.el.click();
        hit.el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
        await sleep(100);
        return true;
      }
    }
  }

  const inputLike = trigger.tagName.toLowerCase() === "input" ? trigger : trigger.querySelector("input");
  if (inputLike && setReactFriendlyValue(inputLike, list[0])) return true;
  warnings.push(`${labelText.toLowerCase()} option not found`);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  return false;
}

function mapConditionOptions(conditionText) {
  const c = normalizeText(conditionText);
  if (!c) return [];
  if (c.includes("excellent") || c.includes("like new") || c.includes("mint")) {
    return ["Excellent", "Like New", "Very Good", "Good"];
  }
  if (c.includes("good")) return ["Good", "Very Good", "Excellent", "Like New"];
  if (c.includes("fair")) return ["Fair", "Good", "Acceptable"];
  if (c.includes("poor") || c.includes("damage") || c.includes("repair")) return ["Poor", "Fair", "For Parts"];
  return ["Good", "Very Good", "Excellent", "Like New", "Fair", "Acceptable"];
}

const CTSELLER_ALLOWED_CATEGORIES = [
  "Accessories",
  "Advertising",
  "All",
  "Animals",
  "Animation Art & Characters",
  "Antique",
  "Antique & Vintage",
  "Antiques",
  "Antiques Other",
  "Appliances",
  "Art",
  "Art Other",
  "Artful Pottery",
  "Asian Art",
  "Audio",
  "Baby",
  "Baby Asseccories",
  "Baby accessories",
  "Bar ware & Breweriana",
  "Bath",
  "Bedroom",
  "Bedroom Furniture",
  "Bedroom Goods",
  "Boats",
  "Book Shelf",
  "Books",
  "Books & Periodicals",
  "Bottles & Insulators",
  "Bracelets",
  "Building Materials",
  "Cabinets",
  "Camera Other",
  "Cameras & Photo Equipment",
  "Camping",
  "Car Seat",
  "Carpets & Rugs",
  "Cars",
  "Chairs",
  "Children Footwear",
  "Children Toys",
  "Children cloths",
  "Childrens",
  "China Sets",
  "Clocks",
  "Clothing & Accessories",
  "Clothing Other",
  "Coins",
  "Collectibles",
  "Collectibles Other",
  "Comics",
  "Computers, Laptops",
  "Costume Jewelry",
  "Craft & Hobby",
  "Crafts & Hobbies",
  "Crystal",
  "Cycling",
  "DVDs",
  "Decor",
  "Decorative Art",
  "Decorative Collectibles",
  "Desks",
  "Digital",
  "Dining Room",
  "Dolls",
  "Earrings",
  "Electronics",
  "Electronics Other",
  "Farm Equipment",
  "Farm Equipment Other",
  "Figures & Figurines",
  "Film",
  "Fine Jewelry",
  "Fishing",
  "Fitness",
  "Floor Coverings",
  "Folk Art",
  "Footwear",
  "Furniture",
  "Furniture Other",
  "Games",
  "Games & Toys",
  "Glassware",
  "Golf",
  "Grilling & BBQ",
  "Hand Tools",
  "Handbags & Wallets",
  "Hardback",
  "Heavy Equipment",
  "Historical",
  "Holiday & Seasonal",
  "Home Audio",
  "Home Video",
  "Household Goods",
  "Household Goods Other",
  "Household Items",
  "Hunting",
  "Instruments",
  "Jewelry",
  "Jewelry Other",
  "Keyboards & Pianos",
  "Kitchen",
  "Kitchen & Home",
  "Kitchen Appliances",
  "Kitchen Electricals",
  "Kitchen Utensils",
  "Knives, Swords, Blades",
  "Ladders",
  "Lawn & Landscape",
  "Lighting",
  "Linens",
  "Luggage",
  "Luxury and Designer Items",
  "Magazines & Newspapers",
  "Matresses",
  "Medical Equipment",
  "Men's",
  "Men's Jewelry",
  "Mexican Art",
  "Militaria",
  "Mirrors",
  "Mobile Devices",
  "Model Cars",
  "Motor Vehicles",
  "Motor Vehicles Other",
  "Motorcycles",
  "Movie Memorabilia",
  "Music",
  "Music Other",
  "Mystery Lots",
  "Native American Art",
  "Necklaces",
  "Office",
  "Oil Paintings",
  "Paint & Painting Supplies",
  "Paintings",
  "Paperback",
  "Parts & Attachments",
  "Parts and Accessories",
  "Patio & Deck",
  "Patio Furniture",
  "Pet Supplies",
  "Photo Frames",
  "Photography",
  "Power tools",
  "Prints",
  "Railroad/Transportation",
  "Records & CDs",
  "Rings",
  "Rocks, Fossils, Minerals",
  "Sailing",
  "Sculpture",
  "Seasonal & Holiday",
  "Shelving",
  "Shoes",
  "Silver",
  "Sofas",
  "Sporting Goods Other",
  "Sports & Hobbies",
  "Sports Memorabilia",
  "Stamps",
  "Sterling Silver",
  "Storage",
  "Storage & Organization",
  "Sunglasses & Glasses",
  "TVs",
  "Tables",
  "Test Category",
  "Tool Storage",
  "Tools & Home Improvement",
  "Tools - Other",
  "Toys",
  "Toys Other",
  "Tractors",
  "Trading Cards",
  "Trailers",
  "Trains",
  "Trucks",
  "Video",
  "Video Games",
  "Vintage",
  "Vintage & Antique Toys",
  "Vintage Books",
  "Vintage Records",
  "Vintage, Retro, Mid-Century",
  "Vinyl Records",
  "Watches",
  "Watercolor",
  "Women's",
];

function mapCategoryCandidates(category) {
  const c = normalizeText(category);
  if (!c) return [];

  const aliasToAllowed = {
    "kitchen and home": "Kitchen & Home",
    "kitchen & home": "Kitchen & Home",
    "figures and figurines": "Figures & Figurines",
    "figures & figurines": "Figures & Figurines",
    "womens": "Women's",
    "women's": "Women's",
    "mens": "Men's",
    "men's": "Men's",
    "decorative collectible": "Decorative Collectibles",
    "decorative collectibles": "Decorative Collectibles",
    art: "Art",
    "art other": "Art Other",
    "art supplies": "Art",
    paintings: "Paintings",
    glassware: "Glassware",
    crystal: "Crystal",
    "dining room": "Dining Room",
    dining: "Dining Room",
    decor: "Decor",
    housewares: "Kitchen & Home",
    bakeware: "Kitchen & Home",
    dinnerware: "Dining Room",
    china: "Dining Room",
    books: "Books",
    book: "Books",
    "books and magazines": "Books",
    magazine: "Books",
    furniture: "Household Goods Other",
    home: "Household Goods Other",
    "jewelry other": "Jewelry Other",
    "luxury & designer items": "Luxury and Designer Items",
    "luxury and designer items": "Luxury and Designer Items"
  };

  const out = [];
  const push = (v) => {
    const t = String(v || "").trim();
    if (!t) return;
    if (!out.some((x) => normalizeText(x) === normalizeText(t))) out.push(t);
  };

  const mapped = aliasToAllowed[c];
  if (mapped) push(mapped);
  push(category);

  // Only keep categories known to exist on CT Seller (/category/list snapshot).
  const allowedNorm = new Set(CTSELLER_ALLOWED_CATEGORIES.map((x) => normalizeText(x)));
  const filtered = out.filter((x) => allowedNorm.has(normalizeText(x)));
  return filtered.length ? filtered : [];
}

function tryFillDescriptionRich(plain, html, warnings) {
  if (!plain) return false;
  const descriptionContainer =
    findFormFieldContainerByLabel("Description") ||
    document.querySelector("textarea[name='description'], textarea#description")?.closest("div");

  const editButtons = Array.from(
    (descriptionContainer || document).querySelectorAll("button, [role='button'], .mat-icon-button, i, .fa, .fas")
  ).filter((b) => {
    const t = normalizeText(
      b.textContent || b.getAttribute("aria-label") || b.getAttribute("title") || b.className || ""
    );
    return t.includes("edit") || t.includes("pencil") || t.includes("create");
  });
  if (editButtons.length) {
    editButtons[0].click();
  }
  const rich = document.querySelector("[contenteditable='true']");
  if (rich) {
    rich.focus();
    const isQuill =
      rich.classList.contains("ql-editor") ||
      !!rich.closest(".ql-container, .ql-snow, .ql-toolbar");
    if (isQuill && html) {
      rich.innerHTML = html;
    } else {
      rich.textContent = plain;
    }
    rich.dispatchEvent(new Event("input", { bubbles: true }));
    rich.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  }

  const descInput =
    document.querySelector("textarea[name='description'], textarea#description, input[name='description'], input#description") ||
    pickBestField(["description", "description *", "item description", "details", "notes"], 2);
  if (descInput) {
    if (descInput.readOnly) {
      try {
        descInput.readOnly = false;
      } catch {
        /* ignore */
      }
    }
    if (setReactFriendlyValue(descInput, plain)) {
      return true;
    }
  }

  // Last resort: if the description container has a visible text node area, try content injection.
  if (descriptionContainer) {
    const pseudoEditable = descriptionContainer.querySelector(".ql-editor, .ProseMirror, .tox-edit-area iframe");
    if (pseudoEditable) {
      pseudoEditable.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
  }

  const descTextareas = Array.from(document.querySelectorAll("textarea"));
  const bestTextarea = descTextareas.find((t) => {
    const b = blob(t);
    return b.includes("description") || b.includes("detail");
  });
  if (bestTextarea && setReactFriendlyValue(bestTextarea, plain)) {
    return true;
  }
  warnings.push("no rich description editor");
  return false;
}

function openDescriptionEditorFromForm() {
  const clickOpen = (el) => {
    if (!el) return false;
    const clickable = el.closest("button, [role='button'], a, div, span") || el;
    if (typeof clickable.click === "function") clickable.click();
    clickable.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  };

  const byLabel = Array.from(document.querySelectorAll("label, span, div")).find((n) =>
    normalizeText(n.textContent || "").startsWith("description")
  );
  const blocks = [];
  if (byLabel) {
    const blk = byLabel.closest(".float-control, .text-editor, .form-group, .field, div") || byLabel.parentElement;
    if (blk) blocks.push(blk);
  }
  const placeholderNode = Array.from(document.querySelectorAll("div, span, p")).find((n) =>
    normalizeText(n.textContent || "").includes("click edit icon to add text")
  );
  if (placeholderNode) {
    const blk = placeholderNode.closest(".float-control, .text-editor, .form-group, .field, div") || placeholderNode.parentElement;
    if (blk) blocks.push(blk);
  }

  for (const block of blocks) {
    const explicit = block.querySelector(
      "[aria-label*='edit' i], [title*='edit' i], .fa-pencil, .fa-pencil-alt, .fa-edit, .bi-pencil, .icon-edit, .edit"
    );
    if (clickOpen(explicit)) return true;
    const iconButtons = Array.from(block.querySelectorAll("button, [role='button'], i, svg, a, div")).filter((el) => {
      const t = normalizeText(
        el.textContent || el.getAttribute("aria-label") || el.getAttribute("title") || el.className || ""
      );
      return t.includes("pencil") || t.includes("edit");
    });
    if (iconButtons.length && clickOpen(iconButtons[0])) return true;
  }

  // Geometry fallback: click icon controls to the right of the Description field.
  const descAnchor = Array.from(document.querySelectorAll("label, span, div, p")).find((n) => {
    const t = normalizeText(n.textContent || "");
    return t.startsWith("description") || t.includes("click edit icon to add text");
  });
  if (descAnchor) {
    const root =
      descAnchor.closest(".float-control, .text-editor, .form-group, .field, div") || descAnchor.parentElement;
    if (root) {
      const anchorRect = root.getBoundingClientRect();
      const nearby = Array.from(root.querySelectorAll("button, [role='button'], a, i, svg, div")).filter((el) => {
        const r = el.getBoundingClientRect();
        if (r.width < 10 || r.height < 10) return false;
        const klass = normalizeText(el.className || "");
        const aria = normalizeText(el.getAttribute("aria-label") || el.getAttribute("title") || "");
        // likely small square action icons near right edge
        const rightSide = r.left >= anchorRect.right - 90;
        const iconish = klass.includes("icon") || klass.includes("btn") || klass.includes("fa") || aria.length > 0;
        return rightSide && iconish;
      });
      if (nearby.length) {
        // Usually eye first, pencil/edit second -> prefer last.
        const candidate = nearby[nearby.length - 1];
        if (clickOpen(candidate)) return true;
      }

      // Direct hit for CT Seller icon markup:
      // <img src="/static/icons/table/edit.svg" alt="edit" class="action-button" ...>
      const directEditImg = root.querySelector(
        "img.action-button[alt*='edit' i], img[alt*='edit' i][src*='edit.svg'], img[src*='/icons/table/edit.svg']"
      );
      if (directEditImg && clickOpen(directEditImg)) return true;

      // Coordinate fallback: click to the right of description placeholder text.
      const placeholder = Array.from(root.querySelectorAll("div, span, p")).find((n) =>
        normalizeText(n.textContent || "").includes("click edit icon to add text")
      );
      if (placeholder) {
        const r = placeholder.getBoundingClientRect();
        const points = [
          [Math.min(window.innerWidth - 8, r.right + 20), r.top + r.height * 0.35],
          [Math.min(window.innerWidth - 8, r.right + 20), r.top + r.height * 0.65],
          [Math.min(window.innerWidth - 8, r.right + 40), r.top + r.height * 0.5]
        ];
        for (const [x, y] of points) {
          const target = document.elementFromPoint(Math.max(2, x), Math.max(2, y));
          if (target && clickOpen(target)) return true;
        }
      }
    }
  }

  // Global fallback: try obvious pencil/edit icons anywhere on page.
  const globalEditImg = document.querySelector(
    "img.action-button[alt*='edit' i], img[alt*='edit' i][src*='edit.svg'], img[src*='/icons/table/edit.svg']"
  );
  if (globalEditImg && clickOpen(globalEditImg)) return true;

  const globalEdit = Array.from(document.querySelectorAll("button, [role='button'], i, svg, a")).find((el) => {
    const t = normalizeText(
      el.textContent || el.getAttribute("aria-label") || el.getAttribute("title") || el.className || ""
    );
    return t.includes("pencil") || t.includes("edit");
  });
  return clickOpen(globalEdit);
}

async function fillDescriptionViaQuillModal(plain, html, warnings) {
  if (!plain) return false;
  // Ensure the modal is opened from the form first.
  const opened = openDescriptionEditorFromForm();
  if (!opened) {
    warnings.push("description edit trigger not found");
  }
  await sleep(120);
  // Wait for modal + quill to appear after pencil click.
  const globalEditIcons = Array.from(
    document.querySelectorAll(
      "img.action-button[alt*='edit' i], img[alt*='edit' i][src*='edit.svg'], img[src*='/icons/table/edit.svg']"
    )
  ).filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 8 && r.height > 8;
  });

  for (let attempt = 0; attempt < 24; attempt += 1) {
    if (attempt > 0 && attempt % 4 === 0) {
      openDescriptionEditorFromForm();
    }
    if (attempt > 0 && globalEditIcons.length) {
      const i = Math.min(globalEditIcons.length - 1, Math.floor((attempt - 1) / 3));
      const icon = globalEditIcons[i];
      if (icon) {
        if (typeof icon.click === "function") icon.click();
        icon.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        icon.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
        icon.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      }
    }
    const editor = document.querySelector(
      ".modal .ql-editor, [role='dialog'] .ql-editor, .ql-container .ql-editor, .ql-editor[contenteditable='true']"
    );
    if (editor) {
      editor.focus();
      const inner =
        html && html.length
          ? html
          : plain
              .split("\n")
              .map((ln) => ln.trim())
              .filter((ln) => ln.length)
              .map((ln) => `<p${CT_DESC_P}>${escapeHtml(ln)}</p>`)
              .join("");
      editor.innerHTML = inner;
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      editor.dispatchEvent(new Event("blur", { bubbles: true }));

      const saveBtn = Array.from(
        document.querySelectorAll(".modal button, [role='dialog'] button, .modal [role='button']")
      ).find((b) => {
        const t = normalizeText(b.textContent || b.getAttribute("aria-label") || b.getAttribute("title") || "");
        return t === "save" || t.endsWith(" save") || t.includes(" save ");
      });
      if (saveBtn) {
        saveBtn.click();
      } else {
        warnings.push("quill save button not found");
      }
      return true;
    }
    // eslint-disable-next-line no-await-in-loop
    await sleep(160);
  }
  warnings.push("quill modal not detected");
  return false;
}

function inferAreaFromCategory(category) {
  const c = normalizeText(category);
  if (!c) return "Living Room";
  if (
    c.includes("kitchen") ||
    c.includes("dining") ||
    c.includes("glassware") ||
    c.includes("crystal") ||
    c.includes("china") ||
    c.includes("silver") ||
    c.includes("sterling")
  ) {
    return "Kitchen & Dining";
  }
  if (
    c.includes("women") ||
    c.includes("men") ||
    c.includes("shoes") ||
    c.includes("handbags") ||
    c.includes("clothing") ||
    c.includes("jewelry") ||
    c.includes("earrings") ||
    c.includes("bracelets") ||
    c.includes("rings") ||
    c.includes("luggage") ||
    c.includes("bedroom")
  ) {
    return "Bedroom";
  }
  if (c.includes("toy") || c.includes("doll") || c.includes("game") || c.includes("children") || c.includes("baby")) {
    return "Kids Playroom";
  }
  if (c.includes("tool") || c.includes("garage")) return "Garage";
  if (c.includes("patio") || c.includes("lawn") || c.includes("grill") || c.includes("outdoor")) return "Patio & Lawn";
  if (c.includes("music") || c.includes("record") || c.includes("dvd") || c.includes("video game") || c.includes("tv")) {
    return "Entertainment Cave";
  }
  if (
    c.includes("book") ||
    c.includes("magazine") ||
    c.includes("office") ||
    c.includes("electronics") ||
    c.includes("computer") ||
    c.includes("camera") ||
    c.includes("mobile")
  ) {
    return "Office";
  }
  return "Living Room";
}

function mapAreaCandidates(area, inferredCategory, title) {
  const out = [];
  const push = (v) => {
    const t = String(v || "").trim();
    if (!t) return;
    if (!out.some((x) => normalizeText(x) === normalizeText(t))) out.push(t);
  };

  push(area);
  push(inferAreaFromCategory(inferredCategory));

  const cat = normalizeText(inferredCategory);
  if (cat.includes("kitchen") || cat.includes("dining") || cat.includes("glass")) push("Kitchen & Dining");
  if (cat.includes("art") || cat.includes("paint") || cat.includes("decor") || cat.includes("household")) {
    push("Living Room");
  }
  if (cat.includes("women") || cat.includes("men") || cat.includes("shoe") || cat.includes("handbag")) {
    push("Bedroom");
  }
  if (/\b(chest|dresser|cabinet|nightstand|bookshelf|bookcase|hutch|console|table|desk|armoire|wardrobe)\b/i.test(title || "")) {
    push("Living Room");
    push("Bedroom");
  }
  return out;
}

async function fillFromLot(lot) {
  const filled = [];
  const warnings = [];

  const title = lot.auction_title || "";
  const { plain: description, html: descriptionHtml } = buildDescriptionPayload(lot);
  const sku = lot.sku || "";
  const price = lot.starting_price != null ? String(lot.starting_price) : "";
  const condition = lot.condition_text || "";
  const inferredCategoryRaw = String(lot.ct_category || "").trim();
  const keywordCategory = inferCategory(lot, description);
  // Prefer keyword hit when AI stored a weak catch-all (e.g. books → Decorative Collectibles).
  let inferredCategory = inferredCategoryRaw || keywordCategory;
  if (
    keywordCategory &&
    (!inferredCategoryRaw ||
      (WEAK_CATCHALL_CATEGORIES.has(inferredCategoryRaw) && !WEAK_CATCHALL_CATEGORIES.has(keywordCategory)) ||
      (keywordCategory === "Books" && inferredCategoryRaw !== "Books"))
  ) {
    inferredCategory = keywordCategory;
  }
  let categoryCandidates = mapCategoryCandidates(inferredCategory);
  if (!categoryCandidates.length && keywordCategory) {
    categoryCandidates = mapCategoryCandidates(keywordCategory);
    if (categoryCandidates.length) inferredCategory = keywordCategory;
  }
  const inferredSubcategory = lot.ct_subcategory || "";
  const inferredArea = inferAreaFromCategory(inferredCategory);

  const titleEl = pickBestField(["title", "item name", "itemname", "listing", "headline", "name", "short"]);
  if (titleEl && title) {
    if (setReactFriendlyValue(titleEl, title)) filled.push("title");
  } else if (title) warnings.push("no title field");

  const descEl = pickBestField(["description", "detail", "narrative", "story", "body", "long", "notes", "overview"]);
  if (descEl && description) {
    if (setReactFriendlyValue(descEl, description)) filled.push("description");
  }

  const skuEl = pickBestField(["sku", "lot #", "lot#", "lot number", "item number", "stock", "code"]);
  if (skuEl && sku) {
    if (setReactFriendlyValue(skuEl, sku)) filled.push("sku");
  }

  const priceEl = pickBestField(["starting", "start price", "price", "bid", "amount", "fmv", "value", "reserve"]);
  if (priceEl && price) {
    if (setReactFriendlyValue(priceEl, price)) filled.push("price");
  } else if (price) warnings.push("no price field");

  if (condition) {
    const condOptions = mapConditionOptions(condition);
    const conditionSelect = getExactSelect("condition");
    if (conditionSelect && setSelectByTextOrValue(conditionSelect, condOptions)) {
      filled.push("condition");
    } else if (await setDropdownByLabel("Condition", condOptions, warnings)) {
      filled.push("condition");
    }
  }

  if (inferredCategory) {
    const categorySelect = getExactSelect("category");
    if (categorySelect && setSelectByTextOrValue(categorySelect, categoryCandidates)) {
      filled.push("category");
    } else if (await setDropdownByLabel("Category", categoryCandidates, warnings)) {
      filled.push("category");
    } else {
      warnings.push(`category option not found: ${categoryCandidates.join(" | ")}`);
    }
  } else {
    warnings.push("no category inference");
  }

  if (inferredSubcategory) {
    if (await setDropdownByLabel("Subcategory", [inferredSubcategory], warnings)) {
      filled.push("subcategory");
    }
  }

  if (inferredArea) {
    const areaCandidates = mapAreaCandidates(inferredArea, inferredCategory, title);
    const areaSelect = getExactSelect("area");
    if (areaSelect && setSelectByTextOrValue(areaSelect, areaCandidates)) {
      filled.push("area");
    } else if (await setDropdownByLabel("Area", areaCandidates, warnings)) {
      filled.push("area");
    }
  }

  if (description && !filled.includes("description")) {
    if (
      tryFillDescriptionRich(description, descriptionHtml, warnings) ||
      (await fillDescriptionViaQuillModal(description, descriptionHtml, warnings))
    ) {
      filled.push("description");
    } else {
      warnings.push("no description field");
    }
  }

  return { filled, warnings };
}

async function performAutoPhotoUpload(lotId, files) {
  const fileInput =
    document.querySelector("input[type='file'][accept*='image']") ||
    document.querySelector("input[type='file']");
  if (!fileInput) {
    return { ok: false, error: "No file input found on this page. Open Media step first." };
  }
  const dt = new DataTransfer();
  for (const f of files) {
    let bytes = null;
    if (f?.arrayBuffer instanceof ArrayBuffer) {
      bytes = new Uint8Array(f.arrayBuffer);
    } else if (f?.dataBase64) {
      const bin = atob(f.dataBase64);
      bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    }
    if (!bytes || !bytes.length) continue;
    const file = new File([bytes], f.name || "photo.jpg", { type: f.type || "image/jpeg" });
    dt.items.add(file);
  }
  if (!dt.files.length) {
    return { ok: false, error: "No image files available to upload." };
  }
  fileInput.files = dt.files;
  fileInput.dispatchEvent(new Event("input", { bubbles: true }));
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  saveGate.armed = true;
  saveGate.lotId = String(lotId || "");
  saveGate.armedAt = Date.now();
  saveGate.savedAt = 0;
  return { ok: true, count: dt.files.length };
}

function getSaveGateSnapshot(lotId) {
  const lid = String(lotId || "");
  const sameLot = !lid || !saveGate.lotId || lid === saveGate.lotId;
  const saved = sameLot && saveGate.armed && saveGate.savedAt >= saveGate.armedAt && saveGate.savedAt > 0;
  return {
    ok: true,
    armed: saveGate.armed,
    lotId: saveGate.lotId,
    saved,
    savedAt: saveGate.savedAt
  };
}

/**
 * Injects ``window.__estateEzPlaywright`` (fill, upload, step nav, save gate) and legacy
 * ``window.__estateEzPlaywrightBridge`` (fill only) for Playwright-driven full listings.
 */
(function installPlaywrightAutomationBridge() {
  if (document.documentElement?.dataset?.estateEzPwBridge === "1") return;

  try {
    const src = document.createElement("script");
    src.textContent =
      "(function(){" +
      "if(window.__estateEzPwApi)return;" +
      "window.__estateEzPwApi=true;" +
      "function waitDone(n){return new Promise(function(res){" +
      "document.addEventListener(n,function(e){res(e.detail);},{once:true});});}" +
      "window.__estateEzPlaywright={" +
      "fillLot:function(lot){document.dispatchEvent(new CustomEvent('__estateEzAutofill',{detail:lot}));" +
      "return waitDone('__estateEzAutofillDone');}," +
      "uploadPhotos:function(lotId,files){document.dispatchEvent(new CustomEvent('__estateEzUploadPhotos',{detail:{lotId:lotId,files:files}}));" +
      "return waitDone('__estateEzUploadPhotosDone');}," +
      "getStep:function(){document.dispatchEvent(new CustomEvent('__estateEzPwGetStep'));" +
      "return waitDone('__estateEzPwGetStepDone');}," +
      "clickStep:function(step){document.dispatchEvent(new CustomEvent('__estateEzPwClickStep',{detail:{step:step}}));" +
      "return waitDone('__estateEzPwClickStepDone');}," +
      "getSaveGate:function(lotId){document.dispatchEvent(new CustomEvent('__estateEzPwGetSaveGate',{detail:{lotId:lotId}}));" +
      "return waitDone('__estateEzPwGetSaveGateDone');}" +
      "};" +
      "window.__estateEzPlaywrightBridge=function(lot){document.dispatchEvent(new CustomEvent('__estateEzAutofill',{detail:lot}));};" +
      "})();";
    (document.documentElement || document.head).appendChild(src);
    src.remove();
  } catch (e) {
    console.warn("[Estate-Ez] Playwright bridge inject failed", e);
  }

  document.addEventListener("__estateEzAutofill", (ev) => {
    const lot = ev.detail;
    if (!lot || typeof lot !== "object") return;
    void (async () => {
      let detail;
      try {
        const { filled, warnings } = await fillFromLot(lot);
        detail = { ok: true, filled, warnings, lotId: lot?.id };
      } catch (err) {
        detail = { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
      try {
        document.dispatchEvent(new CustomEvent("__estateEzAutofillDone", { detail }));
      } catch (err2) {
        console.error("[Estate-Ez] Playwright bridge done dispatch failed", err2);
      }
    })();
  });

  document.addEventListener("__estateEzUploadPhotos", (ev) => {
    const { lotId, files } = ev.detail || {};
    void (async () => {
      let detail;
      try {
        detail = await performAutoPhotoUpload(lotId, Array.isArray(files) ? files : []);
      } catch (err) {
        detail = { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
      document.dispatchEvent(new CustomEvent("__estateEzUploadPhotosDone", { detail }));
    })();
  });

  document.addEventListener("__estateEzPwGetStep", () => {
    document.dispatchEvent(
      new CustomEvent("__estateEzPwGetStepDone", { detail: { ok: true, step: detectCtSellerStep() } })
    );
  });

  document.addEventListener("__estateEzPwClickStep", (ev) => {
    const step = ev.detail?.step;
    document.dispatchEvent(
      new CustomEvent("__estateEzPwClickStepDone", { detail: clickCtSellerTabStep(step) })
    );
  });

  document.addEventListener("__estateEzPwGetSaveGate", (ev) => {
    const lotId = ev.detail?.lotId;
    document.dispatchEvent(
      new CustomEvent("__estateEzPwGetSaveGateDone", { detail: getSaveGateSnapshot(lotId) })
    );
  });

  if (document.documentElement) document.documentElement.dataset.estateEzPwBridge = "1";
})();

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "GET_CTSELLER_STEP") {
    sendResponse({ ok: true, step: detectCtSellerStep() });
    return false;
  }

  if (message?.type === "AUTO_UPLOAD_PHOTOS" && Array.isArray(message.files)) {
    void (async () => {
      const out = await performAutoPhotoUpload(message?.lotId, message.files);
      sendResponse(out);
    })();
    return true;
  }

  if (message?.type === "GET_SAVE_GATE_STATE") {
    sendResponse(getSaveGateSnapshot(message?.lotId));
    return false;
  }

  if (message?.type !== "AUTO_FILL_ITEM" || !message.lot) {
    return false;
  }
  void (async () => {
    try {
      const { filled, warnings } = await fillFromLot(message.lot);
      console.log("[Estate-Ez] autofill", { filled, warnings, lotId: message.lot.id });
      sendResponse({ ok: true, filled, warnings });
    } catch (e) {
      console.error("[Estate-Ez]", e);
      sendResponse({ ok: false, error: e instanceof Error ? e.message : String(e) });
    }
  })();
  return true;
});
