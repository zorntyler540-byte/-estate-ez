"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { FEE_WINNERS } from "../../../../../components/operations/QuoteSummary";
import { apiFetch } from "../../../../../lib/api";
import { isAuthFailureMessage, type OpsJob, type OpsSettings } from "../../../../../lib/ops";
import { hours, money, percent } from "../../../../../lib/opsFormat";

const BLURBS: Record<string, string> = {
  sorting: "Sort and separate what is kept, donated, sold, or discarded.",
  cleanout: "Remove what is left and leave the property broom-clean as scoped.",
  packing: "Pack the items that are moving.",
  resettling: "Unpack and place items in the new home.",
  space_plan: "Measure and plan the items that move.",
  estate: "Prepare the house for the in-person estate sale.",
  estate_day: "Staff the estate sale day.",
  auction: "Photograph, edit, and list lots for the online auction.",
  auction_pickup: "Staff auction pickup.",
  flash: "Prepare a short in-person sale.",
  flash_day: "Staff the flash sale day."
};

export default function OperationsProposalPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const jobId = params?.id || "";
  const [token, setToken] = useState("");
  const [job, setJob] = useState<OpsJob | null>(null);
  const [locationName, setLocationName] = useState("");
  const [depositRate, setDepositRate] = useState(0.2);
  const [cardFeeRate, setCardFeeRate] = useState(0.04);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      router.replace("/");
      return;
    }
    void Promise.all([
      apiFetch(`/ops/jobs/${jobId}`, t) as Promise<OpsJob>,
      apiFetch("/ops/settings", t) as Promise<OpsSettings>
    ])
      .then(([jobData, settings]) => {
        setJob(jobData);
        setLocationName(settings.rates.location_name);
        setDepositRate(settings.rates.deposit_rate);
        setCardFeeRate(settings.rates.card_fee_rate);
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load proposal";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          router.replace("/");
          return;
        }
        setError(msg);
      });
  }, [jobId, router]);

  if (!token) return null;
  const quote = job?.quote;

  return (
    <main className="ct-ops-proposal">
      <div className="ct-ops-no-print ct-ops-proposal__toolbar">
        <Link href={`/operations/jobs/${jobId}`}>Back to job</Link>
        <button type="button" className="ct-btn ct-btn--ghost" onClick={() => window.print()}>
          Print
        </button>
      </div>
      {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
      {job ? (
        <article className="ct-ops-proposal__paper">
          <p className="ct-ops-kicker">{locationName}</p>
          <h1 className="ct-page-title">Estimate of services</h1>
          <p>{job.client_name}</p>
          <p className="ct-muted">{[job.address, job.zip].filter(Boolean).join(", ") || "Address to be confirmed"}</p>
          {job.phone || job.email ? <p className="ct-muted">{[job.phone, job.email].filter(Boolean).join(" · ")}</p> : null}
          <p>
            {job.cuft > 0 ? `${job.cuft.toLocaleString("en-US")} cubic feet` : "Cubic footage to be confirmed"}
            {job.cuft > 0 ? `, ${job.density} density` : ""}
            {job.liquidation === "auction" && job.projected_lots > 0
              ? `, ${job.projected_lots.toLocaleString("en-US")} lots`
              : ""}
            . Hours are estimated from this location&apos;s pace. The final invoice follows actual hours and actual sale
            proceeds.
          </p>
          {quote ? (
            <>
              <ul className="ct-ops-proposal__lines">
                {quote.lines.map((line) => (
                  <li key={line.key}>
                    <strong>{line.label}</strong>
                    <span className="ct-muted">
                      {" "}
                      — {BLURBS[line.key] || ""} {hours(line.hours)} hours
                      {line.billable ? `, ${money(line.price)}` : ", included in the sale fee"}.
                    </span>
                  </li>
                ))}
              </ul>
              <dl className="ct-ops-dl">
                <div className="ct-ops-dl__row">
                  <dt>Hourly services</dt>
                  <dd className="ct-ops-num">{money(quote.hourlyInvoiced)}</dd>
                </div>
                <div className="ct-ops-dl__row">
                  <dt>Sale fee</dt>
                  <dd className="ct-ops-num">{money(quote.commission)}</dd>
                </div>
                <div className="ct-ops-dl__row">
                  <dt>
                    <strong>Estimate</strong>
                  </dt>
                  <dd className="ct-ops-num">
                    <strong>{money(quote.clientTotal)}</strong>
                  </dd>
                </div>
                <div className="ct-ops-dl__row">
                  <dt>Deposit to schedule ({percent(depositRate)})</dt>
                  <dd className="ct-ops-num">{money(quote.deposit)}</dd>
                </div>
              </dl>
              {quote.feeWinner !== "none" ? (
                <p className="ct-muted">
                  The sale fee is the greater of the prep price ({money(quote.prepPrice)}), the commission on expected
                  proceeds ({money(quote.percentFee)}), and the minimum ({money(quote.minimumFee)}). This estimate uses
                  the {FEE_WINNERS[quote.feeWinner]}. Card payments add a {percent(cardFeeRate)} processing fee.
                </p>
              ) : (
                <p className="ct-muted">Card payments add a {percent(cardFeeRate)} processing fee.</p>
              )}
            </>
          ) : (
            <p>Save the job before printing an estimate.</p>
          )}
          <p>
            Send this with your Caring Transitions service agreement. This page is the scope and the numbers. It is not
            the contract.
          </p>
          {job.sale_name ? <p className="ct-muted">Estate-EZ sale: {job.sale_name}</p> : null}
        </article>
      ) : null}
    </main>
  );
}
