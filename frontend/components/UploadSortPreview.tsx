"use client";

import { useEffect, useId, useRef, useState } from "react";
import { arrangeStateFromIndexes, type ArrangeState } from "../lib/arrangeLots";
import { isHeicLikeFile } from "../lib/heicConvert";
import { lotSplitSummary, previewDividerFile, previewLotSplits } from "../lib/lotSplitPreview";
import { buildUploadOrderPreview, uploadSortLabel, type UploadOrderRow, type UploadSortable } from "../lib/sortUploadFiles";
import { stagedUploadRelOrNull } from "../lib/stagedUploadPath";
import { ArrangeLotsDialog } from "./ArrangeLotsDialog";

type PreviewProps<T extends UploadSortable> = {
  files: readonly T[];
  /** Parallel to ``files``. Empty string means the thumbnail is still being prepared. */
  thumbUrls?: readonly (string | null | undefined)[];
  /** Path the sort used (folder-relative when the picker provided one). */
  labelOf?: (file: T) => string;
  /** All photos stay one catalog lot. */
  singleLot?: boolean;
  /** Digit-named folders (``035``) become the lot number. */
  inheritSku?: boolean;
  /** Staff already arranged these groups. Blank sheets are not pulled back out. */
  arrangementLocked?: boolean;
  onOpen?: (index: number) => void;
  /** Apply an arranged grouping to the staged upload. */
  onCommitArrangement?: (state: ArrangeState) => void;
  onResetArrangement?: () => void;
};

function useObjectThumbUrls(files: readonly UploadSortable[], enabled: boolean): (string | null)[] {
  const [urls, setUrls] = useState<(string | null)[]>([]);
  useEffect(() => {
    if (!enabled) {
      setUrls([]);
      return;
    }
    const created = files.map((file) => {
      if (!(file instanceof File) || isHeicLikeFile(file)) return null;
      const type = (file.type || "").toLowerCase();
      if (type && !type.startsWith("image/")) return null;
      try {
        return URL.createObjectURL(file);
      } catch {
        return null;
      }
    });
    setUrls(created);
    return () => {
      for (const url of created) {
        if (url) URL.revokeObjectURL(url);
      }
    };
  }, [enabled, files]);
  return urls;
}

function useBlankDividers(files: readonly UploadSortable[], enabled: boolean): {
  dividers: ReadonlySet<number>;
  likely: ReadonlySet<number>;
  checking: boolean;
} {
  const [dividers, setDividers] = useState<ReadonlySet<number>>(new Set());
  const [likely, setLikely] = useState<ReadonlySet<number>>(new Set());
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setDividers(new Set());
      setLikely(new Set());
      setChecking(false);
      return;
    }
    let cancelled = false;
    setChecking(true);
    setDividers(new Set());
    setLikely(new Set());
    void (async () => {
      const found = new Set<number>();
      const soft = new Set<number>();
      for (let index = 0; index < files.length; index += 1) {
        if (cancelled) return;
        const file = files[index];
        if (file instanceof File) {
          const kind = await previewDividerFile(file);
          if (kind) found.add(index);
          if (kind === "likely") soft.add(index);
        }
        if (index % 6 === 5 && !cancelled) {
          setDividers(new Set(found));
          setLikely(new Set(soft));
        }
      }
      if (!cancelled) {
        setDividers(new Set(found));
        setLikely(new Set(soft));
        setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, files]);
  return { dividers, likely, checking };
}

/**
 * Lot groups cataloging will start with, shown before Upload.
 * Folders and blank sheets split the list. Tape numbers are still read later.
 */
