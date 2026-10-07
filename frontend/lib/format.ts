export const ATTO = 10n ** 18n;

export function fmtGen(value: bigint, maxDecimals = 2): string {
  const negative = value < 0n;
  const abs = negative ? -value : value;
  const whole = abs / ATTO;
  const frac = abs % ATTO;
  let fracStr = frac.toString().padStart(18, "0").slice(0, maxDecimals).replace(/0+$/, "");
  const wholeStr = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${wholeStr}${fracStr ? "." + fracStr : ""}`;
}

export function parseGen(text: string): bigint {
  const t = text.trim();
  if (!/^\d+(\.\d{1,18})?$/.test(t)) throw new Error("Enter a positive amount");
  const [w, f = ""] = t.split(".");
  return BigInt(w) * ATTO + BigInt(f.padEnd(18, "0"));
}

export function shortAddr(a: string): string {
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

export function shortSha(s: string): string {
  return s ? s.slice(0, 7) : "—";
}

export function fmtBps(bps: number): string {
  return `${(bps / 100).toFixed(bps % 100 === 0 ? 0 : 2)}%`;
}

export function fmtDuration(seconds: number): string {
  if (seconds <= 0) return "0s";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  return `${m}m ${s}s`;
}

export function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const FAILURE_LABELS: Record<string, string> = {
  repo_unavailable: "Repository is deleted, private or unreachable (external fault)",
  commit_not_found: "Commit does not exist on the repository",
  spoofed_payload: "Spoofed payload: commit belongs to another repository",
  SPOOFED_REPORT_PAYLOAD: "report.json contradicts the authentic CI check-run output",
  commit_not_on_ref: "Commit is not part of the delivery ref (target branch, branch or PR head)",
  ci_pending: "Attested check-run still running (no attempt consumed)",
  ci_attestation_missing: "No check-run with the required name from the trusted GitHub App",
  ci_failed: "The attested CI check-run failed",
  tests_below_minimum: "Passing test count below the contract minimum",
  tests_failing: "Failing tests present",
  coverage_below_minimum: "Branch coverage below the contract minimum",
  critical_findings: "Critical security findings present or unverifiable",
};
