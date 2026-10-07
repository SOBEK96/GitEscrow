# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

# GitEscrow - autonomous, code-verifiable milestone escrow on GenLayer.
#
# An employer locks milestone rewards in native GEN, a contractor stakes a
# performance bond (10-20% of each milestone), and delivery is judged by a
# GenVM validator quorum that independently inspects the submitted GitHub
# commit: its existence on the target branch and the output of ONE named
# GitHub Actions check-run produced by a pinned GitHub App id. A committed
# `.gitescrow/report.json` can only corroborate that check-run, never replace it.
#
# Lifecycle of a milestone:
#
#   PENDING --evaluate (consensus PASS)--> VERIFIED --48h window--> RELEASED
#      |  ^                                  |
#      |  |                                  +--file_dispute (escalating bond + non-refundable fee)
#      |  +--overturned: deadline >= now+72h,    upheld    -> RELEASED, bond to contractor
#      |     attempts reset, no default                      overturned -> PENDING
#      |  (a dispute re-checks the delivered SHA and its PINNED check-run, never the branch tip)
#      |
#      +--deadline + resubmit grace pass--> DEFAULTED   (employer: refund + slashed bond)
#      |
#      +--repository deleted / private / inaccessible--> FROZEN_EXTERNAL_FAULT
#             +--thaw_milestone / evaluate once the repo answers--> PENDING (deadline extended,
#             |                                                    at least one fresh attempt)
#             +--cancel_fault_free (both consent, or 7 days + repo gone / attempts spent / deadline passed)
#                  --> CANCELLED_FAULT_FREE: employer refunded, bond returned intact
#
# The repository is bound by its numeric GitHub id when the contractor accepts,
# so renames and 301 redirects never break evaluation. Verification is a
# deterministic function of immutable data (commit SHA) plus the live
# availability of the repository, so validators re-collect the evidence
# themselves and demand the same derived verdict. No LLM is involved.

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import genlayer as gl
from genlayer import Address, u256
from genlayer.storage import TreeMap

# genvm-lint matches the bare name `allow_storage` on storage dataclasses.
allow_storage = gl.storage.allow

# ----------------------------------------------------------------------------
# Error classification (see GenLayer equivalence-principle guidance)
# ----------------------------------------------------------------------------
ERROR_EXPECTED = "[EXPECTED]"  # deterministic business-rule failure
ERROR_EXTERNAL = "[EXTERNAL]"  # deterministic upstream 4xx
ERROR_TRANSIENT = "[TRANSIENT]"  # network / 5xx / rate limit

# ----------------------------------------------------------------------------
# Protocol constants
# ----------------------------------------------------------------------------
ATTO = 10**18
BPS = 10_000
MIN_BOND_BPS = 1_000  # contractor bond: 10% ...
MAX_BOND_BPS = 2_000  # ... to 20% of each milestone reward
MAX_MILESTONES = 10
MAX_ATTEMPTS = 5  # failed delivery evaluations per milestone (bounds validator cost)
MAX_PENDING_POLLS = 20  # "CI still running" polls that do not consume an attempt
DISPUTE_WINDOW = 48 * 3600  # seconds a verified milestone stays challengeable
RESUBMIT_GRACE = 72 * 3600  # contractor's fresh delivery window after an overturned delivery
FREEZE_GRACE = 7 * 86400  # frozen this long (and still unreachable) -> unilateral fault-free cancel
MAX_DISPUTES = 3  # disputes per milestone
MIN_DISPUTE_BOND = ATTO // 10  # 0.1 GEN floor
DISPUTE_BASE_BPS = 100  # base dispute bond = 1% of the milestone reward
DISPUTE_FEE_BPS = 300  # non-refundable arbitration fee = 3% of the milestone reward ...
MIN_DISPUTE_FEE = ATTO // 50  # ... with a 0.02 GEN floor
DEFAULT_APP_ID = 15368  # the GitHub Actions app
MAX_CHECK_NAME_LEN = 100
MAX_STRIKE_EXPONENT = 4  # lost disputes by one address raise its next bond up to 2^4
MAX_TITLE_LEN = 120
MAX_REASON_LEN = 500

ST_OPEN = "OPEN"  # escrow funded, waiting for contractor bond
ST_ACTIVE = "ACTIVE"
ST_CLOSED = "CLOSED"
ST_CANCELLED = "CANCELLED"

MS_PENDING = "PENDING"
MS_VERIFIED = "VERIFIED"
MS_RELEASED = "RELEASED"
MS_DEFAULTED = "DEFAULTED"
MS_CANCELLED = "CANCELLED"
MS_FROZEN = "FROZEN_EXTERNAL_FAULT"
MS_FAULT_CANCELLED = "CANCELLED_FAULT_FREE"

GITHUB_ORIGIN = "https://api.github.com/"
REPO_BY_NAME = GITHUB_ORIGIN + "repos/"
REPO_BY_ID = GITHUB_ORIGIN + "repositories/"  # numeric id: survives renames and transfers
RAW_GITHUB = "https://raw.githubusercontent.com/"
REPORT_PATH = ".gitescrow/report.json"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "GitEscrow-GenVM",
}

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^(pull/[0-9]{1,9}|[A-Za-z0-9_./-]{1,100})$")  # branch name or PR head (pull/N)

SPOOFED_REPORT = "SPOOFED_REPORT_PAYLOAD"


# ----------------------------------------------------------------------------
# Pure helpers (deterministic, unit-testable, safe to call inside nondet blocks)
# ----------------------------------------------------------------------------
def _to_text(body) -> str:
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    return str(body)


