// Small, dependency-free motion helpers shared by the storefront: a pointer-driven 3D tilt and a
// scroll-reveal observer. Both bow out of motion when the viewer asks for less (prefers-reduced-motion),
// and both fail open — if anything throws, content stays visible and interactive.

export function reducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

// 3D tilt: map the pointer's position over an element to CSS custom properties the stylesheet turns
// into a perspective rotation and a moving highlight. Attach onPointerMove / onPointerLeave.
export function tiltMove(e: PointerEvent, maxDeg = 7): void {
  if (reducedMotion()) return;
  const el = e.currentTarget as HTMLElement | null;
  if (!el) return;
  const r = el.getBoundingClientRect();
  const px = (e.clientX - r.left) / r.width;
  const py = (e.clientY - r.top) / r.height;
  el.style.setProperty("--ry", `${(px - 0.5) * 2 * maxDeg}deg`);
  el.style.setProperty("--rx", `${(0.5 - py) * 2 * maxDeg}deg`);
  el.style.setProperty("--mx", `${px * 100}%`);
  el.style.setProperty("--my", `${py * 100}%`);
  el.classList.add("tilting");
}

export function tiltLeave(e: PointerEvent): void {
  const el = e.currentTarget as HTMLElement | null;
  if (!el) return;
  el.style.setProperty("--ry", "0deg");
  el.style.setProperty("--rx", "0deg");
  el.classList.remove("tilting");
}

// Scroll-reveal: fade-and-rise each registered element the first time it scrolls into view. One shared
// observer; elements register themselves (so async-loaded cards are covered). Nothing is hidden until
// an element registers, so a page with JS off — or this module erroring — shows everything.
let io: IntersectionObserver | undefined;
function observer(): IntersectionObserver | undefined {
  if (io) return io;
  if (typeof IntersectionObserver === "undefined") return undefined;
  io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          io?.unobserve(e.target);
        }
      }
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
  );
  return io;
}

export function reveal(el: Element | null, delay = 0): void {
  if (!el) return;
  try {
    if (reducedMotion()) {
      el.classList.add("in");
      return;
    }
    if (delay) (el as HTMLElement).style.setProperty("--reveal-delay", `${delay}ms`);
    el.classList.add("reveal");
    const o = observer();
    if (o) o.observe(el);
    else el.classList.add("in"); // no observer support: just show it
  } catch {
    el.classList.add("in");
  }
}
