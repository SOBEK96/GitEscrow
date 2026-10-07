// Live backend: reads and writes the deployed GitEscrow contract through genlayer-js.
//
// Identity is a browser-held key (generated once, or pasted by the user): this
// is a hackathon dashboard, not a custody product. The key never leaves the
// browser and is only used to sign transactions against the configured contract.

import "./bigintPolyfill";
import type {
  Actor, Backend, ConsensusTrace, CreateEscrowInput, Escrow, Milestone, Outcome, Report, Solvency,
  Stats, ValidatorVote,
} from "./types";

import { CONTRACT_ADDRESS, RPC_URL } from "./config";

const KEY_STORAGE = "gitescrow.burner.key";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

function normalize(x: Any): Any {
  if (x instanceof Map) {
    const o: Record<string, Any> = {};
    x.forEach((v, k) => { o[String(k)] = normalize(v); });
    return o;
  }
  if (Array.isArray(x)) return x.map(normalize);
  if (x && typeof x === "object" && !(x instanceof Uint8Array)) {
    const o: Record<string, Any> = {};
    for (const [k, v] of Object.entries(x)) o[k] = normalize(v);
    return o;
  }
  return x;
}

const big = (v: Any) => BigInt(v ?? 0);
const num = (v: Any) => Number(v ?? 0);

function parseReport(raw: Any): Report | null {
  if (!raw || typeof raw !== "string") return null;
  try { return JSON.parse(raw) as Report; } catch { return null; }
}

function toMilestone(m: Any): Milestone {
  return {
    id: num(m.id), escrowId: num(m.escrow_id), index: num(m.index), title: m.title, reward: big(m.reward),
    bond: big(m.bond), expectedSha: m.expected_sha, checkName: m.check_name, appId: num(m.app_id), minTests: num(m.min_tests), minCoverageBps: num(m.min_coverage_bps),
    deadline: num(m.deadline), status: m.status, submittedSha: m.submitted_sha, deliveryRef: m.delivery_ref ?? "", checkRunId: num(m.check_run_id), attempts: num(m.attempts),
    pendingPolls: num(m.pending_polls), verifiedAt: num(m.verified_at), releaseAt: num(m.release_at),
    resubmitUntil: num(m.resubmit_until), frozenAt: num(m.frozen_at), consentMask: num(m.consent_mask),
    disputeCount: num(m.dispute_count), nextDisputeBond: big(m.next_dispute_bond), nextDisputeFee: big(m.next_dispute_fee), lastReport: parseReport(m.last_report),
  };
}

function toEscrow(e: Any): Escrow {
  return {
    id: num(e.id), employer: e.employer, contractor: e.contractor, repo: e.repo, repoId: num(e.repo_id), branch: e.branch, title: e.title,
    bondBps: num(e.bond_bps), totalReward: big(e.total_reward), totalBond: big(e.total_bond),
    openMilestones: num(e.open_milestones), createdAt: num(e.created_at), status: e.status,
    milestones: (e.milestones ?? []).map(toMilestone),
  };
}

/** Best-effort extraction of validator votes from a transaction receipt. */
function traceFromReceipt(tx: Any, report: Report): ConsensusTrace {
  const data = tx?.consensusData ?? tx?.consensus_data ?? {};
  const raw = data?.votes ?? tx?.votes ?? {};
  const entries: [string, Any][] = Array.isArray(raw) ? raw.map((v, i) => [`validator-${i + 1}`, v]) : Object.entries(raw);
  const votes: ValidatorVote[] = entries.map(([validator, v], i) => {
    const text = String(typeof v === "object" ? v?.vote ?? v?.result ?? "" : v).toLowerCase();
    const agree = text.includes("agree") && !text.includes("disagree");
    return {
      validator: validator.length > 14 ? `${validator.slice(0, 6)}…${validator.slice(-4)}` : validator,
      role: i === 0 ? "leader" : "validator",
      vote: agree ? "AGREE" : "DISAGREE",
      note: text || "vote recorded on-chain",
    };
  });
  return {
    source: "receipt",
    txHash: tx?.hash ?? tx?.txId,
    rounds: [{ round: 1, leaderClaim: report.passed ? "PASS" : "FAIL", votes, accepted: true }],
  };
}

export class LiveBackend implements Backend {
  label = "Studio Next";
  private listeners = new Set<() => void>();
  private clientP: Promise<{ client: Any; account: Any }> | null = null;
  private addr = "";

  constructor(private readonly contract: string = CONTRACT_ADDRESS) {}

  private async ctx() {
    if (!this.clientP) {
      this.clientP = (async () => {
        const sdk: Any = await import("genlayer-js");
        const { generatePrivateKey, createAccount, createClient, chains } = sdk;
        let key: string | null = null;
        try { key = localStorage.getItem(KEY_STORAGE); } catch { /* storage may be blocked */ }
        if (!key) {
          key = generatePrivateKey();
          try { localStorage.setItem(KEY_STORAGE, key as string); } catch { /* ignore */ }
        }
        const account = createAccount(key);
        const client = createClient({ chain: chains.studioDevnet, endpoint: RPC_URL, account });
        this.addr = account.address;
        return { client, account };
      })();
    }
    return this.clientP;
  }

  /** Resolve the burner address (also primes the client). */
  async init(): Promise<string> { await this.ctx(); return this.addr; }

  async importKey(privateKey: string) {
    try { localStorage.setItem(KEY_STORAGE, privateKey); } catch { /* ignore */ }
    this.clientP = null;
    await this.init();
    this.emit();
  }

