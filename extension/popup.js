const STORAGE_KEYS = {
  token: "estateEzToken",
  activity: "estateEzActivity",
  loginEmail: "estateEzLoginEmail",
  /** Pin ``GET /lots`` to one sale (same as web dashboard) so the list stays fast on Railway. */
  saleId: "ct_extension_sale_id"
};

/** Persisted when the side panel / popup would close so reopening restores CT workflow. */
const SESSION_UI_KEYS = {
  currentLotId: "estateEzUiCurrentLotId",
  lastStatus: "estateEzUiStatus",
  lastPreviewText: "estateEzUiPreview",
  lastThumbDataUrl: "estateEzUiThumb"
};

const MAX_THUMB_STORAGE_CHARS = 480_000;

/** JPEG long edge for extension → CT Seller (avoids multi‑MB originals that time out behind Railway). */
const EXTENSION_UPLOAD_THUMB_MAX = 1600;

/** Parallel thumbnail downloads (bounded so Railway / browser stay stable). */
const EXTENSION_PHOTO_FETCH_CONCURRENCY = 5;

/** Align with dashboard / CT Seller walk order (oldest lot first). API must support ``sort`` (newer backends). */
const EXTENSION_LOTS_SORT = "created_asc";
/** Page size when API returns ``X-Total-Count`` (paginated ``limit``/``offset``). */
const EXTENSION_LOTS_PAGE_SIZE = 200;

const API_BASE = "https://caringtransitions-ai-production.up.railway.app";

const lotBrowser = {
  lots: [],
  sales: [],
  currentLotId: "",
  preferredSaleId: ""
};

function isAuthFailureMessage(message) {
  const m = String(message || "").toLowerCase();
  return (
    m.includes("invalid authentication") ||
    m.includes("401") ||
    m.includes("unauthorized")
  );
}

async function clearSession() {
  await chrome.storage.local
    .remove([STORAGE_KEYS.token, ...Object.values(SESSION_UI_KEYS)])
    .catch(() => {});
}

async function persistLotSession() {
  try {
    const status = document.getElementById("status");
    const preview = document.getElementById("preview");
    const thumbEl = document.getElementById("currentLotThumb");
    let thumb = thumbEl?.src?.startsWith("data:") ? thumbEl.src : "";
    if (thumb.length > MAX_THUMB_STORAGE_CHARS) thumb = "";
    await chrome.storage.local.set({
      [SESSION_UI_KEYS.currentLotId]: lotBrowser.currentLotId,
      [SESSION_UI_KEYS.lastStatus]: status ? status.textContent : "",
      [SESSION_UI_KEYS.lastPreviewText]: preview ? preview.textContent : "",
      [SESSION_UI_KEYS.lastThumbDataUrl]: thumb
    });
  } catch {
    /* storage quota / private mode */
  }
}

async function clearLotUiSession() {
  lotBrowser.lots = [];
  lotBrowser.currentLotId = "";
  await chrome.storage.local.remove(Object.values(SESSION_UI_KEYS)).catch(() => {});
}

async function appendActivity(line) {
  const now = new Date().toLocaleString();
  const data = await chrome.storage.local.get([STORAGE_KEYS.activity]);
  const arr = Array.isArray(data[STORAGE_KEYS.activity]) ? data[STORAGE_KEYS.activity] : [];
  arr.unshift(`[${now}] ${line}`);
  const trimmed = arr.slice(0, 20);
  await chrome.storage.local.set({ [STORAGE_KEYS.activity]: trimmed });
  renderActivity(trimmed);
}

function renderActivity(lines) {
  const el = document.getElementById("activity");
  const arr = Array.isArray(lines) ? lines : [];
  el.innerHTML = arr.length
    ? `<strong>Recent actions</strong><br>${arr.map((x) => x.replaceAll("<", "&lt;")).join("<br>")}`
    : "";
}

async function loadActivity() {
  const data = await chrome.storage.local.get([STORAGE_KEYS.activity]);
  renderActivity(data[STORAGE_KEYS.activity]);
}

async function fetchWithRetry(url, init, attempts = 3) {
  let lastErr;
  for (let i = 0; i < attempts; i += 1) {
    if (i > 0) await new Promise((r) => setTimeout(r, 450 + i * 300));
    try {
      const res = await fetch(url, { ...init, cache: "no-store" });
      if ([502, 503, 504].includes(res.status) && i < attempts - 1) {
        await res.text().catch(() => "");
        continue;
      }
      return res;
    } catch (e) {
      lastErr = e instanceof Error ? e : new Error(String(e));
      const transient =
        /failed to fetch|networkerror|network request failed/i.test(lastErr.message) ||
        (e instanceof TypeError && !lastErr.message);
      if (!transient || i === attempts - 1) throw lastErr;
    }
  }
  throw lastErr;
}

