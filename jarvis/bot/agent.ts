import { homedir } from "node:os";
import { defineAgent } from "@cursor/bdk";

export default defineAgent({
  name: "Jarvis",
  description: "Daily personal assistant for this computer.",
  model: {
    id: "grok-4.5",
    params: [
      { id: "effort", value: "high" },
      { id: "fast", value: "true" },
    ],
  },
  // Session workspaces sit under the home directory, outside this checkout,
  // so the monorepo's rules and skills do not leak into a turn.
  local: {
    cwd: homedir(),
  },
  // Authored tools are the only computer access. An empty harness allowlist
  // keeps the built-in shell from skipping the confirmation gate on `run`.
  tools: [],
});
