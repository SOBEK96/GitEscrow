// Single source of truth for the live deployment this dashboard talks to.
export const CONTRACT_ADDRESS = "0x1A0Fb6315dc4289Ad24D79608650a9A360748EEA";
export const CHAIN_ID = 61997;
export const NETWORK_NAME = "GenLayer Studio Next";
export const RPC_URL = process.env.NEXT_PUBLIC_GENLAYER_RPC_URL ?? "https://studio-next.genlayer.com/api";
export const EXPLORER_URL = `https://explorer-studio-next.genlayer.com/address/${CONTRACT_ADDRESS}`;
export const GITHUB_URL = "https://github.com/SOBEK96/GitEscrow";
