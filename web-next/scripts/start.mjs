import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.dirname(appRoot);

export function parseArguments(args) {
  const options = { project: repoRoot, dataRoot: undefined, port: 3000, backendPort: 8766, dev: false };
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === "--dev") { options.dev = true; continue; }
    const key = { "--project": "project", "--data-root": "dataRoot", "--port": "port", "--backend-port": "backendPort" }[flag];
    if (!key) throw new Error(`Unknown option: ${flag}`);
    const value = args[++index];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${flag}`);
    if (key === "port" || key === "backendPort") {
      if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) throw new Error(`Invalid port for ${flag}`);
      options[key] = Number(value);
    } else options[key] = value;
  }
  if (options.port === options.backendPort) throw new Error("Frontend and backend ports must differ");
  return options;
}

export async function ensurePortFree(port) {
  const server = net.createServer();
  try {
    server.listen(port, "127.0.0.1");
    await once(server, "listening");
    await new Promise((resolve) => server.close(resolve));
  } catch {
    throw new Error(`Loopback port ${port} is occupied or unavailable; stop its owner or select another port`);
  }
}

export async function stopChild(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit");
  if (process.platform === "win32") {
    // Only terminate a tree rooted at a child created by this launcher.
    const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    await once(killer, "exit");
  } else child.kill("SIGTERM");
  let timer;
  await Promise.race([exited, new Promise((resolve) => {
    timer = setTimeout(() => { child.kill("SIGKILL"); resolve(); }, 5000);
  })]);
  clearTimeout(timer);
}

async function waitReady(url, child) {
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error("Server exited before becoming ready");
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      await response.body?.cancel();
      if (response.ok) return;
    } catch { /* Server may still be starting. */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Server did not become ready at ${new URL(url).origin}`);
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  await ensurePortFree(options.port);
  await ensurePortFree(options.backendPort);
  const project = path.resolve(options.project);
  const dataRoot = options.dataRoot ? path.resolve(options.dataRoot) : undefined;
  const localPython = path.join(repoRoot, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const python = existsSync(localPython) ? localPython : (process.platform === "win32" ? "python" : "python3");
  const children = [];
  let stopping = false;
  const shutdown = async (code) => {
    if (stopping) return;
    stopping = true;
    await Promise.all(children.map(stopChild));
    process.exitCode = code;
  };
  process.on("SIGINT", () => { void shutdown(0); });
  process.on("SIGTERM", () => { void shutdown(0); });
  const start = (command, args, settings) => {
    const child = spawn(command, args, { stdio: "inherit", windowsHide: true, ...settings });
    children.push(child);
    child.on("error", () => { console.error("Unable to start a workbench server"); void shutdown(1); });
    child.on("exit", (code) => { if (!stopping) void shutdown(code || 1); });
    return child;
  };
  try {
    const args = ["-m", "raccoon.interfaces.web", "--project", project, "--port", String(options.backendPort)];
    if (dataRoot) args.push("--data-root", dataRoot);
    const backend = start(python, args, { cwd: repoRoot });
    await waitReady(`http://127.0.0.1:${options.backendPort}/api/state`, backend);
    if (stopping) return;
    const frontend = start(process.execPath, [path.join(appRoot, "node_modules/next/dist/bin/next"), options.dev ? "dev" : "start", "--hostname", "127.0.0.1", "--port", String(options.port)], {
      cwd: appRoot,
      env: { ...process.env, RACCOON_BACKEND_URL: `http://127.0.0.1:${options.backendPort}` },
    });
    await waitReady(`http://127.0.0.1:${options.port}`, frontend);
    if (!stopping) console.log(`Raccoon: http://127.0.0.1:${options.port} (project: ${project})`);
  } catch (error) {
    console.error(error.message);
    await shutdown(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
