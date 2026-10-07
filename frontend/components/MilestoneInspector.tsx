"use client";

import { FileCode2, GitCommitHorizontal, Play, Target } from "lucide-react";
import { useState } from "react";
import { useBackend } from "@/lib/backend";
import { MAX_ATTEMPTS } from "@/lib/rules";
import { fmtBps, fmtDate, fmtGen, shortSha } from "@/lib/format";
import type { Escrow, Milestone, Outcome } from "@/lib/types";
import { Section, StatusChip } from "./ui";

export function MilestoneProgress({ escrow, selectedId, onSelect }: { escrow: Escrow; selectedId: number; onSelect(id: number): void }) {
  const released = escrow.milestones.filter((m) => m.status === "RELEASED").length;
  const pct = Math.round((released / escrow.milestones.length) * 100);
  return (
    <Section title="Milestone inspector" hint="Funds move only when a validator quorum confirms the deliverable on GitHub."
      right={<span className="font-mono text-xs text-zinc-400">{released}/{escrow.milestones.length} · {pct}%</span>}>
      <div className="mb-4 h-2 overflow-hidden rounded-full bg-ink-700" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded-full bg-gradient-to-r from-gl to-ok transition-all duration-700" style={{ width: `${pct}%` }} />
      </div>
      <ol className="grid gap-2 md:grid-cols-[repeat(auto-fit,minmax(150px,1fr))]">
        {escrow.milestones.map((m) => (
          <li key={m.id}>
            <button onClick={() => onSelect(m.id)} aria-pressed={m.id === selectedId}
              className={`w-full rounded-xl p-3 text-left ring-1 transition ${m.id === selectedId ? "bg-gl/10 ring-gl/50" : "bg-ink-850 ring-white/5 hover:ring-white/15"}`}>
              <div className="flex items-center justify-between"><span className="font-mono text-[11px] text-zinc-500">M{m.index + 1}</span><StatusChip status={m.status} /></div>
              <div className="mt-1.5 truncate text-sm text-zinc-200">{m.title}</div>
              <div className="mt-0.5 font-mono text-xs text-zinc-400">{fmtGen(m.reward)} GEN</div>
            </button>
          </li>
        ))}
      </ol>
    </Section>
  );
}

export function MilestoneDetail({ escrow, m, actorAddress, onOutcome }: { escrow: Escrow; m: Milestone; actorAddress: string; onOutcome(o: Outcome): void }) {
  const { backend, run, notify, busy, snap } = useBackend();
  const [sha, setSha] = useState("");
  const isContractor = actorAddress.toLowerCase() === escrow.contractor.toLowerCase();
  const expired = snap.now > m.deadline;
  const canDeliver = escrow.status === "ACTIVE" && m.status === "PENDING" && isContractor && !expired && m.attempts < MAX_ATTEMPTS;

  async function deliver() {
    const out = await run(() => backend.evaluate(actorAddress, m.id, sha || m.expectedSha));
    if (!out) return;
    onOutcome(out);
    notify(out.report.passed ? "ok" : "err", out.report.passed ? "Consensus verified the delivery — dispute window opened" : "Consensus rejected the delivery — see telemetry");
  }

  return (
    <Section title={`M${m.index + 1} · ${m.title}`} hint={`${escrow.repo} @ ${escrow.branch}`} right={<StatusChip status={m.status} />}>
      <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <Criterion icon={<Target size={14} />} label="Reward / bond" value={`${fmtGen(m.reward)} / ${fmtGen(m.bond)} GEN`} />
        <Criterion icon={<FileCode2 size={14} />} label="Min passing tests" value={String(m.minTests)} />
        <Criterion icon={<FileCode2 size={14} />} label="Min branch coverage" value={fmtBps(m.minCoverageBps)} />
        <Criterion icon={<GitCommitHorizontal size={14} />} label="Pinned commit" value={m.expectedSha ? shortSha(m.expectedSha) : "any on branch"} />
      </dl>
      <div className="mt-2 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <Criterion label="Critical findings" value="0 allowed" />
        <Criterion label="Deadline" value={fmtDate(m.deadline)} tone={expired && m.status === "PENDING" ? "bad" : undefined} />
        <Criterion label="Attempts" value={`${m.attempts} / ${MAX_ATTEMPTS}`} />
        <Criterion label="Submitted" value={m.submittedSha ? shortSha(m.submittedSha) : "—"} />
      </div>

      {m.status === "PENDING" && (
        <div className="mt-5 rounded-xl bg-ink-850 p-4 ring-1 ring-white/5">
          <div className="label">Deliverable submission</div>
          <div className="mt-2 flex flex-col gap-2 md:flex-row">
            <input className="input font-mono" value={sha} onChange={(e) => setSha(e.target.value)} disabled={!canDeliver}
              placeholder={m.expectedSha || "full 40-character commit SHA"} aria-label="Commit SHA" />
            <button className="btn-primary whitespace-nowrap" disabled={!canDeliver || busy} onClick={deliver}><Play size={14} /> Verify on-chain</button>
          </div>
          {!isContractor && <p className="mt-2 text-xs text-zinc-500">Only the contractor can submit a commit. Switch persona to submit.</p>}
          {isContractor && escrow.status !== "ACTIVE" && <p className="mt-2 text-xs text-warn">Post the performance bond first to activate this escrow.</p>}
          {expired && <p className="mt-2 text-xs text-bad">Deadline passed — the employer may claim a default.</p>}
        </div>
      )}
    </Section>
  );
}

function Criterion({ label, value, icon, tone }: { label: string; value: string; icon?: React.ReactNode; tone?: "bad" }) {
  return (
    <div className="rounded-xl bg-ink-850 p-3 ring-1 ring-white/5">
      <dt className="label flex items-center gap-1.5">{icon}{label}</dt>
      <dd className={`mt-1 font-mono text-[13px] ${tone === "bad" ? "text-bad" : "text-zinc-100"}`}>{value}</dd>
    </div>
  );
}
