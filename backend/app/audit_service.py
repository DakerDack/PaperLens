from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable
from typing import Literal

from pydantic import ValidationError
from rank_bm25 import BM25Okapi

from backend.app.hy3_service import (
    Hy3Service,
    build_safe_diagnostic_message,
)
from backend.app.models import (
    AtomicClaim,
    AuditReport,
    AuditStatus,
    Auditability,
    ClaimType,
    ComplianceContext,
    ContentDraft,
    DIMENSION_WEIGHTS,
    DeepAuditResult,
    Decision,
    DimensionId,
    DimensionResult,
    EvidenceRecord,
    GeneratedBundle,
    Importance,
    MatchMethod,
    Relation,
    RiskAssessment,
    RiskCategory,
    RiskFinding,
    RiskLocationType,
    RiskStatus,
    ScopeStatus,
    SectionId,
    SemanticJudgment,
    Severity,
    SourceBlock,
    TerminologyStatus,
    code_owned_sensitive_text,
    compute_audit_outcome,
    compute_risk_level_points,
    dimension_level,
    dimension_score,
    fixed_risk_hard_failures,
)


logger = logging.getLogger("uvicorn.error.paperlens.audit_service")

AuditValidationBoundary = Literal[
    "compliance_context_validation",
    "deep_audit_result_validation",
    "evidence_pair_validation",
    "semantic_pair_validation",
    "risk_category_validation",
    "risk_location_validation",
    "risk_excerpt_validation",
    "risk_assessment_construction",
    "dimension_construction",
    "final_report_construction",
]


_HYPHENATED_LINE_BREAK = re.compile(
    r"(?<=\w)[\-\u00ad\u2010\u2011]\s*(?:\r\n|\r|\n)\s*(?=\w)"
)
_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?|[<>≤≥=]+", re.UNICODE)
_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][-+]?\d+)?%?"
)
_UNIT_TERMS = (
    "%",
    "percentage",
    "percent",
    "seconds",
    "second",
    "minutes",
    "minute",
    "months",
    "month",
    "weeks",
    "week",
    "years",
    "year",
    "hours",
    "hour",
    "days",
    "day",
    "secs",
    "sec",
    "mins",
    "min",
    "hrs",
    "hr",
    "mhz",
    "khz",
    "hz",
    "mg",
    "kg",
    "ug",
    "μg",
    "µg",
    "ml",
    "mm",
    "cm",
    "km",
    "ms",
    "gb",
    "mb",
    "kb",
    "g",
    "l",
    "m",
    "s",
    "h",
    "小时",
    "分钟",
    "毫秒",
    "千克",
    "毫克",
    "微克",
    "毫升",
    "千米",
    "毫米",
    "厘米",
    "名",
    "例",
    "岁",
    "年",
    "月",
    "周",
    "天",
    "秒",
    "人",
    "个",
    "组",
    "次",
    "倍",
    "克",
    "升",
    "米",
)
_UNIT_ALTERNATION = "|".join(
    re.escape(unit) for unit in sorted(_UNIT_TERMS, key=len, reverse=True)
)
_UNIT = re.compile(
    rf"(?<![\w.])[-+]?(?:\d+(?:[.,]\d+)*|\.\d+)\s*"
    rf"({_UNIT_ALTERNATION})(?![A-Za-z])",
    re.IGNORECASE,
)
_ENGLISH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
}
_NEGATION_TERMS = (
    "not",
    "no",
    "never",
    "neither",
    "without",
    "none",
    "cannot",
    "can't",
    "didn't",
    "doesn't",
    "isn't",
    "wasn't",
    "非",
    "不",
    "未",
    "无",
    "没有",
    "并非",
    "不能",
)
_DIRECTION_TERMS = {
    "up": (
        "increase",
        "increased",
        "increases",
        "increasing",
        "higher",
        "greater",
        "more",
        "improve",
        "improved",
        "improves",
        "提升",
        "提高",
        "增加",
        "上升",
        "高于",
        ">",
        "≥",
    ),
    "down": (
        "decrease",
        "decreased",
        "decreases",
        "decreasing",
        "lower",
        "less",
        "reduce",
        "reduced",
        "reduces",
        "decline",
        "降低",
        "减少",
        "下降",
        "低于",
        "<",
        "≤",
    ),
    "equal": (
        "equal",
        "equivalent",
        "same",
        "相同",
        "相等",
        "等于",
        "=",
    ),
}
_CLAUSE_BOUNDARY = re.compile(
    r"(?:[,，]\s*(?:and|but|while|whereas|并且|同时|但是|但|而|且)\s*|"
    r"[;；]\s*|\s+(?:and|but|while|whereas)\s+)",
    re.IGNORECASE,
)
_CAUSAL_MARKER = re.compile(
    r"\b(?:because|therefore|thus|due\s+to|caused?|causes?)\b|"
    r"因为|因此|由于|导致|说明|归因于",
    re.IGNORECASE,
)
_EVIDENCE_FRAGMENT_BOUNDARY = re.compile(
    r"(?<=[!?。！？;；])(?:\s+|(?=[^\s]))|"
    r"(?<=\.)(?=\s+[A-Z])\s+|[\r\n]+"
)


