"""GitEscrow direct-mode test suite.

Run: pytest            (from the repository root)
Direct mode runs the leader; validator agreement is exercised explicitly with
direct_vm.run_validator against swapped mocks. GitHub is mocked endpoint by
endpoint (see conftest.mock_github).
"""

import json

import pytest

from conftest import (
    ATTO, BOND, BOND_BPS, CHECK, CONTRACT, DAY, HOUR, OTHER_SHA, REPO, REPO_ID, REWARD, SHA, T0,
    accept, active_escrow, as_, create, fund, good_report, hx, iso, milestone_spec, mock_github, mock_repo,
)

FEE = max(ATTO // 50, REWARD * 300 // 10_000)  # 3% of 100 GEN
DISPUTE_BOND_1 = REWARD * 100 // 10_000  # 1% of 100 GEN


@pytest.fixture
def world(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    c = direct_deploy(CONTRACT)
    return c, direct_vm, direct_alice, direct_bob, direct_charlie


def deliver(c, vm, contractor, mid=1, sha=SHA):
    as_(vm, contractor)
    return c.evaluate_milestone_delivery(mid, sha)


def dispute(c, vm, employer, mid=1, reason="evidence changed", bond=None):
    cost = (bond if bond is not None else c.quote_dispute_bond(mid, hx(employer))) + c.quote_dispute_fee(mid)
    as_(vm, employer, cost)
    out = c.file_dispute(mid, reason)
    vm.value = 0
    return out


def verified(world, **mock):
    c, vm, alice, bob, _ = world
    active_escrow(c, vm, alice, bob)
    mock_github(vm, **mock)
    report = deliver(c, vm, bob)
    assert report["passed"] is True
    return report


def solvent(c):
    s = c.get_solvency()
    assert s["solvent"], s
    return s


# ============================================================ deposit & staking
class TestDepositAndStaking:
    def test_deposit_math_and_bond_lockup(self, world):
        c, vm, alice, bob, _ = world
        specs = [milestone_spec(reward=100 * ATTO), milestone_spec(reward=40 * ATTO, title="Docs")]
        eid = create(c, vm, alice, bob, specs)
        esc = c.get_escrow(eid)
        assert esc["status"] == "OPEN"
        assert esc["total_reward"] == 140 * ATTO
        assert esc["total_bond"] == 140 * ATTO * BOND_BPS // 10_000
        assert [m["bond"] for m in esc["milestones"]] == [15 * ATTO, 6 * ATTO]
        assert c.get_stats()["locked_bonds"] == 0  # bond not yet posted
        solvent(c)

        accept(c, vm, bob, eid)
        stats = c.get_stats()
        esc = c.get_escrow(eid)
        assert esc["status"] == "ACTIVE" and esc["repo_id"] == REPO_ID  # repository bound by numeric id
        assert stats["locked_rewards"] == 140 * ATTO
        assert stats["locked_bonds"] == 21 * ATTO
        assert stats["tvl"] == 161 * ATTO
        assert stats["active_escrows"] == 1
        s = solvent(c)
        assert s["total_in"] == 161 * ATTO and s["total_paid_out"] == 0

    def test_deposit_must_equal_sum_of_rewards(self, world):
        c, vm, alice, bob, _ = world
        fund(vm, alice)
        as_(vm, alice, REWARD - 1)
        with vm.expect_revert("msg.value must equal"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec()]))

    @pytest.mark.parametrize("bps", [999, 2001, 0, 10_000])
    def test_bond_bps_bounds(self, world, bps):
        c, vm, alice, bob, _ = world
        fund(vm, alice)
        as_(vm, alice, REWARD)
        with vm.expect_revert("bond_bps"):
            c.create_escrow(hx(bob), REPO, "main", "t", bps, json.dumps([milestone_spec()]))

    @pytest.mark.parametrize("bps", [1000, 1500, 2000])
    def test_bond_bps_edges_accepted(self, world, bps):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob, bond_bps=bps)
        assert c.get_escrow(eid)["total_bond"] == REWARD * bps // 10_000

    def test_input_validation(self, world):
        c, vm, alice, bob, _ = world
        fund(vm, alice)
        as_(vm, alice, REWARD)
        good = json.dumps([milestone_spec()])
        with vm.expect_revert("differ"):
            c.create_escrow(hx(alice), REPO, "main", "t", 1500, good)
        with vm.expect_revert("owner/repo"):
            c.create_escrow(hx(bob), "not-a-repo", "main", "t", 1500, good)
        with vm.expect_revert("owner/repo"):
            c.create_escrow(hx(bob), "a/b/../c", "main", "t", 1500, good)
        with vm.expect_revert("branch"):
            c.create_escrow(hx(bob), REPO, "a..b", "t", 1500, good)
        with vm.expect_revert("milestones"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, "[]")
        with vm.expect_revert("milestones"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, "not json")
        with vm.expect_revert("expected_sha"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec(expected_sha="abc")]))
        with vm.expect_revert("deadline"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec(deadline=T0 - 1)]))
        with vm.expect_revert("threshold"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec(min_coverage_bps=10_001)]))
        with vm.expect_revert("reward"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec(reward=0)]))
        with vm.expect_revert("check_name"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec(check_name="")]))
        with vm.expect_revert("app_id"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec(app_id=0)]))

    def test_too_many_milestones(self, world):
        c, vm, alice, bob, _ = world
        fund(vm, alice)
        as_(vm, alice, REWARD * 11)
        with vm.expect_revert("milestones"):
            c.create_escrow(hx(bob), REPO, "main", "t", 1500, json.dumps([milestone_spec()] * 11))

    def test_accept_requires_exact_bond_and_contractor(self, world):
        c, vm, alice, bob, charlie = world
        eid = create(c, vm, alice, bob)
        fund(vm, bob)
        mock_repo(vm)
        as_(vm, bob, BOND - 1)
        with vm.expect_revert("performance bond"):
            c.accept_escrow(eid)
        as_(vm, charlie, BOND)
        with vm.expect_revert("only the contractor"):
            c.accept_escrow(eid)
        accept(c, vm, bob, eid)
        as_(vm, bob, BOND)
        with vm.expect_revert("not open"):
            c.accept_escrow(eid)

    def test_accept_after_deadline_rejected(self, world):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob)
        vm.warp(iso(T0 + 8 * DAY))
        fund(vm, bob)
        as_(vm, bob, BOND)
        with vm.expect_revert("deadline already passed"):
            c.accept_escrow(eid)

    @pytest.mark.parametrize("state", ["gone", "private"])
    def test_accept_requires_accessible_repository(self, world, state):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob)
        fund(vm, bob)
        vm.clear_mocks()
        mock_repo(vm, state=state)
        as_(vm, bob, BOND)
        with vm.expect_revert("not publicly accessible"):
            c.accept_escrow(eid)
        assert c.get_escrow(eid)["status"] == "OPEN"

    def test_accept_survives_rename_301(self, world):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob)
        fund(vm, bob)
        vm.clear_mocks()
        mock_repo(vm, redirect=True, full_name="acme/widgets-renamed")
        as_(vm, bob, BOND)
        c.accept_escrow(eid)
        assert c.get_escrow(eid)["repo_id"] == REPO_ID

    def test_accept_transient_upstream_fault_reverts_cleanly(self, world):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob)
        fund(vm, bob)
        vm.clear_mocks()
        mock_repo(vm, state="error")
        as_(vm, bob, BOND)
        with vm.expect_revert("[TRANSIENT]"):
            c.accept_escrow(eid)
        assert c.get_escrow(eid)["status"] == "OPEN"

    def test_cancel_unbonded_escrow_refunds_employer(self, world):
        c, vm, alice, bob, charlie = world
        eid = create(c, vm, alice, bob)
        as_(vm, charlie)
        with vm.expect_revert("only the employer"):
            c.cancel_escrow(eid)
        as_(vm, alice)
        c.cancel_escrow(eid)
        assert c.get_escrow(eid)["status"] == "CANCELLED"
        s = solvent(c)
        assert s["total_paid_out"] == REWARD and s["liabilities"] == 0
        with vm.expect_revert("unbonded"):
            c.cancel_escrow(eid)

    def test_cannot_cancel_after_bonding(self, world):
        c, vm, alice, bob, _ = world
        eid = active_escrow(c, vm, alice, bob)
        as_(vm, alice)
        with vm.expect_revert("unbonded"):
            c.cancel_escrow(eid)


