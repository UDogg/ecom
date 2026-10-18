import { useState } from "preact/hooks";
import { api, ApiError } from "../api";
import { Icon } from "../components/Icon";
import { useTitle } from "../lib/hooks";
import { href, loc, navigate } from "../lib/router";
import { signIn } from "../state/session";
import { toast } from "../state/toast";

/** Demo only: choose who to be. The live shop signs in through the BFF (OIDC) instead. */
export function SignIn() {
  useTitle("Sign in");
  const next = loc.value.query.get("next") ?? "/";
  if (!api.demo) {
    signIn(next);
    return <div class="wrap empty"><span class="spinner" /> Taking you to sign-in…</div>;
  }
  const as = async (role: "customer" | "shop_owner") => {
    await signIn(next, role);
    toast(role === "shop_owner" ? "Signed in as the shop owner" : "Signed in as a customer");
    navigate(role === "shop_owner" && next === "/" ? "/admin" : next);
  };
  return (
    <div class="wrap">
      <div class="auth-card panel">
        <h1>Sign in</h1>
        <p class="muted">In the demo you can be either side of the counter. The real shop signs you in with a password through its own identity service.</p>
        <button class="btn btn-primary btn-lg btn-block" onClick={() => as("customer")}><Icon name="user" /> Continue as a customer</button>
        <button class="btn btn-dark btn-lg btn-block" onClick={() => as("shop_owner")}><Icon name="chart" /> Continue as the shop owner</button>
        <p class="tiny muted center">customer@shop.local · owner@shop.local</p>
      </div>
    </div>
  );
}

export function Register() {
  useTitle("Create an account");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const submit = async (e: Event) => {
    e.preventDefault();
    setBusy(true);
    setErrors({});
    try {
      await api.register(email.trim(), password);
      toast("Account created. Sign in to continue.");
      if (api.demo) navigate("/signin");
      else signIn("/");
    } catch (err) {
      const p = (err as ApiError).problem;
      setErrors(p?.errors ?? { form: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const strength = Math.min(4, Math.floor(password.length / 4) + Number(/[^a-z]/i.test(password)) - 1);
  return (
    <div class="wrap">
      <form class="auth-card panel" onSubmit={submit} noValidate>
        <h1>Create your account</h1>
        <p class="muted">Track orders, keep your bag on every device, check out in seconds.</p>
        {errors.form && <div class="callout bad"><Icon name="alert" /><span>{errors.form}</span></div>}
        <label class="field">
          <span>E-mail</span>
          <input class="input" type="email" autocomplete="email" required value={email} onInput={(e) => setEmail(e.currentTarget.value)} aria-invalid={!!errors.email} />
          {errors.email && <span class="err">{errors.email}</span>}
        </label>
        <label class="field">
          <span>Password</span>
          <input class="input" type="password" autocomplete="new-password" required minLength={12} value={password} onInput={(e) => setPassword(e.currentTarget.value)} aria-invalid={!!errors.password} />
          <div class="meter" aria-hidden="true"><i style={{ width: `${(Math.max(0, strength) / 4) * 100}%` }} /></div>
          {errors.password ? <span class="err">{errors.password}</span> : <small>At least 12 characters. A passphrase works well.</small>}
        </label>
        <button class="btn btn-primary btn-lg btn-block" disabled={busy}>{busy && <span class="spinner" />} Create account</button>
        <p class="small muted center">Already have one? <a href={href("/signin")}><b>Sign in</b></a></p>
      </form>
    </div>
  );
}
