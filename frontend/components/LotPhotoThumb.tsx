"use client";

import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";

type Props = {
  lotId: string;
  photoId: string;
  token: string;
  /** Shown while loading and in img alt */
  label: string;
  maxPx?: number;
  minHeight?: number;
};

/**
 * Loads ``/lots/{lotId}/photos/{photoId}/thumbnail`` with Bearer auth (blob URL).
 */
export function LotPhotoThumb({ lotId, photoId, token, label, maxPx = 280, minHeight = 120 }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!token || !photoId) {
      setSrc(null);
      setFailed(false);
      return;
    }

    let cancelled = false;
    const ac = new AbortController();

    void (async () => {
      setFailed(false);
      setSrc(null);
      try {
        const res = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=${maxPx}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "default",
            signal: ac.signal
          }
        );
        if (!res.ok || cancelled) {
          if (!cancelled) setFailed(true);
          return;
        }
        const blob = await res.blob();
        if (cancelled) return;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        const u = URL.createObjectURL(blob);
        urlRef.current = u;
        setSrc(u);
      } catch {
        if (!cancelled) setFailed(true);
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
  }, [lotId, photoId, token, maxPx]);

  const shell: CSSProperties = {
    borderRadius: "var(--ct-radius-sm)",
    border: "1px solid var(--ct-border)",
    background: failed ? "var(--ct-error-bg)" : "var(--ct-thumb-placeholder)",
    minHeight,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column"
  };

  if (failed) {
    return (
      <div style={shell} title={label}>
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "0.5rem",
            color: "var(--ct-error)",
            fontSize: "0.78rem",
            textAlign: "center"
          }}
        >
          Could not load
        </div>
      </div>
    );
  }

  if (!src) {
    return (
      <div
        style={{ ...shell, background: "var(--ct-thumb-loading)" }}
        title={label}
        aria-busy
      />
    );
  }

  return (
    <div style={shell}>
      <img
        src={src}
        alt={label}
        title={label}
        style={{
          width: "100%",
          height: "auto",
          maxHeight: 220,
          objectFit: "contain",
          display: "block"
        }}
      />
    </div>
  );
}
