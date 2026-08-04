/** Estate-EZ palette — mirrors frontend/app/globals.css tokens. */
export const theme = {
  bg: "#070708",
  bgElevated: "#0e0e10",
  surface: "#141416",
  text: "#f5f5f4",
  textMuted: "#a8a29e",
  textSubtle: "#78716c",
  border: "#27272a",
  borderStrong: "#3f3f46",
  primary: "#ff4d1a",
  primaryHover: "#ff7347",
  primaryMuted: "rgba(255, 77, 26, 0.14)",
  onPrimary: "#140500",
  success: "#4ade80",
  warning: "#fbbf24",
  warningText: "#451a03",
  error: "#fca5a5",
  errorBg: "#450a0a",
  errorBtn: "#b91c1c",
  black: "#000000",
  overlay: "rgba(14, 14, 16, 0.78)",
  overlayHeavy: "rgba(7, 7, 8, 0.92)"
} as const;

export type Theme = typeof theme;
