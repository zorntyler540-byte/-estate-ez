import type { Quote, SettlementResult } from "./quote";

export const OPS_STATUSES = ["lead", "quoted", "proposal", "signed", "in_progress", "settled"] as const;
export type OpsJobStatus = (typeof OPS_STATUSES)[number];

export const OPS_STATUS_LABELS: Record<OpsJobStatus, string> = {
  lead: "Lead",
  quoted: "Quoted",
  proposal: "Proposal sent",
  signed: "Signed",
  in_progress: "In progress",
  settled: "Settled"
};

export type OpsSettlement = {
  job_id: string;
  actual_hours: number;
  in_person_proceeds: number;
  online_proceeds: number;
  buyer_premium: number;
  shipping: number;
  misc_revenue: number;
  direct_expenses: number;
  deposit_received: number;
  balance_received: number;
  hourly_labor_cost: number;
  commission_rate: number;
  minimum_fee: number;
  royalty_rate: number;
  settled_on: string;
};

export type OpsJob = {
  id: string;
  account_id: string;
  client_name: string;
  phone: string;
  email: string;
  address: string;
  zip: string;
  cuft: number;
  density: "low" | "medium" | "high" | "hoarder";
  liquidation: "none" | "estate" | "auction" | "flash";
  include_sorting: boolean;
  include_cleanout: boolean;
  include_packing: boolean;
  include_resettling: boolean;
  space_plan_hours: number;
  space_plan_areas: number;
  space_plan_items: number;
  projected_proceeds: number;
  projected_lots: number;
  auction_questions: number;
  pickup_days: number;
  pickup_staff: number;
  pickup_hours_per_day: number;
  sale_days: number;
  sale_staff: number;
  sale_hours_per_day: number;
  sale_id: string | null;
  sale_name: string | null;
  sale_lot_count: number | null;
  status: OpsJobStatus;
  notes: string;
  quote: Quote | null;
  settlement: OpsSettlement | null;
  settlement_result: SettlementResult | null;
  client_total: number | null;
  suggested_factor: number | null;
  suggested_service: string | null;
  created_at: string;
  settled_on: string | null;
};

export type OpsJobWrite = {
  client_name: string;
  phone: string;
  email: string;
  address: string;
  zip: string;
  cuft: number;
  density: OpsJob["density"];
  liquidation: OpsJob["liquidation"];
  include_sorting: boolean;
  include_cleanout: boolean;
  include_packing: boolean;
  include_resettling: boolean;
  space_plan_hours: number;
  space_plan_areas: number;
  space_plan_items: number;
  projected_proceeds: number;
  projected_lots: number;
  auction_questions: number;
  pickup_days: number;
  pickup_staff: number;
  pickup_hours_per_day: number;
  sale_days: number;
  sale_staff: number;
  sale_hours_per_day: number;
  sale_id: string | null;
  status: OpsJobStatus;
  notes: string;
};

export type OpsRates = {
  account_id: string;
  location_name: string;
  owner_name: string;
  bill_rate: number;
  labor_target: number;
  burden_rate: number;
  royalty_rate: number;
  deposit_rate: number;
  card_fee_rate: number;
  commission_rate: number;
  minimum_fee: number;
};

export type OpsEmployee = {
  id: string;
  name: string;
  pay_rate: number;
};

export type OpsSettings = {
  rates: OpsRates;
  factors: Record<string, Record<string, number>>;
  employees: OpsEmployee[];
};

export type OpsMonth = {
  month: string;
  consultations: number;
  marketing_spend: number;
  jobs: Array<{ job: OpsJob; settlement: OpsSettlement; result: SettlementResult }>;
};

export type OpsSaleOption = {
  id: string;
  name: string;
  closed_at?: string | null;
};

export function emptyJobWrite(): OpsJobWrite {
  return {
    client_name: "",
    phone: "",
    email: "",
    address: "",
    zip: "",
    cuft: 0,
    density: "medium",
    liquidation: "none",
    include_sorting: true,
    include_cleanout: false,
    include_packing: false,
    include_resettling: false,
    space_plan_hours: 0,
    space_plan_areas: 0,
    space_plan_items: 0,
    projected_proceeds: 0,
    projected_lots: 0,
    auction_questions: 0,
    pickup_days: 0,
    pickup_staff: 0,
    pickup_hours_per_day: 0,
    sale_days: 0,
    sale_staff: 0,
    sale_hours_per_day: 0,
    sale_id: null,
    status: "quoted",
    notes: ""
  };
}

export function jobToWrite(job: OpsJob): OpsJobWrite {
  return {
    client_name: job.client_name,
    phone: job.phone,
    email: job.email,
    address: job.address,
    zip: job.zip,
    cuft: job.cuft,
    density: job.density,
    liquidation: job.liquidation,
    include_sorting: job.include_sorting,
    include_cleanout: job.include_cleanout,
    include_packing: job.include_packing,
    include_resettling: job.include_resettling,
    space_plan_hours: job.space_plan_hours || 0,
    space_plan_areas: job.space_plan_areas || 0,
    space_plan_items: job.space_plan_items || 0,
    projected_proceeds: job.projected_proceeds,
    projected_lots: job.projected_lots || 0,
    auction_questions: job.auction_questions || 0,
    pickup_days: job.pickup_days || 0,
    pickup_staff: job.pickup_staff || 0,
    pickup_hours_per_day: job.pickup_hours_per_day || 0,
    sale_days: job.sale_days || 0,
    sale_staff: job.sale_staff || 0,
    sale_hours_per_day: job.sale_hours_per_day || 0,
    sale_id: job.sale_id,
    status: job.status,
    notes: job.notes
  };
}

export function isAuthFailureMessage(message: string): boolean {
  const m = message.toLowerCase();
  return m.includes("invalid authentication") || m.includes("401") || m.includes("unauthorized");
}
