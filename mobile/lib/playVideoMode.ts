export function videoPlayMode(uri: string | null | undefined): "remote" | "local" | "none" {
  const raw = String(uri || "").trim();
  if (!raw) return "none";
  if (/^https?:\/\//i.test(raw)) return "remote";
  return "local";
}
