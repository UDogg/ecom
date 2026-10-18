import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { href, navigate } from "../lib/router";
import { cartOpen, count, forget } from "../state/cart";
import { isOwner, me, signedIn, signIn, signOut } from "../state/session";
import { toast } from "../state/toast";
import { theme, toggleTheme } from "../state/theme";
import { Icon } from "./Icon";
import { SearchBox } from "./SearchBox";

export function Header() {
  const [bump, setBump] = useState(false);
  const last = useRef(count.value);
  useEffect(() => {
    if (count.value > last.current) {
      setBump(true);
      setTimeout(() => setBump(false), 400);
    }
    last.current = count.value;
  }, [count.value]);

  const out = async () => {
    await signOut();
    forget();
    toast("Signed out. See you soon!", "info");
    navigate("/");
  };

  return (
    <>
      {api.demo && (
        <div class="demo-ribbon">
          Live demo: the whole shop runs in your browser. Test cards decide the payment outcome.{" "}
          <button onClick={() => { api.reset?.(); location.reload(); }}>Reset demo</button>
        </div>
      )}
      <header class="header">
        <div class="wrap">
          <a class="logo" href={href("/")} aria-label="Babel Bazaar home">
            <span class="logo-mark" aria-hidden="true">B</span>
            <span>Babel Bazaar<br /><small>Goods worth keeping</small></span>
          </a>
          <SearchBox />
          <nav class="nav-actions" aria-label="Account">
            <button class="btn btn-ghost btn-icon" onClick={toggleTheme} aria-label={`Switch to ${theme.value === "dark" ? "light" : "dark"} mode`}>
              <Icon name={theme.value === "dark" ? "sun" : "moon"} />
            </button>
            {isOwner.value && <a class="btn btn-ghost btn-sm" href={href("/admin")}><Icon name="chart" size={18} /> Admin</a>}
            {signedIn.value ? (
              <>
                <a class="btn btn-ghost btn-sm" href={href("/orders")} title={me.value?.user?.email}><Icon name="box" size={18} /> Orders</a>
                <button class="btn btn-ghost btn-icon" onClick={out} aria-label="Sign out"><Icon name="logout" /></button>
              </>
            ) : (
              <button class="btn btn-ghost btn-sm" onClick={() => (api.demo ? navigate("/signin") : signIn())}><Icon name="user" size={18} /> Sign in</button>
            )}
            <button class="btn btn-ghost btn-icon cart-btn" onClick={() => (cartOpen.value = true)} aria-label={`Bag, ${count.value} items`}>
              <Icon name="bag" />
              {count.value > 0 && <span class={`cart-count ${bump ? "bump" : ""}`}>{count.value}</span>}
            </button>
          </nav>
        </div>
      </header>
    </>
  );
}