function _lotsQueryParams(saleId, offset, pageSize) {
  const p = new URLSearchParams();
  p.set("sort", EXTENSION_LOTS_SORT);
  p.set("limit", String(pageSize));
  p.set("offset", String(offset));
  if (saleId) p.set("sale_id", saleId);
  return p.toString();
}

function saleOptionLabel(sale) {
  const name = String(sale?.name || "Sale").trim() || "Sale";
  return sale?.closed_at ? `${name} (closed)` : name;
}

async function loadSalesList(apiBase, token) {
  const res = await fetchWithRetry(`${apiBase}/sales`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Sales API ${res.status}`);
  }
  const rows = await res.json();
  lotBrowser.sales = Array.isArray(rows) ? rows : [];
  return lotBrowser.sales;
}

function renderSaleSelect() {
  const sel = document.getElementById("saleSelect");
  const hint = document.getElementById("saleHint");
  if (!sel) return;
  sel.innerHTML = "";
  const sales = lotBrowser.sales;
  if (!sales.length) {
    sel.disabled = true;
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No sales — add one in Estate app";
    sel.appendChild(opt);
    if (hint) hint.textContent = "Create a sale on the web dashboard first.";
    return;
  }
  sel.disabled = false;
  for (const s of sales) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = saleOptionLabel(s);
    sel.appendChild(opt);
  }
  const preferred = String(lotBrowser.preferredSaleId || "").trim();
  if (preferred && sales.some((s) => s.id === preferred)) {
    sel.value = preferred;
  } else {
    sel.value = sales[0].id;
    lotBrowser.preferredSaleId = sales[0].id;
    void chrome.storage.local.set({ [STORAGE_KEYS.saleId]: sales[0].id }).catch(() => {});
  }
  const active = sales.find((s) => s.id === sel.value);
  if (hint && active) {
    hint.textContent = active.closed_at
      ? "This sale is closed — you can still browse lots, but uploads may be disabled in the app."
      : "Lots and CT Seller actions use the selected sale.";
  }
}

async function setPreferredSale(saleId, { persist = true } = {}) {
  const sid = String(saleId || "").trim();
  lotBrowser.preferredSaleId = sid;
  lotBrowser.lots = [];
  lotBrowser.currentLotId = "";
  if (persist) {
    if (sid) {
      await chrome.storage.local.set({ [STORAGE_KEYS.saleId]: sid }).catch(() => {});
    } else {
      await chrome.storage.local.remove([STORAGE_KEYS.saleId]).catch(() => {});
    }
    await chrome.storage.local.remove(Object.values(SESSION_UI_KEYS)).catch(() => {});
  }
  const sel = document.getElementById("saleSelect");
  if (sel && sid) sel.value = sid;
  renderSaleSelect();
  const preview = document.getElementById("preview");
  if (preview) preview.textContent = "";
  setCurrentLotUI(null, "");
}

async function ensureSaleSelected(apiBase, token) {
  if (!lotBrowser.sales.length) {
    await loadSalesList(apiBase, token);
    renderSaleSelect();
  }
  let sid = String(lotBrowser.preferredSaleId || "").trim();
  if (!sid && lotBrowser.sales.length === 1) {
    sid = lotBrowser.sales[0].id;
    await setPreferredSale(sid);
  }
  if (!sid && lotBrowser.sales.length > 1) {
    throw new Error("Select a sale in the dropdown above.");
  }
  if (!lotBrowser.sales.length) {
    throw new Error("No sales found — create a sale in the Estate app first.");
  }
  return sid;
}

async function refreshSalesUi(apiBase, token) {
  await loadSalesList(apiBase, token);
  renderSaleSelect();
}

async function fetchLots(apiBase, token) {
  await ensureSaleSelected(apiBase, token);
  const sid = String(lotBrowser.preferredSaleId || "").trim();

  async function fetchOnce(queryString) {
    const url = `${apiBase}/lots?${queryString}`;
    const res = await fetchWithRetry(url, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `API ${res.status}`);
    }
    const lots = await res.json();
    const totalHdr = res.headers.get("X-Total-Count");
    const total = totalHdr != null && totalHdr !== "" ? parseInt(totalHdr, 10) : NaN;
    return { lots: Array.isArray(lots) ? lots : [], total };
  }

  let merged = [];
  let offset = 0;
  for (;;) {
    const { lots, total } = await fetchOnce(_lotsQueryParams(sid, offset, EXTENSION_LOTS_PAGE_SIZE));
    if (!Number.isFinite(total)) {
      merged = lots;
      break;
    }
    merged.push(...lots);
    if (lots.length === 0 || merged.length >= total || lots.length < EXTENSION_LOTS_PAGE_SIZE) {
      break;
    }
    offset += EXTENSION_LOTS_PAGE_SIZE;
  }

  if (!Array.isArray(merged) || merged.length === 0) {
    const saleName =
      lotBrowser.sales.find((s) => s.id === sid)?.name || "this sale";
    throw new Error(`No lots in “${saleName}” — add inventory in the Estate app or pick another sale.`);
  }
  return merged;
}

function firstOpenIndex(lots) {
  return lots.findIndex((l) => !l?.is_completed);
}

/**
 * After completing a lot, advance to the next open lot in ``created_asc`` walk order (same
 * as dashboard), not the globally first open lot — otherwise finishing lot 12 jumps back to 1.
 */
function nextOpenLotIndexAfterComplete(lots, completedLotId) {
  if (!Array.isArray(lots) || !lots.length) return -1;
  const at = lots.findIndex((l) => l?.id === completedLotId);
  if (at < 0) return firstOpenIndex(lots);
  for (let j = at + 1; j < lots.length; j += 1) {
    if (!lots[j]?.is_completed) return j;
  }
  return firstOpenIndex(lots);
}

function getCurrentLotFromCache() {
  if (!lotBrowser.currentLotId) return null;
  return lotBrowser.lots.find((l) => l?.id === lotBrowser.currentLotId) || null;
}

async function resolveCurrentLot(apiBase, token, { preferNextOpen = true } = {}) {
  const lots = await fetchLots(apiBase, token);
  lotBrowser.lots = lots;
  if (!lots.length) throw new Error("No lots returned — create or select inventory in the Estate app first.");

  let selected = getCurrentLotFromCache();
  if (selected?.is_completed) selected = null;
  if (!selected) {
    const idx = preferNextOpen ? firstOpenIndex(lots) : -1;
    selected = idx >= 0 ? lots[idx] : lots[0];
  }
  lotBrowser.currentLotId = selected?.id || "";
  void persistLotSession();
  return selected;
}

function moveCurrentLot(step) {
  if (!lotBrowser.lots.length) throw new Error("No lots loaded yet. Click Preview first.");
  const idx = lotBrowser.lots.findIndex((l) => l?.id === lotBrowser.currentLotId);
  const base = idx >= 0 ? idx : 0;
  const nextIndex = Math.min(Math.max(base + step, 0), lotBrowser.lots.length - 1);
  const lot = lotBrowser.lots[nextIndex];
  lotBrowser.currentLotId = lot?.id || "";
  return lot;
}

/** ``which`` = ``"first"`` (oldest in list) or ``"last"`` (newest) — matches ``created_asc`` order from the API. */
function moveCurrentLotToBoundary(which) {
  if (!lotBrowser.lots.length) throw new Error("No lots loaded yet. Click Preview first.");
  const last = lotBrowser.lots.length - 1;
  const idx = which === "last" ? last : 0;
  const lot = lotBrowser.lots[idx];
  lotBrowser.currentLotId = lot?.id || "";
  return lot;
}

async function markLotCompleted(apiBase, token, lotId) {
  const res = await fetchWithRetry(`${apiBase}/lots/${encodeURIComponent(lotId)}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ is_completed: true })
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Could not mark lot completed (API ${res.status})`);
  }
  return res.json();
}

function isSkuTapePhoto(photo) {
  return String(photo?.photo_role || "").trim().toLowerCase() === "tape";
}

function listingPhotosForCtBids(photos) {
  return (Array.isArray(photos) ? photos : []).filter((p) => !isSkuTapePhoto(p));
}

async function fetchLotPhotos(apiBase, token, lotId, { forListing = false } = {}) {
  const qs = forListing ? "?for_listing=true" : "";
  const res = await fetchWithRetry(`${apiBase}/lots/${encodeURIComponent(lotId)}/photos${qs}`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Photo list API ${res.status}`);
  }
  const list = await res.json();
  if (!Array.isArray(list)) {
    throw new Error("No linked photos found for this lot.");
  }
  const photos = forListing ? listingPhotosForCtBids(list) : list;
  if (!photos.length) {
    throw new Error(
      forListing
        ? "Only SKU tape photos on this lot — add a product photo before posting to CT Bids."
        : "No linked photos found for this lot."
    );
  }
  return photos;
}