  /** Studio networks expose a faucet-style RPC for test accounts. */
  async fund(amountGen = 1000): Promise<void> {
    const { client, account } = await this.ctx();
    await client.request({ method: "sim_fundAccount", params: [account.address, amountGen * 10 ** 6 * 10 ** 12] });
    this.emit();
  }

  actors(): Actor[] { return [{ id: "me", label: "Browser key", address: this.addr || "…" }]; }
  now() { return Math.floor(Date.now() / 1000); }
  subscribe(fn: () => void) { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; }
  private emit() { this.listeners.forEach((l) => l()); }

  private async read(functionName: string, args: Any[] = []): Promise<Any> {
    const { client } = await this.ctx();
    return normalize(await client.readContract({ address: this.contract, functionName, args }));
  }

  private async write(functionName: string, args: Any[], value?: bigint): Promise<Any> {
    const { client } = await this.ctx();
    const fees = await client.estimateTransactionFees({});
    const hash = await client.writeContract({ address: this.contract, functionName, args, value, fees });
    const tx = await client.waitForTransactionReceipt({ hash, waitUntil: "decided", retries: 200, interval: 3000 });
    const status = Number(tx?.status);
    const name = String(tx?.statusName ?? "");
    const ok = status === 5 || status === 7 || name === "ACCEPTED" || name === "FINALIZED";
    if (!ok) throw new Error(`Transaction ${name || status} — consensus did not accept it`);
    this.emit();
    return tx;
  }

  async listEscrows(): Promise<Escrow[]> {
    const rows = await this.read("list_escrows", [0n, 50n]);
    return (rows as Any[]).map(toEscrow).sort((a, b) => b.id - a.id);
  }

  async stats(): Promise<Stats> {
    const s = await this.read("get_stats");
    return {
      escrowCount: num(s.escrow_count), activeEscrows: num(s.active_escrows), tvl: big(s.tvl),
      lockedRewards: big(s.locked_rewards), lockedBonds: big(s.locked_bonds), totalReleased: big(s.total_released),
      totalSlashed: big(s.total_slashed), totalDisputeForfeited: big(s.total_dispute_forfeited), feesRetained: big(s.fees_retained),
    };
  }

  async solvency(): Promise<Solvency> {
    const s = await this.read("get_solvency");
    return { totalIn: big(s.total_in), totalPaidOut: big(s.total_paid_out), liabilities: big(s.liabilities), feesRetained: big(s.fees_retained), solvent: Boolean(s.solvent) };
  }

  async createEscrow(_by: string, input: CreateEscrowInput): Promise<number> {
    const specs = input.milestones.map((m) => ({
      title: m.title, reward: m.reward.toString(), expected_sha: m.expectedSha, check_name: m.checkName, app_id: m.appId, min_tests: m.minTests,
      min_coverage_bps: m.minCoverageBps, deadline: m.deadline,
    }));
    const total = input.milestones.reduce((a, m) => a + m.reward, 0n);
    const before = (await this.stats()).escrowCount;
    await this.write("create_escrow", [input.contractor, input.repo, input.branch, input.title, BigInt(input.bondBps), JSON.stringify(specs)], total);
    return before + 1;
  }

  async acceptEscrow(_by: string, escrowId: number) {
    const e = toEscrow(await this.read("get_escrow", [BigInt(escrowId)]));
    await this.write("accept_escrow", [BigInt(escrowId)], e.totalBond);
  }
  async cancelEscrow(_by: string, escrowId: number) { await this.write("cancel_escrow", [BigInt(escrowId)]); }
  async settle(_by: string, id: number) { await this.write("settle_milestone", [BigInt(id)]); }
  async approve(_by: string, id: number) { await this.write("approve_milestone", [BigInt(id)]); }
  async claimDefault(_by: string, id: number): Promise<string> {
    await this.write("claim_default", [BigInt(id)]);
    return (await this.read("get_milestone", [BigInt(id)])).status;
  }

  async cancelFaultFree(_by: string, id: number): Promise<string> {
    await this.write("cancel_fault_free", [BigInt(id)]);
    return (await this.read("get_milestone", [BigInt(id)])).status;
  }

  async thaw(_by: string, id: number): Promise<string> {
    await this.write("thaw_milestone", [BigInt(id)]);
    return (await this.read("get_milestone", [BigInt(id)])).status;
  }

  async quoteDisputeFee(milestoneId: number): Promise<bigint> {
    return big(await this.read("quote_dispute_fee", [BigInt(milestoneId)]));
  }

  async quoteDisputeBond(milestoneId: number, who: string): Promise<bigint> {
    return big(await this.read("quote_dispute_bond", [BigInt(milestoneId), who]));
  }

  private async outcome(milestoneId: number, tx: Any): Promise<Outcome> {
    const m = toMilestone(await this.read("get_milestone", [BigInt(milestoneId)]));
    const report = m.lastReport;
    if (!report) throw new Error("Contract recorded no report for this milestone");
    return { report, trace: traceFromReceipt(tx, report) };
  }

  async evaluate(_by: string, milestoneId: number, sha: string, deliveryRef = ""): Promise<Outcome> {
    const tx = await this.write("evaluate_milestone_delivery", [BigInt(milestoneId), sha, deliveryRef]);
    return this.outcome(milestoneId, tx);
  }

  async fileDispute(_by: string, milestoneId: number, reason: string): Promise<Outcome> {
    const { account } = await this.ctx();
    // The contract demands exactly: escalating refundable bond + non-refundable arbitration fee.
    const cost = (await this.quoteDisputeBond(milestoneId, account.address)) + (await this.quoteDisputeFee(milestoneId));
    const tx = await this.write("file_dispute", [BigInt(milestoneId), reason], cost);
    return this.outcome(milestoneId, tx);
  }
}
