"use client";

import { Play } from "lucide-react";
import { memo, useEffect, useState } from "react";
import { API_BASE } from "../lib/api";
import { cn } from "../lib/utils";

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

  const shell = cn(
    "relative flex flex-col overflow-hidden rounded-xl border shadow-sm shadow-black/30",
    failed ? "border-[rgba(248,113,113,0.35)] bg-[rgba(239,68,68,0.12)]" : "border-border bg-[var(--ct-thumb-placeholder)]"
  );

  if (failed) {
    return (
      <div className={shell} style={{ minHeight }} title={label}>
        <div className="flex flex-1 items-center justify-center px-2 py-2 text-center text-xs text-[#fca5a5]">
          {fileOnDisk ? "Could not load video" : "Missing on server"}
        </div>
      </div>
    );
  }

  if (!src) {
    return (
      <div
        className={cn(shell, "animate-pulse bg-[var(--ct-thumb-loading)]")}
        style={{ minHeight }}
        title={label}
        aria-busy
      />
    );
  }

  return (
    <div className={shell} style={{ minHeight }}>
      <video
        src={src}
        muted
        playsInline
        preload="metadata"
        onError={() => {
          setFailed(true);
          setSrc(null);
        }}
        className="block h-auto w-full bg-[#111] object-contain"
        style={{ maxHeight }}
      />
      <span className="pointer-events-none absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-md border border-primary/40 bg-black/70 px-1.5 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wide text-primary">
        <Play size={10} aria-hidden />
        Video
      </span>
    </div>
  );
}

export const LotVideoThumb = memo(LotVideoThumbInner);
