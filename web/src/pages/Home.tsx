import { api } from "../api";
import { Art } from "../components/Art";
import { Hero3D } from "../components/Hero3D";
import { Icon } from "../components/Icon";
import { Grid, ProductCard } from "../components/ProductCard";
import { reveal, tiltLeave, tiltMove } from "../lib/fx";
import { useAsync, useTitle } from "../lib/hooks";
import { href } from "../lib/router";
import { recent } from "../state/recent";

export function Home() {
  useTitle(undefined);
  const newest = useAsync(() => api.products({ sort: "newest", page_size: 10 }), []);
  const picks = useAsync(() => api.recommendations(undefined, 10), []);
  const cats = useAsync(() => api.categories(), []);
  const under = useAsync(() => api.products({ max_price: 3000, sort: "price_desc", page_size: 5 }), []);
  const hero = (newest.data?.items ?? []).filter((p, i, all) => all.findIndex((q) => q.category?.slug === p.category?.slug) === i).slice(0, 3);

  return (
    <div class="wrap">
      <section class="hero">
        <Hero3D class="hero-bg" />
        <div class="hero-inner">
          <div>
            <span class="eyebrow">New season · Free delivery over $50</span>
            <h1>Goods worth<br />keeping.</h1>
            <p>Forty well-made things for the kitchen, the desk and the trail. Reserved the moment you check out, so nobody sells your last one.</p>
            <div class="cta">
              <a class="btn btn-dark btn-lg" href={href("/search")}>Shop everything <Icon name="arrow" /></a>
              <a class="btn btn-lg" href={href("/search?category=coffee")}>Coffee & tea</a>
            </div>
          </div>
          <div class="hero-collage">
            {hero.map((p) => (
              <a class="tile tilt" key={p.id} href={href(`/products/${p.id}`)} aria-label={p.name}
                onPointerMove={(e) => tiltMove(e, 10)} onPointerLeave={tiltLeave}>
                <Art product={p} size="lg" />
                <span class="sheen" aria-hidden="true" />
              </a>
            ))}
          </div>
        </div>
      </section>

      <div class="perks" ref={reveal}>
        <div class="perk"><Icon name="truck" /><span><b>Free delivery</b><span class="muted">on orders over $50</span></span></div>
        <div class="perk"><Icon name="shield" /><span><b>Stock held for you</b><span class="muted">15 minutes at checkout</span></span></div>
        <div class="perk"><Icon name="refresh" /><span><b>Easy refunds</b><span class="muted">straight to your card</span></span></div>
        <div class="perk"><Icon name="card" /><span><b>Charged once</b><span class="muted">even if you click twice</span></span></div>
      </div>

      <section class="section" ref={reveal}>
        <div class="section-head"><h2>Shop by category</h2></div>
        <div class="cats">
          {(cats.data ?? []).map((c) => (
            <a class="cat" key={c.slug} href={href(`/search?category=${c.slug}`)}>
              <Art product={{ sku: c.slug + "-cat", name: c.name, category: c }} />
              <span>{c.name}</span>
            </a>
          ))}
        </div>
      </section>

      <section class="section" ref={reveal}>
        <div class="section-head">
          <div><span class="eyebrow">Just in</span><h2>New arrivals</h2></div>
          <a class="btn btn-sm" href={href("/search?sort=newest")}>See all <Icon name="arrow" size={16} /></a>
        </div>
        <Grid products={newest.data?.items} loading={newest.loading} count={10} />
      </section>

      {picks.data && picks.data.items.length > 0 && (
        <section class="section" ref={reveal}>
          <div class="section-head"><div><span class="eyebrow">Popular right now</span><h2>Picked for you</h2></div></div>
          <PickRail ids={picks.data.items.map((i) => i.product_id)} />
        </section>
      )}

      <section class="section" ref={reveal}>
        <div class="section-head">
          <div><span class="eyebrow">Small luxuries</span><h2>Under $30</h2></div>
          <a class="btn btn-sm" href={href("/search?max_price=3000&sort=price_asc")}>See all <Icon name="arrow" size={16} /></a>
        </div>
        <Grid products={under.data?.items} loading={under.loading} count={5} />
      </section>

      {recent.value.length > 0 && (
        <section class="section" ref={reveal}>
          <div class="section-head"><h2>Recently viewed</h2></div>
          <div class="rail">{recent.value.map((p) => <ProductCard key={p.id} product={p} />)}</div>
        </section>
      )}
    </div>
  );
}

/** Recommendations carry ids and prices; load the full products (cached by the BFF) for cards. */
export function PickRail({ ids }: { ids: string[] }) {
  const products = useAsync(() => Promise.all(ids.map((id) => api.product(id).catch(() => null))), [ids.join()]);
  if (!products.data) return <div class="rail">{ids.map((id) => <div key={id} class="sk sk-card" />)}</div>;
  return <div class="rail">{products.data.filter((p) => p !== null).map((p) => <ProductCard key={p!.id} product={p!} />)}</div>;
}
