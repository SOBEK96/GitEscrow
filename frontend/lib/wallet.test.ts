// Network switching and error text for injected wallets, against a mock EIP-1193 provider.
import assert from "node:assert/strict";
import test from "node:test";
import { CHAIN_ID, NATIVE_CURRENCY, RPC_URL } from "./config";
import { ATTO, parseGen } from "./format";
import { CHAIN_HEX, ensureStudioNetwork, friendlyError, type Eip1193 } from "./wallet";

function mockWallet(opts: { chain: string; known: boolean }) {
  const calls: { method: string; params?: unknown[] }[] = [];
  let chain = opts.chain;
  let known = opts.known;
  const provider: Eip1193 = {
    async request({ method, params }) {
      calls.push({ method, params });
      if (method === "eth_chainId") return chain;
      if (method === "wallet_switchEthereumChain") {
        const id = (params![0] as { chainId: string }).chainId;
        if (!known) throw Object.assign(new Error("Unrecognized chain ID"), { code: 4902 });
        chain = id;
        return null;
      }
      if (method === "wallet_addEthereumChain") {
        known = true;
        chain = (params![0] as { chainId: string }).chainId;
        return null;
      }
      throw new Error("unexpected " + method);
    },
  };
  return { provider, calls, chain: () => chain };
}

test("chain id is Studio Next (61997 = 0xf22d)", () => {
  assert.equal(CHAIN_ID, 61997);
  assert.equal(CHAIN_HEX, "0xf22d");
});

test("already on Studio Next: nothing is requested", async () => {
  const w = mockWallet({ chain: "0xf22d", known: true });
  await ensureStudioNetwork(w.provider);
  assert.deepEqual(w.calls.map((c) => c.method), ["eth_chainId"]);
});

test("known network on another chain: plain switch", async () => {
  const w = mockWallet({ chain: "0x1", known: true });
  await ensureStudioNetwork(w.provider);
  assert.deepEqual(w.calls.map((c) => c.method), ["eth_chainId", "wallet_switchEthereumChain"]);
  assert.equal(w.chain(), "0xf22d");
});

test("unknown network: adds GenLayer Studio Next (GEN, 18 decimals) then ends up on it", async () => {
  const w = mockWallet({ chain: "0x1", known: false });
  await ensureStudioNetwork(w.provider);
  const add = w.calls.find((c) => c.method === "wallet_addEthereumChain")!;
  const p = add.params![0] as Record<string, unknown>;
  assert.equal(p.chainId, "0xf22d");
  assert.equal(p.chainName, "GenLayer Studio Next");
  assert.deepEqual(p.nativeCurrency, NATIVE_CURRENCY);
  assert.equal(NATIVE_CURRENCY.symbol, "GEN");
  assert.deepEqual(p.rpcUrls, [RPC_URL]);
  assert.equal(w.chain(), "0xf22d");
});

test("user rejection is reported plainly and not retried", async () => {
  const provider: Eip1193 = {
    async request({ method }) {
      if (method === "eth_chainId") return "0x1";
      throw Object.assign(new Error("User rejected the request."), { code: 4001 });
    },
  };
  await assert.rejects(ensureStudioNetwork(provider), /rejected/);
  assert.equal(friendlyError({ code: 4001, message: "User rejected the request." }), "Request rejected in the wallet.");
});

test("faucet amounts become positive integer wei (never a float like 1e+21)", () => {
  const wei = parseGen("1000");
  assert.equal(wei, 1000n * ATTO);
  assert.equal(wei.toString(), "1000000000000000000000"); // what is sent on the wire
  assert.ok(!String(1000 * 10 ** 18).match(/^\d+$/), "the old float expression serialises as 1e+21");
  assert.equal(parseGen("0.25"), 250000000000000000n);
  for (const bad of ["0.0000000000000000001", "-1", "abc", ""]) assert.throws(() => parseGen(bad));
});
