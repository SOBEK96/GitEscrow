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
REPO_ID = 424242
CHECK = "ci/tests"
APP_ID = 15368  # GitHub Actions
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
        "check_name": CHECK,
        "app_id": APP_ID,
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


def accept(c, vm, contractor, eid, repo_state="ok"):
    esc = c.get_escrow(eid)
    vm.clear_mocks()
    mock_repo(vm, esc["repo"], REPO_ID, repo_state)
    fund(vm, contractor)
    as_(vm, contractor, esc["total_bond"])
    c.accept_escrow(eid)
    vm.value = 0


def active_escrow(c, vm, employer, contractor, specs=None, bond_bps=BOND_BPS):
    eid = create(c, vm, employer, contractor, specs, bond_bps)
    accept(c, vm, contractor, eid)
    return eid


# --- GitHub mocks -----------------------------------------------------------
def repo_payload(full_name=REPO, repo_id=REPO_ID, private=False) -> dict:
    return {"id": repo_id, "full_name": full_name, "private": private}


def mock_repo(vm, repo=REPO, repo_id=REPO_ID, state="ok", full_name=None, redirect=False):
    """Mock the repository-identity endpoints (by name, used at accept; by id, used afterwards).

    state: "ok" | "gone" (404) | "private" (200 + private flag) | "error" (503).
    redirect: the by-name endpoint answers 301 -> /repositories/{id} (a renamed repository).
    """
    by_id = rf"https://api\.github\.com/repositories/{repo_id}$"
    by_name = rf"https://api\.github\.com/repos/{repo}$"
    name = full_name or repo
    if state == "gone":
        vm.mock_web(by_id, {"status": 404, "body": json.dumps({"message": "Not Found"})})
        vm.mock_web(by_name, {"status": 404, "body": json.dumps({"message": "Not Found"})})
        return
    if state == "error":
        vm.mock_web(by_id, {"status": 503, "body": "unavailable"})
        vm.mock_web(by_name, {"status": 503, "body": "unavailable"})
        return
    body = json.dumps(repo_payload(name, repo_id, private=(state == "private")))
    vm.mock_web(by_id, {"status": 200, "body": body})
    if redirect:
        vm.mock_web(by_name, {"method": "GET", "response": {
            "status": 301, "headers": {"location": f"https://api.github.com/repositories/{repo_id}".encode()}, "body": b""}})
    else:
        vm.mock_web(by_name, {"status": 200, "body": body})


def check_text_for(tests, failed, coverage, critical) -> str:
    return f"{tests} passed, {failed} failed. Branch coverage: {coverage}%. Critical issues: {critical}"


def mock_github(
    vm,
    sha=SHA,
    repo=REPO,
    repo_id=REPO_ID,
    full_name=None,
    branch=BRANCH,
    repo_state="ok",
    exists=True,
    on_branch=True,
    ci="success",
    tests=120,
    failed=0,
    coverage=91.5,
    critical=0,
    check_text=None,
    check_name=CHECK,
    app_id=APP_ID,
    decoys=(),
    report: object = "match",
    commit_url_repo=None,
    run_id=101,
    compare_status=None,
    compare_http=200,
    pr=None,
    pr_base=BRANCH,
    pr_head=None,
):
    """Mock every GitHub endpoint the verifier reads.

    The attested check-run (name + app id) carries the authentic metrics.
    report: "match" -> a report agreeing with the check-run, dict -> that payload, None -> absent.
    decoys: extra check-run dicts (e.g. a great-looking run from the wrong app).
    """
    vm.clear_mocks()
    name = full_name or repo
    mock_repo(vm, repo, repo_id, repo_state, full_name)
    if repo_state != "ok":
        return
    api = rf"https://api\.github\.com/repositories/{repo_id}"
    if not exists:
        vm.mock_web(rf"{api}/commits/", {"status": 404, "body": json.dumps({"message": "Not Found"})})
        return
    shown = commit_url_repo or name
    body = {
        "sha": sha,
        "url": f"https://api.github.com/repos/{shown}/commits/{sha}",
        "html_url": f"https://github.com/{shown}/commit/{sha}",
    }
    vm.mock_web(rf"{api}/commits/{sha}$", {"status": 200, "body": json.dumps(body)})
    cmp_status = compare_status or ("behind" if on_branch else "diverged")
    vm.mock_web(rf"{api}/compare/", {"status": compare_http, "body": json.dumps({"status": cmp_status})})
    if pr is not None:
        vm.mock_web(rf"{api}/pulls/{pr}$", {"status": 200, "body": json.dumps(
            {"head": {"sha": pr_head or sha}, "base": {"ref": pr_base}, "state": "open"})})

    runs = list(decoys)
    if ci != "absent":
        text = check_text if check_text is not None else check_text_for(tests, failed, coverage, critical)
        runs.append({
            "id": run_id, "name": check_name, "app": {"id": app_id},
            "status": "in_progress" if ci == "pending" else "completed",
            "conclusion": None if ci == "pending" else ("failure" if ci == "failure" else "success"),
            "output": {"title": "CI", "summary": text, "text": ""},
        })
    vm.mock_web(rf"{api}/commits/{sha}/check-runs", {"status": 200, "body": json.dumps({"check_runs": runs})})

    if report == "match":
        report = good_report(sha, name, tests_passed=tests, tests_failed=failed, branch_coverage=coverage,
                             critical_findings=critical)
    if report is None:
        vm.mock_web(r"raw\.githubusercontent\.com", {"status": 404, "body": "404: Not Found"})
    else:
        vm.mock_web(r"raw\.githubusercontent\.com", {"status": 200, "body": json.dumps(report)})


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
