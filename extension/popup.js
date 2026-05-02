const STORAGE_KEYS = {
  token: "estateEzToken",
  activity: "estateEzActivity",
  loginEmail: "estateEzLoginEmail",
  /** Pin ``GET /lots`` to one sale (same as web dashboard) so the list stays fast on Railway. */
  saleId: "ct_extension_sale_id"
};

/** JPEG long edge for extension → CT Seller (avoids multi‑MB originals that time out behind Railway). */
const EXTENSION_UPLOAD_THUMB_MAX = 1600;

const API_BASE = "https://caringtransitions-ai-production.up.railway.app";

const lotBrowser = {
  lots: [],
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
  await chrome.storage.local.remove([STORAGE_KEYS.token]);
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

async function fetchLots(apiBase, token) {
  let url = `${apiBase}/lots`;
  const sid = String(lotBrowser.preferredSaleId || "").trim();
  if (sid) {
    url += `?sale_id=${encodeURIComponent(sid)}`;
  }
  let res = await fetchWithRetry(url, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `API ${res.status}`);
  }
  let lots = await res.json();
  if ((!Array.isArray(lots) || lots.length === 0) && sid) {
    lotBrowser.preferredSaleId = "";
    await chrome.storage.local.remove([STORAGE_KEYS.saleId]).catch(() => {});
    url = `${apiBase}/lots`;
    res = await fetchWithRetry(url, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `API ${res.status}`);
    }
    lots = await res.json();
  }
  if (!Array.isArray(lots) || lots.length === 0) {
    throw new Error("No lots returned — create or select inventory in the Estate app first.");
  }
  return lots;
}

function firstOpenIndex(lots) {
  return lots.findIndex((l) => !l?.is_completed);
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
  if (!selected) {
    const idx = preferNextOpen ? firstOpenIndex(lots) : -1;
    selected = idx >= 0 ? lots[idx] : lots[0];
  }
  lotBrowser.currentLotId = selected?.id || "";
  const saleId = String(selected?.sale_id || "").trim();
  if (saleId) {
    lotBrowser.preferredSaleId = saleId;
    void chrome.storage.local.set({ [STORAGE_KEYS.saleId]: saleId }).catch(() => {});
  }
  return selected;
}

