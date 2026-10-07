// EIP-1193 helpers: injected-wallet detection, GenLayer Studio Next network switching, error text.

import { CHAIN_ID, EXPLORER_BASE, NATIVE_CURRENCY, NETWORK_NAME, RPC_URL } from "./config";

export interface Eip1193 {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
  on?(event: string, handler: (...args: unknown[]) => void): void;
  removeListener?(event: string, handler: (...args: unknown[]) => void): void;
}

export const CHAIN_HEX = `0x${CHAIN_ID.toString(16)}`;

export function getInjected(): Eip1193 | null {
  if (typeof window === "undefined") return null;
  const eth = (window as unknown as { ethereum?: Eip1193 }).ethereum;
  return eth && typeof eth.request === "function" ? eth : null;
}

interface RpcError { code?: number; message?: string }

/** Switch the wallet to Studio Next, adding the network first if the wallet has never seen it. */
export async function ensureStudioNetwork(provider: Eip1193): Promise<void> {
  const current = String(await provider.request({ method: "eth_chainId" })).toLowerCase();
  if (current === CHAIN_HEX) return;
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: CHAIN_HEX }] });
  } catch (e) {
    const err = e as RpcError;
    const unknown = err.code === 4902 || err.code === -32603 || /unrecognized chain|not been added|unknown chain/i.test(err.message ?? "");
    if (!unknown) throw e;
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [{
        chainId: CHAIN_HEX,
        chainName: NETWORK_NAME,
        nativeCurrency: NATIVE_CURRENCY,
        rpcUrls: [RPC_URL],
        blockExplorerUrls: [EXPLORER_BASE],
      }],
    });
    const after = String(await provider.request({ method: "eth_chainId" })).toLowerCase();
    if (after !== CHAIN_HEX) {
      await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: CHAIN_HEX }] });
    }
  }
}

/** Human-readable text for wallet / RPC / viem failures. */
export function friendlyError(e: unknown): string {
  const err = e as RpcError & { shortMessage?: string; details?: string };
  if (err?.code === 4001 || /user (rejected|denied)/i.test(err?.message ?? "")) return "Request rejected in the wallet.";
  if (err?.code === -32002) return "A wallet request is already pending - open your wallet to continue.";
  const raw = err?.shortMessage ?? err?.details ?? err?.message ?? String(e);
  return raw.replace(/^.*?\[EXPECTED\]\s*/, "").replace(/\s+/g, " ").slice(0, 280);
}
