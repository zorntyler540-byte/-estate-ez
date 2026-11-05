import { ChevronDown } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";
import { inputClassName } from "./input";

/**
 * Native select styled like ``Input`` (keyboard, screen reader, and mobile pickers for free).
 * Options use the elevated surface so the open list is dark on every browser that honors it.
 */
const Select = React.forwardRef<HTMLSelectElement, React.ComponentProps<"select">>(
  ({ className, children, ...props }, ref) => (
    <div data-slot="select" className={cn("relative w-full", className)}>
      <select
        ref={ref}
        className={cn(
          inputClassName,
          "cursor-pointer appearance-none pr-9 [&>option]:bg-[var(--ct-bg-elevated)] [&>option]:text-foreground",
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        size={16}
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  ),
);
Select.displayName = "Select";

export { Select };
export default Select;
