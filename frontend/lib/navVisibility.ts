/**
 * App shell / menu entries hidden from navigation (pages and APIs stay live).
 * Remove an href from this list to show it again in the sidebar and related menus.
 */
export const HIDDEN_NAV_HREFS = ["/facebook", "/appraiser"] as const;

export function isNavRouteHidden(href: string): boolean {
  return (HIDDEN_NAV_HREFS as readonly string[]).includes(href);
}
