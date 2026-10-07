"use client";

import { ShieldCheck } from "lucide-react";
import { useBackend } from "@/lib/backend";
import { fmtGen } from "@/lib/format";
import { Stat } from "./ui";

export function StatsStrip() {
  const { snap } = useBackend();
  const s = snap.stats;
  const sol = snap.solvency;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Stat label="Active escrows" value={s ? s.activeEscrows : "—"} sub={s ? `${s.escrowCount} created` : undefined} />
      <Stat label="Total value locked" value={s ? `${fmtGen(s.tvl)}` : "—"} sub="GEN · rewards + bonds" accent />
      <Stat label="Dual-staking" value={s ? fmtGen(s.lockedBonds) : "—"} sub={s ? `GEN bond vs ${fmtGen(s.lockedRewards)} reward` : undefined} />
      <Stat label="Released" value={s ? fmtGen(s.totalReleased) : "—"} sub="GEN paid to contractors" />
      <Stat label="Slashed" value={s ? fmtGen(s.totalSlashed) : "—"} sub="GEN from defaults" />
      <div className="card flex flex-col justify-between p-4">
        <div className="label">Solvency</div>
        <div className={`mt-1.5 flex items-center gap-2 text-lg font-semibold ${sol?.solvent === false ? "text-bad" : "text-ok"}`}>
          <ShieldCheck size={20} /> {sol ? (sol.solvent ? "Balanced" : "MISMATCH") : "—"}
        </div>
        <div className="mt-1 font-mono text-[11px] text-zinc-500">
          {sol ? `in ${fmtGen(sol.totalIn)} = out ${fmtGen(sol.totalPaidOut)} + held ${fmtGen(sol.liabilities)}${sol.feesRetained > 0n ? ` + fees ${fmtGen(sol.feesRetained)}` : ""}` : ""}
        </div>
      </div>
    </div>
  );
}
