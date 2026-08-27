// 07 - The TypeScript CI slice: dataset -> run -> submit -> finalize -> gate.assert(), in Node.
//
// @agentx/eval is the zero-dependency TS client for exactly this loop - the CI surface only
// (Python remains the full SDK). This script is what a TS repo's eval job looks like: run your
// agent however you like, submit the outputs, and let gate.assert() fail the build on a
// regression.
//
// Until @agentx/eval is published to npm, point AGENTX_EVAL_SDK at a local build of it
// (AgentX-trace-eval: `yarn workspace @agentx/eval build`), default: the sibling checkout.
//
// Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 node 07_ts_ci_gate.mjs

import { pathToFileURL } from "node:url";

const sdkPath =
  process.env.AGENTX_EVAL_SDK ??
  new URL("../../AgentX-trace-eval/packages/agentx-eval/dist/index.js", import.meta.url).pathname;
const { AgentXEval } = await import(pathToFileURL(sdkPath).href);

const baseUrl = process.env.AGENTX_SELFHOST_BASE_URL ?? "http://localhost:4700/api/v1";
const evals = new AgentXEval({ apiKey: process.env.AGENTX_API_KEY ?? "", baseUrl });

const failures = [];
const check = (name, ok, detail = "") => {
  console.log(`  ${ok ? "OK " : "BAD"} ${name}${detail ? ` - ${detail}` : ""}`);
  if (!ok) failures.push(name);
};

// 1. A dataset, created from TS with the flat question shape.
const dataset = await evals.createDataset({
  name: `TS CI demo ${Date.now()}`,
  evaluationCriteria: "The answer must state the concrete number the policy defines.",
  questions: [
    { query: "How long is the return window?", expectedResults: "30 days." },
    { query: "How long is the warranty?", expectedResults: "2 years." },
  ],
});
check("dataset created from TS", Boolean(dataset.datasetId));

// 2. Run the agent (here: a stub - in CI this is your real agent) and submit outputs.
const ANSWERS = {
  "How long is the return window?": "You have 30 days from delivery.",
  "How long is the warranty?": "Two years, parts and labor.",
};
const run = await evals.initRun({ datasetId: dataset.datasetId, subject: { displayName: "ts-policy-bot" } });
const submitted = await run.submit([
  { caseIndex: 0, query: "How long is the return window?", output: ANSWERS["How long is the return window?"] },
  { caseIndex: 1, query: "How long is the warranty?", output: ANSWERS["How long is the warranty?"] },
]);
check("both results accepted", submitted.accepted === 2, `accepted=${submitted.accepted}`);

// 3. Finalize and gate - assert() throws with the failing check names, which is what fails CI.
const summary = await run.finalize();
check("finalize reports live statistics", typeof summary.liveStatistics?.ratedCount === "number",
  `rated=${summary.liveStatistics?.ratedCount}`);

const gate = await run.gate({ failUnder: 5, record: true, caller: "eval_ops/07_ts_ci_gate.mjs" });
check("gate passes the good answers", gate.passed === true, `avg=${gate.averageRating}`);
try {
  gate.assert();
  check("gate.assert() stays quiet on a pass", true);
} catch (err) {
  check("gate.assert() stays quiet on a pass", false, String(err));
}

// 4. Resume support exists from TS too.
const keys = await run.submittedKeys();
check("submitted keys are readable for resume", keys.length === 2, `${keys.length} keys`);

if (failures.length > 0) {
  console.log(`\nFAILED: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("\nTypeScript CI slice verified: init, submit, finalize, gate, resume - no Python required.");
