import { useEffect, useRef } from "preact/hooks";
import type { Product } from "../api";
import { reveal, tiltLeave, tiltMove } from "../lib/fx";
import { href } from "../lib/router";
import { add, cartOpen } from "../state/cart";
import { toast } from "../state/toast";
import { Art } from "./Art";
import { Icon } from "./Icon";
import { Price } from "./Price";

export function ProductCard({ product: p, index = 0 }: { product: Product; index?: number }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => reveal(ref.current, Math.min(index, 8) * 45), [p.id]);
  const quickAdd = (e: Event) => {
    e.preventDefault();
    add(p);
    const el = ref.current;
    if (el) {
      el.classList.remove("added");
      void el.offsetWidth; // restart the animation
      el.classList.add("added");
    }
    toast(`${p.name} added to your bag`, "ok", { label: "View bag", run: () => (cartOpen.value = true) });
  };
  return (
    <article ref={ref} class="card tilt" onPointerMove={tiltMove} onPointerLeave={tiltLeave}>
      <div class="media">
        <Art product={p} />
        <span class="sheen" aria-hidden="true" />
        {!p.in_stock && <span class="flag badge bad">Sold out</span>}
      </div>
      <div class="body">
        <span class="tiny muted">{p.category?.name}</span>
        <a class="name" href={href(`/products/${p.id}`)}>{p.name}</a>
        <p class="desc">{p.description}</p>
      </div>
      <div class="foot">
        <Price minor={p.price_minor} currency={p.currency} />
        <button class="btn btn-sm quick" disabled={!p.in_stock} onClick={quickAdd} aria-label={`Add ${p.name} to bag`}>
          <Icon name="plus" size={16} /> Add
        </button>
      </div>
    </article>
  );
}

export function CardSkeleton() {
  return (
    <div class="stack" style={{ "--gap": "10px" }} aria-hidden="true">
      <div class="sk sk-card" />
      <div class="sk sk-line" style={{ width: "70%" }} />
      <div class="sk sk-line" style={{ width: "40%" }} />
    </div>
  );
}

export function Grid({ products, loading, count = 8 }: { products?: Product[]; loading?: boolean; count?: number }) {
  return (
    <div class="grid" aria-busy={loading}>
      {loading && !products ? Array.from({ length: count }, (_, i) => <CardSkeleton key={i} />) : products?.map((p, i) => <ProductCard key={p.id} product={p} index={i} />)}
    </div>
  );
}
