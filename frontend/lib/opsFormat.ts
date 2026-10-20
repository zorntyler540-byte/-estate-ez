export function money(value: number) {
  return value.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function hours(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 0 });
}

export function percent(value: number) {
  return value.toLocaleString("en-US", { style: "percent", maximumFractionDigits: 1 });
}

export function thisMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(month: string) {
  const [year, index] = month.split("-").map(Number);
  if (!year || !index) return month;
  return new Date(year, index - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
}

export function shiftMonth(month: string, delta: number) {
  const [year, index] = month.split("-").map(Number);
  const date = new Date(year, index - 1 + delta, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}