# ====================================================== successful verification
class TestSuccessfulDelivery:
    def test_passing_commit_is_verified_with_telemetry(self, world):
        report = verified(world)
        assert report["commit_exists"] and report["on_branch"] and report["repo_available"]
        assert report["ci_state"] == "success"
        assert report["tests_passed"] == 120 and report["tests_failed"] == 0
        assert report["coverage_bps"] == 9150
        assert report["critical_findings"] == 0
        assert report["failures"] == []
        assert report["report_source"] == "check_run"
        assert report["report_present"] and not report["report_mismatch"]
        c = world[0]
        m = c.get_milestone(1)
        assert m["status"] == "VERIFIED"
        assert m["release_at"] == T0 + 48 * HOUR
        assert json.loads(m["last_report"])["passed"] is True

    def test_report_file_is_optional(self, world):
        r = verified(world, report=None)
        assert not r["report_present"]

    def test_release_waits_for_dispute_window(self, world):
        c, vm, alice, bob, charlie = world
        verified(world)
        as_(vm, charlie)
        with vm.expect_revert("dispute window still open"):
            c.settle_milestone(1)
        vm.warp(iso(T0 + 48 * HOUR))
        c.settle_milestone(1)  # anyone may settle
        assert c.get_milestone(1)["status"] == "RELEASED"
        assert c.get_escrow(1)["status"] == "CLOSED"
        s = solvent(c)
        assert s["total_paid_out"] == REWARD + BOND  # reward + own bond back
        assert s["liabilities"] == 0
        assert c.get_stats()["total_released"] == REWARD
        with vm.expect_revert("not verified"):
            c.settle_milestone(1)

    def test_employer_can_release_instantly(self, world):
        c, vm, alice, bob, charlie = world
        verified(world)
        as_(vm, charlie)
        with vm.expect_revert("only the employer"):
            c.approve_milestone(1)
        as_(vm, alice)
        c.approve_milestone(1)
        assert c.get_milestone(1)["status"] == "RELEASED"
        assert solvent(c)["total_paid_out"] == REWARD + BOND

    def test_multi_milestone_progress(self, world):
        c, vm, alice, bob, _ = world
        specs = [milestone_spec(), milestone_spec(title="Part two", reward=50 * ATTO)]
        active_escrow(c, vm, alice, bob, specs)
        mock_github(vm)
        deliver(c, vm, bob, 1)
        as_(vm, alice)
        c.approve_milestone(1)
        esc = c.get_escrow(1)
        assert esc["status"] == "ACTIVE" and esc["open_milestones"] == 1
        assert [m["status"] for m in esc["milestones"]] == ["RELEASED", "PENDING"]
        deliver(c, vm, bob, 2)
        as_(vm, alice)
        c.approve_milestone(2)
        assert c.get_escrow(1)["status"] == "CLOSED"
        assert c.get_stats()["active_escrows"] == 0
        solvent(c)

    def test_pinned_expected_sha(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob, [milestone_spec(expected_sha=SHA)])
        mock_github(vm, sha=OTHER_SHA)
        with vm.expect_revert("expected_sha"):
            deliver(c, vm, bob, 1, OTHER_SHA)
        mock_github(vm, sha=SHA)
        assert deliver(c, vm, bob, 1, SHA)["passed"]

    def test_metrics_come_from_the_attested_check_run(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, report=None, check_text="80 passed, 0 failed. Branch coverage: 88.2%. Critical issues: 0")
        r = deliver(c, vm, bob)
        assert r["passed"] and r["tests_passed"] == 80 and r["coverage_bps"] == 8820

    def test_only_contractor_can_deliver(self, world):
        c, vm, alice, bob, charlie = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        for who in (alice, charlie):
            with vm.expect_revert("only the contractor"):
                deliver(c, vm, who)

    def test_sha_must_be_full_hex(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        for bad in ("abc123", "Z" * 40, SHA + "0"):
            with vm.expect_revert("40-hex"):
                deliver(c, vm, bob, 1, bad)

    def test_repo_rename_does_not_break_evaluation(self, world):
        """Evaluation is bound to the numeric repository id, not the stored name."""
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, full_name="acme/widgets-renamed")
        r = deliver(c, vm, bob)
        assert r["passed"], r["failures"]


