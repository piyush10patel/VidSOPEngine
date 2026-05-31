// Promptfoo custom assertion: every tool in the output must appear in
// the transcript or in any event description. Catches hallucinated tools.
//
// Returns { pass, score, reason } — score is the fraction of grounded tools.

module.exports = (output, context) => {
  let parsed;
  try {
    parsed = JSON.parse(output);
  } catch (e) {
    return { pass: false, score: 0, reason: "Output is not valid JSON" };
  }

  const steps = parsed.sop || parsed.steps || [];
  const allTools = new Set();
  for (const s of steps) {
    const tools = s.objects || s.tools || [];
    for (const t of tools) allTools.add(String(t).toLowerCase().trim());
  }

  if (allTools.size === 0) {
    return { pass: true, score: 1.0, reason: "No tools claimed (vacuously grounded)" };
  }

  const transcript = String(context.vars.transcript || "").toLowerCase();
  const events = String(context.vars.events || "").toLowerCase();
  const corpus = transcript + " " + events;

  const ungrounded = [];
  for (const tool of allTools) {
    // Token-level check: any word in the tool name appearing in the corpus counts as grounded
    const tokens = tool.split(/\s+/).filter(t => t.length > 2);
    const hasMatch = tokens.some(tok => corpus.includes(tok));
    if (!hasMatch) ungrounded.push(tool);
  }

  const score = (allTools.size - ungrounded.length) / allTools.size;
  const pass = ungrounded.length === 0;

  return {
    pass,
    score,
    reason: pass
      ? `All ${allTools.size} tools grounded in source`
      : `Hallucinated tools: ${ungrounded.join(", ")}`,
  };
};