/**
 * Loads a downscaled JPEG from the Estate API for CT Seller upload.
 * Returns ``ArrayBuffer`` in-memory; the popup encodes to base64 only when sending to the tab (Chrome message JSON).
 */
async function fetchPhotoJpegBuffer(apiBase, token, lotId, photoId, originalName) {
  const res = await fetchWithRetry(
    `${apiBase}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=${EXTENSION_UPLOAD_THUMB_MAX}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Photo API ${res.status}`);
  }
  const arrayBuffer = await res.arrayBuffer();
  const stem = String(originalName || "photo")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.[^.]+$/i, "");
  const safeName = (stem || "photo").replace(/[^\w.\-]+/g, "_").slice(0, 120) || "photo";
  return { type: "image/jpeg", arrayBuffer, fileName: `${safeName}.jpg` };
}

/** Data URL for the small lot preview card (one image; async FileReader). */
function jpegBufferToDataUrl(arrayBuffer, mime = "image/jpeg") {
  return new Promise((resolve, reject) => {
    const blob = new Blob([arrayBuffer], { type: mime });
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ""));
    r.onerror = () => reject(r.error || new Error("readAsDataURL failed"));
    r.readAsDataURL(blob);
  });
}

/**
 * Base64 for ``chrome.tabs.sendMessage``. Chrome extension messages are JSON-serialized by default,
 * so ``ArrayBuffer`` does not survive the hop to the content script — we must send a string.
 */
