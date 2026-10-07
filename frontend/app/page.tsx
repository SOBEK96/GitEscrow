"use client";

import { useMemo, useState } from "react";
import { ConsensusPanel } from "@/components/ConsensusPanel";
import { CreateEscrowModal } from "@/components/CreateEscrowModal";
import { EscrowExplorer } from "@/components/EscrowExplorer";
import { MilestoneDetail, MilestoneProgress } from "@/components/MilestoneInspector";
import { SettlementTerminal } from "@/components/SettlementTerminal";
import { StatsStrip } from "@/components/StatsStrip";
import { TopBar } from "@/components/TopBar";
import { StatusChip } from "@/components/ui";
import { BackendProvider, useBackend } from "@/lib/backend";
import { fmtGen, shortAddr } from "@/lib/format";
import type { Outcome } from "@/lib/types";

function Dashboard() {
  const { backend, snap, toast } = useBackend();
  const [escrowId, setEscrowId] = useState<number | null>(null);
  const [milestoneId, setMilestoneId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [outcomes, setOutcomes] = useState<Record<number, Outcome>>({});

  const actor = backend.actors()[0];

  const escrow = useMemo(() => snap.escrows.find((e) => e.id === escrowId) ?? snap.escrows[0] ?? null, [snap.escrows, escrowId]);
  const milestone = useMemo(() => {
    if (!escrow) return null;
    return escrow.milestones.find((m) => m.id === milestoneId)
      ?? escrow.milestones.find((m) => m.status === "VERIFIED" || m.status === "PENDING")
      ?? escrow.milestones[0];
  }, [escrow, milestoneId]);

  const onOutcome = (o: Outcome) => milestone && setOutcomes((prev) => ({ ...prev, [milestone.id]: o }));

  return (
    <>
      <TopBar />
      <main className="mx-auto max-w-[1400px] space-y-5 px-5 py-6">
        <StatsStrip />
        <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
          <EscrowExplorer selected={escrow?.id ?? null} onSelect={(id) => { setEscrowId(id); setMilestoneId(null); }} onCreate={() => setCreating(true)} />

          <div className="space-y-5">
            {!escrow || !milestone ? (
              <div className="card p-12 text-center text-sm text-zinc-500">{snap.loading ? "Loading protocol state…" : "Select or create an escrow to inspect its milestones."}</div>
            ) : (
              <>
                <div className="card flex flex-wrap items-center justify-between gap-3 p-5">
                  <div>
                    <div className="flex items-center gap-2"><h1 className="text-lg font-semibold">{escrow.title}</h1><StatusChip status={escrow.status} /></div>
                    <div className="mt-1 font-mono text-xs text-zinc-500">
                      #{escrow.id} · github.com/{escrow.repo} · {escrow.branch} · employer {shortAddr(escrow.employer)} → contractor {shortAddr(escrow.contractor)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-mono text-xl font-semibold">{fmtGen(escrow.totalReward)} <span className="text-sm text-zinc-500">GEN</span></div>
                    <div className="text-xs text-zinc-500">+ {fmtGen(escrow.totalBond)} GEN contractor bond ({escrow.bondBps / 100}%)</div>
                  </div>
                </div>
                <MilestoneProgress escrow={escrow} selectedId={milestone.id} onSelect={setMilestoneId} />
                <MilestoneDetail escrow={escrow} m={milestone} actorAddress={actor.address} onOutcome={onOutcome} />
                <ConsensusPanel outcome={outcomes[milestone.id] ?? null} report={milestone.lastReport} minTests={milestone.minTests} minCoverageBps={milestone.minCoverageBps} />
                <SettlementTerminal escrow={escrow} m={milestone} actorAddress={actor.address} onOutcome={onOutcome} />
              </>
            )}
          </div>
        </div>
      </main>

      {creating && <CreateEscrowModal actorAddress={actor.address} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setEscrowId(id); setMilestoneId(null); }} />}
      {toast && (
        <div role="status" className={`fixed bottom-5 left-1/2 z-[60] max-w-md -translate-x-1/2 animate-rise rounded-xl px-4 py-3 text-sm shadow-card ring-1 ${toast.kind === "ok" ? "bg-ink-800 text-ok ring-ok/30" : "bg-ink-800 text-bad ring-bad/30"}`}>
          {toast.text}
        </div>
      )}
    </>
  );
}

export default function Page() {
  return (
    <BackendProvider>
      <Dashboard />
    </BackendProvider>
  );
}
