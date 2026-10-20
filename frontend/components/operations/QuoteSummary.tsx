"use client";

import { hours, money, percent } from "../../lib/opsFormat";
import type { Quote, SettlementResult } from "../../lib/quote";

export const FEE_WINNERS = {
  none: "No sale fee",
  prep: "prep price",
  percent: "percent of proceeds",
  minimum: "minimum fee"
};

export function QuoteSummary({
  quote,
  laborTarget,
  settlement
}: {
  quote: Quote;
  laborTarget: number;
  settlement?: SettlementResult | null;
}) {
  const settled = Boolean(settlement);
  const saleFee = settlement?.commission ?? quote.commission;
  const clientTotal = settlement ? quote.hourlyInvoiced + settlement.commission : quote.clientTotal;
  const laborCost = settlement?.laborCost ?? quote.laborCost;
  const laborPercent = settlement?.laborPercent ?? quote.laborPercent;
  const royaltyBase = settlement?.royaltyBase ?? quote.royaltyBase;
  const royalty = settlement?.royalty ?? quote.royalty;
  const percentFee = settlement?.percentFee ?? quote.percentFee;
  const feeWinner = settlement?.feeWinner ?? quote.feeWinner;
  const laborBase = settlement ? settlement.invoiced : quote.clientTotal;

  return (
    <aside className="ct-card ct-card--padded ct-ops-summary">
      <p className="ct-ops-kicker">{settled ? "Settled client total" : "Client total"}</p>
      <p className="ct-ops-hero-value">{money(clientTotal)}</p>
      {settled ? (
        <p className="ct-muted">Sale fee uses actual settlement proceeds.</p>
      ) : (
        <p className="ct-muted">Deposit to schedule {money(quote.deposit)}</p>
      )}
      <dl className="ct-ops-dl">
        <Row label="Hourly services" value={money(quote.hourlyInvoiced)} />
        <Row label="Sale fee" value={money(saleFee)} />
        <Row label="Hours" value={hours(quote.hours)} />
        <Row label={settled ? "Labor cost" : "Estimated labor"} value={money(laborCost)} />
        <Row label="Labor" value={percent(laborPercent)} warn={laborPercent > laborTarget && laborBase > 0} />
        <Row label="Royalty base" value={money(royaltyBase)} />
        <Row label="Royalty" value={money(royalty)} />
      </dl>
      {feeWinner !== "none" ? (
        <p className="ct-field-hint">
          The sale fee is the greater of prep ({money(quote.prepPrice)}), {money(percentFee)} from{" "}
          {settled ? "actual" : "expected"} proceeds, and the {money(quote.minimumFee)} minimum. This{" "}
          {settled ? "settlement" : "quote"} uses the {FEE_WINNERS[feeWinner]}. Prep hours still count as labor.
        </p>
      ) : null}
    </aside>
  );
}

function Row({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="ct-ops-dl__row">
      <dt className="ct-muted">{label}</dt>
      <dd className={`ct-ops-num${warn ? " ct-ops-warn" : ""}`}>{value}</dd>
    </div>
  );
}
