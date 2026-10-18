// The cart lives in the browser (it works signed out, and survives reloads) and is mirrored to the
// account's cart once signed in, so it follows the customer to another device.
import { computed, effect, signal } from "@preact/signals";
import { api, type Product } from "../api";
import { read, write } from "../lib/store";
import { signedIn } from "./session";

export interface CartItem {
  product_id: string;
  quantity: number;
  name: string;
  sku: string;
  price_minor: number;
  currency: string;
  category: { slug: string; name: string } | null;
  available?: boolean;
}

const KEY = "bb-cart-v1";
export const MAX_QTY = 99;

export const cart = signal<CartItem[]>(read(KEY, []));
export const cartOpen = signal(false);
export const count = computed(() => cart.value.reduce((n, i) => n + i.quantity, 0));
export const subtotal = computed(() => cart.value.filter((i) => i.available !== false).reduce((s, i) => s + i.price_minor * i.quantity, 0));
export const FREE_SHIPPING_MINOR = 5000;

effect(() => write(KEY, cart.value));

let syncTimer: ReturnType<typeof setTimeout> | undefined;
let synced = false;

function scheduleSync() {
  if (!signedIn.value) return;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(push, 350);
}

async function push() {
  try {
    const server = await api.setCart(cart.value.map(({ product_id, quantity }) => ({ product_id, quantity })));
    const availability = new Map(server.items.map((l) => [l.product_id, l.available]));
    cart.value = cart.value.map((i) => ({ ...i, available: availability.get(i.product_id) ?? i.available }));
  } catch { /* the local cart still works; the next change retries */ }
}

/** After sign-in: merge the account's saved cart into this browser's, then keep them in step. */
effect(() => {
  if (!signedIn.value || synced) return;
  synced = true;
  api.cart().then((server) => {
    const local = new Map(cart.value.map((i) => [i.product_id, i]));
    for (const l of server.items) {
      const mine = local.get(l.product_id);
      if (mine) mine.quantity = Math.min(MAX_QTY, Math.max(mine.quantity, l.quantity));
      else local.set(l.product_id, { product_id: l.product_id, quantity: l.quantity, name: l.name, sku: l.sku,
        price_minor: l.unit_price_minor, currency: l.currency, category: null, available: l.available });
    }
    cart.value = [...local.values()];
    push();
  }).catch(() => {});
});

export function add(p: Product, quantity = 1) {
  const existing = cart.value.find((i) => i.product_id === p.id);
  if (existing) {
    setQuantity(p.id, existing.quantity + quantity);
  } else {
    cart.value = [...cart.value, { product_id: p.id, quantity: Math.min(quantity, MAX_QTY), name: p.name, sku: p.sku,
      price_minor: p.price_minor, currency: p.currency, category: p.category, available: p.in_stock }];
    scheduleSync();
  }
}

export function setQuantity(id: string, quantity: number) {
  cart.value = quantity <= 0
    ? cart.value.filter((i) => i.product_id !== id)
    : cart.value.map((i) => (i.product_id === id ? { ...i, quantity: Math.min(quantity, MAX_QTY) } : i));
  scheduleSync();
}

export function remove(id: string) {
  setQuantity(id, 0);
}

export function clear() {
  cart.value = [];
  scheduleSync();
}

/** Forget the browser cart on sign-out (the account keeps its copy). */
export function forget() {
  synced = false;
  cart.value = [];
}
