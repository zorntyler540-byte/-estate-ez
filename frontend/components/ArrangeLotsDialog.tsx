"use client";

import { useEffect, useState, type DragEvent } from "react";
import {
  mergeArrangeLots,
  moveArrangePhoto,
  moveToNewLot,
  splitArrangeLot,
  type ArrangeRef,
  type ArrangeState
} from "../lib/arrangeLots";
import type { UploadOrderRow } from "../lib/sortUploadFiles";

type DialogProps = {
  rows: readonly UploadOrderRow[];
  thumbs: readonly (string | null | undefined)[];
  initial: ArrangeState;
  singleLot?: boolean;
  onClose: () => void;
  onUse: (state: ArrangeState) => void;
};

const LOT_CAP = 40;

function dragToken(ref: ArrangeRef): string {
  return ref.where === "omitted" ? `omitted:${ref.pos}` : `lot:${ref.lot}:${ref.pos}`;
}

function parseDragToken(token: string): ArrangeRef | null {
  const omitted = /^omitted:(\d+)$/.exec(token);
  if (omitted) return { where: "omitted", pos: Number(omitted[1]) };
  const lot = /^lot:(\d+):(\d+)$/.exec(token);
  if (lot) return { where: "lot", lot: Number(lot[1]), pos: Number(lot[2]) };
  return null;
}

