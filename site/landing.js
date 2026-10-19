// Progressive enhancement for the landing page: pointer tilt, scroll-reveal, a highlights carousel,
// and a WebGL hero (three.js, vendored next to this file). Everything is cosmetic and guarded — if
// three fails to load, WebGL is unavailable, or the viewer prefers reduced motion, the page keeps its
// static hero and content and simply shows everything.
const reduced = (() => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; } })();

/* ── pointer tilt ───────────────────────────────────────────────────────── */
function initTilt() {
  if (reduced) return;
  for (const el of document.querySelectorAll("[data-tilt]")) {
    const max = el.classList.contains("shot") ? 7 : 5;
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      el.style.setProperty("--ry", `${(px - 0.5) * 2 * max}deg`);
      el.style.setProperty("--rx", `${(0.5 - py) * 2 * max}deg`);
      el.style.setProperty("--mx", `${px * 100}%`);
      el.style.setProperty("--my", `${py * 100}%`);
      el.classList.add("tilting");
    });
    el.addEventListener("pointerleave", () => {
      el.style.setProperty("--rx", "0deg"); el.style.setProperty("--ry", "0deg");
      el.classList.remove("tilting");
    });
  }
}

/* ── scroll reveal ──────────────────────────────────────────────────────── */
function initReveal() {
  const els = document.querySelectorAll("[data-reveal]");
  if (reduced || typeof IntersectionObserver === "undefined") { els.forEach((e) => e.classList.add("in")); return; }
  const io = new IntersectionObserver((ents) => {
    for (const e of ents) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  let i = 0;
  for (const el of els) { el.classList.add("reveal"); el.style.setProperty("--reveal-delay", `${(i++ % 4) * 60}ms`); io.observe(el); }
}

/* ── highlights carousel ────────────────────────────────────────────────── */
function initCarousel() {
  const root = document.querySelector("[data-carousel]");
  if (!root) return;
  const slides = [...root.querySelectorAll(".slide")];
  const dotsWrap = root.querySelector(".cdots");
  let active = 0, timer;
  const dots = slides.map((_, i) => {
    const d = document.createElement("button");
    d.setAttribute("role", "tab");
    d.setAttribute("aria-label", `Highlight ${i + 1}`);
    d.addEventListener("click", () => { go(i); restart(); });
    dotsWrap.appendChild(d);
    return d;
  });
  function go(n) {
    active = (n + slides.length) % slides.length;
    slides.forEach((s, i) => s.classList.toggle("on", i === active));
    dots.forEach((d, i) => d.setAttribute("aria-selected", i === active ? "true" : "false"));
  }
  function restart() { if (reduced) return; clearInterval(timer); timer = setInterval(() => go(active + 1), 5000); }
  root.querySelector(".next").addEventListener("click", () => { go(active + 1); restart(); });
  root.querySelector(".prev").addEventListener("click", () => { go(active - 1); restart(); });
  root.addEventListener("pointerenter", () => clearInterval(timer));
  root.addEventListener("pointerleave", restart);
  go(0); restart();
}

/* ── WebGL hero ─────────────────────────────────────────────────────────── */
async function initHero3D() {
  const canvas = document.querySelector(".shot3d");
  if (!canvas || reduced) return;
  let THREE;
  try { THREE = await import("./vendor/three.module.js"); } catch { return; }
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true }); } catch { return; }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  canvas.classList.add("on"); // reveal the canvas over the SVG fallback only once it's live

  const css = (n, f) => getComputedStyle(document.body).getPropertyValue(n).trim() || f;
  const palette = [css("--brand", "#d4482b"), css("--accent", "#0f6b62"), css("--gold", "#b7791f")]
    .map((c) => new THREE.Color(c));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1.5, 0.1, 100);
  camera.position.z = 6.4;
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(4, 6, 8); scene.add(key);
  const rim = new THREE.PointLight(palette[1], 1.2, 40); rim.position.set(-6, -2, 5); scene.add(rim);

  const geoms = [new THREE.IcosahedronGeometry(1, 0), new THREE.OctahedronGeometry(1, 0),
    new THREE.DodecahedronGeometry(1, 0), new THREE.TetrahedronGeometry(1.15, 0)];
  const group = new THREE.Group();
  const gems = [];
  const N = 12;
  for (let i = 0; i < N; i++) {
    const mesh = new THREE.Mesh(geoms[i % geoms.length], new THREE.MeshStandardMaterial(
      { color: palette[i % palette.length], metalness: 0.35, roughness: 0.34, flatShading: true }));
    const a = (i / N) * Math.PI * 2, radius = 2.6 + (i % 3) * 0.6;
    mesh.position.set(Math.cos(a) * radius, Math.sin(a * 1.3) * 1.7, Math.sin(a) * radius - 1);
    mesh.scale.setScalar(0.4 + ((i * 97) % 10) / 18);
    mesh.rotation.set(Math.random() * 6, Math.random() * 6, Math.random() * 6);
    group.add(mesh);
    gems.push({ mesh, spin: 0.2 + ((i * 53) % 10) / 24, phase: a, amp: 0.12 + ((i * 31) % 7) / 40 });
  }
  scene.add(group);

  const resize = () => {
    const r = canvas.getBoundingClientRect(), w = Math.max(1, r.width), h = Math.max(1, r.height);
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  resize();
  new ResizeObserver(resize).observe(canvas);

  let tx = 0, ty = 0, cx = 0, cy = 0, visible = true;
  addEventListener("pointermove", (e) => { tx = (e.clientX / innerWidth - 0.5) * 0.6; ty = (e.clientY / innerHeight - 0.5) * 0.6; }, { passive: true });
  new IntersectionObserver((es) => { for (const e of es) visible = e.isIntersecting; }, { threshold: 0 }).observe(canvas);

  const clock = new THREE.Clock();
  (function frame() {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime();
    cx += (tx - cx) * 0.05; cy += (ty - cy) * 0.05;
    group.rotation.y = t * 0.12 + cx; group.rotation.x = cy * 0.6;
    for (const g of gems) {
      g.mesh.rotation.x += g.spin * 0.01; g.mesh.rotation.y += g.spin * 0.013;
      g.mesh.position.y += Math.sin(t * 0.8 + g.phase) * g.amp * 0.01;
    }
    renderer.render(scene, camera);
  })();
}

initTilt();
initReveal();
initCarousel();
initHero3D();
