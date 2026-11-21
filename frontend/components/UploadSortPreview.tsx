"use client";

import { useEffect, useId, useRef, useState } from "react";
import { isHeicLikeFile } from "../lib/heicConvert";
import { confidentBlankFile, lotSplitSummary, previewLotSplits } from "../lib/lotSplitPreview";
import { buildUploadOrderPreview, type UploadOrderRow, type UploadSortable } from "../lib/sortUploadFiles";

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
  onOpen?: (index: number) => void;
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
  checking: boolean;
} {
  const [dividers, setDividers] = useState<ReadonlySet<number>>(new Set());
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setDividers(new Set());
      setChecking(false);
      return;
    }
    let cancelled = false;
    setChecking(true);
    setDividers(new Set());
    void (async () => {
      const found = new Set<number>();
      for (let index = 0; index < files.length; index += 1) {
        if (cancelled) return;
        const file = files[index];
        if (file instanceof File && (await confidentBlankFile(file))) found.add(index);
        if (index % 6 === 5 && !cancelled) setDividers(new Set(found));
      }
      if (!cancelled) {
        setDividers(new Set(found));
        setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, files]);
  return { dividers, checking };
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
  onOpen
}: PreviewProps<T>) {
  const titleId = useId();
  const listRef = useRef<HTMLOListElement>(null);
  const generated = useObjectThumbUrls(files, thumbUrls == null);
  const thumbs = thumbUrls ?? generated;
  const preview = buildUploadOrderPreview(files, labelOf ? (file) => labelOf(file as T) : undefined);
  const { dividers, checking } = useBlankDividers(files, !singleLot && files.length > 1);
  const splits = previewLotSplits(
    preview.rows.map((row) => row.label),
    { dividerIndexes: dividers, singleLot, inheritSku }
  );
  const lotCount = splits.filter((split) => split.kind === "lot").length;
  const dividerCount = splits.filter((split) => split.kind === "divider").length;

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
            ? "Blank sheet. It separates lots and is not cataloged."
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
          {lotSplitSummary({ lotCount, dividerCount, singleLot, checkingBlank: checking })}
        </p>
      </div>
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
                <div className="ct-sort-preview__lot ct-sort-preview__lot--divider">Divider · left out</div>
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
