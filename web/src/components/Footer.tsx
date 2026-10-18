import { href } from "../lib/router";
import { CATEGORIES } from "../api/demo-catalog";

export function Footer() {
  return (
    <footer class="footer">
      <div class="wrap">
        <div class="cols">
          <div class="stack" style={{ "--gap": "10px" }}>
            <b class="logo">Babel Bazaar</b>
            <p>Well-made things for the kitchen, the desk and the road. Small catalogue, carefully kept.</p>
          </div>
          <div>
            <h4>Shop</h4>
            <ul>{CATEGORIES.slice(0, 4).map((c) => <li key={c.slug}><a href={href(`/search?category=${c.slug}`)}>{c.name}</a></li>)}</ul>
          </div>
          <div>
            <h4>More</h4>
            <ul>{CATEGORIES.slice(4).map((c) => <li key={c.slug}><a href={href(`/search?category=${c.slug}`)}>{c.name}</a></li>)}</ul>
          </div>
          <div>
            <h4>Account</h4>
            <ul>
              <li><a href={href("/orders")}>Your orders</a></li>
              <li><a href={href("/checkout")}>Checkout</a></li>
              <li><a href={href("/register")}>Create an account</a></li>
            </ul>
          </div>
        </div>
        <div class="legal">
          <span>© Babel Bazaar. A polyglot reference shop: Go, Python, Java behind one BFF.</span>
          <span>Prices in USD · Payments are simulated</span>
        </div>
      </div>
    </footer>
  );
}
