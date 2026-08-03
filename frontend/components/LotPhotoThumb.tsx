"use client";

import type { CSSProperties } from "react";
import { memo, useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";

type Props = {
  lotId: string;
  photoId: string;
  token: string;
  /** Shown while loading and in img alt */
  label: string;
  maxPx?: number;
  minHeight?: number;
  /** When false, skip fetch — file is not on the API host (orphaned DB row). */
  fileOnDisk?: boolean;
  /** Bust thumbnail cache after replace/crop/rotate. */
  cacheBust?: number;
};

type MediaUrlOut = { url: string; expires_in?: number; kind?: string };

/**
 * Prefer a signed R2 URL (browser loads from Cloudflare — no Railway image egress).
 * Fall back to authenticated ``/thumbnail`` blob when R2 is unavailable.
 */
function LotPhotoThumbInner({
  lotId,
  photoId,
  token,
  label,
  maxPx = 280,
  minHeight = 120,
  fileOnDisk = true,
  cacheBust = 0
}: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!fileOnDisk) {
      setSrc(null);
      setFailed(true);
      return;
    }
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
      if (urlRef.current?.startsWith("blob:")) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      try {
        const mediaRes = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/media-url?max_px=${maxPx}&v=${encodeURIComponent(String(cacheBust))}`,
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

        const res = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=${maxPx}&v=${encodeURIComponent(String(cacheBust))}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: ac.signal
          }
        );
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
        if (!cancelled) setFailed(true);
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
  }, [lotId, photoId, token, maxPx, fileOnDisk, cacheBust]);

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
          {fileOnDisk ? "Could not load" : "Missing on server"}
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
        draggable={false}
        style={
          {
            width: "100%",
            height: "auto",
            maxHeight: 220,
            objectFit: "contain",
            display: "block",
            WebkitUserDrag: "none"
          } as CSSProperties
        }
      />
    </div>
  );
}

export const LotPhotoThumb = memo(LotPhotoThumbInner);