# ============================================================== failure cases
class TestFailedDelivery:
    @pytest.fixture(autouse=True)
    def _active(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)

    def attempt(self, world, **mock):
        c, vm, _, bob, _ = world
        mock_github(vm, **mock)
        r = deliver(c, vm, bob)
        assert r["passed"] is False and c.get_milestone(1)["status"] == "PENDING"  # retry stays possible
        return r

    def test_insufficient_test_count(self, world):
        assert self.attempt(world, tests=49)["failures"] == ["tests_below_minimum"]

    def test_failing_tests(self, world):
        assert self.attempt(world, failed=2)["failures"] == ["tests_failing"]

    def test_low_coverage(self, world):
        assert self.attempt(world, coverage=79.99)["failures"] == ["coverage_below_minimum"]

    def test_critical_findings(self, world):
        assert self.attempt(world, critical=1)["failures"] == ["critical_findings"]

    def test_missing_commit(self, world):
        r = self.attempt(world, exists=False)
        assert r["failures"] == ["commit_not_found"] and not r["commit_exists"]

    def test_commit_not_on_target_branch(self, world):
        assert self.attempt(world, on_branch=False)["failures"] == ["commit_not_on_branch"]

    def test_ci_failure(self, world):
        assert "ci_failed" in self.attempt(world, ci="failure")["failures"]

    def test_missing_attested_check_run(self, world):
        r = self.attempt(world, ci="absent", report=None)
        assert r["failures"] == ["ci_attestation_missing"]

    def test_spoofed_commit_payload_from_other_repository(self, world):
        assert "spoofed_payload" in self.attempt(world, commit_url_repo="evil/fork")["failures"]

    def test_unattested_security_evidence_fails_closed(self, world):
        r = self.attempt(world, report=None, check_text="all good!")
        assert "critical_findings" in r["failures"] and "tests_below_minimum" in r["failures"]

    def test_attempt_counter_and_cap(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, tests=1)
        for _ in range(5):
            assert deliver(c, vm, bob)["passed"] is False
        assert c.get_milestone(1)["attempts"] == 5
        with vm.expect_revert("maximum delivery attempts"):
            deliver(c, vm, bob)

    def test_expired_deadline_blocks_delivery(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm)
        vm.warp(iso(T0 + 7 * DAY + 1))
        with vm.expect_revert("deadline expired"):
            deliver(c, vm, bob)

    def test_retry_after_failure_succeeds(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, tests=3)
        assert not deliver(c, vm, bob)["passed"]
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]
        assert c.get_milestone(1)["status"] == "VERIFIED"


