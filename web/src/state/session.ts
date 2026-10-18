import { computed, signal } from "@preact/signals";
import { api, type Me, type Role } from "../api";

export const me = signal<Me | null>(null);
export const signedIn = computed(() => !!me.value?.authenticated);
export const isOwner = computed(() => !!me.value?.user?.roles.includes("shop_owner"));
export const isCustomer = computed(() => !!me.value?.user?.roles.some((r) => r === "customer" || r === "shop_owner"));

export async function refreshMe() {
  try {
    me.value = await api.me();
  } catch {
    me.value = { authenticated: false, csrf_token: "" };
  }
  return me.value;
}

/** Live: the BFF's sign-in (OIDC). Demo: pick a role. */
export async function signIn(returnTo = location.pathname + location.search, as?: Role) {
  await api.signIn(returnTo, as);
  if (api.demo) await refreshMe();
}

export async function signOut() {
  await api.signOut();
  await refreshMe();
}
