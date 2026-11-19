// Playground readings (shared/playground/readings.cjs): what a playground shows at some settings, for the follow-up
// talk step and editor and Jev's text-vs-playground check.
// Input: {"spec": <playground spec>, "runs"?: [{set?, play?, press?, label?}]} as JSON on stdin.
// Output: one JSON line {"runs": [...], "text": "<formatted>"} or {"error": "..."}.
// Runs in a worker thread with a time limit, like check-playground.mjs, so a snippet that loops forever is stopped.
// Called by server/explainer/modes/explorer/playground.py (read_playground), which treats any failure as "no readings".
import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const TIMEOUT_MS = 5000;

if (isMainThread) {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) input += chunk;
  const print = (r) => process.stdout.write(JSON.stringify(r) + "\n");
  let req;
  try {
    req = JSON.parse(input);
  } catch (e) {
    print({ error: `not valid JSON: ${e.message}` });
    process.exit(0);
  }
  const w = new Worker(fileURLToPath(import.meta.url), { workerData: req });
  const timer = setTimeout(() => {
    w.terminate();
    print({ error: "running it took too long (a snippet may loop forever)" });
  }, TIMEOUT_MS);
  w.once("message", (r) => { clearTimeout(timer); w.terminate(); print(r); });
  w.once("error", (e) => { clearTimeout(timer); print({ error: `readings error: ${e.message}` }); });
} else {
  const require = createRequire(import.meta.url);
  const { read, format } = require("../shared/playground/readings.cjs");
  let r;
  try {
    r = read(workerData && workerData.spec, workerData && workerData.runs);
    if (!r.error) r.text = format(r, Number(workerData.maxChars) || 3000);
  } catch (e) {
    r = { error: `readings error: ${e.message}` };
  }
  parentPort.postMessage(r);
}