# ========================= PoC 2: forged / self-authored report.json spoofing
class TestAttestedCheckRun:
    @pytest.fixture(autouse=True)
    def _active(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)

    def test_forged_report_with_5000_tests_is_rejected_when_ci_shows_12(self, world):
        c, vm, _, bob, _ = world
        forged = good_report(tests_passed=5000, branch_coverage=99.0)
        mock_github(vm, tests=12, coverage=99.0, report=forged)
        r = deliver(c, vm, bob)
        assert r["passed"] is False
        assert "SPOOFED_REPORT_PAYLOAD" in r["failures"]
        assert r["tests_passed"] == 12  # the forged 5000 never enters the verdict
        assert c.get_milestone(1)["status"] == "PENDING"

    def test_report_cannot_substitute_a_missing_check_run(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, ci="absent", report=good_report())
        r = deliver(c, vm, bob)
        assert not r["passed"] and "ci_attestation_missing" in r["failures"]

    def test_report_cannot_override_failing_ci(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, ci="failure", tests=3, report=good_report())
        r = deliver(c, vm, bob)
        assert not r["passed"]
        assert {"ci_failed", "tests_below_minimum", "SPOOFED_REPORT_PAYLOAD"} <= set(r["failures"])

    def test_report_for_other_commit_or_repository_is_spoofed(self, world):
        c, vm, _, bob, _ = world
        for bad in (good_report(sha=OTHER_SHA), good_report(repo="evil/fork"), ["not", "a", "dict"]):
            mock_github(vm, report=bad)
            assert "SPOOFED_REPORT_PAYLOAD" in deliver(c, vm, bob)["failures"]

    def test_matching_report_corroborates(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, report=good_report())
        r = deliver(c, vm, bob)
        assert r["passed"] and r["report_present"] and not r["report_mismatch"]

    def test_report_claiming_an_unknown_metric_the_check_run_lacks_is_a_mismatch(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, check_text="120 passed, 0 failed.", report=good_report())
        assert "SPOOFED_REPORT_PAYLOAD" in deliver(c, vm, bob)["failures"]

    def test_check_run_from_the_wrong_app_is_ignored(self, world):
        c, vm, _, bob, _ = world
        decoy = {"id": 999, "name": CHECK, "app": {"id": 1234}, "status": "completed", "conclusion": "success",
                 "output": {"title": "fake", "summary": "9000 passed, 0 failed. Branch coverage: 100%. Critical issues: 0"}}
        mock_github(vm, ci="absent", decoys=[decoy], report=None)
        r = deliver(c, vm, bob)
        assert r["failures"] == ["ci_attestation_missing"] and r["tests_passed"] == 0

    def test_check_run_with_the_wrong_name_is_ignored(self, world):
        c, vm, _, bob, _ = world
        decoy = {"id": 999, "name": "lint", "app": {"id": 15368}, "status": "completed", "conclusion": "success",
                 "output": {"title": "x", "summary": "9000 passed, 0 failed. Branch coverage: 100%. Critical issues: 0"}}
        mock_github(vm, ci="absent", decoys=[decoy], report=None)
        assert deliver(c, vm, bob)["failures"] == ["ci_attestation_missing"]

    def test_newest_rerun_of_the_attested_check_wins(self, world):
        c, vm, _, bob, _ = world
        newer_failure = {"id": 500, "name": CHECK, "app": {"id": 15368}, "status": "completed", "conclusion": "failure",
                         "output": {"title": "", "summary": "7 passed, 3 failed. Branch coverage: 50%. Critical issues: 0"}}
        mock_github(vm, decoys=[newer_failure], report=None)  # genuine run has id 101
        assert not deliver(c, vm, bob)["passed"]

    def test_custom_trusted_app_id_is_enforced(self, world):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob, [milestone_spec(app_id=777, check_name="audit/suite")])
        accept(c, vm, bob, eid)
        mid = c.get_escrow(eid)["first_milestone_id"]
        mock_github(vm, report=None)  # an attested run exists, but under the default name/app
        assert deliver(c, vm, bob, mid)["failures"] == ["ci_attestation_missing"]
        mock_github(vm, check_name="audit/suite", app_id=777)
        assert deliver(c, vm, bob, mid)["passed"]


