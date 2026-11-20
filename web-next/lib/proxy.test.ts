import { createServer, type RequestListener, type Server, type ServerResponse } from "node:http";
import { once } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxyRequest } from "./proxy";

const servers: Server[] = [];
afterEach(async () => {
  vi.unstubAllEnvs();
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => { server.closeAllConnections(); server.close(() => resolve()); })));
});

async function backend(handler: RequestListener) {
  const server = createServer(handler);
  servers.push(server);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw Error("No backend address");
  vi.stubEnv("RACCOON_BACKEND_URL", `http://127.0.0.1:${address.port}`);
  return server;
}

function request(headers: Record<string, string> = {}, init: RequestInit = {}) {
  return new Request("http://127.0.0.1:3000/api/state?since=3", {
    ...init, headers: { host: "127.0.0.1:3000", ...headers },
  });
}

describe("local API boundary over HTTP", () => {
  it('allows opaque sandbox origins only for reading Canvas bundle assets', async()=>{
    let hits=0;
    await backend((req,res)=>{hits++;res.setHeader('Access-Control-Allow-Origin','*');res.end('export const ok=true;');});
    const canvasRequest=(path:string,method='GET')=>new Request(`http://127.0.0.1:3000${path}`,{method,headers:{host:'127.0.0.1:3000',origin:'null'}});
    const response=await proxyRequest(canvasRequest('/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/canvas/app.js'));
    expect(response.status).toBe(200);
    expect(response.headers.get('access-control-allow-origin')).toBe('*');
    await response.text();
    for(const [path,method] of [['/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/content','GET'],['/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/canvas/app.js','POST'],['/api/state','GET']]){
      expect((await proxyRequest(canvasRequest(path,method))).status).toBe(403);
    }
    expect(hits).toBe(1);
  });
  it.each(["evil.example:3000", "127.0.0.1.evil.example", "127.1:3000", "user@localhost:3000", "localhost:3000/path"])("rejects hostile Host %s before upstream", async (host) => {
    let hits = 0;
    await backend((_req, res) => { hits++; res.end(); });
    expect((await proxyRequest(request({ host }))).status).toBe(403);
    expect(hits).toBe(0);
  });

  it.each(["http://evil.example", "http://localhost:3000", "https://127.0.0.1:3000", "null", "http://127.0.0.1:3000/path"])("rejects off-origin Origin %s", async (origin) => {
    let hits = 0;
    await backend((_req, res) => { hits++; res.end(); });
    expect((await proxyRequest(request({ origin, "x-forwarded-host": "evil.example" }))).status).toBe(403);
    expect(hits).toBe(0);
  });

  it.each(["http://evil.example:8766", "http://127.0.0.1:8766/path", "http://user:pass@localhost:8766", "https://127.0.0.1:8766", "http://127.0.0.1:8766?upstream=x", "http://localhost:3000"])("rejects unsafe upstream %s", async (url) => {
    vi.stubEnv("RACCOON_BACKEND_URL", url);
    expect((await proxyRequest(request())).status).toBe(502);
  });

  it("keeps public Host, Origin, token and body on the wire, discards forwarded and hop-by-hop headers", async () => {
    await backend(async (req, res) => {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      res.writeHead(202, { "content-type": "application/json" });
      res.end(JSON.stringify({ url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString() }));
    });
    const response = await proxyRequest(request({
      origin: "http://127.0.0.1:3000", "x-raccoon-token": "test-token",
      forwarded: "host=evil.example", "x-forwarded-host": "evil.example", "x-forwarded-proto": "https", "x-real-ip": "evil.example",
      connection: "keep-alive, x-secret", "x-secret": "drop-me",
    }, { method: "POST", body: '{"message":"hi"}' }));
    expect(response.status).toBe(202);
    const received = await response.json();
    expect(received.url).toBe("/api/state?since=3");
    expect(received.body).toBe('{"message":"hi"}');
    expect(received.headers.host).toBe("127.0.0.1:3000");
    expect(received.headers.origin).toBe("http://127.0.0.1:3000");
    expect(received.headers["x-raccoon-token"]).toBe("test-token");
    for (const key of ["forwarded", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "x-secret"]) expect(received.headers[key]).toBeUndefined();
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("leaves token absent so Python can reject unauthorized writes", async () => {
    await backend((req, res) => { res.writeHead(req.headers["x-raccoon-token"] ? 200 : 403); res.end(); });
    expect((await proxyRequest(request({}, { method: "POST", body: "{}" }))).status).toBe(403);
  });

  it("returns SSE chunks before upstream closes, and disconnects on unsubscribe", async () => {
    let source!: ServerResponse;
    let disconnected = false;
    await backend((_req, res) => {
      source = res; res.on("close", () => { disconnected = true; });
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("data: first\n\n");
    });
    const response = await proxyRequest(request());
    const reader = response.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("data: first\n\n");
    source.write("data: second\n\n");
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("data: second\n\n");
    await reader.cancel();
    await vi.waitFor(() => expect(disconnected).toBe(true));
  });

  it("does not follow upstream redirects", async () => {
    await backend((_req, res) => { res.writeHead(302, { location: "http://evil.example" }); res.end(); });
    const response = await proxyRequest(request());
    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe("http://evil.example");
    await response.body?.cancel();
  });

  it("returns a safe gateway failure when upstream drops the connection", async () => {
    await backend((req) => { req.socket.destroy(); });
    const response = await proxyRequest(request());
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("socket");
  });

  it("propagates browser abort to upstream without turning it into a server failure", async () => {
    await backend((_req, _res) => {});
    const abort = new AbortController();
    const pending = proxyRequest(request({}, { signal: abort.signal }));
    abort.abort();
    expect((await pending).status).toBe(499);
  });
});
