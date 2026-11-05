import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Text input in the shadcn/21st.dev shape, on Estate-EZ tokens: surface #141416,
 * border-strong, text #f5f5f4, primary #ff4d1a focus ring. 16px text keeps iOS from zooming.
 */
export const inputClassName =
  "flex h-10 w-full min-w-0 rounded-lg border border-input bg-card px-3 py-2 font-sans text-base text-foreground shadow-sm shadow-black/20 transition-[border-color,box-shadow] placeholder:text-[var(--ct-text-subtle)] focus-visible:border-primary focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-primary/35 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-[rgba(248,113,113,0.6)]";

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type = "text", ...props }, ref) => (
    <input ref={ref} type={type} data-slot="input" className={cn(inputClassName, className)} {...props} />
  ),
);
Input.displayName = "Input";

function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn("flex flex-col gap-1.5 text-sm font-medium text-muted-foreground", className)}
      {...props}
    />
  );
}

export { Input, Label };
export default Input;
