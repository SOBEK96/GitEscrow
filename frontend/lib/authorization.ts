// Wallet authorizations for the two actions the contract verifies cryptographically:
//   SUBMIT  - the contractor's delivery of a commit        (signer must be the registered contractor)
//   APPROVE - the employer's early release of a milestone  (signer must be the registered employer)
//
// The wallet signs an EIP-191 `personal_sign` message. The contract rebuilds the exact same text from its own
// state, recovers the signer (keccak256 + secp256k1 in pure Python) and compares it to the registered role, so
// the transaction can be relayed by anyone, replays die on the per-signer nonce, and a signature only ever
// authorises one contract, chain, milestone, action, commit and ref until it expires.
//
// buildAuthorizationMessage MUST stay byte-identical to `_authorization_message` in contracts/git_escrow.py;
// authorization.test.ts pins both sides to a vector produced by the Python reference signer.

import { bytesToHex, getAddress, recoverMessageAddress, stringToBytes } from "viem";
import type { Eip1193 } from "./wallet";

export const SIGNATURE_VERSION = "GitEscrow authorization v1";
export const SIGNATURE_TTL_SECONDS = 3600; // contract rejects anything valid for more than 7 days
export const MAX_SIGNATURE_TTL = 7 * 86400;

export type AuthAction = "SUBMIT" | "APPROVE";

export interface AuthorizationFields {
  action: AuthAction;
  chainId: number;
  contract: string;
  milestoneId: number;
  commitSha: string;
  deliveryRef: string;
  nonce: bigint | number;
  expiresAt: number;
}

/** The contract normalises the commit (trim + lower-case) and ref (trim) before building the message. */
export function normalizeCommit(sha: string): string { return sha.trim().toLowerCase(); }
export function normalizeRef(ref: string): string { return ref.trim(); }

export function buildAuthorizationMessage(f: AuthorizationFields): string {
  return [
    SIGNATURE_VERSION,
    `action: ${f.action}`,
    `chain: ${f.chainId}`,
    `contract: ${f.contract.toLowerCase()}`,
    `milestone: ${f.milestoneId}`,
    `commit: ${normalizeCommit(f.commitSha)}`,
    `ref: ${normalizeRef(f.deliveryRef)}`,
    `nonce: ${f.nonce.toString()}`,
    `expires: ${f.expiresAt}`,
  ].join("\n");
}

export interface RecoverableSignature {
  /** 0x + r(32) s(32) v(1) */
  signature: `0x${string}`;
}

/** Ask an injected (EIP-1193) wallet for `personal_sign`. The text is hex-encoded so no wallet reinterprets it. */
export async function requestWalletSignature(provider: Eip1193, signer: string, message: string): Promise<`0x${string}`> {
  const data = bytesToHex(stringToBytes(message));
  const raw = await provider.request({ method: "personal_sign", params: [data, signer] });
  if (typeof raw !== "string" || !/^0x[0-9a-fA-F]{130}$/.test(raw)) {
    throw new Error("The wallet returned a malformed signature.");
  }
  return raw as `0x${string}`;
}

/** Recover the signer locally and refuse to send a transaction the contract would revert. */
export async function assertSignedBy(message: string, signature: `0x${string}`, expected: string): Promise<void> {
  const recovered = await recoverMessageAddress({ message, signature });
  if (getAddress(recovered) !== getAddress(expected)) {
    throw new Error(`The wallet signed with ${getAddress(recovered)}, not the connected account ${getAddress(expected)}. Switch account and retry.`);
  }
}

export function signatureExpiry(nowSeconds: number, ttl = SIGNATURE_TTL_SECONDS): number {
  if (ttl <= 0 || ttl > MAX_SIGNATURE_TTL) throw new Error("Signature validity must be between 1 second and 7 days.");
  return Math.floor(nowSeconds) + ttl;
}