export function ArrangeLotsDialog({ rows, thumbs, initial, singleLot = false, onClose, onUse }: DialogProps) {
  const [state, setState] = useState(initial);
  const [drag, setDrag] = useState<ArrangeRef | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function move(from: ArrangeRef, to: ArrangeRef) {
    setState((current) => moveArrangePhoto(current, from, to));
  }

  function onDrop(to: ArrangeRef, event: DragEvent) {
    event.preventDefault();
    const from = drag || parseDragToken(event.dataTransfer.getData("text/plain"));
    setDrag(null);
    if (!from) return;
    move(from, to);
  }

  function allowDrop(event: DragEvent) {
    event.preventDefault();
  }

  const photoCount = state.lots.reduce((sum, photos) => sum + photos.length, 0);

  return (
    <div className="ct-arrange" role="dialog" aria-modal="true" aria-labelledby="arrange-lots-title" onClick={onClose}>
      <div className="ct-arrange__panel" onClick={(event) => event.stopPropagation()}>
        <div className="ct-arrange__head">
          <div>
            <h2 id="arrange-lots-title">Arrange lots</h2>
            <p className="ct-muted">
              Drag a photo into another lot, or choose its lot below. Cataloging keeps these groups and does not
              split them again on a tape number or a blank sheet.
            </p>
          </div>
          <button type="button" className="ct-btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="ct-arrange__body">
          {state.lots.map((photos, lotIndex) => (
            <section
              key={`lot-${lotIndex}`}
              className="ct-arrange__lot"
              onDragOver={allowDrop}
              onDrop={(event) => onDrop({ where: "lot", lot: lotIndex, pos: photos.length }, event)}
            >
              <div className="ct-arrange__lot-head">
                <h3>
                  Lot {lotIndex + 1} · {photos.length} photo{photos.length === 1 ? "" : "s"}
                </h3>
                <div className="ct-arrange__lot-actions">
                  {!singleLot && photos.length > 1 ? (
                    <button
                      type="button"
                      className="ct-btn"
                      onClick={() => setState((current) => splitArrangeLot(current, lotIndex, Math.ceil(photos.length / 2)))}
                    >
                      Split in half
                    </button>
                  ) : null}
                  {!singleLot && lotIndex < state.lots.length - 1 ? (
                    <button
                      type="button"
                      className="ct-btn"
                      onClick={() => setState((current) => mergeArrangeLots(current, lotIndex))}
                    >
                      Merge with next
                    </button>
                  ) : null}
                </div>
              </div>
              {photos.length > LOT_CAP ? (
                <p className="ct-muted ct-arrange__cap">
                  More than {LOT_CAP} photos. The listing limit splits this lot and flags it for review.
                </p>
              ) : null}
              <div className="ct-arrange__grid">
                {photos.map((fileIndex, pos) => (
                  <PhotoCard
                    key={fileIndex}
                    row={rows[fileIndex]}
                    thumb={thumbs[fileIndex]}
                    lotCount={state.lots.length}
                    lotIndex={lotIndex}
                    singleLot={singleLot}
                    draggable
                    onDragStart={(event) => {
                      const ref: ArrangeRef = { where: "lot", lot: lotIndex, pos };
                      setDrag(ref);
                      event.dataTransfer.setData("text/plain", dragToken(ref));
                      event.dataTransfer.effectAllowed = "move";
                    }}
                    onDragOver={allowDrop}
                    onDrop={(event) => {
                      event.stopPropagation();
                      onDrop({ where: "lot", lot: lotIndex, pos }, event);
                    }}
                    onLotChange={(value) => {
                      const from: ArrangeRef = { where: "lot", lot: lotIndex, pos };
                      if (value === "out") move(from, { where: "omitted", pos: state.omitted.length });
                      else if (value === "new") setState((current) => moveToNewLot(current, from));
                      else {
                        const target = Number(value);
                        if (target === lotIndex) return;
                        move(from, { where: "lot", lot: target, pos: state.lots[target]?.length ?? 0 });
                      }
                    }}
                  />
                ))}
              </div>
            </section>
          ))}
          {!singleLot ? (
            <section
              className="ct-arrange__lot ct-arrange__lot--out"
              onDragOver={allowDrop}
              onDrop={(event) => onDrop({ where: "omitted", pos: state.omitted.length }, event)}
            >
              <div className="ct-arrange__lot-head">
                <h3>Leave out · {state.omitted.length}</h3>
              </div>
              <p className="ct-muted">These photos are not uploaded. Drop a blank sheet here when it should not be its own lot.</p>
              <div className="ct-arrange__grid">
                {state.omitted.map((fileIndex, pos) => (
                  <PhotoCard
                    key={fileIndex}
                    row={rows[fileIndex]}
                    thumb={thumbs[fileIndex]}
                    lotCount={state.lots.length}
                    lotIndex={-1}
                    singleLot={false}
                    draggable
                    onDragStart={(event) => {
                      const ref: ArrangeRef = { where: "omitted", pos };
                      setDrag(ref);
                      event.dataTransfer.setData("text/plain", dragToken(ref));
                      event.dataTransfer.effectAllowed = "move";
                    }}
                    onDragOver={allowDrop}
                    onDrop={(event) => {
                      event.stopPropagation();
                      onDrop({ where: "omitted", pos }, event);
                    }}
                    onLotChange={(value) => {
                      const from: ArrangeRef = { where: "omitted", pos };
                      if (value === "out") return;
                      if (value === "new") setState((current) => moveToNewLot(current, from));
                      else {
                        move(from, { where: "lot", lot: Number(value), pos: state.lots[Number(value)]?.length ?? 0 });
                      }
                    }}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
        <div className="ct-arrange__foot">
          <p className="ct-muted">
            {photoCount} photo{photoCount === 1 ? "" : "s"} in {state.lots.length} lot
            {state.lots.length === 1 ? "" : "s"}
            {state.omitted.length ? ` · ${state.omitted.length} left out` : ""}
          </p>
          <div className="ct-arrange__foot-actions">
            <button type="button" className="ct-btn" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              disabled={photoCount === 0}
              onClick={() => onUse(state)}
            >
              Use these lots
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function PhotoCard({
  row,
  thumb,
  lotCount,
  lotIndex,
  singleLot,
  draggable,
  onDragStart,
  onDragOver,
  onDrop,
  onLotChange
}: {
  row: UploadOrderRow | undefined;
  thumb: string | null | undefined;
  lotCount: number;
  lotIndex: number;
  singleLot: boolean;
  draggable: boolean;
  onDragStart: (event: DragEvent) => void;
  onDragOver: (event: DragEvent) => void;
  onDrop: (event: DragEvent) => void;
  onLotChange: (value: string) => void;
}) {
  const leaf = row?.leaf || "Photo";
  return (
    <article className="ct-arrange__card" draggable={draggable} onDragStart={onDragStart} onDragOver={onDragOver} onDrop={onDrop}>
      <div className="ct-arrange__thumb">{thumb ? <img src={thumb} alt="" /> : <span />}</div>
      <span className="ct-arrange__name" title={row?.label || leaf}>
        {leaf}
      </span>
      <select
        aria-label={`Lot for ${leaf}`}
        value={lotIndex < 0 ? "out" : String(lotIndex)}
        onChange={(event) => onLotChange(event.target.value)}
      >
        {Array.from({ length: lotCount }, (_, index) => (
          <option key={index} value={String(index)}>
            Lot {index + 1}
          </option>
        ))}
        {singleLot ? null : <option value="new">New lot</option>}
        {singleLot ? null : <option value="out">Leave out</option>}
      </select>
    </article>
  );
}
