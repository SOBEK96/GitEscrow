"""Shared helpers for the GitEscrow direct-mode suite.

Uses the genlayer-test pytest plugin fixtures: direct_vm, direct_deploy,
direct_alice (employer), direct_bob (contractor), direct_charlie (bystander).
GitHub is mocked endpoint by endpoint, so every failure mode of the
verification pipeline can be reproduced deterministically.
"""

import json
from datetime import datetime, timezone

import pytest

CONTRACT = "contracts/git_escrow.py"
ATTO = 10**18
REPO = "acme/widgets"
BRANCH = "main"
SHA = "a" * 40
OTHER_SHA = "b" * 40
T0 = 1_800_000_000  # fixed block clock used by every test
HOUR = 3600
DAY = 24 * HOUR
REWARD = 100 * ATTO
BOND_BPS = 1500
BOND = REWARD * BOND_BPS // 10_000


def hx(addr) -> str:
    """0x-hex of a raw test address (the direct harness hands out bytes)."""
    return "0x" + bytes(addr).hex()


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _clock(direct_vm):
    direct_vm.warp(iso(T0))


def milestone_spec(**over) -> dict:
    spec = {
        "title": "Ship the parser",
        "reward": REWARD,
        "expected_sha": "",
        "min_tests": 50,
        "min_coverage_bps": 8000,
        "deadline": T0 + 7 * DAY,
    }
    spec.update(over)
    return spec


def fund(vm, who, amount=10_000 * ATTO):
    vm.deal(who, amount)


def as_(vm, who, value=0):
    vm.sender = who
    vm.value = value


def create(c, vm, employer, contractor, specs=None, bond_bps=BOND_BPS, repo=REPO, branch=BRANCH):
    specs = specs if specs is not None else [milestone_spec()]
    total = sum(int(s["reward"]) for s in specs)
    fund(vm, employer)
    as_(vm, employer, total)
    eid = c.create_escrow(hx(contractor), repo, branch, "Widget grant", bond_bps, json.dumps(specs))
    vm.value = 0
    return int(eid)


def accept(c, vm, contractor, eid):
    esc = c.get_escrow(eid)
    fund(vm, contractor)
    as_(vm, contractor, esc["total_bond"])
    c.accept_escrow(eid)
    vm.value = 0


def active_escrow(c, vm, employer, contractor, specs=None, bond_bps=BOND_BPS):
    eid = create(c, vm, employer, contractor, specs, bond_bps)
    accept(c, vm, contractor, eid)
    return eid


# --- GitHub mocks -----------------------------------------------------------
def mock_github(
    vm,
    sha=SHA,
    repo=REPO,
    branch=BRANCH,
    exists=True,
    on_branch=True,
    ci="success",
    report="default",
    check_text=None,
    commit_url_repo=None,
):
    """Mock the four GitHub endpoints the verifier reads.

    report: "default" -> a passing report, dict -> that payload, None -> 404.
    """
    vm.clear_mocks()
    api = rf"https://api\.github\.com/repos/{repo}"
    if exists:
        shown = commit_url_repo or repo
        body = {
            "sha": sha,
            "url": f"https://api.github.com/repos/{shown}/commits/{sha}",
            "html_url": f"https://github.com/{shown}/commit/{sha}",
        }
        vm.mock_web(rf"{api}/commits/{sha}$", {"status": 200, "body": json.dumps(body)})
        vm.mock_web(
            rf"{api}/compare/",
            {"status": 200, "body": json.dumps({"status": "behind" if on_branch else "diverged"})},
        )
        if ci == "none":
            runs = []
        else:
            run = {
                "status": "in_progress" if ci == "pending" else "completed",
                "conclusion": None if ci == "pending" else ("failure" if ci == "failure" else "success"),
                "output": {"title": "CI", "summary": check_text or "", "text": ""},
            }
            runs = [run]
        vm.mock_web(rf"{api}/commits/{sha}/check-runs", {"status": 200, "body": json.dumps({"check_runs": runs})})
        if report == "default":
            report = good_report(sha, repo)
        if report is None:
            vm.mock_web(r"raw\.githubusercontent\.com", {"status": 404, "body": "404: Not Found"})
        else:
            vm.mock_web(r"raw\.githubusercontent\.com", {"status": 200, "body": json.dumps(report)})
    else:
        vm.mock_web(rf"{api}/commits/", {"status": 404, "body": json.dumps({"message": "Not Found"})})


def good_report(sha=SHA, repo=REPO, **over):
    rep = {
        "commit": sha,
        "repository": repo,
        "tests_passed": 120,
        "tests_failed": 0,
        "branch_coverage": 91.5,
        "critical_findings": 0,
    }
    rep.update(over)
    return rep
