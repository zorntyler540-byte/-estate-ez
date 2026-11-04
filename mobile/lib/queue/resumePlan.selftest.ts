import { namesStillMissing, planResume } from "./resumePlan";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const expected = ["p-01.jpg", "p-02.jpg", "p-04.jpg"];

{
  const plan = planResume({ expected, received: ["p-01.jpg"] });
  assert(plan.action === "resume", "subset resumes");
  if (plan.action === "resume") {
    assert(plan.missing.join() === "p-02.jpg,p-04.jpg", "only missing photos are sent");
  }
}
{
  const plan = planResume({ expected, received: [] });
  assert(plan.action === "resume" && plan.missing.length === 3, "empty batch sends all");
}
{
  const plan = planResume({ expected, received: ["p-01.jpg", "p-02.jpg", "p-04.jpg"] });
  assert(plan.action === "resume" && plan.missing.length === 0, "everything already there");
}
{
  const plan = planResume({ expected, received: ["p-01.jpg", "p-03.jpg"] });
  assert(plan.action === "restart" && plan.reason === "extra_files", "deleted photo → restart");
}
{
  const plan = planResume({ expected, received: null });
  assert(plan.action === "restart" && plan.reason === "unknown", "older API → restart");
}
{
  const plan = planResume({ expected, received: [], folderMissing: true });
  assert(plan.action === "restart" && plan.reason === "folder_missing", "lost batch → restart");
}

assert(namesStillMissing(expected, null) === null, "unknown when server is silent");
assert(namesStillMissing(expected, ["p-04.jpg"])!.join() === "p-01.jpg,p-02.jpg", "missing list");

console.log("resumePlan selftest ok");
