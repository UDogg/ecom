import { useEffect } from "preact/hooks";
import { api, type Order, type OrderStatus } from "../api";
import { Art } from "../components/Art";
import { Icon } from "../components/Icon";
import { Price } from "../components/Price";
import { date, dateTime, plural, shortId } from "../lib/format";
import { useAsync, useTitle } from "../lib/hooks";
import { href, loc } from "../lib/router";
import { signedIn, signIn } from "../state/session";
import { Steps } from "./Checkout";
import { NotFound } from "./NotFound";

export const STATUS: Record<OrderStatus, [string, string]> = {
  pending_payment: ["Awaiting payment", "warn"],
  paid: ["Paid", "ok"],
  payment_failed: ["Payment failed", "bad"],
  expired: ["Expired", "plain"],
  cancelled: ["Cancelled", "plain"],
  fulfilled: ["Shipped", "info"],
  refunded: ["Refunded", "info"],
};

export function StatusBadge({ status }: { status: OrderStatus }) {
  const [label, tone] = STATUS[status];
  return <span class={`badge ${tone}`}>{label}</span>;
}

function SignInFirst({ next }: { next: string }) {
  return (
    <div class="wrap empty">
      <Icon name="user" size={44} />
      <h1>Sign in to see your orders</h1>
      <button class="btn btn-dark" onClick={() => (api.demo ? location.assign(href(`/signin?next=${next}`)) : signIn(next))}>Sign in</button>
    </div>
  );
}

export function Orders() {
  useTitle("Your orders");
  const orders = useAsync(() => (signedIn.value ? api.orders() : Promise.resolve(undefined)), [signedIn.value]);
  if (!signedIn.value) return <SignInFirst next="/orders" />;
  return (
    <div class="wrap section stack" style={{ "--gap": "18px" }}>
      <h1>Your orders</h1>
      {orders.loading && !orders.data && [1, 2, 3].map((n) => <div key={n} class="sk" style={{ height: "120px", borderRadius: "14px" }} />)}
      {orders.data?.items.length === 0 && (
        <div class="empty"><Icon name="box" size={44} /><h3>No orders yet</h3><a class="btn btn-dark" href={href("/search")}>Start shopping</a></div>
      )}
      {orders.data?.items.map((o) => <OrderCard key={o.id} order={o} />)}
    </div>
  );
}

function OrderCard({ order: o }: { order: Order }) {
  const units = o.items.reduce((n, i) => n + i.quantity, 0);
  return (
    <a class="order-card" href={href(`/orders/${o.id}`)}>
      <div class="row between wrap-row">
        <div><b>Order {shortId(o.id)}</b> <span class="muted small">· {date(o.created_at)}</span></div>
        <StatusBadge status={o.status} />
      </div>
      <div class="row between">
        <div class="arts">{o.items.slice(0, 5).map((i) => <Art key={i.product_id} product={{ sku: i.sku, name: i.name }} size="sm" />)}</div>
        <div class="center"><Price minor={o.total_minor} currency={o.currency} /><br /><span class="tiny muted">{plural(units, "item")}</span></div>
      </div>
    </a>
  );
}

const TRACK: [string, (o: Order) => boolean][] = [
  ["Placed", () => true],
  ["Paid", (o) => ["paid", "fulfilled", "refunded"].includes(o.status)],
  ["Shipped", (o) => o.status === "fulfilled"],
  ["Delivered", () => false],
];

export function OrderPage({ id }: { id: string }) {
  const order = useAsync(() => api.order(id), [id]);
  const thanks = loc.value.query.has("thanks");
  const o = order.data;
  useTitle(o ? `Order ${shortId(o.id)}` : "Order");
  useEffect(() => {
    if (o?.payment.status !== "pending") return;
    const t = setTimeout(order.reload, 2000);
    return () => clearTimeout(t);
  }, [o]);

  if (!signedIn.value) return <SignInFirst next={`/orders/${id}`} />;
  if (order.error) return <NotFound what="order" />;
  if (!o) return <div class="wrap section"><div class="sk" style={{ height: "360px", borderRadius: "22px" }} /></div>;
  const failed = ["payment_failed", "expired", "cancelled"].includes(o.status);
  return (
    <div class="wrap section stack" style={{ "--gap": "22px" }}>
      {thanks && <Steps at={3} />}
      <div class="status-hero">
        <div class={`ring ${o.status === "paid" || o.status === "fulfilled" ? "ok" : failed ? "bad" : "wait"}`}>
          <Icon name={o.status === "paid" || o.status === "fulfilled" ? "check" : failed ? "alert" : o.status === "refunded" ? "refresh" : "clock"} size={36} />
        </div>
        <h1>{thanks && o.status === "paid" ? "Thank you! Your order is confirmed." : STATUS[o.status][0]}</h1>
        <p class="muted">Order {shortId(o.id)} · placed {dateTime(o.created_at)}{thanks ? " · a confirmation e-mail is on its way" : ""}</p>
        {(o.status === "pending_payment" || o.status === "payment_failed") && <a class="btn btn-primary" href={href(`/orders/${o.id}/pay`)}><Icon name="card" /> Pay now</a>}
      </div>
      {!failed && o.status !== "refunded" && (
        <div class="timeline" aria-label="Order progress">
          {TRACK.map(([name, done]) => (
            <div key={name} class={done(o) ? "done" : ""}><span class="node">{done(o) && <Icon name="check" size={14} />}</span>{name}</div>
          ))}
        </div>
      )}
      <div class="split">
        <div class="box">
          <h3>Items</h3>
          {o.items.map((i) => (
            <div class="line" key={i.product_id}>
              <a href={href(`/products/${i.product_id}`)}><Art product={{ sku: i.sku, name: i.name }} size="sm" /></a>
              <span><a class="name" href={href(`/products/${i.product_id}`)}>{i.name}</a><br /><span class="tiny muted">Qty {i.quantity} · <span class="mono">{i.sku}</span></span></span>
              <Price minor={i.unit_price_minor * i.quantity} currency={o.currency} />
            </div>
          ))}
        </div>
        <div class="box">
          <h3>Payment</h3>
          <div class="totals">
            <div class="row"><span>Status</span><StatusBadge status={o.status} /></div>
            <div class="row"><span>Card</span><span>{o.payment.status ? o.payment.status.replace("captured", "charged") : "not paid yet"}</span></div>
            <div class="row grand"><span>Total</span><Price minor={o.total_minor} currency={o.currency} /></div>
          </div>
          <a class="btn" href={href("/orders")}><Icon name="back" size={16} /> All orders</a>
        </div>
      </div>
    </div>
  );
}
