import { lazy, Suspense } from "preact/compat";
import { CartDrawer } from "./components/CartDrawer";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Toasts } from "./components/Toasts";
import { loc, match } from "./lib/router";
import { Account } from "./pages/AccountRoutes";
import { Checkout } from "./pages/Checkout";
import { Home } from "./pages/Home";
import { NotFound } from "./pages/NotFound";
import { OrderPage, Orders } from "./pages/Orders";
import { Pay } from "./pages/Pay";
import { Product } from "./pages/Product";
import { Search } from "./pages/Search";
import { cartOpen } from "./state/cart";

const Admin = lazy(() => import("./admin/Admin"));

function Page() {
  const path = loc.value.path;
  let m: Record<string, string> | null;
  if (path === "/") return <Home />;
  if (path === "/search") return <Search />;
  if ((m = match("/products/:id", path))) return <Product id={m.id!} />;
  if (path === "/checkout") return <Checkout />;
  if (path === "/orders") return <Orders />;
  if ((m = match("/orders/:id/pay", path))) return <Pay id={m.id!} />;
  if ((m = match("/orders/:id", path))) return <OrderPage id={m.id!} />;
  if (path === "/signin" || path === "/register") return <Account path={path} />;
  if (path === "/admin" || path.startsWith("/admin/")) return <Suspense fallback={<div class="wrap section"><span class="spinner" /></div>}><Admin /></Suspense>;
  return <NotFound />;
}

export function App() {
  return (
    <>
      <a class="skip" href="#main" data-native>Skip to content</a>
      <Header />
      <main id="main" tabIndex={-1}><Page /></main>
      <Footer />
      {cartOpen.value && <CartDrawer />}
      <Toasts />
    </>
  );
}
