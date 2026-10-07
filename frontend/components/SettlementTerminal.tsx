"use client";

import { Gavel, Hourglass, Landmark, Scale, Unlock } from "lucide-react";
import { useEffect, useState } from "react";
import { useBackend } from "@/lib/backend";
import { MAX_DISPUTES } from "@/lib/rules";
import { fmtDuration, fmtGen } from "@/lib/format";
import type { Escrow, Milestone, Outcome } from "@/lib/types";
import { Section } from "./ui";

export function SettlementTerminal({ escrow, m, actorAddress, onOutcome }: { escrow: Escrow; m: Milestone; actorAddress: string; onOutcome(o: Outcome): void }) {
  const { backend, snap, run, busy, notify } = useBackend();
  const [reason, setReason] = useState("");
  const [bond, setBond] = useState<bigint | null>(null);
  const eq = (a: string) => actorAddress.toLowerCase() === a.toLowerCase();
  const isEmployer = eq(escrow.employer);
  const isContractor = eq(escrow.contractor);
  const now = snap.now;

  useEffect(() => {
    let live = true;
    backend.quoteDisputeBond(m.id, escrow.employer).then((b) => live && setBond(b)).catch(() => live && setBond(m.nextDisputeBond));
    return () => { live = false; };
  }, [backend, m.id, m.disputeCount, m.nextDisputeBond, escrow.employer]);

  const windowLeft = m.releaseAt - now;
  const deadlineLeft = m.deadline - now;

  return (
    <Section title="Dispute & settlement terminal" hint="Trustless release, default slashing and escalating anti-griefing bonds.">
      <div className="grid gap-4 lg:grid-cols-2">
        {/* ----------------------------------------------------- state / actions */}
        <div className="space-y-3">
          {escrow.status === "OPEN" && (
            <Box icon={<Landmark size={15} />} title="Awaiting contractor bond">
              <p className="text-xs text-zinc-400">The reward is locked. The contractor must stake <b className="font-mono text-zinc-100">{fmtGen(escrow.totalBond)} GEN</b> ({escrow.bondBps / 100}%) to start the clock.</p>
              <div className="mt-3 flex gap-2">
                <button className="btn-primary" disabled={!isContractor || busy} onClick={() => run(() => backend.acceptEscrow(actorAddress, escrow.id)).then(() => notify("ok", "Bond posted — escrow active"))}>Post bond</button>
                <button className="btn-ghost" disabled={!isEmployer || busy} onClick={() => run(() => backend.cancelEscrow(actorAddress, escrow.id)).then(() => notify("ok", "Escrow cancelled, deposit refunded"))}>Cancel & refund</button>
              </div>
            </Box>
          )}

          {escrow.status === "ACTIVE" && m.status === "PENDING" && (
            <Box icon={<Hourglass size={15} />} title="Delivery deadline" tone={deadlineLeft <= 0 ? "bad" : undefined}>
              <div className="font-mono text-3xl font-semibold tabular-nums">{deadlineLeft > 0 ? fmtDuration(deadlineLeft) : "EXPIRED"}</div>
              <p className="mt-2 text-xs text-zinc-500">
                {deadlineLeft > 0 ? "Deliver a verified commit before this reaches zero, or the bond is slashed."
                  : `Contractor defaulted. The employer reclaims ${fmtGen(m.reward)} + the ${fmtGen(m.bond)} GEN slashed bond.`}
              </p>
              <button className="btn-danger mt-3" disabled={deadlineLeft > 0 || busy}
                onClick={() => run(() => backend.claimDefault(actorAddress, m.id)).then(() => notify("ok", "Default settled: refund + slashed bond sent to employer"))}>
                <Gavel size={14} /> Claim default & slash
              </button>
            </Box>
          )}

          {m.status === "VERIFIED" && (
            <Box icon={<Unlock size={15} />} title="48h dispute grace period" tone="ok">
              <div className="font-mono text-3xl font-semibold tabular-nums">{windowLeft > 0 ? fmtDuration(windowLeft) : "OPEN FOR SETTLEMENT"}</div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
                <div className="h-full bg-ok transition-all" style={{ width: `${Math.min(100, Math.max(0, ((now - m.verifiedAt) / (m.releaseAt - m.verifiedAt)) * 100))}%` }} />
              </div>
              <p className="mt-2 text-xs text-zinc-500">Release sends <b className="font-mono text-zinc-200">{fmtGen(m.reward)} GEN</b> reward + <b className="font-mono text-zinc-200">{fmtGen(m.bond)} GEN</b> returned bond to the contractor.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="btn-primary" disabled={windowLeft > 0 || busy}
                  onClick={() => run(() => backend.settle(actorAddress, m.id)).then(() => notify("ok", "Milestone settled — payout released"))}>Claim settlement</button>
                <button className="btn-ghost" disabled={!isEmployer || busy}
                  onClick={() => run(() => backend.approve(actorAddress, m.id)).then(() => notify("ok", "Employer approved — instant release"))}>Approve now (waive window)</button>
              </div>
            </Box>
          )}

          {m.status === "RELEASED" && <Done tone="ok" text={`Released. ${fmtGen(m.reward + m.bond)} GEN paid to the contractor (reward + returned bond).`} />}
          {m.status === "DEFAULTED" && <Done tone="bad" text={`Defaulted. ${fmtGen(m.reward + m.bond)} GEN returned to the employer — ${fmtGen(m.bond)} GEN of it slashed from the contractor.`} />}
          {m.status === "CANCELLED" && <Done tone="muted" text="Escrow cancelled before bonding. Deposit refunded." />}
        </div>

        {/* -------------------------------------------------------------- dispute */}
        <Box icon={<Scale size={15} />} title="Dispute with escalating bond">
          <div className="flex items-end gap-1.5" aria-label="Dispute bond ladder">
            {Array.from({ length: MAX_DISPUTES }).map((_, i) => {
              const mult = 2 ** i;
              const state = i < m.disputeCount ? "used" : i === m.disputeCount ? "next" : "future";
              return (
                <div key={i} className="flex-1 text-center">
                  <div className={`rounded-lg px-1 py-2 font-mono text-xs ring-1 ${state === "used" ? "bg-zinc-500/10 text-zinc-500 line-through ring-white/5" : state === "next" ? "bg-warn/10 text-warn ring-warn/40" : "bg-ink-900 text-zinc-500 ring-white/5"}`}
                    style={{ height: 36 + i * 14 }}>{mult}×</div>
                  <div className="mt-1 text-[10px] text-zinc-600">#{i + 1}</div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-xs text-zinc-500">Next dispute bond</span>
            <span className="font-mono text-lg font-semibold text-warn">{bond !== null ? fmtGen(bond, 3) : "—"} GEN</span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-zinc-600">
            max(0.1 GEN, 1% of reward) × 2<sup>disputes + lost-dispute strikes</sup>. A fresh quorum re-verifies the commit: if the delivery holds, your bond goes to the contractor; if it was invalidated (e.g. force-push), the bond is refunded and the milestone reopens.
          </p>
          <textarea className="input mt-3 min-h-[64px]" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500}
            placeholder="Why should the delivery be invalid?" aria-label="Dispute reason" disabled={m.status !== "VERIFIED" || !isEmployer} />
          <button className="btn-danger mt-3 w-full" disabled={m.status !== "VERIFIED" || !isEmployer || windowLeft <= 0 || !reason.trim() || busy || m.disputeCount >= MAX_DISPUTES}
            onClick={async () => {
              const out = await run(() => backend.fileDispute(actorAddress, m.id, reason.trim()));
              if (out) { onOutcome(out); setReason(""); notify(out.report.dispute_outcome === "UPHELD_DELIVERY" ? "err" : "ok",
                out.report.dispute_outcome === "UPHELD_DELIVERY" ? "Dispute lost: delivery upheld, bond forfeited" : "Dispute won: delivery overturned, bond refunded"); }
            }}>
            File dispute · stake {bond !== null ? fmtGen(bond, 3) : "—"} GEN
          </button>
          {m.status !== "VERIFIED" && <p className="mt-2 text-[11px] text-zinc-600">Disputes open once a delivery is verified, and close when the 48h window ends.</p>}
          {m.status === "VERIFIED" && !isEmployer && <p className="mt-2 text-[11px] text-zinc-600">Only the employer can dispute.</p>}
        </Box>
      </div>
    </Section>
  );
}

function Box({ icon, title, children, tone }: { icon: React.ReactNode; title: string; children: React.ReactNode; tone?: "ok" | "bad" }) {
  return (
    <div className={`rounded-xl bg-ink-850 p-4 ring-1 ${tone === "ok" ? "ring-ok/25" : tone === "bad" ? "ring-bad/30" : "ring-white/5"}`}>
      <div className="label mb-2 flex items-center gap-1.5">{icon}{title}</div>
      {children}
    </div>
  );
}

function Done({ tone, text }: { tone: "ok" | "bad" | "muted"; text: string }) {
  const c = tone === "ok" ? "bg-ok/10 text-ok ring-ok/25" : tone === "bad" ? "bg-bad/10 text-bad ring-bad/25" : "bg-ink-850 text-zinc-400 ring-white/5";
  return <div className={`rounded-xl p-4 text-sm ring-1 ${c}`}>{text}</div>;
}
