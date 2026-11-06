import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineChannel, GET } from "@cursor/bdk/channels";
import * as z from "zod/v4";

const page = join(dirname(fileURLToPath(import.meta.url)), "../../web/index.html");

export default defineChannel({
  routes: [
    GET("/", {
      description: "Daily HUD, served on the same origin as the HTTP session API.",
      querySchema: z.object({}),
      async handler() {
        const html = await readFile(page, "utf8");
        return new Response(html, {
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      },
    }),
  ],
});
