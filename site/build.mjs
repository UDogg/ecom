// Builds the GitHub Pages site into ../_site: the landing page, the documents listed in docs.json
// rendered to HTML, and the storefront demo (web/, built with its in-browser backend).
//
//   cd site && npm ci && npm run build          (SKIP_DEMO=1 to reuse web/dist-demo)
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, posix, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Marked } from "marked";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const out = join(root, "_site");
const repo = process.env.GITHUB_REPOSITORY || "UDogg/ecom";
const ref = process.env.SITE_SOURCE_REF || "main";
const blob = (p) => `https://github.com/${repo}/blob/${ref}/${p}`;

const groups = JSON.parse(readFileSync(join(here, "docs.json"), "utf8"));
const published = new Map(groups.flatMap((g) => g.items).map((d) => [d.id, d]));

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
for (const f of ["index.html", "site.css", "stress.html", "landing.js", "metrics.html"]) cpSync(join(here, f), join(out, f));
cpSync(join(root, "web/public/favicon.svg"), join(out, "favicon.svg"));
writeFileSync(join(out, ".nojekyll"), "");

// config.js: the optional shared-backend URL (deploy/pages-backend) baked in from DEMO_SYNC_URL, plus a
// tiny anonymous beacon the static pages use. Empty URL = local-only; the site still works unchanged.
const syncUrl = (process.env.DEMO_SYNC_URL || "").trim().replace(/\/+$/, "");
writeFileSync(join(out, "config.js"),
  `window.BAZAAR_SYNC=${JSON.stringify(syncUrl)};\n` +
  `window.bzTrack=function(n){try{var K="bb-telemetry-v1",V="bb-visitor",` +
  `c=JSON.parse(localStorage.getItem(K)||"{}");c[n]=(c[n]||0)+1;localStorage.setItem(K,JSON.stringify(c));` +
  `var v=localStorage.getItem(V);if(!v){v=(self.crypto&&crypto.randomUUID&&crypto.randomUUID())||String(Math.random());localStorage.setItem(V,v);}` +
  `var b=window.BAZAAR_SYNC;if(b){fetch(b+"/event",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({name:n,vid:v}),mode:"cors"}).catch(function(){});}}catch(e){}};\n`);

// Vendor three.js (the landing page's WebGL hero) from web's node_modules, so the site carries no
// runtime CDN dependency. The demo build below runs `npm ci` in web/, which installs it.
const threeSrc = join(root, "web/node_modules/three/build/three.module.js");
if (existsSync(threeSrc)) {
  mkdirSync(join(out, "vendor"), { recursive: true });
  cpSync(threeSrc, join(out, "vendor/three.module.js"));
} else {
  console.warn("three.module.js not found in web/node_modules; the landing WebGL hero will fall back to the static mock");
}

// ── docs ────────────────────────────────────────────────────────────────────
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function page(doc, html) {
  const from = posix.dirname(`docs/${doc.id}.html`);
  const link = (to) => posix.relative(from, to) || ".";
  const nav = groups.map((g) => `<h4>${esc(g.group)}</h4><ul>${g.items.map((d) =>
    `<li><a href="${link(`docs/${d.id}.html`)}"${d.id === doc.id ? ' aria-current="page"' : ""}>${esc(d.title)}</a></li>`).join("")}</ul>`).join("");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(doc.title)} · Babel Bazaar</title><link rel="icon" href="${link("favicon.svg")}"><link rel="stylesheet" href="${link("site.css")}"></head>
<body><header class="top"><a class="brand" href="${link("index.html")}"><span class="mark">B</span> Babel Bazaar</a>
<nav><a href="${link("demo/index.html")}">Demo</a><a href="${link("docs/aws.html")}">Docs</a><a href="https://github.com/${repo}">GitHub</a></nav></header>
<div class="docs"><aside>${nav}</aside><main class="prose">${html}
<p class="source">Source: <a href="${blob(`docs/${doc.id}.md`)}">docs/${esc(doc.id)}.md</a></p></main></div></body></html>`;
}

for (const doc of published.values()) {
  const srcPath = join(root, "docs", `${doc.id}.md`);
  const marked = new Marked({
    gfm: true,
    walkTokens(token) {
      if (token.type !== "link" || /^(https?:|mailto:|#)/.test(token.href)) return;
      const [path, hash = ""] = token.href.split("#");
      const target = posix.normalize(posix.join(posix.dirname(`docs/${doc.id}.md`), path));
      const id = target.replace(/^docs\//, "").replace(/\.md$/, "");
      token.href = target.startsWith("docs/") && target.endsWith(".md") && published.has(id)
        ? posix.relative(posix.dirname(`docs/${doc.id}.html`), `docs/${id}.html`) + (hash ? `#${hash}` : "")
        : blob(target) + (hash ? `#${hash}` : ""); // code and unpublished files: on GitHub
    },
  });
  const file = join(out, "docs", `${doc.id}.html`);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, page(doc, marked.parse(readFileSync(srcPath, "utf8"))));
}
writeFileSync(join(out, "docs/index.html"), `<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=aws.html"><a href="aws.html">Docs</a>`);

// ── the demo ────────────────────────────────────────────────────────────────
const web = join(root, "web");
if (!process.env.SKIP_DEMO || !existsSync(join(web, "dist-demo"))) {
  execFileSync("npm", ["ci", "--no-audit", "--no-fund"], { cwd: web, stdio: "inherit" });
  execFileSync("npm", ["run", "build:demo"], { cwd: web, stdio: "inherit" });
}
cpSync(join(web, "dist-demo"), join(out, "demo"), { recursive: true });

console.log(`built ${relative(root, out)}: ${published.size} documents, the demo, the landing page`);
