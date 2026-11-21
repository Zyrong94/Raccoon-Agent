import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import net from "node:net";
import { test } from "node:test";
import { ensurePortFree, parseArguments, stopChild } from "./start.mjs";

test("parses project and data root and loopback server ports", () => {
  const options = parseArguments(["--project", "C:/test project", "--data-root", "C:/test data", "--port", "3011", "--backend-port", "8767", "--dev"]);
  assert.equal(options.project, "C:/test project");
  assert.equal(options.dataRoot, "C:/test data");
  assert.equal(options.port, 3011);
  assert.equal(options.backendPort, 8767);
  assert.equal(options.dev, true);
  assert.equal(parseArguments(["--open"]).open, true);
  assert.ok(!parseArguments([]).open);
});

test("rejects invalid or colliding ports and unknown flags before launch", () => {
  for (const args of [["--port", "0"], ["--port", "3000.5"], ["--backend-port", "65536"], ["--project"], ["--unknown"], ["--port", "8766"]]) {
    assert.throws(() => parseArguments(args));
  }
});

test("refuses an occupied port without disturbing its owner", async () => {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = server.address().port;
  try {
    await assert.rejects(ensurePortFree(port), /occupied/);
    assert.equal(server.listening, true);
  } finally { await new Promise((resolve) => server.close(resolve)); }
  await ensurePortFree(port);
});

test("teardown terminates its child tree and leaves an unrelated process alive", async () => {
  const owned = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"]);
  const unrelated = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"]);
  await Promise.all([once(owned, "spawn"), once(unrelated, "spawn")]);
  try {
    await stopChild(owned);
    assert.notEqual(owned.signalCode ?? owned.exitCode, null);
    assert.equal(unrelated.exitCode, null);
    assert.equal(unrelated.signalCode, null);
    await stopChild(owned);
  } finally { await stopChild(unrelated); }
});
