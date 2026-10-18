import { href, navigate } from "../lib/router";
import { cart, cartOpen, FREE_SHIPPING_MINOR, MAX_QTY, remove, setQuantity, subtotal } from "../state/cart";
import { Art } from "./Art";
import { Icon } from "./Icon";
import { Drawer } from "./Overlay";
import { Price } from "./Price";
import { Stepper } from "./Stepper";
import { price } from "../lib/format";

export function ShippingMeter({ minor }: { minor: number }) {
  const left = FREE_SHIPPING_MINOR - minor;
  return (
    <div class="ship-meter">
      <span>{left > 0 ? <>Add <b>{price(left)}</b> for free delivery</> : <><Icon name="truck" size={16} class="inline" /> <b>Free delivery</b> unlocked</>}</span>
      <div class="meter" role="progressbar" aria-valuemin={0} aria-valuemax={FREE_SHIPPING_MINOR} aria-valuenow={Math.min(minor, FREE_SHIPPING_MINOR)}>
        <i style={{ width: `${Math.min(100, (minor / FREE_SHIPPING_MINOR) * 100)}%` }} />
      </div>
    </div>
  );
}

export function CartLines({ compact = false }: { compact?: boolean }) {
  return (
    <div>
      {cart.value.map((i) => (
        <div class="line" key={i.product_id}>
          <a href={href(`/products/${i.product_id}`)} onClick={() => (cartOpen.value = false)}><Art product={i} size="sm" /></a>
          <div class="stack" style={{ "--gap": "6px" }}>
            <a class="name" href={href(`/products/${i.product_id}`)} onClick={() => (cartOpen.value = false)}>{i.name}</a>
            {i.available === false ? <span class="stock-out">Not available in this quantity</span> : <span class="tiny muted">{price(i.price_minor)} each</span>}
            <div class="row">
              <Stepper small value={i.quantity} max={MAX_QTY} onChange={(n) => setQuantity(i.product_id, n)} label={`Quantity of ${i.name}`} />
              {!compact && <button class="btn btn-ghost btn-sm" onClick={() => remove(i.product_id)}>Remove</button>}
            </div>
          </div>
          <Price minor={i.price_minor * i.quantity} />
        </div>
      ))}
    </div>
  );
}

export function CartDrawer() {
  const close = () => (cartOpen.value = false);
  const empty = cart.value.length === 0;
  return (
    <Drawer title="Your bag" onClose={close} footer={!empty && (
      <>
        <ShippingMeter minor={subtotal.value} />
        <div class="row between"><span>Subtotal</span><Price minor={subtotal.value} class="price-lg" /></div>
        <button class="btn btn-primary btn-lg btn-block" onClick={() => { close(); navigate("/checkout"); }}>Checkout <Icon name="arrow" /></button>
        <span class="tiny muted center">Taxes included. Items are reserved for 15 minutes at checkout.</span>
      </>
    )}>
      {empty ? (
        <div class="empty">
          <Icon name="bag" size={44} />
          <h3>Your bag is empty</h3>
          <p class="muted">Good things are waiting.</p>
          <button class="btn btn-dark" onClick={() => { close(); navigate("/search"); }}>Start shopping</button>
        </div>
      ) : <CartLines />}
    </Drawer>
  );
}
