"""Shared helpers for the GitEscrow direct-mode suite.

Uses the genlayer-test pytest plugin fixtures: direct_vm, direct_deploy,
direct_alice (employer), direct_bob (contractor), direct_charlie (bystander).
GitHub is mocked endpoint by endpoint, so every failure mode of the
verification pipeline can be reproduced deterministically.
"""

import json
from datetime import datetime, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

CONTRACT = "contracts/git_escrow.py"
ATTO = 10**18
REPO = "acme/widgets"
REPO_URL = f"https://github.com/{REPO}"
BASELINE = "c" * 40  # the commit the escrow was created at; every delivery must descend from it
SIGNING_CHAIN_ID = 61997
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


# Deterministic wallets: the contract recovers real secp256k1 signatures, so the test actors need keys.
KEYS = {
    "alice": "0x" + "11" * 32,
    "bob": "0x" + "22" * 32,
    "charlie": "0x" + "33" * 32,
    "mallory": "0x" + "44" * 32,
}
ACCOUNTS = {name: Account.from_key(key) for name, key in KEYS.items()}
ADDR = {name: bytes.fromhex(acct.address[2:]) for name, acct in ACCOUNTS.items()}
KEY_OF = {ADDR[name]: KEYS[name] for name in KEYS}


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
        "description": "Implement the streaming parser in src/parser.py with unit tests covering malformed input.",
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
    eid = c.create_escrow(hx(contractor), f"https://github.com/{repo}", branch, "Widget grant", bond_bps, BASELINE,
                          json.dumps(specs))
    vm.value = 0
    return int(eid)


def accept(c, vm, contractor, eid, repo_state="ok"):
    esc = c.get_escrow(eid)
    vm.clear_mocks()
    mock_repo(vm, esc["repo"], REPO_ID, repo_state)
    if repo_state == "ok":
        mock_baseline(vm)
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


def default_files() -> list:
    return [
        {"filename": "src/parser.py", "status": "added", "patch": "@@ -0,0 +1,3 @@\n+def parse(stream):\n+    return list(stream)\n"},
        {"filename": "tests/test_parser.py", "status": "added",
         "patch": "@@ -0,0 +1,3 @@\n+def test_parse():\n+    assert parse([1]) == [1]\n"},
    ]


