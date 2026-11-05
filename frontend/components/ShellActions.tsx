"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";

type ShellLink = {
  href: string;
  label: string;
};

const DEFAULT_LINKS: ShellLink[] = [{ href: "/account", label: "Account" }];

/** Shared top-bar actions. Ghost buttons use the originui button primitive. */
export function ShellActions({
  onLogout,
  links = DEFAULT_LINKS,
}: {
  onLogout: () => void;
  links?: ShellLink[];
}) {
  return (
    <>
      {links.map((link) => (
        <Button key={`${link.href}-${link.label}`} variant="ghost" size="sm" className="text-sm font-semibold" asChild>
          <Link href={link.href}>{link.label}</Link>
        </Button>
      ))}
      <Button variant="ghost" size="sm" className="text-sm font-semibold" type="button" onClick={onLogout}>
        Log out
      </Button>
    </>
  );
}
