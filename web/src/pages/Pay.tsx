import { useEffect, useState } from "preact/hooks";
import { api, ApiError, type Order, type PaymentSource } from "../api";
import { Art } from "../components/Art";
import { Icon } from "../components/Icon";
import { Price } from "../components/Price";
import { Steps } from "./Checkout";
import { shortId } from "../lib/format";
import { useAsync, useTitle } from "../lib/hooks";
import { href, navigate } from "../lib/router";
import { track } from "../lib/telemetry";
import { toast } from "../state/toast";
import { NotFound } from "./NotFound";

export const CARDS: { source: PaymentSource; brand: string; label: string; number: string; note: string }[] = [
  { source: "tok_visa", brand: "visa", label: "Visa", number: "4242 4242 4242 4242", note: "Approved" },
  { source: "tok_mastercard", brand: "mastercard", label: "Mastercard", number: "5555 5555 5555 4444", note: "Approved" },
  { source: "tok_declined", brand: "declined", label: "Declined card", number: "4000 0000 0000 0002", note: "The bank says no" },
  { source: "tok_insufficient", brand: "insufficient", label: "Low balance", number: "4000 0000 0000 9995", note: "Insufficient funds" },
  { source: "tok_timeout", brand: "timeout", label: "Slow bank", number: "4000 0000 0000 0119", note: "Answers late; settled for you" },
];

const DECLINE: Record<string, string> = {
  card_declined: "Your bank declined the card. Nothing was charged; try another card.",
  insufficient_funds: "The card has insufficient funds. Nothing was charged; try another card.",
  not_received: "The payment never reached the bank. Nothing was charged; try again.",
};

export function Pay({ id }: { id: string }) {
  useTitle("Payment");
  const loaded = useAsync(() => api.order(id), [id]);
  const [order, setOrder] = useState<Order | undefined>();
  const [card, setCard] = useState<PaymentSource>("tok_visa");
  const [busy, setBusy] = useState(false);
  const o = order ?? loaded.data;

  // Waiting for a slow bank: poll until the payment settles (reconciliation does it server-side).
  useEffect(() => {
    if (o?.payment.status !== "pending") return;
    const t = setInterval(async () => {
      const fresh = await api.order(id).catch(() => undefined);
      if (fresh && fresh.payment.status !== "pending") setOrder(fresh);
    }, 1500);
    return () => clearInterval(t);
  }, [o?.payment.status]);

  useEffect(() => {
    if (o?.status === "paid") navigate(`/orders/${id}?thanks=1`, { replace: true });
  }, [o?.status]);

  if (loaded.error) return <NotFound what="order" />;
  if (!o) return <div class="wrap section"><div class="sk" style={{ height: "320px", borderRadius: "22px" }} /></div>;

  const pay = async () => {
    setBusy(true);
    try {
      const next = await api.pay(id, card);
      setOrder(next);
      if (next.status === "paid") { track("order_paid"); toast("Payment received", "ok"); }
      if (next.status === "payment_failed") toast("Payment declined. Nothing was charged.", "error");
    } catch (e) {
      const err = e as ApiError;
      toast(err.message, "error");
      if (err.kind === "payment-in-progress" || err.kind === "invalid-state-transition") setOrder(await api.order(id));
    } finally {
      setBusy(false);
    }
  };
  const payable = o.status === "pending_payment" || o.status === "payment_failed";
  const waiting = o.payment.status === "pending";

  return (
    <div class="wrap checkout">
      <section class="stack" style={{ "--gap": "20px" }}>
        <Steps at={2} />
        <h1>Choose how to pay</h1>
        {o.status === "payment_failed" && o.payment.failure_code && !waiting && (
          <div class="callout bad" role="alert"><Icon name="alert" /><span>{DECLINE[o.payment.failure_code] ?? "The payment failed. Nothing was charged."}</span></div>
        )}
        {waiting && (
          <div class="callout" role="status"><span class="spinner" /><span><b>Waiting for your bank.</b> We never charge twice: this page updates by itself as soon as the bank answers.</span></div>
        )}
        {!payable && !waiting && (
          <div class="callout warn"><Icon name="clock" /><span>This order is {o.status.replace("_", " ")} and can no longer be paid. <a href={href("/search")}><b>Keep shopping</b></a></span></div>
        )}
        <div class="cards" role="radiogroup" aria-label="Test cards">
          {CARDS.map((c) => (
            <button key={c.source} class="paycard" role="radio" aria-checked={card === c.source} disabled={!payable || waiting} onClick={() => setCard(c.source)}>
              <div class={`plastic ${c.brand}`}><span class="chipmark" /><span>{c.number}</span></div>
              <span><b>{c.label}</b><br /><span class="tiny muted">{c.note}</span></span>
            </button>
          ))}
        </div>
        <p class="tiny muted">These are the payment simulator's test cards: each one always gives the same outcome, so you can try every path. No real card is ever asked for.</p>
      </section>
      <aside class="summary panel">
        <div class="row between"><h3>Order <span class="mono">{shortId(o.id)}</span></h3><span class="badge warn">Awaiting payment</span></div>
        <div>
          {o.items.map((i) => (
            <div class="line" key={i.product_id}>
              <Art product={{ sku: i.sku, name: i.name }} size="sm" />
              <span><b>{i.name}</b><br /><span class="tiny muted">Qty {i.quantity}</span></span>
              <Price minor={i.unit_price_minor * i.quantity} currency={o.currency} />
            </div>
          ))}
        </div>
        <div class="totals"><div class="row grand"><span>Total</span><Price minor={o.total_minor} currency={o.currency} /></div></div>
        <button class="btn btn-primary btn-lg btn-block" disabled={busy || !payable || waiting} onClick={pay}>
          {busy || waiting ? <span class="spinner" /> : <Icon name="card" />} Pay <Price minor={o.total_minor} currency={o.currency} />
        </button>
        <span class="tiny muted center"><Icon name="shield" size={14} class="inline" /> Paying twice is impossible: every attempt is idempotent.</span>
      </aside>
    </div>
  );
}
