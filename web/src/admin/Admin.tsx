// The shop owner's back office. A separate chunk: customers never download it.
import { useState } from "preact/hooks";
import { api, ApiError, type AdminOrder, type AdminProduct, type OrderStatus, type ProductStatus } from "../api";
import { Art } from "../components/Art";
import { Icon, type IconName } from "../components/Icon";
import { Modal } from "../components/Overlay";
import { Price } from "../components/Price";
import { ago, date, dateTime, price, shortId } from "../lib/format";
import { useAsync, useDebounced, useTitle } from "../lib/hooks";
import { href, loc, setQuery } from "../lib/router";
import { isOwner } from "../state/session";
import { toast } from "../state/toast";
import { STATUS, StatusBadge } from "../pages/Orders";

const NAV: [string, string, IconName][] = [["/admin", "Overview", "chart"], ["/admin/products", "Products", "tag"], ["/admin/orders", "Orders", "box"]];

export default function Admin() {
  const path = loc.value.path;
  if (!isOwner.value) {
    return <div class="wrap empty"><Icon name="shield" size={44} /><h1>Shop owners only</h1><p class="muted">Sign in with the owner account to manage the shop.</p></div>;
  }
  return (
    <div class="wrap admin">
      <nav class="admin-nav" aria-label="Admin">
        {NAV.map(([to, label, icon]) => (
          <a key={to} href={href(to)} aria-current={path === to ? "page" : undefined}><Icon name={icon} size={18} /> {label}</a>
        ))}
      </nav>
      <section>{path === "/admin/products" ? <Products /> : path === "/admin/orders" ? <Orders /> : <Overview />}</section>
    </div>
  );
}

// ── overview ─────────────────────────────────────────────────────────────────

