"use client";

import { Radio } from "lucide-react";
import { useBackend } from "@/lib/backend";
import { NETWORK_NAME } from "@/lib/config";
import { shortAddr } from "@/lib/format";

export function TopBar() {
  const { backend, notify, run } = useBackend();
  const me = backend.actors()[0];

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
          <span className="chip bg-ink-850 font-mono text-zinc-300 ring-white/10" title="Browser-held test key used to sign transactions">
            {shortAddr(me.address)}
          </span>
          <button className="btn-ghost !px-3 !py-1.5 text-xs"
            onClick={() => run(async () => { await backend.fund(); notify("ok", "Requested test funds from Studio"); })}>
            Fund
          </button>
        </div>
      </div>
    </header>
  );
}
