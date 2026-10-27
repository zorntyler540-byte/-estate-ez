"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Activity, Briefcase, ClipboardList, LayoutDashboard, Megaphone, Menu, Monitor, Settings, Store, X } from "lucide-react";
import { apiFetch } from "../lib/api";
import {
  isWatcherPlan,
  rememberAccountAccess,
  storedAccountAccess,
  watcherHome,
  watcherPathBlocked,
  type AccountAccess
} from "../lib/plans";
import { isClientRole, rememberAccountRole } from "../lib/roles";

type AppShellProps = {
  children: ReactNode;
  /** Center-top slot (e.g. sale selector on dashboard). */
  topBar?: ReactNode;
  /** Right side of top bar (Account, Log out). */
  actions: ReactNode;
  /** Fixed bottom bar on mobile (e.g. dashboard upload shortcuts). */
  mobileBottomBar?: ReactNode;
};

const NAV_GROUPS = [
  {
    label: "Catalog",
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: LayoutDashboard,
        match: (p: string) => p.startsWith("/dashboard")
      },
      {
        href: "/facebook",
        label: "Facebook",
        icon: Store,
        match: (p: string) => p.startsWith("/facebook")
      },
      {
        href: "/engagement",
        label: "Push queue",
        icon: Megaphone,
        match: (p: string) => p.startsWith("/engagement")
      }
    ]
  },
  {
    label: "Business",
    items: [
      {
        href: "/operations",
        label: "Operations",
        icon: Briefcase,
        match: (p: string) => p.startsWith("/operations")
      }
    ]
  },
  {
    label: "More",
    items: [
      {
        href: "/appraiser",
        label: "Appraiser",
        icon: ClipboardList,
        match: (p: string) => p.startsWith("/appraiser")
      },
      {
        href: "/tools",
        label: "Tools",
        icon: Monitor,
        match: (p: string) => p.startsWith("/tools")
      },
      {
        href: "/activity",
        label: "Team",
        icon: Activity,
        match: (p: string) => p.startsWith("/activity")
      },
      {
        href: "/account",
        label: "Settings",
        icon: Settings,
        match: (p: string) => p.startsWith("/account")
      }
    ]
  }
] as const;

function isNavActive(pathname: string, href: string, match: (p: string) => boolean): boolean {
  return match(pathname) || (href === "/dashboard" && pathname.startsWith("/lots/"));
}

function BrandMark({ href }: { href: string }) {
  return (
    <Link href={href} className="ct-app-sidebar__brand">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        className="ct-app-sidebar__logo"
        src="/zortron-logo.jpg"
        alt="Zortron Tech Solutions"
        width={56}
        height={56}
      />
      <span className="ct-app-sidebar__brand-text">
        <span className="ct-app-sidebar__title">Estate-EZ</span>
        <span className="ct-app-sidebar__eyebrow">Zortron</span>
      </span>
    </Link>
  );
}

type NavGroup = {
  label: string;
  items: ReadonlyArray<{
    href: string;
    label: string;
    icon: typeof LayoutDashboard;
    match: (p: string) => boolean;
  }>;
};

function navGroupsFor(access: AccountAccess | null): readonly NavGroup[] {
  if (!access || !isWatcherPlan(access.plan_code)) return NAV_GROUPS;
  const items: NavGroup["items"][number][] = [];
  if (access.feature_analytics !== false) {
    items.push({
      href: "/engagement",
      label: "Push queue",
      icon: Megaphone,
      match: (p: string) => p.startsWith("/engagement")
    });
  }
  items.push({
    href: "/account",
    label: "Settings",
    icon: Settings,
    match: (p: string) => p.startsWith("/account")
  });
  return [{ label: "Auction Watcher", items }];
}

type ShellNavLinksProps = {
  pathname: string;
  groups: readonly NavGroup[];
  onNavigate?: () => void;
  linkClass?: string;
};

function ShellNavLinks({ pathname, groups, onNavigate, linkClass = "ct-app-sidebar__link" }: ShellNavLinksProps) {
  return (
    <>
      {groups.map((group) => (
        <div key={group.label} className="ct-app-nav-group">
          <p className="ct-app-nav-group__label">{group.label}</p>
          {group.items.map(({ href, label, icon: Icon, match }) => {
            const active = isNavActive(pathname, href, match);
            return (
              <Link
                key={href}
                href={href}
                className={`${linkClass}${active ? ` ${linkClass}--active` : ""}`}
                onClick={onNavigate}
              >
                <Icon size={18} aria-hidden className="ct-app-sidebar__link-icon" />
                {label}
              </Link>
            );
          })}
        </div>
      ))}
    </>
  );
}

