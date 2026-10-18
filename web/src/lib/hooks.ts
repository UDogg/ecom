import { useEffect, useRef, useState } from "preact/hooks";

export interface Async<T> { data: T | undefined; error: Error | undefined; loading: boolean; reload: () => void }

/** Runs `load` whenever `deps` change; stale answers (from an earlier key) are dropped. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): Async<T> {
  const [state, setState] = useState<{ data?: T; error?: Error; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const run = useRef(0);
  useEffect(() => {
    const mine = ++run.current;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    load().then(
      (data) => mine === run.current && setState({ data, loading: false }),
      (error: Error) => mine === run.current && setState((s) => ({ data: s.data, error, loading: false })),
    );
  }, [...deps, tick]);
  return { data: state.data, error: state.error, loading: state.loading, reload: () => setTick((t) => t + 1) };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Sets the document title for the page. */
export function useTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · Babel Bazaar` : "Babel Bazaar";
  }, [title]);
}
