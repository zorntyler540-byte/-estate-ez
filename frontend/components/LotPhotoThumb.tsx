"use client";

import type { CSSProperties } from "react";
import { memo, useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";
import { nextThumbStep, thumbFailedMessage, type ThumbStep } from "../lib/lotPhotoThumbLoad";

type Props = {
  lotId: string;
  photoId: string;
  token: string;
  /** Shown while loading and in img alt */
  label: string;
  maxPx?: number;
  minHeight?: number;
  /** Cap displayed image height (CSS px). Lot detail passes a larger value for sharper previews. */
  maxHeight?: number;
  /** When false, skip fetch — file is not on the API host (orphaned DB row). */
  fileOnDisk?: boolean;
  /** Bust thumbnail cache after replace/crop/rotate. */
  cacheBust?: number;
};

type MediaUrlOut = { url: string; expires_in?: number; kind?: string };

/**
 * Prefer a signed R2 URL (browser loads from Cloudflare — no Railway image egress).
 * Fall back to authenticated ``/thumbnail`` blob when R2 is unavailable or the
 * signed object is not a displayable still (a clip stored as ``.jpg``).
 */
function LotPhotoThumbInner({
  lotId,
  photoId,
  token,
  label,
  maxPx = 280,
  minHeight = 120,
  maxHeight = 220,
  fileOnDisk = true,
  cacheBust = 0
}: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const urlRef = useRef<string | null>(null);
  const stepRef = useRef<ThumbStep>("idle");

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
    stepRef.current = "idle";

    const forgetBlob = () => {
      if (urlRef.current?.startsWith("blob:")) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };

    const loadThumbnail = async () => {
      stepRef.current = nextThumbStep(stepRef.current, "signed-fail");
      const res = await fetch(
        `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=${maxPx}&v=${encodeURIComponent(String(cacheBust))}`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: ac.signal
        }
      );
      if (!res.ok || cancelled) {
        if (!cancelled) {
          stepRef.current = nextThumbStep(stepRef.current, "thumb-fail");
          setFailed(true);
        }
        return;
      }
      const blob = await res.blob();
      if (cancelled) return;
      if (!blob.type.startsWith("image/")) {
        stepRef.current = nextThumbStep(stepRef.current, "thumb-fail");
        setFailed(true);
        return;
      }
      forgetBlob();
      const u = URL.createObjectURL(blob);
      urlRef.current = u;
      stepRef.current = nextThumbStep(stepRef.current, "thumb-ok");
      setSrc(u);
    };

    void (async () => {
      setFailed(false);
      setSrc(null);
      forgetBlob();
      try {
        const mediaRes = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/media-url?max_px=${maxPx}&generate_if_missing=false&v=${encodeURIComponent(String(cacheBust))}`,
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
            stepRef.current = nextThumbStep(stepRef.current, "signed-ok");
            setSrc(data.url);
            return;
          }
        }

        await loadThumbnail();
      } catch {
        if (!cancelled) {
          try {
            await loadThumbnail();
          } catch {
            if (!cancelled) {
              stepRef.current = nextThumbStep(stepRef.current, "thumb-fail");
              setFailed(true);
            }
          }
        }
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
      forgetBlob();
    };
  }, [lotId, photoId, token, maxPx, fileOnDisk, cacheBust]);

  const onImgError = () => {
    const next = nextThumbStep(stepRef.current, "img-error");
    stepRef.current = next;
    if (next === "failed") {
      setSrc(null);
      setFailed(true);
      return;
    }
    setSrc(null);
    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=${maxPx}&v=${encodeURIComponent(String(cacheBust))}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store"
          }
        );
        if (!res.ok) {
          stepRef.current = nextThumbStep(stepRef.current, "thumb-fail");
          setFailed(true);
          return;
        }
        const blob = await res.blob();
        if (!blob.type.startsWith("image/")) {
          stepRef.current = nextThumbStep(stepRef.current, "thumb-fail");
          setFailed(true);
          return;
        }
        if (urlRef.current?.startsWith("blob:")) URL.revokeObjectURL(urlRef.current);
        const u = URL.createObjectURL(blob);
        urlRef.current = u;
        stepRef.current = nextThumbStep(stepRef.current, "thumb-ok");
        setSrc(u);
      } catch {
        stepRef.current = nextThumbStep(stepRef.current, "thumb-fail");
        setFailed(true);
      }
    })();
  };

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
          {thumbFailedMessage(fileOnDisk)}
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
        onError={onImgError}
        style={
          {
            width: "100%",
            height: "auto",
            maxHeight,
            objectFit: "contain",
            display: "block",
            imageOrientation: "from-image",
            // Avoid browser “optimize for speed” softening when upscaling thumbs on retina.
            imageRendering: "auto",
            WebkitUserDrag: "none"
          } as CSSProperties
        }
      />
    </div>
  );
}

export const LotPhotoThumb = memo(LotPhotoThumbInner);
