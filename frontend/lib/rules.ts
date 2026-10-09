// Protocol constants and pure rules mirrored from contracts/git_escrow.py.
// The Python test-suite is the source of truth; rules.test.ts pins these to the same numbers.

import { ATTO } from "./format";

export const BPS = 10_000;
export const MIN_BOND_BPS = 1_000;
export const MAX_BOND_BPS = 2_000;
export const MAX_MILESTONES = 10;
export const MAX_ATTEMPTS = 5;
export const DISPUTE_WINDOW = 48 * 3600;
export const MAX_DISPUTES = 3;
export const MIN_DISPUTE_BOND = ATTO / 10n;
export const DISPUTE_BASE_BPS = 100n;
export const MAX_STRIKE_EXPONENT = 4;
export const RESUBMIT_GRACE = 72 * 3600;
export const FREEZE_GRACE = 7 * 86400;
export const MAX_PENDING_POLLS = 20;
export const DISPUTE_FEE_BPS = 300n;
export const MIN_DISPUTE_FEE = ATTO / 50n;
export const DEFAULT_APP_ID = 15368;
export const MAX_DESCRIPTION_LEN = 1000;

const REPO_URL_RE = /^https:\/\/github\.com\/[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const SHA_RE = /^[0-9a-f]{40}$/;

/** Mirrors the contract's create_escrow validation so the form fails before a wallet prompt does. */
export function validateRepositoryUrl(url: string): string | null {
  const u = url.trim();
  if (!REPO_URL_RE.test(u) || u.endsWith(".git")) return "Repository must be exactly https://github.com/<owner>/<repo>";
  return null;
}

export function validateBaseline(sha: string): string | null {
  return SHA_RE.test(sha.trim().toLowerCase()) ? null : "Baseline must be a full 40-character commit SHA";
}

export function bondFor(reward: bigint, bondBps: number): bigint {
  return (reward * BigInt(bondBps)) / BigInt(BPS);
}

/** Dispute bond: max(0.1 GEN, 1% of reward) doubled per dispute and per lost dispute. */
export function disputeBond(reward: bigint, disputeCount: number, strikes: number): bigint {
  const pct = (reward * DISPUTE_BASE_BPS) / BigInt(BPS);
  const base = pct > MIN_DISPUTE_BOND ? pct : MIN_DISPUTE_BOND;
  return base << BigInt(disputeCount + Math.min(strikes, MAX_STRIKE_EXPONENT));
}

/** Non-refundable arbitration fee: 3% of the reward, never below 0.02 GEN. */
export function disputeFee(reward: bigint): bigint {
  const pct = (reward * DISPUTE_FEE_BPS) / BigInt(BPS);
  return pct > MIN_DISPUTE_FEE ? pct : MIN_DISPUTE_FEE;
}