_DETERMINISTIC_CONTRADICTION_PREFIXES = (
    "NUMBER_MISMATCH:",
    "UNIT_MISMATCH:",
    "NEGATION_MISMATCH",
    "COMPARISON_DIRECTION_MISMATCH",
)
_FORGED_CITATION_PREFIXES = (
    "CANDIDATE_BLOCK_NOT_FOUND:",
    "CANDIDATE_QUOTE_NOT_FOUND:",
)


class AuditServiceError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


def _audit_service_error(
    error_code: str,
    message: str,
    *,
    retryable: bool,
    validation_boundary: AuditValidationBoundary,
    validation_error: ValidationError | None = None,
) -> AuditServiceError:
    if validation_error is None:
        validation_error_count = 0
        validation_error_type = "none"
        validation_location = "none"
    else:
        (
            validation_error_count,
            validation_error_type,
            validation_location,
        ) = Hy3Service._validation_error_diagnostic(validation_error)
    message_prefix = (
        "audit_postprocessing_failure operation=audit_postprocessing "
        "attempt=0 retry_count=0 completion_tokens=null "
        "configured_completion_limit=null completion_limit_reached=false "
        f"finish_reason=missing validation_boundary={validation_boundary} "
        f"error_code={error_code} "
        f"validation_error_count={validation_error_count} "
        f"validation_error_type={validation_error_type} "
        "validation_location="
    )
    logger.info(
        "%s",
        build_safe_diagnostic_message(
            message_prefix,
            validation_location,
        ),
    )
    return AuditServiceError(
        error_code,
        message,
        retryable=retryable,
    )


