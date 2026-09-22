/** Query string for ``GET /team/activity``. ``1`` is local calendar today, not a rolling 24h. */
export function teamActivityQuery(sinceDays: number): string {
  const days = Math.max(1, Math.min(366, Math.floor(sinceDays)));
  if (days === 1) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const qs = new URLSearchParams({
      since: start.toISOString(),
      until: new Date().toISOString()
    });
    return qs.toString();
  }
  return `since_days=${days}`;
}
