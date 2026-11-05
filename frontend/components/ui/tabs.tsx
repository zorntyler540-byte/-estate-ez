"use client";

import { cn } from "@/lib/utils";

type SegmentedTab<T extends string> = {
  id: T;
  label: string;
};

/**
 * Segmented control for high-traffic tab bars. Active tab uses the Estate-EZ
 * primary accent. The selected tab stays disabled so choosing it again is a no-op,
 * matching the previous dashboard and sign-in controls.
 */
export function SegmentedTabs<T extends string>({
  label,
  active,
  tabs,
  onSelect,
  className,
}: {
  label: string;
  active: T;
  tabs: Array<SegmentedTab<T>>;
  onSelect: (id: T) => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-lg border border-border bg-[var(--ct-bg-elevated)] p-1",
        className,
      )}
      role="tablist"
      aria-label={label}
    >
      {tabs.map((tab) => {
        const selected = active === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={selected}
            onClick={() => onSelect(tab.id)}
            className={cn(
              "min-h-9 cursor-pointer whitespace-nowrap rounded-md px-3.5 py-1.5 font-sans text-[0.88rem] font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-default",
              selected
                ? "bg-primary text-primary-foreground shadow-sm shadow-black/30 disabled:opacity-100"
                : "bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
