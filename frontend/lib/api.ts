const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
    cache: "no-store"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `API error ${response.status}`);
  }
  if (response.status === 204) return null;
  const ct = response.headers.get("content-type") || "";
  if (ct.includes("application/json")) return response.json();
  return response.text();
}

export { API_BASE };
