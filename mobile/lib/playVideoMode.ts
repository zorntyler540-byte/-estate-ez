export function videoPlayMode(uri: string | null | undefined): "remote" | "local" | "none" {
  const raw = String(uri || "").trim();
  if (!raw) return "none";
  if (/^https?:\/\//i.test(raw)) return "remote";
  return "local";
}

/** iOS blocks Linking.openURL(file://). Share sheet previews the clip. */
export function localVideoPlayMethod(platform: string): "share" | "content-uri" {
  return platform === "ios" ? "share" : "content-uri";
}
