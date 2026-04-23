"use client";

import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";

type Props = {
  lotId: string;
  token: string;
  hasThumbnail: boolean;
  size?: number;
  maxPx?: number;
};

/**
 * Loads `/lots/{id}/thumbnail` with Bearer auth (blob URL) — required because
 * the API is typically on another origin than the Next app.
 */
export function LotThumbnail({ lotId, token, hasThumbnail, size = 56, maxPx = 220 }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!hasThumbnail || !token) {
      setSrc(null);
      return;
    }

    let cancelled = false;
    const ac = new AbortController();

    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/lots/${lotId}/thumbnail?max_px=${maxPx}`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: ac.signal
        });
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (cancelled) return;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        const u = URL.createObjectURL(blob);
        urlRef.current = u;
        setSrc(u);
      } catch {
        if (!cancelled) setSrc(null);
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, [lotId, token, hasThumbnail, maxPx]);

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

  if (!src) {
    return <div style={{ ...box, background: "var(--ct-thumb-loading)" }} title="Loading thumbnail…" />;
  }

  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      style={{ ...box, objectFit: "cover", display: "block" }}
    />
  );
}
