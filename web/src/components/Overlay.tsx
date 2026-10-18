import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { Icon } from "./Icon";

/** Esc closes, focus moves in and returns, the page behind does not scroll. */
function useDialog(onClose: () => void) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("[autofocus], button, a, input")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      before?.focus?.();
    };
  }, []);
  return box;
}

export function Drawer({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ComponentChildren; footer?: ComponentChildren }) {
  const box = useDialog(onClose);
  return (
    <>
      <div class="scrim" onClick={onClose} />
      <div class="drawer" role="dialog" aria-modal="true" aria-label={title} ref={box}>
        <header><h2>{title}</h2><button class="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><Icon name="close" /></button></header>
        <div class="content">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ComponentChildren }) {
  const box = useDialog(onClose);
  return (
    <>
      <div class="scrim" onClick={onClose} />
      <div class="modal">
        <div role="dialog" aria-modal="true" aria-label={title} ref={box}>
          <header><h2>{title}</h2><button class="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><Icon name="close" /></button></header>
          <div class="content">{children}</div>
        </div>
      </div>
    </>
  );
}
