import { stepCaptureIndex } from "./previewOrder";

const cases: Array<{ index: number; total: number; delta: number; want: number }> = [
  { index: 0, total: 12, delta: 1, want: 1 },
  { index: 11, total: 12, delta: 1, want: 11 },
  { index: 0, total: 12, delta: -1, want: 0 },
  { index: 5, total: 12, delta: -1, want: 4 },
  { index: 0, total: 1, delta: 1, want: 0 },
  { index: 0, total: 0, delta: 1, want: 0 }
];

for (const c of cases) {
  const got = stepCaptureIndex(c.index, c.total, c.delta);
  if (got !== c.want) {
    throw new Error(`${c.index}+${c.delta} of ${c.total} => ${got}, expected ${c.want}`);
  }
}

console.log(`previewOrder selftest ok (${cases.length} cases)`);