function arrayBufferToBase64Chunked(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  let binary = "";
  const step = 8192;
  for (let i = 0; i < bytes.length; i += step) {
    const slice = bytes.subarray(i, Math.min(i + step, bytes.length));
    binary += String.fromCharCode.apply(null, slice);
  }
  return btoa(binary);
}

/**
 * Run async ``mapper`` on ``items`` with at most ``concurrency`` in flight; preserves order in the result array.
 */
async function mapWithConcurrency(items, concurrency, mapper) {
  const results = new Array(items.length);
  let next = 0;

  async function worker() {
    while (true) {
      const i = next;
      next += 1;
      if (i >= items.length) break;
      results[i] = await mapper(items[i], i);
    }
  }

  const n = Math.max(1, Math.min(concurrency, items.length));
  await Promise.all(Array.from({ length: n }, () => worker()));
  return results;
}

function setCurrentLotUI(lot, thumbDataUrl = "") {
  const meta = document.getElementById("currentLotMeta");
  const img = document.getElementById("currentLotThumb");
  if (!meta || !img) return;
  if (!lot) {
    meta.textContent = "Not loaded yet.";
    img.removeAttribute("src");
    img.style.display = "none";
    return;
  }
  const sku = lot?.sku || lot?.id || "-";
  const title = lot?.auction_title || "(untitled)";
  const photoCount = lot?.photo_count ?? 0;
  const idx = lotBrowser.lots.findIndex((l) => l?.id === lot?.id);
  const pos = idx >= 0 ? `${idx + 1}/${lotBrowser.lots.length}` : "-/-";
  const done = lot?.is_completed ? "done" : "open";
  const saleName = lotBrowser.sales.find((s) => s.id === lot?.sale_id)?.name;
  const salePrefix = saleName ? `${saleName} · ` : "";
  meta.textContent = `${salePrefix}#${pos} | ${done} | SKU ${sku} - ${title} (photos: ${photoCount})`;
  if (thumbDataUrl) {
    img.src = thumbDataUrl;
    img.style.display = "block";
  } else {
    img.removeAttribute("src");
    img.style.display = "none";
  }
}

async function refreshCurrentLotCard(apiBase, token, lot) {
  setCurrentLotUI(lot, "");
  try {
    const photos = await fetchLotPhotos(apiBase, token, lot.id);
    if (!photos.length) return;
    const firstPhoto = photos[0];
    const downloaded = await fetchPhotoJpegBuffer(apiBase, token, lot.id, firstPhoto.id, firstPhoto.original_name);
    const dataUrl = await jpegBufferToDataUrl(downloaded.arrayBuffer, downloaded.type || "image/jpeg");
    setCurrentLotUI(lot, dataUrl);
  } catch (_err) {
    // Keep metadata visible even if thumbnail cannot load.
  } finally {
    void persistLotSession();
  }
}

