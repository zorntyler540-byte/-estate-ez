"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AppShell } from "../AppShell";

const LINKS = [
  { href: "/operations", label: "Home", match: (p: string) => p === "/operations" },
  { href: "/operations/jobs", label: "Jobs", match: (p: string) => p.startsWith("/operations/jobs") },
  { href: "/operations/month", label: "Month", match: (p: string) => p.startsWith("/operations/month") },
  { href: "/operations/settings", label: "Rates", match: (p: string) => p.startsWith("/operations/settings") }
];

export function OperationsShell({
  children,
  actions
}: {
  children: ReactNode;
  actions: ReactNode;
}) {
  const pathname = usePathname() || "";
  return (
    <AppShell actions={actions}>
      <nav className="ct-ops-subnav" aria-label="Operations">
        {LINKS.map((link) => {
          const active = link.match(pathname);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`ct-ops-subnav__link${active ? " ct-ops-subnav__link--active" : ""}`}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </AppShell>
  );
}

export function OperationsSignInPrompt() {
  return (
    <main className="ct-shell">
      <div className="ct-panel">
        <p style={{ margin: 0 }}>Please sign in first.</p>
        <p style={{ margin: "1rem 0 0" }}>
          <Link href="/">Go to sign in</Link>
        </p>
      </div>
    </main>
  );
}

export function operationsActions(logout: () => void) {
  return (
    <>
      <Link href="/account" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
        Account
      </Link>
      <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
        Log out
      </button>
    </>
  );
}
