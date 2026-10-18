// Product art: the catalog has no photos, so every product gets a generated illustration, stable for
// its SKU (same colours and composition on every visit) and themed by category. Pure SVG: no requests,
// no layout shift, sharp at any size.
import type { Product } from "../api/types";
import { read, write } from "../lib/store";

// Order lines and cart lines from the server carry no category; remember it from every product seen
// so a product looks the same on every page.
const categoryOf = new Map<string, string>(read<[string, string][]>("bb-sku-categories", []));
let saveTimer: ReturnType<typeof setTimeout> | undefined;

export function rememberCategories(products: Pick<Product, "sku" | "category">[]) {
  let changed = false;
  for (const p of products) {
    if (p.category && categoryOf.get(p.sku) !== p.category.slug) {
      categoryOf.set(p.sku, p.category.slug);
      changed = true;
    }
  }
  if (changed) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => write("bb-sku-categories", [...categoryOf].slice(-500)), 500);
  }
}

// One drawing per kind of product (SKU prefix), on a 100 x 100 canvas; categories are the fallback.
const kinds: Record<string, string> = {
  MUG: "M30 34h30v26a10 10 0 0 1-10 10H40a10 10 0 0 1-10-10V34Zm30 6h5a7 7 0 0 1 0 14h-5",
  KTL: "M30 44h36l-4 28H34l-4-28Zm6 0c0-8 6-12 12-12s12 4 12 12M66 50l14-10m-30-18h4",
  BWL: "M22 46h56a28 26 0 0 1-56 0Zm10 26h36",
  KNF: "M20 64 66 26c6-4 12 0 10 6L40 66l-20-2Zm20 2 8 10",
  PAN: "M22 48h48v8a12 12 0 0 1-12 12H34a12 12 0 0 1-12-12v-8Zm48 4h18",
  BRD: "M24 34h44a8 8 0 0 1 8 8v24a8 8 0 0 1-8 8H24a8 8 0 0 1-8-8V42a8 8 0 0 1 8-8Zm60 18h6",
  SPC: "M24 40h14v32H24zM43 40h14v32H43zM62 40h14v32H62zM22 34h56",
  CND: "M36 44h28v30H36zM50 44v-8m0-10c4 4 4 8 0 10-4-2-4-6 0-10Z",
  THR: "M24 30h52v40H24zM24 40h52M24 50h52M24 60h52M34 70v8m10-8v8m10-8v8m10-8v8",
  VSE: "M40 24h20M42 24c0 10-10 14-10 30s8 22 18 22 18-6 18-22-10-20-10-30",
  PLW: "M26 30c8 4 40 4 48 0-4 8-4 32 0 40-8-4-40-4-48 0 4-8 4-32 0-40Z",
  CLK: "M50 78a28 28 0 1 0 0-56 28 28 0 0 0 0 56Zm0-42v14l10 6",
  DIF: "M34 56h32l-4 20H38l-4-20Zm4 0a12 14 0 0 1 24 0M44 40c0-6 6-6 6-12m6 12c0-6 6-6 6-12",
  LMP: "M38 30h24v40H38zM44 22h12v8M38 44h24M38 56h24M50 70v6",
  NTB: "M30 22h40v56H30zM38 22v56M46 36h16M46 46h16",
  PLN: "M28 26h44v52H28zM28 38h44M38 20v12m24-12v12M36 48h6m8 0h6m8 0h6M36 60h6m8 0h6",
  PEN: "M28 72 62 30c3-4 9-2 8 3L36 78l-8-6Zm-4 10 4-10",
  INK: "M38 40h24v34H38zM42 30h16v10H42zM44 56h12",
  DSK: "M18 38h64v28H18zM70 46h6v12h-6",
  TNT: "M16 74 50 24l34 50H16Zm34-50v50m-10 0 10-20 10 20",
  STV: "M30 70h40M36 70l4-22h20l4 22M50 48v-6m-14 0h28m-22-8c0-6 6-6 6-12",
  HMK: "M16 30l16 24h36l16-24M32 54c6 10 30 10 36 0",
  BTL: "M40 28h20v8l6 8v32H34V44l6-8v-8Zm-6 28h32",
  GRD: "M36 46h28v30H36zM40 46l-4-14h28l-4 14M50 32v-8h14",
  DRP: "M28 34h44L58 58H42L28 34Zm6 34h32M50 58v10",
  BNS: "M38 30c12-4 22 8 18 22s-20 18-26 6c-4-10-4-24 8-28Zm6 4c-4 12 0 22 6 26",
  TEA: "M28 40h44v22a14 14 0 0 1-14 14H42a14 14 0 0 1-14-14V40Zm22 0V28m0 0h10v8H50",
  PRS: "M36 30h24v44H36zM60 40h6v24h-6M34 30h28M48 22v8M36 52h24",
  HPH: "M26 58v-8a24 24 0 0 1 48 0v8M26 56h8v16h-8zM66 56h8v16h-8z",
  SPK: "M32 22h36v56H32zM50 66a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-30a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
  EAR: "M34 40a8 8 0 1 1 0 16v14M66 40a8 8 0 1 0 0 16v14",
  TRN: "M18 30h64v44H18zM44 70a18 18 0 1 0 0-36 18 18 0 0 0 0 36Zm0-14a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm26-18v20l-8 8",
  YGA: "M20 62h50a10 10 0 0 0 0-20H30a10 10 0 0 0 0 20m-10 0v10h60",
  RLR: "M26 42h48v16H26zM34 42v16m8-16v16m8-16v16m8-16v16m8-16v16",
  BTH: "M34 38h32v38H34zM30 30h40v8H30zM42 54h4m4 6h4m4-10h4",
  BCK: "M32 32h36v44H32zM40 32v-6a10 10 0 0 1 20 0v6M38 52h24v14H38z",
  DUF: "M18 46h64v26H18zM36 46v-8h28v8M18 58h64",
  WLT: "M24 34h52v32H24zM60 44h16v12H60z",
  PKC: "M22 36h28v20H22zM50 36h28v20H50zM34 56h32v20H34z",
};