async function sendToActiveTabWithFallback(message) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("No active tab.");
  }

  try {
    return await chrome.tabs.sendMessage(tab.id, message);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const noReceiver = msg.includes("Receiving end does not exist");
    if (!noReceiver) throw err;

    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"]
    });
    return await chrome.tabs.sendMessage(tab.id, message);
  }
}

async function getActiveTabStep() {
  const reply = await sendToActiveTabWithFallback({ type: "GET_CTSELLER_STEP" });
  return reply?.step || "unknown";
}

async function getSaveGateState(lotId) {
  const reply = await sendToActiveTabWithFallback({ type: "GET_SAVE_GATE_STATE", lotId });
  return {
    armed: Boolean(reply?.armed),
    saved: Boolean(reply?.saved),
    lotId: String(reply?.lotId || "")
  };
}

async function waitForSaveClick(lotId, timeoutMs = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const state = await getSaveGateState(lotId);
    if (state.saved) return true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return false;
}

function specLinePlainForPreview(item) {
  return String(item || "")
    .trim()
    .replace(/^\s*[-•*·◇►]+\s*/, "");
}

function specLinesBrandValuesBlobLower(specItems) {
  const bits = [];
  for (const raw of specItems) {
    const line = specLinePlainForPreview(raw);
    let m = /^brands?\s*:\s*(.+)$/i.exec(line);
    if (!m) m = /^maker\s*:\s*(.+)$/i.exec(line);
    if (!m) m = /^manufacturers?\s*:\s*(.+)$/i.exec(line);
    if (!m) m = /^origin\s*:\s*(.+)$/i.exec(line);
    if (m) bits.push(m[1].trim());
  }
  if (!bits.length) return "";
  return bits.join("; ").toLowerCase();
}

function filterBrandsForFooter(brands, specItems) {
  const blob = specLinesBrandValuesBlobLower(specItems);
  if (!blob) return brands;
  return brands.filter((b) => {
    const t = String(b || "").trim().toLowerCase();
    if (!t) return false;
    return !blob.includes(t);
  });
}

function buildDescription(lot) {
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

  const sections = [];
  if (body) sections.push(body);
  if (specItems.length) {
    sections.push(["Key Details:", ...specItems.map((item) => `- ${item}`)].join("\n"));
  }
  if (brands.length) sections.push(`Brands: ${brands.join(", ")}`);
  // Single newlines between blocks (matches extension content script plain text).
  return sections.join("\n").trim();
}

/** Full preview block for the side panel — must stay in sync whenever the selected lot changes. */
function formatLotPreviewText(lot) {
  if (!lot) return "";
  const computedDescription = buildDescription(lot);
  const rawDescription =
    lot.description || lot.auction_description || lot.ai_description || "";
  const shownDescription = computedDescription || rawDescription || "-";
  return [
    `SKU: ${lot.sku || "-"}`,
    `Title: ${lot.auction_title || "-"}`,
    `Category: ${lot.ct_category || "-"}`,
    `Condition: ${lot.condition_text || "-"}`,
    `Price: ${lot.starting_price ?? "-"}`,
    `Photos linked: ${lot.photo_count ?? 0}`,
    "",
    "Description:",
    shownDescription
  ].join("\n");
}

