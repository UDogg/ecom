// The demo backend: the whole shop in the browser, for the GitHub Pages build. It follows the real
// services' rules (stock reservations, idempotent checkout, payment outcomes per test card, ETag
// versions, refunds) so the demo behaves like the stack, and keeps its state in localStorage.
import { CATEGORIES, PRODUCTS } from "./demo-catalog";
import {
  ApiError,
  type AdminOrder,
  type AdminProduct,
  type Adjustment,
  type Api,
  type Cart,
  type Line,
  type Me,
  type Order,
  type Page,
  type Product,
  type ProductQuery,
  type Role,
} from "./types";
import { pullState, pushProduct } from "./sync";
import { track } from "../lib/telemetry";

const KEY = "babel-bazaar-demo-v1";
const CUSTOMER = "00000000-0000-4000-8000-000000000002";
const DECLINES: Record<string, string> = { tok_declined: "card_declined", tok_insufficient: "insufficient_funds" };

interface StoredProduct extends Omit<AdminProduct, "category" | "images"> { category: string }
interface StoredOrder extends AdminOrder { settle_at?: number; idempotency_key?: string }
interface State {
  role: Role | null;
  products: StoredProduct[];
  categories: { slug: string; name: string }[];
  cart: Line[];
  orders: StoredOrder[];
  adjustments: Adjustment[];
}

function seed(): State {
  const t = Date.now();
  return {
    role: null,
    categories: CATEGORIES.map((c) => ({ ...c })),
    products: PRODUCTS.map((p, i) => ({
      id: p.id, sku: p.sku, name: p.name, description: p.description, price_minor: p.price_minor, currency: "USD",
      category: p.category, in_stock: p.stock > 0, status: "active", version: 1, available_quantity: p.stock,
      created_at: new Date(t - (PRODUCTS.length - i) * 3_600_000).toISOString(), updated_at: new Date(t).toISOString(),
    })),
    cart: [],
    orders: [],
    adjustments: [],
  };
}

function load(): State {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as State;
  } catch { /* private mode: in memory only */ }
  return seed();
}


