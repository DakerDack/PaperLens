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
export type ModelMode = "mock" | "live";
export type ClaimPolicy = "required" | "must_be_empty";
export type DisclosureStatus = "present" | "missing";
export type GeneratedContentLabelApplicability = "applicable" | "not_applicable";
export type GeneratedContentLabelStatus = "present" | "missing" | "not_applicable";
export type RiskCategory =
  | "sensitive_information"
  | "author_impersonation"
  | "academic_integrity";
export type RiskStatus = "detected" | "not_detected" | "unclear";
export type RiskLocationType = "sentence" | "title" | "document";

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

export interface ComplianceContext {
  rights_or_license_confirmed: boolean;
  source_disclosure_status: DisclosureStatus;
  ai_assistance_disclosure_status: DisclosureStatus;
  generated_content_label_applicability: GeneratedContentLabelApplicability;
  generated_content_label_status: GeneratedContentLabelStatus;
}

export interface RiskLocation {
  location_type: RiskLocationType;
  sentence_id: string | null;
  evidence_excerpt: string | null;
}

export interface RiskFinding {
  category: RiskCategory;
  status: RiskStatus;
  locations: RiskLocation[];
  reason: string;
  remediation: string;
}

export interface RiskAssessment {
  compliance_context: ComplianceContext;
  risk_findings: RiskFinding[];
  level_points: number;
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
  risk_assessment: RiskAssessment | null;
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

export interface ParseQualitySnapshot {
  page_count: number;
  block_count: number;
  empty_page_rate: number;
  abnormal_character_rate: number;
  page_number_completeness_rate: number;
  bbox_availability_rate: number;
}

export interface ProjectCreateResponse {
  project_id: string;
  stage: "parsed";
  parse_quality: ParseQualitySnapshot;
  source_block_count: number;
  created_at: string;
}

export interface VersionSummary {
  version_id: string;
  version_no: number;
  parent_version_id: string | null;
  reason: string;
  created_at: string;
}

export interface ProjectView {
  project_id: string;
  stage: ProjectStage;
  model_mode: ModelMode;
  parse_quality: ParseQualitySnapshot | null;
  source_block_count: number;
  current_version_id: string | null;
  current_version_no: number | null;
  document: ContentDraft | null;
  claims: AtomicClaim[];
  evidence_records: EvidenceRecord[];
  audit_report: AuditReport | null;
  versions: VersionSummary[];
  error_code: string | null;
  retryable_stage: ProjectStage | null;
  created_at: string;
  updated_at: string;
}

export interface GenerationResponse {
  project_id: string;
  version_id: string;
  stage: "quick_checked";
  model_mode: ModelMode;
  document: ContentDraft;
  claims: AtomicClaim[];
  evidence_records: EvidenceRecord[];
  quick_report: AuditReport;
}

export interface GenerationRequest {
  claim_policy: ClaimPolicy;
}

export interface DeepAuditRequest {
  source_disclosure_status: DisclosureStatus;
  ai_assistance_disclosure_status: DisclosureStatus;
  generated_content_label_applicability: GeneratedContentLabelApplicability;
  generated_content_label_status: GeneratedContentLabelStatus;
}

export interface DeepAuditResponse {
  project_id: string;
  version_id: string;
  stage: "deep_audited";
  audit_report: AuditReport;
}
