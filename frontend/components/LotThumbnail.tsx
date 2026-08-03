"use client";

import type { CSSProperties } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";

type Props = {
  lotId: string;
  token: string;
  hasThumbnail: boolean;
  size?: number;
  maxPx?: number;
};

type MediaUrlOut = { url: string; expires_in?: number; kind?: string };

/** Caps parallel thumbnail GETs so the API is not hit with dozens of Pillow decodes at once. */
let thumbSlots = 0;
const THUMB_MAX_PARALLEL = 5;
const thumbWaitQueue: Array<() => void> = [];

function acquireThumbSlot(): Promise<void> {
  if (thumbSlots < THUMB_MAX_PARALLEL) {
    thumbSlots += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    thumbWaitQueue.push(() => {
      thumbSlots += 1;
      resolve();
    });
  });
}

function releaseThumbSlot(): void {
  thumbSlots -= 1;
  const next = thumbWaitQueue.shift();
  if (next) next();
}

/**
 * Prefer signed R2 cover URL; fall back to authenticated ``/thumbnail`` blob.
 */
export function LotThumbnail({ lotId, token, hasThumbnail, size = 56, maxPx = 220 }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [inView, setInView] = useState(false);
  const urlRef = useRef<string | null>(null);
  const holderRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!hasThumbnail || !token) {
      setInView(false);
      return;
    }
    const el = holderRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setInView(true);
      },
      { root: null, rootMargin: "180px", threshold: 0.01 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasThumbnail, token, lotId]);

  useEffect(() => {
    if (!hasThumbnail || !token || !inView) {
      if (!hasThumbnail || !token) setSrc(null);
      setFailed(false);
      return;
    }

    let cancelled = false;
    const ac = new AbortController();

    void (async () => {
      setFailed(false);
      setSrc(null);
      if (urlRef.current?.startsWith("blob:")) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      await acquireThumbSlot();
      if (cancelled) {
        releaseThumbSlot();
        return;
      }
      try {
        const mediaRes = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/media-url?max_px=${maxPx}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: ac.signal
          }
        );
        if (mediaRes.ok) {
          const data = (await mediaRes.json()) as MediaUrlOut;
          if (!cancelled && data?.url) {
            urlRef.current = null;
            setSrc(data.url);
            return;
          }
        }

        const res = await fetch(`${API_BASE}/lots/${lotId}/thumbnail?max_px=${maxPx}`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: ac.signal
        });
        if (!res.ok || cancelled) {
          if (!cancelled) setFailed(true);
          return;
        }
        const blob = await res.blob();
        if (cancelled) return;
        if (urlRef.current?.startsWith("blob:")) URL.revokeObjectURL(urlRef.current);
        const u = URL.createObjectURL(blob);
        urlRef.current = u;
        setSrc(u);
      } catch {
        if (!cancelled) {
          setFailed(true);
          setSrc(null);
        }
      } finally {
        releaseThumbSlot();
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
      if (urlRef.current?.startsWith("blob:")) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, [lotId, token, hasThumbnail, maxPx, inView]);

  const box: CSSProperties = {
    width: size,
    height: size,
    borderRadius: 10,
    flexShrink: 0,
    background: "var(--ct-thumb-placeholder)",
    border: "1px solid var(--ct-border)",
    boxShadow: "0 1px 3px rgba(0, 0, 0, 0.35)"
  };

  if (!hasThumbnail) {
    return <div style={box} aria-hidden title="No linked photo" />;
  }

  return (
    <div ref={holderRef} style={{ width: size, height: size, flexShrink: 0 }}>
      {failed ? (
        <div
          style={{
            ...box,
            background: "var(--ct-error-bg)",
            fontSize: "0.62rem",
            color: "var(--ct-error)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            textAlign: "center",
            padding: 2,
            lineHeight: 1.1
          }}
          title="Thumbnail unavailable"
        >
          —
        </div>
      ) : !src ? (
        <div
          style={{ ...box, background: "var(--ct-thumb-loading)" }}
          title={inView ? "Loading thumbnail…" : "Scroll to load"}
        />
      ) : (
        <img
          src={src}
          alt=""
          width={size}
          height={size}
          style={{ ...box, objectFit: "cover", display: "block" }}
        />
      )}
    </div>
  );
}
