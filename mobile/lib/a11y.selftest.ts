import { MIN_TAP_PX, hitSlopFor, nextAnnouncement } from "./a11y";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

for (const size of [24, 30, 32, 34, 43]) {
  const slop = hitSlopFor(size);
  assert(size + slop.top + slop.bottom >= MIN_TAP_PX, `${size}pt control reaches 44pt tall`);
  assert(size + slop.left + slop.right >= MIN_TAP_PX, `${size}pt control reaches 44pt wide`);
}
assert(hitSlopFor(44).top === 0, "44pt control needs no slop");
assert(hitSlopFor(60).top === 0, "large control needs no slop");
assert(hitSlopFor(-5).top === 22, "bad size treated as 0");

assert(nextAnnouncement(null, "Synced 2 lots.") === "Synced 2 lots.", "first outcome is spoken");
assert(nextAnnouncement("Synced 2 lots.", "Synced 2 lots.") === null, "same outcome is not repeated");
assert(nextAnnouncement("Synced 2 lots.", "  Synced 2 lots.  ") === null, "whitespace does not re-announce");
assert(nextAnnouncement("a", null) === null, "cleared message is silent");
assert(nextAnnouncement("a", "   ") === null, "blank message is silent");
assert(nextAnnouncement("Synced 2 lots.", "Offline") === "Offline", "new outcome is spoken");

console.log("a11y selftest ok (16 cases)");
