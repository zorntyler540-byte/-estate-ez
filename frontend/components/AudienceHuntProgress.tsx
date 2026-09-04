"use client";

export type AudienceHuntLotProgress = {
  lot_id: string;
  sku?: string | null;
  status: string;
  phase?: string | null;
};

export function audienceHuntPhaseLabel(phase?: string | null): string {
  if (phase === "structuring") return "Writing group list and blurbs…";
  if (phase === "searching") return "Searching public collector groups…";
  if (phase === "queued") return "Waiting in the hunt queue…";
  return "Finding public collector groups…";
}

export function audienceHuntStepPercent(phase?: string | null): number {
  if (phase === "structuring") return 75;
  if (phase === "searching") return 40;
  if (phase === "queued") return 8;
  return 20;
}

type AudienceHuntProgressProps = {
  title?: string;
  phaseLabel: string;
  done?: number;
  total?: number;
  percent?: number;
  indeterminate?: boolean;
  lots?: AudienceHuntLotProgress[];
};

export function AudienceHuntProgress({
  title = "Audience hunt",
  phaseLabel,
  done = 0,
  total = 0,
  percent,
  indeterminate = false,
  lots
}: AudienceHuntProgressProps) {
  const hasCount = total > 0;
  const pct =
    typeof percent === "number"
      ? Math.max(0, Math.min(100, Math.round(percent)))
      : hasCount
        ? Math.min(100, Math.round((done / total) * 100))
        : 0;
  const sliding = Boolean(indeterminate) || (!hasCount && typeof percent !== "number");

  return (
    <div className="ct-photo-progress ct-photo-progress--audience" aria-live="polite">
      <div className="ct-photo-progress__head">
        <span className="ct-photo-progress__title">{title}</span>
        <span className="ct-photo-progress__count">
          {hasCount ? `${Math.min(done, total)} / ${total}` : "…"}
        </span>
      </div>
      <div
        className="ct-photo-progress__bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={sliding ? undefined : pct}
        aria-label="Audience hunt progress"
      >
        <div
          className={`ct-photo-progress__bar-fill${sliding ? " ct-photo-progress__bar-fill--indeterminate" : ""}`}
          style={sliding ? undefined : { width: `${pct}%` }}
        />
      </div>
      <p className="ct-photo-progress__phase">{phaseLabel}</p>
      {lots && lots.length > 1 ? (
        <details className="ct-photo-progress__details">
          <summary>
            Per-lot status ({lots.length})
          </summary>
          <ul className="ct-photo-progress__list">
            {lots.map((row, index) => (
              <li
                key={row.lot_id}
                className={`ct-photo-progress__item${
                  row.status === "queued"
                    ? " ct-photo-progress__item--queued"
                    : row.status === "done"
                      ? " ct-photo-progress__item--done"
                      : ""
                }`}
              >
                <span className="ct-photo-progress__item-icon" aria-hidden>
                  {row.status === "done" ? "✓" : row.status === "running" ? "●" : "…"}
                </span>
                <span className="ct-photo-progress__item-text">
                  <strong>
                    {index + 1}/{lots.length}
                  </strong>
                  <span className="ct-photo-progress__item-file"> · {row.sku || "lot"}</span>
                  <span className="ct-photo-progress__item-note">
                    {" "}
                    —{" "}
                    {row.status === "done"
                      ? "Done"
                      : row.status === "running"
                        ? audienceHuntPhaseLabel(row.phase)
                        : "Waiting…"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
