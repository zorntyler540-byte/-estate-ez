"use client";

import type { CSSProperties } from "react";
import { memo, useEffect, useState } from "react";
import { API_BASE } from "../lib/api";

type Props = {
  lotId: string;
  videoId: string;
  token: string;
  label: string;
  minHeight?: number;
  maxHeight?: number;
  fileOnDisk?: boolean;
};

type MediaUrlOut = { url: string; expires_in?: number; kind?: string };

function LotVideoThumbInner({
  lotId,
  videoId,
  token,
  label,
  minHeight = 200,
  maxHeight = 420,
  fileOnDisk = true
}: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!fileOnDisk) {
      setSrc(null);
      setFailed(true);
      return;
    }
    if (!token || !videoId) {
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
        const mediaRes = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/videos/${encodeURIComponent(videoId)}/media-url`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: ac.signal
          }
        );
        if (!mediaRes.ok || cancelled) {
          if (!cancelled) setFailed(true);
          return;
        }
        const data = (await mediaRes.json()) as MediaUrlOut;
        if (!cancelled && data?.url) setSrc(data.url);
        else if (!cancelled) setFailed(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [lotId, videoId, token, fileOnDisk]);

  const shell: CSSProperties = {
    borderRadius: "var(--ct-radius-sm)",
    border: "1px solid var(--ct-border)",
    background: failed ? "var(--ct-error-bg)" : "var(--ct-thumb-placeholder)",
    minHeight,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    position: "relative"
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
          {fileOnDisk ? "Could not load video" : "Missing on server"}
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
      <video
        src={src}
        muted
        playsInline
        preload="metadata"
        onError={() => {
          setFailed(true);
          setSrc(null);
        }}
        style={{
          width: "100%",
          height: "auto",
          maxHeight,
          objectFit: "contain",
          display: "block",
          background: "#111"
        }}
      />
    </div>
  );
}

export const LotVideoThumb = memo(LotVideoThumbInner);
