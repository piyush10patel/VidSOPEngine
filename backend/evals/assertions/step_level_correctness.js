// Promptfoo assertion: step-level correctness.
//
// Aligns predicted steps to expected steps by title/description similarity,
// then scores each pair on action match + tool grounding. Returns a
// per-step breakdown in the reason and an aggregate score.
//
// Weight: 3 (most important assertion for A/B testing).

function tokenize(text) {
  return new Set(
    (text || "").toLowerCase().match(/[a-z0-9]{3,}/g) || []
  );
}

function jaccard(a, b) {
  const setA = tokenize(a);
  const setB = tokenize(b);
  if (setA.size === 0 && setB.size === 0) return 0;
  const inter = new Set([...setA].filter(x => setB.has(x)));
  const union = new Set([...setA, ...setB]);
  return inter.size / union.size;
}

function stepSimilarity(pred, exp) {
  const titleSim = jaccard(pred.title || "", exp.title || "");
  const descSim = jaccard(
    pred.instruction || pred.description || "",
    exp.instruction || exp.description || ""
  );
  const predTools = (pred.objects || pred.tools || []).join(" ");
  const expTools = (exp.objects || exp.tools || []).join(" ");
  const toolSim = jaccard(predTools, expTools);
  return 0.4 * titleSim + 0.4 * descSim + 0.2 * toolSim;
}

module.exports = (output, context) => {
  let predicted, expected;
  try {
    predicted = JSON.parse(output);
    expected = JSON.parse(context.vars.expected_output);
  } catch (e) {
    return { pass: false, score: 0, reason: "Could not parse JSON" };
  }

  const predSteps = predicted.sop || predicted.steps || [];
  const expSteps = expected.steps || [];

  if (expSteps.length === 0) {
    return {
      pass: predSteps.length === 0,
      score: predSteps.length === 0 ? 1 : 0,
      reason: "Expected 0 steps"
    };
  }

  // Build similarity matrix
  const simMatrix = predSteps.map(p =>
    expSteps.map(e => stepSimilarity(p, e))
  );

  // Greedy bipartite matching
  const MIN_SIM = 0.15;
  const candidates = [];
  for (let i = 0; i < predSteps.length; i++) {
    for (let j = 0; j < expSteps.length; j++) {
      if (simMatrix[i][j] >= MIN_SIM) {
        candidates.push({ sim: simMatrix[i][j], pi: i, ei: j });
      }
    }
  }
  candidates.sort((a, b) => b.sim - a.sim);

  const usedPred = new Set();
  const usedExp = new Set();
  const matches = [];

  for (const { sim, pi, ei } of candidates) {
    if (usedPred.has(pi) || usedExp.has(ei)) continue;
    matches.push({ pi, ei, sim });
    usedPred.add(pi);
    usedExp.add(ei);
  }

  // Score components
  const matchedCount = matches.length;
  const insertions = predSteps.length - matchedCount;  // hallucinated steps
  const deletions = expSteps.length - matchedCount;    // missing steps

  // Precision: fraction of predicted steps that matched
  const precision = predSteps.length > 0 ? matchedCount / predSteps.length : 0;
  // Recall: fraction of expected steps found
  const recall = expSteps.length > 0 ? matchedCount / expSteps.length : 0;
  // F1
  const f1 = (precision + recall) > 0
    ? (2 * precision * recall) / (precision + recall)
    : 0;

  // Average similarity of matched pairs
  const avgSim = matchedCount > 0
    ? matches.reduce((s, m) => s + m.sim, 0) / matchedCount
    : 0;

  // Composite score: F1 weighted by average match quality
  const score = f1 * 0.6 + avgSim * 0.4;

  // Build per-step breakdown
  const breakdown = matches
    .sort((a, b) => a.pi - b.pi)
    .map(m => {
      const ps = predSteps[m.pi];
      const es = expSteps[m.ei];
      return `  Step ${m.pi + 1} → Expected ${m.ei + 1} (sim=${m.sim.toFixed(2)}): "${(ps.title || "").substring(0, 40)}" ↔ "${(es.title || "").substring(0, 40)}"`;
    });

  if (insertions > 0) {
    for (let i = 0; i < predSteps.length; i++) {
      if (!usedPred.has(i)) {
        breakdown.push(`  Step ${i + 1} → HALLUCINATED: "${(predSteps[i].title || "").substring(0, 40)}"`);
      }
    }
  }
  if (deletions > 0) {
    for (let j = 0; j < expSteps.length; j++) {
      if (!usedExp.has(j)) {
        breakdown.push(`  Expected ${j + 1} → MISSING: "${(expSteps[j].title || "").substring(0, 40)}"`);
      }
    }
  }

  const reason = [
    `Matched ${matchedCount}/${expSteps.length} expected steps`,
    `P=${precision.toFixed(2)} R=${recall.toFixed(2)} F1=${f1.toFixed(2)} AvgSim=${avgSim.toFixed(2)}`,
    `Insertions (hallucinated): ${insertions}, Deletions (missing): ${deletions}`,
    ...breakdown,
  ].join("\n");

  return {
    pass: score >= 0.5,
    score,
    reason,
  };
};
