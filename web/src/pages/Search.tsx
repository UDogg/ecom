import { useState } from "preact/hooks";
import { api, type Sort } from "../api";
import { Icon } from "../components/Icon";
import { Grid } from "../components/ProductCard";
import { plural } from "../lib/format";
import { useAsync, useTitle } from "../lib/hooks";
import { loc, setQuery } from "../lib/router";

const SORTS: [Sort, string][] = [["relevance", "Best match"], ["newest", "Newest"], ["price_asc", "Price: low to high"], ["price_desc", "Price: high to low"], ["name", "Name"]];
const PRICES: [string, number | undefined, number | undefined][] = [["Any price", undefined, undefined], ["Under $25", undefined, 2500], ["$25 to $50", 2500, 5000], ["$50 to $100", 5000, 10000], ["Over $100", 10000, undefined]];

export function Search() {
  const q = loc.value.query;
  const params = {
    q: q.get("q") ?? undefined,
    category: q.get("category") ?? undefined,
    min_price: q.has("min_price") ? Number(q.get("min_price")) : undefined,
    max_price: q.has("max_price") ? Number(q.get("max_price")) : undefined,
    sort: (q.get("sort") as Sort | null) ?? undefined,
    page: q.has("page") ? Number(q.get("page")) : 1,
    page_size: 24,
  };
  const key = q.toString();
  const result = useAsync(() => api.products(params), [key]);
  const cats = useAsync(() => api.categories(), []);
  const [sheet, setSheet] = useState(false);
  const catName = cats.data?.find((c) => c.slug === params.category)?.name;
  const title = params.q ? `“${params.q}”` : catName ?? "All products";
  useTitle(params.q ? `Search: ${params.q}` : catName ?? "Shop");

  const priceKey = `${params.min_price ?? ""}-${params.max_price ?? ""}`;
  return (
    <div class="wrap shop">
      <aside class={`filters ${sheet ? "open" : ""}`} aria-label="Filters">
        <div>
          <h3>Category</h3>
          <div class="filter-list">
            <button aria-pressed={!params.category} onClick={() => setQuery({ category: undefined, page: undefined })}>All</button>
            {cats.data?.map((c) => (
              <button key={c.slug} aria-pressed={params.category === c.slug} onClick={() => { setQuery({ category: c.slug, page: undefined }); setSheet(false); }}>{c.name}</button>
            ))}
          </div>
        </div>
        <div>
          <h3>Price</h3>
          <div class="filter-list">
            {PRICES.map(([label, lo, hi]) => (
              <button key={label} aria-pressed={priceKey === `${lo ?? ""}-${hi ?? ""}`} onClick={() => { setQuery({ min_price: lo, max_price: hi, page: undefined }); setSheet(false); }}>{label}</button>
            ))}
          </div>
        </div>
        <button class="btn btn-dark filters-toggle" onClick={() => setSheet(false)}>Show results</button>
      </aside>

      <section>
        <div class="toolbar">
          <div>
            <h1>{title}</h1>
            <span class="muted small" aria-live="polite">{result.data ? plural(result.data.total, "product") : "Searching…"}</span>
          </div>
          <div class="row">
            <button class="btn btn-sm filters-toggle" onClick={() => setSheet(true)}><Icon name="filter" size={16} /> Filters</button>
            <label class="sr-only" for="sort">Sort by</label>
            <select id="sort" class="select" value={params.sort ?? (params.q ? "relevance" : "newest")} onChange={(e) => setQuery({ sort: e.currentTarget.value, page: undefined })}>
              {SORTS.filter(([s]) => s !== "relevance" || params.q).map(([s, label]) => <option key={s} value={s}>{label}</option>)}
            </select>
          </div>
        </div>
        {(params.category || params.min_price || params.max_price) && (
          <div class="chips" style={{ marginBottom: "16px" }}>
            {catName && <button class="chip on" onClick={() => setQuery({ category: undefined })}>{catName} <Icon name="close" size={14} /></button>}
            {(params.min_price || params.max_price) && <button class="chip on" onClick={() => setQuery({ min_price: undefined, max_price: undefined })}>{PRICES.find(([, lo, hi]) => `${lo ?? ""}-${hi ?? ""}` === priceKey)?.[0] ?? "Price"} <Icon name="close" size={14} /></button>}
          </div>
        )}
        {result.error ? (
          <div class="empty"><Icon name="alert" size={40} /><h3>We could not load the shop</h3><button class="btn" onClick={result.reload}>Try again</button></div>
        ) : result.data && result.data.items.length === 0 ? (
          <div class="empty">
            <Icon name="search" size={44} />
            <h3>Nothing matches {params.q ? `“${params.q}”` : "these filters"}</h3>
            <p class="muted">Try a broader word, or clear the filters.</p>
            <button class="btn btn-dark" onClick={() => setQuery({ q: undefined, category: undefined, min_price: undefined, max_price: undefined, page: undefined })}>Clear everything</button>
          </div>
        ) : (
          <Grid products={result.data?.items} loading={result.loading} count={12} />
        )}
        {result.data && result.data.pages > 1 && (
          <nav class="pager" aria-label="Pages">
            {Array.from({ length: result.data.pages }, (_, i) => i + 1).map((n) => (
              <button key={n} class={`btn btn-sm ${n === params.page ? "btn-dark" : ""}`} aria-current={n === params.page ? "page" : undefined}
                onClick={() => { setQuery({ page: n === 1 ? undefined : n }, false); scrollTo({ top: 0, behavior: "smooth" }); }}>{n}</button>
            ))}
          </nav>
        )}
      </section>
      {sheet && <div class="scrim" onClick={() => setSheet(false)} />}
    </div>
  );
}
