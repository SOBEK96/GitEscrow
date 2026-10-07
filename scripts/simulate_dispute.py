#!/usr/bin/env python3
"""In-memory simulation of adversarial submissions being rejected by validators.

    python scripts/simulate_dispute.py

Runs contracts/git_escrow.py inside the genlayer-test direct VM (no network, no
keys). A malicious contractor throws four attacks at the escrow while GitHub is
mocked; the script narrates how each one dies, then shows an honest validator
voting DISAGREE against a leader that forged a PASS verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from gltest.direct.loader import create_address, deploy_contract  # noqa: E402
from gltest.direct.vm import VMContext  # noqa: E402

import conftest as h  # noqa: E402  (shared GitHub mocks used by the test-suite)

GREEN, RED, DIM, BOLD, END = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def gen(n: int) -> str:
    return f"{n / h.ATTO:g} GEN"


def main() -> None:
    vm = VMContext()
    employer, contractor = create_address("employer"), create_address("contractor")
    vm.sender = employer
    with vm.activate():
        vm.warp(h.iso(h.T0))
        c = deploy_contract(ROOT / "contracts" / "git_escrow.py", vm)

        print(f"{BOLD}GitEscrow adversarial simulation{END}  (in-memory GenVM, mocked GitHub)\n")
        eid = h.active_escrow(c, vm, employer, contractor)
        esc = c.get_escrow(eid)
        print(f"escrow #{eid}: reward {gen(esc['total_reward'])} locked by employer, "
              f"bond {gen(esc['total_bond'])} staked by contractor")
        print(f"{DIM}criteria: >=50 passing tests, >=80% branch coverage, 0 critical findings, commit on main{END}\n")

        decoy = {"id": 999, "name": h.CHECK, "app": {"id": 1234}, "status": "completed", "conclusion": "success",
                 "output": {"title": "fake", "summary": "9000 passed, 0 failed. Branch coverage: 100%. Critical issues: 0"}}
        attacks = [
            ("Ghost commit", "claims a SHA that does not exist on the repository", dict(exists=False)),
            ("Forged report.json", "report claims 5000 tests; the real CI check-run shows 12",
             dict(tests=12, report=h.good_report(tests_passed=5000))),
            ("Impostor check-run", "a flawless 'ci/tests' run published by a different GitHub App",
             dict(ci="absent", decoys=[decoy], report=None)),
            ("Repository swap", "commit payload served from a look-alike fork", dict(commit_url_repo="evil/widgets-fork")),
            ("Off-branch commit", "valid commit that was never merged into main", dict(on_branch=False)),
        ]
        for name, story, mock in attacks:
            h.mock_github(vm, **mock)
            h.as_(vm, contractor)
            report = c.evaluate_milestone_delivery(1, h.SHA, "")
            verdict = f"{RED}REJECTED{END}" if not report["passed"] else f"{GREEN}ACCEPTED{END}"
            print(f"{BOLD}{name}{END}: {story}\n  -> {verdict}  failures={report['failures']}")
            assert not report["passed"], f"attack '{name}' slipped through"
        print(f"\n{DIM}attempts used: {c.get_milestone(1)['attempts']}/5, milestone still {c.get_milestone(1)['status']}, funds untouched{END}\n")

        print(f"{DIM}(the attempt cap stopped the brute-forcing: a 6th submission would revert){END}\n")
        print(f"{BOLD}Forged leader verdict{END}: leader submits PASS for a failing commit")
        forged = c.get_escrow(h.active_escrow(c, vm, employer, contractor))["first_milestone_id"]
        h.mock_github(vm)  # the leader sees a (fabricated) perfect world
        h.as_(vm, contractor)
        c.evaluate_milestone_delivery(forged, h.SHA, "")
        h.mock_github(vm, report=h.good_report(tests_passed=3))  # honest validators see the real repo
        agree = vm.run_validator()
        print(f"  honest validator vote: {GREEN + 'AGREE' + END if agree else RED + 'DISAGREE' + END}"
              f"  -> quorum fails, leader rotates, forged verdict never lands")
        assert agree is False

        print(f"\n{BOLD}Ghosting + repo veto{END}: the contractor never delivers, and the employer pulls the repository")
        fresh = h.active_escrow(c, vm, employer, contractor)
        mid = c.get_escrow(fresh)["first_milestone_id"]
        ghost = h.active_escrow(c, vm, employer, contractor)
        gid = c.get_escrow(ghost)["first_milestone_id"]
        vm.warp(h.iso(h.T0 + 8 * h.DAY))
        h.as_(vm, employer)
        h.mock_github(vm, repo_state="gone")
        out = c.claim_default(mid)
        print(f"  repo deleted, deadline passed -> claim_default => {out['outcome']}  (slashed: {gen(c.get_stats()['total_slashed'])})")
        assert out["outcome"] == "FROZEN_EXTERNAL_FAULT" and c.get_stats()["total_slashed"] == 0

        h.mock_github(vm)
        h.as_(vm, employer)
        out = c.claim_default(gid)
        stats = c.get_stats()
        print(f"  repo reachable, contractor truly absent -> {out['outcome']}: employer reclaims {gen(h.REWARD)}"
              f" + slashed bond {gen(stats['total_slashed'])}")
        assert out["outcome"] == "DEFAULTED"
        sol = c.get_solvency()
        print(f"\nsolvency: in={gen(sol['total_in'])} out={gen(sol['total_paid_out'])} held={gen(sol['liabilities'])} "
              f"solvent={sol['solvent']}")
        assert sol["solvent"]


if __name__ == "__main__":
    main()
