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

export function bondFor(reward: bigint, bondBps: number): bigint {
  return (reward * BigInt(bondBps)) / BigInt(BPS);
}

/** Dispute bond: max(0.1 GEN, 1% of reward) doubled per dispute and per lost dispute. */
export function disputeBond(reward: bigint, disputeCount: number, strikes: number): bigint {
  const pct = (reward * DISPUTE_BASE_BPS) / BigInt(BPS);
  const base = pct > MIN_DISPUTE_BOND ? pct : MIN_DISPUTE_BOND;
  return base << BigInt(disputeCount + Math.min(strikes, MAX_STRIKE_EXPONENT));
}
