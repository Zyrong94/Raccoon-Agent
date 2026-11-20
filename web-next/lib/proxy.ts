import { request as httpRequest } from "node:http";
import { Readable } from "node:stream";

const hopByHop = ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"];

function cleanHeaders(source: Headers): Headers {
  const headers = new Headers(source);
  for (const name of (source.get("connection") ?? "").split(",")) {
    if (name.trim()) headers.delete(name.trim());
  }
  for (const name of hopByHop) headers.delete(name);
  for (const name of [...headers.keys()]) {
    if (name === "forwarded" || name.startsWith("x-forwarded-") || name === "x-real-ip") headers.delete(name);
  }
  return headers;
}

function isLoopbackHost(host: string): boolean {
  const match = /^(?:127\.0\.0\.1|localhost|\[::1\])(?::(\d{1,5}))?$/.exec(host);
  return !!match && (!match[1] || (Number(match[1]) >= 1 && Number(match[1]) <= 65535));
}

export async function proxyRequest(request: Request): Promise<Response> {
  // The launcher serves HTTP loopback; forwarded headers never define the public origin.
  const host = request.headers.get("host") ?? "";
  const origin = request.headers.get("origin");
  const publicURL = new URL(request.url);
  // Sandboxed Canvas modules/fonts have an opaque origin; match Python's read-only asset rule.
  const canvasAsset = ["GET", "HEAD"].includes(request.method) &&
    /^\/api\/projects\/[a-f0-9]{16}\/sessions\/[A-Za-z0-9_-]+\/artifacts\/[^/]+\/canvas\/.+/.test(publicURL.pathname);
  if (!isLoopbackHost(host) || (origin !== null && origin !== `http://${host}` && !(origin === 'null' && canvasAsset))) {
    return Response.json({ detail: "仅允许本机同源访问" }, { status: 403 });
  }

  let upstream: URL;
  try {
    upstream = new URL(process.env.RACCOON_BACKEND_URL ?? "http://127.0.0.1:8766");
    const publicPort = new URL(`http://${host}`).port || "80";
    if (upstream.protocol !== "http:" || !isLoopbackHost(upstream.host) || upstream.username || upstream.password || upstream.pathname !== "/" || upstream.search || upstream.hash || (upstream.port || "80") === publicPort) throw new Error("Unsafe upstream");
  } catch {
    return Response.json({ detail: "本机后端地址配置无效" }, { status: 502 });
  }

  if (!publicURL.pathname.startsWith("/api/")) return new Response(null, { status: 404 });
  upstream.pathname = publicURL.pathname;
  upstream.search = publicURL.search;
  const headers = cleanHeaders(request.headers);
  headers.set("host", host);
  if (origin !== null) headers.set("origin", origin);
  const token = request.headers.get("x-raccoon-token");
  if (token !== null) headers.set("x-raccoon-token", token);
  headers.delete("content-length");
  headers.set("accept-encoding", "identity");

  return new Promise<Response>((resolve) => {
    // node:http preserves Host on the wire; Node/Next fetch may replace it.
    const outgoing = httpRequest(upstream, { method: request.method, headers: Object.fromEntries(headers), signal: request.signal }, (response) => {
      const responseHeaders = new Headers();
      for (let index = 0; index < response.rawHeaders.length; index += 2) responseHeaders.append(response.rawHeaders[index], response.rawHeaders[index + 1]);
      const cleaned = cleanHeaders(responseHeaders);
      cleaned.set("cache-control", "no-store");
      cleaned.set("x-accel-buffering", "no");
      const status = response.statusCode ?? 502;
      const noBody = request.method === "HEAD" || [204, 205, 304].includes(status);
      if (noBody) response.resume();
      resolve(new Response(noBody ? null : Readable.toWeb(response) as ReadableStream<Uint8Array>, { status, headers: cleaned }));
    });
    outgoing.on("error", () => resolve(Response.json({ detail: request.signal.aborted ? "请求已取消" : "无法连接本机后端" }, { status: request.signal.aborted ? 499 : 502 })));
    if (request.body && request.method !== "GET" && request.method !== "HEAD") {
      const body = Readable.fromWeb(request.body as import("node:stream/web").ReadableStream<Uint8Array>);
      body.on("error", (error) => outgoing.destroy(error));
      outgoing.on("close", () => { if (!body.destroyed) body.destroy(); });
      body.pipe(outgoing);
    } else outgoing.end();
  });
}

