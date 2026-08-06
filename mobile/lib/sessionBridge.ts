/** Lets ``apiFetch`` request a silent credential refresh without importing React context. */

type SessionRefresher = () => Promise<string | null>;

let refresher: SessionRefresher | null = null;

export function registerSessionRefresher(fn: SessionRefresher | null): void {
  refresher = fn;
}

export async function refreshSessionIfPossible(): Promise<string | null> {
  if (!refresher) return null;
  try {
    return await refresher();
  } catch {
    return null;
  }
}
