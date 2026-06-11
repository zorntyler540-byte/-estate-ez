"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Box, ClipboardList, LayoutDashboard, Menu, Settings, X } from "lucide-react";

type AppShellProps = {
  children: ReactNode;
  /** Center-top slot (e.g. sale selector on dashboard). */
  topBar?: ReactNode;
  /** Right side of top bar (Account, Log out). */
  actions: ReactNode;
};

const NAV = [
  {
    href: "/dashboard",
    label: "Sales dashboard",
    shortLabel: "Dashboard",
    icon: LayoutDashboard,
    match: (p: string) => p.startsWith("/dashboard")
  },
  {
    href: "/appraiser",
    label: "Appraiser",
    shortLabel: "Appraiser",
    icon: ClipboardList,
    match: (p: string) => p.startsWith("/appraiser")
  },
  {
    href: "/account",
    label: "Settings",
    shortLabel: "Settings",
    icon: Settings,
    match: (p: string) => p.startsWith("/account")
  }
] as const;

function isNavActive(pathname: string, href: string, match: (p: string) => boolean): boolean {
  return match(pathname) || (href === "/dashboard" && pathname.startsWith("/lots/"));
}

function BrandMark() {
  return (
    <Link href="/dashboard" className="ct-app-sidebar__brand">
      <span className="ct-app-sidebar__logo" aria-hidden>
        <Box size={22} strokeWidth={1.75} />
      </span>
      <span className="ct-app-sidebar__brand-text">
        <span className="ct-app-sidebar__eyebrow">Estate</span>
        <span className="ct-app-sidebar__title">Inventory</span>
      </span>
    </Link>
  );
}

type ShellNavLinksProps = {
  pathname: string;
  onNavigate?: () => void;
  linkClass?: string;
};

function ShellNavLinks({ pathname, onNavigate, linkClass = "ct-app-sidebar__link" }: ShellNavLinksProps) {
  return (
    <>
      {NAV.map(({ href, label, icon: Icon, match }) => {
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
    </>
  );
}

export function AppShell({ children, topBar, actions }: AppShellProps) {
  const pathname = usePathname() || "";
  const [navOpen, setNavOpen] = useState(false);

  const closeNav = useCallback(() => setNavOpen(false), []);

  useEffect(() => {
    closeNav();
  }, [pathname, closeNav]);

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
    <div className={`ct-app${navOpen ? " ct-app--nav-open" : ""}`}>
      <aside className="ct-app-sidebar" aria-label="Main navigation">
        <BrandMark />
        <nav className="ct-app-sidebar__nav">
          <ShellNavLinks pathname={pathname} />
        </nav>
      </aside>

      <div className="ct-app-main">
        <header className={`ct-app-topbar${topBar ? " ct-app-topbar--has-center" : ""}`}>
          <button
            type="button"
            className="ct-app-menu-btn"
            aria-expanded={navOpen}
            aria-controls="ct-app-nav-drawer"
            onClick={() => setNavOpen((v) => !v)}
          >
            {navOpen ? <X size={22} aria-hidden /> : <Menu size={22} aria-hidden />}
            <span className="ct-app-menu-btn__label">{navOpen ? "Close menu" : "Open menu"}</span>
          </button>
          <div className="ct-app-topbar__start">
            {topBar ?? (
              <div className="ct-app-topbar__mobile-brand" aria-hidden>
                <BrandMark />
              </div>
            )}
          </div>
          <nav className="ct-app-topbar__actions" aria-label="Account navigation">
            {actions}
          </nav>
        </header>

        <div className="ct-app-content">{children}</div>

        <nav className="ct-app-bottom-nav" aria-label="Quick navigation">
          {NAV.map(({ href, shortLabel, icon: Icon, match }) => {
            const active = isNavActive(pathname, href, match);
            return (
              <Link
                key={href}
                href={href}
                className={`ct-app-bottom-nav__link${active ? " ct-app-bottom-nav__link--active" : ""}`}
              >
                <Icon size={20} aria-hidden className="ct-app-bottom-nav__icon" />
                <span>{shortLabel}</span>
              </Link>
            );
          })}
        </nav>
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
              <BrandMark />
              <button type="button" className="ct-app-nav-drawer__close" onClick={closeNav} aria-label="Close menu">
                <X size={20} aria-hidden />
              </button>
            </div>
            <nav className="ct-app-nav-drawer__nav">
              <ShellNavLinks pathname={pathname} onNavigate={closeNav} linkClass="ct-app-nav-drawer__link" />
            </nav>
          </aside>
        </>
      ) : null}
    </div>
  );
}
