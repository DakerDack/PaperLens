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


class DimensionId(str, Enum):
    FACTUAL_CONSISTENCY = "factual_consistency"
    CITATION_CORRECTNESS = "citation_correctness"
    CITATION_COMPLETENESS = "citation_completeness"
    METHOD_SCOPE = "method_scope"
    CONCLUSION_LIMITATIONS = "conclusion_limitations"
    TERMINOLOGY = "terminology"
    READER_ADAPTATION = "reader_adaptation"
    RISK_COMPLIANCE = "risk_compliance"


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


MetricValue = int | float


class DimensionResult(StrictModel):
    dimension_id: DimensionId
    raw_metrics: dict[str, MetricValue]
    score: float = Field(ge=0, le=100)
    level: str = Field(pattern="^(good|acceptable|poor)$")


class AuditReport(StrictModel):
    audit_status: AuditStatus
    dimensions: list[DimensionResult]
    hard_failures: list[str]
    core_gate_passed: bool | None
    overall_score: float | None = Field(default=None, ge=0, le=100)
    decision: Decision

    @model_validator(mode="after")
    def validate_completion_state(self) -> AuditReport:
        if self.audit_status == AuditStatus.QUICK_COMPLETE:
            if self.dimensions:
                raise ValueError("quick audit cannot contain final dimension scores")
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
        if self.decision == Decision.PENDING_DEEP_AUDIT:
            raise ValueError("deep audit cannot remain pending")
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
