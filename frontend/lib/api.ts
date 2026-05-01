const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export async function apiUploadFiles(
  path: string,
  token: string,
  formData: FormData,
  /** Large mobile batches can be slow on cellular; abort avoids an endless spinner. */
  timeoutMs: number = 900_000
) {
  const ac = new AbortController();
  const tid = setTimeout(() => ac.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
      cache: "no-store",
      signal: ac.signal,
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
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  if (init?.body != null && typeof init.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
    cache: "no-store",
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

