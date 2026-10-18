import { signal } from "@preact/signals";
import type { Product } from "../api";
import { read, write } from "../lib/store";

const KEY = "bb-recent-v1";
export const recent = signal<Product[]>(read(KEY, []));

export function viewed(p: Product) {
  recent.value = [p, ...recent.value.filter((r) => r.id !== p.id)].slice(0, 12);
  write(KEY, recent.value);
}
