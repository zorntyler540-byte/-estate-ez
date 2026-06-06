"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";

type Props = {
  checkId: string;
  photoIndex: number;
  token: string;
  size?: number;
  maxPx?: number;
};

/** Loads authenticated appraisal check thumbnail as a blob URL. */
export function AppraisalPhotoThumb({ checkId, photoIndex, token, size = 72, maxPx = 320 }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!token || !checkId) return;
    let cancelled = false;
    const ac = new AbortController();

    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/appraiser/checks/${encodeURIComponent(checkId)}/photos/${photoIndex}/thumbnail?max_px=${maxPx}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: ac.signal
          }
        );
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (cancelled) return;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        const u = URL.createObjectURL(blob);
        urlRef.current = u;
        setSrc(u);
      } catch {
        /* ignore */
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
  }, [checkId, photoIndex, token, maxPx]);

  const box = {
    width: size,
    height: size,
    borderRadius: 6,
    border: "1px solid var(--ct-border)",
    objectFit: "cover" as const,
    background: "var(--ct-thumb-loading)"
  };

  if (!src) {
    return <div style={box} aria-hidden />;
  }

  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" style={box} />;
}