document.addEventListener("DOMContentLoaded", () => {
  const status = document.getElementById("status");
  const preview = document.getElementById("preview");
  const loginEmailEl = document.getElementById("loginEmail");
  const loginPasswordEl = document.getElementById("loginPassword");
  const loginBtn = document.getElementById("loginBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const authState = document.getElementById("authState");
  const salePanel = document.getElementById("salePanel");
  const saleSelect = document.getElementById("saleSelect");
  const actionButtonIds = [
    "previewBtn",
    "fillBtn",
    "uploadBtn",
    "firstLotBtn",
    "lastLotBtn",
    "prevLotBtn",
    "nextLotBtn"
  ];
  let authToken = "";

  async function applySelectedLotNav(apiBase, token, lot, statusText) {
    preview.textContent = formatLotPreviewText(lot);
    await refreshCurrentLotCard(apiBase, token, lot);
    status.textContent = statusText;
    void persistLotSession();
  }

  function setActionButtonsEnabled(enabled) {
    for (const id of actionButtonIds) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.disabled = !enabled;
    }
  }

  function renderAuthState() {
    const signedIn = Boolean(authToken);
    loginPasswordEl.style.display = signedIn ? "none" : "block";
    loginBtn.style.display = signedIn ? "none" : "block";
    logoutBtn.style.display = signedIn ? "block" : "none";
    if (salePanel) salePanel.style.display = signedIn ? "block" : "none";
    const email = loginEmailEl.value.trim();
    authState.textContent = signedIn
      ? `Signed in${email ? ` as ${email}` : ""}.`
      : "Not signed in.";
    setActionButtonsEnabled(signedIn);
    if (!signedIn) {
      lotBrowser.sales = [];
      setCurrentLotUI(null, "");
    }
  }

  async function readCreds(requireSaveMessage) {
    if (!authToken) {
      status.textContent = requireSaveMessage
        ? "Please sign in first."
        : "Sign in first.";
      return null;
    }
    return { apiBase: API_BASE, token: authToken };
  }

  void (async () => {
    const data = await chrome.storage.local.get([
      STORAGE_KEYS.token,
      STORAGE_KEYS.loginEmail,
      STORAGE_KEYS.saleId,
      SESSION_UI_KEYS.currentLotId,
      SESSION_UI_KEYS.lastStatus,
      SESSION_UI_KEYS.lastPreviewText,
      SESSION_UI_KEYS.lastThumbDataUrl
    ]);
    authToken = String(data[STORAGE_KEYS.token] || "");
    loginEmailEl.value = String(data[STORAGE_KEYS.loginEmail] || "");
    lotBrowser.preferredSaleId = String(data[STORAGE_KEYS.saleId] || "").trim();
    renderAuthState();

    if (authToken) {
      try {
        await refreshSalesUi(API_BASE, authToken);
      } catch (e) {
        status.textContent = e instanceof Error ? e.message : "Could not load sales.";
      }
      const cid = String(data[SESSION_UI_KEYS.currentLotId] || "").trim();
      if (cid) {
        lotBrowser.currentLotId = cid;
        const cachedStatus = String(data[SESSION_UI_KEYS.lastStatus] || "").trim();
        const cachedThumb = String(data[SESSION_UI_KEYS.lastThumbDataUrl] || "").trim();
        if (cachedStatus) status.textContent = cachedStatus;
        try {
          const lots = await fetchLots(API_BASE, authToken);
          lotBrowser.lots = lots;
          const lot = lots.find((l) => l?.id === cid);
          if (lot) {
            preview.textContent = formatLotPreviewText(lot);
            setCurrentLotUI(lot, cachedThumb.startsWith("data:") ? cachedThumb : "");
            void refreshCurrentLotCard(API_BASE, authToken, lot);
          } else {
            status.textContent = "Previous lot no longer in list — click Preview current lot.";
            await chrome.storage.local.remove(Object.values(SESSION_UI_KEYS)).catch(() => {});
            lotBrowser.currentLotId = "";
          }
        } catch {
          status.textContent = "Could not reload lots — click Preview when online.";
          lotBrowser.currentLotId = "";
        }
      }
    }

    void loadActivity();
  })();

  loginBtn.addEventListener("click", async () => {
    const email = loginEmailEl.value.trim().toLowerCase();
    const password = loginPasswordEl.value;
    if (!email || !password) {
      status.textContent = "Enter email and password.";
      return;
    }
    status.textContent = "Signing in…";
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Login failed (${res.status})`);
      }
      const out = await res.json();
      const token = String(out?.access_token || "");
      if (!token) throw new Error("Login failed: token missing.");
      authToken = token;
      await chrome.storage.local.set({
        [STORAGE_KEYS.token]: token,
        [STORAGE_KEYS.loginEmail]: email
      });
      loginPasswordEl.value = "";
      renderAuthState();
      status.textContent = "Loading sales…";
      try {
        await refreshSalesUi(API_BASE, authToken);
        status.textContent = "Signed in — select a sale, then Preview.";
      } catch (e) {
        status.textContent = e instanceof Error ? e.message : "Signed in, but sales failed to load.";
      }
    } catch (e) {
      console.error(e);
      status.textContent = e instanceof Error ? e.message : "Sign in failed.";
    }
  });

  logoutBtn.addEventListener("click", async () => {
    authToken = "";
    await clearSession();
    lotBrowser.preferredSaleId = "";
    lotBrowser.sales = [];
    lotBrowser.lots = [];
    lotBrowser.currentLotId = "";
    await chrome.storage.local.remove([STORAGE_KEYS.saleId]).catch(() => {});
    renderAuthState();
    status.textContent = "Signed out.";
    preview.textContent = "";
    setCurrentLotUI(null, "");
  });

  function handleActionError(err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (isAuthFailureMessage(msg)) {
      void clearSession();
      authToken = "";
      lotBrowser.lots = [];
      lotBrowser.currentLotId = "";
      renderAuthState();
      status.textContent = "Session expired. Please sign in again.";
      preview.textContent = "";
      setCurrentLotUI(null, "");
      return;
    }
    status.textContent = msg;
  }

  document.getElementById("fillBtn").addEventListener("click", async () => {
    status.textContent = "Working…";
    try {
      const creds = await readCreds(true);
      if (!creds) return;
      const { apiBase, token } = creds;
      const lot = await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      await refreshCurrentLotCard(apiBase, token, lot);
      preview.textContent = formatLotPreviewText(lot);
      const step = await getActiveTabStep();
      if (step !== "detail") {
        status.textContent = `You are on "${step}" step. Go to Detail (Step 1) first.`;
        await appendActivity(`Blocked autofill: wrong step (${step})`);
        return;
      }

      const reply = await sendToActiveTabWithFallback({
        type: "AUTO_FILL_ITEM",
        source: "estate-ez-popup",
        lot
      });

      if (reply?.ok) {
        const filled = reply.filled?.length ? reply.filled.join(", ") : "page";
        const warnings = Array.isArray(reply.warnings) && reply.warnings.length
          ? ` Warnings: ${reply.warnings.join(" | ")}`
          : "";
        status.textContent = `Filled: ${filled}.${warnings}`;
        void persistLotSession();
        await appendActivity(`Autofill lot ${lot.sku || lot.id}: ${filled}`);
      } else {
        status.textContent = "Message sent; no fill report.";
        await appendActivity("Autofill returned no report");
      }
    } catch (err) {
      console.error(err);
      handleActionError(err);
    }
  });

  document.getElementById("uploadBtn").addEventListener("click", async () => {
    status.textContent = "Fetching lot photos…";
    try {
      const creds = await readCreds(true);
      if (!creds) return;
      const { apiBase, token } = creds;
      const lot = await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      await refreshCurrentLotCard(apiBase, token, lot);
      preview.textContent = formatLotPreviewText(lot);
      const step = await getActiveTabStep();
      if (step !== "media") {
        status.textContent = `You are on "${step}" step. Go to Media (Step 3) first.`;
        await appendActivity(`Blocked photo upload: wrong step (${step})`);
        return;
      }
      const photos = await fetchLotPhotos(apiBase, token, lot.id, { forListing: true });
      if (!photos.length) {
        throw new Error("Only SKU tape photos on this lot — add a product photo before posting to CT Bids.");
      }

      const total = photos.length;
      let fetchDone = 0;
      status.textContent = `Fetching lot photos… (0/${total})`;
      const buffers = await mapWithConcurrency(
        photos,
        EXTENSION_PHOTO_FETCH_CONCURRENCY,
        async (p) => {
          const out = await fetchPhotoJpegBuffer(apiBase, token, lot.id, p.id, p.original_name);
          fetchDone += 1;
          status.textContent = `Fetching lot photos… (${fetchDone}/${total})`;
          return out;
        }
      );

      const payloadFiles = buffers.map((downloaded, i) => {
        const p = photos[i];
        return {
          name: downloaded.fileName || p?.original_name || `${p?.id || i}.jpg`,
          type: downloaded.type || "image/jpeg",
          dataBase64: arrayBufferToBase64Chunked(downloaded.arrayBuffer)
        };
      });

      status.textContent = "Uploading into CT Seller form…";
      const reply = await sendToActiveTabWithFallback({
        type: "AUTO_UPLOAD_PHOTOS",
        source: "estate-ez-popup",
        lotId: lot.id,
        files: payloadFiles
      });
      if (reply?.ok) {
        status.textContent = `Queued ${reply.count || payloadFiles.length} image(s). Waiting for page Save click…`;
        void persistLotSession();
        const saveClicked = await waitForSaveClick(lot.id, 60000);
        if (!saveClicked) {
          status.textContent = "Photos queued. Click Save on CT Seller first, then re-run upload if needed. Lot not advanced.";
          void persistLotSession();
          await appendActivity(`Save not detected for lot ${lot.sku || lot.id}; not marked completed`);
          return;
        }
        await markLotCompleted(apiBase, token, lot.id);
        let nextLabel = "";
        try {
          const refreshed = await fetchLots(apiBase, token);
          lotBrowser.lots = refreshed;
          const idx = nextOpenLotIndexAfterComplete(refreshed, lot.id);
          if (idx >= 0) {
            const nextLot = refreshed[idx];
            lotBrowser.currentLotId = nextLot?.id || "";
            nextLabel = nextLot?.sku || nextLot?.id || "";
            preview.textContent = formatLotPreviewText(nextLot);
            await refreshCurrentLotCard(apiBase, token, nextLot);
          }
        } catch (_err) {
          // Ignore "all completed" check here; upload already succeeded.
        }
        status.textContent = nextLabel
          ? `Saved + completed lot. Next lot: ${nextLabel}`
          : "Saved + completed lot.";
        void persistLotSession();
        await appendActivity(`Uploaded ${reply.count || payloadFiles.length} photos for lot ${lot.sku || lot.id}`);
        await appendActivity(`Detected CT Seller Save click for lot ${lot.sku || lot.id}`);
        await appendActivity(`Marked lot ${lot.sku || lot.id} completed`);
      } else {
        status.textContent = reply?.error || "Upload message sent, but no response.";
        await appendActivity(`Photo upload failed for lot ${lot.sku || lot.id}`);
      }
    } catch (err) {
      console.error(err);
      handleActionError(err);
    }
  });

  if (saleSelect) {
    saleSelect.addEventListener("change", async () => {
      const creds = await readCreds(false);
      if (!creds) return;
      const nextId = String(saleSelect.value || "").trim();
      if (!nextId || nextId === lotBrowser.preferredSaleId) return;
      status.textContent = "Switching sale…";
      try {
        await setPreferredSale(nextId);
        status.textContent = "Sale changed — click Preview current lot.";
        await appendActivity(`Selected sale ${saleOptionLabel(lotBrowser.sales.find((s) => s.id === nextId) || {})}`);
      } catch (err) {
        handleActionError(err);
      }
    });
  }

  document.getElementById("previewBtn").addEventListener("click", async () => {
    status.textContent = "Loading preview…";
    try {
      const creds = await readCreds(false);
      if (!creds) return;
      const { apiBase, token } = creds;
      const lot = await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      await refreshCurrentLotCard(apiBase, token, lot);
      preview.textContent = formatLotPreviewText(lot);
      status.textContent = "Preview ready.";
      void persistLotSession();
      await appendActivity(`Previewed lot ${lot.sku || lot.id}`);
    } catch (err) {
      console.error(err);
      handleActionError(err);
    }
  });

  document.getElementById("firstLotBtn").addEventListener("click", async () => {
    try {
      const creds = await readCreds(false);
      if (!creds) return;
      const { apiBase, token } = creds;
      if (!lotBrowser.lots.length) {
        await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      }
      const lot = moveCurrentLotToBoundary("first");
      await applySelectedLotNav(apiBase, token, lot, `First lot: ${lot?.sku || lot?.id || "-"}`);
    } catch (err) {
      handleActionError(err);
    }
  });

  document.getElementById("lastLotBtn").addEventListener("click", async () => {
    try {
      const creds = await readCreds(false);
      if (!creds) return;
      const { apiBase, token } = creds;
      if (!lotBrowser.lots.length) {
        await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      }
      const lot = moveCurrentLotToBoundary("last");
      await applySelectedLotNav(apiBase, token, lot, `Last lot: ${lot?.sku || lot?.id || "-"}`);
    } catch (err) {
      handleActionError(err);
    }
  });

  document.getElementById("prevLotBtn").addEventListener("click", async () => {
    try {
      const creds = await readCreds(false);
      if (!creds) return;
      const { apiBase, token } = creds;
      if (!lotBrowser.lots.length) {
        await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      }
      const lot = moveCurrentLot(-1);
      await applySelectedLotNav(apiBase, token, lot, `Selected previous lot: ${lot?.sku || lot?.id || "-"}`);
    } catch (err) {
      handleActionError(err);
    }
  });

  document.getElementById("nextLotBtn").addEventListener("click", async () => {
    try {
      const creds = await readCreds(false);
      if (!creds) return;
      const { apiBase, token } = creds;
      if (!lotBrowser.lots.length) {
        await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      }
      const lot = moveCurrentLot(1);
      await applySelectedLotNav(apiBase, token, lot, `Selected next lot: ${lot?.sku || lot?.id || "-"}`);
    } catch (err) {
      handleActionError(err);
    }
  });
});
