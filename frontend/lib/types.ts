// Shared domain types. Amounts are wei-scale bigints (1 GEN = 10^18).

export type EscrowStatus = "OPEN" | "ACTIVE" | "CLOSED" | "CANCELLED";
export type MilestoneStatus = "PENDING" | "VERIFIED" | "RELEASED" | "DEFAULTED" | "CANCELLED";

export type CiState = "success" | "failure" | "pending" | "none";

/** Verification report a validator quorum registered for one delivery attempt. */
export interface Report {
  passed: boolean;
  failures: string[];
  commit_exists: boolean;
  repo_match: boolean;
  on_branch: boolean;
  spoofed: boolean;
  ci_state: CiState;
  tests_passed: number;
  tests_failed: number;
  coverage_bps: number;
  critical_findings: number; // -1 means "could not be verified"
  report_source: "report" | "check_runs" | "none";
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
  reward: bigint;
  bond: bigint;
  expectedSha: string;
  minTests: number;
  minCoverageBps: number;
  deadline: number;
  status: MilestoneStatus;
  submittedSha: string;
  attempts: number;
  verifiedAt: number;
  releaseAt: number;
  disputeCount: number;
  nextDisputeBond: bigint;
  lastReport: Report | null;
}

export interface Escrow {
  id: number;
  employer: string;
  contractor: string;
  repo: string;
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
}

export interface Solvency {
  totalIn: bigint;
  totalPaidOut: bigint;
  liabilities: bigint;
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
  reward: bigint;
  expectedSha: string;
  minTests: number;
  minCoverageBps: number;
  deadline: number;
}

export interface CreateEscrowInput {
  contractor: string;
  repo: string;
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
  evaluate(by: string, milestoneId: number, sha: string): Promise<Outcome>;
  settle(by: string, milestoneId: number): Promise<void>;
  approve(by: string, milestoneId: number): Promise<void>;
  claimDefault(by: string, milestoneId: number): Promise<void>;
  quoteDisputeBond(milestoneId: number, who: string): Promise<bigint>;
  fileDispute(by: string, milestoneId: number, reason: string): Promise<Outcome>;
}
