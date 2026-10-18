const money = new Map<string, Intl.NumberFormat>();

/** 2899 USD -> "$28.99". */
export function price(minor: number, currency = "USD"): string {
  let f = money.get(currency);
  if (!f) money.set(currency, (f = new Intl.NumberFormat(undefined, { style: "currency", currency })));
  return f.format(minor / 100);
}

/** Splits a price into its whole and fractional parts, for typographic display. */
export function priceParts(minor: number, currency = "USD") {
  const parts = new Intl.NumberFormat(undefined, { style: "currency", currency }).formatToParts(minor / 100);
  const symbol = parts.filter((p) => p.type === "currency").map((p) => p.value).join("");
  const whole = parts.filter((p) => p.type === "integer" || p.type === "group").map((p) => p.value).join("");
  const fraction = parts.find((p) => p.type === "fraction")?.value ?? "";
  return { symbol, whole, fraction };
}

const day = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const time = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
export const date = (iso: string) => day.format(new Date(iso));
export const dateTime = (iso: string) => time.format(new Date(iso));

export function ago(iso: string): string {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (s < 60) return rtf.format(-s, "second");
  if (s < 3600) return rtf.format(-Math.round(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.round(s / 3600), "hour");
  return rtf.format(-Math.round(s / 86400), "day");
}

export const shortId = (id: string) => id.slice(0, 8).toUpperCase();

export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
