#!/usr/bin/env python3
"""End-to-end runs against the GitEscrow deployment on GenLayer Studio Next.

Every transaction hash is printed together with its explorer link, so the output is the evidence trail.

    # Full lifecycle with adversarial probes (needs a repo whose CI publishes the `ci/tests` check-run):
    python scripts/verify_live.py lifecycle \\
        --repo-url https://github.com/OWNER/REPO --branch main \\
        --baseline BASELINE_SHA --sha DELIVERY_SHA \\
        [--old-sha SHA_BEFORE_BASELINE] [--foreign-sha SHA_FROM_ANOTHER_REPO] [--ref pull/N]

    # Failure path (no check-run exists for the public commit): rejected -> deadline -> default & slash
    python scripts/verify_live.py default [--repo octocat/Hello-World --branch master]

`lifecycle` runs

  1  employer deposits the milestone reward (create_escrow with repository_url + baseline)
  2  contractor posts the bond          (consensus binds the repo id and checks the baseline)
  3  ATTACKS, each of which must revert and leave the milestone FUNDED with 0 attempts:
       - wrong wallet: the employer signs a SUBMIT for the contractor's milestone
       - pre-baseline commit (--old-sha)        - commit of another repository (--foreign-sha)
  4  contractor submits DELIVERY_SHA with a wallet signature; validators verify it on GitHub
  5  employer signs an approval -> FINALIZED, funds paid exactly once
  6  double-payout attempts (settle again, approve again, re-submit) must all revert
  7  solvency invariant, explorer links
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as cm  # noqa: E402

# Stable, long-lived public commit used when the GitHub lookup is rate-limited (unauthenticated: 60/h per IP).
FALLBACK = {("octocat/Hello-World", "master"): "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"}
TX_LOG: list[tuple[str, str]] = []


def gen(n: int) -> str:
    return f"{n / cm.ATTO:.4f} GEN"


def head_sha(repo: str, branch: str) -> str:
    try:
        return cm.github_head(repo, branch)
    except Exception as exc:  # noqa: BLE001
        pinned = FALLBACK.get((repo, branch))
        if not pinned:
            raise SystemExit(f"Could not resolve {repo}@{branch} ({exc}); pass --sha explicitly") from exc
        print(f"      GitHub lookup failed ({exc}); using pinned commit {pinned[:7]}")
        return pinned


def log_tx(label: str) -> None:
    tx = cm.LAST_TX["hash"]
    TX_LOG.append((label, f"{cm.EXPLORER_URL}/tx/{tx}" if tx else "(see tx= line above)"))


def send(c, addr, fn, args, value=0, label=None):
    receipt = cm.send(c, addr, fn, args, value=value, label=label)
    log_tx(label or fn)
    return receipt


def send_retry(c, addr, fn, args, value=0, label=None, tries=8, wait=600):
    """Send a GitHub-dependent transaction; if the validators were rate limited (TRANSIENT 403) it is rejected
    at no cost, so wait for the unauthenticated GitHub window to reset and send again."""
    for i in range(1, tries + 1):
        try:
            return send(c, addr, fn, args, value=value, label=label)
        except RuntimeError as exc:
            if i == tries or "TRANSIENT" not in str(exc) and "403" not in str(exc):
                raise
            log_tx(f"FAILED (transient GitHub 403) {label or fn}, try {i}")
            print(f"      transient GitHub fault; waiting {wait}s then retrying ({i}/{tries})", flush=True)
            time.sleep(wait)


def must_revert(c, addr, fn, args, label):
    """Send a transaction that the contract has to reject. A decided-but-failed receipt is the evidence."""
    try:
        cm.send(c, addr, fn, args, label=f"{label} (expected to revert)")
    except RuntimeError as exc:
        log_tx(f"REVERTED: {label}")
        print(f"      reverted as required: {str(exc)[:160]}")
        return
    raise SystemExit(f"SECURITY FAILURE: '{label}' was accepted by the contract")


def milestone(c, addr, mid):
    return cm.view(c, addr, "get_milestone", [mid])


def sign(role, c, addr, action, mid, sha, ref=""):
    return cm.sign_authorization(role, c, addr, action, mid, sha, ref)


def open_escrow(employer, contractor, addr, repo_url, branch, baseline, deadline, min_tests, label):
    emp, con = cm.account("employer").address, cm.account("contractor").address
    reward = cm.ATTO  # 1 GEN
    bond = reward * 1500 // 10_000
    spec = [{"title": label, "description": "Live verification milestone: add tested code to the repository after the baseline commit.",
             "reward": str(reward), "expected_sha": "", "check_name": "ci/tests", "app_id": 15368, "min_tests": min_tests,
             "min_coverage_bps": 0, "deadline": deadline}]
    before = cm.view(employer, addr, "get_stats")["escrow_count"]
    print("[1] employer deposits the milestone reward (exact repository_url + baseline commit are stored)")
    send(employer, addr, "create_escrow", [con, repo_url, branch, label, 1500, baseline, json.dumps(spec)], value=reward)
    eid = before + 1
    escrow = cm.view(employer, addr, "get_escrow", [eid])
    assert escrow["repository_url"] == repo_url and escrow["baseline_commit_sha"] == baseline, escrow
    assert escrow["total_reward"] == reward and escrow["total_bond"] == bond, escrow
    print("[2] contractor posts the performance bond (validators bind the repo id and verify the baseline)")
    send_retry(contractor, addr, "accept_escrow", [eid], value=bond, wait=600)
    assert cm.view(employer, addr, "get_escrow", [eid])["status"] == "ACTIVE"
    return eid, escrow["milestones"][0]["id"], emp, con


def lifecycle(args) -> None:
    dep = cm.load_deployment()
    addr = dep["contract_address"]
    employer, contractor = cm.client("employer"), cm.client("contractor")
    for c, role in ((employer, "employer"), (contractor, "contractor")):
        cm.ensure_funds(c, cm.account(role).address)
    print(f"contract {addr}\nrepo     {args.repo_url}@{args.branch}\nbaseline {args.baseline}\ndelivery {args.sha}\n")

    if args.resume_milestone:
        mid = args.resume_milestone
        m = milestone(employer, addr, mid)
        assert m["status"] == "FUNDED" and m["attempts"] == 0, m
        print(f"resuming milestone {mid} (FUNDED, 0 attempts, {m['deadline'] - cm.now()}s to deadline): steps 1-3 already ran\n")
    else:
        _, mid, _, _ = open_escrow(employer, contractor, addr, args.repo_url, args.branch, args.baseline,
                                   cm.now() + args.deadline_seconds, args.min_tests, "gitescrow lifecycle")
        if not args.skip_probes:
            run_probes(employer, contractor, addr, mid, args)

    print("[4] contractor submits the delivery with a wallet signature; validators verify it on GitHub")
    for attempt in range(1, args.retries + 1):
        nonce, exp, sig = sign("contractor", contractor, addr, "SUBMIT", mid, args.sha, args.ref)
        try:
            send(contractor, addr, "evaluate_milestone_delivery", [mid, args.sha, args.ref, nonce, exp, sig])
            break
        except RuntimeError as exc:
            log_tx(f"FAILED delivery attempt {attempt} (transient GitHub fault, no attempt consumed)")
            if attempt == args.retries or "TRANSIENT" not in str(exc) + json.dumps(str(exc)) and "403" not in str(exc):
                raise
            print(f"      transient upstream fault; waiting {args.retry_wait}s (GitHub unauthenticated rate limit) and retrying")
            time.sleep(args.retry_wait)
    finish_delivery(employer, contractor, addr, mid, args, dep)


def run_probes(employer, contractor, addr, mid, args) -> None:
    print("[3] adversarial probes (each must revert and change nothing)")
    nonce, exp, sig = sign("employer", employer, addr, "SUBMIT", mid, args.sha, args.ref)
    must_revert(contractor, addr, "evaluate_milestone_delivery", [mid, args.sha, args.ref, nonce, exp, sig],
                "wrong wallet: employer's signature on the contractor's submission")
    probes = []
    if args.old_sha:
        probes.append((args.old_sha, "pre-baseline commit"))
    if args.foreign_sha:
        probes.append((args.foreign_sha, "commit of an unrelated repository"))
    for sha, label in probes:
        nonce, exp, sig = sign("contractor", contractor, addr, "SUBMIT", mid, sha)
        must_revert(contractor, addr, "evaluate_milestone_delivery", [mid, sha, "", nonce, exp, sig], label)
    m = milestone(employer, addr, mid)
    assert m["status"] == "FUNDED" and m["attempts"] == 0 and m["paid_out"] == 0, m
    print(f"      milestone still FUNDED, attempts={m['attempts']}, held={gen(m['escrowed'])}")



def finish_delivery(employer, contractor, addr, mid, args, dep) -> None:
    m = milestone(employer, addr, mid)
    report = json.loads(m["last_report"])
    print(f"      status={m['status']} passed={report['passed']} failures={report['failures']}")
    print(f"      descends_from_baseline={report.get('descends_from_baseline')} ci={report['ci_state']} "
          f"tests={report['tests_passed']} coverage_bps={report['coverage_bps']} review={report.get('review_reason')!r}")
    if not report["passed"]:
        raise SystemExit(f"validators rejected the delivery: {report['failures']}")
    assert m["status"] == "SUBMITTED"

    print("[5] employer signs an approval -> FINALIZED (single payout)")
    nonce, exp, sig = sign("employer", employer, addr, "APPROVE", mid, m["submitted_sha"], m["delivery_ref"])
    send(employer, addr, "approve_milestone", [mid, nonce, exp, sig])
    final = milestone(employer, addr, mid)
    assert final["status"] == "FINALIZED" and final["escrowed"] == 0, final
    print(f"      FINALIZED: paid_out={gen(final['paid_out'])}, still held by the contract={gen(final['escrowed'])}")
    ledger = cm.view(employer, addr, "get_solvency")

    print("[6] double-payout attempts (each must revert)")
    must_revert(contractor, addr, "settle_milestone", [mid], "settle a finalized milestone")
    nonce, exp, sig = sign("employer", employer, addr, "APPROVE", mid, m["submitted_sha"], m["delivery_ref"])
    must_revert(employer, addr, "approve_milestone", [mid, nonce, exp, sig], "approve a finalized milestone again")
    nonce, exp, sig = sign("contractor", contractor, addr, "SUBMIT", mid, args.sha, args.ref)
    must_revert(contractor, addr, "evaluate_milestone_delivery", [mid, args.sha, args.ref, nonce, exp, sig],
                "re-evaluate a finalized milestone")
    assert cm.view(employer, addr, "get_solvency") == ledger, "ledger moved after a revert"

    finish(employer, addr, dep)


def default_path(args) -> None:
    dep = cm.load_deployment()
    addr = dep["contract_address"]
    employer, contractor = cm.client("employer"), cm.client("contractor")
    for c, role in ((employer, "employer"), (contractor, "contractor")):
        cm.ensure_funds(c, cm.account(role).address)
    baseline = head_sha(args.repo, args.branch)
    print(f"contract {addr}\nrepo     {args.repo}@{args.branch}\nbaseline {baseline}\n"
          "mode     rejected delivery -> default (the baseline itself is not a delivery)\n")
    deadline = cm.now() + args.deadline_seconds
    eid, mid, _, _ = open_escrow(employer, contractor, addr, f"https://github.com/{args.repo}", args.branch, baseline,
                                 deadline, 1, "gitescrow default path")
    print("[3] contractor submits a commit that is not a descendant of the baseline: the contract refuses it")
    nonce, exp, sig = sign("contractor", contractor, addr, "SUBMIT", mid, baseline)
    must_revert(contractor, addr, "evaluate_milestone_delivery", [mid, baseline, "", nonce, exp, sig], "baseline as delivery")
    wait = deadline - cm.now() + 20
    print(f"[4] waiting {max(wait, 0)}s for the delivery deadline to pass")
    while cm.now() <= deadline + 15:
        time.sleep(10)
    print("[5] anyone claims the default: employer gets refund + slashed bond")
    send(employer, addr, "claim_default", [mid])
    final = milestone(employer, addr, mid)
    assert final["status"] == "DEFAULTED" and final["escrowed"] == 0, final
    finish(employer, addr, dep)


def finish(employer, addr, dep) -> None:
    solv = cm.view(employer, addr, "get_solvency")
    stats = cm.view(employer, addr, "get_stats")
    print(f"\nsolvency: in={gen(solv['total_in'])} out={gen(solv['total_paid_out'])} held={gen(solv['liabilities'])} "
          f"fees={gen(solv['fees_retained'])} solvent={solv['solvent']}")
    print(f"released={gen(stats['total_released'])} slashed={gen(stats['total_slashed'])}")
    assert solv["solvent"], "solvency invariant broken"
    print("\nEvidence (explorer):")
    print(f"  contract  {dep['explorer_url']}")
    for label, url in TX_LOG:
        print(f"  {label:<62} {url}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    life = sub.add_parser("lifecycle", help="deposit -> bond -> attacks -> signed delivery -> signed approval -> double-payout attempts")
    life.add_argument("--repo-url", required=True, help="exactly https://github.com/<owner>/<repo>")
    life.add_argument("--branch", default="main")
    life.add_argument("--baseline", required=True, help="full commit SHA the work started from")
    life.add_argument("--sha", required=True, help="full delivery commit SHA (descendant of the baseline, green ci/tests check-run)")
    life.add_argument("--ref", default="", help="delivery ref: empty = target branch, pull/N = an unmerged PR head")
    life.add_argument("--old-sha", default="", help="a commit from BEFORE the baseline (replay probe)")
    life.add_argument("--foreign-sha", default="", help="a commit from an unrelated repository (spoof probe)")
    life.add_argument("--min-tests", type=int, default=1)
    life.add_argument("--resume-milestone", type=int, default=0, help="skip steps 1-3 and deliver on this FUNDED milestone")
    life.add_argument("--deadline-seconds", type=int, default=3600, help="delivery window of the new milestone")
    life.add_argument("--skip-probes", action="store_true", help="skip step 3 (spend the validators' GitHub budget on the honest path)")
    life.add_argument("--retries", type=int, default=4, help="delivery attempts when GitHub rate-limits the validators")
    life.add_argument("--retry-wait", type=int, default=240)
    life.set_defaults(fn=lifecycle)
    dflt = sub.add_parser("default", help="rejected submission, then deadline default & slash")
    dflt.add_argument("--repo", default="octocat/Hello-World")
    dflt.add_argument("--branch", default="master")
    dflt.add_argument("--deadline-seconds", type=int, default=420)
    dflt.set_defaults(fn=default_path)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
