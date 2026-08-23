export type BlockType = "title" | "text" | "table" | "formula" | "image_caption";
export type ParserName = "mineru" | "pdfplumber";
export type SectionId =
  | "research_question"
  | "methods"
  | "results"
  | "limitations"
  | "plain_explanation";
export type ClaimType =
  | "background"
  | "method"
  | "result"
  | "conclusion"
  | "limitation"
  | "explanation";
export type Importance = "critical" | "noncritical";
export type Auditability = "auditable" | "non_auditable" | "needs_review";
export type MatchMethod = "model_candidate" | "bm25_fallback" | "none";
export type Relation = "supports" | "contradicts" | "insufficient";
export type ScopeStatus = "preserved" | "expanded" | "unclear";
export type TerminologyStatus = "correct" | "misused" | "unclear";
export type Severity = "none" | "minor" | "major" | "critical";
export type DimensionId =
  | "factual_consistency"
  | "citation_correctness"
  | "citation_completeness"
  | "method_scope"
  | "conclusion_limitations"
  | "terminology"
  | "reader_adaptation"
  | "risk_compliance";
export type AuditStatus = "quick_complete" | "deep_complete";
export type Decision =
  | "pending_deep_audit"
  | "qualified"
  | "needs_revision"
  | "unqualified";
export type PatchScope = "sentence" | "document";
export type ProjectStage =
  | "created"
  | "parsed"
  | "generated"
  | "quick_checked"
  | "deep_audited"
  | "patch_pending"
  | "failed";

export interface SourceBlock {
  block_id: string;
  page_index: number;
  type: BlockType;
  text: string;
  bbox: [number, number, number, number] | null;
  reading_order: number;
  parser: ParserName;
  parser_version: string;
}

export interface Sentence {
  sentence_id: string;
  text: string;
}

export interface Section {
  section_id: SectionId;
  heading: string;
  sentences: Sentence[];
}

export interface ContentDraft {
  title: string;
  sections: Section[];
}

export interface AtomicClaim {
  claim_id: string;
  sentence_id: string;
  text: string;
  claim_type: ClaimType;
  importance: Importance;
  qualifiers: string[];
  numeric_entities: string[];
  auditability: Auditability;
  candidate_block_ids: string[];
  candidate_quote: string | null;
}

export interface GeneratedBundle {
  document: ContentDraft;
  claims: AtomicClaim[];
}

export interface EvidenceRecord {
  claim_id: string;
  block_id: string | null;
  page_index: number | null;
  quote: string | null;
  bbox: [number, number, number, number] | null;
  match_method: MatchMethod;
  quote_verified: boolean;
  rule_flags: string[];
}

export interface SemanticJudgment {
  claim_id: string;
  block_id: string;
  relation: Relation;
  scope_status: ScopeStatus;
  terminology_status: TerminologyStatus;
  severity: Severity;
  reason: string;
}

export interface DimensionResult {
  dimension_id: DimensionId;
  raw_metrics: Record<string, number>;
  score: number;
  level: "good" | "acceptable" | "poor";
}

export interface AuditReport {
  audit_status: AuditStatus;
  dimensions: DimensionResult[];
  hard_failures: string[];
  core_gate_passed: boolean | null;
  overall_score: number | null;
  decision: Decision;
}

export interface EditPatch {
  patch_id: string;
  base_version: number;
  scope: PatchScope;
  target_sentence_ids: string[];
  before_hash: string;
  before_text: string;
  after_text: string;
  reason: string;
  fact_changed: boolean;
  evidence_changed: boolean;
}

export interface ErrorResponse {
  error_code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: "ok";
  service: "paperlens-api";
  version: string;
}