def _normalize_hyphenated_line_breaks(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _HYPHENATED_LINE_BREAK.sub("", normalized)
    return normalized.replace("\u00ad", "")


def normalize_evidence_text(text: str) -> str:
    return " ".join(_normalize_hyphenated_line_breaks(text).split()).casefold()


def should_split_sentence(text: str) -> bool:
    normalized = normalize_evidence_text(text)
    if _CAUSAL_MARKER.search(normalized) and _comparison_directions(normalized):
        return True

    clauses = [
        clause.strip()
        for clause in _CLAUSE_BOUNDARY.split(normalized)
        if clause.strip()
    ]
    independently_checkable = [
        clause
        for clause in clauses
        if _numbers(clause) or _comparison_directions(clause)
    ]
    return len(independently_checkable) >= 2


def required_section_flags(
    section_ids: Iterable[SectionId | str],
) -> list[str]:
    present = {
        item if isinstance(item, SectionId) else SectionId(item)
        for item in section_ids
    }
    return [
        f"MISSING_REQUIRED_SECTION:{section_id.value}"
        for section_id in SectionId
        if section_id not in present
    ]


class AuditService:
    def __init__(self, hy3_service: Hy3Service | None = None) -> None:
        self.hy3_service = hy3_service or Hy3Service()

    def quick_check(
        self,
        bundle: GeneratedBundle,
        source_blocks: list[SourceBlock],
    ) -> tuple[list[EvidenceRecord], AuditReport]:
        records = [
            record
            for claim in bundle.claims
            for record in self.verify_claim_evidence(claim, source_blocks)
        ]
        report = AuditReport(
            audit_status=AuditStatus.QUICK_COMPLETE,
            dimensions=[],
            risk_assessment=None,
            hard_failures=[],
            core_gate_passed=None,
            overall_score=None,
            decision=Decision.PENDING_DEEP_AUDIT,
        )
        return records, report

    def semantic_pairs(
        self,
        bundle: GeneratedBundle,
        evidence_records: list[EvidenceRecord],
    ) -> list[tuple[AtomicClaim, EvidenceRecord]]:
        claims = {claim.claim_id: claim for claim in bundle.claims}
        pairs: list[tuple[AtomicClaim, EvidenceRecord]] = []
        seen: set[tuple[str, str]] = set()
        for evidence in evidence_records:
            claim = claims.get(evidence.claim_id)
            if claim is None:
                raise _audit_service_error(
                    "AUDIT_INCOMPLETE",
                    "Evidence references a claim outside the generated bundle.",
                    retryable=False,
                    validation_boundary="evidence_pair_validation",
                )
            if (
                claim.auditability == Auditability.NON_AUDITABLE
                or not evidence.quote_verified
                or evidence.block_id is None
            ):
                continue
            key = (claim.claim_id, evidence.block_id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((claim, evidence))
        return pairs

    def run_deep_audit(
        self,
        bundle: GeneratedBundle,
        evidence_records: list[EvidenceRecord],
        compliance_context: ComplianceContext,
    ) -> tuple[DeepAuditResult, AuditReport]:
        try:
            validated_context = ComplianceContext.model_validate(
                compliance_context.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "Deep audit compliance context is incomplete.",
                retryable=False,
                validation_boundary="compliance_context_validation",
                validation_error=(
                    exc if isinstance(exc, ValidationError) else None
                ),
            ) from exc
        if not validated_context.rights_or_license_confirmed:
            raise AuditServiceError(
                "RIGHTS_NOT_CONFIRMED",
                "Document processing rights or permission are not confirmed.",
                retryable=False,
            )

        pairs = self.semantic_pairs(bundle, evidence_records)
        result = self.hy3_service.deep_audit(
            document=bundle.document,
            claim_evidence_pairs=pairs
        )
        report = self.score(
            bundle,
            evidence_records,
            result,
            validated_context,
        )
        if report.risk_assessment is None:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "Deep audit risk assessment is missing.",
                retryable=False,
                validation_boundary="final_report_construction",
            )
        safe_result = DeepAuditResult(
            semantic_judgments=result.semantic_judgments,
            risk_findings=report.risk_assessment.risk_findings,
        )
        return safe_result, report

    def score(
        self,
        bundle: GeneratedBundle,
        evidence_records: list[EvidenceRecord],
        deep_audit_result: DeepAuditResult,
        compliance_context: ComplianceContext,
    ) -> AuditReport:
        try:
            validated_context = ComplianceContext.model_validate(
                compliance_context.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "Deep audit context or result is incomplete.",
                retryable=False,
                validation_boundary="compliance_context_validation",
                validation_error=(
                    exc if isinstance(exc, ValidationError) else None
                ),
            ) from exc
        try:
            validated_result = DeepAuditResult.model_validate(
                deep_audit_result.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "Deep audit context or result is incomplete.",
                retryable=False,
                validation_boundary="deep_audit_result_validation",
                validation_error=(
                    exc if isinstance(exc, ValidationError) else None
                ),
            ) from exc

        semantic_judgments = validated_result.semantic_judgments
        expected_pairs = {
            (claim.claim_id, evidence.block_id)
            for claim, evidence in self.semantic_pairs(bundle, evidence_records)
            if evidence.block_id is not None
        }
        actual_pairs = [
            (judgment.claim_id, judgment.block_id)
            for judgment in semantic_judgments
        ]
        if (
            len(actual_pairs) != len(set(actual_pairs))
            or set(actual_pairs) != expected_pairs
        ):
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "Semantic judgments must cover every verified evidence pair exactly once.",
                retryable=True,
                validation_boundary="semantic_pair_validation",
            )

        _validate_risk_findings(bundle.document, validated_result.risk_findings)
        safe_risk_findings = _redact_sensitive_risk_findings(
            validated_result.risk_findings
        )
        risk_points, risk_metrics = _risk_level(
            validated_context,
            safe_risk_findings,
        )
        try:
            risk_assessment = RiskAssessment(
                compliance_context=validated_context,
                risk_findings=safe_risk_findings,
                level_points=risk_points,
            )
        except ValidationError as exc:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "Risk assessment could not be completed.",
                retryable=False,
                validation_boundary="risk_assessment_construction",
                validation_error=exc,
            ) from exc

        try:
            dimensions, level_points = _build_dimensions(
                bundle,
                evidence_records,
                semantic_judgments,
                risk_points=risk_points,
                risk_metrics=risk_metrics,
            )
        except ValidationError as exc:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "All eight dimension results are required before scoring.",
                retryable=False,
                validation_boundary="dimension_construction",
                validation_error=exc,
            ) from exc
        if set(level_points) != set(DimensionId):
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "All eight dimension results are required before scoring.",
                retryable=False,
                validation_boundary="dimension_construction",
            )

        hard_failures = _deduplicate(
            _rule_hard_failures(bundle, evidence_records)
            + _semantic_hard_failures(bundle, semantic_judgments)
            + _risk_hard_failures(safe_risk_findings)
        )
        overall_score, core_gate_passed, decision = compute_audit_outcome(
            dimensions,
            hard_failures,
        )

        try:
            return AuditReport(
                audit_status=AuditStatus.DEEP_COMPLETE,
                dimensions=dimensions,
                risk_assessment=risk_assessment,
                hard_failures=hard_failures,
                core_gate_passed=core_gate_passed,
                overall_score=overall_score,
                decision=decision,
            )
        except ValidationError as exc:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "The final audit report could not be completed.",
                retryable=False,
                validation_boundary="final_report_construction",
                validation_error=exc,
            ) from exc

    def verify_claim_evidence(
        self,
        claim: AtomicClaim,
        source_blocks: list[SourceBlock],
    ) -> list[EvidenceRecord]:
        if claim.auditability == Auditability.NON_AUDITABLE:
            return [
                EvidenceRecord(
                    claim_id=claim.claim_id,
                    block_id=None,
                    page_index=None,
                    quote=None,
                    bbox=None,
                    match_method=MatchMethod.NONE,
                    quote_verified=False,
                    rule_flags=["NON_AUDITABLE"],
                )
            ]

        blocks_by_id = {block.block_id: block for block in source_blocks}
        candidate_flags: list[str] = []
        verified_candidates: list[EvidenceRecord] = []
        for block_id in claim.candidate_block_ids:
            block = blocks_by_id.get(block_id)
            if block is None:
                candidate_flags.append(
                    f"CANDIDATE_BLOCK_NOT_FOUND:{block_id}"
                )
                continue
            if claim.candidate_quote is None:
                candidate_flags.append(
                    f"CANDIDATE_QUOTE_MISSING:{block_id}"
                )
                continue
            if not self._quote_matches(claim.candidate_quote, block.text):
                candidate_flags.append(
                    f"CANDIDATE_QUOTE_NOT_FOUND:{block_id}"
                )
                continue
            verified_candidates.append(
                self._matched_record(
                    claim=claim,
                    block=block,
                    quote=claim.candidate_quote,
                    match_method=MatchMethod.MODEL_CANDIDATE,
                    candidate_flags=candidate_flags,
                )
            )

        if verified_candidates:
            final_candidate_flags = _deduplicate(candidate_flags)
            return [
                record.model_copy(
                    update={
                        "rule_flags": _deduplicate(
                            [*record.rule_flags, *final_candidate_flags]
                        )
                    }
                )
                for record in verified_candidates
            ]

        fallback_matches = self._bm25_fallback(claim, source_blocks)
        if fallback_matches:
            return [
                self._matched_record(
                    claim=claim,
                    block=block,
                    quote=quote,
                    match_method=MatchMethod.BM25_FALLBACK,
                    candidate_flags=candidate_flags,
                )
                for block, quote in fallback_matches
            ]

        return [
            EvidenceRecord(
                claim_id=claim.claim_id,
                block_id=None,
                page_index=None,
                quote=None,
                bbox=None,
                match_method=MatchMethod.NONE,
                quote_verified=False,
                rule_flags=_deduplicate(
                    [*candidate_flags, "INSUFFICIENT_EVIDENCE"]
                ),
            )
        ]

    @staticmethod
    def _quote_matches(quote: str, source_text: str) -> bool:
        normalized_quote = normalize_evidence_text(quote)
        return bool(normalized_quote) and normalized_quote in normalize_evidence_text(
            source_text
        )

    @staticmethod
    def _matched_record(
        *,
        claim: AtomicClaim,
        block: SourceBlock,
        quote: str,
        match_method: MatchMethod,
        candidate_flags: list[str],
    ) -> EvidenceRecord:
        return EvidenceRecord(
            claim_id=claim.claim_id,
            block_id=block.block_id,
            page_index=block.page_index,
            quote=quote,
            bbox=block.bbox,
            match_method=match_method,
            quote_verified=True,
            rule_flags=_deduplicate(
                [*candidate_flags, *_claim_source_flags(claim, quote)]
            ),
        )

    @staticmethod
    def _bm25_fallback(
        claim: AtomicClaim,
        source_blocks: list[SourceBlock],
    ) -> list[tuple[SourceBlock, str]]:
        claim_numbers = _claim_numbers(claim)
        claim_units = _units(claim.text)
        claim_negations = _negations(claim.text)
        eligible = [
            (block, fragment_index, fragment)
            for block in source_blocks
            for fragment_index, fragment in enumerate(
                _evidence_fragments(block.text)
            )
            if claim_numbers.issubset(_numbers(fragment))
            and claim_units.issubset(_units(fragment))
            and claim_negations == _negations(fragment)
        ]
        if not eligible:
            return []

        query_tokens = _tokenize(claim.text)
        query_content = {
            token
            for token in query_tokens
            if token not in _ENGLISH_STOP_WORDS
        }
        if not query_content:
            return []

        corpus = [_tokenize(fragment) for _, _, fragment in eligible]
        scores = BM25Okapi(corpus).get_scores(query_tokens)
        ranked: list[
            tuple[float, int, int, str, int, SourceBlock, str]
        ] = []
        for (
            (block, fragment_index, fragment),
            tokens,
            score,
        ) in zip(eligible, corpus, scores, strict=True):
            overlap = len(query_content.intersection(tokens))
            required_overlap = min(2, len(query_content))
            if overlap < required_overlap:
                continue
            ranked.append(
                (
                    -float(score),
                    -overlap,
                    block.reading_order,
                    block.block_id,
                    fragment_index,
                    block,
                    fragment,
                )
            )
        best_by_block: dict[
            str,
            tuple[float, int, int, str, int, SourceBlock, str],
        ] = {}
        for item in sorted(
            ranked,
            key=lambda candidate: (
                candidate[3],
                candidate[1],
                len(_tokenize(candidate[6])),
                candidate[0],
                candidate[4],
            ),
        ):
            best_by_block.setdefault(item[3], item)

        selected = sorted(best_by_block.values(), key=lambda item: item[:5])[:3]
        return [(item[5], item[6]) for item in selected]


