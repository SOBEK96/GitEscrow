"use client";

import { CheckCircle2, RotateCw, XCircle } from "lucide-react";
import { fmtBps, FAILURE_LABELS, shortSha } from "@/lib/format";
import type { Outcome, Report } from "@/lib/types";
import { Check, Section } from "./ui";

export function ConsensusPanel({ outcome, report, minTests, minCoverageBps, checkName, appId }: {
  outcome: Outcome | null; report: Report | null; minTests: number; minCoverageBps: number; checkName?: string; appId?: number;
}) {
  const r = outcome?.report ?? report;
  return (
    <Section title="Live consensus telemetry" hint="Every validator re-fetches GitHub itself and must derive the same verdict."
      right={r && (
        <span className={`chip ${r.passed ? "bg-ok/10 text-ok ring-ok/30" : "bg-bad/10 text-bad ring-bad/30"}`}>
          {r.passed ? <CheckCircle2 size={12} /> : <XCircle size={12} />} {r.passed ? "VERIFIED" : "REJECTED"}
        </span>
      )}>
      {!r ? (
        <div className="rounded-xl bg-ink-850 p-8 text-center text-sm text-zinc-500">No delivery submitted yet. Telemetry appears here after the first on-chain verification.</div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-2">
          <div>
            <div className="label mb-1">Registered evidence · {r.sha ? shortSha(r.sha) : ""}</div>
            <ul className="divide-y divide-white/5">
              <Check ok={r.repo_available} label="Repository reachable" detail={r.repo_available ? "bound by numeric id (rename-proof)" : "deleted, private or blocked — external fault"} />
              <Check ok={r.repo_available ? r.commit_exists : null} label="Commit authenticity" detail={r.commit_exists ? "SHA exists in repository" : "SHA not found via GitHub API"} />
              <Check ok={r.commit_exists ? r.on_branch : null} label="On target branch" detail="compare API: identical | behind" />
              <Check ok={r.commit_exists ? r.repo_match : null} label="Payload provenance" detail={r.repo_match ? "commit payload bound to this repository" : "commit payload from another repository"} />
              <Check ok={r.commit_exists ? r.ci_state === "success" : null} label="Attested check-run" detail={`${checkName ?? "check"} · app ${appId ?? "?"} · state: ${r.ci_state}`} />
              <Check ok={r.commit_exists && r.ci_state !== "none" && r.ci_state !== "pending" ? r.tests_passed >= minTests && r.tests_failed === 0 : null} label="Test results"
                detail={`${r.tests_passed} passed · ${r.tests_failed} failed · need ≥ ${minTests}`} />
              <Check ok={r.commit_exists && r.ci_state !== "none" && r.ci_state !== "pending" ? r.coverage_bps >= minCoverageBps : null} label="Branch coverage" detail={`${fmtBps(r.coverage_bps)} · need ≥ ${fmtBps(minCoverageBps)}`} />
              <Check ok={r.commit_exists && r.ci_state !== "none" && r.ci_state !== "pending" ? r.critical_findings === 0 : null} label="Security analysis"
                detail={r.critical_findings < 0 ? "unverifiable" : `${r.critical_findings} critical findings`} />
              <Check ok={r.report_present ? !r.report_mismatch : null} label="report.json cross-check"
                detail={!r.report_present ? "not committed (optional)" : r.report_mismatch ? "contradicts the check-run — SPOOFED_REPORT_PAYLOAD" : "matches the check-run"} />
            </ul>
            {r.failures.length > 0 && (
              <ul className="mt-3 space-y-1 rounded-xl bg-bad/10 p-3 text-xs text-bad">
                {r.failures.map((f) => <li key={f}>• {FAILURE_LABELS[f] ?? f}</li>)}
              </ul>
            )}
          </div>

          <div>
            <div className="label mb-2">Validator votes</div>
            {outcome ? outcome.trace.rounds.map((round) => (
              <div key={round.round} className="mb-3 rounded-xl bg-ink-850 p-3 ring-1 ring-white/5">
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="font-mono text-zinc-400">Round {round.round} · leader claims <b className={round.leaderClaim === "PASS" ? "text-ok" : "text-bad"}>{round.leaderClaim}</b></span>
                  {round.accepted ? <span className="text-ok">accepted</span> : <span className="flex items-center gap-1 text-warn"><RotateCw size={11} /> rejected · rotating leader</span>}
                </div>
                <ul className="space-y-1">
                  {round.votes.map((v) => (
                    <li key={v.validator} className="flex items-center gap-2 text-xs">
                      <span className={`h-1.5 w-1.5 rounded-full ${v.vote === "AGREE" ? "bg-ok" : "bg-bad"}`} />
                      <span className="w-24 font-mono text-zinc-300">{v.validator}</span>
                      {v.role === "leader" && <span className="rounded bg-gl/20 px-1.5 text-[10px] text-gl-soft">LEADER</span>}
                      <span className="flex-1 truncate text-zinc-500">{v.note}</span>
                      <span className={`font-mono ${v.vote === "AGREE" ? "text-ok" : "text-bad"}`}>{v.vote}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )) : (
              <div className="rounded-xl bg-ink-850 p-4 text-xs text-zinc-500">Vote breakdown is streamed at decision time. This verdict was loaded from contract storage.</div>
            )}
            {outcome?.trace.txHash && <div className="font-mono text-[11px] text-zinc-600">tx {outcome.trace.txHash}</div>}
            {outcome?.report.dispute_outcome && (
              <div className="mt-2 rounded-xl bg-gl/10 p-3 text-xs text-gl-soft">
                Dispute outcome: <b>{outcome.report.dispute_outcome === "UPHELD_DELIVERY" ? "delivery upheld — bond forfeited to contractor" : "delivery overturned — bond refunded"}</b>
              </div>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}
