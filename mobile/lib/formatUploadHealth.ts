/** Short Sync-result suffix from ``GET /sales/{id}/upload-health``. */
export function formatUploadHealthSummary(health: {
  cataloging_count?: number;
  stuck_count?: number;
} | null | undefined): string | null {
  if (!health) return null;
  const cataloging = Number(health.cataloging_count) || 0;
  const stuck = Number(health.stuck_count) || 0;
  const parts: string[] = [];
  if (cataloging > 0) parts.push(`${cataloging} cataloging`);
  if (stuck > 0) parts.push(`${stuck} stuck`);
  return parts.length ? parts.join(", ") : null;
}
