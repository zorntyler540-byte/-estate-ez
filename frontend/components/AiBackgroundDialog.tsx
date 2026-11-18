"use client";

import { useEffect, useId, useRef } from "react";
import { Loader2 } from "lucide-react";
import { photoCanBeLotCover, type AiBackgroundPhoto } from "../lib/aiBackground";
import { LotPhotoThumb } from "./LotPhotoThumb";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

export type AiBackgroundColor = "studio" | "white" | "gray";

const COLORS: { id: AiBackgroundColor; swatch: string; label: string }[] = [
  { id: "studio", swatch: "#f3f0ea", label: "Warm" },
  { id: "white", swatch: "#ffffff", label: "White" },
  { id: "gray", swatch: "#e2e2e5", label: "Gray" }
];

const ROLE_LABEL: Record<string, string> = {
  hero: "Group",
  item_front: "Item",
  angle_only: "Detail",
  tape: "SKU tape",
  unusable: "Skip"
};

type Photo = AiBackgroundPhoto & {
  original_name: string;
  file_on_disk?: boolean;
  shows_measuring_scale?: boolean;
  plain_background?: boolean;
};

type Props = {
  lotId: string;
  token: string;
  photos: Photo[];
  coverId: string | null;
  selectedId: string | null;
  color: AiBackgroundColor;
  makeCover: boolean;
  busy: boolean;
  note: string;
  cacheBust: Record<string, number>;
  onSelect: (photoId: string) => void;
  onColor: (color: AiBackgroundColor) => void;
  onMakeCover: (value: boolean) => void;
  onApply: () => void;
  onRestore: () => void;
  onClose: () => void;
};

export function AiBackgroundDialog({
  lotId,
  token,
  photos,
  coverId,
  selectedId,
  color,
  makeCover,
  busy,
  note,
  cacheBust,
  onSelect,
  onColor,
  onMakeCover,
  onApply,
  onRestore,
  onClose
}: Props) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const selected = photos.find((photo) => photo.id === selectedId) || null;
  const canCover = selected ? photoCanBeLotCover(selected) : false;
  const canRestore = Boolean(selected?.plain_background);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return (
    <div
      className="ct-sale-covers-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-busy={busy}
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="ct-sale-covers-modal__panel ct-ai-bg-panel"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="ct-sale-covers-modal__head">
          <h3 id={titleId}>AI background</h3>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Close
          </Button>
        </div>
        <p className="ct-ai-bg-note">
          Pick one photo. AI keeps the piece and replaces the room with a plain color. Other photos stay as shot.
          A replaced photo is sent again the next time you use Add photos to CT Seller.
        </p>
        <div className="ct-ai-bg-colors" role="group" aria-label="Background color">
          {COLORS.map((item) => (
            <Button
              key={item.id}
              type="button"
              size="sm"
              variant={color === item.id ? "default" : "outline"}
              disabled={busy}
              aria-pressed={color === item.id}
              onClick={() => onColor(item.id)}
            >
              <span
                aria-hidden
                className="ct-ai-bg-swatch"
                style={{ background: item.swatch }}
              />
              {item.label}
            </Button>
          ))}
        </div>
        <div className="ct-sale-covers-grid ct-ai-bg-grid" role="listbox" aria-label="Lot photos">
          {photos.map((photo) => {
            const isSelected = photo.id === selectedId;
            const role = (photo.photo_role || "").toLowerCase();
            const roleLabel = photo.shows_measuring_scale ? "Measure" : ROLE_LABEL[role] || null;
            const missing = photo.file_on_disk === false;
            return (
              <button
                key={photo.id}
                type="button"
                role="option"
                aria-selected={isSelected}
                disabled={busy || missing}
                className={`ct-ai-bg-card${isSelected ? " is-selected" : ""}`}
                onClick={() => onSelect(photo.id)}
              >
                <LotPhotoThumb
                  lotId={lotId}
                  photoId={photo.id}
                  token={token}
                  label={photo.original_name}
                  maxPx={320}
                  minHeight={108}
                  maxHeight={128}
                  fileOnDisk={photo.file_on_disk !== false}
                  cacheBust={cacheBust[photo.id] ?? 0}
                />
                <span className="ct-ai-bg-card__meta">
                  {roleLabel ? <Badge variant="neutral">{roleLabel}</Badge> : <span className="ct-muted">Photo</span>}
                  {photo.id === coverId ? <Badge variant="info">Cover</Badge> : null}
                  {photo.plain_background ? <Badge variant="warning">AI</Badge> : null}
                </span>
              </button>
            );
          })}
        </div>
        <label className="ct-ai-bg-cover">
          <input
            type="checkbox"
            checked={makeCover && canCover}
            disabled={busy || !canCover}
            onChange={(event) => onMakeCover(event.target.checked)}
          />
          <span>
            Make this the lot cover
            <span className="ct-ai-bg-cover__hint">
              {canCover
                ? "Moves this photo to the front so the dashboard and the listing use it as the cover."
                : "SKU tape and measurement photos stay off the cover."}
            </span>
          </span>
        </label>
        {note ? (
          <p className="ct-ai-bg-note" role="status">
            {busy ? <Loader2 size={14} className="ct-spin" aria-hidden /> : null}
            {note}
          </p>
        ) : null}
        <div className="ct-ai-bg-actions">
          <Button type="button" onClick={onApply} disabled={busy || !selected || selected.file_on_disk === false} aria-busy={busy}>
            {busy ? "Replacing background…" : "Replace background"}
          </Button>
          {canRestore ? (
            <Button type="button" variant="outline" onClick={onRestore} disabled={busy}>
              Restore original
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