function Overview() {
  useTitle("Overview");
  const dash = useAsync(() => api.admin.dashboard(), []);
  const all = useAsync(() => api.admin.orders({}), []);
  const d = dash.data;
  const orders = all.data?.items ?? [];
  const paid = orders.filter((o) => o.status === "paid" || o.status === "fulfilled");
  const revenue = paid.reduce((s, o) => s + o.total_minor, 0);
  const days = Array.from({ length: 7 }, (_, i) => {
    const day = new Date(Date.now() - (6 - i) * 86_400_000).toDateString();
    return { label: new Date(day).toLocaleDateString(undefined, { weekday: "short" }), total: paid.filter((o) => new Date(o.created_at).toDateString() === day).reduce((s, o) => s + o.total_minor, 0) };
  });
  const peak = Math.max(1, ...days.map((d) => d.total));
  return (
    <div class="stack" style={{ "--gap": "20px" }}>
      <div class="row between"><h1>Overview</h1><button class="btn btn-sm" onClick={() => { dash.reload(); all.reload(); }}><Icon name="refresh" size={16} /> Refresh</button></div>
      <div class="kpis">
        <Kpi label="Revenue (paid orders)" value={price(revenue)} />
        <Kpi label="Orders" value={String(all.data?.total ?? "—")} />
        <Kpi label="Awaiting payment" value={String(orders.filter((o) => o.status === "pending_payment").length)} />
        <Kpi label="Low on stock" value={String(d?.low_stock.length ?? "—")} tone={d && d.low_stock.length > 0 ? "warn" : undefined} />
      </div>
      <div class="split">
        <div class="box">
          <div class="row between"><h3>Sales, last 7 days</h3><span class="muted small">{price(days.reduce((s, d) => s + d.total, 0))}</span></div>
          <div class="bars" role="img" aria-label="Paid order value per day">
            {days.map((d) => <div key={d.label} title={price(d.total)}><i style={{ height: `${(d.total / peak) * 90 + 3}px` }} />{d.label}</div>)}
          </div>
        </div>
        <div class="box">
          <h3>Event pipeline</h3>
          <div class="row"><span class={`badge ${d?.system.outbox_pending ? "warn" : "ok"}`}>{d?.system.outbox_pending ? `${d.system.outbox_pending} events waiting` : "All events delivered"}</span></div>
          <p class="small muted">Orders and stock changes are published through a transactional outbox: e-mails and alerts go out even if the broker was down when the order was placed.</p>
          {d?.system.outbox_oldest_pending_at && <p class="small">Oldest waiting since {ago(d.system.outbox_oldest_pending_at)}.</p>}
        </div>
      </div>
      <div class="split">
        <div class="box">
          <div class="row between"><h3>Recent orders</h3><a class="small" href={href("/admin/orders")}>All orders →</a></div>
          <OrderTable orders={(d?.recent_orders ?? []) as AdminOrder[]} compact onChange={() => dash.reload()} />
        </div>
        <div class="box">
          <h3>Running low</h3>
          {d?.low_stock.length === 0 && <p class="muted small">Everything is well stocked.</p>}
          {d?.low_stock.map((l) => (
            <div class="row between" key={l.product_id}>
              <span class="row" style={{ "--gap": "10px" }}><span class="thumb"><Art product={{ sku: l.sku, name: l.name }} size="sm" /></span><span>{l.name}<br /><span class="tiny muted mono">{l.sku}</span></span></span>
              <span class={`badge ${l.available_quantity === 0 ? "bad" : "warn"}`}>{l.available_quantity} left</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  return <div class="kpi"><span class="l">{label}</span><span class={`v ${tone === "warn" ? "stock-low" : ""}`}>{value}</span></div>;
}

// ── products ─────────────────────────────────────────────────────────────────

function Products() {
  useTitle("Products");
  const q = loc.value.query;
  const [text, setText] = useState(q.get("q") ?? "");
  const search = useDebounced(text, 200);
  const status = (q.get("status") as ProductStatus | null) ?? undefined;
  const list = useAsync(() => api.admin.products({ q: search || undefined, status }), [search, status]);
  const [editing, setEditing] = useState<AdminProduct | "new" | null>(null);
  const [stock, setStock] = useState<AdminProduct | null>(null);

  const archive = async (p: AdminProduct) => {
    try {
      await (p.status === "archived" ? api.admin.restore(p.id) : api.admin.archive(p.id));
      toast(p.status === "archived" ? `${p.name} is back on sale` : `${p.name} archived`, "ok");
      list.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div class="stack" style={{ "--gap": "18px" }}>
      <div class="row between wrap-row"><h1>Products</h1><button class="btn btn-primary" onClick={() => setEditing("new")}><Icon name="plus" /> New product</button></div>
      <div class="row wrap-row">
        <input class="input grow" type="search" placeholder="Search name or SKU" value={text} onInput={(e) => setText(e.currentTarget.value)} aria-label="Search products" />
        <div class="chips">
          {([undefined, "active", "draft", "archived"] as (ProductStatus | undefined)[]).map((s) => (
            <button key={s ?? "all"} class="chip" aria-pressed={status === s} onClick={() => setQuery({ status: s })}>{s ? s[0]!.toUpperCase() + s.slice(1) : "All"}</button>
          ))}
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Product</th><th>Status</th><th class="num">Price</th><th class="num">Stock</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {list.loading && !list.data && <tr><td colSpan={6}><div class="sk sk-line" /></td></tr>}
            {list.data?.items.map((p) => (
              <tr key={p.id}>
                <td><span class="row" style={{ "--gap": "12px" }}><span class="thumb"><Art product={p} size="sm" /></span><span><b>{p.name}</b><br /><span class="tiny muted mono">{p.sku} · {p.category?.name}</span></span></span></td>
                <td><span class={`badge ${p.status === "active" ? "ok" : p.status === "draft" ? "warn" : "plain"}`}>{p.status}</span></td>
                <td class="num">{price(p.price_minor, p.currency)}</td>
                <td class="num"><button class="btn btn-sm" onClick={() => setStock(p)}><span class={p.available_quantity === 0 ? "stock-out" : p.available_quantity <= 5 ? "stock-low" : ""}>{p.available_quantity}</span> <Icon name="edit" size={14} /></button></td>
                <td class="small muted">{ago(p.updated_at)}</td>
                <td class="num">
                  <span class="row" style={{ "--gap": "4px", justifyContent: "end" }}>
                    <button class="btn btn-ghost btn-sm" onClick={() => setEditing(p)} aria-label={`Edit ${p.name}`}><Icon name="edit" size={16} /></button>
                    <button class="btn btn-ghost btn-sm" onClick={() => archive(p)} aria-label={p.status === "archived" ? `Restore ${p.name}` : `Archive ${p.name}`}><Icon name={p.status === "archived" ? "undo" : "archive"} size={16} /></button>
                  </span>
                </td>
              </tr>
            ))}
            {list.data?.items.length === 0 && <tr><td colSpan={6} class="center muted">No products match.</td></tr>}
          </tbody>
        </table>
      </div>
      {editing && <ProductEditor product={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload(); }} />}
      {stock && <StockEditor product={stock} onClose={() => setStock(null)} onSaved={() => { setStock(null); list.reload(); }} />}
    </div>
  );
}

function ProductEditor({ product, onClose, onSaved }: { product: AdminProduct | null; onClose: () => void; onSaved: () => void }) {
  const cats = useAsync(() => api.categories(), []);
  const [f, setF] = useState({
    name: product?.name ?? "", sku: product?.sku ?? "", description: product?.description ?? "",
    price: product ? (product.price_minor / 100).toFixed(2) : "", category: product?.category?.slug ?? "kitchen",
    status: (product?.status === "draft" ? "draft" : "active") as "draft" | "active", stock: "10",
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: Event) => setF({ ...f, [k]: (e.currentTarget as HTMLInputElement).value });
  const save = async (e: Event) => {
    e.preventDefault();
    const minor = Math.round(Number(f.price) * 100);
    if (!f.name.trim() || !Number.isFinite(minor) || minor < 0) {
      setErrors({ ...(f.name.trim() ? {} : { name: "Give it a name" }), ...(Number.isFinite(minor) && minor >= 0 ? {} : { price_minor: "A price such as 12.99" }) });
      return;
    }
    setBusy(true);
    try {
      const common = { name: f.name.trim(), description: f.description, price_minor: minor, currency: "USD", category: f.category, status: f.status };
      if (product) await api.admin.update(product.id, product.version, common);
      else await api.admin.create({ ...common, sku: f.sku.trim(), initial_stock: Number(f.stock) || 0 });
      toast(product ? "Saved" : `${f.name} created`);
      onSaved();
    } catch (err) {
      const p = (err as ApiError).problem;
      if (p?.type.endsWith("version-conflict")) toast("Someone else changed this product meanwhile. Reopen it to see their version.", "error");
      setErrors(p?.errors ?? { form: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={product ? `Edit ${product.name}` : "New product"} onClose={onClose}>
      <form class="stack" onSubmit={save} noValidate>
        {errors.form && <div class="callout bad"><Icon name="alert" /><span>{errors.form}</span></div>}
        <label class="field"><span>Name</span><input class="input" value={f.name} onInput={set("name")} aria-invalid={!!errors.name} autofocus />{errors.name && <span class="err">{errors.name}</span>}</label>
        <div class="row wrap-row" style={{ alignItems: "start" }}>
          <label class="field grow"><span>SKU</span><input class="input mono" value={f.sku} onInput={set("sku")} disabled={!!product} aria-invalid={!!errors.sku} />{errors.sku && <span class="err">{errors.sku}</span>}</label>
          <label class="field grow"><span>Price (USD)</span><input class="input" inputMode="decimal" value={f.price} onInput={set("price")} aria-invalid={!!errors.price_minor} />{errors.price_minor && <span class="err">{errors.price_minor}</span>}</label>
        </div>
        <label class="field"><span>Description</span><textarea class="textarea" value={f.description} onInput={set("description")} /></label>
        <div class="row wrap-row" style={{ alignItems: "start" }}>
          <label class="field grow"><span>Category</span>
            <select class="select" value={f.category} onChange={set("category")}>{cats.data?.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select>
          </label>
          <label class="field grow"><span>Status</span>
            <select class="select" value={f.status} onChange={set("status")}><option value="active">Active (on sale)</option><option value="draft">Draft (hidden)</option></select>
          </label>
          {!product && <label class="field grow"><span>Opening stock</span><input class="input" inputMode="numeric" value={f.stock} onInput={set("stock")} /></label>}
        </div>
        {product && <p class="tiny muted">Version {product.version}. If someone else saves first, your change is refused rather than overwriting theirs.</p>}
        <div class="row" style={{ justifyContent: "end" }}>
          <button type="button" class="btn" onClick={onClose}>Cancel</button>
          <button class="btn btn-primary" disabled={busy}>{busy && <span class="spinner" />} {product ? "Save changes" : "Create product"}</button>
        </div>
      </form>
    </Modal>
  );
}

function StockEditor({ product, onClose, onSaved }: { product: AdminProduct; onClose: () => void; onSaved: () => void }) {
  const history = useAsync(() => api.admin.adjustments(product.id), [product.id]);
  const [delta, setDelta] = useState("10");
  const [reason, setReason] = useState("Restock");
  const [busy, setBusy] = useState(false);
  const n = Math.trunc(Number(delta));
  const save = async (e: Event) => {
    e.preventDefault();
    if (!n || !reason.trim()) return;
    setBusy(true);
    try {
      const a = await api.admin.adjust(product.id, n, reason.trim());
      toast(`${product.name}: ${a.resulting_quantity} in stock`);
      onSaved();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Stock: ${product.name}`} onClose={onClose}>
      <form class="stack" onSubmit={save}>
        <div class="row between"><span class="muted">Available now</span><b class="price-lg">{product.available_quantity}</b></div>
        <div class="row wrap-row" style={{ alignItems: "end" }}>
          <label class="field"><span>Change by</span><input class="input" inputMode="numeric" value={delta} onInput={(e) => setDelta(e.currentTarget.value)} autofocus /></label>
          <label class="field grow"><span>Reason</span><input class="input" value={reason} onInput={(e) => setReason(e.currentTarget.value)} /></label>
          <button class="btn btn-primary" disabled={busy || !n}>{busy && <span class="spinner" />} {n < 0 ? "Remove" : "Add"} {Math.abs(n) || ""}</button>
        </div>
        <p class="tiny muted">Every change is recorded with its reason; customers' reservations are never affected.</p>
        <h3>History</h3>
        {history.data?.items.length === 0 && <p class="muted small">No adjustments yet.</p>}
        {history.data?.items.map((a) => (
          <div class="row between small" key={a.id}>
            <span><b class={a.delta < 0 ? "stock-out" : "stock-ok"}>{a.delta > 0 ? "+" : ""}{a.delta}</b> {a.reason}</span>
            <span class="muted">→ {a.resulting_quantity} · {dateTime(a.created_at)}</span>
          </div>
        ))}
      </form>
    </Modal>
  );
}

// ── orders ───────────────────────────────────────────────────────────────────

function Orders() {
  useTitle("Orders");
  const status = (loc.value.query.get("status") as OrderStatus | null) ?? undefined;
  const list = useAsync(() => api.admin.orders({ status }), [status]);
  return (
    <div class="stack" style={{ "--gap": "18px" }}>
      <h1>Orders</h1>
      <div class="chips">
        <button class="chip" aria-pressed={!status} onClick={() => setQuery({ status: undefined })}>All</button>
        {(Object.keys(STATUS) as OrderStatus[]).map((s) => <button key={s} class="chip" aria-pressed={status === s} onClick={() => setQuery({ status: s })}>{STATUS[s][0]}</button>)}
      </div>
      {list.loading && !list.data ? <div class="sk" style={{ height: "200px" }} /> : <OrderTable orders={list.data?.items ?? []} onChange={list.reload} />}
    </div>
  );
}

function OrderTable({ orders, compact = false, onChange }: { orders: AdminOrder[]; compact?: boolean; onChange: () => void }) {
  const [refunding, setRefunding] = useState<AdminOrder | null>(null);
  const [busy, setBusy] = useState(false);
  if (orders.length === 0) return <p class="muted small">No orders yet.</p>;
  const refund = async () => {
    if (!refunding) return;
    setBusy(true);
    try {
      await api.admin.refund(refunding.id);
      toast(`Order ${shortId(refunding.id)} refunded`);
      setRefunding(null);
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div class="table-wrap">
      <table>
        <thead><tr><th>Order</th>{!compact && <th>Placed</th>}<th>Status</th><th class="num">Total</th>{!compact && <th />}</tr></thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td><b class="mono">{shortId(o.id)}</b><br /><span class="tiny muted">{o.items.length} line{o.items.length === 1 ? "" : "s"}{compact ? ` · ${ago(o.created_at)}` : ""}</span></td>
              {!compact && <td class="small">{date(o.created_at)}<br /><span class="tiny muted">{ago(o.created_at)}</span></td>}
              <td><StatusBadge status={o.status} /></td>
              <td class="num"><Price minor={o.total_minor} currency={o.currency} /></td>
              {!compact && <td class="num">{(o.status === "paid" || o.status === "fulfilled") && <button class="btn btn-sm btn-danger" onClick={() => setRefunding(o)}><Icon name="undo" size={16} /> Refund</button>}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {refunding && (
        <Modal title={`Refund order ${shortId(refunding.id)}?`} onClose={() => setRefunding(null)}>
          <div class="stack">
            <p>The full <b>{price(refunding.total_minor, refunding.currency)}</b> goes back to the customer's card. This cannot be undone.</p>
            <div class="row" style={{ justifyContent: "end" }}>
              <button class="btn" onClick={() => setRefunding(null)} autofocus>Keep the order</button>
              <button class="btn btn-primary" disabled={busy} onClick={refund}>{busy && <span class="spinner" />} Refund {price(refunding.total_minor, refunding.currency)}</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
