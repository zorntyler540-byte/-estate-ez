/**
 * Source Sans 3 (matches the web app). Each weight is its own loaded family, so a style's
 * ``fontWeight`` picks the face and is then dropped — Android would otherwise fake-bold
 * a custom typeface on top of the real bold file.
 */
export const FONT_FAMILY = {
  regular: "SourceSans3_400Regular",
  semibold: "SourceSans3_600SemiBold",
  bold: "SourceSans3_700Bold",
  extrabold: "SourceSans3_800ExtraBold"
} as const;

export function fontFamilyForWeight(weight: string | number | null | undefined): string {
  if (weight == null || weight === "normal") return FONT_FAMILY.regular;
  if (weight === "bold") return FONT_FAMILY.bold;
  const n = Number(weight);
  if (!Number.isFinite(n) || n < 600) return FONT_FAMILY.regular;
  if (n < 700) return FONT_FAMILY.semibold;
  if (n < 800) return FONT_FAMILY.bold;
  return FONT_FAMILY.extrabold;
}

/** Flattened text style with the app font applied. An explicit ``fontFamily`` wins. */
export function withAppFont(
  style: object | null | undefined
): Record<string, unknown> & { fontFamily: string } {
  const base = (style || {}) as Record<string, unknown>;
  if (typeof base.fontFamily === "string" && base.fontFamily) {
    return base as Record<string, unknown> & { fontFamily: string };
  }
  const { fontWeight, ...rest } = base;
  return { ...rest, fontFamily: fontFamilyForWeight(fontWeight as string | number | undefined) };
}
