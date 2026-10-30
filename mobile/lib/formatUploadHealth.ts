/** Short Sync-result suffix from ``GET /sales/{id}/upload-health``. */
export function formatUploadHealthSummary(health: {
  cataloging_count?: number;
  stuck_count?: number;
} | null | undefined): string | null {
  if (!health) return null;
  const cataloging = Number(health.cataloging_count) || 0;
  const stuck = Number(health.stuck_count) || 0;
  if (stuck > 0 && cataloging > 0) {
    return `${cataloging} cataloging, ${stuck} stuck — open Jobs on the website`;
  }
  if (stuck > 0) {
    return `${stuck} stuck — open Jobs on the website`;
  }
  if (cataloging > 0) {
    return `${cataloging} still cataloging`;
  }
  return null;
}
