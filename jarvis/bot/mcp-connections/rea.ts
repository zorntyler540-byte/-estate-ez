import { defineConnection } from "@cursor/bdk/connections";

// Local stdio MCP for this project only. Do not run `rea-agents setup`:
// that writes the operator's personal agent config.
export default defineConnection({
  description:
    "REA inspects software the user owns or is authorized to examine and returns evidence. It does not install Hopper, IDA, or Ghidra.",
  command: "npx",
  args: ["-y", "rea-agents@6.7.0", "mcp"],
  advertiseTools: true,
});
