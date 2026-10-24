export const DENSITIES = ["low", "medium", "high", "hoarder"] as const;
export type Density = (typeof DENSITIES)[number];

export const LIQUIDATIONS = ["none", "estate", "auction", "flash"] as const;
export type Liquidation = (typeof LIQUIDATIONS)[number];

export const SERVICE_KEYS = [
  "sorting",
  "cleanout",
  "packing",
  "resettling",
  "estate",
  "auction",
  "flash",
] as const;
export type ServiceKey = (typeof SERVICE_KEYS)[number];

export type Factors = Record<ServiceKey, Record<Density, number>>;

export const DEFAULT_FACTORS: Factors = {
  sorting: { low: 50, medium: 30, high: 15, hoarder: 10 },
  cleanout: { low: 130, medium: 90, high: 45, hoarder: 12 },
  packing: { low: 80, medium: 55, high: 40, hoarder: 20 },
  resettling: { low: 70, medium: 45, high: 30, hoarder: 15 },
  estate: { low: 50, medium: 30, high: 16, hoarder: 7 },
  auction: { low: 50, medium: 30, high: 16, hoarder: 7 },
  flash: { low: 200, medium: 130, high: 80, hoarder: 56 },
};

export const SERVICE_LABELS: Record<ServiceKey, string> = {
  sorting: "Sorting and organizing",
  cleanout: "Cleanout",
  packing: "Packing",
  resettling: "Resettling",
  estate: "Estate sale prep",
  auction: "Online auction prep",
  flash: "Flash sale prep",
};

export const DENSITY_LABELS: Record<Density, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  hoarder: "Hoarder",
};

export const LIQUIDATION_LABELS: Record<Liquidation, string> = {
  none: "No sale",
  estate: "In-person estate sale",
  auction: "Online auction",
  flash: "Flash sale",
};

export type QuoteBucket = "setup" | "cleanout" | "relocation" | "liquidation";

export type QuoteLine = {
  key: string;
  label: string;
  hours: number;
  price: number;
  bucket: QuoteBucket;
  billable: boolean;
};

export type QuoteInput = {
  cuft: number;
  density: Density;
  liquidation: Liquidation;
  includeSorting: boolean;
  includeCleanout: boolean;
  includePacking: boolean;
  includeResettling: boolean;
  spacePlanHours: number;
  projectedProceeds: number;
  billRate: number;
  laborTarget: number;
  royaltyRate: number;
  depositRate: number;
  commissionRate: number;
  minimumFee: number;
  factors: Factors;
};

export type Quote = {
  lines: QuoteLine[];
  hours: number;
  laborCost: number;
  hourlyInvoiced: number;
  prepPrice: number;
  proceeds: number;
  percentFee: number;
  minimumFee: number;
  commission: number;
  feeWinner: "none" | "prep" | "percent" | "minimum";
  clientTotal: number;
  deposit: number;
  royaltyBase: number;
  royalty: number;
  laborPercent: number;
  setup: number;
  cleanout: number;
  relocation: number;
};

export type SettlementInput = {
  quote: Quote;
  liquidation: Liquidation;
  inPersonProceeds: number;
  onlineProceeds: number;
  buyerPremium: number;
  shipping: number;
  miscRevenue: number;
  directExpenses: number;
  actualHours: number;
  hourlyLaborCost: number;
  commissionRate: number;
  minimumFee: number;
  royaltyRate: number;
};

export type SettlementResult = {
  proceeds: number;
  percentFee: number;
  commission: number;
  feeWinner: Quote["feeWinner"];
  invoiced: number;
  laborCost: number;
  laborPercent: number;
  royaltyBase: number;
  royalty: number;
  contribution: number;
  setup: number;
  cleanout: number;
  relocation: number;
  balanceDue: number;
};

export function primaryService(input: {
  liquidation: Liquidation;
  includeSorting: boolean;
  includeCleanout: boolean;
  includePacking: boolean;
  includeResettling: boolean;
}): ServiceKey | null {
  if (input.liquidation !== "none") return input.liquidation;
  if (input.includeSorting) return "sorting";
  if (input.includeCleanout) return "cleanout";
  if (input.includePacking) return "packing";
  if (input.includeResettling) return "resettling";
  return null;
}

function hoursFor(cuft: number, factor: number) {
  if (!(cuft > 0) || !(factor > 0)) return 0;
  return cuft / factor;
}

function sumBucket(lines: QuoteLine[], bucket: QuoteBucket, billableOnly = false) {
  return lines
    .filter((line) => line.bucket === bucket && (!billableOnly || line.billable))
    .reduce((total, line) => total + line.price, 0);
}

function feeFrom(prepPrice: number, proceeds: number, commissionRate: number, minimumFee: number, hasSale: boolean) {
  if (!hasSale) {
    return { percentFee: 0, commission: 0, feeWinner: "none" as const };
  }
  const percentFee = Math.max(0, proceeds) * Math.max(0, commissionRate);
  const minimum = Math.max(0, minimumFee);
  const prep = Math.max(0, prepPrice);
  const commission = Math.max(prep, percentFee, minimum);
  let feeWinner: Quote["feeWinner"] = "minimum";
  if (commission === prep && prep >= percentFee && prep >= minimum) feeWinner = "prep";
  else if (commission === percentFee && percentFee >= minimum) feeWinner = "percent";
  return { percentFee, commission, feeWinner };
}

