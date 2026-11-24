import { readdir, readFile, stat } from "node:fs/promises";
import { defineTool } from "@cursor/bdk/tools";
import * as z from "zod/v4";
import { confine } from "../lib/home.js";

const TEXT_CAP = 24_000;
const DIR_CAP = 200;

export default defineTool({
  description:
    "Read a text file or list a directory inside the user home directory. Relative paths start at home. Refuses paths that leave home.",
  execution: "server",
  effect: "read",
  inputSchema: z.object({
    path: z
      .string()
      .optional()
      .describe("File or directory. Omit, or pass ~, to list the home directory."),
  }),
  async execute({ path: requested }) {
    const confined = await confine(requested);
    if (!confined.ok) {
      return { refused: true, reason: confined.reason };
    }
    const info = await stat(confined.path);
    if (info.isDirectory()) {
      const dirents = await readdir(confined.path, { withFileTypes: true });
      dirents.sort((a, b) => a.name.localeCompare(b.name));
      const entries = dirents.slice(0, DIR_CAP).map((entry) => ({
        name: entry.name,
        kind: entry.isDirectory()
          ? "directory"
          : entry.isSymbolicLink()
            ? "symlink"
            : "file",
      }));
      return {
        kind: "directory" as const,
        path: confined.path,
        entries,
        truncated: dirents.length > DIR_CAP,
      };
    }
    if (!info.isFile()) {
      return { refused: true, reason: "That path is not a file or directory." };
    }
    if (info.size > 512_000) {
      return {
        kind: "file" as const,
        path: confined.path,
        binary: false,
        text: "",
        truncated: true,
        reason: "File is too large to read here.",
      };
    }
    const bytes = await readFile(confined.path);
    if (bytes.includes(0)) {
      return {
        kind: "file" as const,
        path: confined.path,
        binary: true,
        bytes: info.size,
      };
    }
    const text = bytes.toString("utf8");
    return {
      kind: "file" as const,
      path: confined.path,
      binary: false,
      text: text.slice(0, TEXT_CAP),
      truncated: text.length > TEXT_CAP,
    };
  },
});