def _tokenize(text: str) -> list[str]:
    normalized = normalize_evidence_text(text)
    tokens = _TOKEN.findall(normalized)
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        if any("\u4e00" <= character <= "\u9fff" for character in token):
            expanded.extend(
                token[index : index + 2]
                for index in range(max(0, len(token) - 1))
            )
    return expanded


def _evidence_fragments(text: str) -> list[str]:
    normalized_text = _normalize_hyphenated_line_breaks(text)
    atomic_fragments = [
        fragment.strip()
        for fragment in _EVIDENCE_FRAGMENT_BOUNDARY.split(normalized_text)
        if fragment.strip()
    ]
    if not atomic_fragments:
        return [normalized_text.strip()]

    adjacent_pairs = [
        f"{left} {right}"
        for left, right in zip(
            atomic_fragments,
            atomic_fragments[1:],
        )
    ]
    return [*atomic_fragments, *adjacent_pairs]


def _numbers(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text)
    return {_canonical_number(match.group(0)) for match in _NUMBER.finditer(normalized)}


def _claim_numbers(claim: AtomicClaim) -> set[str]:
    values = _numbers(claim.text)
    for entity in claim.numeric_entities:
        values.update(_numbers(entity))
    return values


def _canonical_number(value: str) -> str:
    return value.replace(",", "").casefold()


