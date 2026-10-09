# GitEscrow

**Autonomous, code-verifiable milestone escrow on [GenLayer](https://genlayer.com).**

An employer locks milestone rewards in GEN and pins the exact `repository_url` and a `baseline_commit_sha`. A contractor stakes a 10-20% performance bond. When the contractor submits a GitHub commit (signed with their wallet), a quorum of GenVM validators **independently** inspects it - is it a strict descendant of the baseline, does it live in that repository and on the delivery ref, did it leave CI untouched, is the attested CI check-run for that very commit green, how many tests pass, what is the branch coverage, are there critical findings, does the diff genuinely implement the milestone description - and the contract pays out, or doesn't, based on that verdict. No multisig, no arbiter, no "client went silent".

```
contracts/git_escrow.py   the intelligent contract (GenVM, Python)
tests/                    direct-mode pytest: test_git_escrow.py (protocol behaviour) + test_adversarial.py (exploits that must revert)
frontend/                 Next.js + Tailwind dashboard (live against Studio Next)
scripts/                  deploy.py, verify_live.py, simulate_dispute.py
deployments/              recorded Studio Next deployment
```

---

## Architecture

```mermaid
flowchart LR
    subgraph Stake["1 · Dual staking"]
        E[Employer] -- "rewards (GEN)" --> X[(GitEscrow)]
        C[Contractor] -- "bond 10-20%" --> X
    end
    C -- "2 · commit SHA" --> V
    subgraph V["3 · GenVM multi-validator verification  (run_nondet)"]
        L[Leader] --> G[(GitHub API<br/>commit · compare · check-runs · report)]
        V1[Validator] --> G
        V2[Validator] --> G
        V3[Validator] --> G
        L -.verdict.-> Q{{"same derived verdict?"}}
        V1 -.-> Q
        V2 -.-> Q
        V3 -.-> Q
    end
    Q -- PASS --> W["48h dispute window"]
    Q -- FAIL --> R["retry until deadline<br/>(max 5 attempts)"]
    W -- "no dispute / employer approves" --> P["4 · Autonomous payout<br/>reward + bond -> contractor"]
    W -- "dispute + escalating bond" --> Q2{{"fresh quorum re-verifies"}}
    Q2 -- "delivery holds" --> P
    Q2 -- "overturned" --> R
    R -- "deadline passes" --> D["Default: employer gets<br/>refund + slashed bond"]
```

```
 Employer ──reward──┐                                         ┌── PASS ──► 48h window ──► RELEASE
                    ├─► [ GitEscrow ] ◄─commit SHA─ Contractor│                  │ dispute (1x,2x,4x bond)
 Contractor ──bond──┘        │                                │                  ▼
                             ▼                                │          fresh quorum re-check
              ┌─────────────────────────────┐                 │         holds ──► RELEASE (bond -> contractor)
              │ GenVM validators (5)        │─────────────────┤         fails ──► reopen, bond refunded
              │ each fetches GitHub itself  │                 │
              │ leader proposes, rest verify│                 └── FAIL ──► retry … deadline ──► DEFAULT
              └─────────────────────────────┘                                      employer: refund + slashed bond
```

### What validators check (`evaluate_milestone_delivery`)

Every gate except the last is a deterministic function of **immutable data bound to the commit SHA** plus the live availability of the repository, so independent validators converge on the same answer. The final gate is an **LLM provenance review** that is deliberately limited to a *veto*: it only runs after every deterministic gate has passed, it can never rescue a commit those gates rejected, the contractor-authored diff and CI text reach it inside fenced "untrusted data" blocks with instructions to treat any embedded instruction as bad faith, and validators must reach the same boolean verdicts (`implements_milestone`, `ci_provenance_ok`) or the round rotates. A dispute re-check does not re-sample the model: the verdict the consensus accepted at submission is carried over.

The repository is bound by its **numeric GitHub id** when the contractor accepts (a consensus round resolves `owner/repo` to an id, following 301 redirects). Every later call goes through `/repositories/{id}`, so a rename or transfer never breaks evaluation.

| # | Check | Source | Failure code |
|---|-------|--------|--------------|
| 0 | Repository reachable (not deleted, private or blocked) | `GET /repositories/{id}` | `repo_unavailable` -> **frozen**, never slashed |
| 1 | Commit exists, payload belongs to this repository | `GET /repositories/{id}/commits/{sha}` (`sha`, `url`, `html_url` must match the repository's current `full_name`) | `commit_not_found`, `spoofed_payload` (both **revert**) |
| 1a | **Baseline ancestry** - the delivered commit is strictly *ahead* of the escrow's `baseline_commit_sha` (the baseline itself, ancestors, diverged histories, unrelated roots all fail) | `GET .../compare/{baseline}...{sha}` (`status == ahead`, `behind_by == 0`) | `not_descendant_of_baseline` (**reverts**) |
| 1b | **CI untouched** - no file under `.github/` added / modified / removed / renamed in the baseline...delivery diff; no test file deleted or moved out of the test tree; no always-green construct added to tests or test tooling (`assert True`, `\|\| true`, `exit 0`, `pytest.mark.skip/xfail`, `it.skip`, `continue-on-error`, `passWithNoTests`, `pytest_collection_modifyitems`, `--deselect`, ...); a diff of 300+ files is unauditable and fails closed | same compare response (`files[].patch`) | `ci_config_tampered`, `tests_removed`, `rigged_tests` (**revert**), `diff_too_large` |
| 2 | Commit is in the **delivery ref** (see below) | `delivery_ref` = `""` target branch -> `compare/{branch}...{sha}`; `pull/N` -> `GET /pulls/N` (head SHA must equal the commit, base must be the agreed branch); any other branch -> `compare/{ref}...{sha}` | `commit_not_on_ref` |
| 3 | **Attested CI**: the check-run with the milestone's exact `check_name`, created by the exact `app_id` (default `15368` = GitHub Actions), **executed against exactly the delivered commit** (`head_sha`), completed with `success`; newest re-run wins | `GET .../commits/{sha}/check-runs` | `ci_attestation_missing`, `ci_pending`, `ci_failed` |
| 4 | Passing tests >= minimum, zero failing | parsed from **that check-run's output** (`120 passed`, `0 failed`) | `tests_below_minimum`, `tests_failing` |
| 5 | Branch coverage >= minimum | same check-run output (`Branch coverage: 91.5%`) | `coverage_below_minimum` |
| 6 | Zero critical lint / vulnerability findings (unknown counts as **not clean**) | same check-run output (`Critical issues: 0`) | `critical_findings` |
| 7 | Optional `.gitescrow/report.json` must **strictly match** the check-run | `raw.githubusercontent.com/{repo}/{sha}/.gitescrow/report.json` | `SPOOFED_REPORT_PAYLOAD` (**reverts**) |
| 8 | **LLM provenance review** (veto only): the diff implements the milestone's `description`, and the CI output credibly comes from genuinely exercising it (counts and coverage plausible for the diff, tests not neutered / skipped / stubbed) | `gl.nondet.exec_prompt` over the diff, check-run output and description | `milestone_not_implemented` (recorded, costs an attempt), `ci_provenance_rejected` (**reverts**), `review_missing` |

**Revert vs. record.** A verdict containing a *provenance violation* (`commit_not_found`, `spoofed_payload`, `SPOOFED_REPORT_PAYLOAD`, `not_descendant_of_baseline`, `ci_config_tampered`, `tests_removed`, `rigged_tests`, `ci_provenance_rejected`) reverts the transaction with `submission rejected: <codes>`: nothing is recorded, the submission signature's nonce is not burned, no attempt is consumed and no payout path moves. Ordinary shortfalls (too few tests, low coverage, CI red/pending, `milestone_not_implemented`) are recorded in `last_report` and consume an attempt as before.

**Delivery refs - no merge veto.** `evaluate_milestone_delivery(milestone, sha, delivery_ref)` takes the place the commit lives. The default `""` is the agreed target branch. `pull/N` verifies the commit as the **head of pull request N**, which must target the agreed branch, so an *unmerged* PR whose attested check-run is green is a valid delivery: the employer cannot veto payment by declining to merge, closing the PR, or rewriting `main`. A contractor may also name any other branch of the repository. The ref only has to *contain* the commit; trust comes from the attested check-run bound to that SHA.

**Disputes judge the delivered SHA, not the branch.** When a delivery is disputed, validators re-check only: the commit still exists and belongs to the repository, and the **pinned check-run** (the exact run id recorded at submission) still verifies with the same thresholds. Ref containment is deliberately *not* re-evaluated, and re-runs of the check (new run ids) are ignored. An employer who force-pushes, resets or deletes the branch after delivery, closes the PR, or re-triggers CI therefore cannot overturn a valid delivery. A dispute can only succeed if the evidence the delivery was accepted on has actually changed (the pinned run now reports failure, the run or the commit is gone).

A contractor-committed `report.json` can **never** substitute for, or improve on, the CI evidence: if it is present it may only corroborate. Any claimed metric (tests, failures, coverage, critical findings) that differs from the check-run, a missing check-run behind a populated report, or a report naming another commit or repository fails the delivery with `SPOOFED_REPORT_PAYLOAD`. Check-runs from other apps or with other names are ignored entirely. Example of the attested check-run summary the parser reads:

```
120 passed, 0 failed. Branch coverage: 91.5%. Critical issues: 0
```

> **Workflow requirement: publish results through the Checks API.** A stock GitHub Actions job conclusion (`success`/`failure`) is not enough: validators parse the check-run's `output.title` / `output.summary` / `output.text`. Your workflow must therefore publish its test, coverage and security numbers there, either with a step that calls the Checks API (`POST /repos/{repo}/check-runs` or `PATCH /repos/{repo}/check-runs/{id}` with `output: { title, summary }`), or with an action that writes a test summary into the check-run output in the format above (`N passed`, `N failed`, `Branch coverage: X%`, `Critical issues: N`). The check-run's **name** must equal the milestone's `check_name` and it must be created by the milestone's `app_id` (`15368` for the built-in `GITHUB_TOKEN` / GitHub Actions). A job that only writes to the step summary (`$GITHUB_STEP_SUMMARY`) or uploads an artifact is invisible to the validators and will fail with `ci_attestation_missing` or `tests_below_minimum`.

The validator function re-collects the evidence itself and requires the leader's `passed`, `failures`, repository availability, test count, coverage, CI state, report-mismatch flag, baseline-ancestry flag, tamper / rig flags and review verdicts to match exactly. A leader that forges a PASS, inflates one number, hides a report mismatch or fakes a repository outage is voted down and the round rotates. Errors are classified (`[EXPECTED]`, `[EXTERNAL]`, `[TRANSIENT]`) so rate limits, 5xx and unresolved 301s never convert into a verdict or a penalty.

**Attempt conservation.** A delivery attempt is consumed only by a real verdict. Transient faults revert (nothing consumed). A verdict that is purely `ci_pending` costs a free *CI poll* (up to 20 per milestone, then it counts). An unreachable repository freezes the milestone and costs nothing.

### Milestone state machine

```
FUNDED ──evaluate(sha, ref, signature): PASS──► SUBMITTED ──settle (after 48h) / approve (signed by employer)──► FINALIZED
   │  ▲                                          │
   │  │  DISPUTED ◄── dispute OVERTURNED         │  dispute UPHELD (the delivered SHA + pinned check-run
   │  │  (re-submit allowed from FUNDED and      │  still verify, whatever happened to the branch)
   │  │  DISPUTED): deadline := max(deadline,    └──► FINALIZED (disputer's bond -> contractor)
   │  │  now + 72h), attempts reset
   │
   ├── deadline + resubmit grace passed, repo reachable, claim_default ──► DEFAULTED (refund + slashed bond)
   │
   └── repo deleted / private / blocked (claim_default or evaluate) ──► FROZEN_EXTERNAL_FAULT
            ├─ thaw_milestone (anyone) or evaluate, once the repo answers
            │     ──► FUNDED: deadline >= now + 72h, at least ONE fresh attempt (attempts <= 4)
            └─ cancel_fault_free ──► CANCELLED_FAULT_FREE (employer refunded, bond returned intact)
                  * both parties consent, or
                  * after 7 days: repo still unreachable, OR attempts exhausted, OR deadline elapsed

(OPEN escrow, employer cancel ──► CANCELLED)
```

**One finalizer, one payout.** Every wei a milestone holds lives in its `escrowed` field. All terminal transitions begin with `_debit`, which reads `escrowed`, sets it to **0**, and reverts if it was already 0 - *before* any transfer is queued. `FINALIZED` is reached only through `_finalize`, which additionally requires the status to be `SUBMITTED` and `paid_out == 0`, then records `paid_out` and `finalized_at`. A finalized milestone therefore holds exactly 0 wei, cannot be settled, approved, disputed, evaluated, defaulted, thawed or cancelled again (each path reverts with `already finalized` / its own state guard), and `get_solvency()` reconciles `total_in == total_paid_out + Σ escrowed + fees_retained` after every path. A `SUBMITTED` milestone cannot be re-evaluated either, so a contractor cannot swap in a different commit after a delivery was verified.

**No milestone can be deadlocked.** Every non-terminal state has a path that does not depend on the counterparty: `OPEN` -> employer `cancel_escrow`; `FUNDED` / `DISPUTED` -> delivery, or `claim_default` after the deadline; `SUBMITTED` -> `settle_milestone` after 48h (no repo access needed); `FROZEN` -> `thaw_milestone` / `evaluate`, or after the 7-day cooldown a unilateral `cancel_fault_free` (the cancel is refused only while the repo is reachable *and* the contractor still has attempts *and* time left, in which case thaw/resubmit is the path). A frozen milestone whose attempts are spent can always be thawed (a fresh attempt is granted) or cancelled. Funds therefore always end in one of: contractor payout, employer refund + slash, or a fault-free refund.

### Contract surface

| Method | Who | What |
|---|---|---|
| *constructor* `(signing_chain_id)` | deployer | the chain id every authorization signature is bound to (`61997` on Studio Next) |
| `create_escrow(contractor, repository_url, branch, title, bond_bps, baseline_commit_sha, milestones_json)` *payable* | employer | deposits `sum(rewards)`; `repository_url` must be exactly `https://github.com/<owner>/<repo>`; `baseline_commit_sha` is a full 40-hex commit; each milestone carries a required `description`; 1-10 milestones, bond 1000-2000 bps |
| `accept_escrow(id)` *payable* | contractor | posts `sum(bonds)`; consensus binds the repository's numeric id **and** checks the baseline is a real commit of that repository that the agreed branch descends from |
| `cancel_escrow(id)` | employer | refund while the contractor has not bonded |
| `evaluate_milestone_delivery(milestone, sha, delivery_ref, nonce, expires_at, signature)` | contractor's **signature** (any relayer) | **consensus verification** of the commit on the target branch / a branch / `pull/N`; records the report and pins the check-run |
| `settle_milestone(id)` | anyone | release after the 48h window |
| `approve_milestone(id, nonce, expires_at, signature)` | employer's **signature** (any relayer) | waive the window, finalize now |
| `file_dispute(id, reason)` *payable* | employer | escalating bond + non-refundable 3% fee; fresh quorum re-verification; overturn grants a 72h resubmit window |
| `claim_default(id)` | anyone | after deadline **and** resubmit grace: probes the repo, then refund + slashed bond to the employer (or freezes if the repo is unreachable) |
| `thaw_milestone(id)` | anyone | revive a frozen milestone once the repo answers (fresh 72h window, >= 1 fresh attempt) |
| `cancel_fault_free(id)` | employer / contractor | neutral exit for a frozen milestone: employer refunded, bond returned intact |
| `get_escrow`, `get_milestone`, `list_escrows`, `get_stats`, `get_solvency`, `quote_dispute_bond`, `quote_dispute_fee`, `get_strikes`, `get_nonce(address)`, `get_signing_domain()`, `authorization_message(action, milestone, sha, ref, nonce, expires)` | views | |

### Wallet signatures (injected-wallet authorization)

Client approvals and contractor submissions are authorised by an **EIP-191 `personal_sign`** signature that the contract verifies itself. The signed text is rebuilt byte for byte from contract state (`authorization_message` returns it):

```
GitEscrow authorization v1
action: SUBMIT | APPROVE
chain: 61997
contract: 0x<this contract, lower-case>
milestone: <id>
commit: <40-hex>          (APPROVE: the commit that was verified)
ref: <delivery ref>
nonce: <signer's next nonce>
expires: <unix seconds, at most 7 days ahead>
```

The contract computes `keccak256("\x19Ethereum Signed Message:\n" + len + text)`, recovers the secp256k1 signer in pure Python (both implemented in the contract and checked against `eth_account` / `eth_utils` in the test-suite), and requires it to equal the registered **contractor** (SUBMIT) or **employer** (APPROVE). Signatures with a wrong length, `r`/`s` out of range, high-`s` (malleable) form or an unknown recovery id are rejected; a signature is bound to one contract, chain, milestone, action, commit, ref, nonce and expiry; the nonce is per signer and burned when the action commits, so replays fail with `stale or future nonce`. Because the *signature*, not `msg.sender`, authorises the action, anyone may relay it. The frontend asks the injected wallet for `personal_sign` (hex-encoded text), recovers the signer locally and refuses to send a transaction the contract would revert (`frontend/lib/authorization.ts`, pinned to a Python-produced vector in `authorization.test.ts`).

---

## Game theory & solvency

Notation: reward `R`, bond `b·R` with `b ∈ [10%, 20%]`, contractor's cost of doing the work `c`. The bonding table and the repository-trust analysis are in *Game Theory, Repo Trust Trilemma & Known Limitations* below.

### Slashing conditions

* **Default** - milestone still `FUNDED` / `DISPUTED` after its deadline *and* any resubmit grace window, **and** a consensus probe confirms the repository is reachable: 100% of that milestone's bond goes to the employer together with the refunded reward. Callable by anyone.
* A **failed verification is not slashing**. A rejected delivery costs only an attempt (max 5); the contractor can fix and resubmit until the deadline.
* **Submitted milestones cannot be defaulted**, even after the deadline: the contractor delivered in time.
* **External faults are never slashing.** A deleted, private or blocked repository freezes the milestone instead (see below).

### Escalating dispute bonds and the non-refundable fee (anti-griefing)

The employer has no discretionary veto - the only way to contest a verified delivery is `file_dispute` inside the 48h window, and it costs two things:

```
bond = max(0.1 GEN, 1% of reward) × 2^(disputes_on_this_milestone + min(strikes, 4))   (refundable if the delivery is overturned)
fee  = max(0.02 GEN, 3% of reward)                                                       (never refunded; held by the contract for good)
```

| Dispute # on a 100 GEN milestone | Bond | Fee | Total paid |
|---|---|---|---|
| 1st | 1 GEN | 3 GEN | 4 GEN |
| 2nd | 2 GEN | 3 GEN | 5 GEN |
| 3rd | 4 GEN (no 4th: hard cap of 3) | 3 GEN | 7 GEN |
| 1st, by an address with 1 lost dispute | 2 GEN | 3 GEN | 5 GEN |
| 1st, by an address with 4+ lost disputes | 16 GEN | 3 GEN | 19 GEN |

* A dispute is resolved **in the same transaction** by a fresh quorum re-verification of the same commit, so it cannot freeze funds - the worst-case delay is one consensus round, not a time lock.
* **Upheld delivery** (the dispute was frivolous): the bond is forfeited *to the contractor* as compensation, the fee is retained, and the milestone is finalized immediately. The disputer earns a strike that raises every future bond for that address.
* **Overturned delivery** (e.g. history force-pushed after verification): the bond is refunded, the **fee is not**, and the milestone becomes `DISPUTED` (open for re-delivery) with `deadline = max(deadline, now + 72h)` and a fresh attempt budget. `claim_default` is blocked until that window has passed, so an employer cannot disprove a delivery after the deadline and then slash the bond before the contractor can react.
* A dispute **cannot be decided** while the repository is unreachable or the pinned check-run is `pending` - the employer cannot win by deleting the repo or re-triggering CI. It also **cannot be won by rewriting the branch**: validators judge the delivered SHA and its pinned check-run, not the branch tip.

The fee makes a zero-cost harassment dispute impossible: every dispute burns at least 3% of the milestone regardless of outcome, the bond ladder bounds spam on one milestone, and strikes raise the price for repeat offenders.

### Solvency invariant

Every wei is accounted for in exactly one of three buckets, and `get_solvency()` exposes the check on-chain:

```
total_in  ==  total_paid_out  +  liabilities  +  fees_retained
liabilities = Σ over all milestones of `escrowed`  (reward before the contractor bonds, reward + bond after; 0 once terminal)
```

| Transition | In | Out | Liabilities |
|---|---|---|---|
| create_escrow | +ΣR | | +ΣR |
| accept_escrow | +Σb·R | | +Σb·R |
| finalize | | R + b·R | −(R + b·R) |
| claim_default | | R + b·R (employer) | −(R + b·R) |
| cancel_escrow | | ΣR | −ΣR |
| cancel_fault_free | | R (employer) + b·R (contractor) | −(R + b·R) |
| file_dispute (upheld) | +bond +fee | R + b·R + bond | −(R + b·R), fees +fee |
| file_dispute (overturned) | +bond +fee | bond | fees +fee |

State is updated *before* any transfer is queued (checks-effects-interactions), and the test-suite asserts the invariant after every settlement path.

## Game Theory, Repo Trust Trilemma & Known Limitations

### The Repository Trilemma

Someone must control the repository, and whoever does can influence the evidence. The three options trade off differently:

| Repository owner | Failure mode | What GitEscrow does about it |
|---|---|---|
| **Employer-owned** (contractor delivers via PR) | The employer controls `main`, the branch protections and, unless pinned, the workflow files. They can refuse to merge, close the PR, force-push, delete the repo, make it private, or edit CI configuration | **Delivery is verified at the commit level, on the delivery ref.** A PR head (`pull/N`) with a green attested check-run is a valid delivery whether or not it is ever merged, so refusing to merge cannot block payment. Branch rewrites after delivery are ignored by disputes (the SHA and its pinned check-run are what is judged). Repo deletion / privatisation freezes the milestone instead of defaulting it, and ends in a fault-free refund out (employer refunded, bond returned). **Residual risk, disclosed:** if the employer also controls the branch *and the CI configuration* (workflow files on the base branch, required-workflow settings, the app that publishes the check-run), they can make the attested check fail for a genuinely good commit before delivery is evaluated. Protection then rests only on commit-level SHA verification plus the fault-free refund exit: the contractor is never slashed for it (an unreachable repo freezes; a failing check just burns an attempt), but can be denied *payment*. Mitigate by pinning the CI workflow in the agreed commit, using a neutral GitHub App `app_id`, or a neutral org. |
| **Contractor-owned** | The contractor controls code, workflow and report, so they can forge the evidence, or delete the repo to dodge a slash | Evidence is bound to a **named check-run from a pinned GitHub App id**; a committed `report.json` can only corroborate it (any mismatch is `SPOOFED_REPORT_PAYLOAD`). Deletion yields the same neutral freeze, so it earns the contractor a free exit, never the employer's money. |
| **Neutral third party** (org both sides trust) | Needs a third party; fully removes the incentive to tamper | Recommended whenever the stakes justify it. |

GitEscrow therefore does not claim to remove the need for repo trust. It removes the **profitable** abuses: a party that destroys the evidence cannot slash the other side, a party that refuses to merge cannot block a verified delivery, and a party that fabricates evidence cannot beat an attested check-run. The unavoidable residual risk is whoever controls the CI that publishes the check-run: a contractor-owned repo lets the contractor make that workflow print flattering numbers; an employer-owned repo lets the employer make it fail. Neither side can steal the other's *funds* through it (worst cases: a free fault-free exit, or an unpaid-but-unslashed contractor), but pick the owner, or a neutral GitHub App, accordingly.

**Neutral fault-free cancellation.** Deleting, privatising or blocking the repository (404/410/451, a non-rate-limit 403, or a `private` repo) freezes the milestone. Anyone can `thaw_milestone` once the repo answers, or the contractor can simply resubmit: the deadline is extended to at least `now + 72h` and at least one attempt is restored, so a frozen milestone never lands in a state where every call reverts. Otherwise either party may `cancel_fault_free`: immediately if both consent, or after a 7-day cooldown if a fresh quorum round still finds the repo unreachable, **or** the attempts are exhausted, **or** the deadline has elapsed. The employer's deposit is refunded and the contractor's bond is returned 100% intact; nobody is slashed and nobody is paid.

### Bonding dynamics and slashing

| Contractor action | Contractor payoff | Employer payoff |
|---|---|---|
| Delivers valid code in time | `R − c` (bond returned) | the deliverable |
| Ghosts / misses the deadline (repo reachable) | `−c_sunk − b·R` | `R + b·R` (full refund **plus** slashed bond) |
| Submits junk repeatedly | nothing; capped at 5 attempts, then default | unaffected |
| Deletes the repo to dodge the slash | bond returned, no reward (neutral exit) | deposit refunded, no deliverable |

Delivering is a dominant strategy whenever `R > c`: ghosting costs the bond, and the neutral-exit route only returns the contractor to where they started. The documented weakness is exactly that last row - on a contractor-owned repo a bad-faith contractor can buy a free exit by deleting it. If that matters, own the repo on the employer side or use a neutral org.

### Mutable off-chain state and timing attacks

Branch heads, check-run states and repository visibility are **mutable off-chain**: a branch can be force-pushed after verification, a check-run can be re-run, a repo can disappear. The defences are structural rather than clock-based:

* **72-hour dispute-resubmit window.** If a dispute overturns a delivery, `deadline = max(deadline, now + 72h)` and attempts reset, and `claim_default` stays blocked until the new deadline passes. An employer who waits for the original deadline to expire, force-pushes, disputes, and then tries to harvest the bond gets nothing: the contractor has three full days to resubmit.
* **Non-refundable arbitration fee** (3%, floor 0.02 GEN) on every dispute, on top of the escalating refundable bond. A dispute that is cheap to file is a free option on the contractor's money; with the fee it is a guaranteed loss unless the delivery was genuinely invalidated.
* **Re-run and rewrite attacks.** Disputes are pinned to the check-run recorded at delivery, so re-running CI (a new run id) does nothing; they never re-evaluate the branch, so force-pushing, resetting or deleting it does nothing; and they are refused while the pinned run is `pending` or the repository is unreachable. The only way to overturn is for the *pinned evidence itself* to change (the app updates that run to a failure, or the run or commit disappears), in which case the contractor gets the 72h resubmit window.
* **Branch tip is irrelevant after delivery.** Ref containment is checked once, at submission. After that the delivery stands on its SHA.

### Commit Baseline & Author Binding

Each escrow is created against an exact `repository_url` and a `baseline_commit_sha`, and the contractor's `accept_escrow` consensus round proves the baseline is a real commit of that repository which the agreed branch descends from. Every delivery must then be a **strict descendant** of the baseline (`compare/{baseline}...{sha}` reports `ahead`, `behind_by == 0`). This closes the replay hole: an already-existing green commit, the baseline itself, an ancestor of it, a commit on a diverged history, or a third-party PR on an unrelated root can never satisfy a milestone. The same check runs again, deterministically, when a delivery is disputed.

What it still does **not** do, and the employer should understand before funding:

* **Author attestation.** Nothing verifies the Git author, committer or a GPG/SSH signature on the commit. A contractor-signed submission proves *who submitted* the SHA, not who wrote it. A descendant of the baseline that someone else authored (for example a third-party fork-network commit that also happens to be on the delivery ref, or a PR by another developer) can still be submitted.
* **Pre-delivery work.** If the employer's own branch already contains unreleased work *after* the baseline that meets the thresholds, it counts as a descendant. Pick the baseline as the tip the work should start from, and keep thresholds incremental.
* **Fork-network objects.** GitHub serves commits from a repository's fork network through the parent's API; containment on the delivery ref (`on_ref`) is what excludes them, and a `pull/N` ref must be that PR's head targeting the agreed branch.
* **Semantic judgement is probabilistic.** Whether the diff "implements the description" is decided by an LLM review quorum that can only *veto*. A determined contractor can try prompt injection through the diff (it is fenced and flagged as untrusted, and an injection can only turn a veto into a pass if every deterministic gate already held), so keep rewards proportionate and use `expected_sha` for fixed deliverables.

### Check-Run Persistence & Workflow Control

* **Check-runs must stay on the GitHub Checks API.** Validators read the check-run's `output` at verification time and again, pinned by run id, if the delivery is disputed. If a check-run is deleted or its app is uninstalled, a re-check finds no pinned run and reports `ci_attestation_missing`, which is the one thing that can overturn an already verified delivery. Keep the CI app installed and do not prune check-runs for delivered SHAs until the milestone is `FINALIZED`.
* **Why an employer-owned repo with a pinned `check_name` matters.** The attested check-run is only as trustworthy as whoever controls the workflow that produces it. In a contractor-owned repo the contractor writes the workflow and can make it print any numbers under the right name. In an employer-owned repo, the employer fixes the workflow and the exact `check_name` / `app_id` in the milestone, so a contractor PR cannot invent a check-run that validators will accept: check-runs from other names or other apps are ignored outright, and a committed `report.json` can only corroborate the real one.
* **The workflow file is protected by the diff audit.** For the plain `pull_request` trigger GitHub runs the workflow definition from the PR's own merge commit, so a contractor PR could edit the workflow that reports its own results. Validators therefore reject (revert) any delivery whose baseline...commit diff touches `.github/`, deletes or relocates test files, or adds always-green constructs to tests or test tooling, and require the attested check-run to have executed against exactly the delivered commit. **Residual risk, disclosed:** the audit sees what changed *in the repository between baseline and delivery*. It cannot see repository or organisation settings (rulesets, required workflows, secrets), a workflow that is pulled in remotely, behaviour hidden in application code that detects CI and fakes success, or a hostile `.github/` already present *at* the baseline. The employer should still protect the base branch with required workflows or a neutral GitHub App `app_id`, and treat the LLM review as a second line of defence, not a proof.

### Public Recovery (`thaw_milestone`)

`thaw_milestone` is deliberately **permissionless**: anyone (employer, contractor, a third-party keeper or a bot) can call it. Its only effect is to move a `FROZEN_EXTERNAL_FAULT` milestone back to `FUNDED` once a validator quorum confirms the repository is reachable again, granting a fresh 72h window and at least one fresh attempt. It never moves funds, never slashes, and reverts unless the milestone is frozen *and* the repo currently answers, so it cannot be used to grief either side. This exists so that an outage which resolves itself cannot leave a milestone stranded waiting for a specific party to notice: the recovery needs no cooperation from the contractor or the employer. If nobody thaws it, the 7-day `cancel_fault_free` exit still guarantees a terminal outcome.

### GitHub API rate limits near close deadlines

Validators call the unauthenticated GitHub API (60 requests/hour/IP). A rate-limit 403 (header or body says so), 429, 5xx or unresolved 301 is classified `[TRANSIENT]`: the transaction reverts, **no attempt is consumed, and the contractor is never slashed for it**. But `claim_default`, `cancel_fault_free` and `accept_escrow` also need a successful probe, so an outage or rate-limit burst just delays them. Contractors should submit well before a deadline rather than in the last minutes, because a delivery that cannot be verified in time cannot be defended on-chain (the 72h window only starts after an overturned dispute).

### Other limits

* **Payout transfers are queued `on="finalized"`** after the accepting transaction, per GenVM semantics.
* Dispute fees are held by the contract with no withdrawal path (equivalent to burning); `get_stats().fees_retained` reports the total.
* The contract has not been audited.

---

## Setup

Requirements: Python ≥ 3.11, Node ≥ 20, [`genvm-lint`](https://pypi.org/project/genvm-lint/), and the GenLayer Python packages.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt genvm-lint
```

### Lint and test the contract

```bash
genvm-lint check contracts/git_escrow.py     # lint + SDK validation: 0 errors
pytest                                       # direct-mode tests: protocol suite + adversarial suite (in-memory GenVM, under a minute)
```

> If `pytest` fails during fixture setup with `runner py-genlayer:... not under .../trees-v2/<version>`, the direct-mode loader resolved a GenVM release that is not cached locally. Pin one that is: `GENVM_VERSION=v0.6.0-rc8 pytest` (and `GENVM_VERSION=v0.6.0-rc8 genvm-lint check contracts/git_escrow.py`).

`tests/test_adversarial.py` is the **adversarial direct-mode suite**. Each exploit class must revert, and every negative test also proves nothing moved (milestone status, attempts, held funds and the solvency ledger are identical before and after):

| Exploit | What the tests throw at the contract |
|---|---|
| a) pre-baseline / replayed commit | `behind`, `diverged`, `identical` and no-common-ancestor comparisons; `ahead` with `behind_by > 0`; the baseline SHA itself; a PR-ref smuggle; a baseline that is not on the agreed branch at `accept`; the same check re-run on a dispute |
| b) commit from an unrelated repository | a SHA unknown to the bound repository id, payloads naming other repositories, a report for another repository, a swapped numeric id, fork-network objects off the branch, a PR head that is another commit |
| c) CI rigged to always pass | every change under `.github/` (added / modified / removed / renamed), 19 always-green constructs across test files and test tooling, deleted or relocated tests, a 300-file unauditable diff, a check-run that ran on a different commit, LLM veto (`ci_provenance_rejected`), malformed model output (fails closed), the model never consulted when a deterministic gate fails |
| d) invalid wallet signatures | malformed / zero / wrong-length signatures, a stranger, the wrong role (employer signs a SUBMIT, contractor signs an APPROVE), signatures redirected to another milestone / commit / ref / action / contract / chain, tampered r / s / v, high-`s` malleability, out-of-range `r`/`s`, bad recovery ids, expired and over-long expiry, changed nonce or expiry after signing, future nonce, replay after consumption |
| e) double payout | settle twice, approve after settle, settle after approve, re-evaluate, dispute, default, thaw and cancel after `FINALIZED`; swapping the commit after `SUBMITTED`; default / cancel twice; every terminal status holds exactly 0 wei; the ledger is unchanged across a barrage of post-finalization attacks |

`TestCryptoParity` checks the contract's pure-Python `keccak256` and `ecrecover` against `eth_utils` / `eth_account` on 30 random keys (both recovery parities). Mutation checks - disabling the signer comparison, nonce check, baseline ancestry, the finalized guard, the balance zeroing, the check-run `head_sha` binding, the workflow-tamper detector, the rig scanner, the revert-on-violation rule and the low-`s` rule one at a time - each make the suite fail (the only surviving mutant is the redundant `paid_out != 0` belt-and-braces check in `_finalize`, which `_debit` and the `SUBMITTED` status guard already make unreachable).

The protocol suite covers deposit math and the bond lock-up, a passing delivery with parsed test/coverage metrics, every rejection path (too few tests, failing tests, low coverage, critical findings, missing commit, off-branch commit, CI red/pending/missing, expired deadline, spoofed report, spoofed repository payload), default + slashing, the escalating dispute ladder and strikes, and validator agreement/disagreement against swapped GitHub mocks (`direct_vm.run_validator`).

### Adversarial simulation (no network, no keys)

```bash
python scripts/simulate_dispute.py
```

A contractor throws a ghost commit, a pre-baseline replay, an unrelated repository, a rigged workflow, an always-true test, a forged report, a stolen check-run, forged wallet signatures and a double-payout attempt at the escrow; every one is rejected. Then an honest validator votes `DISAGREE` against a leader that forged a PASS, and a ghosting contractor is slashed.

### Dashboard

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
npm test               # bond / dispute rules pinned to the Python numbers; wallet authorization pinned to a Python-signed vector
```

The dashboard is live-only: it reads and writes the contract on Studio Next (address in `frontend/lib/config.ts`) through `genlayer-js`.

* **Connect Wallet** uses any injected EIP-1193 wallet (`window.ethereum`, e.g. MetaMask). It requests your account, then adds or switches to *GenLayer Studio Next* (chain id `61997` / `0xf22d`, symbol `GEN`, RPC `https://studio-next.genlayer.com/api`) and signs every transaction in the wallet. Submitting a delivery and approving a milestone additionally ask the wallet for a gas-free `personal_sign` authorization (`lib/authorization.ts`); the dashboard recovers the signer locally and refuses to send a transaction the contract would reject. A "Switch to Studio Next" button appears if the wallet drifts to another chain. Disconnect returns to the logged-out state.
* **Demo / Quick Test** (only offered when no wallet extension is installed) generates a throwaway key that lives in this browser only.
* **Fund** credits 1000 GEN from the Studio faucet to the connected address. The faucet (`sim_fundAccount`) needs a positive *integer* wei amount and keys balances by exact address casing, so the amount is sent as a BigInt-derived decimal string and the address is EIP-55 checksummed first. Studio rate-limits an IP to 30 RPC requests per minute; the dashboard polls every 45s and backs off automatically on `-32029`.

Panels: escrow explorer + creator (repo, branch, milestones, thresholds, bond slider, deadline pickers), TVL / dual-staking / solvency strip, milestone progress bar and submission card, live consensus telemetry (per-check results, validator votes), and the dispute & settlement terminal (countdown, claim settlement, default claim, escalating bond ladder).

### Deploy to GenLayer Studio Next

```bash
python -m scripts.deploy                    # deploy (constructor arg = signing chain id 61997), seed demo escrows, export artifacts
python scripts/verify_live.py lifecycle \
    --repo-url https://github.com/OWNER/REPO --branch main \
    --baseline BASELINE_SHA --sha DELIVERY_SHA \
    --old-sha SHA_BEFORE_BASELINE --foreign-sha SHA_FROM_ANOTHER_REPO
python scripts/verify_live.py default       # failure path: baseline replay refused -> deadline -> default & slash
```

`deploy.py` writes `deployments/studio-next.json`, `frontend/lib/gitescrow.generated.json` (address + ABI) and updates `CONTRACT_ADDRESS` in `frontend/lib/config.ts`, which drives the dashboard and footer links. Test keys are generated into `deployments/.keys/` (git-ignored) and funded from the Studio faucet; override with `GITESCROW_EMPLOYER_KEY` / `GITESCROW_CONTRACTOR_KEY`.

`verify_live.py lifecycle` prints every transaction hash with its explorer link, including the **reverted** attack transactions, so the output is the reviewer's evidence trail. It needs a repository whose CI publishes the `ci/tests` check-run: `examples/gitescrow-ci.yml` is a template. Commit it in the baseline (a delivery may not touch `.github/`).
