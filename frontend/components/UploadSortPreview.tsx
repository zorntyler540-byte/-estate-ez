"use client";

import { useEffect, useId, useRef, useState } from "react";
import { isHeicLikeFile } from "../lib/heicConvert";
import { buildUploadOrderPreview, type UploadSortable } from "../lib/sortUploadFiles";

type PreviewProps<T extends UploadSortable> = {
  files: readonly T[];
  /** Parallel to ``files``. Empty string means the thumbnail is still being prepared. */
  thumbUrls?: readonly (string | null | undefined)[];
  /** Path the sort used (folder-relative when the picker provided one). */
  labelOf?: (file: T) => string;
  /** All photos stay one catalog lot; the list is still the order cataloging sees. */
  singleLot?: boolean;
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

/**
 * Numbered order the catalog pipeline will see, shown before Upload sends the batch.
 * Thumbnails make a blank divider sheet visible between the groups it splits.
 */
export function UploadSortPreview<T extends UploadSortable>({
  files,
  thumbUrls,
  labelOf,
  singleLot = false,
  onOpen
}: PreviewProps<T>) {
  const titleId = useId();
  const listRef = useRef<HTMLOListElement>(null);
  const generated = useObjectThumbUrls(files, thumbUrls == null);
  const thumbs = thumbUrls ?? generated;
  const preview = buildUploadOrderPreview(
    files,
    labelOf ? (file) => labelOf(file as T) : undefined
  );

  if (files.length < 2) return null;

  function jumpTo(index: number) {
    listRef.current?.querySelector(`[data-sort-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
  }

  return (
    <section className="ct-sort-preview" aria-labelledby={titleId}>
      <div className="ct-sort-preview__head">
        <h3 id={titleId} className="ct-sort-preview__title">
          Catalog order
        </h3>
        <p className="ct-muted ct-sort-preview__lead">
          {files.length} photos
          {preview.folderCount > 1 ? ` in ${preview.folderCount} folders` : ""}
          {preview.first && preview.last ? `, from ${preview.first} to ${preview.last}` : ""}.{" "}
          {singleLot
            ? "These stay one lot. Cataloging still sees them in this sequence."
            : "Cataloging uses this sequence. A new tape number or a blank divider photo starts the next lot."}
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
        {preview.rows.map((row) => {
          const raw = thumbs[row.index];
          const thumb = raw === "" ? undefined : raw;
          const position = String(row.index + 1).padStart(files.length >= 100 ? 4 : 2, "0");
          return (
            <li key={row.index} className="ct-sort-preview__item" data-sort-index={row.index}>
              {row.showDir ? <div className="ct-sort-preview__dir">{row.dir}</div> : null}
              <div
                className={`ct-sort-preview__row${row.backwards ? " ct-sort-preview__row--backwards" : ""}`}
                title={
                  row.backwards && row.earlierIndex != null
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