def _units(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text)
    return {match.group(1).casefold() for match in _UNIT.finditer(normalized)}


def _negations(text: str) -> set[str]:
    normalized = normalize_evidence_text(text)
    found: set[str] = set()
    for term in _NEGATION_TERMS:
        if term.isascii():
            if re.search(rf"\b{re.escape(term)}\b", normalized):
                found.add(term)
        elif term in normalized:
            found.add(term)
    return found


def _comparison_directions(text: str) -> set[str]:
    normalized = normalize_evidence_text(text)
    directions: set[str] = set()
    for direction, terms in _DIRECTION_TERMS.items():
        for term in terms:
            if term.isalpha() and term.isascii():
                matched = re.search(rf"\b{re.escape(term)}\b", normalized)
            else:
                matched = term in normalized
            if matched:
                directions.add(direction)
                break
    return directions


def _claim_source_flags(claim: AtomicClaim, source_text: str) -> list[str]:
    source_numbers = _numbers(source_text)
    flags = [
        f"NUMBER_MISMATCH:{number}"
        for number in sorted(_claim_numbers(claim) - source_numbers)
    ]
    source_units = _units(source_text)
    flags.extend(
        f"UNIT_MISMATCH:{unit}"
        for unit in sorted(_units(claim.text) - source_units)
    )
    if _negations(claim.text) != _negations(source_text):
        flags.append("NEGATION_MISMATCH")
    claim_directions = _comparison_directions(claim.text)
    source_directions = _comparison_directions(source_text)
    if claim_directions and not claim_directions.issubset(source_directions):
        flags.append("COMPARISON_DIRECTION_MISMATCH")
    return flags


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _build_dimensions(
    bundle: GeneratedBundle,
    evidence_records: list[EvidenceRecord],
    semantic_judgments: list[SemanticJudgment],
    *,
    risk_points: int,
    risk_metrics: dict[str, int | float],
) -> tuple[list[DimensionResult], dict[DimensionId, int]]:
    auditable_claims = [
        claim
        for claim in bundle.claims
        if claim.auditability != Auditability.NON_AUDITABLE
    ]
    records_by_claim = _records_by_claim(evidence_records)
    judgments_by_claim = _judgments_by_claim(semantic_judgments)
    claim_states = _claim_states(
        auditable_claims,
        records_by_claim,
        judgments_by_claim,
    )

    claim_weights = {
        claim.claim_id: 2 if claim.importance == Importance.CRITICAL else 1
        for claim in auditable_claims
    }
    total_claim_weight = sum(claim_weights.values())
    supported_weight = sum(
        claim_weights[claim.claim_id]
        for claim in auditable_claims
        if claim_states[claim.claim_id] == "supported"
    )
    critical_contradictions = sum(
        1
        for claim in auditable_claims
        if claim.importance == Importance.CRITICAL
        and claim_states[claim.claim_id] == "contradicted"
    )
    critical_insufficient = sum(
        1
        for claim in auditable_claims
        if claim.importance == Importance.CRITICAL
        and claim_states[claim.claim_id] == "insufficient"
    )
    factual_rate = _percentage(supported_weight, total_claim_weight)
    factual_points = _ratio_level(factual_rate, (95, 85, 70, 50))
    if critical_contradictions >= 2:
        factual_points = 0
    elif critical_contradictions == 1:
        factual_points = min(factual_points, 1)
    elif critical_insufficient:
        factual_points = min(factual_points, 2)

    citation_pairs = [
        (claim, block_id)
        for claim in auditable_claims
        for block_id in claim.candidate_block_ids
    ]
    forged_citations = {
        (record.claim_id, flag)
        for record in evidence_records
        for flag in record.rule_flags
        if _starts_with_any(flag, _FORGED_CITATION_PREFIXES)
    }
    judgments_by_pair = {
        (judgment.claim_id, judgment.block_id): judgment
        for judgment in semantic_judgments
    }
    accurate_citations = sum(
        1
        for claim, block_id in citation_pairs
        if (
            (judgment := judgments_by_pair.get((claim.claim_id, block_id)))
            is not None
        )
        and judgment.relation == Relation.SUPPORTS
        and any(
            record.match_method == MatchMethod.MODEL_CANDIDATE
            and record.quote_verified
            and record.block_id == block_id
            and not any(
                _starts_with_any(
                    flag,
                    _DETERMINISTIC_CONTRADICTION_PREFIXES,
                )
                for flag in record.rule_flags
            )
            for record in records_by_claim.get(claim.claim_id, [])
        )
    )
    citation_rate = _percentage(accurate_citations, len(citation_pairs))
    citation_points = _ratio_level(citation_rate, (95, 85, 70, 50))
    if len(forged_citations) >= 2:
        citation_points = 0
    elif forged_citations:
        citation_points = min(citation_points, 1)

    critical_claims = [
        claim
        for claim in auditable_claims
        if claim.importance == Importance.CRITICAL
    ]
    covered_critical = sum(
        1
        for claim in critical_claims
        if claim_states[claim.claim_id] == "supported"
        and any(
            record.quote_verified
            for record in records_by_claim.get(claim.claim_id, [])
        )
    )
    completeness_rate = _percentage(covered_critical, len(critical_claims))
    completeness_points = _ratio_level(completeness_rate, (90, 80, 60, 30))

    method_claims = [
        claim
        for claim in auditable_claims
        if claim.claim_type
        in {ClaimType.METHOD, ClaimType.RESULT, ClaimType.CONCLUSION}
    ]
    preserved_method_claims = sum(
        1
        for claim in method_claims
        if claim_states[claim.claim_id] == "supported"
        and _claim_scope_is_preserved(
            judgments_by_claim.get(claim.claim_id, [])
        )
    )
    method_rate = _percentage(preserved_method_claims, len(method_claims))
    method_scope_errors = [
        judgment
        for judgment in semantic_judgments
        if judgment.scope_status == ScopeStatus.EXPANDED
        and judgment.severity in {Severity.MAJOR, Severity.CRITICAL}
    ]
    method_points = _ratio_level(method_rate, (90, 75, 50, 25))
    if method_scope_errors:
        method_points = min(method_points, 1)

    conclusion_claims = [
        claim
        for claim in auditable_claims
        if claim.claim_type
        in {ClaimType.RESULT, ClaimType.CONCLUSION, ClaimType.LIMITATION}
    ]
    covered_conclusions = sum(
        1
        for claim in conclusion_claims
        if claim_states[claim.claim_id] == "supported"
        and _claim_scope_is_preserved(
            judgments_by_claim.get(claim.claim_id, [])
        )
    )
    conclusion_rate = _percentage(covered_conclusions, len(conclusion_claims))
    limitation_claims = [
        claim
        for claim in conclusion_claims
        if claim.claim_type == ClaimType.LIMITATION
    ]
    unsupported_limitations = sum(
        1
        for claim in limitation_claims
        if claim_states[claim.claim_id] != "supported"
    )
    conclusion_points = _ratio_level(conclusion_rate, (90, 75, 50, 25))
    if not limitation_claims:
        conclusion_points = 0
    elif unsupported_limitations:
        conclusion_points = min(conclusion_points, 2)

    terminology_points, terminology_issue_count = _terminology_level(
        semantic_judgments,
        has_auditable_claims=bool(auditable_claims),
    )
    reader_points, reader_checks_passed = _reader_adaptation_level(
        bundle.document,
        critical_claims,
        records_by_claim,
        semantic_judgments,
        terminology_points,
    )
    dimension_specs = [
        (
            DimensionId.FACTUAL_CONSISTENCY,
            {
                "supported_weight": supported_weight,
                "auditable_weight": total_claim_weight,
                "support_rate": factual_rate,
                "critical_contradictions": critical_contradictions,
                "critical_insufficient": critical_insufficient,
            },
            factual_points,
        ),
        (
            DimensionId.CITATION_CORRECTNESS,
            {
                "accurate_citations": accurate_citations,
                "citations": len(citation_pairs),
                "accuracy_rate": citation_rate,
                "forged_citations": len(forged_citations),
            },
            citation_points,
        ),
        (
            DimensionId.CITATION_COMPLETENESS,
            {
                "covered_critical_claims": covered_critical,
                "critical_claims": len(critical_claims),
                "coverage_rate": completeness_rate,
            },
            completeness_points,
        ),
        (
            DimensionId.METHOD_SCOPE,
            {
                "preserved_fields": preserved_method_claims,
                "applicable_fields": len(method_claims),
                "coverage_rate": method_rate,
                "major_scope_errors": len(method_scope_errors),
            },
            method_points,
        ),
        (
            DimensionId.CONCLUSION_LIMITATIONS,
            {
                "covered_items": covered_conclusions,
                "applicable_items": len(conclusion_claims),
                "coverage_rate": conclusion_rate,
                "unsupported_limitations": unsupported_limitations,
            },
            conclusion_points,
        ),
        (
            DimensionId.TERMINOLOGY,
            {
                "judgments": len(semantic_judgments),
                "terminology_issues": terminology_issue_count,
            },
            terminology_points,
        ),
        (
            DimensionId.READER_ADAPTATION,
            {
                "checks_passed": reader_checks_passed,
                "checks_total": 5,
            },
            reader_points,
        ),
        (
            DimensionId.RISK_COMPLIANCE,
            risk_metrics,
            risk_points,
        ),
    ]
    dimensions = [
        _dimension_result(dimension_id, raw_metrics, points)
        for dimension_id, raw_metrics, points in dimension_specs
    ]
    return dimensions, {
        dimension_id: points
        for dimension_id, _, points in dimension_specs
    }


