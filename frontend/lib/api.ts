export const PRODUCTION_API_BASE = "https://caringtransitions-ai-production.up.railway.app";

export function isLocalApiBase(url: string): boolean {
  const raw = url.trim();
  if (!raw) return false;
  try {
    const host = new URL(raw).hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "::1";
  } catch {
    return /localhost|127\.0\.0\.1/i.test(raw);
  }
}

/**
 * Railway/Docker production must never bake localhost. An empty or localhost
 * ``NEXT_PUBLIC_API_BASE`` at image-build time used to ship a site that could
 * not reach the API from phones or other machines.
 */
export function resolveApiBase(fromEnv: string | undefined, nodeEnv: string | undefined): string {
  const raw = (fromEnv || "").trim().replace(/\/$/, "");
  const production = nodeEnv === "production";
  if (production) {
    if (!raw || isLocalApiBase(raw)) return PRODUCTION_API_BASE;
    return raw;
  }
  return raw || "http://localhost:8000";
}

const API_BASE = resolveApiBase(process.env.NEXT_PUBLIC_API_BASE, process.env.NODE_ENV);

/** Turn FastAPI / fetch failures into a short string for banners. */
export function parseApiErrorMessage(raw: string, fallback = "Request failed"): string {
  const t = raw.trim();
  if (!t) return fallback;
  try {
    const j = JSON.parse(t) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string" && d.trim()) return d.trim();
    if (Array.isArray(d)) {
      const parts = d
        .map((x) => {
          if (typeof x === "string") return x;
          if (x && typeof x === "object" && "msg" in x) return String((x as { msg: unknown }).msg);
          return "";
        })
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  } catch {
    /* not JSON */
  }
  return t.length > 280 ? `${t.slice(0, 277)}…` : t;
}

/** True when the built API URL points at localhost but the page is opened on a real host (common deploy mistake). */
export function isApiBaseMisconfiguredForHost(hostname: string): boolean {
  if (!hostname || hostname === "localhost" || hostname === "127.0.0.1") return false;
  try {
    const u = new URL(API_BASE);
    return u.hostname === "localhost" || u.hostname === "127.0.0.1";
  } catch {
    return false;
  }
}

/** Rough byte size of file parts only (multipart overhead is ignored). */
function estimateFormDataFileBytes(fd: FormData): number {
  let n = 0;
  for (const [, v] of fd.entries()) {
    if (v instanceof File) n += v.size;
  }
  return n;
}

function xhrPostFormJson(
  path: string,
  token: string,
  formData: FormData,
  timeoutMs: number,
  onUploadProgress: (ev: { loaded: number; total: number | null }) => void
): Promise<unknown> {
  const estimatedTotal = estimateFormDataFileBytes(formData);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${path}`);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.timeout = timeoutMs;
    xhr.upload.onloadstart = () => {
      onUploadProgress({ loaded: 0, total: estimatedTotal > 0 ? estimatedTotal : null });
    };
    xhr.upload.onprogress = (e) => {
      const total =
        e.lengthComputable && e.total > 0
          ? e.total
          : estimatedTotal > 0
            ? estimatedTotal
            : null;
      onUploadProgress({ loaded: e.loaded, total });
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const raw = xhr.responseText || "{}";
        try {
          resolve(JSON.parse(raw) as unknown);
        } catch {
          resolve(null);
        }
        return;
      }
      reject(new Error(xhr.responseText?.trim() || `API error ${xhr.status}`));
    };
    xhr.onerror = () =>
      reject(
        new Error(
          "Network error while uploading — check connection, VPN, or try fewer photos per batch."
        )
      );
    xhr.ontimeout = () =>
      reject(new Error("Upload timed out — try fewer photos at once or a wired connection."));
    xhr.send(formData);
  });
}

export async function apiUploadFiles(
  path: string,
  token: string,
  formData: FormData,
  /** Large mobile batches can be slow on cellular; abort avoids an endless spinner. */
  timeoutMs: number = 900_000,
  /**
   * When set, uses XMLHttpRequest so upload progress events fire (``fetch`` does not expose them).
   * Helps long one-shot multipart uploads feel responsive and surface network failures.
   */
  onUploadProgress?: (ev: { loaded: number; total: number | null }) => void
) {
  if (onUploadProgress && typeof XMLHttpRequest !== "undefined") {
    return xhrPostFormJson(path, token, formData, timeoutMs, onUploadProgress);
  }

  const ac = new AbortController();
  const tid = setTimeout(() => ac.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
      cache: "no-store",
      signal: ac.signal
    });
  } catch (e) {
    if (e instanceof Error && e.name === "AbortError") {
      throw new Error("Upload timed out — try fewer photos at once or a stronger signal.");
    }
    throw e;
  } finally {
    clearTimeout(tid);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `API error ${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

export async function apiFetch(path: string, token?: string, init?: RequestInit) {
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
  if (init?.body != null && typeof init.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
      cache: "no-store"
    });
  } catch (e) {
    const hint =
      typeof window !== "undefined" && isApiBaseMisconfiguredForHost(window.location.hostname)
        ? " This site is trying to reach a localhost API — redeploy the web app with NEXT_PUBLIC_API_BASE set to your public API URL."
        : " Check Wi‑Fi/cellular, VPN, and that the API service is up.";
    if (e instanceof TypeError) {
      throw new Error(`Cannot reach the server at ${API_BASE}.${hint}`);
    }
    throw e;
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(parseApiErrorMessage(text, `API error ${response.status}`));
  }
  if (response.status === 204) return null;
  const ct = response.headers.get("content-type") || "";
  if (ct.includes("application/json")) return response.json();
  return response.text();
}

export { API_BASE };
