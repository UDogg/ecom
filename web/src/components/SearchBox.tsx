// Instant search: suggestions as you type (debounced, cancelled when stale), arrow keys to move,
// Enter for the full results, "/" anywhere to focus.
import { useEffect, useRef, useState } from "preact/hooks";
import { api, type Product } from "../api";
import { useDebounced } from "../lib/hooks";
import { href, loc, navigate } from "../lib/router";
import { Art } from "./Art";
import { Icon } from "./Icon";
import { Price } from "./Price";

export function SearchBox() {
  const [text, setText] = useState(loc.value.path === "/search" ? loc.value.query.get("q") ?? "" : "");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Product[]>([]);
  const [active, setActive] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const q = useDebounced(text.trim(), 140);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (q.length < 2) return setItems([]);
    let live = true;
    api.products({ q, page_size: 6 }).then((r) => live && (setItems(r.items), setActive(-1))).catch(() => {});
    return () => { live = false; };
  }, [q]);

  const go = (to: string) => {
    setOpen(false);
    input.current?.blur();
    navigate(to);
  };
  const submit = (e: Event) => {
    e.preventDefault();
    const pick = items[active];
    go(pick ? `/products/${pick.id}` : `/search?q=${encodeURIComponent(text.trim())}`);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, items.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, -1)); }
    if (e.key === "Escape") { setOpen(false); input.current?.blur(); }
  };
  const show = open && q.length >= 2 && items.length > 0;

  return (
    <form class="search" role="search" onSubmit={submit}>
      <Icon name="search" />
      <input ref={input} class="input" type="search" placeholder="Search mugs, kettles, headphones…" aria-label="Search the shop"
        value={text} onInput={(e) => { setText(e.currentTarget.value); setOpen(true); }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} onKeyDown={onKeyDown}
        role="combobox" list={undefined} aria-expanded={show} aria-controls="suggestions" aria-autocomplete="list" autocomplete="off" enterKeyHint="search" />
      <kbd aria-hidden="true">/</kbd>
      {show && (
        <div class="suggest" id="suggestions" role="listbox">
          {items.map((p, i) => (
            <a key={p.id} href={href(`/products/${p.id}`)} role="option" aria-selected={i === active} onMouseDown={(e) => { e.preventDefault(); go(`/products/${p.id}`); }}>
              <Art product={p} size="sm" />
              <span class="grow"><b>{p.name}</b><br /><span class="tiny muted">{p.category?.name}</span></span>
              <Price minor={p.price_minor} currency={p.currency} />
            </a>
          ))}
          <button type="submit" class="all">See all results for “{q}” <Icon name="arrow" size={16} /></button>
        </div>
      )}
    </form>
  );
}
