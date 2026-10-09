#!/usr/bin/env python3
"""In-memory simulation of adversarial submissions being rejected by validators.

    python scripts/simulate_dispute.py

Runs contracts/git_escrow.py inside the genlayer-test direct VM (no network, no
keys). A malicious contractor throws the exploits a reviewer would try at the escrow
while GitHub is mocked: replaying a pre-baseline commit, a commit from another
repository, rigged CI, forged wallet signatures, and a double payout. The script
narrates how each one dies, then shows an honest validator voting DISAGREE against
a leader that forged a PASS verdict.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from gltest.direct.loader import deploy_contract  # noqa: E402
from gltest.direct.vm import VMContext  # noqa: E402

import conftest as h  # noqa: E402  (shared GitHub mocks and wallet helpers used by the test-suite)

GREEN, RED, DIM, BOLD, END = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def gen(n: int) -> str:
    return f"{n / h.ATTO:g} GEN"


def reason(exc: Exception) -> str:
    return str(exc).replace("UserError", "").strip("()'\" ")


def attempt(c, vm, contractor, **kwargs) -> str:
    """Submit as the contractor; describe how the contract answered."""
    h.as_(vm, contractor)
    try:
        report = h.submit(c, vm, 1, h.SHA, "", **kwargs)
    except Exception as exc:  # noqa: BLE001 - direct mode raises the contract's UserError
        return f"{RED}REVERTED{END}  {reason(exc)}"
    verdict = f"{GREEN}ACCEPTED{END}" if report["passed"] else f"{RED}REJECTED{END}"
    return f"{verdict}  failures={report['failures']}"


def main() -> None:
    vm = VMContext()
    employer, contractor = h.ADDR["alice"], h.ADDR["bob"]
    vm.sender = employer
    with vm.activate():
        vm.warp(h.iso(h.T0))
        c = deploy_contract(ROOT / "contracts" / "git_escrow.py", vm, h.SIGNING_CHAIN_ID)

        print(f"{BOLD}GitEscrow adversarial simulation{END}  (in-memory GenVM, mocked GitHub)\n")
        eid = h.active_escrow(c, vm, employer, contractor)
        esc = c.get_escrow(eid)
        print(f"escrow #{eid}: reward {gen(esc['total_reward'])} locked by employer, "
              f"bond {gen(esc['total_bond'])} staked by contractor")
        print(f"{DIM}repository {esc['repository_url']}, baseline {esc['baseline_commit_sha'][:7]}; "
              f">=50 passing tests, >=80% branch coverage, 0 critical findings, commit on main{END}\n")

        decoy = {"id": 999, "name": h.CHECK, "app": {"id": 1234}, "status": "completed", "conclusion": "success",
                 "output": {"title": "fake", "summary": "9000 passed, 0 failed. Branch coverage: 100%. Critical issues: 0"}}
        workflow = {"filename": ".github/workflows/ci.yml", "status": "modified",
                    "patch": "@@ -4,1 +4,1 @@\n-      - run: pytest\n+      - run: pytest || true\n"}
        rigged = {"filename": "tests/test_parser.py", "status": "modified",
                  "patch": "@@ -1,2 +1,2 @@\n-    assert parse(x) == y\n+    assert True\n"}
        attacks = [
            ("Ghost commit", "claims a SHA that does not exist on the repository", dict(exists=False)),
            ("Pre-baseline replay", "a green commit that predates the escrow's baseline", dict(baseline_status="behind")),
            ("Unrelated repository", "commit payload served from a look-alike fork", dict(commit_url_repo="evil/widgets-fork")),
            ("Rigged workflow", "edits .github/workflows/ci.yml so CI always goes green",
             dict(files=h.default_files() + [workflow])),
            ("Always-true test", "rewrites an assertion to `assert True`", dict(files=h.default_files() + [rigged])),
            ("Forged report.json", "report claims 5000 tests; the real CI check-run shows 12",
             dict(tests=12, report=h.good_report(tests_passed=5000))),
            ("Stolen check-run", "a flawless run that executed against a different commit", dict(ci_head_sha=h.OTHER_SHA, report=None)),
            ("Impostor check-run", "a flawless 'ci/tests' run published by a different GitHub App",
             dict(ci="absent", decoys=[decoy], report=None)),
            ("Off-branch commit", "valid commit that was never merged into main", dict(on_branch=False)),
            ("Hollow diff", "green CI, but the reviewer finds the diff unrelated to the milestone", dict(llm="implements_false")),
        ]
        for name, story, mock in attacks:
            h.mock_github(vm, **mock)
            print(f"{BOLD}{name}{END}: {story}\n  -> {attempt(c, vm, contractor)}")
            assert c.get_milestone(1)["status"] == "FUNDED" and c.get_milestone(1)["paid_out"] == 0, name

        print(f"\n{BOLD}Wallet forgeries{END}")
        h.mock_github(vm)
        for name, signer, extra in (
            ("stranger's signature", h.KEYS["mallory"], {}),
            ("employer signing for the contractor", h.KEYS["alice"], {}),
            ("signature for another commit", h.KEYS["bob"], dict(message_sha=h.OTHER_SHA)),
            ("expired signature", h.KEYS["bob"], dict(expires=h.now_ts(vm) - 1)),
        ):
            print(f"  {name}: {attempt(c, vm, contractor, signer=signer, **extra)}")
        print(f"\n{DIM}attempts used: {c.get_milestone(1)['attempts']}/5, milestone still {c.get_milestone(1)['status']}, funds untouched{END}\n")

        print(f"{BOLD}Double payout{END}: deliver honestly, finalize, then try to be paid again")
        h.mock_github(vm)
        h.as_(vm, contractor)
        assert h.submit(c, vm, 1, h.SHA, "")["passed"]
        h.as_(vm, employer)
        h.approve(c, vm, 1)
        paid = c.get_solvency()["total_paid_out"]
        for label, call in (("settle again", lambda: c.settle_milestone(1)),
                            ("approve again", lambda: h.approve(c, vm, 1)),
                            ("re-evaluate", lambda: (h.as_(vm, contractor), h.submit(c, vm, 1, h.SHA, "")))):
            try:
                call()
            except Exception as exc:  # noqa: BLE001
                print(f"  {label}: {RED}REVERTED{END}  {reason(exc)}")
            else:
                raise AssertionError(f"{label} paid twice")
        assert c.get_solvency()["total_paid_out"] == paid and c.get_milestone(1)["escrowed"] == 0
        print(f"  {GREEN}paid exactly once{END}: {gen(paid)}, milestone holds {gen(c.get_milestone(1)['escrowed'])}\n")

        print(f"{BOLD}Forged leader verdict{END}: leader submits PASS for a failing commit")
        forged = c.get_escrow(h.active_escrow(c, vm, employer, contractor))["first_milestone_id"]
        h.mock_github(vm)  # the leader sees a (fabricated) perfect world
        h.as_(vm, contractor)
        h.submit(c, vm, forged, h.SHA, "")
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
