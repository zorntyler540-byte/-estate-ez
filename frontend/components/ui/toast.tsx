"use client";

import { CircleAlert, Check, Info, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";

export type ToastTone = "success" | "error" | "info";

type ToastItem = {
  id: string;
  tone: ToastTone;
  message: string;
};

type ToastApi = {
  push: (message: string, tone?: ToastTone) => void;
  dismiss: (id: string) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const TOAST_TTL_MS = 4200;

const toneClass: Record<ToastTone, string> = {
  success: "border-[rgba(74,222,128,0.4)]",
  error: "border-[rgba(248,113,113,0.45)]",
  info: "border-[rgba(255,77,26,0.4)]",
};

const toneIcon: Record<ToastTone, typeof Check> = {
  success: Check,
  error: CircleAlert,
  info: Info,
};

const toneIconClass: Record<ToastTone, string> = {
  success: "text-[#4ade80]",
  error: "text-[#fca5a5]",
  info: "text-primary",
};

/**
 * Animated toast provider adapted from dev.shejanmahamud/animated-toast (21st.dev).
 * Success, error, and info tones use the Estate-EZ dark palette. Motion is skipped
 * when the visitor prefers reduced motion.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const reduceMotion = useReducedMotion();

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const text = message.trim();
      if (!text) return;
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setToasts((prev) => [...prev.slice(-3), { id, tone, message: text }]);
      window.setTimeout(() => dismiss(id), TOAST_TTL_MS);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      dismiss,
      success: (message) => push(message, "success"),
      error: (message) => push(message, "error"),
      info: (message) => push(message, "info"),
    }),
    [dismiss, push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2"
        aria-live="polite"
        aria-atomic="true"
      >
        <AnimatePresence initial={false}>
          {toasts.map((toast) => {
            const Icon = toneIcon[toast.tone];
            return (
              <motion.div
                key={toast.id}
                layout
                role="status"
                initial={reduceMotion ? false : { opacity: 0, y: 14, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.98 }}
                transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 32 }}
                className={cn(
                  "pointer-events-auto flex items-start gap-2.5 rounded-lg border bg-[var(--ct-bg-elevated)] px-3 py-2.5 text-sm text-foreground shadow-lg shadow-black/40",
                  toneClass[toast.tone],
                )}
              >
                <Icon size={16} aria-hidden className={cn("mt-0.5 shrink-0", toneIconClass[toast.tone])} />
                <p className="m-0 flex-1 leading-snug">{toast.message}</p>
                <button
                  type="button"
                  className="shrink-0 rounded-md p-0.5 text-muted-foreground hover:text-foreground"
                  aria-label="Dismiss notification"
                  onClick={() => dismiss(toast.id)}
                >
                  <X size={14} aria-hidden />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return api;
}
