"use client";

import { GitBranch, Plus } from "lucide-react";
import { useBackend } from "@/lib/backend";
import { fmtGen, shortAddr } from "@/lib/format";
import type { Escrow } from "@/lib/types";
import { StatusChip } from "./ui";

export function EscrowExplorer({ selected, onSelect, onCreate }: { selected: number | null; onSelect(id: number): void; onCreate(): void }) {
  const { snap } = useBackend();

  return (
    <section className="card flex max-h-[calc(100vh-9rem)] flex-col p-4 lg:sticky lg:top-24">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Escrow explorer</h2>
          <p className="text-xs text-zinc-500">{snap.escrows.length} on-chain</p>
        </div>
        <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={onCreate}><Plus size={14} /> New escrow</button>
      </div>
      <div className="-mx-1 flex-1 space-y-2 overflow-y-auto px-1 pb-1">
        {snap.loading && <div className="py-8 text-center text-sm text-zinc-500">Loading…</div>}
        {snap.error && <div className="rounded-xl bg-bad/10 p-3 text-xs text-bad">{snap.error}</div>}
        {!snap.loading && !snap.error && snap.escrows.length === 0 && (
          <div className="rounded-xl bg-ink-850 p-6 text-center text-sm text-zinc-500">No escrows yet. Create the first one.</div>
        )}
        {snap.escrows.map((e) => <Row key={e.id} e={e} active={e.id === selected} onClick={() => onSelect(e.id)} />)}
      </div>
    </section>
  );
}

function Row({ e, active, onClick }: { e: Escrow; active: boolean; onClick(): void }) {
  const done = e.milestones.filter((m) => m.status === "RELEASED").length;
  return (
    <button onClick={onClick} aria-pressed={active}
      className={`block w-full rounded-xl p-3 text-left ring-1 transition ${active ? "bg-gl/10 ring-gl/50" : "bg-ink-850 ring-white/5 hover:ring-white/15"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-zinc-100">{e.title}</div>
          <div className="mt-0.5 flex items-center gap-1 font-mono text-[11px] text-zinc-500"><GitBranch size={11} />{e.repo}@{e.branch}</div>
        </div>
        <StatusChip status={e.status} />
      </div>
      <div className="mt-3 flex gap-1">
        {e.milestones.map((m) => (
          <span key={m.id} className={`h-1.5 flex-1 rounded-full ${m.status === "RELEASED" ? "bg-ok" : m.status === "VERIFIED" ? "bg-ok/50" : m.status === "DEFAULTED" ? "bg-bad" : "bg-ink-600"}`} />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-500">
        <span>{done}/{e.milestones.length} released</span>
        <span className="font-mono text-zinc-300">{fmtGen(e.totalReward)} GEN</span>
      </div>
      <div className="mt-1 font-mono text-[10px] text-zinc-600">{shortAddr(e.employer)} → {shortAddr(e.contractor)}</div>
    </button>
  );
}