/** The demo backend. A factory, so the live build (which never calls it) drops the module entirely. */
export function createDemoApi(): Api {
  let state: State = load();
  const save = () => {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* full or blocked */ }
  };
  /** Starts the demo shop afresh. */
  function resetDemo() {
    state = seed();
    save();
  }

  // One-time merge of the shared community catalog (products any owner added), so a product added on
  // one device shows up for every future visitor. Best-effort: with no backend configured, or if it is
  // unreachable, this resolves to a no-op and the demo runs on local state alone.
  let sharedPromise: Promise<void> | null = null;
  function ensureShared(): Promise<void> {
    if (!sharedPromise) {
      sharedPromise = (async () => {
        const remote = await pullState();
        if (!remote) return;
        let changed = false;
        for (const rc of remote.categories ?? []) {
          if (rc?.slug && !state.categories.some((c) => c.slug === rc.slug)) {
            state.categories.push({ slug: rc.slug, name: rc.name || rc.slug });
            changed = true;
          }
        }
        for (const rp of (remote.products ?? []) as unknown as StoredProduct[]) {
          if (!rp || typeof rp.id !== "string") continue;
          const i = state.products.findIndex((p) => p.id === rp.id);
          if (i < 0) { state.products.push(rp); changed = true; }
          else if ((rp.updated_at || "") > (state.products[i]!.updated_at || "")) { state.products[i] = rp; changed = true; }
        }
        if (changed) save();
      })().catch(() => {});
    }
    return sharedPromise;
  }
  /** Share a product with every future visitor (after a local mutation). */
  const share = (p: StoredProduct) => pushProduct(p);

  const pause = (ms = 90 + Math.random() * 120) => new Promise((r) => setTimeout(r, ms));
  const uuid = () => crypto.randomUUID();

  function fail(status: number, slug: string, title: string, detail: string, extra: object = {}): never {
    throw new ApiError({ type: `https://shop.local/errors/${slug}`, title, status, detail, ...extra });
  }

  function need(role: Role) {
    if (!state.role) fail(401, "unauthenticated", "Sign in required", "Sign in to continue.");
    if (role === "shop_owner" && state.role !== "shop_owner") fail(403, "forbidden", "Forbidden", "Your account cannot use this.");
  }

  function category(slug: string) {
    const c = state.categories.find((c) => c.slug === slug);
    return c ? { slug: c.slug, name: c.name } : null;
  }

  const publicProduct = (p: StoredProduct): Product => ({
    id: p.id, sku: p.sku, name: p.name, description: p.description, price_minor: p.price_minor, currency: p.currency,
    category: category(p.category), in_stock: p.available_quantity > 0, images: [],
  });

  const adminProduct = (p: StoredProduct): AdminProduct => ({ ...p, category: category(p.category), in_stock: p.available_quantity > 0, images: [] });

  function page<T>(all: T[], pageNo = 1, size = 24): Page<T> {
    const start = (pageNo - 1) * size;
    return { items: all.slice(start, start + size), total: all.length, page: pageNo, page_size: size, pages: Math.ceil(all.length / size) };
  }

  function product(id: string, onlyActive = false) {
    const p = state.products.find((p) => p.id === id);
    if (!p || (onlyActive && p.status !== "active")) fail(404, "not-found", "Not found", "Product not found.");
    return p;
  }

  function settle(o: StoredOrder) {
    if (o.settle_at && Date.now() >= o.settle_at) {
      o.settle_at = undefined;
      o.status = o.status === "pending_payment" ? "paid" : o.status;
      o.payment = { status: "captured", failure_code: null };
      save();
    }
  }

  const customerOrder = ({ customer_id: _c, settle_at: _s, idempotency_key: _k, ...o }: StoredOrder): Order => o;
  const ownerOrder = ({ settle_at: _s, idempotency_key: _k, ...o }: StoredOrder): AdminOrder => o;

  function order(id: string) {
    const o = state.orders.find((o) => o.id === id);
    if (!o) fail(404, "not-found", "Not found", "Order not found.");
    settle(o);
    return o;
  }

  function cartOut(): Cart {
    const items = state.cart.map((l) => {
      const p = product(l.product_id);
      return { product_id: p.id, sku: p.sku, name: p.name, quantity: l.quantity, unit_price_minor: p.price_minor, currency: p.currency,
        available: p.status === "active" && p.available_quantity >= l.quantity };
    });
    return { items, subtotal_minor: items.filter((i) => i.available).reduce((s, i) => s + i.unit_price_minor * i.quantity, 0) };
  }

  const demoApi: Api = {
    demo: true,
    reset: resetDemo,
    async me(): Promise<Me> {
      await pause(30);
      if (!state.role) return { authenticated: false, csrf_token: "demo", login_url: "/auth/login" };
      const email = state.role === "shop_owner" ? "owner@shop.local" : "customer@shop.local";
      return { authenticated: true, csrf_token: "demo", user: { id: CUSTOMER, email, roles: state.role === "shop_owner" ? ["customer", "shop_owner"] : ["customer"] } };
    },
    async signIn(_returnTo, as = "customer") {
      await pause();
      state.role = as;
      save();
    },
    async signOut() {
      state.role = null;
      save();
    },
    async register(email, password) {
      await pause();
      if (password.length < 12) fail(400, "validation", "Invalid request", "One or more fields are invalid.", { errors: { password: "must be at least 12 characters" } });
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) fail(400, "validation", "Invalid request", "One or more fields are invalid.", { errors: { email: "must be an e-mail address" } });
    },

    async products(q: ProductQuery) {
      await ensureShared();
      await pause();
      const text = (q.q ?? "").trim().toLowerCase();
      let all = state.products.filter((p) => p.status === "active"
        && (!q.category || p.category === q.category)
        && (q.min_price === undefined || p.price_minor >= q.min_price)
        && (q.max_price === undefined || p.price_minor <= q.max_price)
        && (!text || `${p.name} ${p.description} ${p.sku} ${category(p.category)?.name ?? ""}`.toLowerCase().includes(text)));
      const sort = q.sort && q.sort !== "relevance" ? q.sort : text ? "relevance" : "newest";
      const by: Record<string, (a: StoredProduct, b: StoredProduct) => number> = {
        relevance: (a, b) => Number(b.name.toLowerCase().includes(text)) - Number(a.name.toLowerCase().includes(text)) || a.name.localeCompare(b.name),
        newest: (a, b) => b.created_at.localeCompare(a.created_at),
        price_asc: (a, b) => a.price_minor - b.price_minor,
        price_desc: (a, b) => b.price_minor - a.price_minor,
        name: (a, b) => a.name.localeCompare(b.name),
      };
      all = [...all].sort(by[sort]);
      return page(all.map(publicProduct), q.page, q.page_size);
    },
    async product(id) {
      await ensureShared();
      await pause();
      return publicProduct(product(id, true));
    },
    async categories() {
      await ensureShared();
      await pause(40);
      return state.categories.map((c) => ({ ...c, children: [] }));
    },
    async recommendations(productId, limit = 8) {
      await ensureShared();
      await pause();
      const base = productId ? state.products.find((p) => p.id === productId) : undefined;
      const pool = state.products.filter((p) => p.status === "active" && p.id !== productId && p.available_quantity > 0);
      const ranked = base
        ? [...pool].sort((a, b) => Number(b.category === base.category) - Number(a.category === base.category) || Math.abs(a.price_minor - base.price_minor) - Math.abs(b.price_minor - base.price_minor))
        : [...pool].sort((a, b) => b.available_quantity - a.available_quantity);
      return { strategy: base ? "similar" : "popular",
        items: ranked.slice(0, limit).map((p) => ({ product_id: p.id, sku: p.sku, name: p.name, price_minor: p.price_minor, currency: p.currency })) };
    },

    async cart() {
      need("customer");
      await pause(40);
      return cartOut();
    },
    async setCart(items) {
      need("customer");
      await pause(60);
      const merged = new Map<string, number>();
      for (const l of items) merged.set(l.product_id, Math.min(1000, (merged.get(l.product_id) ?? 0) + l.quantity));
      for (const id of merged.keys()) product(id);
      state.cart = [...merged].map(([product_id, quantity]) => ({ product_id, quantity }));
      save();
      return cartOut();
    },
    async checkout(items, key) {
      need("customer");
      await pause(300);
      const replay = state.orders.find((o) => o.idempotency_key === key);
      if (replay) return customerOrder(replay);
      const lines = items.map((l) => ({ line: l, p: product(l.product_id, true) }));
      for (const { line, p } of lines) {
        if (p.available_quantity < line.quantity) {
          fail(409, "insufficient-stock", "Not enough stock", `Only ${p.available_quantity} of ${p.name} left.`, { product_id: p.id, available: p.available_quantity, requested: line.quantity });
        }
      }
      for (const { line, p } of lines) {
        p.available_quantity -= line.quantity;
        p.in_stock = p.available_quantity > 0;
      }
      const o: StoredOrder = {
        id: uuid(), status: "pending_payment", currency: "USD", created_at: new Date().toISOString(), customer_id: CUSTOMER, idempotency_key: key,
        total_minor: lines.reduce((s, { line, p }) => s + line.quantity * p.price_minor, 0),
        items: lines.map(({ line, p }) => ({ product_id: p.id, sku: p.sku, name: p.name, quantity: line.quantity, unit_price_minor: p.price_minor })),
        payment: { status: null, failure_code: null },
      };
      state.orders.unshift(o);
      state.cart = [];
      save();
      return customerOrder(o);
    },
    async orders(pageNo = 1) {
      need("customer");
      await pause();
      state.orders.forEach(settle);
      return page(state.orders.map(customerOrder), pageNo, 20);
    },
    async order(id) {
      need("customer");
      await pause(60);
      return customerOrder(order(id));
    },
    async pay(id, source) {
      need("customer");
      await pause(source === "tok_timeout" ? 1200 : 500);
      const o = order(id);
      if (o.payment.status === "pending") fail(409, "payment-in-progress", "Payment in progress", "A payment for this order is still being processed.");
      if (o.status !== "pending_payment" && o.status !== "payment_failed") fail(409, "invalid-state-transition", "Cannot be paid", `This order is ${o.status.replace("_", " ")}.`);
      if (source === "tok_timeout") {
        o.payment = { status: "pending", failure_code: null };
        o.settle_at = Date.now() + 4000; // the provider answers later; reconciliation settles it
      } else if (DECLINES[source]) {
        o.status = "payment_failed";
        o.payment = { status: "failed", failure_code: DECLINES[source] };
      } else {
        o.status = "paid";
        o.payment = { status: "captured", failure_code: null };
      }
      save();
      return customerOrder(o);
    },

    admin: {
      async dashboard() {
        need("shop_owner");
        await ensureShared();
        await pause();
        state.orders.forEach(settle);
        return {
          recent_orders: state.orders.slice(0, 10).map(customerOrder),
          low_stock: state.products.filter((p) => p.status === "active" && p.available_quantity <= 5)
            .sort((a, b) => a.available_quantity - b.available_quantity)
            .map((p) => ({ product_id: p.id, sku: p.sku, name: p.name, available_quantity: p.available_quantity })),
          system: { outbox_pending: 0, outbox_oldest_pending_at: null },
        };
      },
      async products(q) {
        need("shop_owner");
        await ensureShared();
        await pause();
        const text = (q.q ?? "").toLowerCase();
        const all = state.products.filter((p) => (!q.status || p.status === q.status) && (!q.category || p.category === q.category)
          && (!text || `${p.name} ${p.sku}`.toLowerCase().includes(text)));
        return page(all.map(adminProduct), q.page, 50);
      },
      async product(id) {
        need("shop_owner");
        await pause(50);
        return adminProduct(product(id));
      },
      async create(input) {
        need("shop_owner");
        await pause();
        if (state.products.some((p) => p.sku.toLowerCase() === input.sku.toLowerCase())) {
          fail(400, "validation", "Invalid request", "One or more fields are invalid.", { errors: { sku: "A product with this SKU already exists." } });
        }
        if (!state.categories.some((c) => c.slug === input.category)) {
          state.categories.push({ slug: input.category, name: input.category_name || input.category });
        }
        const now = new Date().toISOString();
        const p: StoredProduct = { id: uuid(), sku: input.sku, name: input.name, description: input.description, price_minor: input.price_minor,
          currency: input.currency, category: input.category, status: input.status, version: 1, available_quantity: input.initial_stock ?? 0,
          in_stock: (input.initial_stock ?? 0) > 0, created_at: now, updated_at: now };
        state.products.unshift(p);
        save();
        share(p);                 // every future visitor will see this product
        track("product_created");
        return adminProduct(p);
      },
      async update(id, version, change) {
        need("shop_owner");
        await pause();
        const p = product(id);
        if (p.version !== version) fail(409, "version-conflict", "Edited elsewhere", "Someone changed this product meanwhile; reload it.", { current_version: p.version });
        const { category_name, ...rest } = change;
        if (rest.category && !state.categories.some((c) => c.slug === rest.category)) {
          state.categories.push({ slug: rest.category, name: category_name || rest.category });
        }
        Object.assign(p, rest, { version: p.version + 1, updated_at: new Date().toISOString() });
        save();
        share(p);
        return adminProduct(p);
      },
      async archive(id) {
        need("shop_owner");
        await pause();
        const p = product(id);
        Object.assign(p, { status: "archived", version: p.version + 1, updated_at: new Date().toISOString() });
        save();
        share(p);
        return adminProduct(p);
      },
      async restore(id) {
        need("shop_owner");
        await pause();
        const p = product(id);
        Object.assign(p, { status: "active", version: p.version + 1, updated_at: new Date().toISOString() });
        save();
        share(p);
        return adminProduct(p);
      },
      async adjust(id, delta, reason) {
        need("shop_owner");
        await pause();
        const p = product(id);
        if (p.available_quantity + delta < 0) fail(409, "insufficient-stock", "Not enough stock", `Only ${p.available_quantity} available.`);
        p.available_quantity += delta;
        p.in_stock = p.available_quantity > 0;
        const a: Adjustment = { id: uuid(), product_id: id, delta, reason, resulting_quantity: p.available_quantity, created_at: new Date().toISOString() };
        state.adjustments.unshift(a);
        save();
        share(p);
        return a;
      },
      async adjustments(id) {
        need("shop_owner");
        await pause(50);
        return page(state.adjustments.filter((a) => a.product_id === id), 1, 20);
      },
      async orders(q) {
        need("shop_owner");
        await pause();
        state.orders.forEach(settle);
        return page(state.orders.filter((o) => !q.status || o.status === q.status).map(ownerOrder), q.page, 50);
      },
      async refund(id) {
        need("shop_owner");
        await pause(400);
        const o = order(id);
        if (o.status !== "paid" && o.status !== "fulfilled") fail(409, "invalid-state-transition", "Cannot be refunded", "Only paid orders can be refunded.");
        o.status = "refunded";
        o.payment = { status: "refunded", failure_code: null };
        save();
        return ownerOrder(o);
      },
    },
  };
  return demoApi;
}
