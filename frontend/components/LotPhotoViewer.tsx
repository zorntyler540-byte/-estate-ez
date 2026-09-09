"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchLotPhotoBlob, replaceLotPhotoBlob } from "../lib/lotPhotoReplace";
import { ZoomablePreview } from "./ZoomablePreview";
import {
  cropImageBlob,
  displayPointToNatural,
  normalizeCropRect,
  rotateImageBlob,
  type DisplayPoint
} from "../lib/photoEdit";

export type ViewerPhoto = {
  id: string;
  original_name: string;
  file_on_disk?: boolean;
};

type Props = {
  lotId: string;
  token: string;
  photos: ViewerPhoto[];
  index: number;
  onClose: () => void;
  onIndexChange: (index: number) => void;
  onSaved: (photoId: string) => void;
};

export function LotPhotoViewer({ lotId, token, photos, index, onClose, onIndexChange, onSaved }: Props) {
  const photo = photos[index];
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const workBlobRef = useRef<Blob | null>(null);
  const baseBlobRef = useRef<Blob | null>(null);
  const previewUrlRef = useRef("");
  const [dirty, setDirty] = useState(false);
  const [cropMode, setCropMode] = useState(false);
  const [cropAnchor, setCropAnchor] = useState<DisplayPoint | null>(null);
  const [cropCurrent, setCropCurrent] = useState<DisplayPoint | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const revokePreview = useCallback(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = "";
    }
  }, []);

  const setWorkBlob = useCallback(
    (blob: Blob) => {
      workBlobRef.current = blob;
      revokePreview();
      const u = URL.createObjectURL(blob);
      previewUrlRef.current = u;
      setPreviewUrl(u);
      setDirty(true);
    },
    [revokePreview]
  );

  const loadPhoto = useCallback(async () => {
    if (!photo || !token) return;
    setLoading(true);
    setError("");
    setCropMode(false);
    setCropAnchor(null);
    setCropCurrent(null);
    setDirty(false);
    revokePreview();
    workBlobRef.current = null;
    baseBlobRef.current = null;
    try {
      const blob = await fetchLotPhotoBlob(lotId, photo.id, token);
      baseBlobRef.current = blob;
      workBlobRef.current = blob;
      const u = URL.createObjectURL(blob);
      previewUrlRef.current = u;
      setPreviewUrl(u);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load photo.");
      setPreviewUrl("");
    } finally {
      setLoading(false);
    }
  }, [lotId, photo, token, revokePreview]);

  useEffect(() => {
    void loadPhoto();
    return () => revokePreview();
  }, [loadPhoto, revokePreview]);

  async function handleRotate(degrees: 90 | -90) {
    const blob = workBlobRef.current;
    if (!blob) return;
    setError("");
    try {
      const next = await rotateImageBlob(blob, degrees);
      setWorkBlob(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rotate failed");
    }
  }

  function handleImagePointerDown(e: React.PointerEvent) {
    if (!cropMode || !imgRef.current) return;
    e.preventDefault();
    const pt = displayPointToNatural(e.clientX, e.clientY, imgRef.current);
    if (!pt) return;
    setCropAnchor(pt);
    setCropCurrent(pt);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  }

  function handleImagePointerMove(e: React.PointerEvent) {
    if (!cropMode || !cropAnchor || !imgRef.current) return;
    const pt = displayPointToNatural(e.clientX, e.clientY, imgRef.current);
    if (pt) setCropCurrent(pt);
  }

  function handleImagePointerUp() {
    if (!cropMode) return;
    setCropAnchor(null);
  }

  async function applyCrop() {
    const blob = workBlobRef.current;
    const img = imgRef.current;
    if (!blob || !img || !cropAnchor || !cropCurrent) {
      setError("Drag on the image to select a crop area.");
      return;
    }
    const rect = normalizeCropRect(cropAnchor, cropCurrent, img.naturalWidth, img.naturalHeight);
    setError("");
    try {
      const next = await cropImageBlob(blob, rect);
      setWorkBlob(next);
      setCropMode(false);
      setCropAnchor(null);
      setCropCurrent(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Crop failed");
    }
  }

  function resetEdits() {
    const base = baseBlobRef.current;
    if (!base) return;
    workBlobRef.current = base;
    revokePreview();
    const u = URL.createObjectURL(base);
    previewUrlRef.current = u;
    setPreviewUrl(u);
    setDirty(false);
    setCropMode(false);
    setCropAnchor(null);
    setCropCurrent(null);
    setError("");
  }

  async function saveEdits() {
    const blob = workBlobRef.current;
    if (!photo || !blob || !dirty) return;
    setSaving(true);
    setError("");
    try {
      await replaceLotPhotoBlob(lotId, photo.id, token, blob, photo.original_name);
      const fresh = await fetchLotPhotoBlob(lotId, photo.id, token);
      baseBlobRef.current = fresh;
      workBlobRef.current = fresh;
      revokePreview();
      const u = URL.createObjectURL(fresh);
      previewUrlRef.current = u;
      setPreviewUrl(u);
      setDirty(false);
      onSaved(photo.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save photo");
    } finally {
      setSaving(false);
    }
  }

  const cropOverlay =
    cropMode && cropAnchor && cropCurrent && imgRef.current
      ? normalizeCropRect(cropAnchor, cropCurrent, imgRef.current.naturalWidth, imgRef.current.naturalHeight)
      : null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.85)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem"
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(96vw, 1200px)",
          maxHeight: "92vh",
          display: "flex",
          flexDirection: "column",
          gap: "0.65rem"
        }}
      >
        <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div className="ct-muted" style={{ fontSize: "0.9rem" }}>
            Photo {index + 1}/{photos.length}
            {photo ? ` · ${photo.original_name}` : ""}
          </div>
          <button type="button" className="ct-btn ct-btn--ghost" onClick={onClose}>
            Close
          </button>
        </div>

        <div
          className="ct-row ct-row--gap-lg"
          style={{ flexWrap: "wrap", alignItems: "center", gap: "0.4rem" }}
        >
          <button type="button" className="ct-btn" disabled={loading || saving} onClick={() => void handleRotate(-90)}>
            Rotate left
          </button>
          <button type="button" className="ct-btn" disabled={loading || saving} onClick={() => void handleRotate(90)}>
            Rotate right
          </button>
          <button
            type="button"
            className="ct-btn"
            disabled={loading || saving}
            onClick={() => {
              setCropMode((v) => !v);
              setCropAnchor(null);
              setCropCurrent(null);
              setError("");
            }}
          >
            {cropMode ? "Cancel crop" : "Crop"}
          </button>
          {cropMode ? (
            <button type="button" className="ct-btn ct-btn--primary" disabled={loading || saving} onClick={() => void applyCrop()}>
              Apply crop
            </button>
          ) : null}
          <button type="button" className="ct-btn" disabled={loading || saving || !dirty} onClick={resetEdits}>
            Reset
          </button>
          <button
            type="button"
            className="ct-btn ct-btn--primary"
            disabled={loading || saving || !dirty}
            onClick={() => void saveEdits()}
          >
            {saving ? "Saving…" : "Save to catalog"}
          </button>
        </div>

        {cropMode ? (
          <p className="ct-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
            Drag on the image to draw a crop box, then click <strong>Apply crop</strong>. Finish with{" "}
            <strong>Save to catalog</strong>.
          </p>
        ) : (
          <p className="ct-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
            Pinch or scroll to zoom. Rotate or crop, then <strong>Save to catalog</strong> to replace
            the stored file.
          </p>
        )}

        <div
          style={{
            minHeight: "50vh",
            border: "1px solid var(--ct-border)",
            borderRadius: "var(--ct-radius)",
            background: "#0c0f12",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            overflow: "hidden",
            position: "relative",
            touchAction: "none"
          }}
        >
          {loading ? (
            <div className="ct-muted">Loading full-size photo…</div>
          ) : error && !previewUrl ? (
            <div className="ct-banner ct-banner--error" style={{ margin: "1rem" }}>
              {error}
            </div>
          ) : previewUrl ? (
            <ZoomablePreview resetKey={`${photo?.id ?? "photo"}:${cropMode ? "crop" : "view"}`} disabled={cropMode}>
              <div style={{ position: "relative", maxWidth: "100%", maxHeight: "78vh" }}>
                <img
                  ref={imgRef}
                  src={previewUrl}
                  alt={photo?.original_name || "Lot photo"}
                  draggable={false}
                  onPointerDown={handleImagePointerDown}
                  onPointerMove={handleImagePointerMove}
                  onPointerUp={handleImagePointerUp}
                  onPointerLeave={handleImagePointerUp}
                  style={{
                    maxWidth: "100%",
                    maxHeight: "78vh",
                    objectFit: "contain",
                    display: "block",
                    cursor: cropMode ? "crosshair" : "zoom-in"
                  }}
                />
                {cropOverlay && imgRef.current ? (
                  <CropBoxOverlay img={imgRef.current} rect={cropOverlay} />
                ) : null}
              </div>
            </ZoomablePreview>
          ) : null}
        </div>

        {error && previewUrl ? (
          <div className="ct-banner ct-banner--error" style={{ margin: 0 }}>
            {error}
          </div>
        ) : null}

        <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "space-between" }}>
          <button
            type="button"
            className="ct-btn"
            disabled={loading || saving || index <= 0}
            onClick={() => onIndexChange(index - 1)}
          >
            Previous
          </button>
          <button
            type="button"
            className="ct-btn"
            disabled={loading || saving || index >= photos.length - 1}
            onClick={() => onIndexChange(index + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

function CropBoxOverlay({
  img,
  rect
}: {
  img: HTMLImageElement;
  rect: { x: number; y: number; width: number; height: number };
}) {
  const box = useMemoDisplayRect(img, rect);
  if (!box) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: box.left,
        top: box.top,
        width: box.width,
        height: box.height,
        border: "2px dashed var(--ct-primary)",
        background: "rgba(56, 189, 248, 0.12)",
        pointerEvents: "none",
        boxSizing: "border-box"
      }}
    />
  );
}

function useMemoDisplayRect(
  img: HTMLImageElement,
  rect: { x: number; y: number; width: number; height: number }
) {
  const [box, setBox] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  useEffect(() => {
    const update = () => {
      const r = img.getBoundingClientRect();
      const parent = img.parentElement?.getBoundingClientRect();
      if (!parent || !img.naturalWidth) {
        setBox(null);
        return;
      }
      const scale = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
      const dw = img.naturalWidth * scale;
      const dh = img.naturalHeight * scale;
      const ox = (r.width - dw) / 2;
      const oy = (r.height - dh) / 2;
      setBox({
        left: ox + (rect.x / img.naturalWidth) * dw,
        top: oy + (rect.y / img.naturalHeight) * dh,
        width: (rect.width / img.naturalWidth) * dw,
        height: (rect.height / img.naturalHeight) * dh
      });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(img);
    return () => ro.disconnect();
  }, [img, rect.x, rect.y, rect.width, rect.height]);

  return box;
}
