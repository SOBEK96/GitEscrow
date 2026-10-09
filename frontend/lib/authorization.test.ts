// Wallet authorization: message layout, injected-wallet request, and signer recovery - pinned to the
// Python reference signer (eth_account) that the contract tests use.
import assert from "node:assert/strict";
import test from "node:test";
import { privateKeyToAccount } from "viem/accounts";
import { CHAIN_ID, CONTRACT_ADDRESS } from "./config";
import {
  MAX_SIGNATURE_TTL, SIGNATURE_TTL_SECONDS, assertSignedBy, buildAuthorizationMessage, requestWalletSignature,
  signatureExpiry, type AuthorizationFields,
} from "./authorization";
import type { Eip1193 } from "./wallet";

const KEY = `0x${"22".repeat(32)}` as const; // the "bob" test wallet in tests/conftest.py
const BOB = "0x1563915e194D8CfBA1943570603F7606A3115508";
const SHA = "a".repeat(40);

// Produced by tests/conftest.personal_sign(KEYS["bob"], message) - the exact bytes the contract recovers.
const VECTOR_CONTRACT = "0x1a0fb6315dc4289ad24d79608650a9a360748eea";
const VECTOR_SIGNATURE =
  "0xfc3ddd8fe96cb26e698b7860bfbfe31a2ab71068a23f56fef610af124a7c9a884f1ba10b6ca9870c339e3361614761d1fe031839db2040ed4aa3862d159a6d471c";
const VECTOR_MESSAGE = [
  "GitEscrow authorization v1", "action: SUBMIT", "chain: 61997", `contract: ${VECTOR_CONTRACT}`, "milestone: 1",
  `commit: ${SHA}`, "ref: ", "nonce: 0", "expires: 1800003600",
].join("\n");

const fields: AuthorizationFields = {
  action: "SUBMIT", chainId: 61997, contract: VECTOR_CONTRACT, milestoneId: 1, commitSha: SHA, deliveryRef: "", nonce: 0n, expiresAt: 1800003600,
};

test("message layout matches the contract's _authorization_message byte for byte", () => {
  assert.equal(buildAuthorizationMessage(fields), VECTOR_MESSAGE);
});

test("commit and ref are normalised exactly like the contract does (trim, lower-case commit)", () => {
  const sloppy = { ...fields, commitSha: `  ${SHA.toUpperCase()} `, deliveryRef: " pull/7 " };
  assert.equal(buildAuthorizationMessage(sloppy), buildAuthorizationMessage({ ...fields, commitSha: SHA, deliveryRef: "pull/7" }));
  assert.match(buildAuthorizationMessage(sloppy), /\nref: pull\/7\n/);
});

test("contract address is lower-cased even when given in EIP-55 form", () => {
  const msg = buildAuthorizationMessage({ ...fields, contract: "0x1A0Fb6315dc4289Ad24D79608650a9A360748EEA" });
  assert.equal(msg, VECTOR_MESSAGE);
});

test("the Python-produced signature recovers to the registered wallet", async () => {
  await assertSignedBy(VECTOR_MESSAGE, VECTOR_SIGNATURE, BOB);
});

test("a signature by another account is refused before any transaction is sent", async () => {
  await assert.rejects(assertSignedBy(VECTOR_MESSAGE, VECTOR_SIGNATURE, "0x000000000000000000000000000000000000dEaD"), /signed with/);
  await assert.rejects(assertSignedBy(VECTOR_MESSAGE.replace("milestone: 1", "milestone: 2"), VECTOR_SIGNATURE, BOB), /signed with/);
});

test("viem signs the same message identically (burner-key path)", async () => {
  const account = privateKeyToAccount(KEY);
  assert.equal(account.address, BOB);
  assert.equal(await account.signMessage({ message: VECTOR_MESSAGE }), VECTOR_SIGNATURE);
});

test("injected wallet: personal_sign gets the hex-encoded text and the connected account", async () => {
  const account = privateKeyToAccount(KEY);
  const calls: { method: string; params?: unknown[] }[] = [];
  const provider: Eip1193 = {
    async request({ method, params }) {
      calls.push({ method, params });
      assert.equal(method, "personal_sign");
      const [data, signer] = params as [string, string];
      assert.equal(signer, BOB);
      const text = Buffer.from(data.slice(2), "hex").toString("utf8");
      return account.signMessage({ message: text });
    },
  };
  const sig = await requestWalletSignature(provider, BOB, VECTOR_MESSAGE);
  assert.equal(sig, VECTOR_SIGNATURE);
  assert.equal(calls.length, 1);
  assert.equal((calls[0].params![0] as string).startsWith("0x"), true);
});

test("a wallet that answers with garbage is rejected", async () => {
  const provider: Eip1193 = { async request() { return "0xdeadbeef"; } };
  await assert.rejects(requestWalletSignature(provider, BOB, VECTOR_MESSAGE), /malformed signature/);
});

test("a rejected signature prompt surfaces as the wallet's own error", async () => {
  const provider: Eip1193 = { async request() { throw Object.assign(new Error("User rejected the request."), { code: 4001 }); } };
  await assert.rejects(requestWalletSignature(provider, BOB, VECTOR_MESSAGE), /rejected/);
});

test("expiry stays inside the contract's 7 day ceiling", () => {
  assert.equal(signatureExpiry(1000), 1000 + SIGNATURE_TTL_SECONDS);
  assert.ok(SIGNATURE_TTL_SECONDS < MAX_SIGNATURE_TTL);
  assert.throws(() => signatureExpiry(1000, MAX_SIGNATURE_TTL + 1));
  assert.throws(() => signatureExpiry(1000, 0));
});

test("deployment constants feed the signing domain", () => {
  const msg = buildAuthorizationMessage({ ...fields, chainId: CHAIN_ID, contract: CONTRACT_ADDRESS });
  assert.ok(msg.includes(`chain: ${CHAIN_ID}`) && msg.includes(`contract: ${CONTRACT_ADDRESS.toLowerCase()}`));
});
