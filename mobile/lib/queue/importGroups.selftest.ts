import {
  cameraShotNumber,
  exifTakenAtMs,
  groupStartKeys,
  importCapturedAt,
  orderPicks,
  parseExifDate,
  planImportGroups,
  skuSequence,
  type ImportPick
} from "./importGroups";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const T0 = Date.UTC(2026, 4, 1, 17, 0, 0);
const pick = (i: number, takenAt: number | null, key = `k${i}`): ImportPick => ({
  key,
  uri: `file:///picker/${key}.jpg`,
  takenAt,
  pickIndex: i
});

// EXIF dates: iOS nests under {Exif}, Android is flat; offsets are honored.
assert(parseExifDate("2026:05:01 17:00:00", "+00:00") === T0, "utc offset");
assert(parseExifDate("2026:05:01 13:00:00", "-04:00") === T0, "negative offset");
assert(parseExifDate("garbage") === null, "bad date");
assert(parseExifDate(undefined) === null, "missing date");
assert(exifTakenAtMs({ "{Exif}": { DateTimeOriginal: "2026:05:01 17:00:00", OffsetTimeOriginal: "+00:00" } }) === T0, "ios nested");
assert(exifTakenAtMs({ DateTimeOriginal: "2026:05:01 17:00:00", OffsetTime: "+00:00" }) === T0, "android flat");
assert(exifTakenAtMs({ "{TIFF}": { DateTime: "2026:05:01 17:00:00" } }) != null, "tiff fallback");
assert(exifTakenAtMs(null) === null && exifTakenAtMs({}) === null, "no exif");

// Gap mode sorts by capture time; one-lot mode keeps pick order.
{
  const picks = [pick(0, T0 + 5000), pick(1, T0), pick(2, T0 + 1000)];
  assert(orderPicks(picks, "gap").map((p) => p.key).join() === "k1,k2,k0", "gap sorts by time");
  assert(orderPicks(picks, "one").map((p) => p.key).join() === "k0,k1,k2", "one keeps pick order");
  const undated = [pick(0, T0 + 5000), pick(1, null), pick(2, T0)];
  assert(orderPicks(undated, "gap").map((p) => p.key).join() === "k0,k1,k2", "missing date keeps pick order");
}

// Time gaps split lots; a manual toggle adds a split or joins an automatic one back.
{
  const picks = [
    pick(0, T0),
    pick(1, T0 + 10_000),
    pick(2, T0 + 5 * 60_000), // new lot (gap)
    pick(3, T0 + 5 * 60_000 + 5_000),
    pick(4, T0 + 5 * 60_000 + 9_000)
  ];
  const auto = planImportGroups(picks, { mode: "gap", gapMs: 2 * 60_000 });
  assert(auto.length === 2 && auto[0]!.picks.length === 2 && auto[1]!.picks.length === 3, "gap split");
  const extra = planImportGroups(picks, { mode: "gap", gapMs: 2 * 60_000, toggled: new Set(["k4"]) });
  assert(extra.map((g) => g.picks.length).join() === "2,2,1", "manual split");
  assert(extra.map((g) => g.start).join() === "first,auto,manual", "start reasons");
  const joined = planImportGroups(picks, { mode: "gap", gapMs: 2 * 60_000, toggled: new Set(["k2"]) });
  assert(joined.length === 1 && joined[0]!.picks.length === 5, "toggle joins the automatic split");
  const one = planImportGroups(picks, { mode: "one" });
  assert(one.length === 1, "one lot ignores gaps");
  assert(groupStartKeys(extra).has("k4") && groupStartKeys(extra).has("k0"), "group starts");
  assert(planImportGroups(picks, { mode: "one", toggled: new Set(["k0"]) }).length === 1, "first photo never splits");
}

// The per-lot cap cuts big lots into flagged parts; nothing is dropped.
{
  const picks = Array.from({ length: 95 }, (_, i) => pick(i, T0 + i * 1000));
  const groups = planImportGroups(picks, { mode: "one" });
  assert(groups.map((g) => g.picks.length).join() === "40,40,15", "cap parts");
  assert(groups.every((g) => g.capped), "parts flagged");
  assert(groups.map((g) => g.start).join() === "first,cap,cap", "cap starts cannot be joined");
  assert(groups.reduce((n, g) => n + g.picks.length, 0) === 95, "no photo dropped");
  const small = planImportGroups(picks.slice(0, 40), { mode: "one" });
  assert(small.length === 1 && !small[0]!.capped, "exactly 40 is one lot");
}

// Camera file numbers beat a scrambled picker, including IMG_9 before IMG_10.
{
  const named = (i: number, fileName: string): ImportPick => ({ ...pick(i, null, fileName), fileName });
  const picks = [named(0, "IMG_3840.JPG"), named(1, "IMG_3839.JPG"), named(2, "IMG_10.JPG"), named(3, "IMG_9.JPG")];
  assert(orderPicks(picks, "one").map((p) => p.fileName).join() === "IMG_9.JPG,IMG_10.JPG,IMG_3839.JPG,IMG_3840.JPG", "camera order");
  assert(cameraShotNumber("IMG_3839.JPG") === 3839 && cameraShotNumber("note.txt") === null, "shot number");
  const suggested = planImportGroups(picks, { mode: "one", suggested: new Set(["IMG_3839.JPG"]) });
  assert(suggested.length === 2 && suggested[1]!.picks[0]!.fileName === "IMG_3839.JPG", "suggested tape split");
  const undone = planImportGroups(picks, {
    mode: "one",
    suggested: new Set(["IMG_3839.JPG"]),
    toggled: new Set(["IMG_3839.JPG"])
  });
  assert(undone.length === 1, "toggle undoes a suggestion");
}
assert(skuSequence("1001", 3)?.join() === "1001,1002,1003", "plain sku run");
assert(skuSequence("DK99", 2)?.join() === "DK99,DK100", "prefix grows");
assert(skuSequence("nope", 2) === null, "sku needs a number");

assert(importCapturedAt(pick(0, T0), 3, 99) === T0, "exif time wins");
assert(importCapturedAt(pick(0, null), 3, 1000) === 1003, "fallback keeps order");

console.log("importGroups selftest ok");
