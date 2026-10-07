# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

# GitEscrow - autonomous, code-verifiable milestone escrow on GenLayer.
#
# An employer locks milestone rewards in native GEN, a contractor stakes a
# performance bond (10-20% of each milestone), and delivery is judged by a
# GenVM validator quorum that independently inspects the submitted GitHub
# commit: its existence on the target branch, its CI check-runs, and a test /
# coverage / security report committed next to the code. No human arbiter.
#
# Lifecycle of a milestone:
#
#   PENDING --evaluate (consensus PASS)--> VERIFIED --48h window--> RELEASED
#      |                                      |
#      |                                      +--file_dispute (escalating bond)
#      |                                           upheld   -> RELEASED, bond to contractor
#      |                                           overturned -> PENDING, bond refunded
#      +--deadline passes undelivered--> DEFAULTED (employer: refund + slashed bond)
#
# Verification is fully deterministic given the commit SHA (every input is
# immutable GitHub data), so the validator function re-collects the evidence
# itself and demands the same derived verdict. No LLM is involved: nothing the
# contractor controls is ever interpreted as an instruction.

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
MAX_ATTEMPTS = 5  # delivery evaluations per milestone (bounds validator cost)
DISPUTE_WINDOW = 48 * 3600  # seconds a verified milestone stays challengeable
MAX_DISPUTES = 3  # disputes per milestone
MIN_DISPUTE_BOND = ATTO // 10  # 0.1 GEN floor
DISPUTE_BASE_BPS = 100  # base dispute bond = 1% of the milestone reward
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

GITHUB_API = "https://api.github.com/repos/"
RAW_GITHUB = "https://raw.githubusercontent.com/"
REPORT_PATH = ".gitescrow/report.json"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "GitEscrow-GenVM",
}

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

CI_FAILING = ("failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale")


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


def _summarize_check_runs(payload) -> dict:
    """Derive a stable CI state and counters from a check-runs API payload."""
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or len(runs) == 0:
        return {"state": "none", "passed": None, "failed": None, "coverage_bps": None, "critical": None}
    state = "success"
    best = {"passed": None, "failed": None, "coverage_bps": None, "critical": None}
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("status") != "completed":
            if state != "failure":
                state = "pending"
            continue
        if str(run.get("conclusion")) in CI_FAILING:
            state = "failure"
        out = run.get("output") if isinstance(run.get("output"), dict) else {}
        text = f"{out.get('title') or ''}\n{out.get('summary') or ''}\n{out.get('text') or ''}"
        parsed = _parse_ci_text(text)
        for key in best:
            val = parsed[key]
            if val is None:
                continue
            # max for positive counters (never double-count), min for coverage
            # is not safe either way, so keep the best reported value too.
            best[key] = val if best[key] is None else max(best[key], val)
    return {"state": state, **best}


def _read_report(payload, repo: str, sha: str) -> dict:
    """Validate a committed .gitescrow/report.json. Spoofed payloads are flagged."""
    result = {"present": False, "spoofed": False, "passed": None, "failed": None,
              "coverage_bps": None, "critical": None}
    if not isinstance(payload, dict):
        result["present"] = True
        result["spoofed"] = True
        return result
    result["present"] = True
    claimed_commit = str(payload.get("commit", "")).lower()
    if claimed_commit != sha:
        result["spoofed"] = True
    claimed_repo = payload.get("repository")
    if claimed_repo is not None and str(claimed_repo).lower() != repo.lower():
        result["spoofed"] = True
    if result["spoofed"]:
        return result
    result["passed"] = _as_int(payload.get("tests_passed"))
    result["failed"] = _as_int(payload.get("tests_failed"))
    if payload.get("branch_coverage_bps") is not None:
        result["coverage_bps"] = _as_int(payload.get("branch_coverage_bps"))
    elif payload.get("branch_coverage") is not None:
        result["coverage_bps"] = _percent_to_bps(payload.get("branch_coverage"))
    result["critical"] = _as_int(payload.get("critical_findings"))
    return result


