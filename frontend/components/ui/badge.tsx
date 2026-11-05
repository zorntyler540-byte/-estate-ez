import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Adapted from devetaigabbai/beautiful-simple-badges (21st.dev).
 * Semantic colors are the dark-theme counterparts of that demo, mapped onto
 * the Estate-EZ palette (warning amber, success green, error red, accent orange)
 * so light hex backgrounds are not used on #070708 / #141416.
 */
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-medium transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
        secondary: "border-transparent bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "border-transparent bg-destructive text-white [a&]:hover:bg-destructive/90 dark:bg-destructive/60",
        outline: "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        warning: "border-[rgba(245,158,11,0.45)] bg-[rgba(251,191,36,0.12)] text-[#fde68a]",
        info: "border-[rgba(255,77,26,0.4)] bg-[rgba(255,77,26,0.14)] text-[#ffb199]",
        neutral: "border-[#3f3f46] bg-[#1c1c1f] text-[#a8a29e]",
        positive: "border-[rgba(74,222,128,0.35)] bg-[rgba(74,222,128,0.1)] text-[#4ade80]",
        negative: "border-[rgba(248,113,113,0.35)] bg-[rgba(239,68,68,0.12)] text-[#fca5a5]",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "span";
  return <Comp data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