function moveCurrentLot(step) {
  if (!lotBrowser.lots.length) throw new Error("No lots loaded yet. Click Preview first.");
  const idx = lotBrowser.lots.findIndex((l) => l?.id === lotBrowser.currentLotId);
  const base = idx >= 0 ? idx : 0;
  const nextIndex = Math.min(Math.max(base + step, 0), lotBrowser.lots.length - 1);
  const lot = lotBrowser.lots[nextIndex];
  lotBrowser.currentLotId = lot?.id || "";
  const saleId = String(lot?.sale_id || "").trim();
  if (saleId) {
    lotBrowser.preferredSaleId = saleId;
    void chrome.storage.local.set({ [STORAGE_KEYS.saleId]: saleId }).catch(() => {});
  }
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

async function fetchLotPhotos(apiBase, token, lotId) {
  const res = await fetchWithRetry(`${apiBase}/lots/${encodeURIComponent(lotId)}/photos`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Photo list API ${res.status}`);
  }
  const list = await res.json();
  if (!Array.isArray(list) || list.length === 0) {
    throw new Error("No linked photos found for this lot.");
  }
  return list;
}

/**
 * Loads a downscaled JPEG for CT Seller upload. Full ``/download`` originals are often too large
 * and cause Railway 502 / timeouts when many photos are fetched in sequence from the extension popup.
 */
async function fetchPhotoBlob(apiBase, token, lotId, photoId, originalName) {
  const res = await fetchWithRetry(
    `${apiBase}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=${EXTENSION_UPLOAD_THUMB_MAX}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Photo API ${res.status}`);
  }
  const blob = await res.blob();
  const ab = await blob.arrayBuffer();
  const bytes = new Uint8Array(ab);
  // Avoid String.fromCharCode(... hugeUint8Array): spread size can exceed engine limits and throw.
  let binary = "";
  const step = 8192;
  for (let i = 0; i < bytes.length; i += step) {
    const slice = bytes.subarray(i, Math.min(i + step, bytes.length));
    binary += String.fromCharCode.apply(null, slice);
  }
  const stem = String(originalName || "photo")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.[^.]+$/i, "");
  const safeName = (stem || "photo").replace(/[^\w.\-]+/g, "_").slice(0, 120) || "photo";
  return { type: "image/jpeg", dataBase64: btoa(binary), fileName: `${safeName}.jpg` };
}

function setCurrentLotUI(lot, thumbDataUrl = "") {
  const meta = document.getElementById("currentLotMeta");
  const img = document.getElementById("currentLotThumb");
  if (!meta || !img) return;
  const sku = lot?.sku || lot?.id || "-";
  const title = lot?.auction_title || "(untitled)";
  const photoCount = lot?.photo_count ?? 0;
  const idx = lotBrowser.lots.findIndex((l) => l?.id === lot?.id);
  const pos = idx >= 0 ? `${idx + 1}/${lotBrowser.lots.length}` : "-/-";
  const done = lot?.is_completed ? "done" : "open";
  meta.textContent = `#${pos} | ${done} | SKU ${sku} - ${title} (photos: ${photoCount})`;
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
    const downloaded = await fetchPhotoBlob(apiBase, token, lot.id, firstPhoto.id, firstPhoto.original_name);
    const dataUrl = `data:${downloaded.type || "image/jpeg"};base64,${downloaded.dataBase64}`;
    setCurrentLotUI(lot, dataUrl);
  } catch (_err) {
    // Keep metadata visible even if thumbnail cannot load.
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

  const brands = Array.isArray(lot?.brands) ? lot.brands.map((x) => String(x || "").trim()).filter(Boolean) : [];

  const sections = [];
  if (body) sections.push(body);
  if (specItems.length) {
    sections.push(["Key Details:", ...specItems.map((item) => `- ${item}`)].join("\n"));
  }
  if (brands.length) sections.push(`Brands: ${brands.join(", ")}`);
  return sections.join("\n\n").trim();
}

document.addEventListener("DOMContentLoaded", () => {
  const status = document.getElementById("status");
  const preview = document.getElementById("preview");
  const loginEmailEl = document.getElementById("loginEmail");
  const loginPasswordEl = document.getElementById("loginPassword");
  const loginBtn = document.getElementById("loginBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const authState = document.getElementById("authState");
  const actionButtonIds = ["previewBtn", "fillBtn", "uploadBtn", "prevLotBtn", "nextLotBtn"];
  let authToken = "";

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
    const email = loginEmailEl.value.trim();
    authState.textContent = signedIn
      ? `Signed in${email ? ` as ${email}` : ""}.`
      : "Not signed in.";
    setActionButtonsEnabled(signedIn);
    if (!signedIn) {
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
      STORAGE_KEYS.saleId
    ]);
    authToken = String(data[STORAGE_KEYS.token] || "");
    loginEmailEl.value = String(data[STORAGE_KEYS.loginEmail] || "");
    lotBrowser.preferredSaleId = String(data[STORAGE_KEYS.saleId] || "").trim();
    renderAuthState();
  })();
  void loadActivity();

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
      status.textContent = "Signed in.";
    } catch (e) {
      console.error(e);
      status.textContent = e instanceof Error ? e.message : "Sign in failed.";
    }
  });

  logoutBtn.addEventListener("click", async () => {
    authToken = "";
    await clearSession();
    lotBrowser.preferredSaleId = "";
    lotBrowser.lots = [];
    lotBrowser.currentLotId = "";
    await chrome.storage.local.remove([STORAGE_KEYS.saleId]).catch(() => {});
    renderAuthState();
    status.textContent = "Signed out.";
    preview.textContent = "";
  });

  function handleActionError(err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (isAuthFailureMessage(msg)) {
      void clearSession();
      authToken = "";
      renderAuthState();
      status.textContent = "Session expired. Please sign in again.";
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
      const step = await getActiveTabStep();
      if (step !== "media") {
        status.textContent = `You are on "${step}" step. Go to Media (Step 3) first.`;
        await appendActivity(`Blocked photo upload: wrong step (${step})`);
        return;
      }
      const photos = await fetchLotPhotos(apiBase, token, lot.id);

      const payloadFiles = [];
      for (let i = 0; i < photos.length; i += 1) {
        const p = photos[i];
        status.textContent = `Fetching lot photos… (${i + 1}/${photos.length})`;
        const downloaded = await fetchPhotoBlob(apiBase, token, lot.id, p.id, p.original_name);
        payloadFiles.push({
          name: downloaded.fileName || p.original_name || `${p.id}.jpg`,
          type: downloaded.type,
          dataBase64: downloaded.dataBase64
        });
      }

      status.textContent = "Uploading into CT Seller form…";
      const reply = await sendToActiveTabWithFallback({
        type: "AUTO_UPLOAD_PHOTOS",
        source: "estate-ez-popup",
        lotId: lot.id,
        files: payloadFiles
      });
      if (reply?.ok) {
        status.textContent = `Queued ${reply.count || payloadFiles.length} image(s). Waiting for page Save click…`;
        const saveClicked = await waitForSaveClick(lot.id, 60000);
        if (!saveClicked) {
          status.textContent = "Photos queued. Click Save on CT Seller first, then re-run upload if needed. Lot not advanced.";
          await appendActivity(`Save not detected for lot ${lot.sku || lot.id}; not marked completed`);
          return;
        }
        await markLotCompleted(apiBase, token, lot.id);
        let nextLabel = "";
        try {
          const refreshed = await fetchLots(apiBase, token);
          lotBrowser.lots = refreshed;
          const idx = firstOpenIndex(refreshed);
          if (idx >= 0) {
            const nextLot = refreshed[idx];
            lotBrowser.currentLotId = nextLot?.id || "";
            nextLabel = nextLot?.sku || nextLot?.id || "";
            await refreshCurrentLotCard(apiBase, token, nextLot);
          }
        } catch (_err) {
          // Ignore "all completed" check here; upload already succeeded.
        }
        status.textContent = nextLabel
          ? `Saved + completed lot. Next lot: ${nextLabel}`
          : "Saved + completed lot.";
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

  document.getElementById("previewBtn").addEventListener("click", async () => {
    status.textContent = "Loading preview…";
    try {
      const creds = await readCreds(false);
      if (!creds) return;
      const { apiBase, token } = creds;
      const lot = await resolveCurrentLot(apiBase, token, { preferNextOpen: true });
      await refreshCurrentLotCard(apiBase, token, lot);
      const computedDescription = buildDescription(lot);
      const rawDescription =
        lot.description ||
        lot.auction_description ||
        lot.ai_description ||
        "";
      const shownDescription = computedDescription || rawDescription || "-";
      preview.textContent = [
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
      status.textContent = "Preview ready.";
      await appendActivity(`Previewed lot ${lot.sku || lot.id}`);
    } catch (err) {
      console.error(err);
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
      await refreshCurrentLotCard(apiBase, token, lot);
      status.textContent = `Selected previous lot: ${lot?.sku || lot?.id || "-"}`;
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
      await refreshCurrentLotCard(apiBase, token, lot);
      status.textContent = `Selected next lot: ${lot?.sku || lot?.id || "-"}`;
    } catch (err) {
      handleActionError(err);
    }
  });
});
