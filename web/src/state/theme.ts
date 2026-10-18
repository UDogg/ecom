import { signal } from "@preact/signals";
import { read, write } from "../lib/store";

const system = () => (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
export const theme = signal<"light" | "dark">(read<"light" | "dark" | null>("bb-theme", null) ?? system());

export function applyTheme() {
  document.documentElement.dataset.theme = theme.value;
}

export function toggleTheme() {
  theme.value = theme.value === "dark" ? "light" : "dark";
  write("bb-theme", theme.value);
  applyTheme();
}
