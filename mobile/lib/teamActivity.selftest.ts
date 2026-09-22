import { teamActivityQuery } from "./teamActivity";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(teamActivityQuery(7) === "since_days=7", "7d query");
assert(teamActivityQuery(30) === "since_days=30", "30d query");
assert(teamActivityQuery(900) === "since_days=366", "clamp high");
assert(teamActivityQuery(1).includes("since="), "today uses since/until");
assert(teamActivityQuery(1).includes("until="), "today has until");
assert(!teamActivityQuery(1).includes("since_days="), "today is not rolling 24h");

console.log("teamActivity selftest ok (6 cases)");
