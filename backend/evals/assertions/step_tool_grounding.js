// Promptfoo assertion: per-step tool grounding.
//
// Reports WHICH STEP has hallucinated tools for granular A/B visibility.

module.exports = (output, context) => {
  let parsed;
  try {
    parsed = JSON.parse(output);
  } catch (e) {
    return { pass: false, score: 0, reason: "Output is not valid JSON" };
  }

  const steps = parsed.sop || parsed.steps || [];
  const transcript = String(context.vars.transcript || "").toLowerCase();
  const events = String(context.vars.events || "").toLowerCase();
  const corpus = transcript + " " + events;

  if (steps.length === 0) {
    return { pass: true, score: 1.0, reason: "No steps to check" };
  }

  let totalTools = 0;
  let totalGrounded = 0;
  const badSteps = [];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const tools = step.objects || step.tools || [];
    if (tools.length === 0) continue;

    const hallucinated = [];
    for (const tool of tools) {
      const tokens = String(tool).toLowerCase().split(/\s+/).filter(t => t.length > 2);
      const hasMatch = tokens.some(tok => corpus.includes(tok));
      if (hasMatch) {
        totalGrounded++;
      } else {
        hallucinated.push(tool);
      }
      totalTools++;
    }

    if (hallucinated.length > 0) {
      badSteps.push(`Step ${i+1} ("${(step.title||"").substring(0,40)}"): [${hallucinated.join(", ")}]`);
    }
  }

  const score = totalTools > 0 ? totalGrounded / totalTools : 1.0;
  const reason = badSteps.length === 0
    ? `All ${totalTools} tools grounded across ${steps.length} steps`
    : `${badSteps.length} steps with hallucinated tools:\n  ${badSteps.join("\n  ")}`;

  return { pass: badSteps.length === 0, score, reason };
};
