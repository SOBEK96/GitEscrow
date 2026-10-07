"use client";

import { ReactNode } from "react";
import type { EscrowStatus, MilestoneStatus } from "@/lib/types";

const TONES: Record<string, string> = {
  OPEN: "bg-warn/10 text-warn ring-warn/30",
  ACTIVE: "bg-gl/15 text-gl-soft ring-gl/40",
  CLOSED: "bg-zinc-500/10 text-zinc-400 ring-zinc-500/30",
  CANCELLED: "bg-zinc-500/10 text-zinc-500 ring-zinc-500/30",
  PENDING: "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30",
  VERIFIED: "bg-ok/10 text-ok ring-ok/30",
  RELEASED: "bg-ok/15 text-ok ring-ok/40",
  DEFAULTED: "bg-bad/10 text-bad ring-bad/30",
  FROZEN_EXTERNAL_FAULT: "bg-warn/10 text-warn ring-warn/30",
  CANCELLED_FAULT_FREE: "bg-zinc-500/10 text-zinc-300 ring-zinc-500/30",
};

export function StatusChip({ status }: { status: EscrowStatus | MilestoneStatus }) {
  return <span className={`chip ${TONES[status] ?? TONES.CLOSED}`}>{status.replace(/_/g, " ")}</span>;
}

export function Stat({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: ReactNode; accent?: boolean }) {
  return (
    <div className="card p-4">
      <div className="label">{label}</div>
      <div className={`mt-1.5 font-mono text-2xl font-semibold tabular-nums ${accent ? "text-gl-soft" : "text-zinc-50"}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

export function Section({ title, hint, right, children }: { title: string; hint?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card animate-rise p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
          {hint && <p className="mt-0.5 text-xs text-zinc-500">{hint}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <div className="mt-1.5">{children}</div>
      {hint && <span className="mt-1 block text-[11px] text-zinc-600">{hint}</span>}
    </label>
  );
}

export function Check({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) {
  const color = ok === null ? "bg-zinc-600" : ok ? "bg-ok" : "bg-bad";
  return (
    <li className="flex items-start gap-3 py-2">
      <span className={`mt-1.5 h-2 w-2 flex-none rounded-full ${color}`} />
      <div className="min-w-0 flex-1">
        <div className="text-sm text-zinc-200">{label}</div>
        {detail && <div className="font-mono text-xs text-zinc-500">{detail}</div>}
      </div>
      <span className={`font-mono text-[11px] ${ok === null ? "text-zinc-600" : ok ? "text-ok" : "text-bad"}`}>
        {ok === null ? "—" : ok ? "PASS" : "FAIL"}
      </span>
    </li>
  );
}
