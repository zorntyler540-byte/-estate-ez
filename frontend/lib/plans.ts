export const PLAN_FULL = "beta_free";
export const PLAN_WATCHER = "watcher";
export const ACCOUNT_ACCESS_STORAGE_KEY = "ct_account_access";

export type AccountAccess = {
  role?: string;
  plan_code?: string;
  feature_analytics?: boolean;
  feature_facebook?: boolean;
};

export function normalizePlan(plan: string | null | undefined): string {
  return (plan || "").trim().toLowerCase();
}

export function isWatcherPlan(plan: string | null | undefined): boolean {
  return normalizePlan(plan) === PLAN_WATCHER;
}

export function planLabel(plan: string | null | undefined): string {
  const code = normalizePlan(plan);
  if (code === PLAN_WATCHER) return "Auction Watcher";
  if (code === PLAN_FULL) return "Full catalog";
  return plan || "Full catalog";
}

/** Where a watcher login should land. Full catalog stays on the dashboard. */
export function watcherHome(access: AccountAccess | null | undefined): string {
  if (!access || !isWatcherPlan(access.plan_code)) return "/dashboard";
  if (access.feature_analytics !== false) return "/engagement";
  return "/account";
}

export function rememberAccountAccess(access: AccountAccess | null | undefined): void {
  try {
    if (!access) {
      localStorage.removeItem(ACCOUNT_ACCESS_STORAGE_KEY);
      return;
    }
    localStorage.setItem(
      ACCOUNT_ACCESS_STORAGE_KEY,
      JSON.stringify({
        plan_code: normalizePlan(access.plan_code) || PLAN_FULL,
        feature_analytics: access.feature_analytics !== false,
        feature_facebook: access.feature_facebook !== false
      })
    );
  } catch {
    /* private mode */
  }
}

export function storedAccountAccess(): AccountAccess | null {
  try {
    const raw = localStorage.getItem(ACCOUNT_ACCESS_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AccountAccess;
    if (!parsed || typeof parsed !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}

const WATCHER_BLOCKED_PREFIXES = [
  "/dashboard",
  "/lots",
  "/operations",
  "/appraiser",
  "/tools",
  "/sales",
  "/activity",
  "/facebook",
  "/client"
];

export function watcherPathBlocked(pathname: string, access: AccountAccess | null | undefined): boolean {
  if (!access || !isWatcherPlan(access.plan_code)) return false;
  const path = pathname || "/";
  if (WATCHER_BLOCKED_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) {
    return true;
  }
  if ((path === "/engagement" || path.startsWith("/engagement/")) && access.feature_analytics === false) {
    return true;
  }
  return false;
}
