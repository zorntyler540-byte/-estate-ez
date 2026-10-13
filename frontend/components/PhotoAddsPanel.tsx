"use client";

import Link from "next/link";

export type PhotoAddLot = {
  lot_id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  added_count: number;
  latest_added_at: string;
  added_by: string[];
  on_ctseller: boolean;
};

export function photoAddsFromResponse(data: unknown): PhotoAddLot[] {
  if (!data || typeof data !== "object" || !("lots" in data)) return [];
  const rows = (data as { lots: unknown }).lots;
  if (!Array.isArray(rows)) return [];
  const out: PhotoAddLot[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const r = row as Partial<PhotoAddLot>;
    if (typeof r.lot_id !== "string" || typeof r.added_count !== "number" || r.added_count < 1) continue;
    out.push({
      lot_id: r.lot_id,
      sale_id: typeof r.sale_id === "string" ? r.sale_id : "",
      sku: typeof r.sku === "string" && r.sku.trim() ? r.sku : null,
      auction_title: typeof r.auction_title === "string" ? r.auction_title : "",
      added_count: r.added_count,
      latest_added_at: typeof r.latest_added_at === "string" ? r.latest_added_at : "",
      added_by: Array.isArray(r.added_by) ? r.added_by.filter((email): email is string => typeof email === "string") : [],
      on_ctseller: r.on_ctseller === true
    });
  }
  return out;
}

function formatAddedWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(d);
}

export function PhotoAddsPanel({
  lots,
  updateBusy = false,
  onUpdatePhotos
}: {
  lots: PhotoAddLot[];
  updateBusy?: boolean;
  onUpdatePhotos?: (ids: string[]) => void;
}) {
  if (lots.length < 1) return null;
  const photoTotal = lots.reduce((n, row) => n + row.added_count, 0);
  const postedIds = lots.filter((row) => row.on_ctseller).map((row) => row.lot_id);
  return (
    <section className="ct-photo-adds" aria-label="Photos added to existing lots">
      <h2 className="ct-photo-adds__title">Photos added to existing lots</h2>
      <p className="ct-photo-adds__note">
        {photoTotal} photo{photoTotal === 1 ? "" : "s"} landed on {lots.length} lot
        {lots.length === 1 ? "" : "s"} that already had photos. These uploads came in at least 2 hours
        after the first photos, in the last 14 days.
        {onUpdatePhotos
          ? " Add photos to CT Seller puts new pictures on the listing already posted. Photos already there stay. Videos on the lot go to the media tab. SKU tape stays off CT Bids."
          : ""}
      </p>
      {onUpdatePhotos && postedIds.length > 0 ? (
        <div className="ct-photo-adds__actions">
          <button
            type="button"
            className="ct-btn ct-btn--primary"
            disabled={updateBusy}
            onClick={() => onUpdatePhotos(postedIds)}
          >
            {updateBusy ? "Adding photos…" : `Add photos to CT Seller (${postedIds.length})`}
          </button>
        </div>
      ) : null}
      <ul className="ct-photo-adds__list">
        {lots.map((row) => {
          const when = formatAddedWhen(row.latest_added_at);
          const who = row.added_by.length ? `by ${row.added_by.join(", ")}` : "";
          return (
            <li key={row.lot_id} className="ct-photo-adds__row">
              <Link href={`/lots/${row.lot_id}`} className="ct-photo-adds__sku">
                {row.sku || "Open lot"}
              </Link>
              <span className="ct-photo-adds__title-text">{row.auction_title || "Untitled lot"}</span>
              <span className="ct-photo-adds__count">
                +{row.added_count} photo{row.added_count === 1 ? "" : "s"}
              </span>
              {when ? <span className="ct-photo-adds__when">{when}</span> : null}
              {who ? <span className="ct-photo-adds__who">{who}</span> : null}
              {onUpdatePhotos ? (
                row.on_ctseller ? (
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost ct-photo-adds__action"
                    disabled={updateBusy}
                    onClick={() => onUpdatePhotos([row.lot_id])}
                  >
                    Add to CT Seller
                  </button>
                ) : (
                  <span className="ct-photo-adds__who">Not on CT Seller yet</span>
                )
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
