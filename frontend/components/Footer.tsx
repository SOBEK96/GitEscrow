import { ExternalLink, Github } from "lucide-react";
import { CHAIN_ID, CONTRACT_ADDRESS, EXPLORER_URL, GITHUB_URL, NETWORK_NAME } from "@/lib/config";

export function Footer() {
  return (
    <footer className="mt-10 border-t border-white/5 bg-ink-950/80">
      <div className="mx-auto grid max-w-[1400px] gap-6 px-5 py-8 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <div className="label">Protocol status</div>
          <p className="mt-2 flex items-center gap-2 text-sm text-zinc-200">
            <span className="h-2 w-2 flex-none animate-pulseDot rounded-full bg-ok" aria-hidden />
            Autonomous Multi-Validator Consensus: Active
          </p>
        </div>
        <div>
          <div className="label">Network</div>
          <p className="mt-2 inline-flex items-center rounded-full bg-gl/15 px-3 py-1 text-xs font-medium text-gl-soft ring-1 ring-inset ring-gl/40">
            {NETWORK_NAME} (Chain ID: {CHAIN_ID})
          </p>
        </div>
        <div>
          <div className="label">Contract</div>
          <a href={EXPLORER_URL} target="_blank" rel="noopener noreferrer"
            className="mt-2 inline-flex items-center gap-1.5 font-mono text-xs text-zinc-300 transition hover:text-gl-soft">
            Contract Explorer · {CONTRACT_ADDRESS.slice(0, 8)}…{CONTRACT_ADDRESS.slice(-6)} <ExternalLink size={12} />
          </a>
        </div>
        <div>
          <div className="label">Source</div>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer"
            className="mt-2 inline-flex items-center gap-1.5 text-sm text-zinc-300 transition hover:text-gl-soft">
            <Github size={14} /> GitHub Repository
          </a>
        </div>
      </div>
      <div className="border-t border-white/5 py-4 text-center text-xs text-zinc-600">GitEscrow Protocol • MIT License</div>
    </footer>
  );
}
