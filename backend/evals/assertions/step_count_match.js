// Promptfoo assertion: predicted step count should be within 50% of expected.
// Penalises both over-decomposition and missing steps.

module.exports = (output, context) => {
  let predicted;
  let expected;
  try {
    predicted = JSON.parse(output);
    expected = JSON.parse(context.vars.expected_output);
  } catch (e) {
    return { pass: false, score: 0, reason: "Could not parse JSON" };
  }

  const predSteps = (predicted.sop || predicted.steps || []).length;
  const expSteps = (expected.steps || []).length;

  if (expSteps === 0) {
    return { pass: predSteps === 0, score: predSteps === 0 ? 1 : 0, reason: "Expected 0 steps" };
  }

  const ratio = Math.abs(predSteps - expSteps) / expSteps;
  const score = Math.max(0, 1 - ratio);
  const pass = ratio <= 0.5;

  return {
    pass,
    score,
    reason: `Expected ${expSteps} steps, got ${predSteps} (${(ratio * 100).toFixed(0)}% off)`,
  };
};
