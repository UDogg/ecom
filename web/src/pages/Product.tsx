import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { Art } from "../components/Art";
import { Icon } from "../components/Icon";
import { Price } from "../components/Price";
import { Stepper } from "../components/Stepper";
import { PickRail } from "./Home";
import { useAsync, useTitle } from "../lib/hooks";
import { href, navigate } from "../lib/router";
import { add, cartOpen, FREE_SHIPPING_MINOR } from "../state/cart";
import { viewed } from "../state/recent";
import { toast } from "../state/toast";
import { price } from "../lib/format";
import { NotFound } from "./NotFound";

export function Product({ id }: { id: string }) {
  const product = useAsync(() => api.product(id), [id]);
  const recs = useAsync(() => api.recommendations(id, 8), [id]);
  const [qty, setQty] = useState(1);
  const p = product.data;
  useTitle(p?.name);
  useEffect(() => { if (p) viewed(p); setQty(1); }, [p?.id]);

  if (product.error) return <NotFound what="product" />;
  if (!p) {
    return (
      <div class="wrap pdp" aria-busy="true">
        <div class="sk" style={{ aspectRatio: "1", borderRadius: "22px" }} />
        <div class="stack"><div class="sk sk-line" style={{ width: "30%" }} /><div class="sk" style={{ height: "48px", width: "80%" }} /><div class="sk sk-line" /><div class="sk" style={{ height: "220px", borderRadius: "22px" }} /></div>
      </div>
    );
  }
  const buy = (now: boolean) => {
    add(p, qty);
    if (now) navigate("/checkout");
    else toast(`${qty} × ${p.name} added to your bag`, "ok", { label: "View bag", run: () => (cartOpen.value = true) });
  };
  return (
    <div class="wrap">
      <nav class="crumbs" aria-label="Breadcrumbs">
        <a href={href("/")}>Home</a><span>/</span>
        {p.category && <><a href={href(`/search?category=${p.category.slug}`)}>{p.category.name}</a><span>/</span></>}
        <span aria-current="page">{p.name}</span>
      </nav>
      <div class="pdp">
        <div class="gallery">
          <div class="main"><Art product={p} size="lg" /></div>
          <div class="thumbs" aria-hidden="true">
            {[0, 1, 2, 3].map((n) => <Art key={n} product={{ ...p, sku: `${p.sku}-${n}` }} size="sm" />)}
          </div>
        </div>
        <div class="buybox">
          <span class="eyebrow">{p.category?.name}</span>
          <h1>{p.name}</h1>
          <p class="lede">{p.description}</p>
          <div class="panel">
            <div class="row between">
              <Price minor={p.price_minor} currency={p.currency} class="price-lg" />
              {p.in_stock ? <span class="stock-ok">In stock</span> : <span class="stock-out">Sold out</span>}
            </div>
            <div class="row wrap-row">
              <Stepper value={qty} onChange={setQty} />
              <button class="btn btn-primary btn-lg grow" disabled={!p.in_stock} onClick={() => buy(false)}><Icon name="bag" /> Add to bag</button>
            </div>
            <button class="btn btn-dark btn-block" disabled={!p.in_stock} onClick={() => buy(true)}>Buy now</button>
            <div class="assurances">
              <div class="row"><Icon name="truck" /> {p.price_minor * qty >= FREE_SHIPPING_MINOR ? "Free delivery on this order" : `Free delivery over ${price(FREE_SHIPPING_MINOR)}`}</div>
              <div class="row"><Icon name="shield" /> Reserved for you for 15 minutes once you check out</div>
              <div class="row"><Icon name="refresh" /> Refunds go straight back to your card</div>
            </div>
          </div>
          <dl class="specs">
            <dt>SKU</dt><dd class="mono">{p.sku}</dd>
            <dt>Category</dt><dd>{p.category?.name ?? "—"}</dd>
            <dt>Price</dt><dd>{price(p.price_minor, p.currency)} incl. taxes</dd>
          </dl>
        </div>
      </div>
      {recs.data && recs.data.items.length > 0 && (
        <section class="section">
          <div class="section-head"><div><span class="eyebrow">{recs.data.degraded ? "More to explore" : "Customers also liked"}</span><h2>You might also like</h2></div></div>
          <PickRail ids={recs.data.items.map((r) => r.product_id)} />
        </section>
      )}
    </div>
  );
}
