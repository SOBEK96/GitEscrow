"""GitEscrow direct-mode test suite.

Run: pytest            (from the repository root)
Direct mode runs the leader; validator agreement is exercised explicitly with
direct_vm.run_validator against swapped mocks.
"""

import json

import pytest

from conftest import (
    ATTO, BOND, BOND_BPS, CONTRACT, DAY, HOUR, OTHER_SHA, REPO, REWARD, SHA, T0,
    accept, active_escrow, as_, create, fund, good_report, hx, iso, milestone_spec, mock_github,
)


@pytest.fixture
def world(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    c = direct_deploy(CONTRACT)
    return c, direct_vm, direct_alice, direct_bob, direct_charlie


def deliver(c, vm, contractor, mid=1, sha=SHA):
    as_(vm, contractor)
    return c.evaluate_milestone_delivery(mid, sha)


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
        assert c.get_escrow(eid)["status"] == "ACTIVE"
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
        assert report["commit_exists"] and report["on_branch"]
        assert report["ci_state"] == "success"
        assert report["tests_passed"] == 120 and report["tests_failed"] == 0
        assert report["coverage_bps"] == 9150
        assert report["critical_findings"] == 0
        assert report["failures"] == []
        assert report["report_source"] == "report"
        c = world[0]
        m = c.get_milestone(1)
        assert m["status"] == "VERIFIED"
        assert m["release_at"] == T0 + 48 * HOUR
        assert json.loads(m["last_report"])["passed"] is True

    def test_release_waits_for_dispute_window(self, world):
        c, vm, alice, bob, charlie = world
        verified(world)
        as_(vm, charlie)
        with vm.expect_revert("dispute window still open"):
            c.settle_milestone(1)
        vm.warp(iso(T0 + 48 * HOUR))
        c.settle_milestone(1)  # anyone may settle
        m = c.get_milestone(1)
        assert m["status"] == "RELEASED"
        assert c.get_escrow(1)["status"] == "CLOSED"
        s = solvent(c)
        # contractor receives reward + own bond back
        assert s["total_paid_out"] == REWARD + BOND
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

    def test_check_run_fallback_without_report_file(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm, report=None, check_text="80 passed, 0 failed. Branch coverage: 88.2%. Critical issues: 0")
        r = deliver(c, vm, bob)
        assert r["passed"] and r["report_source"] == "check_runs"
        assert r["tests_passed"] == 80 and r["coverage_bps"] == 8820

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
        m = c.get_milestone(1)
        assert r["passed"] is False and m["status"] == "PENDING"  # retry stays possible
        return r

    def test_insufficient_test_count(self, world):
        r = self.attempt(world, report=good_report(tests_passed=49))
        assert r["failures"] == ["tests_below_minimum"]

    def test_failing_tests(self, world):
        r = self.attempt(world, report=good_report(tests_failed=2))
        assert r["failures"] == ["tests_failing"]

    def test_low_coverage(self, world):
        r = self.attempt(world, report=good_report(branch_coverage=79.99))
        assert r["failures"] == ["coverage_below_minimum"]

    def test_critical_findings(self, world):
        r = self.attempt(world, report=good_report(critical_findings=1))
        assert r["failures"] == ["critical_findings"]

    def test_missing_commit(self, world):
        r = self.attempt(world, exists=False)
        assert r["failures"] == ["commit_not_found"]
        assert not r["commit_exists"]

    def test_commit_not_on_target_branch(self, world):
        r = self.attempt(world, on_branch=False)
        assert r["failures"] == ["commit_not_on_branch"]

    def test_ci_failure_and_pending_and_missing(self, world):
        assert "ci_failed" in self.attempt(world, ci="failure")["failures"]
        assert "ci_pending" in self.attempt(world, ci="pending")["failures"]
        assert "ci_missing" in self.attempt(world, ci="none")["failures"]

    def test_spoofed_report_for_other_commit(self, world):
        r = self.attempt(world, report=good_report(sha=OTHER_SHA))
        assert r["spoofed"] and "spoofed_payload" in r["failures"]

    def test_spoofed_report_claiming_other_repository(self, world):
        r = self.attempt(world, report=good_report(repo="evil/fork"))
        assert r["spoofed"] and "spoofed_payload" in r["failures"]

    def test_spoofed_commit_payload_from_other_repository(self, world):
        r = self.attempt(world, commit_url_repo="evil/fork")
        assert "spoofed_payload" in r["failures"]

    def test_garbage_report_is_spoofed_not_trusted(self, world):
        r = self.attempt(world, report=["tests_passed", 9999])
        assert r["spoofed"]

    def test_no_evidence_at_all_fails_closed(self, world):
        r = self.attempt(world, report=None, ci="success", check_text="all good!")
        assert "critical_findings" in r["failures"]  # unverified security == not clean
        assert "tests_below_minimum" in r["failures"]

    def test_attempt_counter_and_cap(self, world):
        c, vm, _, bob, _ = world
        mock_github(vm, report=good_report(tests_passed=1))
        for i in range(5):
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
        mock_github(vm, report=good_report(tests_passed=3))
        assert not deliver(c, vm, bob)["passed"]
        mock_github(vm)
        assert deliver(c, vm, bob)["passed"]
        assert c.get_milestone(1)["status"] == "VERIFIED"

    def test_upstream_rate_limit_is_transient_revert(self, world):
        c, vm, _, bob, _ = world
        vm.clear_mocks()
        vm.mock_web(r".*", {"status": 429, "body": "slow down"})
        with vm.expect_revert("[TRANSIENT]"):
            deliver(c, vm, bob)
        assert c.get_milestone(1)["attempts"] == 0  # transient faults cost the contractor nothing


# ===================================================== default and slashing
class TestDefaultAndSlashing:
    def test_default_refunds_employer_and_slashes_bond(self, world):
        c, vm, alice, bob, charlie = world
        active_escrow(c, vm, alice, bob)
        as_(vm, charlie)
        with vm.expect_revert("deadline has not passed"):
            c.claim_default(1)
        vm.warp(iso(T0 + 7 * DAY + 1))
        c.claim_default(1)  # permissionless, funds always go to the employer
        m = c.get_milestone(1)
        assert m["status"] == "DEFAULTED"
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
        mock_github(vm, report=good_report(tests_passed=1))
        deliver(c, vm, bob)
        vm.warp(iso(T0 + 8 * DAY))
        c.claim_default(1)
        assert c.get_milestone(1)["status"] == "DEFAULTED"

    def test_default_one_milestone_keeps_other_alive(self, world):
        c, vm, alice, bob, _ = world
        specs = [milestone_spec(deadline=T0 + DAY), milestone_spec(deadline=T0 + 10 * DAY)]
        active_escrow(c, vm, alice, bob, specs)
        vm.warp(iso(T0 + 2 * DAY))
        c.claim_default(1)
        esc = c.get_escrow(1)
        assert esc["status"] == "ACTIVE" and esc["open_milestones"] == 1
        mock_github(vm)
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
        base = REWARD * 100 // 10_000  # 1% of 100 GEN = 1 GEN > floor
        assert c.quote_dispute_bond(1, hx(alice)) == base
        # overturned disputes send the milestone back to PENDING; resubmit and dispute again
        expected = [base, 2 * base, 4 * base]
        for n, bond in enumerate(expected):
            assert c.get_milestone(1)["next_dispute_bond"] == bond
            mock_github(vm, report=good_report(tests_passed=1))  # evidence now fails
            as_(vm, alice, bond)
            out = c.file_dispute(1, f"branch force-pushed {n}")
            assert out["dispute_outcome"] == "DELIVERY_OVERTURNED"
            assert c.get_milestone(1)["status"] == "PENDING"
            assert c.get_milestone(1)["dispute_count"] == n + 1
            mock_github(vm)
            deliver(c, vm, bob)
        as_(vm, alice, 8 * base)
        with vm.expect_revert("dispute limit"):
            c.file_dispute(1, "again")
        solvent(c)

    def test_floor_applies_to_small_milestones(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob, [milestone_spec(reward=ATTO)])
        assert c.quote_dispute_bond(1, hx(alice)) == ATTO // 10  # 1% would be 0.01 GEN

    def test_exact_bond_enforced(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        need = c.quote_dispute_bond(1, hx(alice))
        for wrong in (0, need - 1, need + 1):
            as_(vm, alice, wrong)
            with vm.expect_revert("dispute bond must be exactly"):
                c.file_dispute(1, "nope")

    def test_frivolous_dispute_forfeits_bond_and_releases(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        need = c.quote_dispute_bond(1, hx(alice))
        mock_github(vm)  # evidence unchanged: dispute is frivolous
        as_(vm, alice, need)
        out = c.file_dispute(1, "I just do not like it")
        assert out["dispute_outcome"] == "UPHELD_DELIVERY"
        assert c.get_milestone(1)["status"] == "RELEASED"
        s = solvent(c)
        assert s["total_paid_out"] == REWARD + BOND + need  # contractor is compensated
        assert c.get_stats()["total_dispute_forfeited"] == need
        assert c.get_strikes(hx(alice)) == 1

    def test_lost_disputes_raise_future_bonds_for_the_same_address(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob, 1)
        base = c.quote_dispute_bond(1, hx(alice))
        as_(vm, alice, base)
        c.file_dispute(1, "frivolous")
        # a second escrow by the same employer starts at 2x because of the strike
        create(c, vm, alice, bob)
        accept(c, vm, bob, 2)
        assert c.quote_dispute_bond(2, hx(alice)) == 2 * base
        assert c.quote_dispute_bond(2, hx(bob)) == base  # strikes are per address

    def test_successful_dispute_refunds_bond_and_reopens_milestone(self, world):
        c, vm, alice, bob, _ = world
        verified(world)
        need = c.quote_dispute_bond(1, hx(alice))
        mock_github(vm, on_branch=False)  # history rewritten after verification
        as_(vm, alice, need)
        out = c.file_dispute(1, "commit dropped from main by force push")
        assert out["dispute_outcome"] == "DELIVERY_OVERTURNED"
        assert "commit_not_on_branch" in out["failures"]
        m = c.get_milestone(1)
        assert m["status"] == "PENDING" and m["release_at"] == 0
        s = solvent(c)
        assert s["total_paid_out"] == need  # bond refunded, nothing else moved
        assert c.get_strikes(hx(alice)) == 0

    def test_dispute_guards(self, world):
        c, vm, alice, bob, charlie = world
        verified(world)
        need = c.quote_dispute_bond(1, hx(alice))
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
        mock_github(vm, report=good_report(tests_passed=2))
        assert vm.run_validator() is False

    def test_validator_rejects_forged_numbers_with_same_verdict(self, world):
        c, vm, alice, bob, _ = world
        active_escrow(c, vm, alice, bob)
        mock_github(vm)
        deliver(c, vm, bob)
        mock_github(vm, report=good_report(tests_passed=121))
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
