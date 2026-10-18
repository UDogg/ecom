import { Icon } from "./Icon";

export function Stepper({ value, onChange, min = 1, max = 99, small = false, label = "Quantity" }:
  { value: number; onChange: (n: number) => void; min?: number; max?: number; small?: boolean; label?: string }) {
  return (
    <div class={`stepper ${small ? "sm" : ""}`} role="group" aria-label={label}>
      <button type="button" aria-label="One less" disabled={value <= min} onClick={() => onChange(value - 1)}><Icon name="minus" size={16} /></button>
      <output aria-live="polite">{value}</output>
      <button type="button" aria-label="One more" disabled={value >= max} onClick={() => onChange(value + 1)}><Icon name="plus" size={16} /></button>
    </div>
  );
}