def _records_by_claim(
    records: list[EvidenceRecord],
) -> dict[str, list[EvidenceRecord]]:
    grouped: dict[str, list[EvidenceRecord]] = {}
    for record in records:
        grouped.setdefault(record.claim_id, []).append(record)
    return grouped


def _judgments_by_claim(
    judgments: list[SemanticJudgment],
) -> dict[str, list[SemanticJudgment]]:
    grouped: dict[str, list[SemanticJudgment]] = {}
    for judgment in judgments:
        grouped.setdefault(judgment.claim_id, []).append(judgment)
    return grouped


def _claim_states(
    claims: list[AtomicClaim],
    records_by_claim: dict[str, list[EvidenceRecord]],
    judgments_by_claim: dict[str, list[SemanticJudgment]],
) -> dict[str, str]:
    states: dict[str, str] = {}
    for claim in claims:
        records = records_by_claim.get(claim.claim_id, [])
        judgments = judgments_by_claim.get(claim.claim_id, [])
        deterministic_contradiction = any(
            _starts_with_any(flag, _DETERMINISTIC_CONTRADICTION_PREFIXES)
            for record in records
            for flag in record.rule_flags
        )
        if deterministic_contradiction or any(
            judgment.relation == Relation.CONTRADICTS
            for judgment in judgments
        ):
            states[claim.claim_id] = "contradicted"
        elif any(
            judgment.relation == Relation.SUPPORTS
            for judgment in judgments
        ):
            states[claim.claim_id] = "supported"
        else:
            states[claim.claim_id] = "insufficient"
    return states


