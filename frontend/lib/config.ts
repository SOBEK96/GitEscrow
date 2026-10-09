// Single source of truth for the live deployment this dashboard talks to.
export const CONTRACT_ADDRESS = "0x2F0186CF5CF4276409b4b4eF4b6525Ce474B096D";
export const CHAIN_ID = 61997;
export const NETWORK_NAME = "GenLayer Studio Next";
export const RPC_URL = process.env.NEXT_PUBLIC_GENLAYER_RPC_URL ?? "https://studio-next.genlayer.com/api";
export const EXPLORER_URL = `https://explorer-studio-next.genlayer.com/address/${CONTRACT_ADDRESS}`;
export const GITHUB_URL = "https://github.com/SOBEK96/GitEscrow";
export const EXPLORER_BASE = "https://explorer-studio-next.genlayer.com";
export const NATIVE_CURRENCY = { name: "GEN Token", symbol: "GEN", decimals: 18 } as const;
