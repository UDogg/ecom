import { useState } from "preact/hooks";
import { api, ApiError } from "../api";
import { CartLines, ShippingMeter } from "../components/CartDrawer";
import { Icon } from "../components/Icon";
import { Price } from "../components/Price";
import { plural } from "../lib/format";
import { useTitle } from "../lib/hooks";
import { href, navigate } from "../lib/router";
import { cart, clear, count, subtotal } from "../state/cart";
import { signedIn, signIn } from "../state/session";
import { toast } from "../state/toast";

export function Steps({ at }: { at: 1 | 2 | 3 }) {
  const names = ["Bag", "Payment", "Done"];
  return (
    <ol class="steps" aria-label="Checkout progress">
      {names.map((n, i) => (
        <li key={n} class={`row ${i + 1 === at ? "on" : i + 1 < at ? "done" : ""}`} style={{ "--gap": "8px" }} aria-current={i + 1 === at ? "step" : undefined}>
          <span class="dot">{i + 1 < at ? <Icon name="check" size={14} /> : i + 1}</span>{n}{i < 2 && <span class="muted"> ——</span>}
        </li>
      ))}
    </ol>
  );
}

/** One key per bag: a retried or double-clicked "Place order" never makes a second order. */
function idempotencyKey(): string {
  const sig = JSON.stringify(cart.value.map((i) => [i.product_id, i.quantity]));
  try {
    const saved = JSON.parse(sessionStorage.getItem("bb-checkout") ?? "null") as { sig: string; key: string } | null;
    if (saved?.sig === sig) return saved.key;
    const key = crypto.randomUUID();
    sessionStorage.setItem("bb-checkout", JSON.stringify({ sig, key }));
    return key;
  } catch {
    return crypto.randomUUID();
  }
}

export function Checkout() {
  useTitle("Checkout");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  if (cart.value.length === 0) {
    return (
      <div class="wrap empty">
        <Icon name="bag" size={48} /><h1>Your bag is empty</h1>
        <a class="btn btn-dark" href={href("/search")}>Find something good</a>
      </div>
    );
  }
  const place = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const order = await api.checkout(cart.value.map(({ product_id, quantity }) => ({ product_id, quantity })), idempotencyKey());
      clear();
      try { sessionStorage.removeItem("bb-checkout"); } catch { /* ignore */ }
      navigate(`/orders/${order.id}/pay`);
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 401) return signIn("/checkout");
      setProblem(err.kind === "insufficient-stock" ? `${err.message} Adjust the quantity and try again.` : err.message);
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div class="wrap checkout">
      <section class="stack" style={{ "--gap": "20px" }}>
        <Steps at={1} />
        <h1>Review your bag</h1>
        {problem && <div class="callout bad" role="alert"><Icon name="alert" /><span>{problem}</span></div>}
        <div class="box"><CartLines /></div>
      </section>
      <aside class="summary panel">
        <h2>Summary</h2>
        <div class="totals">
          <div class="row"><span>Items ({plural(count.value, "item")})</span><Price minor={subtotal.value} /></div>
          <div class="row"><span>Delivery</span><span>{subtotal.value >= 5000 ? <b class="stock-ok">Free</b> : "Calculated after payment"}</span></div>
          <div class="row grand"><span>Total</span><Price minor={subtotal.value} /></div>
        </div>
        <ShippingMeter minor={subtotal.value} />
        {signedIn.value ? (
          <button class="btn btn-primary btn-lg btn-block" disabled={busy} onClick={place}>
            {busy ? <span class="spinner" /> : <Icon name="shield" />} Place order and pay
          </button>
        ) : (
          <div class="stack" style={{ "--gap": "10px" }}>
            <button class="btn btn-primary btn-lg btn-block" onClick={() => (api.demo ? navigate("/signin?next=/checkout") : signIn("/checkout"))}>Sign in to check out</button>
            <a class="btn btn-block" href={href("/register")}>New here? Create an account</a>
          </div>
        )}
        <p class="tiny muted">Placing the order reserves your items for 15 minutes while you pay. Nothing is charged until you pick a card.</p>
      </aside>
    </div>
  );
}
