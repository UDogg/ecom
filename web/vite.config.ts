import { defineConfig } from "vite";

// Read an optional env var without pulling in @types/node (tsc type-checks this file).
const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};

// `vite build` makes the app the BFF serves; `--mode demo` makes the self-contained demo for GitHub
// Pages (an in-browser backend, relative base path, no server at all).
export default defineConfig(({ mode }) => ({
  base: mode === "demo" ? "./" : "/",
  // __SYNC_URL__: optional shared backend for the demo (deploy/pages-backend). Set DEMO_SYNC_URL in the
  // Pages workflow to bake it in; empty means local-only (the demo still works, per browser).
  define: { __DEMO__: JSON.stringify(mode === "demo"), __SYNC_URL__: JSON.stringify(env.DEMO_SYNC_URL || "") },
  oxc: { jsx: { runtime: "automatic", importSource: "preact" } },
  build: {
    target: "es2022",
    cssCodeSplit: true,
    modulePreload: { polyfill: false },
    reportCompressedSize: false,
    assetsInlineLimit: 0, // the CSP has no data: scripts or styles; images may still be data: URIs we build at runtime
  },
  server: {
    // `npm run dev` against a running stack: the BFF is the only backend.
    proxy: Object.fromEntries(["/api", "/auth", "/login", "/oauth2", "/auth-ui.css"].map((p) => [p, "http://localhost:8080"])),
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
}));
