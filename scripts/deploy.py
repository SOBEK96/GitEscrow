#!/usr/bin/env python3
"""Deploy GitEscrow to GenLayer Studio Next, seed demo escrows, export artifacts.

    python -m scripts.deploy [--no-demo]

Outputs
  deployments/studio-next.json          address, explorer link, source hash, ABI
  frontend/lib/gitescrow.generated.json address + ABI for the dashboard
  frontend/lib/config.ts                CONTRACT_ADDRESS (drives the dashboard and the footer links)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import _common as cm  # noqa: E402

DAY = 86400


def schema() -> dict:
    out = subprocess.run(["genvm-lint", "schema", "--json", str(cm.CONTRACT_PATH)], capture_output=True, text=True)
    try:
        return json.loads(out.stdout).get("schema", {})
    except json.JSONDecodeError:
        return {}


def seed_demo(c, address: str, contractor_addr: str) -> None:
    t = cm.now()
    demo = [
        ("SDK streaming transport grant", "genlayerlabs/genlayer-js", "main", 1500, [
            ("Transport layer", "Add a streaming transport with reconnect-safe framing and unit tests.", 40, 60, 8500, t + 6 * DAY),
            ("Reconnect & backoff", "Add exponential reconnect backoff with jitter and tests for dropped sockets.", 30, 40, 8000, t + 12 * DAY),
        ]),
        ("Vault invariants audit fixes", "acme/vault-audit", "release", 1000, [
            ("Fix critical findings", "Fix every critical finding from the audit report and add regression tests.", 25, 120, 9000, t + 9 * DAY),
        ]),
    ]
    for title, repo, branch, bps, miles in demo:
        try:
            baseline = cm.github_head(repo, branch)
        except Exception as exc:  # noqa: BLE001 - unauthenticated GitHub is rate limited / the demo repo may not exist
            print(f"  skip '{title}': cannot resolve {repo}@{branch} baseline ({exc})")
            continue
        specs = [{"title": n, "description": desc, "reward": str(r * cm.ATTO // 10), "expected_sha": "", "check_name": "ci/tests",
                  "app_id": 15368, "min_tests": tests, "min_coverage_bps": cov, "deadline": dl} for n, desc, r, tests, cov, dl in miles]
        total = sum(int(s["reward"]) for s in specs)
        cm.send(c, address, "create_escrow",
                [contractor_addr, f"https://github.com/{repo}", branch, title, bps, baseline, json.dumps(specs)],
                value=total, label=f"create_escrow '{title}'")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-demo", action="store_true", help="skip seeding demo escrows (they need GitHub reachable for baseline lookup)")
    args = ap.parse_args()

    code = cm.CONTRACT_PATH.read_bytes()
    employer = cm.client("employer")
    emp_addr = cm.account("employer").address
    contractor_addr = cm.account("contractor").address
    print(f"network   {cm.RPC_URL}\nemployer  {emp_addr}\ncontractor {contractor_addr}")

    cm.ensure_funds(employer, emp_addr)
    cm.ensure_funds(employer, contractor_addr)

    employer.initialize_consensus_smart_contract()
    fees = cm.retry(lambda: employer.estimate_transaction_fees())
    print("deploying contracts/git_escrow.py ...")
    tx_hash = employer.deploy_contract(code=code, args=[cm.SIGNING_CHAIN_ID], fees=fees)
    receipt = employer.wait_for_transaction_receipt(transaction_hash=tx_hash, wait_until="decided", retries=200, interval=3000)
    if not cm.is_success(receipt):
        raise SystemExit(f"Deployment failed: {receipt}")
    address = cm.contract_address_from(receipt)
    if not address:
        raise SystemExit(f"Deployment succeeded but no address found in receipt: {receipt}")
    print(f"deployed  {address}")

    if not args.no_demo:
        print("seeding demo escrows ...")
        seed_demo(employer, address, contractor_addr)
        print("stats:", cm.view(employer, address, "get_stats"))

    abi = schema()
    record = {
        "network": "studio-next",
        "chain_id": 61997,
        "rpc_url": cm.RPC_URL,
        "contract_address": address,
        "explorer_url": f"{cm.EXPLORER_URL}/address/{address}",
        "source": "contracts/git_escrow.py",
        "source_sha256": hashlib.sha256(code).hexdigest(),
        "employer": emp_addr,
        "contractor": contractor_addr,
        "abi": abi,
    }
    cm.DEPLOYMENT_FILE.parent.mkdir(exist_ok=True)
    cm.DEPLOYMENT_FILE.write_text(json.dumps(record, indent=2) + "\n")
    front = cm.ROOT / "frontend"
    (front / "lib").mkdir(parents=True, exist_ok=True)
    (front / "lib" / "gitescrow.generated.json").write_text(
        json.dumps({"address": address, "chainId": 61997, "rpcUrl": cm.RPC_URL, "abi": abi}, indent=2) + "\n")
    config = front / "lib" / "config.ts"
    text = config.read_text()
    config.write_text(re.sub(r'CONTRACT_ADDRESS = "0x[0-9a-fA-F]{40}"', f'CONTRACT_ADDRESS = "{address}"', text))
    print(f"recorded  {cm.DEPLOYMENT_FILE.relative_to(cm.ROOT)}  (frontend/lib/config.ts updated)")


if __name__ == "__main__":
    main()
