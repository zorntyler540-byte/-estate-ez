"use client";

import { Sparkles, X } from "lucide-react";
import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject
} from "react";
import { apiFetch } from "../lib/api";
import { isClientRole, storedAccountRole } from "../lib/roles";
import {
  formatReleaseNoteDate,
  markReleaseNoteSeen,
  parseReleaseNoteLatest,
  readSeenReleaseNoteId,
  releaseNoteToShow,
  type ReleaseNote
} from "../lib/releaseNotes";
import { Button } from "./ui/button";

type LoadStatus = "idle" | "loading" | "ready" | "error";

type WhatsNewContextValue = {
  openWhatsNew: () => void;
  setInShell: (value: boolean) => void;
};

const WhatsNewContext = createContext<WhatsNewContextValue | null>(null);

const fallback: WhatsNewContextValue = {
  openWhatsNew: () => {},
  setInShell: () => {}
};

function isAuthScreen(pathname: string): boolean {
  return pathname === "/" || pathname === "/client-invite" || pathname.startsWith("/client-invite/");
}

export function useWhatsNew(): WhatsNewContextValue {
  return useContext(WhatsNewContext) ?? fallback;
}

/** AppShell calls this so the dialog opens on signed-in pages and not on the login screen. */
export function useRegisterWhatsNewShell(): void {
  const { setInShell } = useWhatsNew();
  useEffect(() => {
    setInShell(true);
    return () => setInShell(false);
  }, [setInShell]);
}

export function WhatsNewNavButton({ className, onOpen }: { className: string; onOpen?: () => void }) {
  const { openWhatsNew } = useWhatsNew();
  return (
    <button
      type="button"
      className={`${className} ct-whats-new-nav`}
      onClick={() => {
        onOpen?.();
        openWhatsNew();
      }}
    >
      <Sparkles size={18} aria-hidden className="ct-app-sidebar__link-icon" />
      What's new
    </button>
  );
}

export function WhatsNewProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "";
  const [inShell, setInShellState] = useState(false);
  const [note, setNote] = useState<ReleaseNote | null>(null);
  const [status, setStatus] = useState<LoadStatus>("idle");
  const [open, setOpen] = useState(false);
  const loadStarted = useRef(false);
  const decided = useRef(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    const token = typeof window === "undefined" ? "" : localStorage.getItem("token") || "";
    if (!token) {
      setStatus("idle");
      return;
    }
    setStatus("loading");
    try {
      const data = await apiFetch("/release-notes/latest?platform=web", token);
      setNote(parseReleaseNoteLatest(data));
      setStatus("ready");
    } catch {
      setStatus("error");
    }
  }, []);

  const setInShell = useCallback((value: boolean) => {
    setInShellState(value);
  }, []);

  const openWhatsNew = useCallback(() => {
    setOpen(true);
    if (!loadStarted.current) {
      loadStarted.current = true;
      void load();
      return;
    }
    if (status === "idle" || status === "error") void load();
  }, [load, status]);

  const dismiss = useCallback(() => {
    setOpen(false);
    decided.current = true;
    if (note?.id) markReleaseNoteSeen(note.id);
  }, [note]);

  useEffect(() => {
    if (!inShell) return;
    const token = typeof window === "undefined" ? "" : localStorage.getItem("token") || "";
    if (!token || loadStarted.current) return;
    loadStarted.current = true;
    void load();
  }, [inShell, load, pathname]);

  useEffect(() => {
    if (!inShell || status !== "ready" || decided.current) return;
    decided.current = true;
    if (isClientRole(storedAccountRole())) return;
    if (releaseNoteToShow(readSeenReleaseNoteId(), note)) setOpen(true);
  }, [inShell, status, note]);

  const visible = open && !isAuthScreen(pathname);

  useEffect(() => {
    if (!visible) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [visible, dismiss]);

  useEffect(() => {
    if (!visible) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [visible]);

  useEffect(() => {
    if (visible) closeRef.current?.focus();
  }, [visible, status]);

  const value = useMemo(() => ({ openWhatsNew, setInShell }), [openWhatsNew, setInShell]);

  return (
    <WhatsNewContext.Provider value={value}>
      {children}
      {visible ? (
        <WhatsNewDialog closeRef={closeRef} note={note} status={status} onDismiss={dismiss} />
      ) : null}
    </WhatsNewContext.Provider>
  );
}

function WhatsNewDialog({
  note,
  status,
  onDismiss,
  closeRef
}: {
  note: ReleaseNote | null;
  status: LoadStatus;
  onDismiss: () => void;
  closeRef: RefObject<HTMLButtonElement | null>;
}) {
  const heading = note?.title || "What's new";
  return (
    <div className="ct-whats-new" onClick={onDismiss}>
      <div
        className="ct-whats-new__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ct-whats-new-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="ct-whats-new__head">
          <div>
            {note ? <p className="ct-whats-new__kicker">What's new</p> : null}
            <h2 id="ct-whats-new-title">{heading}</h2>
            {note?.date ? <p className="ct-whats-new__date">{formatReleaseNoteDate(note.date)}</p> : null}
          </div>
          <button type="button" className="ct-whats-new__close" onClick={onDismiss} aria-label="Close">
            <X size={18} aria-hidden />
          </button>
        </div>
        <WhatsNewBody note={note} status={status} />
        <div className="ct-whats-new__actions">
          <Button ref={closeRef} type="button" onClick={onDismiss}>
            Got it
          </Button>
        </div>
      </div>
    </div>
  );
}

function WhatsNewBody({ note, status }: { note: ReleaseNote | null; status: LoadStatus }) {
  if ((status === "loading" || status === "idle") && !note) {
    return <p className="ct-whats-new__status">Loading…</p>;
  }
  if (status === "error" && !note) {
    return (
      <p className="ct-whats-new__status">Couldn't load what's new right now. Check your connection and try again.</p>
    );
  }
  if (!note) {
    return <p className="ct-whats-new__status">You're up to date.</p>;
  }
  return (
    <ul className="ct-whats-new__items">
      {note.items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}
