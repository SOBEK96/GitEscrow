#!/usr/bin/env python3
"""End-to-end run against the GitEscrow deployment on GenLayer Studio Next.

    python scripts/verify_live.py                       # default: rejected delivery -> default & slash
    python scripts/verify_live.py --repo OWNER/REPO --sha FULL_SHA [--branch main] [--ref pull/N]

Both paths exercise real validator consensus over live GitHub data:

  deposit -> contractor bond -> commit submission -> GenVM multi-validator
  GitHub verification -> settlement

* With --repo/--sha pointing at a commit whose repository publishes a `ci/tests`
  check-run from the GitHub Actions app (id 15368) with a summary such as
  "120 passed, 0 failed. Branch coverage: 91%. Critical issues: 0", the delivery
  is VERIFIED and the employer releases the milestone.
* Without them a real public commit that has no such check-run is submitted. The
  quorum rejects it (`ci_attestation_missing`), the contractor misses the
  deadline, and the default path slashes the bond to the employer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as cm  # noqa: E402


# Stable, long-lived public commit used when the GitHub lookup is rate-limited (unauthenticated: 60/h per IP).
FALLBACK = {("octocat/Hello-World", "master"): "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"}


def head_sha(repo: str, branch: str) -> str:
    try:
        return _fetch_head(repo, branch)
    except Exception as exc:  # noqa: BLE001
        pinned = FALLBACK.get((repo, branch))
        if not pinned:
            raise SystemExit(f"Could not resolve {repo}@{branch} ({exc}); pass --sha explicitly") from exc
        print(f"      GitHub lookup failed ({exc}); using pinned commit {pinned[:7]}")
        return pinned


def _fetch_head(repo: str, branch: str) -> str:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{branch}",
                                 headers={"User-Agent": "GitEscrow-verify", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["sha"]


def gen(n: int) -> str:
    return f"{n / cm.ATTO:.4f} GEN"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="octocat/Hello-World")
    ap.add_argument("--branch", default="master")
    ap.add_argument("--sha", default="")
    ap.add_argument("--ref", default="", help="delivery ref: empty = target branch, pull/N = an unmerged PR head, or a branch")
    ap.add_argument("--deadline-seconds", type=int, default=420, help="delivery window for the default path")
    args = ap.parse_args()

    dep = cm.load_deployment()
    addr = dep["contract_address"]
    employer, contractor = cm.client("employer"), cm.client("contractor")
    emp, con = cm.account("employer").address, cm.account("contractor").address
    for c, a in ((employer, emp), (contractor, con)):
        cm.ensure_funds(c, a)

    sha = args.sha or head_sha(args.repo, args.branch)
    expect_pass = bool(args.sha)
    reward = cm.ATTO  # 1 GEN
    bond = reward * 1500 // 10_000
    print(f"contract {addr}\nrepo     {args.repo}@{args.branch}\ncommit   {sha}\nmode     "
          f"{'verified delivery' if expect_pass else 'rejected delivery -> default'}\n")

    print("[1/5] employer deposits the milestone reward")
    deadline = cm.now() + (3600 if expect_pass else args.deadline_seconds)
    spec = [{"title": "Live verification milestone", "reward": str(reward), "expected_sha": "", "check_name": "ci/tests", "app_id": 15368, "min_tests": 1,
             "min_coverage_bps": 0, "deadline": deadline}]
    before = cm.view(employer, addr, "get_stats")["escrow_count"]
    cm.send(employer, addr, "create_escrow", [con, args.repo, args.branch, "verify_live", 1500, json.dumps(spec)], value=reward)
    eid = before + 1
    escrow = cm.view(employer, addr, "get_escrow", [eid])
    mid = escrow["milestones"][0]["id"]
    assert escrow["total_reward"] == reward and escrow["total_bond"] == bond, escrow

    print("[2/5] contractor posts the performance bond")
    cm.send(contractor, addr, "accept_escrow", [eid], value=bond)
    assert cm.view(employer, addr, "get_escrow", [eid])["status"] == "ACTIVE"
    print(f"      locked: {cm.view(employer, addr, 'get_stats')['tvl'] / cm.ATTO:.2f} GEN TVL (all escrows)")

    print("[3/5] contractor submits the commit; validators verify it on GitHub")
    cm.send(contractor, addr, "evaluate_milestone_delivery", [mid, sha, args.ref])
    m = cm.view(employer, addr, "get_milestone", [mid])
    report = json.loads(m["last_report"])
    print(f"      status={m['status']} passed={report['passed']} failures={report['failures']}")
    print(f"      commit_exists={report['commit_exists']} on_ref={report['on_ref']} ci={report['ci_state']} "
          f"tests={report['tests_passed']} coverage_bps={report['coverage_bps']} critical={report['critical_findings']}")

    if report["passed"]:
        assert m["status"] == "VERIFIED"
        print("[4/5] employer waives the 48h dispute window and approves")
        cm.send(employer, addr, "approve_milestone", [mid])
        final = cm.view(employer, addr, "get_milestone", [mid])
        assert final["status"] == "RELEASED", final
        print("[5/5] RELEASED: reward + returned bond paid to the contractor")
    else:
        assert not expect_pass, f"expected a verified delivery but validators rejected it: {report['failures']}"
        assert m["status"] == "PENDING"
        wait = deadline - cm.now() + 20
        print(f"[4/5] waiting {max(wait, 0)}s for the delivery deadline to pass")
        while cm.now() <= deadline + 15:
            time.sleep(10)
        print("[5/5] anyone claims the default: employer gets refund + slashed bond")
        cm.send(employer, addr, "claim_default", [mid])
        final = cm.view(employer, addr, "get_milestone", [mid])
        assert final["status"] == "DEFAULTED", final

    solv = cm.view(employer, addr, "get_solvency")
    stats = cm.view(employer, addr, "get_stats")
    print(f"\nsolvency: in={gen(solv['total_in'])} out={gen(solv['total_paid_out'])} held={gen(solv['liabilities'])} "
          f"solvent={solv['solvent']}")
    print(f"released={gen(stats['total_released'])} slashed={gen(stats['total_slashed'])}")
    assert solv["solvent"], "solvency invariant broken"
    print(f"\nOK  {dep['explorer_url']}")


if __name__ == "__main__":
    main()
