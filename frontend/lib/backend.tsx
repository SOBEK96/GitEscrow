"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { LiveBackend } from "./live";
import { friendlyError } from "./wallet";
import type { Escrow, Solvency, Stats } from "./types";

export interface Snapshot {
  escrows: Escrow[];
  stats: Stats | null;
  solvency: Solvency | null;
  now: number;
  loading: boolean;
  error: string | null;
}

interface Ctx {
  backend: LiveBackend;
  snap: Snapshot;
  refresh(): Promise<void>;
  /** Run a mutation, surface its error, refresh afterwards. */
  run<T>(fn: () => Promise<T>): Promise<T | undefined>;
  busy: boolean;
  toast: { kind: "ok" | "err"; text: string } | null;
  notify(kind: "ok" | "err", text: string): void;
}

const BackendCtx = createContext<Ctx | null>(null);

export function BackendProvider({ children }: { children: React.ReactNode }) {
  const ref = useRef<LiveBackend | null>(null);
  if (!ref.current) ref.current = new LiveBackend();
  const backend = ref.current;

  const [snap, setSnap] = useState<Snapshot>({ escrows: [], stats: null, solvency: null, now: 0, loading: true, error: null });
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<Ctx["toast"]>(null);

  const notify = useCallback((kind: "ok" | "err", text: string) => {
    setToast({ kind, text });
    setTimeout(() => setToast((t) => (t && t.text === text ? null : t)), 6000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      await backend.init();
      const [escrows, stats, solvency] = await Promise.all([backend.listEscrows(), backend.stats(), backend.solvency()]);
      setSnap({ escrows, stats, solvency, now: backend.now(), loading: false, error: null });
    } catch (e) {
      setSnap((s) => ({ ...s, loading: false, error: friendlyError(e) }));
    }
  }, [backend]);

  useEffect(() => {
    refresh();
    const unsub = backend.subscribe(refresh);
    const tick = setInterval(() => setSnap((s) => ({ ...s, now: backend.now() })), 1000);
    const poll = setInterval(refresh, 45000); // Studio allows 30 RPC requests/minute per client
    return () => { unsub(); clearInterval(tick); clearInterval(poll); };
  }, [backend, refresh]);

  const run = useCallback(async <T,>(fn: () => Promise<T>) => {
    setBusy(true);
    try {
      const out = await fn();
      await refresh();
      return out;
    } catch (e) {
      notify("err", friendlyError(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [refresh, notify]);

  const value = useMemo<Ctx>(() => ({ backend, snap, refresh, run, busy, toast, notify }), [backend, snap, refresh, run, busy, toast, notify]);
  return <BackendCtx.Provider value={value}>{children}</BackendCtx.Provider>;
}

export function useBackend() {
  const c = useContext(BackendCtx);
  if (!c) throw new Error("BackendProvider missing");
  return c;
}