def mock_baseline(vm, baseline=BASELINE, branch=BRANCH, repo=REPO, repo_id=REPO_ID, compare="ahead", exists=True):
    """Mocks used when the contractor accepts: the baseline commit must exist and sit under the branch."""
    api = rf"https://api\.github\.com/repositories/{repo_id}"
    if exists:
        vm.mock_web(rf"{api}/commits/{baseline}$", {"status": 200, "body": json.dumps({
            "sha": baseline,
            "url": f"https://api.github.com/repos/{repo}/commits/{baseline}",
            "html_url": f"https://github.com/{repo}/commit/{baseline}"})})
    else:
        vm.mock_web(rf"{api}/commits/{baseline}$", {"status": 404, "body": json.dumps({"message": "Not Found"})})
    vm.mock_web(rf"{api}/compare/{baseline}\.\.\.{branch}$", {"status": 200, "body": json.dumps(
        {"status": compare, "ahead_by": 7, "behind_by": 0, "files": []})})


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
    baseline_status="ahead",
    baseline_behind=0,
    baseline_http=200,
    ci_head_sha=None,
    files=None,
    llm: "str | None" = "ok",
):
    """Mock every GitHub endpoint the verifier reads.

    The attested check-run (name + app id + head commit) carries the authentic metrics.
    baseline_status: what GitHub's compare API says about BASELINE...sha ("ahead" = a genuine descendant).
    baseline_http: 404 reproduces "no common ancestor" (an unrelated history).
    ci_head_sha: the commit the attested check-run claims to have executed against (default: sha).
    files: the changed-file listing of that comparison (default: one source file and one new test file).
    llm: "ok" | "implements_false" | "provenance_false" | "malformed" | None (no reviewer mock registered).
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
    if files is None:
        files = default_files()
    vm.mock_web(rf"{api}/compare/{BASELINE}\.\.\.{sha}$", {"status": baseline_http, "body": json.dumps(
        {"status": baseline_status, "ahead_by": 3, "behind_by": baseline_behind, "files": files}
        if baseline_http == 200 else {"message": "No common ancestor between the commits"})})
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

    runs = [dict(d, head_sha=d.get("head_sha", sha)) for d in decoys]
    if ci != "absent":
        text = check_text if check_text is not None else check_text_for(tests, failed, coverage, critical)
        runs.append({
            "id": run_id, "name": check_name, "app": {"id": app_id}, "head_sha": ci_head_sha or sha,
            "status": "in_progress" if ci == "pending" else "completed",
            "conclusion": None if ci == "pending" else ("failure" if ci == "failure" else "success"),
            "output": {"title": "CI", "summary": text, "text": ""},
        })
    vm.mock_web(rf"{api}/commits/{sha}/check-runs", {"status": 200, "body": json.dumps({"check_runs": runs})})

    if llm is not None:
        verdicts = {
            "ok": {"implements_milestone": True, "ci_provenance_ok": True, "reason": "genuine parser work"},
            "implements_false": {"implements_milestone": False, "ci_provenance_ok": True, "reason": "unrelated change"},
            "provenance_false": {"implements_milestone": True, "ci_provenance_ok": False, "reason": "tests look neutered"},
        }
        # The text prefix keeps the harness from auto-parsing the reply: the pinned SDK returns exec_prompt
        # text as a string, which the contract then extracts the JSON object from (as it must for real models).
        vm.mock_llm(r"MILESTONE DESCRIPTION",
                    "not json at all" if llm == "malformed" else "Verdict: " + json.dumps(verdicts[llm]))

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


# --- wallet signatures --------------------------------------------------------
def now_ts(vm) -> int:
    return int(datetime.fromisoformat(vm._datetime).timestamp())


def contract_hex(vm) -> str:
    return "0x" + bytes(vm._contract_address).hex()


def auth_message(vm, action, mid, sha, ref, nonce, expires, chain=SIGNING_CHAIN_ID, contract=None) -> str:
    """Byte-for-byte the text lib/authorization.ts builds in the browser."""
    return "\n".join([
        "GitEscrow authorization v1",
        f"action: {action}",
        f"chain: {chain}",
        f"contract: {(contract or contract_hex(vm)).lower()}",
        f"milestone: {mid}",
        f"commit: {sha}",
        f"ref: {ref}",
        f"nonce: {nonce}",
        f"expires: {expires}",
    ])


def personal_sign(key: str, message: str) -> str:
    """What an injected wallet's personal_sign returns: 0x r||s||v."""
    return "0x" + Account.sign_message(encode_defunct(text=message), key).signature.hex()


def next_nonce(c, key: str) -> int:
    return int(c.get_nonce(ACCOUNTS_BY_KEY[key].address))


ACCOUNTS_BY_KEY = {KEYS[name]: ACCOUNTS[name] for name in KEYS}


def signed_args(c, vm, signer, action, mid, sha="", ref="", nonce=None, expires=None, chain=SIGNING_CHAIN_ID,
                contract=None, message_sha=None, message_ref=None):
    """(nonce, expires, signature) for an action, signed by `signer` (raw address bytes or a key).

    message_sha / message_ref let a test sign one thing and submit another."""
    key = signer if isinstance(signer, str) else KEY_OF[bytes(signer)]
    nonce = next_nonce(c, key) if nonce is None else nonce
    expires = now_ts(vm) + 3600 if expires is None else expires
    text = auth_message(vm, action, mid, sha if message_sha is None else message_sha,
                        ref if message_ref is None else message_ref, nonce, expires, chain, contract)
    return nonce, expires, personal_sign(key, text)


def submit(c, vm, mid, sha, ref="", signer=None, **over):
    """evaluate_milestone_delivery signed by `signer` (default: whoever vm.sender currently is)."""
    signer = signer if signer is not None else vm.sender
    # The contract signs over the normalised commit and ref, so the wallet is shown exactly those.
    nonce, expires, sig = signed_args(c, vm, signer, "SUBMIT", mid, sha.strip().lower(), ref.strip(), **over)
    return c.evaluate_milestone_delivery(mid, sha, ref, nonce, expires, sig)


def approve(c, vm, mid, signer=None, **over):
    """approve_milestone signed by `signer` (default: vm.sender); binds the currently submitted commit."""
    signer = signer if signer is not None else vm.sender
    m = c.get_milestone(mid)
    nonce, expires, sig = signed_args(c, vm, signer, "APPROVE", mid, m["submitted_sha"], m["delivery_ref"], **over)
    return c.approve_milestone(mid, nonce, expires, sig)