def _claim_scope_is_preserved(
    judgments: list[SemanticJudgment],
) -> bool:
    return bool(judgments) and all(
        judgment.scope_status == ScopeStatus.PRESERVED
        for judgment in judgments
    )


def _terminology_level(
    judgments: list[SemanticJudgment],
    *,
    has_auditable_claims: bool,
) -> tuple[int, int]:
    issues = [
        judgment
        for judgment in judgments
        if judgment.terminology_status != TerminologyStatus.CORRECT
    ]
    if not judgments and has_auditable_claims:
        return 0, 0
    critical = sum(
        judgment.severity == Severity.CRITICAL for judgment in issues
    )
    major = sum(judgment.severity == Severity.MAJOR for judgment in issues)
    if critical or major >= 2:
        points = 0
    elif major == 1:
        points = 1
    elif len(issues) >= 2:
        points = 2
    elif len(issues) == 1:
        points = 3
    else:
        points = 4
    return points, len(issues)


def _reader_adaptation_level(
    document: ContentDraft,
    critical_claims: list[AtomicClaim],
    records_by_claim: dict[str, list[EvidenceRecord]],
    judgments: list[SemanticJudgment],
    terminology_points: int,
) -> tuple[int, int]:
    checks = [
        not required_section_flags(
            section.section_id for section in document.sections
        ),
        terminology_points >= 3,
        all(
            not should_split_sentence(sentence.text)
            for section in document.sections
            for sentence in section.sentences
        ),
        all(
            any(
                record.quote_verified
                for record in records_by_claim.get(claim.claim_id, [])
            )
            for claim in critical_claims
        ),
        bool(judgments)
        and all(
            judgment.scope_status == ScopeStatus.PRESERVED
            for judgment in judgments
        ),
    ]
    passed = sum(checks)
    if passed == 5:
        points = 4
    elif passed == 4:
        points = 3
    elif passed == 3:
        points = 2
    elif passed >= 1:
        points = 1
    else:
        points = 0
    return points, passed


def _validate_risk_findings(
    document: ContentDraft,
    risk_findings: list[RiskFinding],
) -> None:
    categories = [finding.category for finding in risk_findings]
    if len(categories) != len(RiskCategory) or set(categories) != set(RiskCategory):
        raise _audit_service_error(
            "AUDIT_INCOMPLETE",
            "Risk findings must cover each required category exactly once.",
            retryable=True,
            validation_boundary="risk_category_validation",
        )

    sentence_texts = {
        sentence.sentence_id: sentence.text
        for section in document.sections
        for sentence in section.sentences
    }
    for finding in risk_findings:
        if finding.status == RiskStatus.DETECTED and not finding.locations:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "A detected risk finding requires at least one location.",
                retryable=True,
                validation_boundary="risk_location_validation",
            )
        if finding.status == RiskStatus.NOT_DETECTED and finding.locations:
            raise _audit_service_error(
                "AUDIT_INCOMPLETE",
                "A not-detected risk finding cannot contain locations.",
                retryable=True,
                validation_boundary="risk_location_validation",
            )

        for location in finding.locations:
            if location.location_type == RiskLocationType.SENTENCE:
                if location.sentence_id is None:
                    raise _audit_service_error(
                        "AUDIT_INCOMPLETE",
                        "A sentence risk location requires a sentence identifier.",
                        retryable=True,
                        validation_boundary="risk_location_validation",
                    )
                source_text = sentence_texts.get(location.sentence_id or "")
                if source_text is None:
                    raise _audit_service_error(
                        "AUDIT_INCOMPLETE",
                        "A risk location references an unknown generated sentence.",
                        retryable=True,
                        validation_boundary="risk_location_validation",
                    )
            elif location.location_type == RiskLocationType.TITLE:
                if location.sentence_id is not None:
                    raise _audit_service_error(
                        "AUDIT_INCOMPLETE",
                        "A title risk location cannot contain a sentence identifier.",
                        retryable=True,
                        validation_boundary="risk_location_validation",
                    )
                source_text = document.title
            else:
                if (
                    location.sentence_id is not None
                    or location.evidence_excerpt is not None
                ):
                    raise _audit_service_error(
                        "AUDIT_INCOMPLETE",
                        "A document risk location must not contain sentence details.",
                        retryable=True,
                        validation_boundary="risk_location_validation",
                    )
                source_text = ""

            if (
                finding.category == RiskCategory.SENSITIVE_INFORMATION
                and location.evidence_excerpt is not None
            ):
                raise _audit_service_error(
                    "AUDIT_INCOMPLETE",
                    "Sensitive risk locations cannot contain evidence excerpts.",
                    retryable=True,
                    validation_boundary="risk_excerpt_validation",
                )

            if location.evidence_excerpt is not None and (
                not normalize_evidence_text(location.evidence_excerpt)
                or normalize_evidence_text(location.evidence_excerpt)
                not in normalize_evidence_text(source_text)
            ):
                raise _audit_service_error(
                    "AUDIT_INCOMPLETE",
                    "A risk excerpt could not be verified in generated content.",
                    retryable=True,
                    validation_boundary="risk_excerpt_validation",
                )