def _judge(ev: dict, min_tests: int, min_coverage_bps: int):
    """Apply the milestone invariants to collected evidence. Pure function."""
    failures = []
    if not ev["commit_exists"]:
        failures.append("commit_not_found")
    elif not ev["repo_match"] or ev["spoofed"]:
        failures.append("spoofed_payload")
    elif not ev["on_branch"]:
        failures.append("commit_not_on_branch")
    if ev["commit_exists"]:
        if ev["ci_state"] == "pending":
            failures.append("ci_pending")
        elif ev["ci_state"] == "none":
            failures.append("ci_missing")
        elif ev["ci_state"] == "failure":
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


def _http_get(url: str):
    """GET with transient/external error classification. Returns (status, text)."""
    res = gl.nondet.web.get(url, headers=GITHUB_HEADERS)
    status = res.status
    if status == 429 or status == 403 or status >= 500:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} upstream status {status}")
    if status == 401:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} upstream status {status}")
    return status, _to_text(res.body)


def _collect_evidence(repo: str, branch: str, sha: str, min_tests: int, min_coverage_bps: int) -> dict:
    """Fetch and judge the delivery. Runs independently on every validator."""
    base = f"{GITHUB_API}{repo}"
    ev = {
        "commit_exists": False,
        "repo_match": False,
        "on_branch": False,
        "spoofed": False,
        "ci_state": "none",
        "tests_passed": 0,
        "tests_failed": 0,
        "coverage_bps": 0,
        "critical_findings": -1,
        "report_source": "none",
    }

    # 1. Commit authenticity -------------------------------------------------
    status, text = _http_get(f"{base}/commits/{sha}")
    if status == 200:
        data = _safe_json(text)
        if isinstance(data, dict):
            lowered = repo.lower()
            ev["commit_exists"] = str(data.get("sha", "")).lower() == sha
            ev["repo_match"] = (
                ev["commit_exists"]
                and f"/repos/{lowered}/commits/" in str(data.get("url", "")).lower()
                and f"github.com/{lowered}/commit/" in str(data.get("html_url", "")).lower()
            )
    elif status not in (404, 422):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} unexpected commit status {status}")

    if ev["commit_exists"]:
        # 2. Branch containment: the commit must be an ancestor of (or equal to) the branch tip.
        status, text = _http_get(f"{base}/compare/{quote(branch, safe='/')}...{sha}")
        if status == 200:
            data = _safe_json(text)
            if isinstance(data, dict):
                ev["on_branch"] = data.get("status") in ("identical", "behind")

        # 3. CI check-runs bound to this exact commit.
        status, text = _http_get(f"{base}/commits/{sha}/check-runs?per_page=100")
        ci = {"state": "none", "passed": None, "failed": None, "coverage_bps": None, "critical": None}
        if status == 200:
            ci = _summarize_check_runs(_safe_json(text))
        ev["ci_state"] = ci["state"]

        # 4. Test / coverage / security report committed at the delivery SHA.
        status, text = _http_get(f"{RAW_GITHUB}{repo}/{sha}/{REPORT_PATH}")
        report = {"present": False, "spoofed": False, "passed": None, "failed": None,
                  "coverage_bps": None, "critical": None}
        if status == 200:
            report = _read_report(_safe_json(text), repo, sha)
        ev["spoofed"] = report["spoofed"]

        source = report if (report["present"] and not report["spoofed"]) else ci
        ev["report_source"] = "report" if source is report else ("check_runs" if ci["state"] != "none" else "none")
        if source["passed"] is not None:
            ev["tests_passed"] = source["passed"]
        if source["failed"] is not None:
            ev["tests_failed"] = source["failed"]
        elif source["passed"] is None:
            ev["tests_failed"] = 0
        if source["coverage_bps"] is not None:
            ev["coverage_bps"] = source["coverage_bps"]
        if source["critical"] is not None:
            ev["critical_findings"] = source["critical"]

    passed, failures = _judge(ev, min_tests, min_coverage_bps)
    ev["passed"] = passed
    ev["failures"] = failures
    return ev


