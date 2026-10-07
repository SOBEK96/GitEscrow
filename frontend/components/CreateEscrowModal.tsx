"use client";

import { Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useBackend } from "@/lib/backend";
import { DEFAULT_APP_ID, MAX_BOND_BPS, MAX_MILESTONES, MIN_BOND_BPS, bondFor } from "@/lib/rules";
import { fmtGen, parseGen } from "@/lib/format";
import type { MilestoneInput } from "@/lib/types";
import { Field } from "./ui";

interface Row { title: string; reward: string; tests: string; coverage: string; deadline: string; sha: string; check: string; app: string }

const toLocalInput = (ts: number) => {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

export function CreateEscrowModal({ actorAddress, onClose, onCreated }: { actorAddress: string; onClose(): void; onCreated(id: number): void }) {
  const { backend, run, notify } = useBackend();
  const realNow = Math.floor(Date.now() / 1000);

  const [contractor, setContractor] = useState("");
  const [repo, setRepo] = useState("acme/widgets");
  const [branch, setBranch] = useState("main");
  const [title, setTitle] = useState("");
  const [bondPct, setBondPct] = useState(15);
  const [rows, setRows] = useState<Row[]>([
    { title: "", reward: "100", tests: "50", coverage: "80", deadline: toLocalInput(realNow + 7 * 86400), sha: "", check: "ci/tests", app: String(DEFAULT_APP_ID) },
  ]);

  const totals = useMemo(() => {
    try {
      const rewards = rows.map((r) => parseGen(r.reward));
      const total = rewards.reduce((a, b) => a + b, 0n);
      const bond = rewards.reduce((a, r) => a + bondFor(r, bondPct * 100), 0n);
      return { total, bond };
    } catch { return null; }
  }, [rows, bondPct]);

  const patch = (i: number, p: Partial<Row>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...p } : r)));

  async function submit() {
    let milestones: MilestoneInput[];
    try {
      milestones = rows.map((r, i) => ({
        title: r.title.trim() || `Milestone ${i + 1}`,
        reward: parseGen(r.reward),
        expectedSha: r.sha.trim().toLowerCase(),
        checkName: r.check.trim(),
        appId: parseInt(r.app || "0", 10),
        minTests: Math.max(0, parseInt(r.tests || "0", 10)),
        minCoverageBps: Math.round(Math.min(100, Math.max(0, parseFloat(r.coverage || "0"))) * 100),
        deadline: Math.floor(new Date(r.deadline).getTime() / 1000),
      }));
    } catch (e) { notify("err", e instanceof Error ? e.message : "Invalid milestone"); return; }
    const id = await run(() => backend.createEscrow(actorAddress, {
      contractor: contractor.trim(), repo: repo.trim(), branch: branch.trim(), title: title.trim(), bondBps: bondPct * 100, milestones,
    }));
    if (id !== undefined) { notify("ok", `Escrow #${id} funded — waiting for the contractor's bond`); onCreated(id); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Create escrow">
      <div className="card my-8 w-full max-w-3xl animate-rise p-6">
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Create escrow</h2>
            <p className="text-xs text-zinc-500">You deposit every milestone reward now; the contractor stakes a 10–20% bond to start.</p>
          </div>
          <button className="btn-ghost !p-2" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Project title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="SDK streaming transport grant" /></Field>
          <Field label="Contractor address">
            <input className="input font-mono" value={contractor} onChange={(e) => setContractor(e.target.value)} placeholder="0x…" />
          </Field>
          <Field label="Repository (owner/repo)"><input className="input font-mono" value={repo} onChange={(e) => setRepo(e.target.value)} /></Field>
          <Field label="Target branch"><input className="input font-mono" value={branch} onChange={(e) => setBranch(e.target.value)} /></Field>
          <Field label={`Contractor performance bond · ${bondPct}% of each reward`} hint={`Allowed range ${MIN_BOND_BPS / 100}–${MAX_BOND_BPS / 100}%`}>
            <input type="range" min={MIN_BOND_BPS / 100} max={MAX_BOND_BPS / 100} step={1} value={bondPct} onChange={(e) => setBondPct(+e.target.value)} className="w-full accent-gl" />
          </Field>
        </div>

        <div className="mt-6 flex items-center justify-between">
          <span className="label">Milestones</span>
          <button className="btn-ghost !px-3 !py-1 text-xs" disabled={rows.length >= MAX_MILESTONES}
            onClick={() => setRows((rs) => [...rs, { ...rs[rs.length - 1], title: "", sha: "" }])}><Plus size={13} /> Add</button>
        </div>
        <div className="mt-2 space-y-3">
          {rows.map((r, i) => (
            <div key={i} className="rounded-xl bg-ink-850 p-3 ring-1 ring-white/5">
              <div className="grid gap-3 md:grid-cols-6">
                <div className="md:col-span-3"><Field label={`M${i + 1} title`}><input className="input" value={r.title} onChange={(e) => patch(i, { title: e.target.value })} placeholder={`Milestone ${i + 1}`} /></Field></div>
                <div><Field label="Reward (GEN)"><input className="input font-mono" inputMode="decimal" value={r.reward} onChange={(e) => patch(i, { reward: e.target.value })} /></Field></div>
                <div><Field label="Min tests"><input className="input font-mono" inputMode="numeric" value={r.tests} onChange={(e) => patch(i, { tests: e.target.value })} /></Field></div>
                <div><Field label="Min branch cov. %"><input className="input font-mono" inputMode="decimal" value={r.coverage} onChange={(e) => patch(i, { coverage: e.target.value })} /></Field></div>
                <div className="md:col-span-3"><Field label="Deadline"><input type="datetime-local" className="input" value={r.deadline} onChange={(e) => patch(i, { deadline: e.target.value })} /></Field></div>
                <div className="md:col-span-3"><Field label="Attested check-run name" hint="CI check-run whose output carries the test / coverage / security numbers"><input className="input font-mono" value={r.check} onChange={(e) => patch(i, { check: e.target.value })} /></Field></div>
                <div className="md:col-span-3"><Field label="Trusted GitHub App id" hint="15368 = GitHub Actions. Check-runs from any other app are ignored"><input className="input font-mono" inputMode="numeric" value={r.app} onChange={(e) => patch(i, { app: e.target.value })} /></Field></div>
                <div className="md:col-span-6"><Field label="Pinned commit SHA (optional)"><input className="input font-mono" value={r.sha} onChange={(e) => patch(i, { sha: e.target.value })} placeholder="40-hex, leave empty to accept any commit on the branch" /></Field></div>
              </div>
              {rows.length > 1 && (
                <button className="mt-2 flex items-center gap-1 text-[11px] text-zinc-500 hover:text-bad" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}><Trash2 size={11} /> Remove</button>
              )}
            </div>
          ))}
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-xl bg-gl/10 p-4 ring-1 ring-gl/30">
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            <span className="text-zinc-400">You deposit now</span><span className="text-right font-mono font-semibold">{totals ? fmtGen(totals.total) : "—"} GEN</span>
            <span className="text-zinc-400">Contractor bonds</span><span className="text-right font-mono">{totals ? fmtGen(totals.bond) : "—"} GEN</span>
          </div>
          <button className="btn-primary" onClick={submit} disabled={!totals || !title.trim()}>Fund escrow</button>
        </div>
      </div>
    </div>
  );
}
