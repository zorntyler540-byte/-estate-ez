/** How many draft photo rows to query at once when opening Queue. */
export const QUEUE_PHOTO_LOAD_BATCH = 4;

/** Thumbs decoded on each Queue card. The rest stay as URIs for Review / Resend. */
export const QUEUE_VISIBLE_THUMBS = 6;

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
