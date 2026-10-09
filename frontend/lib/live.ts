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

import { getAddress } from "viem";
import { CHAIN_ID, CONTRACT_ADDRESS, RPC_URL } from "./config";
import {
  assertSignedBy, buildAuthorizationMessage, normalizeCommit, normalizeRef, requestWalletSignature, signatureExpiry,
  type AuthAction,
} from "./authorization";
import { parseGen } from "./format";
import { ensureStudioNetwork, getInjected } from "./wallet";

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
    id: num(m.id), escrowId: num(m.escrow_id), index: num(m.index), title: m.title, description: m.description ?? "", reward: big(m.reward),
    bond: big(m.bond), escrowed: big(m.escrowed), paidOut: big(m.paid_out), finalizedAt: num(m.finalized_at), expectedSha: m.expected_sha, checkName: m.check_name, appId: num(m.app_id), minTests: num(m.min_tests), minCoverageBps: num(m.min_coverage_bps),
    deadline: num(m.deadline), status: m.status, submittedSha: m.submitted_sha, deliveryRef: m.delivery_ref ?? "", checkRunId: num(m.check_run_id), attempts: num(m.attempts),
    pendingPolls: num(m.pending_polls), verifiedAt: num(m.verified_at), releaseAt: num(m.release_at),
    resubmitUntil: num(m.resubmit_until), frozenAt: num(m.frozen_at), consentMask: num(m.consent_mask),
    disputeCount: num(m.dispute_count), nextDisputeBond: big(m.next_dispute_bond), nextDisputeFee: big(m.next_dispute_fee), lastReport: parseReport(m.last_report),
  };
}

function toEscrow(e: Any): Escrow {
  return {
    id: num(e.id), employer: e.employer, contractor: e.contractor, repo: e.repo, repositoryUrl: e.repository_url ?? "", baselineCommitSha: e.baseline_commit_sha ?? "", repoId: num(e.repo_id), branch: e.branch, title: e.title,
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

/** Best-effort extraction of the contract's revert text from a decided receipt. */
function revertReason(tx: Any): string {
  const seen = new Set<unknown>();
  const found: string[] = [];
  const walk = (node: Any, depth: number) => {
    if (!node || typeof node !== "object" || seen.has(node) || depth > 8) return;
    seen.add(node);
    for (const [k, v] of Object.entries(node)) {
      if (typeof v === "string" && /^(stderr|message|error|raw_error|error_description)$/i.test(k) && /\[(EXPECTED|EXTERNAL|TRANSIENT)\]|UserError|Error/.test(v)) found.push(v);
      else walk(v, depth + 1);
    }
  };
  walk(tx, 0);
  const text = found.find((f) => /\[(EXPECTED|EXTERNAL|TRANSIENT)\]/.test(f)) ?? found[0] ?? "";
  return text.replace(/\s+/g, " ").slice(0, 240);
}

/** Studio answers -32029 when a client exceeds 30 requests/minute; wait the advertised time and retry. */
async function withBackoff<T>(fn: () => Promise<T>, attempts = 4): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return await fn();
    } catch (e) {
      const err = e as Any;
      const cause = err?.cause ?? err;
      const limited = cause?.code === -32029 || /rate limit exceeded/i.test(String(cause?.message ?? err?.message ?? ""));
      if (!limited || i + 1 >= attempts) throw e;
      const wait = Number(cause?.data?.retry_after_seconds ?? 10);
      await new Promise((r) => setTimeout(r, (Math.min(wait, 30) + 1) * 1000));
    }
  }
}

export type WalletKind = "injected" | "burner";
export interface Identity { kind: WalletKind; address: string }

const MODE_STORAGE = "gitescrow.wallet";

