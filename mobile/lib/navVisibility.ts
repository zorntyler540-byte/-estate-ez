/**
 * Tabs hidden from the tab bar (screens stay routable). Mirrors the web app's
 * ``frontend/lib/navVisibility.ts`` — remove a name here to show the tab again.
 */
export const HIDDEN_TAB_ROUTES = ["facebook"] as const;

export function isTabHidden(route: string): boolean {
  return (HIDDEN_TAB_ROUTES as readonly string[]).includes(route);
}

/** Badge text for a count, or ``undefined`` to hide the badge. */
export function formatTabBadge(count: number): string | undefined {
  const n = Math.floor(Number(count) || 0);
  if (n <= 0) return undefined;
  return n > 99 ? "99+" : String(n);
}

export function queueTabAccessibilityLabel(count: number): string {
  const n = Math.floor(Number(count) || 0);
  if (n <= 0) return "Queue";
  return `Queue, ${n} lot${n === 1 ? "" : "s"} not on the website yet`;
}