export function AppShell({ children, topBar, actions, mobileBottomBar }: AppShellProps) {
  const pathname = usePathname() || "";
  const router = useRouter();
  const [navOpen, setNavOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [access, setAccess] = useState<AccountAccess | null>(null);
  const groups = navGroupsFor(access);
  const homeHref = access && isWatcherPlan(access.plan_code) ? watcherHome(access) : "/dashboard";

  const closeNav = useCallback(() => setNavOpen(false), []);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1280px)");
    const sync = () => setMobileNav(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    closeNav();
  }, [pathname, closeNav]);

  useEffect(() => {
    setAccess(storedAccountAccess());
  }, []);

  useEffect(() => {
    if (pathname.startsWith("/client")) return;
    const token = localStorage.getItem("token") || "";
    if (!token) return;
    let cancelled = false;
    void apiFetch("/auth/me", token)
      .then((data) => {
        const me = data as AccountAccess;
        const role = String(me.role || "");
        rememberAccountRole(role);
        rememberAccountAccess(me);
        if (cancelled) return;
        setAccess(me);
        if (isClientRole(role)) {
          router.replace("/client");
          return;
        }
        if (watcherPathBlocked(pathname, me)) router.replace(watcherHome(me));
      })
      .catch(() => {
        /* leave the page; the screen handles auth errors */
      });
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  useEffect(() => {
    if (!navOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeNav();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navOpen, closeNav]);

  useEffect(() => {
    if (!navOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [navOpen]);

  return (
    <div
      className={`ct-app${navOpen ? " ct-app--nav-open" : ""}${mobileNav ? " ct-app--mobile-nav" : ""}${mobileBottomBar ? " ct-app--has-mobile-bar" : ""}`}
    >
      <aside className="ct-app-sidebar" aria-label="Main navigation">
        <BrandMark href={homeHref} />
        <nav className="ct-app-sidebar__nav">
          <ShellNavLinks pathname={pathname} groups={groups} />
        </nav>
      </aside>

      <div className="ct-app-main">
        <header
          className={`ct-app-topbar${topBar ? " ct-app-topbar--has-center" : ""}${mobileNav ? " ct-app-topbar--mobile" : ""}`}
        >
          <div className="ct-app-topbar__menu">
            <button
              type="button"
              className="ct-app-menu-btn"
              aria-expanded={navOpen}
              aria-controls="ct-app-nav-drawer"
              onClick={() => setNavOpen((v) => !v)}
            >
              {navOpen ? <X size={22} aria-hidden /> : <Menu size={22} aria-hidden />}
              <span className="ct-app-menu-btn__text">Menu</span>
              <span className="ct-app-menu-btn__label">{navOpen ? "Close menu" : "Open menu"}</span>
            </button>
          </div>
          <div className="ct-app-topbar__start">
            {topBar ?? (
              <div className="ct-app-topbar__mobile-brand" aria-hidden>
                <BrandMark href={homeHref} />
              </div>
            )}
          </div>
          <nav className="ct-app-topbar__actions" aria-label="Account navigation">
            {actions}
          </nav>
        </header>

        <div className="ct-app-content">{children}</div>

        {mobileBottomBar ? (
          <div className="ct-app-mobile-bar" aria-label="Quick actions">
            {mobileBottomBar}
          </div>
        ) : null}
      </div>

      {navOpen ? (
        <>
          <button
            type="button"
            className="ct-app-nav-backdrop"
            aria-label="Close navigation menu"
            onClick={closeNav}
          />
          <aside
            id="ct-app-nav-drawer"
            className="ct-app-nav-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Main navigation"
          >
            <div className="ct-app-nav-drawer__head">
              <BrandMark href={homeHref} />
              <button type="button" className="ct-app-nav-drawer__close" onClick={closeNav} aria-label="Close menu">
                <X size={20} aria-hidden />
              </button>
            </div>
            <nav className="ct-app-nav-drawer__nav">
              <ShellNavLinks
                pathname={pathname}
                groups={groups}
                onNavigate={closeNav}
                linkClass="ct-app-nav-drawer__link"
              />
            </nav>
          </aside>
        </>
      ) : null}
    </div>
  );
}
