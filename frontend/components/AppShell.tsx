"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Box, ClipboardList, LayoutDashboard, Settings } from "lucide-react";

type AppShellProps = {
  children: ReactNode;
  /** Center-top slot (e.g. sale selector on dashboard). */
  topBar?: ReactNode;
  /** Right side of top bar (Account, Log out). */
  actions: ReactNode;
};

const NAV = [
  { href: "/dashboard", label: "Sales dashboard", icon: LayoutDashboard, match: (p: string) => p.startsWith("/dashboard") },
  { href: "/appraiser", label: "Appraiser", icon: ClipboardList, match: (p: string) => p.startsWith("/appraiser") },
  { href: "/account", label: "Settings", icon: Settings, match: (p: string) => p.startsWith("/account") }
] as const;

export function AppShell({ children, topBar, actions }: AppShellProps) {
  const pathname = usePathname() || "";

  return (
    <div className="ct-app">
      <aside className="ct-app-sidebar" aria-label="Main navigation">
        <Link href="/dashboard" className="ct-app-sidebar__brand">
          <span className="ct-app-sidebar__logo" aria-hidden>
            <Box size={22} strokeWidth={1.75} />
          </span>
          <span className="ct-app-sidebar__brand-text">
            <span className="ct-app-sidebar__eyebrow">Estate</span>
            <span className="ct-app-sidebar__title">Inventory</span>
          </span>
        </Link>
        <nav className="ct-app-sidebar__nav">
          {NAV.map(({ href, label, icon: Icon, match }) => {
            const active = match(pathname) || (href === "/dashboard" && pathname.startsWith("/lots/"));
            return (
              <Link
                key={href}
                href={href}
                className={`ct-app-sidebar__link${active ? " ct-app-sidebar__link--active" : ""}`}
              >
                <Icon size={18} aria-hidden className="ct-app-sidebar__link-icon" />
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="ct-app-main">
        <header className="ct-app-topbar">
          <div className="ct-app-topbar__start">{topBar}</div>
          <nav className="ct-app-topbar__actions" aria-label="Account navigation">
            {actions}
          </nav>
        </header>
        <div className="ct-app-content">{children}</div>
      </div>
    </div>
  );
}
