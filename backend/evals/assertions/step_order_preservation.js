// Promptfoo assertion: temporal order preservation via LCS.
//
// Aligns predicted steps to expected, then checks if the matched steps
// preserve the expected temporal order using Longest Common Subsequence.

function tokenize(text) {
  return new Set((text || "").toLowerCase().match(/[a-z0-9]{3,}/g) || []);
}

function jaccard(a, b) {
  const setA = tokenize(a);
  const setB = tokenize(b);
  if (setA.size === 0 && setB.size === 0) return 0;
  const inter = new Set([...setA].filter(x => setB.has(x)));
  const union = new Set([...setA, ...setB]);
  return inter.size / union.size;
}

function stepSim(p, e) {
  return 0.4 * jaccard(p.title || "", e.title || "")
       + 0.4 * jaccard(p.instruction || p.description || "", e.instruction || e.description || "")
       + 0.2 * jaccard((p.objects || p.tools || []).join(" "), (e.objects || e.tools || []).join(" "));
}

function lcsLength(a, b) {
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i-1] === b[j-1]
        ? dp[i-1][j-1] + 1
        : Math.max(dp[i-1][j], dp[i][j-1]);
    }
  }
  return dp[n][m];
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

  if (expSteps.length <= 1 || predSteps.length === 0) {
    return { pass: true, score: 1.0, reason: "Trivially ordered" };
  }

  // Greedy match
  const MIN_SIM = 0.15;
  const cands = [];
  for (let i = 0; i < predSteps.length; i++) {
    for (let j = 0; j < expSteps.length; j++) {
      const s = stepSim(predSteps[i], expSteps[j]);
      if (s >= MIN_SIM) cands.push({ s, i, j });
    }
  }
  cands.sort((a, b) => b.s - a.s);

  const usedP = new Set(), usedE = new Set();
  const matches = [];
  for (const { s, i, j } of cands) {
    if (usedP.has(i) || usedE.has(j)) continue;
    matches.push({ pi: i, ei: j, sim: s });
    usedP.add(i);
    usedE.add(j);
  }

  if (matches.length <= 1) {
    return { pass: true, score: 1.0, reason: `Only ${matches.length} match — trivially ordered` };
  }

  // Sort by predicted index, extract expected indices
  matches.sort((a, b) => a.pi - b.pi);
  const expIndices = matches.map(m => m.ei);
  const sortedIndices = [...expIndices].sort((a, b) => a - b);
  const lcs = lcsLength(expIndices, sortedIndices);
  const score = lcs / expIndices.length;

  const outOfOrder = expIndices.length - lcs;
  const reason = outOfOrder === 0
    ? `All ${matches.length} matched steps preserve temporal order`
    : `${outOfOrder}/${matches.length} matched steps are out of order (LCS=${lcs})`;

  return { pass: score >= 0.7, score, reason };
};
