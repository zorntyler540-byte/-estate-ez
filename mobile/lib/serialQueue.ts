/**
 * Runs async jobs one at a time in enqueue order. Full-resolution JPEG work is serialized
 * so two re-encodes never hold two 48 MP bitmaps at once.
 */
export class SerialQueue {
  private tail: Promise<void> = Promise.resolve();
  private pending = 0;

  get size(): number {
    return this.pending;
  }

  /** Resolves with the job's result; a failing job never stalls the jobs behind it. */
  enqueue<T>(job: () => Promise<T>): Promise<T> {
    this.pending += 1;
    const run = this.tail.then(job);
    this.tail = run.then(
      () => undefined,
      () => undefined
    ).finally(() => {
      this.pending -= 1;
    });
    return run;
  }

  /** Resolves once every job queued so far has finished. */
  idle(): Promise<void> {
    return this.tail;
  }
}