export function royaltyBaseFor(hourlyInvoiced: number, proceeds: number, extras: number, commission: number) {
  const base = Math.max(0, hourlyInvoiced) + Math.max(0, proceeds) + Math.max(0, extras);
  if (proceeds > 0) return base;
  return base + Math.max(0, commission);
}

export function buildQuote(input: QuoteInput): Quote {
  const billRate = Math.max(0, input.billRate);
  const lines: QuoteLine[] = [];

  const add = (
    key: string,
    label: string,
    hours: number,
    bucket: QuoteBucket,
    billable: boolean,
  ) => {
    if (hours <= 0 && key !== "space_plan") return;
    lines.push({
      key,
      label,
      hours,
      price: hours * billRate,
      bucket,
      billable,
    });
  };

  if (input.includeSorting) {
    add(
      "sorting",
      SERVICE_LABELS.sorting,
      hoursFor(input.cuft, input.factors.sorting[input.density]),
      "setup",
      true,
    );
  }
  if (input.spacePlanHours > 0) {
    add("space_plan", "Space planning", input.spacePlanHours, "relocation", true);
  }
  if (input.includePacking) {
    add(
      "packing",
      SERVICE_LABELS.packing,
      hoursFor(input.cuft, input.factors.packing[input.density]),
      "relocation",
      true,
    );
  }
  if (input.includeResettling) {
    add(
      "resettling",
      SERVICE_LABELS.resettling,
      hoursFor(input.cuft, input.factors.resettling[input.density]),
      "relocation",
      true,
    );
  }
  if (input.includeCleanout) {
    add(
      "cleanout",
      SERVICE_LABELS.cleanout,
      hoursFor(input.cuft, input.factors.cleanout[input.density]),
      "cleanout",
      true,
    );
  }
  if (input.liquidation !== "none") {
    add(
      input.liquidation,
      SERVICE_LABELS[input.liquidation],
      hoursFor(input.cuft, input.factors[input.liquidation][input.density]),
      "liquidation",
      false,
    );
  }

  const hours = lines.reduce((total, line) => total + line.hours, 0);
  const hourlyInvoiced = lines.filter((line) => line.billable).reduce((total, line) => total + line.price, 0);
  const prepPrice = sumBucket(lines, "liquidation");
  const proceeds = input.liquidation === "none" ? 0 : Math.max(0, input.projectedProceeds);
  const priced = feeFrom(prepPrice, proceeds, input.commissionRate, input.minimumFee, input.liquidation !== "none");
  const clientTotal = hourlyInvoiced + priced.commission;
  const laborCost = hours * billRate * Math.max(0, input.laborTarget);
  const royaltyBase = royaltyBaseFor(hourlyInvoiced, proceeds, 0, priced.commission);
  const royalty = royaltyBase * Math.max(0, input.royaltyRate);

  return {
    lines,
    hours,
    laborCost,
    hourlyInvoiced,
    prepPrice,
    proceeds,
    percentFee: priced.percentFee,
    minimumFee: input.liquidation === "none" ? 0 : Math.max(0, input.minimumFee),
    commission: priced.commission,
    feeWinner: priced.feeWinner,
    clientTotal,
    deposit: clientTotal * Math.max(0, input.depositRate),
    royaltyBase,
    royalty,
    laborPercent: clientTotal > 0 ? laborCost / clientTotal : 0,
    setup: sumBucket(lines, "setup", true),
    cleanout: sumBucket(lines, "cleanout", true),
    relocation: sumBucket(lines, "relocation", true),
  };
}

export function settle(input: SettlementInput): SettlementResult {
  const proceeds = Math.max(0, input.inPersonProceeds) + Math.max(0, input.onlineProceeds);
  const hasSale = input.liquidation !== "none" || proceeds > 0 || input.quote.prepPrice > 0;
  const priced = feeFrom(
    input.quote.prepPrice,
    proceeds,
    input.commissionRate,
    input.minimumFee,
    hasSale && input.liquidation !== "none",
  );
  const extras =
    Math.max(0, input.buyerPremium) + Math.max(0, input.shipping) + Math.max(0, input.miscRevenue);
  const invoiced = input.quote.hourlyInvoiced + priced.commission + extras;
  const laborCost = Math.max(0, input.actualHours) * Math.max(0, input.hourlyLaborCost);
  const royaltyBase = royaltyBaseFor(input.quote.hourlyInvoiced, proceeds, extras, priced.commission);
  const royalty = royaltyBase * Math.max(0, input.royaltyRate);
  const direct = Math.max(0, input.directExpenses);
  return {
    proceeds,
    percentFee: priced.percentFee,
    commission: priced.commission,
    feeWinner: priced.feeWinner,
    invoiced,
    laborCost,
    laborPercent: invoiced > 0 ? laborCost / invoiced : 0,
    royaltyBase,
    royalty,
    contribution: invoiced - laborCost - direct - royalty,
    setup: input.quote.setup,
    cleanout: input.quote.cleanout,
    relocation: input.quote.relocation,
    balanceDue: invoiced,
  };
}
