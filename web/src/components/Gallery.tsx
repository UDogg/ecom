// Product gallery as a slideshow: a crossfading main frame with a pointer tilt, a strip of thumbnails,
// prev/next arrows, keyboard arrows and a gentle autoplay that stops the moment you interact (and never
// starts when reduced motion is asked for). The catalog has no photos, so each "shot" is a stable
// generated illustration (Art) of a SKU variant — four angles of the same piece.
import { useEffect, useRef, useState } from "preact/hooks";
import type { Product } from "../api";
import { reducedMotion, tiltLeave, tiltMove } from "../lib/fx";
import { Art } from "./Art";
import { Icon } from "./Icon";

const ANGLES = 4;

export function Gallery({ product: p }: { product: Product }) {
  const [active, setActive] = useState(0);
  const paused = useRef(false);
  const skus = Array.from({ length: ANGLES }, (_, n) => (n === 0 ? p.sku : `${p.sku}-${n}`));

  useEffect(() => { setActive(0); }, [p.id]);

  useEffect(() => {
    if (reducedMotion()) return;
    const id = setInterval(() => { if (!paused.current) setActive((i) => (i + 1) % ANGLES); }, 4200);
    return () => clearInterval(id);
  }, [p.id]);

  const go = (n: number) => setActive(((n % ANGLES) + ANGLES) % ANGLES);
  const hold = () => { paused.current = true; };
  const release = () => { paused.current = false; };

  return (
    <div class="gallery" onPointerEnter={hold} onPointerLeave={release} onFocusCapture={hold}
      onKeyDown={(e) => { if (e.key === "ArrowLeft") go(active - 1); else if (e.key === "ArrowRight") go(active + 1); }}>
      <div class="main tilt" onPointerMove={tiltMove} onPointerLeave={tiltLeave} tabIndex={0}
        role="group" aria-roledescription="carousel" aria-label={`${p.name}, image ${active + 1} of ${ANGLES}`}>
        {skus.map((sku, i) => (
          <div class={`slide${i === active ? " on" : ""}`} key={sku} aria-hidden={i !== active}>
            <Art product={{ ...p, sku }} size="lg" />
          </div>
        ))}
        <span class="sheen" aria-hidden="true" />
        <button class="nav prev" aria-label="Previous image" onClick={() => go(active - 1)}><Icon name="arrow" size={18} /></button>
        <button class="nav next" aria-label="Next image" onClick={() => go(active + 1)}><Icon name="arrow" size={18} /></button>
        <div class="dots" aria-hidden="true">{skus.map((s, i) => <span key={s} class={i === active ? "on" : ""} />)}</div>
      </div>
      <div class="thumbs">
        {skus.map((sku, i) => (
          <button key={sku} class={`thumb${i === active ? " on" : ""}`} aria-label={`View image ${i + 1}`}
            aria-current={i === active} onClick={() => go(i)}>
            <Art product={{ ...p, sku }} size="sm" />
          </button>
        ))}
      </div>
    </div>
  );
}
