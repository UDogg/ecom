import { dismiss, toasts } from "../state/toast";
import { Icon } from "./Icon";

export function Toasts() {
  return (
    <div class="toasts" role="status" aria-live="polite">
      {toasts.value.map((t) => (
        <div class={`toast ${t.tone}`} key={t.id}>
          <Icon name={t.tone === "error" ? "alert" : t.tone === "ok" ? "check" : "sparkle"} />
          <span>{t.text}</span>
          {t.action && <button onClick={() => { t.action!.run(); dismiss(t.id); }}>{t.action.label}</button>}
        </div>
      ))}
    </div>
  );
}
