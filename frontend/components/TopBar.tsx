"use client";

import { AlertTriangle, FlaskConical, LogOut, Radio, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { useBackend } from "@/lib/backend";
import { CHAIN_ID, NETWORK_NAME } from "@/lib/config";
import { fmtGen, shortAddr } from "@/lib/format";

export function TopBar() {
  const { backend, snap, notify, run } = useBackend();
  const identity = backend.getIdentity();
  const [hasExtension, setHasExtension] = useState(false); // read after mount: window.ethereum does not exist during SSR
  const [chainId, setChainId] = useState<number | null>(null);
  const [balance, setBalance] = useState<bigint | null>(null);

  useEffect(() => { setHasExtension(backend.hasExtension()); }, [backend]);

  // Re-read network + balance whenever the identity, the chain data or the wallet changes.
  useEffect(() => {
    let live = true;
    backend.walletChainId().then((c) => live && setChainId(c));
    backend.balance().then((b) => live && setBalance(identity ? b : null)).catch(() => live && setBalance(null));
    return () => { live = false; };
  }, [backend, identity?.address, identity?.kind, snap.escrows]);

  const wrongNetwork = identity?.kind === "injected" && chainId !== null && chainId !== CHAIN_ID;

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-ink-950/80 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3">
        <div className="flex items-center gap-3">
          <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden>
            <rect width="32" height="32" rx="8" fill="#11111a" />
            <path d="M9 11l7-4 7 4v10l-7 4-7-4z" fill="none" stroke="#8b5cf6" strokeWidth="2.4" strokeLinejoin="round" />
            <circle cx="16" cy="16" r="2.6" fill="#fb923c" />
          </svg>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight text-zinc-50">GitEscrow</div>
            <div className="text-[11px] text-zinc-500">Code-verified milestone escrow · GenLayer</div>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          <span className="chip bg-gl/15 text-gl-soft ring-gl/40"><Radio size={12} /> {NETWORK_NAME}</span>

          {!identity ? (
            <>
              {hasExtension ? (
                <button className="btn-primary !px-4 !py-1.5 text-xs" onClick={() => run(async () => { await backend.connectWallet(); })}>
                  <Wallet size={14} /> Connect Wallet
                </button>
              ) : (
                <>
                  <a className="btn-ghost !px-3 !py-1.5 text-xs" href="https://metamask.io/download/" target="_blank" rel="noopener noreferrer">
                    <Wallet size={14} /> Install MetaMask
                  </a>
                  <button className="btn-primary !px-3 !py-1.5 text-xs" title="Throwaway key kept in this browser only"
                    onClick={() => run(async () => { await backend.useDemoKey(); notify("ok", "Demo key created. Click Fund to get test GEN."); })}>
                    <FlaskConical size={14} /> Demo / Quick Test
                  </button>
                </>
              )}
            </>
          ) : (
            <>
              {wrongNetwork && (
                <button className="btn-danger !px-3 !py-1.5 text-xs" onClick={() => run(async () => { await backend.switchNetwork(); })}>
                  <AlertTriangle size={13} /> Switch to Studio Next ({CHAIN_ID})
                </button>
              )}
              <span className="chip bg-ink-850 font-mono text-zinc-300 ring-white/10"
                title={identity.kind === "burner" ? "Throwaway demo key stored in this browser" : identity.address}>
                {identity.kind === "burner" ? <FlaskConical size={12} /> : <Wallet size={12} />}
                {identity.kind === "burner" ? "Demo · " : ""}{shortAddr(identity.address)}
                {balance !== null && <span className="text-zinc-500">· {fmtGen(balance)} GEN</span>}
              </span>
              <button className="btn-ghost !px-3 !py-1.5 text-xs"
                onClick={() => run(async () => { const wei = await backend.fund("1000"); notify("ok", `Faucet credited ${fmtGen(wei)} GEN`); })}>
                Fund
              </button>
              <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => backend.disconnect()} aria-label="Disconnect wallet">
                <LogOut size={13} /> Disconnect
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