def _safe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _as_int(value, default=None):
    """Strict-ish integer coercion: rejects bools and non-numeric values."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*\d{1,30}\s*", value):
        return int(value.strip())
    return default


def _percent_to_bps(value):
    try:
        bps = int(round(float(value) * 100))
    except Exception:
        return None
    return max(0, min(BPS, bps))


def _parse_ci_text(text: str) -> dict:
    """Extract pytest/vitest/forge-style counters from a check-run summary."""
    out = {"passed": None, "failed": None, "coverage_bps": None, "critical": None}
    m = re.search(r"(\d{1,7})\s+(?:tests?\s+)?passed", text, re.IGNORECASE)
    if m:
        out["passed"] = int(m.group(1))
    m = re.search(r"(\d{1,7})\s+(?:tests?\s+)?failed", text, re.IGNORECASE)
    if m:
        out["failed"] = int(m.group(1))
    m = re.search(r"(?:branch\s+)?coverage\D{0,12}(\d{1,3}(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if m:
        out["coverage_bps"] = _percent_to_bps(m.group(1))
    m = re.search(r"critical\D{0,24}(\d{1,5})", text, re.IGNORECASE)
    if m:
        out["critical"] = int(m.group(1))
    return out


def _select_attested_run(payload, check_name: str, app_id: int, pin_run_id: int = 0):
    """The newest check-run with this exact name from this exact GitHub App id.

    With `pin_run_id` only that very run qualifies: disputes re-judge the evidence the delivery
    was accepted on, so a later re-run of the check cannot change the outcome."""
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return None
    best = None
    best_id = -1
    for run in runs:
        if not isinstance(run, dict) or run.get("name") != check_name:
            continue
        app = run.get("app") if isinstance(run.get("app"), dict) else {}
        if _as_int(app.get("id"), -1) != app_id:
            continue
        run_id = _as_int(run.get("id"), 0)
        if pin_run_id and run_id != pin_run_id:
            continue
        if best is None or run_id > best_id:
            best, best_id = run, run_id
    return best


def _attested_metrics(run) -> dict:
    """CI state and metrics, read from the attested check-run's own output."""
    if run is None:
        return {"state": "none", "passed": None, "failed": None, "coverage_bps": None, "critical": None}
    if run.get("status") != "completed":
        state = "pending"
    elif str(run.get("conclusion")) == "success":
        state = "success"
    else:
        state = "failure"
    out = run.get("output") if isinstance(run.get("output"), dict) else {}
    text = f"{out.get('title') or ''}\n{out.get('summary') or ''}\n{out.get('text') or ''}"
    return {"state": state, **_parse_ci_text(text)}


def _report_mismatch(payload, names, sha: str, attested: dict) -> bool:
    """A committed report may only corroborate the attested check-run output."""
    if not isinstance(payload, dict):
        return True
    if str(payload.get("commit", "")).lower() != sha:
        return True
    claimed = payload.get("repository")
    if claimed is not None and str(claimed).lower() not in names:
        return True
    claims = {
        "passed": _as_int(payload.get("tests_passed")),
        "failed": _as_int(payload.get("tests_failed")),
        "critical": _as_int(payload.get("critical_findings")),
        "coverage_bps": None,
    }
    if payload.get("branch_coverage_bps") is not None:
        claims["coverage_bps"] = _as_int(payload.get("branch_coverage_bps"))
    elif payload.get("branch_coverage") is not None:
        claims["coverage_bps"] = _percent_to_bps(payload.get("branch_coverage"))
    present = {
        "passed": payload.get("tests_passed") is not None,
        "failed": payload.get("tests_failed") is not None,
        "critical": payload.get("critical_findings") is not None,
        "coverage_bps": payload.get("branch_coverage_bps") is not None or payload.get("branch_coverage") is not None,
    }
    for key, was_claimed in present.items():
        if was_claimed and claims[key] != attested[key]:
            return True
    return False


def _judge(ev: dict, min_tests: int, min_coverage_bps: int):
    """Apply the milestone invariants to collected evidence. Pure function."""
    if not ev["repo_available"]:
        return False, ["repo_unavailable"]
    failures = []
    if not ev["commit_exists"]:
        failures.append("commit_not_found")
    elif not ev["repo_match"]:
        failures.append("spoofed_payload")
    elif not ev["on_ref"]:
        failures.append("commit_not_on_ref")
    if ev["commit_exists"]:
        if ev["report_mismatch"]:
            failures.append(SPOOFED_REPORT)
        state = ev["ci_state"]
        if state == "none":
            failures.append("ci_attestation_missing")
        elif state == "pending":
            failures.append("ci_pending")
        else:
            if state == "failure":
                failures.append("ci_failed")
            if ev["tests_passed"] < min_tests:
                failures.append("tests_below_minimum")
            if ev["tests_failed"] != 0:
                failures.append("tests_failing")
            if ev["coverage_bps"] < min_coverage_bps:
                failures.append("coverage_below_minimum")
            if ev["critical_findings"] != 0:
                failures.append("critical_findings")
    failures = sorted(set(failures))
    return len(failures) == 0, failures


def _header(res, name: str) -> str:
    headers = getattr(res, "headers", None)
    if not headers:
        return ""
    try:
        for key, value in dict(headers).items():
            if str(key).lower() == name:
                return _to_text(value)
    except Exception:
        return ""
    return ""


def _is_rate_limited(res) -> bool:
    if _header(res, "x-ratelimit-remaining") == "0" or _header(res, "retry-after") != "":
        return True
    body = _to_text(res.body).lower()
    return "rate limit" in body or "abuse" in body or "secondary" in body


