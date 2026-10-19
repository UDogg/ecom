// A WebGL hero: a slow carousel of low-poly "goods" — faceted gems in the brand palette — turning in
// space with a soft pointer parallax. three.js is imported lazily (its own chunk), so it never weighs
// on first paint; the scene only builds on the home page, only when WebGL is available, and never when
// the viewer prefers reduced motion. It renders behind the SVG product tiles, which keep working on
// their own if any of this bows out.
import { useEffect, useRef } from "preact/hooks";
import { reducedMotion } from "../lib/fx";

export function Hero3D({ class: className = "" }: { class?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || reducedMotion()) return;
    let stop = false;
    let cleanup = () => {};

    (async () => {
      let THREE: typeof import("three");
      try {
        THREE = await import("three");
      } catch {
        return; // three unavailable: the SVG tiles carry the hero
      }
      if (stop) return;

      let renderer: import("three").WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
      } catch {
        return; // no WebGL context
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

      const css = (name: string, fallback: string) =>
        getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
      const palette = [
        css("--brand", "#d4482b"), css("--accent", "#0f6b62"), css("--gold", "#b7791f"),
        css("--brand", "#d4482b"), css("--accent", "#0f6b62"),
      ].map((c) => new THREE.Color(c));

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
      camera.position.z = 6;

      scene.add(new THREE.AmbientLight(0xffffff, 0.65));
      const key = new THREE.DirectionalLight(0xffffff, 1.4);
      key.position.set(4, 6, 8);
      scene.add(key);
      const rim = new THREE.PointLight(new THREE.Color(css("--accent", "#0f6b62")), 1.1, 40);
      rim.position.set(-6, -3, 4);
      scene.add(rim);

      const geoms = [
        new THREE.IcosahedronGeometry(1, 0),
        new THREE.OctahedronGeometry(1, 0),
        new THREE.DodecahedronGeometry(1, 0),
        new THREE.TetrahedronGeometry(1.1, 0),
      ];
      const group = new THREE.Group();
      const gems: { mesh: import("three").Mesh; spin: number; phase: number; floatAmp: number }[] = [];
      const N = 11;
      for (let i = 0; i < N; i++) {
        const geo = geoms[i % geoms.length]!;
        const mat = new THREE.MeshStandardMaterial({
          color: palette[i % palette.length],
          metalness: 0.35,
          roughness: 0.35,
          flatShading: true,
        });
        const mesh = new THREE.Mesh(geo, mat);
        const a = (i / N) * Math.PI * 2;
        const radius = 2.7 + (i % 3) * 0.55;
        mesh.position.set(Math.cos(a) * radius + 0.8, Math.sin(a * 1.3) * 1.7, Math.sin(a) * radius - 1);
        const s = 0.42 + ((i * 97) % 10) / 18;
        mesh.scale.setScalar(s);
        mesh.rotation.set(Math.random() * 6, Math.random() * 6, Math.random() * 6);
        group.add(mesh);
        gems.push({ mesh, spin: 0.2 + ((i * 53) % 10) / 24, phase: a, floatAmp: 0.12 + ((i * 31) % 7) / 40 });
      }
      scene.add(group);

      let w = 1, h = 1;
      const resize = () => {
        const rect = canvas.getBoundingClientRect();
        w = Math.max(1, rect.width);
        h = Math.max(1, rect.height);
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      resize();
      const ro = new ResizeObserver(resize);
      ro.observe(canvas);

      // Pointer parallax (eased), only while the hero is visible.
      let targetX = 0, targetY = 0, curX = 0, curY = 0, visible = true;
      const onMove = (e: PointerEvent) => {
        targetX = (e.clientX / window.innerWidth - 0.5) * 0.6;
        targetY = (e.clientY / window.innerHeight - 0.5) * 0.6;
      };
      window.addEventListener("pointermove", onMove, { passive: true });
      const io = new IntersectionObserver((es) => { for (const e of es) visible = e.isIntersecting; }, { threshold: 0 });
      io.observe(canvas);

      const clock = new THREE.Clock();
      const frame = () => {
        if (stop) return;
        requestAnimationFrame(frame);
        if (!visible || document.hidden) return;
        const t = clock.getElapsedTime();
        curX += (targetX - curX) * 0.05;
        curY += (targetY - curY) * 0.05;
        group.rotation.y = t * 0.12 + curX;
        group.rotation.x = curY * 0.6;
        for (const g of gems) {
          g.mesh.rotation.x += g.spin * 0.01;
          g.mesh.rotation.y += g.spin * 0.013;
          g.mesh.position.y += Math.sin(t * 0.8 + g.phase) * g.floatAmp * 0.01;
        }
        renderer.render(scene, camera);
      };
      frame();

      cleanup = () => {
        window.removeEventListener("pointermove", onMove);
        ro.disconnect();
        io.disconnect();
        geoms.forEach((g) => g.dispose());
        gems.forEach((g) => (g.mesh.material as import("three").Material).dispose());
        renderer.dispose();
      };
    })();

    return () => { stop = true; cleanup(); };
  }, []);

  return <canvas ref={canvasRef} class={`hero3d ${className}`} aria-hidden="true" />;
}