# ===================================================== default and slashing
class TestDefaultAndSlashing:
    def test_default_refunds_employer_and_slashes_bond(self, world):
        c, vm, alice, bob, charlie = world
        active_escrow(c, vm, alice, bob)
        mock_repo(vm)
        as_(vm, charlie)
        with vm.expect_revert("deadline has not passed"):
            c.claim_default(1)
        vm.warp(iso(T0 + 7 * DAY + 1))
        assert c.claim_default(1)["outcome"] == "DEFAULTED"  # permissionless; funds go to the employer
        assert c.get_milestone(1)["status"] == "DEFAULTED"
        s = solvent(c)
        assert s["total_paid_out"] == REWARD + BOND
        assert c.get_stats()["total_slashed"] == BOND
        assert c.get_escrow(1)["status"] == "CLOSED"
        with vm.expect_revert("not active"):  # escrow closed: no double payout
            c.claim_default(1)

    def test_verified_milestone_cannot_be_defaulted(self, world):
        c, vm, alice, bob, charlie = world
        verified(world)
        vm.warp(iso(T0 + 7 * DAY + 1))
        as_(vm, charlie)
        with vm.expect_revert("not awaiting delivery"):
            c.claim_default(1)

    def test_default_after_failed_attempts(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, tests=1)
        deliver(c, vm, bob)
        vm.warp(iso(T0 + 8 * DAY))
        assert c.claim_default(1)["outcome"] == "DEFAULTED"

    def test_default_one_milestone_keeps_other_alive(self, world):
        c, vm, alice, bob, _ = world
        specs = [milestone_spec(deadline=T0 + DAY), milestone_spec(deadline=T0 + 10 * DAY)]
        active_escrow(c, vm, alice, bob, specs)
        vm.warp(iso(T0 + 2 * DAY))
        mock_github(vm)
        c.claim_default(1)
        esc = c.get_escrow(1)
        assert esc["status"] == "ACTIVE" and esc["open_milestones"] == 1
        assert deliver(c, vm, bob, 2)["passed"]
        solvent(c)

    def test_no_default_on_unbonded_escrow(self, world):
        c, vm, alice, bob, _ = world
        create(c, vm, alice, bob)
        vm.warp(iso(T0 + 8 * DAY))
        with vm.expect_revert("not active"):
            c.claim_default(1)