def _http_get(url: str):
    """GET with (bounded) redirect following and transient/external error classification."""
    target = url
    for _ in range(3):
        res = gl.nondet.web.get(target, headers=GITHUB_HEADERS)
        status = res.status
        if status in (301, 302, 307, 308):
            location = _header(res, "location")
            if location.startswith(GITHUB_ORIGIN):
                target = location
                continue
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} unresolved redirect {status}")
        if status == 403 and not _is_rate_limited(res):
            return status, _to_text(res.body)  # access blocked / disabled repository: a definitive answer
        if status == 429 or status == 403 or status >= 500:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} upstream status {status}")
        if status == 401:
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} upstream status {status}")
        return status, _to_text(res.body)
    raise gl.vm.UserError(f"{ERROR_TRANSIENT} too many redirects")


def _fetch_repo(url: str) -> dict:
    """Repository identity and availability. Deleted, private or blocked == unavailable."""
    status, text = _http_get(url)
    if status in (403, 404, 410, 451):
        return {"available": False, "id": 0, "full_name": ""}
    if status != 200:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} unexpected repository status {status}")
    data = _safe_json(text)
    if not isinstance(data, dict):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} malformed repository payload")
    repo_id = _as_int(data.get("id"), 0)
    return {
        "available": repo_id > 0 and not bool(data.get("private")),
        "id": repo_id,
        "full_name": str(data.get("full_name", "")),
    }


def _ref_contains(base: str, branch: str, delivery_ref: str, sha: str) -> bool:
    """Is the delivered commit part of the agreed delivery ref?

    * "" (default): the target branch's history, via the compare API.
    * "pull/N": the head commit of pull request N, which must target the agreed branch. The
      employer cannot veto delivery by refusing to merge, and cannot rewrite a PR head.
    * any other branch name in the repository: the commit is in that branch's history.
    """
    if delivery_ref.startswith("pull/"):
        status, text = _http_get(f"{base}/pulls/{delivery_ref[5:]}")
        if status != 200:
            return False
        data = _safe_json(text)
        if not isinstance(data, dict):
            return False
        head = data.get("head") if isinstance(data.get("head"), dict) else {}
        target = data.get("base") if isinstance(data.get("base"), dict) else {}
        return str(head.get("sha", "")).lower() == sha and target.get("ref") == branch
    ref = delivery_ref if delivery_ref != "" else branch
    status, text = _http_get(f"{base}/compare/{quote(ref, safe='/')}...{sha}")
    if status != 200:
        return False
    data = _safe_json(text)
    return isinstance(data, dict) and data.get("status") in ("identical", "behind")


def _collect_evidence(repo_id: int, repo_name: str, check_name: str, app_id: int, branch: str,
                      delivery_ref: str, pin_run_id: int, recheck: bool, sha: str,
                      min_tests: int, min_coverage_bps: int) -> dict:
    """Fetch and judge the delivery. Runs independently on every validator.

    recheck=True (disputes) judges the delivered SHA itself: ref containment is not re-evaluated
    (a rewritten or deleted branch tip must not overturn a valid delivery) and the check-run is
    the pinned run the delivery was accepted on.
    """
    ev = {
        "repo_id": repo_id,
        "repo_available": False,
        "commit_exists": False,
        "repo_match": False,
        "on_ref": False,
        "ref_checked": not recheck,
        "ci_state": "none",
        "check_run_id": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "coverage_bps": 0,
        "critical_findings": -1,
        "report_present": False,
        "report_mismatch": False,
        "report_source": "none",
    }
    info = _fetch_repo(f"{REPO_BY_ID}{repo_id}")
    ev["repo_available"] = info["available"] and info["id"] == repo_id
    if not ev["repo_available"]:
        ev["passed"], ev["failures"] = _judge(ev, min_tests, min_coverage_bps)
        return ev

    base = f"{REPO_BY_ID}{repo_id}"
    full = info["full_name"]
    lowered = full.lower()

    # 1. Commit authenticity ----------------------------------------------------
    status, text = _http_get(f"{base}/commits/{sha}")
    if status == 200:
        data = _safe_json(text)
        if isinstance(data, dict):
            ev["commit_exists"] = str(data.get("sha", "")).lower() == sha
            ev["repo_match"] = (
                ev["commit_exists"]
                and f"/repos/{lowered}/commits/" in str(data.get("url", "")).lower()
                and f"github.com/{lowered}/commit/" in str(data.get("html_url", "")).lower()
            )
    elif status not in (404, 422):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} unexpected commit status {status}")

    if ev["commit_exists"]:
        # 2. Delivery-ref containment (skipped on dispute re-checks: the SHA is what was delivered).
        if recheck:
            ev["on_ref"] = True
        else:
            ev["on_ref"] = _ref_contains(base, branch, delivery_ref, sha)

        # 3. The attested check-run: exact name + exact GitHub App id, bound to this commit.
        status, text = _http_get(f"{base}/commits/{sha}/check-runs?per_page=100")
        run = None
        if status == 200:
            run = _select_attested_run(_safe_json(text), check_name, app_id, pin_run_id)
        attested = _attested_metrics(run)
        ev["ci_state"] = attested["state"]
        if run is not None:
            ev["check_run_id"] = _as_int(run.get("id"), 0)
            ev["report_source"] = "check_run"
        if attested["passed"] is not None:
            ev["tests_passed"] = attested["passed"]
        if attested["failed"] is not None:
            ev["tests_failed"] = attested["failed"]
        if attested["coverage_bps"] is not None:
            ev["coverage_bps"] = attested["coverage_bps"]
        if attested["critical"] is not None:
            ev["critical_findings"] = attested["critical"]

        # 4. Optional committed report: may only corroborate the check-run, never override it.
        status, text = _http_get(f"{RAW_GITHUB}{full}/{sha}/{REPORT_PATH}")
        if status == 200:
            ev["report_present"] = True
            ev["report_mismatch"] = _report_mismatch(
                _safe_json(text), (lowered, repo_name.lower()), sha, attested
            )

    ev["passed"], ev["failures"] = _judge(ev, min_tests, min_coverage_bps)
    return ev


