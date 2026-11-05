import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  className,
}: {
  label: string;
  value: ReactNode;
  className?: string;
}) {
  return (
    <article className={cn("flex flex-col gap-1 rounded-lg border border-border bg-[var(--ct-bg-elevated)] px-3 py-2.5", className)}>
      <p className="m-0 text-[1.35rem] font-bold leading-none tracking-tight text-primary">{value}</p>
      <h3 className="m-0 text-[0.76rem] font-medium uppercase tracking-[0.06em] text-muted-foreground">{label}</h3>
    </article>
  );
}
