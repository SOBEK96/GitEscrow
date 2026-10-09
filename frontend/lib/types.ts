// Shared domain types. Amounts are wei-scale bigints (1 GEN = 10^18).

export type EscrowStatus = "OPEN" | "ACTIVE" | "CLOSED" | "CANCELLED";
export type MilestoneStatus =
  | "FUNDED" | "SUBMITTED" | "FINALIZED" | "DISPUTED" | "DEFAULTED" | "CANCELLED"
  | "FROZEN_EXTERNAL_FAULT" | "CANCELLED_FAULT_FREE";

export type CiState = "success" | "failure" | "pending" | "none";

/** Verification report a validator quorum registered for one delivery attempt. */
export interface Report {
  passed: boolean;
  failures: string[];
  repo_id?: number;
  repo_available: boolean;
  commit_exists: boolean;
  repo_match: boolean;
  on_ref: boolean;
  ref_checked?: boolean;
  descends_from_baseline?: boolean;
  ci_config_tampered?: boolean;
  tests_removed?: boolean;
  rigged_tests?: boolean;
  diff_too_large?: boolean;
  diff_findings?: string[];
  changed_files?: number;
  review_done?: boolean;
  review_implements?: boolean;
  review_provenance?: boolean;
  review_reason?: string;
  delivery_ref?: string;
  check_run_id?: number;
  ci_state: CiState;
  tests_passed: number;
  tests_failed: number;
  coverage_bps: number;
  critical_findings: number; // -1 means "could not be verified"
  report_present: boolean;
  report_mismatch: boolean;
  report_source: "check_run" | "none";
  fault?: string;
  sha?: string;
  evaluated_at?: number;
  dispute_reason?: string;
  dispute_outcome?: "UPHELD_DELIVERY" | "DELIVERY_OVERTURNED";
}

export interface Milestone {
  id: number;
  escrowId: number;
  index: number;
  title: string;
  description: string;
  reward: bigint;
  bond: bigint;
  /** Wei the contract still holds for this milestone; exactly 0 once it is paid out or otherwise terminal. */
  escrowed: bigint;
  paidOut: bigint;
  finalizedAt: number;
  expectedSha: string;
  checkName: string;
  appId: number;
  minTests: number;
  minCoverageBps: number;
  deadline: number;
  status: MilestoneStatus;
  submittedSha: string;
  deliveryRef: string;
  checkRunId: number;
  attempts: number;
  pendingPolls: number;
  verifiedAt: number;
  releaseAt: number;
  resubmitUntil: number;
  frozenAt: number;
  consentMask: number;
  disputeCount: number;
  nextDisputeBond: bigint;
  nextDisputeFee: bigint;
  lastReport: Report | null;
}

export interface Escrow {
  id: number;
  employer: string;
  contractor: string;
  repo: string;
  repositoryUrl: string;
  baselineCommitSha: string;
  repoId: number;
  branch: string;
  title: string;
  bondBps: number;
  totalReward: bigint;
  totalBond: bigint;
  openMilestones: number;
  createdAt: number;
  status: EscrowStatus;
  milestones: Milestone[];
}

export interface Stats {
  escrowCount: number;
  activeEscrows: number;
  tvl: bigint;
  lockedRewards: bigint;
  lockedBonds: bigint;
  totalReleased: bigint;
  totalSlashed: bigint;
  totalDisputeForfeited: bigint;
  feesRetained: bigint;
}

export interface Solvency {
  totalIn: bigint;
  totalPaidOut: bigint;
  liabilities: bigint;
  feesRetained: bigint;
  solvent: boolean;
}

export interface ValidatorVote {
  validator: string;
  role: "leader" | "validator";
  vote: "AGREE" | "DISAGREE";
  note: string;
}

export interface ConsensusRound {
  round: number;
  leaderClaim: "PASS" | "FAIL";
  votes: ValidatorVote[];
  accepted: boolean;
}

export interface ConsensusTrace {
  source: "receipt";
  rounds: ConsensusRound[];
  txHash?: string;
}

export interface Outcome {
  report: Report;
  trace: ConsensusTrace;
}

export interface MilestoneInput {
  title: string;
  description: string;
  reward: bigint;
  expectedSha: string;
  checkName: string;
  appId: number;
  minTests: number;
  minCoverageBps: number;
  deadline: number;
}

export interface CreateEscrowInput {
  contractor: string;
  /** Exactly https://github.com/<owner>/<repo> */
  repositoryUrl: string;
  /** Full 40-hex commit every delivery must strictly descend from. */
  baselineCommitSha: string;
  branch: string;
  title: string;
  bondBps: number;
  milestones: MilestoneInput[];
}

export interface Actor {
  id: string;
  label: string;
  address: string;
}

export interface Backend {
  label: string;
  actors(): Actor[];
  now(): number;
  subscribe(fn: () => void): () => void;
  listEscrows(): Promise<Escrow[]>;
  stats(): Promise<Stats>;
  solvency(): Promise<Solvency>;
  createEscrow(by: string, input: CreateEscrowInput): Promise<number>;
  acceptEscrow(by: string, escrowId: number): Promise<void>;
  cancelEscrow(by: string, escrowId: number): Promise<void>;
  evaluate(by: string, milestoneId: number, sha: string, deliveryRef?: string): Promise<Outcome>;
  settle(by: string, milestoneId: number): Promise<void>;
  approve(by: string, milestoneId: number): Promise<void>;
  claimDefault(by: string, milestoneId: number): Promise<string>;
  cancelFaultFree(by: string, milestoneId: number): Promise<string>;
  thaw(by: string, milestoneId: number): Promise<string>;
  quoteDisputeBond(milestoneId: number, who: string): Promise<bigint>;
  quoteDisputeFee(milestoneId: number): Promise<bigint>;
  fileDispute(by: string, milestoneId: number, reason: string): Promise<Outcome>;
}
