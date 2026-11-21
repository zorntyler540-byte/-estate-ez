/**
 * Edits to the lot groups shown before upload.
 *
 * Applying the result rewrites each photo's path to ``arranged/lot-N/…``.
 * Cataloging keeps those groups and does not split them again on tape or blank sheets.
 */

export type ArrangeState = {
  lots: number[][];
  /** Photo indexes left out of every lot. They are not uploaded. */
  omitted: number[];
};

export type ArrangeResult = ArrangeState;

type LotRef = { where: "lot"; lot: number; pos: number };
type OmitRef = { where: "omitted"; pos: number };
export type ArrangeRef = LotRef | OmitRef;

export function arrangeStateFromIndexes(groups: { indexes: number[] }[], omitted: number[]): ArrangeState {
  const lots = groups.map((group) => group.indexes.filter((index) => !omitted.includes(index)));
  const kept = lots.filter((photos) => photos.length > 0);
  return { lots: kept.length ? kept : [[]], omitted: [...omitted] };
}

function cloneState(state: ArrangeState): ArrangeState {
  return { lots: state.lots.map((photos) => [...photos]), omitted: [...state.omitted] };
}

function take(state: ArrangeState, from: ArrangeRef): number | null {
  if (from.where === "omitted") {
    const [index] = state.omitted.splice(from.pos, 1);
    return index ?? null;
  }
  const photos = state.lots[from.lot];
  if (!photos) return null;
  const [index] = photos.splice(from.pos, 1);
  return index ?? null;
}

function sameList(a: ArrangeRef, b: ArrangeRef): boolean {
  if (a.where !== b.where) return false;
  if (a.where === "omitted" && b.where === "omitted") return true;
  return a.where === "lot" && b.where === "lot" && a.lot === b.lot;
}

/** Move one photo. Empty lots are removed. At least one lot remains. */
export function moveArrangePhoto(state: ArrangeState, from: ArrangeRef, to: ArrangeRef): ArrangeState {
  const next = cloneState(state);
  const index = take(next, from);
  if (index == null) return state;
  let dest = to;
  if (sameList(from, to) && from.pos < to.pos) {
    dest = { ...to, pos: to.pos - 1 };
  }
  if (dest.where === "omitted") {
    const pos = Math.max(0, Math.min(dest.pos, next.omitted.length));
    next.omitted.splice(pos, 0, index);
  } else {
    const photos = next.lots[dest.lot];
    if (!photos) return state;
    const pos = Math.max(0, Math.min(dest.pos, photos.length));
    photos.splice(pos, 0, index);
  }
  next.lots = next.lots.filter((photos) => photos.length > 0);
  if (!next.lots.length) next.lots.push([]);
  return next;
}

/** Photos from ``pos`` onward become the following lot. */
export function splitArrangeLot(state: ArrangeState, lot: number, pos: number): ArrangeState {
  const photos = state.lots[lot];
  if (!photos || pos <= 0 || pos >= photos.length) return state;
  const next = cloneState(state);
  const moved = next.lots[lot]!.splice(pos);
  next.lots.splice(lot + 1, 0, moved);
  return next;
}

/** One photo becomes its own lot, placed just after the lot it left. */
export function moveToNewLot(state: ArrangeState, from: ArrangeRef): ArrangeState {
  const next = cloneState(state);
  const index = take(next, from);
  if (index == null) return state;
  if (from.where === "lot" && next.lots[from.lot]?.length === 0) {
    next.lots.splice(from.lot, 1, [index]);
    return next;
  }
  const insertAt = from.where === "lot" ? from.lot + 1 : next.lots.length;
  next.lots.splice(insertAt, 0, [index]);
  next.lots = next.lots.filter((photos) => photos.length > 0);
  if (!next.lots.length) next.lots.push([index]);
  return next;
}

export function mergeArrangeLots(state: ArrangeState, lot: number): ArrangeState {
  const current = state.lots[lot];
  const following = state.lots[lot + 1];
  if (!current || !following) return state;
  const next = cloneState(state);
  next.lots[lot] = [...current, ...following];
  next.lots.splice(lot + 1, 1);
  return next;
}

/** ``arranged/lot-N/leaf``. ``N`` is 1-based. Cataloging splits on ``lot-N`` only. */
export function arrangedRel(lotNumber: number, label: string): string {
  const leaf = label.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "photo.jpg";
  return `arranged/lot-${lotNumber}/${leaf}`;
}
