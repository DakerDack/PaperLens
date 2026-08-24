from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


Identifier = Annotated[str, Field(min_length=1, max_length=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BlockType(str, Enum):
    TITLE = "title"
    TEXT = "text"
    TABLE = "table"
    FORMULA = "formula"
    IMAGE_CAPTION = "image_caption"


class ParserName(str, Enum):
    MINERU = "mineru"
    PDFPLUMBER = "pdfplumber"


class SectionId(str, Enum):
    RESEARCH_QUESTION = "research_question"
    METHODS = "methods"
    RESULTS = "results"
    LIMITATIONS = "limitations"
    PLAIN_EXPLANATION = "plain_explanation"


class ClaimType(str, Enum):
    BACKGROUND = "background"
    METHOD = "method"
    RESULT = "result"
    CONCLUSION = "conclusion"
    LIMITATION = "limitation"
    EXPLANATION = "explanation"


class Importance(str, Enum):
    CRITICAL = "critical"
    NONCRITICAL = "noncritical"


class Auditability(str, Enum):
    AUDITABLE = "auditable"
    NON_AUDITABLE = "non_auditable"
    NEEDS_REVIEW = "needs_review"


class MatchMethod(str, Enum):
    MODEL_CANDIDATE = "model_candidate"
    BM25_FALLBACK = "bm25_fallback"
    NONE = "none"


class Relation(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    INSUFFICIENT = "insufficient"


class ScopeStatus(str, Enum):
    PRESERVED = "preserved"
    EXPANDED = "expanded"
    UNCLEAR = "unclear"


class TerminologyStatus(str, Enum):
    CORRECT = "correct"
    MISUSED = "misused"
    UNCLEAR = "unclear"


class Severity(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class DisclosureStatus(str, Enum):
    PRESENT = "present"
    MISSING = "missing"


class GeneratedContentLabelApplicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


class GeneratedContentLabelStatus(str, Enum):
    PRESENT = "present"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class RiskCategory(str, Enum):
    SENSITIVE_INFORMATION = "sensitive_information"
    AUTHOR_IMPERSONATION = "author_impersonation"
    ACADEMIC_INTEGRITY = "academic_integrity"


class RiskStatus(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNCLEAR = "unclear"


class RiskLocationType(str, Enum):
    SENTENCE = "sentence"
    TITLE = "title"
    DOCUMENT = "document"


class DimensionId(str, Enum):
    FACTUAL_CONSISTENCY = "factual_consistency"
    CITATION_CORRECTNESS = "citation_correctness"
    CITATION_COMPLETENESS = "citation_completeness"
    METHOD_SCOPE = "method_scope"
    CONCLUSION_LIMITATIONS = "conclusion_limitations"
    TERMINOLOGY = "terminology"
    READER_ADAPTATION = "reader_adaptation"
    RISK_COMPLIANCE = "risk_compliance"


DIMENSION_WEIGHTS: dict[DimensionId, float] = {
    DimensionId.FACTUAL_CONSISTENCY: 0.20,
    DimensionId.CITATION_CORRECTNESS: 0.15,
    DimensionId.CITATION_COMPLETENESS: 0.15,
    DimensionId.METHOD_SCOPE: 0.15,
    DimensionId.CONCLUSION_LIMITATIONS: 0.15,
    DimensionId.TERMINOLOGY: 0.07,
    DimensionId.READER_ADAPTATION: 0.08,
    DimensionId.RISK_COMPLIANCE: 0.05,
}
if sum(DIMENSION_WEIGHTS.values()) != 1.0:
    raise RuntimeError("PaperLens dimension weights must sum to exactly 1")

CORE_DIMENSION_GATES: dict[DimensionId, int] = {
    DimensionId.FACTUAL_CONSISTENCY: 3,
    DimensionId.CITATION_CORRECTNESS: 3,
    DimensionId.CITATION_COMPLETENESS: 3,
    DimensionId.METHOD_SCOPE: 3,
    DimensionId.CONCLUSION_LIMITATIONS: 3,
    DimensionId.TERMINOLOGY: 2,
    DimensionId.READER_ADAPTATION: 2,
    DimensionId.RISK_COMPLIANCE: 3,
}


class AuditStatus(str, Enum):
    QUICK_COMPLETE = "quick_complete"
    DEEP_COMPLETE = "deep_complete"


class Decision(str, Enum):
    PENDING_DEEP_AUDIT = "pending_deep_audit"
    QUALIFIED = "qualified"
    NEEDS_REVISION = "needs_revision"
    UNQUALIFIED = "unqualified"


class PatchScope(str, Enum):
    SENTENCE = "sentence"
    DOCUMENT = "document"


class ProjectStage(str, Enum):
    CREATED = "created"
    PARSED = "parsed"
    GENERATED = "generated"
    QUICK_CHECKED = "quick_checked"
    DEEP_AUDITED = "deep_audited"
    PATCH_PENDING = "patch_pending"
    FAILED = "failed"


class SourceBlock(StrictModel):
    block_id: Identifier
    page_index: int = Field(ge=0)
    type: BlockType
    text: str = Field(min_length=1)
    bbox: tuple[float, float, float, float] | None = None
    reading_order: int = Field(ge=0)
    parser: ParserName
    parser_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_bbox(self) -> SourceBlock:
        if self.bbox is None:
            return self
        x0, y0, x1, y1 = self.bbox
        if any(value < 0 or value > 1 for value in self.bbox):
            raise ValueError("bbox coordinates must be normalized to [0, 1]")
        if x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must have positive width and height")
        return self


class Sentence(StrictModel):
    sentence_id: Identifier
    text: str = Field(min_length=1)


class Section(StrictModel):
    section_id: SectionId
    heading: str = Field(min_length=1, max_length=100)
    sentences: list[Sentence] = Field(min_length=1)


class ContentDraft(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    sections: list[Section] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_sections_and_sentences(self) -> ContentDraft:
        section_ids = [section.section_id for section in self.sections]
        if set(section_ids) != set(SectionId) or len(section_ids) != len(set(section_ids)):
            raise ValueError("document must contain each required section exactly once")

        sentence_ids = [
            sentence.sentence_id
            for section in self.sections
            for sentence in section.sentences
        ]
        if len(sentence_ids) != len(set(sentence_ids)):
            raise ValueError("sentence_id values must be unique")
        return self


class AtomicClaim(StrictModel):
    claim_id: Identifier
    sentence_id: Identifier
    text: str = Field(min_length=1)
    claim_type: ClaimType
    importance: Importance
    qualifiers: list[str]
    numeric_entities: list[str]
    auditability: Auditability
    candidate_block_ids: list[Identifier] = Field(max_length=3)
    candidate_quote: str | None = None

    @model_validator(mode="after")
    def validate_candidate_evidence(self) -> AtomicClaim:
        if self.auditability == Auditability.AUDITABLE and not self.candidate_block_ids:
            raise ValueError("auditable claims require at least one candidate block")
        if self.candidate_quote == "":
            raise ValueError("candidate_quote must be null or non-empty")
        return self


class GeneratedBundle(StrictModel):
    document: ContentDraft
    claims: list[AtomicClaim]

    @model_validator(mode="after")
    def validate_claim_links(self) -> GeneratedBundle:
        sentence_ids = {
            sentence.sentence_id
            for section in self.document.sections
            for sentence in section.sentences
        }
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique")
        unknown = {claim.sentence_id for claim in self.claims} - sentence_ids
        if unknown:
            raise ValueError(f"claims reference unknown sentence ids: {sorted(unknown)}")
        return self


class EvidenceRecord(StrictModel):
    claim_id: Identifier
    block_id: Identifier | None = None
    page_index: int | None = Field(default=None, ge=0)
    quote: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    match_method: MatchMethod
    quote_verified: bool
    rule_flags: list[str]

    @model_validator(mode="after")
    def validate_match_state(self) -> EvidenceRecord:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if any(value < 0 or value > 1 for value in self.bbox):
                raise ValueError("bbox coordinates must be normalized to [0, 1]")
            if x0 >= x1 or y0 >= y1:
                raise ValueError("bbox must have positive width and height")
        present = (self.block_id, self.page_index, self.quote)
        if self.match_method == MatchMethod.NONE:
            if any(value is not None for value in present) or self.quote_verified:
                raise ValueError("unmatched evidence cannot contain verified source data")
        elif any(value is None for value in present):
            raise ValueError("matched evidence requires block_id, page_index, and quote")
        return self


class SemanticJudgment(StrictModel):
    claim_id: Identifier
    block_id: Identifier
    relation: Relation
    scope_status: ScopeStatus
    terminology_status: TerminologyStatus
    severity: Severity
    reason: str = Field(min_length=1)


class ComplianceContext(StrictModel):
    rights_or_license_confirmed: bool
    source_disclosure_status: DisclosureStatus
    ai_assistance_disclosure_status: DisclosureStatus
    generated_content_label_applicability: GeneratedContentLabelApplicability
    generated_content_label_status: GeneratedContentLabelStatus

    @model_validator(mode="after")
    def validate_generated_content_label(self) -> ComplianceContext:
        if (
            self.generated_content_label_applicability
            == GeneratedContentLabelApplicability.NOT_APPLICABLE
        ):
            if (
                self.generated_content_label_status
                != GeneratedContentLabelStatus.NOT_APPLICABLE
            ):
                raise ValueError(
                    "not-applicable generated content labels require "
                    "not_applicable status"
                )
        elif (
            self.generated_content_label_status
            == GeneratedContentLabelStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                "applicable generated content labels require present or missing status"
            )
        return self


class RiskLocation(StrictModel):
    location_type: RiskLocationType
    sentence_id: Identifier | None
    evidence_excerpt: str | None = Field(min_length=1, max_length=160)


class RiskFinding(StrictModel):
    category: RiskCategory
    status: RiskStatus
    locations: list[RiskLocation]
    reason: str = Field(min_length=1, max_length=500)
    remediation: str = Field(min_length=1, max_length=300)


_CODE_OWNED_SENSITIVE_TEXT = {
    RiskStatus.NOT_DETECTED: (
        "No sensitive-content risk was detected.",
        "No sensitive-content remediation is required.",
    ),
    RiskStatus.UNCLEAR: (
        "The sensitive-content risk remains unclear.",
        "Review the generated content before release.",
    ),
    RiskStatus.DETECTED: (
        "A sensitive-content risk was detected.",
        "Remove or generalize the flagged content before release.",
    ),
}


def code_owned_sensitive_text(status: RiskStatus) -> tuple[str, str]:
    return _CODE_OWNED_SENSITIVE_TEXT[status]


_FIXED_RISK_HARD_FAILURE_BY_CATEGORY = {
    RiskCategory.SENSITIVE_INFORMATION: "SENSITIVE_INFORMATION",
    RiskCategory.AUTHOR_IMPERSONATION: "AUTHOR_IMPERSONATION",
    RiskCategory.ACADEMIC_INTEGRITY: "ACADEMIC_INTEGRITY",
}
_FIXED_RISK_HARD_FAILURE_CODES = frozenset(
    _FIXED_RISK_HARD_FAILURE_BY_CATEGORY.values()
)


def compute_risk_level_points(
    compliance_context: ComplianceContext,
    risk_findings: list[RiskFinding],
) -> int:
    missing_disclosures = sum(
        status == DisclosureStatus.MISSING
        for status in (
            compliance_context.source_disclosure_status,
            compliance_context.ai_assistance_disclosure_status,
        )
    )
    points = 4
    if missing_disclosures == 1:
        points = 3
    elif missing_disclosures >= 2:
        points = 2
    if any(finding.status == RiskStatus.UNCLEAR for finding in risk_findings):
        points = min(points, 2)
    if not compliance_context.rights_or_license_confirmed:
        points = min(points, 1)
    required_label_missing = (
        compliance_context.generated_content_label_applicability
        == GeneratedContentLabelApplicability.APPLICABLE
        and compliance_context.generated_content_label_status
        == GeneratedContentLabelStatus.MISSING
    )
    if required_label_missing or any(
        finding.status == RiskStatus.DETECTED for finding in risk_findings
    ):
        points = 0
    return points


def fixed_risk_hard_failures(risk_findings: list[RiskFinding]) -> list[str]:
    return [
        _FIXED_RISK_HARD_FAILURE_BY_CATEGORY[finding.category]
        for finding in risk_findings
        if finding.status == RiskStatus.DETECTED
    ]


class DeepAuditResult(StrictModel):
    semantic_judgments: list[SemanticJudgment]
    risk_findings: list[RiskFinding]


class RiskAssessment(StrictModel):
    compliance_context: ComplianceContext
    risk_findings: list[RiskFinding]
    level_points: int = Field(ge=0, le=4)

    @model_validator(mode="after")
    def validate_risk_categories(self) -> RiskAssessment:
        categories = [finding.category for finding in self.risk_findings]
        if len(categories) != len(RiskCategory) or set(categories) != set(
            RiskCategory
        ):
            raise ValueError(
                "risk findings must contain each required category exactly once"
            )
        expected_level = compute_risk_level_points(
            self.compliance_context,
            self.risk_findings,
        )
        if self.level_points != expected_level:
            raise ValueError(
                "level_points must match the code-computed risk level"
            )
        sensitive_finding = next(
            finding
            for finding in self.risk_findings
            if finding.category == RiskCategory.SENSITIVE_INFORMATION
        )
        expected_reason, expected_remediation = code_owned_sensitive_text(
            sensitive_finding.status
        )
        if (
            sensitive_finding.reason != expected_reason
            or sensitive_finding.remediation != expected_remediation
        ):
            raise ValueError(
                "sensitive_information explanations must use code-owned redacted text"
            )
        return self


MetricValue = int | float


class DimensionResult(StrictModel):
    dimension_id: DimensionId
    raw_metrics: dict[str, MetricValue]
    score: float = Field(ge=0, le=100)
    level: str = Field(pattern="^(good|acceptable|poor)$")


def dimension_score(level_points: int) -> float:
    return float(level_points * 25)


def dimension_level(level_points: int) -> str:
    if level_points >= 4:
        return "good"
    if level_points >= 2:
        return "acceptable"
    return "poor"


def dimension_level_points(dimension: DimensionResult) -> int:
    level_points = dimension.raw_metrics.get("level_points")
    if (
        isinstance(level_points, bool)
        or not isinstance(level_points, int)
        or not 0 <= level_points <= 4
    ):
        raise ValueError("dimension level_points must be an integer from 0 to 4")
    return level_points


def compute_audit_outcome(
    dimensions: list[DimensionResult],
    hard_failures: list[str],
) -> tuple[float, bool, Decision]:
    level_points = {
        dimension.dimension_id: dimension_level_points(dimension)
        for dimension in dimensions
    }
    if set(level_points) != set(DimensionId):
        raise ValueError("all eight dimensions are required to compute an outcome")
    overall_score = round(
        sum(
            dimension.score * DIMENSION_WEIGHTS[dimension.dimension_id]
            for dimension in dimensions
        ),
        2,
    )
    core_gate_passed = not hard_failures and all(
        level_points[dimension_id] >= minimum
        for dimension_id, minimum in CORE_DIMENSION_GATES.items()
    )
    if hard_failures:
        decision = Decision.UNQUALIFIED
    elif core_gate_passed and overall_score >= 75:
        decision = Decision.QUALIFIED
    else:
        decision = Decision.NEEDS_REVISION
    return overall_score, core_gate_passed, decision


class AuditReport(StrictModel):
    audit_status: AuditStatus
    dimensions: list[DimensionResult]
    risk_assessment: RiskAssessment | None
    hard_failures: list[str]
    core_gate_passed: bool | None
    overall_score: float | None = Field(default=None, ge=0, le=100)
    decision: Decision

    @model_validator(mode="after")
    def validate_completion_state(self) -> AuditReport:
        if self.audit_status == AuditStatus.QUICK_COMPLETE:
            if self.dimensions:
                raise ValueError("quick audit cannot contain final dimension scores")
            if self.risk_assessment is not None:
                raise ValueError("quick audit risk assessment must be null")
            if self.hard_failures:
                raise ValueError("quick audit hard failures must be empty")
            if self.core_gate_passed is not None or self.overall_score is not None:
                raise ValueError("quick audit cannot contain final gates or score")
            if self.decision != Decision.PENDING_DEEP_AUDIT:
                raise ValueError("quick audit decision must remain pending")
            return self

        dimension_ids = [item.dimension_id for item in self.dimensions]
        if set(dimension_ids) != set(DimensionId) or len(dimension_ids) != len(
            set(dimension_ids)
        ):
            raise ValueError("deep audit must contain each dimension exactly once")
        if self.core_gate_passed is None or self.overall_score is None:
            raise ValueError("deep audit requires gates and overall score")
        if self.risk_assessment is None:
            raise ValueError("deep audit requires a risk assessment")
        for dimension in self.dimensions:
            level_points = dimension_level_points(dimension)
            if (
                dimension.score != dimension_score(level_points)
                or dimension.level != dimension_level(level_points)
            ):
                raise ValueError(
                    "dimension score and level must match the code-computed dimension display"
                )
        expected_risk_level = compute_risk_level_points(
            self.risk_assessment.compliance_context,
            self.risk_assessment.risk_findings,
        )
        if self.risk_assessment.level_points != expected_risk_level:
            raise ValueError(
                "risk assessment level_points must match the code-computed risk level"
            )
        risk_dimension = next(
            item
            for item in self.dimensions
            if item.dimension_id == DimensionId.RISK_COMPLIANCE
        )
        if (
            risk_dimension.raw_metrics.get("level_points")
            != self.risk_assessment.level_points
        ):
            raise ValueError(
                "risk assessment level_points must match risk_compliance dimension"
            )
        expected_risk_failures = set(
            fixed_risk_hard_failures(self.risk_assessment.risk_findings)
        )
        actual_risk_failures = [
            failure
            for failure in self.hard_failures
            if failure in _FIXED_RISK_HARD_FAILURE_CODES
        ]
        if (
            set(actual_risk_failures) != expected_risk_failures
            or len(actual_risk_failures) != len(set(actual_risk_failures))
        ):
            raise ValueError(
                "fixed risk hard failures must exactly match detected risk findings"
            )
        if self.decision == Decision.PENDING_DEEP_AUDIT:
            raise ValueError("deep audit cannot remain pending")
        expected_overall, expected_core_gate, expected_decision = (
            compute_audit_outcome(self.dimensions, self.hard_failures)
        )
        if self.overall_score != expected_overall:
            raise ValueError(
                "overall_score must match the code-computed overall score"
            )
        if self.core_gate_passed != expected_core_gate:
            raise ValueError(
                "core_gate_passed must match the code-computed core gate"
            )
        if self.decision != expected_decision:
            raise ValueError("decision must match the code-computed decision")
        return self


class EditPatch(StrictModel):
    patch_id: Identifier
    base_version: int = Field(ge=1)
    scope: PatchScope
    target_sentence_ids: list[Identifier]
    before_hash: str = Field(pattern="^[a-f0-9]{64}$")
    before_text: str = Field(min_length=1)
    after_text: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    fact_changed: bool
    evidence_changed: bool

    @model_validator(mode="after")
    def validate_scope(self) -> EditPatch:
        if self.scope == PatchScope.SENTENCE and len(self.target_sentence_ids) != 1:
            raise ValueError("sentence patches require exactly one target sentence")
        if self.scope == PatchScope.DOCUMENT and self.target_sentence_ids:
            raise ValueError("document patches cannot contain target sentence ids")
        return self


class ErrorResponse(StrictModel):
    error_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1)
    retryable: bool
    details: dict[str, Any] | None = None


class HealthResponse(StrictModel):
    status: str = Field(pattern="^ok$")
    service: str = Field(pattern="^paperlens-api$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
