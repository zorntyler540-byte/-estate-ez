import { defineEval } from "@cursor/bdk/evals";

export default defineEval({
  tags: ["smoke"],
  description: "Jarvis stores a short note with the remember tool.",
  async test(t) {
    await t.send("Remember this note: tea at four.");
    t.succeeded();
    t.calledTool("remember");
  },
});