export function UploadSortPreview<T extends UploadSortable>({
  files,
  thumbUrls,
  labelOf,
  singleLot = false,
  inheritSku = false,
  arrangementLocked = false,
  onOpen,
  onCommitArrangement,
  onResetArrangement
}: PreviewProps<T>) {
  const titleId = useId();
  const listRef = useRef<HTMLOListElement>(null);
  const [arrangeOpen, setArrangeOpen] = useState(false);
  const generated = useObjectThumbUrls(files, thumbUrls == null);
  const thumbs = thumbUrls ?? generated;
  const preview = buildUploadOrderPreview(files, (file) => {
    if (labelOf) return labelOf(file as T);
    if (file instanceof File) return stagedUploadRelOrNull(file) || file.name;
    return uploadSortLabel(file);
  });
  const { dividers, likely, checking } = useBlankDividers(
    files,
    !singleLot && !arrangementLocked && files.length > 1
  );
  const splits = previewLotSplits(
    preview.rows.map((row) => row.label),
    { dividerIndexes: dividers, singleLot, inheritSku }
  );
  const lotCount = splits.filter((split) => split.kind === "lot").length;
  const dividerCount = splits.filter((split) => split.kind === "divider").length;
  const likelyCount = splits.filter((split) => split.kind === "divider" && likely.has(split.index)).length;

  if (files.length < 2) return null;

  function jumpTo(index: number) {
    listRef.current?.querySelector(`[data-sort-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
  }

  function renderRow(row: UploadOrderRow, divider: boolean) {
    const raw = thumbs[row.index];
    const thumb = raw === "" ? undefined : raw;
    const position = String(row.index + 1).padStart(files.length >= 100 ? 4 : 2, "0");
    return (
      <div
        key={row.index}
        className={`ct-sort-preview__row${row.backwards ? " ct-sort-preview__row--backwards" : ""}${
          divider ? " ct-sort-preview__row--divider" : ""
        }`}
        data-sort-index={row.index}
        title={
          divider
            ? likely.has(row.index)
              ? "Likely blank sheet. It separates lots and is not cataloged."
              : "Blank sheet. It separates lots and is not cataloged."
            : row.backwards && row.earlierIndex != null
              ? `Camera time is earlier than row ${row.earlierIndex + 1} (${row.earlierLeaf})`
              : row.label
        }
      >
        <span className="ct-sort-preview__n">{position}</span>
        {onOpen ? (
          <button
            type="button"
            className="ct-sort-preview__thumb"
            aria-label={`Preview ${row.leaf}`}
            onClick={() => onOpen(row.index)}
          >
            <PreviewThumb url={thumb} />
          </button>
        ) : (
          <span className="ct-sort-preview__thumb" aria-hidden>
            <PreviewThumb url={thumb} />
          </span>
        )}
        <span className="ct-sort-preview__name">{row.leaf}</span>
      </div>
    );
  }

  return (
    <section className="ct-sort-preview" aria-labelledby={titleId}>
      <div className="ct-sort-preview__head">
        <h3 id={titleId} className="ct-sort-preview__title">
          Lot split
        </h3>
        <p className="ct-muted ct-sort-preview__lead">
          {lotSplitSummary({
            lotCount,
            dividerCount,
            likelyCount,
            singleLot,
            checkingBlank: checking,
            arranged: arrangementLocked
          })}
        </p>
      </div>
      {onCommitArrangement ? (
        <div className="ct-sort-preview__actions">
          <button type="button" className="ct-btn" onClick={() => setArrangeOpen(true)}>
            Arrange lots
          </button>
          {arrangementLocked && onResetArrangement ? (
            <button type="button" className="ct-btn" onClick={onResetArrangement}>
              Reset automatic split
            </button>
          ) : null}
        </div>
      ) : null}
      {preview.note ? (
        <div className="ct-banner ct-banner--warning ct-sort-preview__warn" role="status">
          <p style={{ margin: 0 }}>{preview.note}</p>
          <div className="ct-sort-preview__jumps">
            {preview.rows
              .filter((row) => row.backwards)
              .slice(0, 6)
              .map((row) => (
                <button key={row.index} type="button" className="ct-btn" onClick={() => jumpTo(row.index)}>
                  Row {row.index + 1}
                </button>
              ))}
          </div>
        </div>
      ) : null}
      <ol ref={listRef} className="ct-sort-preview__list">
        {splits.map((split) => {
          if (split.kind === "divider") {
            const row = preview.rows[split.index];
            if (!row) return null;
            return (
              <li key={`divider-${split.index}`} className="ct-sort-preview__item">
                <div className="ct-sort-preview__lot ct-sort-preview__lot--divider">
                  {likely.has(split.index) ? "Likely blank sheet · left out" : "Blank sheet · left out"}
                </div>
                {renderRow(row, true)}
              </li>
            );
          }
          const count = split.indexes.length;
          return (
            <li key={`lot-${split.lotNumber}`} className="ct-sort-preview__item">
              <div className="ct-sort-preview__lot">
                Lot {split.lotNumber}
                {split.sku ? ` · ${split.sku}` : ""} · {count} photo{count === 1 ? "" : "s"}
              </div>
              {split.indexes.map((index) => {
                const row = preview.rows[index];
                return row ? renderRow(row, false) : null;
              })}
            </li>
          );
        })}
      </ol>
      {arrangeOpen && onCommitArrangement ? (
        <ArrangeLotsDialog
          rows={preview.rows}
          thumbs={thumbs}
          singleLot={singleLot}
          initial={arrangeStateFromIndexes(
            splits.flatMap((split) => (split.kind === "lot" ? [{ indexes: split.indexes }] : [])),
            splits.flatMap((split) => (split.kind === "divider" ? [split.index] : []))
          )}
          onClose={() => setArrangeOpen(false)}
          onUse={(state) => {
            onCommitArrangement(state);
            setArrangeOpen(false);
          }}
        />
      ) : null}
    </section>
  );
}

function PreviewThumb({ url }: { url: string | null | undefined }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [url]);
  if (url === undefined) {
    return <span className="ct-staging-thumb-pending" />;
  }
  if (!url || failed) {
    return <span className="ct-sort-preview__thumb-empty" />;
  }
  return <img src={url} alt="" onError={() => setFailed(true)} />;
}