const glyphs: Record<string, string> = {
  // mug
  kitchen: "M30 34h30v26a10 10 0 0 1-10 10H40a10 10 0 0 1-10-10V34Zm30 6h5a7 7 0 0 1 0 14h-5",
  // lamp
  home: "M36 26h28l8 22H28l8-22Zm14 22v18m-12 4h24",
  // pen over page
  stationery: "M30 24h32v52H30zM38 36h16M38 46h16M38 56h10M66 30l8 8-22 22h-8v-8l22-22Z",
  // mountains and sun
  outdoor: "M18 70 40 38l12 16 8-10 22 26H18Zm52-38a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z",
  // cup with steam
  coffee: "M28 44h36v14a14 14 0 0 1-14 14h-8a14 14 0 0 1-14-14V44Zm36 4h4a6 6 0 0 1 0 12h-4M38 34c0-4 4-4 4-8m8 8c0-4 4-4 4-8",
  // headphones
  audio: "M26 58v-8a24 24 0 0 1 48 0v8M26 56h8v16h-8zM66 56h8v16h-8z",
  // leaf
  wellness: "M24 72c0-30 20-46 52-48 0 32-18 50-48 48m-4 0 26-26",
  // bag
  bags: "M28 38h44l-4 36H32l-4-36Zm10 0v-6a12 12 0 0 1 24 0v6",
};

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

const palettes: Record<string, [number, number]> = {
  kitchen: [18, 34], home: [28, 48], stationery: [205, 230], outdoor: [140, 175],
  coffee: [20, 32], audio: [250, 280], wellness: [95, 150], bags: [30, 15],
};

export function Art({ product, size = "md" }: { product: Pick<Product, "sku" | "name"> & { category?: { slug: string } | null }; size?: "sm" | "md" | "lg" }) {
  const slug = product.category?.slug ?? categoryOf.get(product.sku) ?? "home";
  const h = hash(product.sku);
  const [a, b] = palettes[slug] ?? [h % 360, (h >> 8) % 360];
  const hue1 = a + (h % 14) - 7, hue2 = b + ((h >> 4) % 18) - 9;
  const rot = ((h >> 10) % 24) - 12;
  const cx = 20 + ((h >> 14) % 60), cy = 18 + ((h >> 18) % 30);
  const glyph = kinds[product.sku.split("-")[0]!.toUpperCase()] ?? glyphs[slug] ?? glyphs.home!;
  const id = `g${h.toString(36)}`;
  return (
    <svg class={`art art-${size}`} viewBox="0 0 100 100" role="img" aria-label={product.name} preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color={`hsl(${hue1} 70% 88%)`} />
          <stop offset="1" stop-color={`hsl(${hue2} 62% 76%)`} />
        </linearGradient>
      </defs>
      <rect width="100" height="100" fill={`url(#${id})`} />
      <circle cx={cx} cy={cy} r="34" fill={`hsl(${hue2} 80% 96%)`} opacity=".55" />
      <circle cx={100 - cx / 2} cy={84} r="22" fill={`hsl(${hue1} 60% 60%)`} opacity=".18" />
      <g transform={`rotate(${rot} 50 50)`} fill="none" stroke={`hsl(${hue1} 45% 26%)`} stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">
        <path d={glyph} fill={`hsl(${hue2} 70% 97%)`} fill-opacity=".85" />
      </g>
    </svg>
  );
}
