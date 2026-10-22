// Give the demo a shared backend with no account and no manual step: create a free jsonblob.com document
// once, cache its URL, and hand it to the build as DEMO_SYNC_URL. Run by the Pages workflow before the
// build; the URL is cached (actions/cache) so the same document is reused on every later deploy and the
// shared data persists. If anything here fails — offline, the service is down, a user override is set —
// it leaves DEMO_SYNC_URL as it found it and exits 0, so the Pages build never breaks; the demo then
// just runs per-browser. The whole thing is a convenience: see deploy/pages-backend/ for a private,
// rate-limited Cloudflare Worker you can point it at instead (set DEMO_SYNC_URL).
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const API = process.env.JSONBLOB_API || "https://jsonblob.com/api/jsonBlob";
const CACHE = process.env.BACKEND_URL_CACHE || "site/.backend-url";
const EMPTY = { products: [], categories: [], metrics: { events: {}, visitors: [] }, since: new Date().toISOString() };

async function emit(url) {
  if (process.env.GITHUB_ENV) await appendFile(process.env.GITHUB_ENV, `DEMO_SYNC_URL=${url}\n`);
  console.log(`demo backend: ${url}`);
}

async function reachable(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(8000) });
    return r.ok;
  } catch {
    return false;
  }
}

async function main() {
  // 1. Explicit override wins (e.g. a Cloudflare Worker URL set as a repo variable).
  if ((process.env.DEMO_SYNC_URL || "").trim()) {
    console.log("demo backend: using DEMO_SYNC_URL override");
    return;
  }
  // 2. Reuse the cached document if it is still there.
  try {
    const cached = (await readFile(CACHE, "utf8")).trim();
    if (cached && (await reachable(cached))) { await emit(cached); return; }
    if (cached) console.log("demo backend: cached document gone, creating a new one");
  } catch { /* no cache yet */ }
  // 3. Create a new document (no account needed).
  try {
    const res = await fetch(API, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(EMPTY),
      signal: AbortSignal.timeout(12000),
    });
    const loc = res.headers.get("location") || res.headers.get("Location");
    if (!res.ok || !loc) throw new Error(`create failed: ${res.status}`);
    const url = loc.startsWith("http") ? loc : new URL(loc, API).origin + loc;
    if (!(await reachable(url))) throw new Error("created but not readable");
    await mkdir(dirname(CACHE), { recursive: true });
    await writeFile(CACHE, url + "\n");
    await emit(url);
  } catch (err) {
    console.log(`demo backend: not provisioned (${err?.message || err}); the demo will run per-browser`);
  }
}

main().catch((err) => { console.log(`demo backend: ${err?.message || err}`); });