def _reports_agree(leader, mine) -> bool:
    if not isinstance(leader, dict) or not isinstance(mine, dict):
        return False
    keys = ("passed", "failures", "commit_exists", "on_branch", "spoofed", "ci_state",
            "tests_passed", "tests_failed", "coverage_bps", "critical_findings")
    for key in keys:
        if leader.get(key) != mine.get(key):
            return False
    return True


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
    min_tests: u256
    min_coverage_bps: u256
    deadline: u256
    status: str
    submitted_sha: str
    attempts: u256
    verified_at: u256
    release_at: u256
    dispute_count: u256
    last_report: str


@allow_storage
@dataclass
class Escrow:
    employer: Address
    contractor: Address
    repo: str
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

    def __init__(self):
        self.escrow_count = u256(0)
        self.milestone_total = u256(0)
        self.active_escrows = u256(0)
        self.total_in = u256(0)
        self.total_paid_out = u256(0)
        self.total_released = u256(0)
        self.total_slashed = u256(0)
        self.total_dispute_forfeited = u256(0)

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

    def _verify(self, repo: str, branch: str, sha: str, min_tests: int, min_cov: int) -> dict:
        def leader_fn():
            return _collect_evidence(repo, branch, sha, min_tests, min_cov)

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
            return _reports_agree(leaders_res.calldata, mine)

        return gl.vm.run_nondet(leader_fn, validator_fn)

    def _record(self, m: Milestone, report: dict) -> None:
        m.last_report = json.dumps(report, sort_keys=True)

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
            min_tests = _as_int(spec.get("min_tests"), -1)
            min_cov = _as_int(spec.get("min_coverage_bps"), -1)
            deadline = _as_int(spec.get("deadline"), 0)
            if len(m_title) == 0 or len(m_title) > MAX_TITLE_LEN:
                self._fail("invalid milestone title")
            if reward <= 0:
                self._fail("milestone reward must be positive")
            if expected_sha != "" and not SHA_RE.match(expected_sha):
                self._fail("expected_sha must be empty or a full 40-hex commit")
            if min_tests < 0 or min_cov < 0 or min_cov > BPS:
                self._fail("invalid test or coverage threshold")
            if deadline <= now:
                self._fail("milestone deadline must be in the future")
            bond = reward * bps // BPS
            total_reward += reward
            total_bond += bond
            parsed.append((m_title, reward, bond, expected_sha, min_tests, min_cov, deadline))

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
                min_tests=u256(p[4]),
                min_coverage_bps=u256(p[5]),
                deadline=u256(p[6]),
                status=MS_PENDING,
                submitted_sha="",
                attempts=u256(0),
                verified_at=u256(0),
                release_at=u256(0),
                dispute_count=u256(0),
                last_report="",
            )
        self.escrows[u256(escrow_id)] = Escrow(
            employer=employer,
            contractor=contractor,
            repo=repo,
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
        """Contractor posts the performance bond (sum of milestone bonds) and starts the clock."""
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
    def evaluate_milestone_delivery(self, milestone_id: u256, commit_sha: str) -> dict:
        """Contractor submits a commit; the validator quorum verifies it on GitHub.

        A failing verdict is recorded (not reverted) so the attempt counter and the
        failure reasons persist; the contractor may fix and resubmit until the deadline.
        """
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        if gl.message.sender_address != e.contractor:
            self._fail("only the contractor can submit delivery")
        if e.status != ST_ACTIVE:
            self._fail("escrow is not active")
        if m.status != MS_PENDING:
            self._fail("milestone is not awaiting delivery")
        now = self._now()
        if now > int(m.deadline):
            self._fail("milestone deadline expired")
        if int(m.attempts) >= MAX_ATTEMPTS:
            self._fail("maximum delivery attempts reached")
        sha = commit_sha.strip().lower()
        if not SHA_RE.match(sha):
            self._fail("commit_sha must be a full 40-hex commit")
        if m.expected_sha != "" and sha != m.expected_sha:
            self._fail("commit does not match the contract's expected_sha")

        report = self._verify(e.repo, e.branch, sha, int(m.min_tests), int(m.min_coverage_bps))
        m.attempts += 1
        m.submitted_sha = sha
        report["sha"] = sha
        report["evaluated_at"] = now
        self._record(m, report)
        if report["passed"]:
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
    def claim_default(self, milestone_id: u256) -> None:
        """Past the deadline with no verified delivery: employer gets refund + slashed bond."""
        m = self._milestone(int(milestone_id))
        e = self.escrows[m.escrow_id]
        if e.status != ST_ACTIVE:
            self._fail("escrow is not active")
        if m.status != MS_PENDING:
            self._fail("milestone is not awaiting delivery")
        if self._now() <= int(m.deadline):
            self._fail("deadline has not passed")
        reward = int(m.reward)
        bond = int(m.bond)
        m.status = MS_DEFAULTED
        self.total_slashed += bond
        self._close_milestone(e)
        self._pay(e.employer, reward + bond)

    # ---------------------------------------------------------------- disputes
    @gl.public.write.payable
    def file_dispute(self, milestone_id: u256, reason: str) -> dict:
        """Employer challenges a verified milestone inside the 48h window.

        The bond doubles with every dispute on the milestone and with every dispute
        this address already lost. A fresh quorum re-verifies the same commit:
          * upheld     -> bond is forfeited to the contractor, milestone releases now
          * overturned -> bond refunded, milestone returns to PENDING for resubmission
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
        required = self._dispute_bond_for(m, who)
        bond_paid = int(gl.message.value)
        if bond_paid != required:
            self._fail(f"dispute bond must be exactly {required}")

        self.total_in += bond_paid
        m.dispute_count += 1
        report = self._verify(e.repo, e.branch, m.submitted_sha, int(m.min_tests), int(m.min_coverage_bps))
        report["sha"] = m.submitted_sha
        report["evaluated_at"] = now
        report["dispute_reason"] = reason
        self._record(m, report)

        if report["passed"]:
            self.strikes[who] = u256((int(self.strikes[who]) if who in self.strikes else 0) + 1)
            self.total_dispute_forfeited += bond_paid
            self._release(e, m, extra_to_contractor=bond_paid)
            report["dispute_outcome"] = "UPHELD_DELIVERY"
        else:
            m.status = MS_PENDING
            m.verified_at = u256(0)
            m.release_at = u256(0)
            self._pay(sender, bond_paid)
            report["dispute_outcome"] = "DELIVERY_OVERTURNED"
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
            "min_tests": int(m.min_tests),
            "min_coverage_bps": int(m.min_coverage_bps),
            "deadline": int(m.deadline),
            "status": m.status,
            "submitted_sha": m.submitted_sha,
            "attempts": int(m.attempts),
            "verified_at": int(m.verified_at),
            "release_at": int(m.release_at),
            "dispute_count": int(m.dispute_count),
            "next_dispute_bond": self._dispute_bond_for(m, e.employer.as_hex),
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
    def get_strikes(self, who_hex: str) -> int:
        key = Address(who_hex).as_hex
        return int(self.strikes[key]) if key in self.strikes else 0

    @gl.public.view
    def get_stats(self) -> dict:
        locked = 0
        bonded = 0
        for mid in range(1, int(self.milestone_total) + 1):
            m = self.milestones[u256(mid)]
            if m.status in (MS_PENDING, MS_VERIFIED):
                e = self.escrows[m.escrow_id]
                locked += int(m.reward)
                if e.status == ST_ACTIVE:
                    bonded += int(m.bond)
        return {
            "escrow_count": int(self.escrow_count),
            "milestone_count": int(self.milestone_total),
            "active_escrows": int(self.active_escrows),
            "tvl": locked + bonded,
            "locked_rewards": locked,
            "locked_bonds": bonded,
            "total_released": int(self.total_released),
            "total_slashed": int(self.total_slashed),
            "total_dispute_forfeited": int(self.total_dispute_forfeited),
        }

    @gl.public.view
    def get_solvency(self) -> dict:
        """Invariant: total_in == total_paid_out + liabilities (nothing is ever created or lost)."""
        liabilities = int(self.get_stats()["tvl"])
        total_in = int(self.total_in)
        paid = int(self.total_paid_out)
        return {
            "total_in": total_in,
            "total_paid_out": paid,
            "liabilities": liabilities,
            "solvent": total_in == paid + liabilities,
        }