def _risk_level(
    compliance_context: ComplianceContext,
    risk_findings: list[RiskFinding],
) -> tuple[int, dict[str, int | float]]:
    missing_disclosures = sum(
        status.value == "missing"
        for status in (
            compliance_context.source_disclosure_status,
            compliance_context.ai_assistance_disclosure_status,
        )
    )
    unclear_findings = sum(
        finding.status == RiskStatus.UNCLEAR for finding in risk_findings
    )
    detected_findings = sum(
        finding.status == RiskStatus.DETECTED for finding in risk_findings
    )
    rights_missing = int(not compliance_context.rights_or_license_confirmed)
    required_label_missing = int(
        compliance_context.generated_content_label_applicability.value
        == "applicable"
        and compliance_context.generated_content_label_status.value == "missing"
    )

    points = compute_risk_level_points(compliance_context, risk_findings)

    return points, {
        "missing_disclosures": missing_disclosures,
        "unclear_findings": unclear_findings,
        "detected_findings": detected_findings,
        "rights_or_license_not_confirmed": rights_missing,
        "required_label_missing": required_label_missing,
        "risk_issues": (
            missing_disclosures
            + unclear_findings
            + detected_findings
            + rights_missing
            + required_label_missing
        ),
    }


def _risk_hard_failures(risk_findings: list[RiskFinding]) -> list[str]:
    return fixed_risk_hard_failures(risk_findings)


def _redact_sensitive_risk_findings(
    risk_findings: list[RiskFinding],
) -> list[RiskFinding]:
    redacted: list[RiskFinding] = []
    for finding in risk_findings:
        if finding.category != RiskCategory.SENSITIVE_INFORMATION:
            redacted.append(finding)
            continue
        reason, remediation = code_owned_sensitive_text(finding.status)
        redacted.append(
            finding.model_copy(
                update={
                    "reason": reason,
                    "remediation": remediation,
                }
            )
        )
    return redacted


def _dimension_result(
    dimension_id: DimensionId,
    raw_metrics: dict[str, int | float],
    level_points: int,
) -> DimensionResult:
    return DimensionResult(
        dimension_id=dimension_id,
        raw_metrics={**raw_metrics, "level_points": level_points},
        score=dimension_score(level_points),
        level=dimension_level(level_points),
    )


def _ratio_level(
    percentage: float,
    thresholds: tuple[int, int, int, int],
) -> int:
    for points, threshold in zip((4, 3, 2, 1), thresholds, strict=True):
        if percentage >= threshold:
            return points
    return 0


def _percentage(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        return 0.0
    return round(float(numerator) / float(denominator) * 100, 2)


def _rule_hard_failures(
    bundle: GeneratedBundle,
    evidence_records: list[EvidenceRecord],
) -> list[str]:
    claims = {claim.claim_id: claim for claim in bundle.claims}
    failures: list[str] = []
    for record in evidence_records:
        claim = claims.get(record.claim_id)
        if claim is None:
            continue
        for flag in record.rule_flags:
            if _starts_with_any(flag, _FORGED_CITATION_PREFIXES):
                failures.append(
                    f"FORGED_CITATION:{record.claim_id}:{flag}"
                )
            if claim.importance != Importance.CRITICAL:
                continue
            if flag.startswith("NUMBER_MISMATCH:"):
                failures.append(f"CRITICAL_NUMBER_ERROR:{record.claim_id}")
            elif flag.startswith("UNIT_MISMATCH:"):
                failures.append(f"CRITICAL_UNIT_ERROR:{record.claim_id}")
            elif flag in {
                "NEGATION_MISMATCH",
                "COMPARISON_DIRECTION_MISMATCH",
            }:
                failures.append(
                    f"CRITICAL_DIRECTION_ERROR:{record.claim_id}"
                )
    return _deduplicate(failures)


def _semantic_hard_failures(
    bundle: GeneratedBundle,
    judgments: list[SemanticJudgment],
) -> list[str]:
    claims = {claim.claim_id: claim for claim in bundle.claims}
    failures: list[str] = []
    for judgment in judgments:
        claim = claims.get(judgment.claim_id)
        if claim is None or claim.importance != Importance.CRITICAL:
            continue
        if judgment.relation == Relation.CONTRADICTS:
            failures.append(
                f"CRITICAL_CONTRADICTION:{judgment.claim_id}"
            )
        if (
            judgment.scope_status == ScopeStatus.EXPANDED
            and judgment.severity in {Severity.MAJOR, Severity.CRITICAL}
        ):
            failures.append(
                f"CRITICAL_SCOPE_EXPANSION:{judgment.claim_id}"
            )
    return _deduplicate(failures)


def _starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)