def _reports_agree(leader, mine) -> bool:
    if not isinstance(leader, dict) or not isinstance(mine, dict):
        return False
    keys = ("passed", "failures", "repo_available", "commit_exists", "on_ref", "ci_state", "check_run_id",
            "tests_passed", "tests_failed", "coverage_bps", "critical_findings", "report_mismatch")
    for key in keys:
        if leader.get(key) != mine.get(key):
            return False
    return True


def _make_validator(leader_fn, agree):
    """Validator side of a consensus round: re-run the leader's function, compare derived verdicts."""

    def validator_fn(leaders_res: gl.vm.Result) -> bool:
        if not isinstance(leaders_res, gl.vm.Return):
            leader_msg = getattr(leaders_res, "message", "")
            try:
                leader_fn()
                return False  # leader failed but the validator could verify
            except gl.vm.UserError as err:
                msg = getattr(err, "message", str(err))
                if msg.startswith(ERROR_EXPECTED) or msg.startswith(ERROR_EXTERNAL):
                    return msg == leader_msg
                return msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT)
            except Exception:
                return False
        try:
            mine = leader_fn()
        except gl.vm.UserError:
            return False
        return agree(leaders_res.calldata, mine)

    return validator_fn


def _probe_agree(leader, mine) -> bool:
    if not isinstance(leader, dict) or not isinstance(mine, dict):
        return False
    return leader.get("available") == mine.get("available") and leader.get("id") == mine.get("id")


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------
@allow_storage
@dataclass
class Milestone:
    escrow_id: u256
    index: u256
    title: str
    reward: u256
    bond: u256
    expected_sha: str
    check_name: str
    app_id: u256
    min_tests: u256
    min_coverage_bps: u256
    deadline: u256
    status: str
    submitted_sha: str
    delivery_ref: str
    check_run_id: u256
    attempts: u256
    pending_polls: u256
    verified_at: u256
    release_at: u256
    resubmit_until: u256
    frozen_at: u256
    consent_mask: u256
    dispute_count: u256
    last_report: str


@allow_storage
@dataclass
class Escrow:
    employer: Address
    contractor: Address
    repo: str
    repo_id: u256
    branch: str
    title: str
    bond_bps: u256
    total_reward: u256
    total_bond: u256
    milestone_count: u256
    open_milestones: u256
    first_milestone_id: u256
    created_at: u256
    status: str


