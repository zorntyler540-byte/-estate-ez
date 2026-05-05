"use client";

import type { ReactNode } from "react";
import Link from "next/link";

type SiteHeaderProps = {
  /** Right-side actions (e.g. Account, Log out). */
  actions: ReactNode;
};

/**
 * Sticky top bar: brand links to dashboard; actions supplied by each page.
 */
export function SiteHeader({ actions }: SiteHeaderProps) {
  return (
    <header className="ct-site-header">
      <div className="ct-site-header__row">
        <Link href="/dashboard" className="ct-site-header__brand" aria-label="Caring Transitions — go to dashboard">
          <span className="ct-site-header__eyebrow">Caring Transitions</span>
          <span className="ct-site-header__tagline">Estate inventory</span>
        </Link>
        <nav className="ct-site-header__actions" aria-label="Account navigation">
          {actions}
        </nav>
      </div>
    </header>
  );
}
