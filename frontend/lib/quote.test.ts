import assert from "node:assert/strict";
import test from "node:test";
import {
  auctionPrepHours,
  buildQuote,
  DEFAULT_FACTORS,
  royaltyBaseFor,
  settle,
  spacePlanHoursFrom,
  type QuoteInput,
} from "./quote.ts";

const base: QuoteInput = {
  cuft: 1500,
  density: "medium",
  liquidation: "estate",
  includeSorting: true,
  includeCleanout: false,
  includePacking: false,
  includeResettling: false,
  spacePlanHours: 0,
  projectedProceeds: 10000,
  billRate: 75,
  laborTarget: 0.33,
  royaltyRate: 0.06,
  depositRate: 0.2,
  commissionRate: 0.35,
  minimumFee: 2500,
  factors: DEFAULT_FACTORS,
};

test("sorting hours are cubic footage divided by the density factor", () => {
  const quote = buildQuote(base);
  const sorting = quote.lines.find((line) => line.key === "sorting");
  assert.ok(sorting);
  assert.equal(sorting.hours, 1500 / 30);
  assert.equal(sorting.price, 50 * 75);
  assert.equal(sorting.billable, true);
});

test("estate prep uses the estate factor and is not billed twice", () => {
  const quote = buildQuote(base);
  const prep = quote.lines.find((line) => line.key === "estate");
  assert.ok(prep);
  assert.equal(prep.hours, 1500 / 30);
  assert.equal(prep.billable, false);
  assert.equal(quote.prepPrice, 3750);
  assert.equal(quote.percentFee, 3500);
  assert.equal(quote.commission, 3750);
  assert.equal(quote.feeWinner, "prep");
  assert.equal(quote.clientTotal, 3750 + 3750);
});

test("a large sale uses the percent when it beats prep and the minimum", () => {
  const quote = buildQuote({ ...base, projectedProceeds: 40000 });
  assert.equal(quote.percentFee, 14000);
  assert.equal(quote.commission, 14000);
  assert.equal(quote.feeWinner, "percent");
  assert.equal(quote.clientTotal, 3750 + 14000);
});

test("a thin sale uses the minimum", () => {
  const quote = buildQuote({
    ...base,
    cuft: 300,
    projectedProceeds: 1000,
    includeSorting: false,
  });
  assert.equal(quote.feeWinner, "minimum");
  assert.equal(quote.commission, 2500);
});

test("no sale means no commission", () => {
  const quote = buildQuote({ ...base, liquidation: "none", projectedProceeds: 10000 });
  assert.equal(quote.commission, 0);
  assert.equal(quote.feeWinner, "none");
  assert.equal(quote.royaltyBase, 3750);
  assert.equal(quote.clientTotal, 3750);
});

test("royalty base includes proceeds and leaves commission out when proceeds exist", () => {
  const quote = buildQuote(base);
  assert.equal(quote.royaltyBase, 3750 + 10000);
  assert.equal(royaltyBaseFor(3750, 0, 0, 2500), 3750 + 2500);
});

test("packing and cleanout add their own hours", () => {
  const quote = buildQuote({
    ...base,
    liquidation: "none",
    includePacking: true,
    includeCleanout: true,
    spacePlanHours: 4,
  });
  const packing = quote.lines.find((line) => line.key === "packing");
  const cleanout = quote.lines.find((line) => line.key === "cleanout");
  assert.equal(packing?.hours, 1500 / 55);
  assert.equal(cleanout?.hours, 1500 / 90);
  assert.equal(quote.relocation, (1500 / 55) * 75 + 4 * 75);
  assert.ok(quote.lines.some((line) => line.key === "space_plan"));
});

test("zero cubic footage does not divide by zero", () => {
  const quote = buildQuote({ ...base, cuft: 0, liquidation: "none" });
  assert.equal(quote.hours, 0);
  assert.equal(quote.clientTotal, 0);
});

test("auction lot hours replace cubic footage and pickup adds prep", () => {
  const quote = buildQuote({
    ...base,
    liquidation: "auction",
    includeSorting: false,
    projectedLots: 150,
    auctionQuestions: 25,
    pickupDays: 1,
    pickupStaff: 3,
    pickupHoursPerDay: 3,
  });
  const prep = quote.lines.find((line) => line.key === "auction");
  const pickup = quote.lines.find((line) => line.key === "auction_pickup");
  assert.equal(prep?.hours, auctionPrepHours(150) + 2.5);
  assert.equal(prep?.hours, 59.5);
  assert.equal(pickup?.hours, 9);
  assert.equal(quote.hours, 68.5);
  assert.equal(quote.prepPrice, 68.5 * 75);
  assert.equal(quote.hourlyInvoiced, 0);
  assert.equal(quote.feeWinner, "prep");
});

test("auction without lots uses the cubic footage factor", () => {
  const quote = buildQuote({ ...base, liquidation: "auction", includeSorting: false });
  const prep = quote.lines.find((line) => line.key === "auction");
  assert.equal(prep?.hours, 1500 / 30);
  assert.equal(quote.lines.some((line) => line.key === "auction_pickup"), false);
});

test("estate sale day staffing raises the prep floor", () => {
  const quote = buildQuote({
    ...base,
    includeSorting: false,
    saleDays: 2,
    saleStaff: 3,
    saleHoursPerDay: 4,
  });
  const day = quote.lines.find((line) => line.key === "estate_day");
  assert.equal(day?.hours, 24);
  assert.equal(day?.billable, false);
  assert.equal(quote.prepPrice, (50 + 24) * 75);
  assert.equal(quote.commission, quote.prepPrice);
});

test("space plan takeoff uses areas and items with a four hour minimum", () => {
  assert.equal(spacePlanHoursFrom(3, 6, 0), 4);
  const quote = buildQuote({
    ...base,
    liquidation: "none",
    includeSorting: false,
    spacePlanAreas: 10,
    spacePlanItems: 20,
  });
  const plan = quote.lines.find((line) => line.key === "space_plan");
  assert.equal(plan?.hours, (10 * 8 + 20 * 10) / 60);
  assert.equal(quote.relocation, (plan?.hours ?? 0) * 75);
});

test("settlement reprices the fee from actual proceeds and keeps service prices", () => {
  const quote = buildQuote(base);
  const result = settle({
    quote,
    liquidation: "estate",
    inPersonProceeds: 40000,
    onlineProceeds: 0,
    buyerPremium: 500,
    shipping: 100,
    miscRevenue: 0,
    directExpenses: 200,
    actualHours: 40,
    hourlyLaborCost: 22,
    commissionRate: 0.35,
    minimumFee: 2500,
    royaltyRate: 0.06,
  });
  assert.equal(result.commission, 14000);
  assert.equal(result.feeWinner, "percent");
  assert.equal(result.invoiced, 3750 + 14000 + 600);
  assert.equal(result.laborCost, 880);
  assert.equal(result.royaltyBase, 3750 + 40000 + 600);
  assert.equal(result.contribution, result.invoiced - 880 - 200 - result.royalty);
});
