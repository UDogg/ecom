// Line icons, 24 x 24, drawn with currentColor.
const paths = {
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm9 16-4.35-4.35",
  bag: "M6 8h12l-1 12H7L6 8Zm3 0V6a3 3 0 0 1 6 0v2",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0",
  close: "M6 6l12 12M18 6 6 18",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  check: "m5 12 5 5L20 7",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  back: "M19 12H5m6 6-6-6 6-6",
  truck: "M3 7h11v9H3zM14 10h4l3 3v3h-7M7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm10 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  shield: "M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3Zm-3 9 2 2 4-4",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  card: "M3 6h18v12H3zM3 10h18M7 15h4",
  box: "M3 7l9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  chart: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  tag: "M3 12V4h8l10 10-8 8L3 12Zm5-4h.01",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2",
  alert: "M12 9v4m0 4h.01M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0-15v2m0 16v2M4.2 4.2l1.4 1.4m12.8 12.8 1.4 1.4M2 12h2m16 0h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  sparkle: "M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5m7 7L18 18M6 18l2.5-2.5m7-7L18 6",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  edit: "M4 20h4L19 9l-4-4L4 16v4Zm10-14 4 4",
  archive: "M3 5h18v4H3zM5 9v10h14V9M10 13h4",
  undo: "M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3",
  filter: "M3 5h18l-7 8v6l-4 2v-8L3 5Z",
  home: "M3 11 12 4l9 7v9h-6v-6H9v6H3v-9Z",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 20, class: cls = "" }: { name: IconName; size?: number; class?: string }) {
  return (
    <svg class={`icon ${cls}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
      <path d={paths[name]} />
    </svg>
  );
}
