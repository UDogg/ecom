import { priceParts } from "../lib/format";

export function Price({ minor, currency = "USD", class: cls = "" }: { minor: number; currency?: string; class?: string }) {
  const { symbol, whole, fraction } = priceParts(minor, currency);
  return (
    <span class={`price ${cls}`} aria-label={`${symbol}${whole}.${fraction}`}>
      <span class="sym">{symbol}</span>{whole}{fraction && <span class="frac">{fraction}</span>}
    </span>
  );
}