function store(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* storage may be blocked */ }
}
function load(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

export class LiveBackend implements Backend {
  label = "Studio Next";
  private listeners = new Set<() => void>();
  private identity: Identity | null = null;
  private readers = new Map<string, Promise<Any>>();
  private restored: Promise<void> | null = null;
  private providerBound = false;
  private burnerKey: string | null = null; // held in memory so signing never depends on localStorage being readable

  constructor(private readonly contract: string = CONTRACT_ADDRESS) {}

  // ------------------------------------------------------------ identity
  /** Silent session restore: re-attach an already-authorised wallet (no popup) or the demo key. */
  init(): Promise<void> {
    if (!this.restored) {
      this.restored = (async () => {
        const mode = load(MODE_STORAGE);
        const injected = getInjected();
        if (mode === "injected" && injected) {
          try {
            const accounts = (await injected.request({ method: "eth_accounts" })) as string[];
            if (accounts?.[0]) this.identity = { kind: "injected", address: getAddress(accounts[0]) };
          } catch { /* wallet locked or unavailable: stay disconnected */ }
        } else if (mode === "burner") {
          await this.useDemoKey(false);
        }
        this.bindProviderEvents();
      })();
    }
    return this.restored;
  }

  hasExtension(): boolean { return getInjected() !== null; }
  getIdentity(): Identity | null { return this.identity; }

  private bindProviderEvents() {
    const provider = getInjected();
    if (!provider?.on || this.providerBound) return;
    this.providerBound = true;
    provider.on("accountsChanged", (accounts) => {
      const list = accounts as string[];
      if (this.identity?.kind !== "injected") return;
      this.identity = list?.[0] ? { kind: "injected", address: getAddress(list[0]) } : null;
      if (!this.identity) store(MODE_STORAGE, null);
      this.emit();
    });
    provider.on("chainChanged", () => this.emit());
  }

  /** Real wallet: prompt for accounts, then add / switch to Studio Next. */
  async connectWallet(): Promise<string> {
    const provider = getInjected();
    if (!provider) throw new Error("No browser wallet found. Install MetaMask, or use the demo key.");
    const accounts = (await provider.request({ method: "eth_requestAccounts" })) as string[];
    if (!accounts?.[0]) throw new Error("The wallet returned no account.");
    await ensureStudioNetwork(provider);
    this.identity = { kind: "injected", address: getAddress(accounts[0]) };
    store(MODE_STORAGE, "injected");
    this.bindProviderEvents();
    this.emit();
    return this.identity.address;
  }

  /** Optional quick-test fallback: a throwaway key generated and kept in this browser only. */
  async useDemoKey(announce = true): Promise<string> {
    const sdk: Any = await import("genlayer-js");
    let key = this.burnerKey ?? load(KEY_STORAGE);
    if (!key) {
      key = sdk.generatePrivateKey() as string;
      store(KEY_STORAGE, key);
    }
    this.burnerKey = key;
    this.identity = { kind: "burner", address: getAddress(sdk.createAccount(key).address) };
    store(MODE_STORAGE, "burner");
    if (announce) this.emit();
    return this.identity.address;
  }

  disconnect() {
    this.identity = null;
    store(MODE_STORAGE, null);
    this.emit();
  }

  /** Chain id the injected wallet is currently on (null for the demo key / no wallet). */
  async walletChainId(): Promise<number | null> {
    const provider = getInjected();
    if (this.identity?.kind !== "injected" || !provider) return null;
    try { return parseInt(String(await provider.request({ method: "eth_chainId" })), 16); } catch { return null; }
  }

  async switchNetwork() {
    const provider = getInjected();
    if (!provider) throw new Error("No browser wallet found.");
    await ensureStudioNetwork(provider);
    this.emit();
  }

  private async client(signer: boolean): Promise<Any> {
    const id = signer ? this.identity : null;
    if (signer && !id) throw new Error("Connect a wallet first.");
    const cacheKey = id ? `${id.kind}:${id.address}` : "read";
    let cached = this.readers.get(cacheKey);
    if (!cached) {
      cached = (async () => {
        const sdk: Any = await import("genlayer-js");
        const { createAccount, createClient, chains } = sdk;
        let account: Any;
        if (id?.kind === "burner") account = createAccount(this.burnerKey);
        else if (id?.kind === "injected") account = id.address; // address-only: genlayer-js signs through window.ethereum
        return createClient({ chain: chains.studioDevnet, endpoint: RPC_URL, ...(account ? { account } : {}) });
      })();
      this.readers.set(cacheKey, cached);
    }
    return cached;
  }

  /**
   * Credit the connected address from the Studio faucet.
   *
   * `sim_fundAccount` insists on a positive INTEGER amount in wei. Passing a JS number such as
   * 1000 * 10**18 serialises as `1e+21` and is rejected ("amount must be a positive integer"),
   * so the amount is parsed to a BigInt and sent as a decimal string. The faucet also keys
   * balances by exact address casing, and wallets hand out lowercase addresses, so the
   * address is EIP-55 checksummed first (otherwise the funds land on a different key).
   */
  async fund(amountGen: string | number = "1000"): Promise<bigint> {
    if (!this.identity) throw new Error("Connect a wallet first.");
    const wei = parseGen(String(amountGen));
    if (wei <= 0n) throw new Error("Amount must be greater than zero.");
    const address = getAddress(this.identity.address);
    const res = await fetch(RPC_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method: "sim_fundAccount", params: [address, wei.toString()] }),
    });
    const body = (await res.json()) as { error?: { message?: string; data?: { retry_after_seconds?: number } }; result?: string };
    if (body.error) {
      const retry = body.error.data?.retry_after_seconds;
      throw new Error(`${body.error.message ?? "Faucet request failed"}${retry ? ` - try again in ${retry}s` : ""}`);
    }
    this.emit();
    return wei;
  }

  async balance(): Promise<bigint> {
    if (!this.identity) return 0n;
    const res = await fetch(RPC_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_getBalance", params: [getAddress(this.identity.address), "latest"] }),
    });
    const body = (await res.json()) as { result?: string };
    return body.result ? BigInt(body.result) : 0n;
  }

  actors(): Actor[] {
    const id = this.identity;
    return [{ id: "me", label: id?.kind === "burner" ? "Demo key" : "Wallet", address: id?.address ?? "" }];
  }
  now() { return Math.floor(Date.now() / 1000); }
  subscribe(fn: () => void) { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; }
  private emit() { this.listeners.forEach((l) => l()); }

  /**
   * Obtain the wallet signature the contract verifies: read the signer's next nonce, build the exact
   * authorization text, have the injected wallet `personal_sign` it (or the demo key sign it), and check
   * locally that it recovers to the connected account before any transaction is sent.
   * Returns [nonce, expiresAt, signature] for the contract call.
   */
  private async authorize(action: AuthAction, milestoneId: number, commitSha: string, deliveryRef: string): Promise<[bigint, number, `0x${string}`]> {
    if (!this.identity) throw new Error("Connect a wallet first.");
    const signer = getAddress(this.identity.address);
    const nonce = big(await this.read("get_nonce", [signer]));
    const expiresAt = signatureExpiry(this.now());
    const message = buildAuthorizationMessage({
      action, chainId: CHAIN_ID, contract: this.contract, milestoneId, commitSha, deliveryRef, nonce, expiresAt,
    });
    let signature: `0x${string}`;
    if (this.identity.kind === "injected") {
      const provider = getInjected();
      if (!provider) throw new Error("The browser wallet is no longer available.");
      await ensureStudioNetwork(provider);
      signature = await requestWalletSignature(provider, signer, message);
    } else {
      const sdk: Any = await import("genlayer-js");
      const key = this.burnerKey ?? load(KEY_STORAGE);
      if (!key) throw new Error("The demo key is missing; reconnect.");
      signature = (await sdk.createAccount(key).signMessage({ message })) as `0x${string}`;
    }
    await assertSignedBy(message, signature, signer);
    return [nonce, expiresAt, signature];
  }

  private async read(functionName: string, args: Any[] = []): Promise<Any> {
    const client = await this.client(false);
    return normalize(await withBackoff(() => client.readContract({ address: this.contract, functionName, args })));
  }

  private async write(functionName: string, args: Any[], value?: bigint): Promise<Any> {
    const client = await this.client(true);
    if (this.identity?.kind === "injected") {
      const provider = getInjected();
      if (!provider) throw new Error("The browser wallet is no longer available.");
      await ensureStudioNetwork(provider);
    }
    if (value !== undefined && value < 0n) throw new Error("Transaction value must not be negative.");
    const fees = await withBackoff(() => client.estimateTransactionFees({}));
    const hash = await withBackoff(() => client.writeContract({ address: this.contract, functionName, args, value, fees }));
    const tx: Any = await withBackoff(() => client.waitForTransactionReceipt({ hash, waitUntil: "decided", retries: 120, interval: 5000 }));
    const status = Number(tx?.status);
    const name = String(tx?.statusName ?? "");
    const accepted = status === 5 || status === 7 || name === "ACCEPTED" || name === "FINALIZED";
    const execution = String(tx?.txExecutionResultName ?? "FINISHED_WITH_RETURN");
    if (!accepted || execution !== "FINISHED_WITH_RETURN") {
      throw Object.assign(new Error(`Transaction ${name || status} (${execution}) - ${revertReason(tx) || "consensus did not accept it"}`), { receipt: tx });
    }
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
      title: m.title, description: m.description, reward: m.reward.toString(), expected_sha: m.expectedSha, check_name: m.checkName, app_id: m.appId, min_tests: m.minTests,
      min_coverage_bps: m.minCoverageBps, deadline: m.deadline,
    }));
    const total = input.milestones.reduce((a, m) => a + m.reward, 0n);
    const before = (await this.stats()).escrowCount;
    await this.write("create_escrow", [
      input.contractor, input.repositoryUrl.trim(), input.branch, input.title, BigInt(input.bondBps),
      input.baselineCommitSha.trim().toLowerCase(), JSON.stringify(specs),
    ], total);
    return before + 1;
  }

  async acceptEscrow(_by: string, escrowId: number) {
    const e = toEscrow(await this.read("get_escrow", [BigInt(escrowId)]));
    await this.write("accept_escrow", [BigInt(escrowId)], e.totalBond);
  }
  async cancelEscrow(_by: string, escrowId: number) { await this.write("cancel_escrow", [BigInt(escrowId)]); }
  async settle(_by: string, id: number) { await this.write("settle_milestone", [BigInt(id)]); }
  /** Employer's early release. The wallet signs an APPROVE authorization for the commit that was verified. */
  async approve(_by: string, id: number) {
    const m = toMilestone(await this.read("get_milestone", [BigInt(id)]));
    const [nonce, expiresAt, signature] = await this.authorize("APPROVE", id, m.submittedSha, m.deliveryRef);
    await this.write("approve_milestone", [BigInt(id), nonce, BigInt(expiresAt), signature]);
  }
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

  /** Contractor's delivery. The wallet signs a SUBMIT authorization the contract recovers and checks against the contractor. */
  async evaluate(_by: string, milestoneId: number, sha: string, deliveryRef = ""): Promise<Outcome> {
    const commit = normalizeCommit(sha);
    const ref = normalizeRef(deliveryRef);
    const [nonce, expiresAt, signature] = await this.authorize("SUBMIT", milestoneId, commit, ref);
    const tx = await this.write("evaluate_milestone_delivery", [BigInt(milestoneId), commit, ref, nonce, BigInt(expiresAt), signature]);
    return this.outcome(milestoneId, tx);
  }

  async fileDispute(_by: string, milestoneId: number, reason: string): Promise<Outcome> {
    if (!this.identity) throw new Error("Connect a wallet first.");
    // The contract demands exactly: escalating refundable bond + non-refundable arbitration fee.
    const cost = (await this.quoteDisputeBond(milestoneId, this.identity.address)) + (await this.quoteDisputeFee(milestoneId));
    const tx = await this.write("file_dispute", [BigInt(milestoneId), reason], cost);
    return this.outcome(milestoneId, tx);
  }
}
