# web

The Babel Bazaar storefront and the shop owner's back office: Preact, signals and
TypeScript, built with Vite. About 23 KB of JavaScript and 6 KB of CSS (Brotli) for
the shop; the admin is a separate chunk only owners download.

The BFF serves it (see `services/bff/README.md`): one origin for the app, the API and
sign-in, a strict CSP, precompressed files from memory. The browser never holds a token,
only the BFF's session cookie and a CSRF token.

| Command | |
|---|---|
| `npm run dev` | Vite on :5173, proxying `/api`, `/auth` and sign-in to a running stack (`./run.sh`) |
| `npm run build` | `dist/`, what the BFF image serves |
| `npm run build:demo` | `dist-demo/`, the GitHub Pages demo: the same app with an in-browser backend |
| `npm test` | unit tests |

## What it does

- Instant search with suggestions (`/` focuses it), category and price filters, sorting.
- Product pages with recommendations from `intelligence` (degrading quietly when it is down).
- A bag that works signed out, is kept in the browser, and is merged into the account's cart after sign-in.
- Checkout with an idempotency key per bag (a double click or a retry never makes two orders);
  the order reserves stock for 15 minutes.
- Payment with payments-sim's test cards: approved, declined, insufficient funds, and a slow bank
  whose outcome the page waits for (the server never charges twice).
- Orders with live status; the owner's overview, products (create, edit with version checks,
  archive, restore, stock adjustments with history) and orders with refunds.
- Light and dark themes, keyboard and screen-reader friendly, responsive down to small phones,
  view transitions, skeletons instead of spinners.

Products have no photos in the seed data, so `components/Art.tsx` draws one per kind of product,
stable for its SKU.

## Demo mode

`--mode demo` swaps `src/api/http.ts` for `src/api/demo.ts`, which implements the same `Api`
interface in the browser with the same rules (stock reservations, idempotent checkout, payment
outcomes per card, version conflicts, refunds) and keeps its state in `localStorage`. Its catalog,
`src/api/demo-catalog.ts`, is generated from the commerce-core seed by `scripts/demo-catalog.py`;
CI fails if they drift. The live build contains none of it.
