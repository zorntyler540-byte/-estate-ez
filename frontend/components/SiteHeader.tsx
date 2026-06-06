"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type SiteHeaderProps = {
  /** Right-side actions (e.g. Account, Log out). */
  actions: ReactNode;
};

/**
 * Sticky top bar: brand + Catalog / Appraiser nav; actions supplied by each page.
 */
export function SiteHeader({ actions }: SiteHeaderProps) {
  const pathname = usePathname() || "";

  return (
    <header className="ct-site-header">
      <div className="ct-site-header__row">
        <div className="ct-site-header__start">
          <Link href="/dashboard" className="ct-site-header__brand" aria-label="Caring Transitions — go to dashboard">
            <span className="ct-site-header__eyebrow">Caring Transitions</span>
            <span className="ct-site-header__tagline">Estate inventory</span>
          </Link>
          <nav className="ct-site-header__nav" aria-label="Main">
            <Link
              href="/dashboard"
              className={`ct-site-header__nav-link${pathname.startsWith("/dashboard") ? " ct-site-header__nav-link--active" : ""}`}
            >
              Catalog
            </Link>
            <Link
              href="/appraiser"
              className={`ct-site-header__nav-link${pathname.startsWith("/appraiser") ? " ct-site-header__nav-link--active" : ""}`}
            >
              Appraiser
            </Link>
          </nav>
        </div>
        <nav className="ct-site-header__actions" aria-label="Account navigation">
          {actions}
        </nav>
      </div>
    </header>
  );
}