class GitEscrow(gl.contract.Contract):
    escrows: TreeMap[u256, Escrow]
    milestones: TreeMap[u256, Milestone]
    strikes: TreeMap[str, u256]  # lost disputes per address (anti-griefing memory)

    escrow_count: u256
    milestone_total: u256
    active_escrows: u256

    total_in: u256  # every wei ever received
    total_paid_out: u256  # every wei ever sent out
    total_released: u256  # rewards paid to contractors
    total_slashed: u256  # bonds confiscated from defaulting contractors
    total_dispute_forfeited: u256  # failed dispute bonds paid to contractors
    fees_retained: u256  # non-refundable dispute fees, held by the contract for good

    def __init__(self):
        self.escrow_count = u256(0)
        self.milestone_total = u256(0)
        self.active_escrows = u256(0)
        self.total_in = u256(0)
        self.total_paid_out = u256(0)
        self.total_released = u256(0)
        self.total_slashed = u256(0)
        self.total_dispute_forfeited = u256(0)
        self.fees_retained = u256(0)

    # ------------------------------------------------------------------ utils
    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _fail(self, message: str):
        raise gl.vm.UserError(f"{ERROR_EXPECTED} {message}")

    def _pay(self, to: Address, amount: int) -> None:
        """Effects are applied by the caller first; this only books and queues the transfer."""
        if amount <= 0:
            return
        self.total_paid_out += amount
        gl.chain.Account(to).emit_transfer(amount, on="finalized")

    def _escrow(self, escrow_id: int) -> Escrow:
        if escrow_id < 1 or escrow_id > int(self.escrow_count):
            self._fail("unknown escrow")
        return self.escrows[u256(escrow_id)]

    def _milestone(self, milestone_id: int) -> Milestone:
        if milestone_id < 1 or milestone_id > int(self.milestone_total):
            self._fail("unknown milestone")
        return self.milestones[u256(milestone_id)]

    def _dispute_bond_for(self, m: Milestone, who_hex: str) -> int:
        base = max(MIN_DISPUTE_BOND, int(m.reward) * DISPUTE_BASE_BPS // BPS)
        strikes = int(self.strikes[who_hex]) if who_hex in self.strikes else 0
        exponent = int(m.dispute_count) + min(strikes, MAX_STRIKE_EXPONENT)
        return base << exponent

    def _dispute_fee_for(self, m: Milestone) -> int:
        return max(MIN_DISPUTE_FEE, int(m.reward) * DISPUTE_FEE_BPS // BPS)

    def _close_milestone(self, e: Escrow) -> None:
        e.open_milestones -= 1
        if int(e.open_milestones) == 0 and e.status == ST_ACTIVE:
            e.status = ST_CLOSED
            self.active_escrows -= 1

    def _release(self, e: Escrow, m: Milestone, extra_to_contractor: int = 0) -> None:
        reward = int(m.reward)
        bond = int(m.bond)
        m.status = MS_RELEASED
        self.total_released += reward
        self._close_milestone(e)
        self._pay(e.contractor, reward + bond + extra_to_contractor)

    def _verify(self, e: Escrow, m: Milestone, sha: str, delivery_ref: str, recheck: bool) -> dict:
        repo_id = int(e.repo_id)
        repo_name = e.repo
        branch = e.branch
        check_name = m.check_name
        app_id = int(m.app_id)
        pin = int(m.check_run_id) if recheck else 0
        min_tests = int(m.min_tests)
        min_cov = int(m.min_coverage_bps)

        def leader_fn():
            return _collect_evidence(repo_id, repo_name, check_name, app_id, branch, delivery_ref, pin,
                                     recheck, sha, min_tests, min_cov)

        return gl.vm.run_nondet(leader_fn, _make_validator(leader_fn, _reports_agree))

    def _probe(self, url: str) -> dict:
        def leader_fn():
            return _fetch_repo(url)

        return gl.vm.run_nondet(leader_fn, _make_validator(leader_fn, _probe_agree))

    def _repo_reachable(self, e: Escrow) -> bool:
        info = self._probe(f"{REPO_BY_ID}{int(e.repo_id)}")
        return bool(info["available"]) and int(info["id"]) == int(e.repo_id)

    def _record(self, m: Milestone, report: dict) -> None:
        m.last_report = json.dumps(report, sort_keys=True)

    def _extend_for_resubmit(self, m: Milestone, now: int) -> None:
        """Fresh delivery window: the contractor is never slashed for time lost to a dispute or an outage."""
        m.deadline = u256(max(int(m.deadline), now + RESUBMIT_GRACE))
        m.resubmit_until = u256(now + RESUBMIT_GRACE)

    # ------------------------------------------------------------- escrow flow
    @gl.public.write.payable
    def create_escrow(
        self,
        contractor_hex: str,
        repo: str,
        branch: str,
        title: str,
        bond_bps: u256,
        milestones_json: str,
    ) -> u256:
        """Employer funds every milestone reward up front (msg.value == sum of rewards)."""
        employer = gl.message.sender_address
        try:
            contractor = Address(contractor_hex)
        except Exception:
            self._fail("invalid contractor address")
        if contractor == employer:
            self._fail("employer and contractor must differ")
        if not REPO_RE.match(repo):
            self._fail("repo must look like owner/repo")
        if not BRANCH_RE.match(branch) or ".." in branch:
            self._fail("invalid branch name")
        if len(title) == 0 or len(title) > MAX_TITLE_LEN:
            self._fail("invalid escrow title")
        bps = int(bond_bps)
        if bps < MIN_BOND_BPS or bps > MAX_BOND_BPS:
            self._fail("bond_bps must be within 1000..2000")

        specs = _safe_json(milestones_json)
        if not isinstance(specs, list) or len(specs) == 0 or len(specs) > MAX_MILESTONES:
            self._fail(f"milestones must be a list of 1..{MAX_MILESTONES}")

        now = self._now()
        parsed = []
        total_reward = 0
        total_bond = 0
        for spec in specs:
            if not isinstance(spec, dict):
                self._fail("milestone must be an object")
            m_title = str(spec.get("title", ""))
            reward = _as_int(spec.get("reward"), 0)
            expected_sha = str(spec.get("expected_sha", "")).lower()
            check_name = str(spec.get("check_name", ""))
            app_id = _as_int(spec.get("app_id", DEFAULT_APP_ID), 0)
            min_tests = _as_int(spec.get("min_tests"), -1)
            min_cov = _as_int(spec.get("min_coverage_bps"), -1)
            deadline = _as_int(spec.get("deadline"), 0)
            if len(m_title) == 0 or len(m_title) > MAX_TITLE_LEN:
                self._fail("invalid milestone title")
            if reward <= 0:
                self._fail("milestone reward must be positive")
            if expected_sha != "" and not SHA_RE.match(expected_sha):
                self._fail("expected_sha must be empty or a full 40-hex commit")
            if len(check_name) == 0 or len(check_name) > MAX_CHECK_NAME_LEN:
                self._fail("check_name (the attested GitHub check-run) is required")
            if app_id <= 0:
                self._fail("app_id must be a positive GitHub App id")
            if min_tests < 0 or min_cov < 0 or min_cov > BPS:
                self._fail("invalid test or coverage threshold")
            if deadline <= now:
                self._fail("milestone deadline must be in the future")
            bond = reward * bps // BPS
            total_reward += reward
            total_bond += bond
            parsed.append((m_title, reward, bond, expected_sha, check_name, app_id, min_tests, min_cov, deadline))

        if int(gl.message.value) != total_reward:
            self._fail("msg.value must equal the sum of milestone rewards")

        self.escrow_count += 1
        escrow_id = int(self.escrow_count)
        first_id = int(self.milestone_total) + 1
        for index, p in enumerate(parsed):
            self.milestone_total += 1
            self.milestones[self.milestone_total] = Milestone(
                escrow_id=u256(escrow_id),
                index=u256(index),
                title=p[0],
                reward=u256(p[1]),
                bond=u256(p[2]),
                expected_sha=p[3],
                check_name=p[4],
                app_id=u256(p[5]),
                min_tests=u256(p[6]),
                min_coverage_bps=u256(p[7]),
                deadline=u256(p[8]),
                status=MS_PENDING,
                submitted_sha="",
                delivery_ref="",
                check_run_id=u256(0),
                attempts=u256(0),
                pending_polls=u256(0),
                verified_at=u256(0),
                release_at=u256(0),
                resubmit_until=u256(0),
                frozen_at=u256(0),
                consent_mask=u256(0),
                dispute_count=u256(0),
                last_report="",
            )
        self.escrows[u256(escrow_id)] = Escrow(
            employer=employer,
            contractor=contractor,
            repo=repo,
            repo_id=u256(0),
            branch=branch,
            title=title,
            bond_bps=u256(bps),
            total_reward=u256(total_reward),
            total_bond=u256(total_bond),
            milestone_count=u256(len(parsed)),
            open_milestones=u256(len(parsed)),
            first_milestone_id=u256(first_id),
            created_at=u256(now),
            status=ST_OPEN,
        )
        self.total_in += total_reward
        return u256(escrow_id)

    @gl.public.write.payable
    def accept_escrow(self, escrow_id: u256) -> None:
        """Contractor posts the bond. The quorum binds the repository's numeric id first."""
        e = self._escrow(int(escrow_id))
        if gl.message.sender_address != e.contractor:
            self._fail("only the contractor can accept")
        if e.status != ST_OPEN:
            self._fail("escrow is not open")
        if int(gl.message.value) != int(e.total_bond):
            self._fail("msg.value must equal the total performance bond")
        first = int(e.first_milestone_id)
        earliest = min(int(self.milestones[u256(first + i)].deadline) for i in range(int(e.milestone_count)))
        if self._now() >= earliest:
            self._fail("a milestone deadline already passed; cancel and recreate")

        info = self._probe(f"{REPO_BY_NAME}{e.repo}")
        if not info["available"]:
            self._fail("repository is not publicly accessible; nothing to verify")
        e.repo_id = u256(int(info["id"]))
        e.status = ST_ACTIVE
        self.active_escrows += 1
        self.total_in += int(gl.message.value)

    @gl.public.write
    def cancel_escrow(self, escrow_id: u256) -> None:
        """Employer withdraws an escrow the contractor never bonded into."""
        e = self._escrow(int(escrow_id))
        if gl.message.sender_address != e.employer:
            self._fail("only the employer can cancel")
        if e.status != ST_OPEN:
            self._fail("only unbonded escrows can be cancelled")
        refund = int(e.total_reward)
        e.status = ST_CANCELLED
        first = int(e.first_milestone_id)
        for i in range(int(e.milestone_count)):
            self.milestones[u256(first + i)].status = MS_CANCELLED
        e.open_milestones = u256(0)
        self._pay(e.employer, refund)

    # --------------------------------------------------------------- delivery
    @gl.public.write
    def evaluate_milestone_delivery(self, milestone_id: u256, commit_sha: str, delivery_ref: str) -> dict:
        """Contractor submits a commit; the validator quorum verifies it on GitHub.

        `delivery_ref` names where the commit lives: "" = the target branch, "pull/N" = the head of
        pull request N (which must target the agreed branch), or any branch of the repository.
        An unmerged PR with a green attested check-run is a valid delivery, so the employer
        cannot veto payment by declining to merge.

        A failing verdict is recorded (not reverted) so the failure reasons persist and the
        contractor may fix and resubmit. Attempts are conserved when the verdict only says
        "CI still running" (up to MAX_PENDING_POLLS) or the repository is unreachable;
        transient upstream faults (5xx, rate limits, unresolved 301) revert and cost nothing.
        A frozen milestone is revived when the repository answers again.
        """
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        if gl.message.sender_address != e.contractor:
            self._fail("only the contractor can submit delivery")
        if e.status != ST_ACTIVE:
            self._fail("escrow is not active")
        was_frozen = m.status == MS_FROZEN
        if m.status != MS_PENDING and not was_frozen:
            self._fail("milestone is not awaiting delivery")
        now = self._now()
        if not was_frozen and now > int(m.deadline):
            self._fail("milestone deadline expired")
        if not was_frozen and int(m.attempts) >= MAX_ATTEMPTS:
            self._fail("maximum delivery attempts reached")
        ref = delivery_ref.strip()
        if ref != "" and (not REF_RE.match(ref) or ".." in ref):
            self._fail("delivery_ref must be empty, a branch name, or pull/N")
        sha = commit_sha.strip().lower()
        if not SHA_RE.match(sha):
            self._fail("commit_sha must be a full 40-hex commit")
        if m.expected_sha != "" and sha != m.expected_sha:
            self._fail("commit does not match the contract's expected_sha")

        report = self._verify(e, m, sha, ref, False)
        report["sha"] = sha
        report["delivery_ref"] = ref
        report["evaluated_at"] = now

        if not report["repo_available"]:
            # External fault: never the contractor's fault, never an attempt, never a slash.
            if not was_frozen:
                m.status = MS_FROZEN
                m.frozen_at = u256(now)
                m.consent_mask = u256(0)
            report["fault"] = MS_FROZEN
            self._record(m, report)
            return report

        if was_frozen:
            m.status = MS_PENDING
            m.frozen_at = u256(0)
            m.consent_mask = u256(0)
            m.attempts = u256(min(int(m.attempts), MAX_ATTEMPTS - 1))  # a revived milestone is never a dead end
            self._extend_for_resubmit(m, now)

        m.submitted_sha = sha
        m.delivery_ref = ref
        only_pending = report["failures"] == ["ci_pending"]
        if only_pending and int(m.pending_polls) < MAX_PENDING_POLLS:
            m.pending_polls += 1
        else:
            m.attempts += 1
        self._record(m, report)
        if report["passed"]:
            m.check_run_id = u256(int(report["check_run_id"]))  # dispute re-checks stay pinned to this run
            m.status = MS_VERIFIED
            m.verified_at = u256(now)
            m.release_at = u256(now + DISPUTE_WINDOW)
        return report

    @gl.public.write
    def settle_milestone(self, milestone_id: u256) -> None:
        """Anyone may release a verified milestone once the dispute window has elapsed."""
        m = self._milestone(int(milestone_id))
        if m.status != MS_VERIFIED:
            self._fail("milestone is not verified")
        if self._now() < int(m.release_at):
            self._fail("dispute window still open")
        self._release(self.escrows[m.escrow_id], m)

    @gl.public.write
    def approve_milestone(self, milestone_id: u256) -> None:
        """Employer waives the remaining dispute window for an instant release."""
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        if gl.message.sender_address != e.employer:
            self._fail("only the employer can approve early")
        if m.status != MS_VERIFIED:
            self._fail("milestone is not verified")
        self._release(e, m)

    @gl.public.write
    def claim_default(self, milestone_id: u256) -> dict:
        """Past the deadline AND the resubmit grace: employer gets refund + slashed bond.

        The quorum first confirms the repository is reachable. If it is not, the employer
        cannot have caused the contractor's silence by deleting it: the milestone is frozen
        instead of slashed.
        """
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        if e.status != ST_ACTIVE:
            self._fail("escrow is not active")
        if m.status != MS_PENDING:
            self._fail("milestone is not awaiting delivery")
        now = self._now()
        if now <= int(m.deadline):
            self._fail("deadline has not passed")
        if now <= int(m.resubmit_until):
            self._fail("resubmit grace window still open")

        if not self._repo_reachable(e):
            m.status = MS_FROZEN
            m.frozen_at = u256(now)
            m.consent_mask = u256(0)
            return {"outcome": MS_FROZEN}

        reward = int(m.reward)
        bond = int(m.bond)
        m.status = MS_DEFAULTED
        self.total_slashed += bond
        self._close_milestone(e)
        self._pay(e.employer, reward + bond)
        return {"outcome": MS_DEFAULTED}

    @gl.public.write
    def thaw_milestone(self, milestone_id: u256) -> dict:
        """Anyone revives a frozen milestone once a quorum sees the repository again.

        The contractor gets a fresh window (deadline >= now + 72h) and at least one fresh attempt.
        """
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        if m.status != MS_FROZEN:
            self._fail("milestone is not frozen by an external fault")
        if not self._repo_reachable(e):
            self._fail("repository is still unreachable")
        now = self._now()
        m.status = MS_PENDING
        m.frozen_at = u256(0)
        m.consent_mask = u256(0)
        m.attempts = u256(min(int(m.attempts), MAX_ATTEMPTS - 1))
        self._extend_for_resubmit(m, now)
        return {"outcome": MS_PENDING, "deadline": int(m.deadline)}

    @gl.public.write
    def cancel_fault_free(self, milestone_id: u256) -> dict:
        """Neutral exit for a frozen milestone: employer refunded, contractor bond returned intact.

        Needs both parties' consent, or FREEZE_GRACE of freeze plus either a fresh quorum
        confirmation that the repository is still unreachable, or proof the milestone cannot make
        progress on its own (attempts exhausted or deadline elapsed). This is the guaranteed
        terminal path: a frozen milestone can never be deadlocked.
        """
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        sender = gl.message.sender_address
        if sender == e.employer:
            bit = 1
        elif sender == e.contractor:
            bit = 2
        else:
            self._fail("only the employer or the contractor can cancel")
        if m.status != MS_FROZEN:
            self._fail("milestone is not frozen by an external fault")

        mask = int(m.consent_mask) | bit
        m.consent_mask = u256(mask)
        if mask != 3:
            if self._now() < int(m.frozen_at) + FREEZE_GRACE:
                return {"outcome": "CONSENT_RECORDED", "consent_mask": mask}
            exhausted = int(m.attempts) >= MAX_ATTEMPTS or self._now() > int(m.deadline)
            if not exhausted and self._repo_reachable(e):
                self._fail("repository is reachable again; thaw_milestone or resubmit instead")

        reward = int(m.reward)
        bond = int(m.bond)
        m.status = MS_FAULT_CANCELLED
        self._close_milestone(e)
        self._pay(e.employer, reward)
        self._pay(e.contractor, bond)
        return {"outcome": MS_FAULT_CANCELLED}

    # ---------------------------------------------------------------- disputes
    @gl.public.write.payable
    def file_dispute(self, milestone_id: u256, reason: str) -> dict:
        """Employer challenges a verified milestone inside the 48h window.

        Cost = escalating refundable bond + non-refundable fee (3% of reward, retained
        by the contract). A fresh quorum re-judges the DELIVERED COMMIT ITSELF: it must still exist,
        belong to the repository, and its pinned check-run must still verify. The branch tip is not
        re-evaluated, so rewriting or deleting the branch after delivery cannot overturn it:
          * upheld     -> bond is forfeited to the contractor, milestone releases now
          * overturned -> bond refunded, milestone returns to PENDING and the contractor gets
                          a fresh 72h delivery window (deadline = max(deadline, now + 72h))
        A dispute cannot be decided while the repository is unreachable or CI is re-running.
        """
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        sender = gl.message.sender_address
        if sender != e.employer:
            self._fail("only the employer can dispute")
        if m.status != MS_VERIFIED:
            self._fail("milestone is not in a disputable state")
        now = self._now()
        if now >= int(m.release_at):
            self._fail("dispute window closed")
        if int(m.dispute_count) >= MAX_DISPUTES:
            self._fail("dispute limit reached")
        if len(reason) == 0 or len(reason) > MAX_REASON_LEN:
            self._fail("invalid dispute reason")
        who = sender.as_hex
        bond = self._dispute_bond_for(m, who)
        fee = self._dispute_fee_for(m)
        paid = int(gl.message.value)
        if paid != bond + fee:
            self._fail(f"dispute cost must be exactly {bond + fee} (bond {bond} + non-refundable fee {fee})")

        report = self._verify(e, m, m.submitted_sha, m.delivery_ref, True)
        if not report["repo_available"]:
            self._fail("repository is unreachable; a dispute cannot be decided")
        if report["failures"] == ["ci_pending"]:
            self._fail("CI is re-running; dispute once the check-run completes")

        self.total_in += paid
        self.fees_retained += fee
        m.dispute_count += 1
        report["sha"] = m.submitted_sha
        report["evaluated_at"] = now
        report["dispute_reason"] = reason

        if report["passed"]:
            self.strikes[who] = u256((int(self.strikes[who]) if who in self.strikes else 0) + 1)
            self.total_dispute_forfeited += bond
            report["dispute_outcome"] = "UPHELD_DELIVERY"
            self._record(m, report)
            self._release(e, m, extra_to_contractor=bond)
        else:
            report["dispute_outcome"] = "DELIVERY_OVERTURNED"
            self._record(m, report)
            m.status = MS_PENDING
            m.verified_at = u256(0)
            m.release_at = u256(0)
            m.attempts = u256(0)
            m.pending_polls = u256(0)
            self._extend_for_resubmit(m, now)
            self._pay(sender, bond)
        return report

    # ------------------------------------------------------------------- views
    def _milestone_view(self, mid: int) -> dict:
        m = self.milestones[u256(mid)]
        e = self.escrows[m.escrow_id]
        return {
            "id": mid,
            "escrow_id": int(m.escrow_id),
            "index": int(m.index),
            "title": m.title,
            "reward": int(m.reward),
            "bond": int(m.bond),
            "expected_sha": m.expected_sha,
            "check_name": m.check_name,
            "app_id": int(m.app_id),
            "min_tests": int(m.min_tests),
            "min_coverage_bps": int(m.min_coverage_bps),
            "deadline": int(m.deadline),
            "status": m.status,
            "submitted_sha": m.submitted_sha,
            "delivery_ref": m.delivery_ref,
            "check_run_id": int(m.check_run_id),
            "attempts": int(m.attempts),
            "pending_polls": int(m.pending_polls),
            "verified_at": int(m.verified_at),
            "release_at": int(m.release_at),
            "resubmit_until": int(m.resubmit_until),
            "frozen_at": int(m.frozen_at),
            "consent_mask": int(m.consent_mask),
            "dispute_count": int(m.dispute_count),
            "next_dispute_bond": self._dispute_bond_for(m, e.employer.as_hex),
            "next_dispute_fee": self._dispute_fee_for(m),
            "last_report": m.last_report,
        }

    def _escrow_view(self, eid: int) -> dict:
        e = self.escrows[u256(eid)]
        first = int(e.first_milestone_id)
        return {
            "id": eid,
            "employer": e.employer.as_hex,
            "contractor": e.contractor.as_hex,
            "repo": e.repo,
            "repo_id": int(e.repo_id),
            "branch": e.branch,
            "title": e.title,
            "bond_bps": int(e.bond_bps),
            "total_reward": int(e.total_reward),
            "total_bond": int(e.total_bond),
            "milestone_count": int(e.milestone_count),
            "open_milestones": int(e.open_milestones),
            "first_milestone_id": first,
            "created_at": int(e.created_at),
            "status": e.status,
            "milestones": [self._milestone_view(first + i) for i in range(int(e.milestone_count))],
        }

    @gl.public.view
    def get_escrow(self, escrow_id: u256) -> dict:
        self._escrow(int(escrow_id))
        return self._escrow_view(int(escrow_id))

    @gl.public.view
    def get_milestone(self, milestone_id: u256) -> dict:
        self._milestone(int(milestone_id))
        return self._milestone_view(int(milestone_id))

    @gl.public.view
    def list_escrows(self, offset: u256, limit: u256) -> list:
        total = int(self.escrow_count)
        start = int(offset) + 1
        end = min(total, start + min(int(limit), 50) - 1)
        return [self._escrow_view(i) for i in range(start, end + 1)]

    @gl.public.view
    def quote_dispute_bond(self, milestone_id: u256, who_hex: str) -> int:
        m = self._milestone(int(milestone_id))
        return self._dispute_bond_for(m, Address(who_hex).as_hex)

    @gl.public.view
    def quote_dispute_fee(self, milestone_id: u256) -> int:
        return self._dispute_fee_for(self._milestone(int(milestone_id)))

    @gl.public.view
    def get_strikes(self, who_hex: str) -> int:
        key = Address(who_hex).as_hex
        return int(self.strikes[key]) if key in self.strikes else 0

    def _liabilities(self) -> dict:
        locked = 0
        bonded = 0
        for mid in range(1, int(self.milestone_total) + 1):
            m = self.milestones[u256(mid)]
            if m.status in (MS_PENDING, MS_VERIFIED, MS_FROZEN):
                e = self.escrows[m.escrow_id]
                locked += int(m.reward)
                if e.status == ST_ACTIVE:
                    bonded += int(m.bond)
        return {"rewards": locked, "bonds": bonded}

    @gl.public.view
    def get_stats(self) -> dict:
        held = self._liabilities()
        return {
            "escrow_count": int(self.escrow_count),
            "milestone_count": int(self.milestone_total),
            "active_escrows": int(self.active_escrows),
            "tvl": held["rewards"] + held["bonds"],
            "locked_rewards": held["rewards"],
            "locked_bonds": held["bonds"],
            "total_released": int(self.total_released),
            "total_slashed": int(self.total_slashed),
            "total_dispute_forfeited": int(self.total_dispute_forfeited),
            "fees_retained": int(self.fees_retained),
        }

    @gl.public.view
    def get_solvency(self) -> dict:
        """Invariant: total_in == total_paid_out + liabilities + fees_retained."""
        held = self._liabilities()
        liabilities = held["rewards"] + held["bonds"]
        total_in = int(self.total_in)
        paid = int(self.total_paid_out)
        fees = int(self.fees_retained)
        return {
            "total_in": total_in,
            "total_paid_out": paid,
            "liabilities": liabilities,
            "fees_retained": fees,
            "solvent": total_in == paid + liabilities + fees,
        }
