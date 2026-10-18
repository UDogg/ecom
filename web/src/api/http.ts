// The live backend: the BFF on this origin. The browser holds a session cookie and a CSRF token,
// never a token for a core service.
import { ApiError, type Api, type Me, type Problem, type ProductQuery } from "./types";

let csrf = "";

function query(params: object): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "" && v !== null) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? "?" + s : "";
}

async function call<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const h: Record<string, string> = { Accept: "application/json", ...headers };
  if (method !== "GET") {
    if (!csrf) await me();
    h["X-CSRF-Token"] = csrf;
  }
  if (body !== undefined) h["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const problem: Problem = data && typeof data === "object" && "type" in data
      ? data
      : { type: "about:blank", title: res.statusText || "Request failed", status: res.status };
    if (problem.type.endsWith("/csrf")) csrf = ""; // a new session: fetch the token again next time
    throw new ApiError(problem);
  }
  return data as T;
}

async function me(): Promise<Me> {
  const m = await call<Me>("GET", "/api/me");
  csrf = m.csrf_token;
  return m;
}

export const httpApi: Api = {
  demo: false,
  me,
  async signIn(returnTo) {
    location.assign("/auth/login" + query({ return_to: returnTo }));
  },
  async signOut() {
    await call("POST", "/auth/logout");
    csrf = "";
  },
  async register(email, password) {
    await call("POST", "/api/auth/register", { email, password });
  },

  products: (q: ProductQuery) => call("GET", "/api/catalog/products" + query(q)),
  product: (id) => call("GET", `/api/catalog/products/${id}`),
  categories: async () => (await call<{ items: never[] }>("GET", "/api/catalog/categories")).items,
  recommendations: (productId, limit = 8) => call("GET", "/api/recommendations" + query({ product_id: productId, limit })),

  cart: () => call("GET", "/api/cart"),
  setCart: (items) => call("PUT", "/api/cart", { items }),
  checkout: (items, key) => call("POST", "/api/checkout", { items }, { "Idempotency-Key": key }),
  orders: (page = 1) => call("GET", "/api/orders" + query({ page, page_size: 20 })),
  order: (id) => call("GET", `/api/orders/${id}`),
  pay: (id, source) => call("POST", `/api/orders/${id}/pay`, { source }),

  admin: {
    dashboard: () => call("GET", "/api/admin/dashboard"),
    products: (q) => call("GET", "/api/admin/products" + query({ ...q, page_size: 50 })),
    product: (id) => call("GET", `/api/admin/products/${id}`),
    create: (p) => call("POST", "/api/admin/products", p),
    update: (id, version, p) => call("PATCH", `/api/admin/products/${id}`, p, { "If-Match": `"${version}"` }),
    archive: (id) => call("DELETE", `/api/admin/products/${id}`),
    restore: (id) => call("POST", `/api/admin/products/${id}/restore`),
    adjust: (id, delta, reason) => call("POST", `/api/admin/inventory/${id}/adjust`, { delta, reason }),
    adjustments: (id) => call("GET", `/api/admin/inventory/${id}/adjustments` + query({ page_size: 20 })),
    orders: (q) => call("GET", "/api/admin/orders" + query({ ...q, page_size: 50 })),
    refund: (id) => call("POST", `/api/admin/orders/${id}/refund`),
  },
};
