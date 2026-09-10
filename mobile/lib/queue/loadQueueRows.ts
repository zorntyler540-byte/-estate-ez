/** Queue first paint must never decode camera JPEGs. Review loads one lot on tap. */
export const QUEUE_OPEN_PHOTO_URIS: string[] = [];

export function queueRowsFromDrafts<T extends { id: string }>(
  drafts: T[]
): Array<T & { photoUris: string[] }> {
  return drafts.map((draft) => ({ ...draft, photoUris: QUEUE_OPEN_PHOTO_URIS }));
}

export async function mapInBatches<T, R>(
  items: T[],
  batchSize: number,
  fn: (item: T) => Promise<R>
): Promise<R[]> {
  const size = Math.max(1, batchSize);
  const out: R[] = [];
  for (let i = 0; i < items.length; i += size) {
    const chunk = items.slice(i, i + size);
    const part = await Promise.all(chunk.map((item) => fn(item)));
    out.push(...part);
  }
  return out;
}
