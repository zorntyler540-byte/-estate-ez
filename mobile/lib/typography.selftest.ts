import { FONT_FAMILY, fontFamilyForWeight, withAppFont } from "./typography";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(fontFamilyForWeight(undefined) === FONT_FAMILY.regular, "default is regular");
assert(fontFamilyForWeight("normal") === FONT_FAMILY.regular, "normal is regular");
assert(fontFamilyForWeight("500") === FONT_FAMILY.regular, "500 stays regular (not loaded)");
assert(fontFamilyForWeight("600") === FONT_FAMILY.semibold, "600 is semibold");
assert(fontFamilyForWeight("700") === FONT_FAMILY.bold, "700 is bold");
assert(fontFamilyForWeight("bold") === FONT_FAMILY.bold, "bold keyword");
assert(fontFamilyForWeight(800) === FONT_FAMILY.extrabold, "800 is extrabold");
assert(fontFamilyForWeight("900") === FONT_FAMILY.extrabold, "900 falls back to extrabold");

const styled = withAppFont({ color: "red", fontSize: 14, fontWeight: "700" });
assert(styled.fontFamily === FONT_FAMILY.bold, "weight picks the face");
assert(!("fontWeight" in styled), "weight is dropped so Android does not fake-bold");
assert(styled.color === "red" && styled.fontSize === 14, "other style keys survive");
assert(withAppFont({ fontFamily: "Menlo", fontWeight: "700" }).fontFamily === "Menlo", "explicit family wins");
assert(withAppFont(undefined).fontFamily === FONT_FAMILY.regular, "unstyled text gets regular");

console.log("typography selftest ok (13 cases)");
