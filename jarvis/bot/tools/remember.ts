import { defineTool } from "@cursor/bdk/tools";
import * as z from "zod/v4";

const KEY = "jarvis:notes";
const MAX_NOTES = 40;

type Note = {
  text: string;
  at: string;
};

function asNotes(value: unknown): Note[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const notes: Note[] = [];
  for (const item of value) {
    if (item === null || typeof item !== "object" || !("text" in item)) {
      continue;
    }
    const text = item.text;
    if (typeof text !== "string" || text.trim() === "") {
      continue;
    }
    const at = "at" in item && typeof item.at === "string" ? item.at : "";
    notes.push({ text, at });
  }
  return notes;
}

export default defineTool({
  description:
    "Save or list short personal notes. Use save when the user asks you to remember something. Use list when they ask what you remember.",
  execution: "server",
  effect: "write",
  inputSchema: z.object({
    action: z.enum(["save", "list"]),
    text: z
      .string()
      .trim()
      .min(1)
      .max(240)
      .optional()
      .describe("Note text. Required when action is save."),
  }),
  async execute({ action, text }, ctx) {
    const notes = asNotes(await ctx.host.kv.get(KEY));
    if (action === "list") {
      return { notes };
    }
    if (text === undefined) {
      return { saved: false, reason: "A note needs text.", notes };
    }
    const next = [...notes, { text, at: new Date().toISOString() }].slice(-MAX_NOTES);
    await ctx.host.kv.put(KEY, next);
    return { saved: true, notes: next };
  },
});