# ================================================== disputes & anti-griefing
class TestDisputes:
    def test_bond_escalates_exponentially_per_dispute(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        base = DISPUTE_BOND_1
        assert c.quote_dispute_bond(1, hx(alice)) == base
        for n, bond in enumerate([base, 2 * base, 4 * base]):
            assert c.get_milestone(1)["next_dispute_bond"] == bond
            mock_github(vm, tests=1)  # evidence now fails
            out = dispute(c, vm, alice, reason=f"force-pushed {n}")
            assert out["dispute_outcome"] == "DELIVERY_OVERTURNED"
            assert c.get_milestone(1)["status"] == "PENDING"
            assert c.get_milestone(1)["dispute_count"] == n + 1
            mock_github(vm)
            deliver(c, vm, bob)
        as_(vm, alice, 8 * base + FEE)
        with vm.expect_revert("dispute limit"):
            c.file_dispute(1, "again")
        solvent(c)

    def test_floor_applies_to_small_milestones(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob, [milestone_spec(reward=ATTO)])
        assert c.quote_dispute_bond(1, hx(alice)) == ATTO // 10
        assert c.quote_dispute_fee(1) == ATTO * 300 // 10_000  # 3% of 1 GEN beats the floor
        active_escrow(c, vm, alice, bob, [milestone_spec(reward=ATTO // 10)])
        assert c.quote_dispute_fee(2) == ATTO // 50  # 3% of 0.1 GEN would be dust: the 0.02 GEN floor applies

    def test_exact_cost_enforced(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        need = c.quote_dispute_bond(1, hx(alice)) + c.quote_dispute_fee(1)
        for wrong in (0, need - 1, need + 1, c.quote_dispute_bond(1, hx(alice))):  # bond alone is not enough
            as_(vm, alice, wrong)
            with vm.expect_revert("dispute cost must be exactly"):
                c.file_dispute(1, "nope")

    def test_frivolous_dispute_forfeits_bond_burns_fee_and_releases(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        bond = c.quote_dispute_bond(1, hx(alice))
        mock_github(vm)  # evidence unchanged: dispute is frivolous
        out = dispute(c, vm, alice, reason="I just do not like it")
        assert out["dispute_outcome"] == "UPHELD_DELIVERY"
        assert c.get_milestone(1)["status"] == "RELEASED"
        s = solvent(c)
        assert s["total_paid_out"] == REWARD + BOND + bond  # contractor is compensated with the bond
        assert s["fees_retained"] == FEE  # the fee is never paid back to anyone
        assert c.get_stats()["total_dispute_forfeited"] == bond
        assert c.get_strikes(hx(alice)) == 1

    def test_lost_disputes_raise_future_bonds_for_the_same_address(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob, 1)
        base = c.quote_dispute_bond(1, hx(alice))
        dispute(c, vm, alice, reason="frivolous")
        create(c, vm, alice, bob)
        accept(c, vm, bob, 2)
        assert c.quote_dispute_bond(2, hx(alice)) == 2 * base  # the strike doubles the next bond
        assert c.quote_dispute_bond(2, hx(bob)) == base  # strikes are per address

    def test_successful_dispute_refunds_bond_but_never_the_fee(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        bond = c.quote_dispute_bond(1, hx(alice))
        mock_github(vm, on_branch=False)  # history rewritten after verification
        out = dispute(c, vm, alice, reason="commit dropped from main by force push")
        assert out["dispute_outcome"] == "DELIVERY_OVERTURNED"
        assert "commit_not_on_branch" in out["failures"]
        m = c.get_milestone(1)
        assert m["status"] == "PENDING" and m["release_at"] == 0
        s = solvent(c)
        assert s["total_paid_out"] == bond  # only the bond comes back
        assert s["fees_retained"] == FEE
        assert c.get_strikes(hx(alice)) == 0

    def test_dispute_guards(self, world):
        c, vm, alice, bob, charlie = world
        verified(world)
        need = c.quote_dispute_bond(1, hx(alice)) + c.quote_dispute_fee(1)
        for who in (bob, charlie):
            as_(vm, who, need)
            with vm.expect_revert("only the employer"):
                c.file_dispute(1, "x")
        as_(vm, alice, need)
        with vm.expect_revert("invalid dispute reason"):
            c.file_dispute(1, "")
        vm.warp(iso(T0 + 48 * HOUR))
        with vm.expect_revert("dispute window closed"):
            c.file_dispute(1, "too late")

    def test_cannot_dispute_unverified_milestone(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        as_(vm, alice, ATTO)
        with vm.expect_revert("not in a disputable state"):
            c.file_dispute(1, "x")

    def test_dispute_cannot_be_won_by_rerunning_ci(self, world):
        """Re-running the check makes it pending; a dispute cannot be decided on a pending check."""
        c, vm, alice, bob, _ = world
        verified(world)
        mock_github(vm, ci="pending")
        need = c.quote_dispute_bond(1, hx(alice)) + c.quote_dispute_fee(1)
        as_(vm, alice, need)
        with vm.expect_revert("CI is re-running"):
            c.file_dispute(1, "rerun")
        assert c.get_milestone(1)["status"] == "VERIFIED"

    def test_dispute_cannot_be_won_by_hiding_the_repository(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        mock_github(vm, repo_state="gone")
        need = c.quote_dispute_bond(1, hx(alice)) + c.quote_dispute_fee(1)
        as_(vm, alice, need)
        with vm.expect_revert("repository is unreachable"):
            c.file_dispute(1, "deleted the repo")
        assert c.get_milestone(1)["status"] == "VERIFIED"
        vm.warp(iso(T0 + 48 * HOUR))
        c.settle_milestone(1)  # the contractor is still paid
        assert c.get_milestone(1)["status"] == "RELEASED"


# ========= PoC 1: overturned delivery after the deadline must not be harvestable
class TestResubmitWindow:
    def deliver_late_in_window(self, world):
        """Deliver 2h before the deadline; the 48h dispute window then outlives the deadline."""
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        vm.warp(iso(T0 + 7 * DAY - 2 * HOUR))
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]

    def test_overturn_after_deadline_extends_by_72h_and_blocks_harvesting(self, world):
        c, vm, alice, bob, charlie = world
        self.deliver_late_in_window(world)
        old_deadline = T0 + 7 * DAY
        t = T0 + 7 * DAY + 20 * HOUR  # deadline passed, dispute window still open
        vm.warp(iso(t))
        mock_github(vm, on_branch=False)
        out = dispute(c, vm, alice, reason="force-pushed after verification")
        assert out["dispute_outcome"] == "DELIVERY_OVERTURNED"

        m = c.get_milestone(1)
        assert m["status"] == "PENDING" and m["attempts"] == 0
        assert old_deadline < t  # the original deadline really was behind us
        assert m["deadline"] == t + 72 * HOUR
        assert m["resubmit_until"] == t + 72 * HOUR

        # The reviewer's exploit: claim_default straight after the overturn.
        mock_github(vm)
        as_(vm, alice)
        with vm.expect_revert("deadline has not passed"):
            c.claim_default(1)
        vm.warp(iso(t + 71 * HOUR))
        with vm.expect_revert("deadline has not passed"):
            c.claim_default(1)
        assert c.get_stats()["total_slashed"] == 0

        # The contractor resubmits inside the fresh window and is paid.
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]
        as_(vm, alice)
        c.approve_milestone(1)
        assert c.get_milestone(1)["status"] == "RELEASED"
        s = solvent(c)
        assert c.get_stats()["total_slashed"] == 0
        assert s["fees_retained"] == FEE

    def test_default_becomes_possible_only_after_the_grace_expires(self, world):
        c, vm, alice, bob, charlie = world
        self.deliver_late_in_window(world)
        t = T0 + 7 * DAY + 20 * HOUR
        vm.warp(iso(t))
        mock_github(vm, tests=1)
        dispute(c, vm, alice)
        vm.warp(iso(t + 72 * HOUR + 1))
        mock_github(vm)
        as_(vm, charlie)
        assert c.claim_default(1)["outcome"] == "DEFAULTED"  # the contractor had a full fresh window

    def test_overturn_with_plenty_of_time_left_does_not_move_the_deadline(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        mock_github(vm, tests=1)
        dispute(c, vm, alice)
        assert c.get_milestone(1)["deadline"] == T0 + 7 * DAY  # 7d left >= 72h: untouched

    def test_overturn_resets_the_attempt_budget(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, tests=1)
        for _ in range(4):
            deliver(c, vm, bob)
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]  # 5th attempt passes
        assert c.get_milestone(1)["attempts"] == 5
        mock_github(vm, tests=1)
        dispute(c, vm, alice)
        assert c.get_milestone(1)["attempts"] == 0  # otherwise the contractor could never resubmit
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]


# =========== PoC 3: repo renamed / 301 / deleted / private -> no malicious slash
class TestExternalFaults:
    def test_rename_via_301_keeps_everything_working(self, world):
        c, vm, alice, bob, _ = world
        eid = create(c, vm, alice, bob)
        fund(vm, bob)
        vm.clear_mocks()
        mock_repo(vm, redirect=True, full_name="acme/widgets-renamed")
        as_(vm, bob, BOND)
        c.accept_escrow(eid)
        mock_github(vm, full_name="acme/widgets-renamed")
        assert deliver(c, vm, bob)["passed"]

    @pytest.mark.parametrize("state", ["gone", "private"])
    def test_deleted_or_private_repo_freezes_instead_of_slashing(self, world, state):
        c, vm, alice, bob, charlie = world
        active_escrow(c, vm, alice, bob)
        # the employer removes access, then the deadline passes with the contractor unable to deliver
        mock_github(vm, repo_state=state)
        vm.warp(iso(T0 + 8 * DAY))
        as_(vm, alice)
        out = c.claim_default(1)
        assert out["outcome"] == "FROZEN_EXTERNAL_FAULT"
        m = c.get_milestone(1)
        assert m["status"] == "FROZEN_EXTERNAL_FAULT" and m["frozen_at"] == T0 + 8 * DAY
        assert c.get_stats()["total_slashed"] == 0
        assert solvent(c)["total_paid_out"] == 0  # nothing moved
        with vm.expect_revert("not awaiting delivery"):
            c.claim_default(1)  # still cannot slash

    def test_contractor_submission_against_unreachable_repo_freezes_and_costs_no_attempt(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, repo_state="gone")
        r = deliver(c, vm, bob)
        assert r["fault"] == "FROZEN_EXTERNAL_FAULT" and r["failures"] == ["repo_unavailable"]
        m = c.get_milestone(1)
        assert m["status"] == "FROZEN_EXTERNAL_FAULT" and m["attempts"] == 0

    def test_default_still_works_when_the_repo_is_reachable(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        vm.warp(iso(T0 + 8 * DAY))
        assert c.claim_default(1)["outcome"] == "DEFAULTED"

    def test_mutual_cancellation_refunds_employer_and_returns_bond_intact(self, world):
        c, vm, alice, bob, charlie = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, repo_state="gone")
        deliver(c, vm, bob)  # -> frozen
        as_(vm, charlie)
        with vm.expect_revert("only the employer or the contractor"):
            c.cancel_fault_free(1)
        as_(vm, alice)
        first = c.cancel_fault_free(1)
        assert first["outcome"] == "CONSENT_RECORDED" and c.get_milestone(1)["status"] == "FROZEN_EXTERNAL_FAULT"
        as_(vm, bob)
        assert c.cancel_fault_free(1)["outcome"] == "CANCELLED_FAULT_FREE"
        m = c.get_milestone(1)
        assert m["status"] == "CANCELLED_FAULT_FREE"
        s = solvent(c)
        assert s["total_paid_out"] == REWARD + BOND  # employer: reward back, contractor: bond back
        assert s["liabilities"] == 0 and c.get_stats()["total_slashed"] == 0
        assert c.get_escrow(1)["status"] == "CLOSED"

    def test_unilateral_cancel_only_after_grace_and_only_if_still_unreachable(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, repo_state="gone")
        deliver(c, vm, bob)
        vm.warp(iso(T0 + 7 * DAY - 1))
        as_(vm, alice)
        assert c.cancel_fault_free(1)["outcome"] == "CONSENT_RECORDED"
        vm.warp(iso(T0 + 7 * DAY + 1))  # frozen at T0, grace (7d) elapsed
        mock_github(vm)  # repository is back: unilateral cancel is refused
        with vm.expect_revert("reachable again"):
            c.cancel_fault_free(1)
        mock_github(vm, repo_state="gone")
        assert c.cancel_fault_free(1)["outcome"] == "CANCELLED_FAULT_FREE"
        assert solvent(c)["total_paid_out"] == REWARD + BOND

    def test_frozen_milestone_revives_when_the_repo_returns(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, repo_state="private")
        deliver(c, vm, bob)
        vm.warp(iso(T0 + 9 * DAY))  # long past the original deadline
        mock_github(vm)
        r = deliver(c, vm, bob)
        assert r["passed"]
        m = c.get_milestone(1)
        assert m["status"] == "VERIFIED" and m["consent_mask"] == 0 and m["frozen_at"] == 0
        assert m["deadline"] == T0 + 9 * DAY + 72 * HOUR  # compensated for the outage
        solvent(c)

    def test_cannot_cancel_a_milestone_that_is_not_frozen(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        as_(vm, alice)
        with vm.expect_revert("not frozen"):
            c.cancel_fault_free(1)

    def test_transient_upstream_errors_revert_and_consume_nothing(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        for status in (429, 503):
            vm.clear_mocks()
            vm.mock_web(r".*", {"status": status, "body": "try later"})
            with vm.expect_revert("[TRANSIENT]"):
                deliver(c, vm, bob)
        m = c.get_milestone(1)
        assert m["attempts"] == 0 and m["status"] == "PENDING"

    def test_unresolvable_301_is_transient(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        vm.clear_mocks()
        vm.mock_web(r".*", {"method": "GET", "response": {"status": 301, "headers": {}, "body": b""}})
        with vm.expect_revert("[TRANSIENT]"):
            deliver(c, vm, bob)
        assert c.get_milestone(1)["attempts"] == 0


# ========================================== PoC 4: pending CI conserves attempts
class TestAttemptConservation:
    def test_pending_ci_does_not_consume_attempts(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, ci="pending")
        for i in range(8):  # more polls than the 5-attempt budget
            r = deliver(c, vm, bob)
            assert r["failures"] == ["ci_pending"] and not r["passed"]
        m = c.get_milestone(1)
        assert m["attempts"] == 0 and m["pending_polls"] == 8
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]  # budget intact: a real attempt still goes through
        assert c.get_milestone(1)["attempts"] == 1

    def test_real_failures_still_consume_attempts(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, tests=1)
        deliver(c, vm, bob)
        assert c.get_milestone(1)["attempts"] == 1

    def test_pending_polls_are_capped_so_validators_are_not_spammed(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, ci="pending")
        for _ in range(20):
            deliver(c, vm, bob)
        assert c.get_milestone(1)["pending_polls"] == 20 and c.get_milestone(1)["attempts"] == 0
        deliver(c, vm, bob)  # 21st poll now costs an attempt
        assert c.get_milestone(1)["attempts"] == 1

    def test_pending_ci_next_to_a_real_failure_is_not_free(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, ci="pending", on_branch=False)
        r = deliver(c, vm, bob)
        assert "commit_not_on_branch" in r["failures"]
        assert c.get_milestone(1)["attempts"] == 1


# ====================================================== validator consensus
class TestValidatorConsensus:
    def test_validator_agrees_on_identical_evidence(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        assert vm.run_validator() is True

    def test_validator_rejects_leader_claiming_false_pass(self, world):
        """A malicious leader reports PASS; honest validators see failing tests."""
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        mock_github(vm, tests=2)
        assert vm.run_validator() is False

    def test_validator_rejects_forged_numbers_with_same_verdict(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        mock_github(vm, tests=121)
        assert vm.run_validator() is False

    def test_validator_rejects_leader_that_hides_a_report_mismatch(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        mock_github(vm, report=good_report(tests_passed=5000))
        assert vm.run_validator() is False

    def test_validator_rejects_leader_error_when_it_can_verify(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        assert vm.run_validator(leader_error=Exception("[TRANSIENT] boom")) is False

    def test_validators_agree_on_failure_verdicts(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, exists=False)
        deliver(c, vm, bob)
        assert vm.run_validator() is True

    def test_validators_must_agree_the_repository_is_gone(self, world):
        """A leader that claims the repo vanished (to freeze a milestone) is outvoted."""
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, repo_state="gone")
        deliver(c, vm, bob)  # leader sees it gone
        mock_github(vm)  # honest validators still see it
        assert vm.run_validator() is False
