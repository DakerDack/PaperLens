import json
import logging
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import backend.app.audit_service as audit_module

from backend.app.audit_service import (
    DIMENSION_WEIGHTS,
    AuditService,
    AuditServiceError,
    normalize_evidence_text,
    required_section_flags,
    should_split_sentence,
)
from backend.app.hy3_service import Hy3Service, SAFE_DIAGNOSTIC_MAX_LENGTH
from backend.app.models import (
    AtomicClaim,
    AuditStatus,
    ComplianceContext,
    DeepAuditResult,
    Decision,
    DisclosureStatus,
    DimensionId,
    EvidenceRecord,
    GeneratedContentLabelApplicability,
    GeneratedContentLabelStatus,
    GeneratedBundle,
    RiskFinding,
    SectionId,
    SemanticJudgment,
    Severity,
    SourceBlock,
    TerminologyStatus,
)


FIXTURES = Path(__file__).parent / "fixtures"
SAFE_DIAGNOSTIC_FIELD_NAMES = {
    "operation",
    "attempt",
    "retry_count",
    "completion_tokens",
    "configured_completion_limit",
    "completion_limit_reached",
    "finish_reason",
    "validation_boundary",
    "error_code",
    "validation_error_count",
    "validation_error_type",
    "validation_location",
}


class InvalidModelPayload:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return self.payload


def audit_postprocessing_logs(caplog: object) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("audit_postprocessing_failure ")
    ]


def audit_log_fields(message: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in message.split()[1:])


def assert_safe_audit_failure_log(
    caplog: object,
    *,
    boundary: str,
    error_code: str = "AUDIT_INCOMPLETE",
    validation_error_count: str = "0",
    validation_error_type: str = "none",
    validation_location: str = "none",
) -> None:
    logs = audit_postprocessing_logs(caplog)
    assert len(logs) == 1
    fields = audit_log_fields(logs[0])
    assert set(fields) == SAFE_DIAGNOSTIC_FIELD_NAMES
    assert fields == {
        "operation": "audit_postprocessing",
        "attempt": "0",
        "retry_count": "0",
        "completion_tokens": "null",
        "configured_completion_limit": "null",
        "completion_limit_reached": "false",
        "finish_reason": "missing",
        "validation_boundary": boundary,
        "error_code": error_code,
        "validation_error_count": validation_error_count,
        "validation_error_type": validation_error_type,
        "validation_location": validation_location,
    }
    assert SAFE_DIAGNOSTIC_MAX_LENGTH == 512
    assert len(logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH
    for sentinel in (
        "PROMPT_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "SOURCE_BLOCK_SENTINEL",
        "CLAIM_TEXT_SENTINEL",
        "EVIDENCE_TEXT_SENTINEL",
        "API_KEY_SENTINEL",
        "PYDANTIC_INPUT_SENTINEL",
    ):
        assert sentinel not in logs[0]


def source_block(
    block_id: str,
    text: str,
    *,
    page_index: int = 0,
    reading_order: int = 0,
) -> SourceBlock:
    return SourceBlock(
        block_id=block_id,
        page_index=page_index,
        type="text",
        text=text,
        bbox=(0.1, 0.2, 0.8, 0.3),
        reading_order=reading_order,
        parser="mineru",
        parser_version="3.4.5",
    )


def atomic_claim(
    text: str,
    *,
    candidate_block_ids: list[str],
    candidate_quote: str | None,
    numeric_entities: list[str] | None = None,
    claim_id: str = "c-001",
    importance: str = "critical",
) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        sentence_id="s-001",
        text=text,
        claim_type="result",
        importance=importance,
        qualifiers=[],
        numeric_entities=numeric_entities or [],
        auditability="auditable",
        candidate_block_ids=candidate_block_ids,
        candidate_quote=candidate_quote,
    )


def only_record(records: list[EvidenceRecord]) -> EvidenceRecord:
    assert len(records) == 1
    return records[0]


def generated_bundle() -> GeneratedBundle:
    return GeneratedBundle.model_validate_json(
        (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")
    )


def source_blocks_fixture() -> list[SourceBlock]:
    payload = json.loads(
        (FIXTURES / "source_blocks.json").read_text(encoding="utf-8")
    )
    return TypeAdapter(list[SourceBlock]).validate_python(payload)


def compliance_context(**updates: object) -> ComplianceContext:
    payload: dict[str, object] = {
        "rights_or_license_confirmed": True,
        "source_disclosure_status": "present",
        "ai_assistance_disclosure_status": "present",
        "generated_content_label_applicability": "not_applicable",
        "generated_content_label_status": "not_applicable",
    }
    payload.update(updates)
    return ComplianceContext.model_validate(payload)


def clear_risk_findings() -> list[RiskFinding]:
    return [
        RiskFinding(
            category=category,
            status="not_detected",
            locations=[],
            reason="No risk was detected.",
            remediation="No change is required.",
        )
        for category in (
            "sensitive_information",
            "author_impersonation",
            "academic_integrity",
        )
    ]


def risk_findings_with(
    category: str,
    status: str,
    *,
    locations: list[dict[str, object]] | None = None,
    reason: str = "The structured risk signal was detected.",
    remediation: str = "Revise the identified generated content.",
) -> list[RiskFinding]:
    findings = clear_risk_findings()
    replacement = RiskFinding.model_validate(
        {
            "category": category,
            "status": status,
            "locations": locations or [],
            "reason": reason,
            "remediation": remediation,
        }
    )
    return [
        replacement if finding.category.value == category else finding
        for finding in findings
    ]


def deep_result(
    judgments: list[SemanticJudgment],
    *,
    risk_findings: list[RiskFinding] | None = None,
) -> DeepAuditResult:
    return DeepAuditResult(
        expression_findings=[{"category": category, "status": "not_detected", "locations": []}
                             for category in ("redundancy_or_off_topic", "unexplained_terminology")],
        semantic_judgments=judgments,
        risk_findings=(
            clear_risk_findings() if risk_findings is None else risk_findings
        ),
    )


class RecordingDeepAudit:
    def __init__(
        self,
        *,
        omit_last: bool = False,
        risk_findings: list[RiskFinding] | None = None,
    ) -> None:
        self.omit_last = omit_last
        self.risk_findings = risk_findings
        self.calls: list[list[tuple[AtomicClaim, EvidenceRecord]]] = []
        self.documents = []

    def deep_audit(
        self,
        *,
        document,
        claim_evidence_pairs: list[tuple[AtomicClaim, EvidenceRecord]],
    ) -> DeepAuditResult:
        self.documents.append(document)
        self.calls.append(claim_evidence_pairs)
        pairs = claim_evidence_pairs[:-1] if self.omit_last else claim_evidence_pairs
        return deep_result(
            [
                SemanticJudgment(
                    claim_id=claim.claim_id,
                    block_id=evidence.block_id,
                    relation="supports",
                    scope_status="preserved",
                    terminology_status="correct",
                    severity="none",
                    reason="The verified evidence supports this claim.",
                )
                for claim, evidence in pairs
                if evidence.block_id is not None
            ],
            risk_findings=self.risk_findings,
        )


@pytest.mark.parametrize("category", ["redundancy_or_off_topic", "unexplained_terminology"])
def test_document_expression_contract_scoring_counts_category_once(category):
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    service = RecordingDeepAudit()
    result = service.deep_audit(document=bundle.document, claim_evidence_pairs=AuditService().semantic_pairs(bundle, records))
    baseline = AuditService().score(bundle, records, result, compliance_context())
    payload = result.model_dump(mode="json")
    finding = next(item for item in payload["expression_findings"] if item["category"] == category)
    sentence = bundle.document.sections[0].sentences[0]
    location = {"location_type": "sentence", "sentence_id": sentence.sentence_id, "evidence_excerpt": sentence.text[:100]}
    finding.update(status="detected", locations=[location, location.copy()])
    result = DeepAuditResult.model_validate(payload)
    report = AuditService().score(bundle, records, result, compliance_context())
    for before, after in zip(baseline.dimensions, report.dimensions):
        if before.dimension_id.value == "reader_adaptation":
            assert after.raw_metrics["checks_passed"] == before.raw_metrics["checks_passed"] - 1
        else:
            assert before == after
    assert report.hard_failures == baseline.hard_failures


def test_document_expression_contract_unclear_preserves_observed_fact_alert():
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    result = RecordingDeepAudit().deep_audit(document=bundle.document, claim_evidence_pairs=AuditService().semantic_pairs(bundle, records))
    payload = result.model_dump(mode="json")
    payload["expression_findings"][0]["status"] = "unclear"
    payload["semantic_judgments"][0].update(relation="contradicts", severity="critical")
    with pytest.raises(AuditServiceError) as caught:
        AuditService().score(bundle, records, DeepAuditResult.model_validate(payload), compliance_context())
    assert caught.value.error_code == "AUDIT_INCOMPLETE"
    assert caught.value.retryable is False
    assert caught.value.safe_metrics["observed_factual_issue_codes"]
    assert "overall_score" not in caught.value.safe_metrics

    payload["semantic_judgments"][0].update(relation="supports", scope_status="expanded", severity="minor")
    with pytest.raises(AuditServiceError) as minor:
        AuditService().score(bundle, records, DeepAuditResult.model_validate(payload), compliance_context())
    assert minor.value.safe_metrics.get("observed_factual_alert_count", 0) > 0


def test_document_expression_contract_legacy_score_is_explicit():
    from backend.app.models import DeepAuditResultV2
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    old = DeepAuditResultV2.model_validate_json((FIXTURES / "deep_audit_valid.json").read_text(encoding="utf-8"))
    report = AuditService().score_legacy_v2(bundle, records, old, compliance_context())
    assert report.audit_status == AuditStatus.DEEP_COMPLETE
    with pytest.raises(AuditServiceError):
        AuditService().score(bundle, records, old, compliance_context())


@pytest.mark.parametrize("text", [
    "Keep the sample sealed during handling. This reminder repeats the handling instruction for safety.",
    "Luma denotes the indicator color in this explanation; the Luma label is used consistently below.",
    "The descriptive guide follows the arrangement of the components along the edge of the container and names each part in the same order as the accompanying overview for readers who are inspecting the setup for the first time.",
])
def test_document_expression_contract_negative_controls_keep_existing_checks(text):
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    provider = RecordingDeepAudit()
    audit = AuditService(hy3_service=provider)
    _, baseline = audit.run_deep_audit(bundle, records, compliance_context())
    sentence = bundle.document.sections[0].sentences[0].model_copy(update={"sentence_id": "expression-control", "text": text})
    bundle.document.sections[0].sentences.append(sentence)
    _, after = audit.run_deep_audit(bundle, records, compliance_context())
    assert after.dimensions == baseline.dimensions
    assert bool(provider.documents[-1] == bundle.document), "FULL_DOCUMENT_REQUIRED"


def test_evidence_correct_model_block_and_quote_copy_source_location() -> None:
    block = source_block(
        "p04-b012",
        "The study enrolled 69 students.",
        page_index=3,
    )
    claim = atomic_claim(
        "The study enrolled 69 students.",
        candidate_block_ids=["p04-b012"],
        candidate_quote="The study enrolled 69 students.",
        numeric_entities=["69"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.claim_id == "c-001"
    assert record.block_id == "p04-b012"
    assert record.page_index == 3
    assert record.bbox == block.bbox
    assert record.quote == "The study enrolled 69 students."
    assert record.quote_verified is True
    assert record.match_method == "model_candidate"
    assert record.rule_flags == []


def test_normalization_accepts_unicode_whitespace_newline_and_hyphenation() -> None:
    block = source_block(
        "p01-b001",
        "The inter-\nvention enrolled ６９ students.\nNo adverse events.",
    )
    quote = "The intervention enrolled 69   students. No adverse events."
    claim = atomic_claim(
        quote,
        candidate_block_ids=["p01-b001"],
        candidate_quote=quote,
        numeric_entities=["69"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert normalize_evidence_text(block.text) == normalize_evidence_text(quote)
    assert record.quote_verified is True
    assert record.match_method == "model_candidate"
    assert record.rule_flags == []


def test_evidence_fake_block_uses_bm25_top_three_with_exact_constraints() -> None:
    blocks = [
        source_block(
            "p01-b001",
            "The treatment did not reduce pain after the 5 mg dose.",
            reading_order=0,
        ),
        source_block(
            "p01-b002",
            "At 5 mg, the treatment did not reduce the measured symptom.",
            reading_order=1,
        ),
        source_block(
            "p01-b003",
            "The control did not change after a 5 mg dose.",
            reading_order=2,
        ),
        source_block(
            "p01-b004",
            "Participants did not improve with the 5 mg dose.",
            reading_order=3,
        ),
        source_block(
            "p01-b005",
            "The treatment reduced pain after a 5 mg dose.",
            reading_order=4,
        ),
        source_block(
            "p01-b006",
            "The treatment did not reduce pain after the 5 kg dose.",
            reading_order=5,
        ),
    ]
    claim = atomic_claim(
        "The treatment did not reduce pain after the 5 mg dose.",
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["5"],
    )

    records = AuditService().verify_claim_evidence(claim, blocks)

    assert len(records) == 3
    assert records[0].block_id == "p01-b001"
    assert all(record.match_method == "bm25_fallback" for record in records)
    assert all(record.quote_verified is True for record in records)
    assert all("CANDIDATE_BLOCK_NOT_FOUND:p99-b999" in record.rule_flags for record in records)
    assert "p01-b005" not in {record.block_id for record in records}
    assert "p01-b006" not in {record.block_id for record in records}


def test_evidence_fake_quote_is_located_even_when_bm25_recovers_block() -> None:
    block = source_block(
        "p01-b001",
        "The study enrolled 69 students from one university.",
    )
    claim = atomic_claim(
        "The study enrolled 69 students from one university.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="The study enrolled 96 students from two universities.",
        numeric_entities=["69"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "bm25_fallback"
    assert record.block_id == "p01-b001"
    assert "CANDIDATE_QUOTE_NOT_FOUND:p01-b001" in record.rule_flags


def test_evidence_detects_changed_number_with_claim_location() -> None:
    block = source_block("p01-b001", "The study enrolled 69 students.")
    claim = atomic_claim(
        "The study enrolled 70 students.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="The study enrolled 69 students.",
        numeric_entities=["70"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert "NUMBER_MISMATCH:70" in record.rule_flags


def test_evidence_detects_changed_unit_with_claim_location() -> None:
    block = source_block("p01-b001", "Participants received a 5 mg dose.")
    claim = atomic_claim(
        "Participants received a 5 kg dose.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="Participants received a 5 mg dose.",
        numeric_entities=["5"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert "UNIT_MISMATCH:kg" in record.rule_flags


def test_evidence_does_not_truncate_milliseconds_to_metres() -> None:
    block = source_block("p01-b001", "Latency was 5 ms.")
    claim = atomic_claim(
        "Latency was 5 m.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="Latency was 5 ms.",
        numeric_entities=["5"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert "UNIT_MISMATCH:m" in record.rule_flags


def test_evidence_detects_negation_reversal() -> None:
    block = source_block("p01-b001", "The treatment did not improve accuracy.")
    claim = atomic_claim(
        "The treatment did improve accuracy.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="The treatment did not improve accuracy.",
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert "NEGATION_MISMATCH" in record.rule_flags


def test_model_candidate_ignores_negation_in_unrelated_fragment() -> None:
    block = source_block(
        "p01-b001",
        "The treatment improved accuracy. No unrelated adverse events were reported.",
    )
    claim = atomic_claim(
        "The treatment improved accuracy.",
        candidate_block_ids=["p01-b001"],
        candidate_quote=block.text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert record.quote_verified is True
    assert "NEGATION_MISMATCH" not in record.rule_flags


def test_negation_mismatch_requires_comparable_proposition() -> None:
    block = source_block(
        "p01-b001",
        "The treatment did not worsen accuracy.",
    )
    claim = atomic_claim(
        "The treatment improved accuracy.",
        candidate_block_ids=["p01-b001"],
        candidate_quote=block.text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert "NEGATION_MISMATCH" not in record.rule_flags


@pytest.mark.parametrize(
    ("claim_text", "evidence_text"),
    (
        (
            "Participants were in contact.",
            "Participants were in no-contact condition.",
        ),
        (
            "Participants were in no-contact condition.",
            "Participants were in contact.",
        ),
    ),
)
def test_hyphenated_negation_reversal_is_detected(
    claim_text: str,
    evidence_text: str,
) -> None:
    block = source_block("p01-b001", evidence_text)
    claim = atomic_claim(
        claim_text,
        candidate_block_ids=["p01-b001"],
        candidate_quote=evidence_text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert record.quote_verified is True
    assert "NEGATION_MISMATCH" in record.rule_flags


def test_hyphenated_negation_in_unrelated_fragment_is_ignored() -> None:
    block = source_block(
        "p01-b001",
        "Participants were in contact. The no-contact arm was excluded.",
    )
    claim = atomic_claim(
        "Participants were in contact.",
        candidate_block_ids=["p01-b001"],
        candidate_quote=block.text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert record.quote_verified is True
    assert "NEGATION_MISMATCH" not in record.rule_flags


@pytest.mark.parametrize("embedded_term", ("nobody", "notable"))
def test_embedded_negation_boundary_does_not_flag(
    embedded_term: str,
) -> None:
    block = source_block("p01-b001", f"The report described {embedded_term}.")
    claim = atomic_claim(
        f"The report described {embedded_term}.",
        candidate_block_ids=["p01-b001"],
        candidate_quote=block.text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert audit_module._negations(embedded_term) == set()
    assert "NEGATION_MISMATCH" not in record.rule_flags


@pytest.mark.parametrize(
    ("claim_text", "evidence_text", "negation_mismatch"),
    (
        (
            "The treatment did not improve accuracy.",
            "The treatment had no improvement in accuracy.",
            False,
        ),
        (
            "该方法未提升准确率。",
            "该方法没有提升准确率。",
            False,
        ),
        (
            "The treatment improved accuracy.",
            "The treatment did not improve accuracy.",
            True,
        ),
    ),
)
def test_negation_equivalence_compares_polarity_not_marker_identity(
    claim_text: str,
    evidence_text: str,
    negation_mismatch: bool,
) -> None:
    block = source_block("p01-b001", evidence_text)
    claim = atomic_claim(
        claim_text,
        candidate_block_ids=["p01-b001"],
        candidate_quote=evidence_text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert ("NEGATION_MISMATCH" in record.rule_flags) is negation_mismatch


@pytest.mark.parametrize(
    ("claim_text", "evidence_text"),
    (
        (
            "The treatment did not improve accuracy.",
            "The treatment had no improvement in accuracy.",
        ),
        (
            "该方法未提升准确率。",
            "该方法没有提升准确率。",
        ),
    ),
)
def test_negation_equivalence_bm25_fallback(
    claim_text: str,
    evidence_text: str,
) -> None:
    block = source_block("p01-b001", evidence_text)
    claim = atomic_claim(
        claim_text,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "bm25_fallback"
    assert record.quote_verified is True
    assert "NEGATION_MISMATCH" not in record.rule_flags
    assert "CANDIDATE_BLOCK_NOT_FOUND:p99-b999" in record.rule_flags


def test_evidence_detects_comparison_direction_change() -> None:
    block = source_block("p01-b001", "Accuracy decreased relative to baseline.")
    claim = atomic_claim(
        "Accuracy increased relative to baseline.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="Accuracy decreased relative to baseline.",
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert "COMPARISON_DIRECTION_MISMATCH" in record.rule_flags


@pytest.mark.parametrize(
    ("claim_text", "evidence_text"),
    (
        (
            "The process completed more slowly than the baseline.",
            "The process completed slower than the baseline.",
        ),
        (
            "The process completed slower than the baseline.",
            "The process completed more slowly than the baseline.",
        ),
    ),
)
def test_comparison_direction_treats_more_slowly_as_slower(
    claim_text: str,
    evidence_text: str,
) -> None:
    block = source_block("p01-b001", evidence_text)
    claim = atomic_claim(
        claim_text,
        candidate_block_ids=["p01-b001"],
        candidate_quote=evidence_text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert record.quote_verified is True
    assert "COMPARISON_DIRECTION_MISMATCH" not in record.rule_flags


@pytest.mark.parametrize(
    ("claim_text", "evidence_text"),
    (
        (
            "The process completed more quickly than the baseline.",
            "The process completed faster than the baseline.",
        ),
        (
            "The process completed faster than the baseline.",
            "The process completed more quickly than the baseline.",
        ),
    ),
)
def test_comparison_direction_treats_more_quickly_as_faster(
    claim_text: str,
    evidence_text: str,
) -> None:
    block = source_block("p01-b001", evidence_text)
    claim = atomic_claim(
        claim_text,
        candidate_block_ids=["p01-b001"],
        candidate_quote=evidence_text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert record.quote_verified is True
    assert "COMPARISON_DIRECTION_MISMATCH" not in record.rule_flags


@pytest.mark.parametrize(
    ("claim_text", "evidence_text"),
    (
        (
            "The process completed faster than the baseline.",
            "The process completed slower than the baseline.",
        ),
        (
            "The process completed slower than the baseline.",
            "The process completed faster than the baseline.",
        ),
        (
            "The process completed more quickly than the baseline.",
            "The process completed more slowly than the baseline.",
        ),
        (
            "The process completed more slowly than the baseline.",
            "The process completed more quickly than the baseline.",
        ),
    ),
)
def test_comparison_direction_detects_faster_vs_slower(
    claim_text: str,
    evidence_text: str,
) -> None:
    block = source_block("p01-b001", evidence_text)
    claim = atomic_claim(
        claim_text,
        candidate_block_ids=["p01-b001"],
        candidate_quote=evidence_text,
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "model_candidate"
    assert record.quote_verified is True
    assert "COMPARISON_DIRECTION_MISMATCH" in record.rule_flags


def test_evidence_without_valid_candidate_or_recall_is_insufficient() -> None:
    block = source_block("p01-b001", "The study used interviews only.")
    claim = atomic_claim(
        "The trial administered 10 mg and did not improve outcomes.",
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated",
        numeric_entities=["10"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.match_method == "none"
    assert record.block_id is None
    assert record.page_index is None
    assert record.quote is None
    assert record.quote_verified is False
    assert "CANDIDATE_BLOCK_NOT_FOUND:p99-b999" in record.rule_flags
    assert "INSUFFICIENT_EVIDENCE" in record.rule_flags


def test_split_trigger_only_selects_sentences_with_independent_facts() -> None:
    assert should_split_sentence(
        "Treatment reduced pain by 10%, and accuracy increased by 5%."
    )
    assert should_split_sentence(
        "Accuracy increased relative to baseline, but latency decreased."
    )
    assert should_split_sentence(
        "Accuracy increased, because the model learned a causal rule."
    )

    assert not should_split_sentence("Accuracy increased by 10%.")
    assert not should_split_sentence("The dose was 5 mg for 10 days.")
    assert not should_split_sentence("The study used interviews and surveys.")


def test_required_section_check_reports_the_missing_region() -> None:
    flags = required_section_flags(
        [
            SectionId.RESEARCH_QUESTION,
            SectionId.METHODS,
            SectionId.RESULTS,
            SectionId.PLAIN_EXPLANATION,
        ]
    )

    assert flags == ["MISSING_REQUIRED_SECTION:limitations"]


def test_quick_complete_keeps_score_and_decision_pending() -> None:
    records, report = AuditService().quick_check(
        generated_bundle(),
        source_blocks_fixture(),
    )

    assert {record.claim_id for record in records} == {
        "c-001",
        "c-002",
        "c-003",
        "c-004",
        "c-005",
    }
    assert report.audit_status == AuditStatus.QUICK_COMPLETE
    assert report.dimensions == []
    assert report.core_gate_passed is None
    assert report.overall_score is None
    assert report.decision == Decision.PENDING_DEEP_AUDIT
    assert report.risk_assessment is None


def test_quick_report_keeps_hard_failures_empty_while_records_keep_rule_flags() -> None:
    bundle = generated_bundle()
    altered_claim = bundle.claims[0].model_copy(
        update={"candidate_quote": "A fabricated quotation."}
    )
    altered_bundle = bundle.model_copy(
        update={"claims": [altered_claim, *bundle.claims[1:]]}
    )

    records, report = AuditService().quick_check(
        altered_bundle,
        source_blocks_fixture(),
    )

    first_claim_records = [record for record in records if record.claim_id == "c-001"]
    assert first_claim_records
    assert any(
        "CANDIDATE_QUOTE_NOT_FOUND:p01-b001" in record.rule_flags
        for record in first_claim_records
    )
    assert report.audit_status == AuditStatus.QUICK_COMPLETE
    assert report.dimensions == []
    assert report.risk_assessment is None
    assert report.hard_failures == []
    assert report.core_gate_passed is None
    assert report.overall_score is None
    assert report.decision == Decision.PENDING_DEEP_AUDIT


def test_deep_audit_batches_once_and_code_builds_all_eight_dimensions() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    semantic_service = RecordingDeepAudit()
    result, report = AuditService(
        hy3_service=semantic_service
    ).run_deep_audit(bundle, records, compliance_context())

    assert len(semantic_service.calls) == 1
    assert len(result.semantic_judgments) == len(semantic_service.calls[0])
    assert all(evidence.quote_verified for _, evidence in semantic_service.calls[0])
    assert semantic_service.documents == [bundle.document]
    assert report.audit_status == AuditStatus.DEEP_COMPLETE
    assert {item.dimension_id for item in report.dimensions} == set(DimensionId)
    assert len(report.dimensions) == 8
    assert all(item.raw_metrics["level_points"] == 4 for item in report.dimensions)
    assert report.hard_failures == []
    assert report.core_gate_passed is True
    assert report.overall_score == 100.0
    assert report.decision == Decision.QUALIFIED
    assert report.risk_assessment is not None
    assert report.risk_assessment.level_points == 4


def test_risk_only_deep_audit_completes_with_empty_pairs() -> None:
    bundle = generated_bundle().model_copy(update={"claims": []})
    semantic_service = RecordingDeepAudit()

    result, report = AuditService(
        hy3_service=semantic_service
    ).run_deep_audit(bundle, [], compliance_context())

    assert semantic_service.calls == [[]]
    assert result.semantic_judgments == []
    assert len(result.risk_findings) == 3
    assert report.audit_status == AuditStatus.DEEP_COMPLETE
    assert len(report.dimensions) == 8
    assert {item.dimension_id for item in report.dimensions} == set(DimensionId)
    assert all(
        item.raw_metrics.get("total_claims", 0) == 0
        for item in report.dimensions
    )


def test_run_deep_audit_rejects_unconfirmed_rights_before_hy3_call() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    recording = RecordingDeepAudit()
    service = AuditService(hy3_service=recording)

    with pytest.raises(AuditServiceError) as exc_info:
        service.run_deep_audit(
            bundle,
            records,
            compliance_context(rights_or_license_confirmed=False),
        )

    assert exc_info.value.error_code == "RIGHTS_NOT_CONFIRMED"
    assert exc_info.value.retryable is False
    assert recording.calls == []
    assert recording.documents == []


def test_deep_audit_rejects_incomplete_semantic_batch_without_score() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    service = AuditService(hy3_service=RecordingDeepAudit(omit_last=True))

    try:
        service.run_deep_audit(bundle, records, compliance_context())
    except AuditServiceError as exc:
        assert exc.error_code == "AUDIT_INCOMPLETE"
        assert exc.retryable is True
    else:
        raise AssertionError("Incomplete semantic output must not be scored")


def test_dimension_weights_sum_strictly_to_one() -> None:
    assert set(DIMENSION_WEIGHTS) == set(DimensionId)
    assert sum(DIMENSION_WEIGHTS.values()) == 1.0


def test_hard_failure_cannot_be_offset_by_other_high_scores() -> None:
    bundle = generated_bundle()
    altered_claim = bundle.claims[0].model_copy(
        update={"candidate_quote": "A fabricated quotation."}
    )
    altered_bundle = bundle.model_copy(
        update={"claims": [altered_claim, *bundle.claims[1:]]}
    )
    records, _ = AuditService().quick_check(
        altered_bundle,
        source_blocks_fixture(),
    )
    pairs = AuditService().semantic_pairs(altered_bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=altered_bundle.document,
        claim_evidence_pairs=pairs,
    )

    report = AuditService().score(
        altered_bundle,
        records,
        result,
        compliance_context(),
    )

    assert "FORGED_CITATION:c-001:CANDIDATE_QUOTE_NOT_FOUND:p01-b001" in (
        report.hard_failures
    )
    assert report.overall_score is not None
    assert report.overall_score >= 75
    assert report.core_gate_passed is False
    assert report.decision == Decision.UNQUALIFIED


def test_critical_number_error_is_a_hard_failure() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    altered_records = [
        (
            record.model_copy(
                update={
                    "rule_flags": [*record.rule_flags, "NUMBER_MISMATCH:999"]
                }
            )
            if record.claim_id == "c-001"
            else record
        )
        for record in records
    ]
    pairs = AuditService().semantic_pairs(bundle, altered_records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )

    report = AuditService().score(
        bundle,
        altered_records,
        result,
        compliance_context(),
    )

    assert "CRITICAL_NUMBER_ERROR:c-001" in report.hard_failures
    assert report.decision == Decision.UNQUALIFIED


def test_core_dimension_gate_can_require_revision_despite_high_total() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    result.semantic_judgments[0] = result.semantic_judgments[0].model_copy(
        update={
            "terminology_status": TerminologyStatus.MISUSED,
            "severity": Severity.MAJOR,
        }
    )

    report = AuditService().score(
        bundle,
        records,
        result,
        compliance_context(),
    )

    terminology = next(
        item
        for item in report.dimensions
        if item.dimension_id == DimensionId.TERMINOLOGY
    )
    assert terminology.raw_metrics["level_points"] == 1
    assert report.overall_score is not None
    assert report.overall_score >= 75
    assert report.hard_failures == []
    assert report.core_gate_passed is False
    assert report.decision == Decision.NEEDS_REVISION
    risk = next(
        item
        for item in report.dimensions
        if item.dimension_id == DimensionId.RISK_COMPLIANCE
    )
    assert risk.raw_metrics["level_points"] == 4


def test_public_mock_flow_uses_real_hy3_service_and_fixed_fixture() -> None:
    service = AuditService()
    assert service.hy3_service.settings.paperlens_model_mode == "mock"
    bundle = generated_bundle()
    records, quick_report = service.quick_check(
        bundle,
        source_blocks_fixture(),
    )

    result, report = service.run_deep_audit(
        bundle,
        records,
        compliance_context(),
    )

    assert quick_report.audit_status == AuditStatus.QUICK_COMPLETE
    assert quick_report.dimensions == []
    assert quick_report.overall_score is None
    assert quick_report.core_gate_passed is None
    assert quick_report.decision == Decision.PENDING_DEEP_AUDIT
    assert [(item.claim_id, item.block_id) for item in result.semantic_judgments] == [
        ("c-001", "p01-b001"),
        ("c-002", "p01-b002"),
        ("c-003", "p02-b001"),
        ("c-004", "p02-b002"),
    ]
    assert report.audit_status == AuditStatus.DEEP_COMPLETE
    assert len(report.dimensions) == 8
    assert report.overall_score is not None
    assert report.decision != Decision.PENDING_DEEP_AUDIT
    assert report.risk_assessment is not None
    assert len(report.risk_assessment.risk_findings) == 3


def test_bound_quote_controls_number_rules_inside_multi_sentence_block() -> None:
    block = source_block(
        "p01-b001",
        "The study enrolled 69 students. "
        "A separate sensitivity analysis considered 70 records.",
    )
    claim = atomic_claim(
        "The study enrolled 70 students.",
        candidate_block_ids=["p01-b001"],
        candidate_quote="The study enrolled 69 students.",
        numeric_entities=["70"],
    )
    bundle = generated_bundle().model_copy(update={"claims": [claim]})

    records, quick_report = AuditService().quick_check(bundle, [block])

    record = only_record(records)
    assert record.quote == "The study enrolled 69 students."
    assert "NUMBER_MISMATCH:70" in record.rule_flags
    assert quick_report.hard_failures == []

    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(bundle, records),
    )
    deep_report = AuditService().score(
        bundle,
        records,
        result,
        compliance_context(),
    )

    assert "CRITICAL_NUMBER_ERROR:c-001" in deep_report.hard_failures
    assert deep_report.decision == Decision.UNQUALIFIED


def test_bm25_uses_matching_sentence_when_block_has_unrelated_negation() -> None:
    matching_sentence = "The treatment did not reduce pain after the 5 mg dose."
    block = source_block(
        "p01-b001",
        matching_sentence + " An unrelated survey reported no missing forms.",
    )
    claim = atomic_claim(
        matching_sentence,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["5"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == matching_sentence
    assert record.quote_verified is True
    assert "NEGATION_MISMATCH" not in record.rule_flags


def test_bm25_preserves_decimal_inside_evidence_fragment() -> None:
    source_text = "The dose was 3.14 mg and reduced pain."
    block = source_block("p01-b001", source_text)
    claim = atomic_claim(
        source_text,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["3.14"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == source_text
    assert "3.14" in record.quote


def test_bm25_normalizes_hyphenated_line_break_before_fragment_split() -> None:
    block = source_block(
        "p01-b001",
        "The inter-\nvention enrolled 69 students.",
    )
    claim = atomic_claim(
        "The intervention enrolled 69 students.",
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["69"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))
    normalized_quote = normalize_evidence_text(record.quote)

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert "intervention enrolled 69 students" in normalized_quote
    assert not normalized_quote.startswith("vention")


def test_bm25_preserves_scientific_abbreviation_and_subject_context() -> None:
    source_text = "The intervention used, e.g. 5 mg, and reduced pain."
    block = source_block("p01-b001", source_text)
    claim = atomic_claim(
        source_text,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["5"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == source_text
    assert "e.g." in record.quote
    assert record.quote.startswith("The intervention")


def test_bm25_preserves_abbreviation_before_number_and_prior_context() -> None:
    source_text = "The result is shown in Fig. 2 and increased by 5%."
    block = source_block("p01-b001", source_text)
    claim = atomic_claim(
        source_text,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["2", "5%"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == source_text
    assert not record.quote.startswith("2 and increased")


def test_bm25_splits_short_common_word_before_unrelated_negation() -> None:
    expected_quote = "The measured risk was low."
    block = source_block(
        "p01-b001",
        expected_quote + " No adverse events occurred.",
    )
    claim = atomic_claim(
        expected_quote,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == expected_quote
    assert "NEGATION_MISMATCH" not in record.rule_flags


def test_bm25_splits_unit_sentence_before_unrelated_negation() -> None:
    expected_quote = "The administered dose was 5 mg."
    block = source_block(
        "p01-b001",
        expected_quote + " No adverse events occurred.",
    )
    claim = atomic_claim(
        expected_quote,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["5"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == expected_quote
    assert "5 mg" in record.quote
    assert "NEGATION_MISMATCH" not in record.rule_flags


def test_bm25_preserves_short_title_abbreviation_context() -> None:
    source_text = "Dr. Smith administered 5 mg."
    block = source_block("p01-b001", source_text)
    claim = atomic_claim(
        source_text,
        candidate_block_ids=["p99-b999"],
        candidate_quote="fabricated candidate",
        numeric_entities=["5"],
    )

    record = only_record(AuditService().verify_claim_evidence(claim, [block]))

    assert record.block_id == "p01-b001"
    assert record.match_method == "bm25_fallback"
    assert record.quote == source_text
    assert not record.quote.startswith("Smith")


def test_citation_accuracy_counts_each_verified_reference_pair() -> None:
    bundle = generated_bundle()
    claim = bundle.claims[0].model_copy(
        update={"candidate_block_ids": ["p01-b001", "p01-b002"]}
    )
    single_claim_bundle = bundle.model_copy(update={"claims": [claim]})
    records = [
        EvidenceRecord(
            claim_id="c-001",
            block_id="p01-b001",
            page_index=0,
            quote="First verified quotation.",
            bbox=None,
            match_method="model_candidate",
            quote_verified=True,
            rule_flags=[],
        ),
        EvidenceRecord(
            claim_id="c-001",
            block_id="p01-b002",
            page_index=0,
            quote="Second verified quotation.",
            bbox=None,
            match_method="model_candidate",
            quote_verified=True,
            rule_flags=[],
        ),
    ]
    judgments = [
        SemanticJudgment(
            claim_id="c-001",
            block_id="p01-b001",
            relation="supports",
            scope_status="preserved",
            terminology_status="correct",
            severity="none",
            reason="The first quotation supports the claim.",
        ),
        SemanticJudgment(
            claim_id="c-001",
            block_id="p01-b002",
            relation="insufficient",
            scope_status="unclear",
            terminology_status="correct",
            severity="none",
            reason="The second quotation is real but does not support the claim.",
        ),
    ]

    report = AuditService().score(
        single_claim_bundle,
        records,
        deep_result(judgments),
        compliance_context(),
    )

    citation = next(
        item
        for item in report.dimensions
        if item.dimension_id == DimensionId.CITATION_CORRECTNESS
    )
    assert citation.raw_metrics["accurate_citations"] == 1
    assert citation.raw_metrics["citations"] == 2
    assert citation.raw_metrics["accuracy_rate"] == 50.0
    assert citation.raw_metrics["level_points"] == 1


@pytest.mark.parametrize("mode", ["duplicate", "extra"])
def test_score_rejects_duplicate_or_extra_semantic_pairs(mode: str) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    if mode == "duplicate":
        invalid_judgments = [
            *result.semantic_judgments,
            result.semantic_judgments[0],
        ]
    else:
        invalid_judgments = [
            *result.semantic_judgments,
            SemanticJudgment(
                claim_id="c-extra",
                block_id="p99-b999",
                relation="supports",
                scope_status="preserved",
                terminology_status="correct",
                severity="none",
                reason="Unexpected semantic pair.",
            ),
        ]

    with pytest.raises(AuditServiceError) as exc_info:
        AuditService().score(
            bundle,
            records,
            deep_result(invalid_judgments),
            compliance_context(),
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True


@pytest.mark.parametrize("mode", ["missing", "duplicate", "extra"])
def test_risk_categories_must_be_complete_exactly_once(mode: str) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    if mode == "missing":
        findings = result.risk_findings[:-1]
    elif mode == "duplicate":
        findings = [*result.risk_findings, result.risk_findings[0]]
    else:
        findings = [
            *result.risk_findings,
            result.risk_findings[0],
            result.risk_findings[1],
        ]

    with pytest.raises(AuditServiceError) as exc_info:
        AuditService().score(
            bundle,
            records,
            result.model_copy(update={"risk_findings": findings}),
            compliance_context(),
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True


def test_sentence_title_document_and_multiple_risk_locations_are_preserved() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    findings = risk_findings_with(
        "author_impersonation",
        "detected",
        locations=[
            {
                "location_type": "sentence",
                "sentence_id": "s-001",
                "evidence_excerpt": "identifies the first page",
            },
            {
                "location_type": "title",
                "sentence_id": None,
                "evidence_excerpt": "PaperLens synthetic fixture",
            },
            {
                "location_type": "document",
                "sentence_id": None,
                "evidence_excerpt": None,
            },
        ],
    )
    result = RecordingDeepAudit(risk_findings=findings).deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )

    report = AuditService().score(
        bundle,
        records,
        result,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "author_impersonation"
    )
    assert [item.location_type.value for item in saved.locations] == [
        "sentence",
        "title",
        "document",
    ]
    assert "AUTHOR_IMPERSONATION" in report.hard_failures


@pytest.mark.parametrize(
    "location",
    [
        {
            "location_type": "sentence",
            "sentence_id": "s-missing",
            "evidence_excerpt": None,
        },
        {
            "location_type": "sentence",
            "sentence_id": "s-001",
            "evidence_excerpt": "excerpt not in the generated sentence",
        },
        {
            "location_type": "title",
            "sentence_id": None,
            "evidence_excerpt": "excerpt not in the generated title",
        },
    ],
)
def test_invalid_risk_location_or_excerpt_is_audit_incomplete(
    location: dict[str, object],
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    result = RecordingDeepAudit(
        risk_findings=risk_findings_with(
            "academic_integrity",
            "detected",
            locations=[location],
        )
    ).deep_audit(
        document=bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(bundle, records),
    )

    with pytest.raises(AuditServiceError) as exc_info:
        AuditService().score(
            bundle,
            records,
            result,
            compliance_context(),
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True


@pytest.mark.parametrize(
    ("category", "status", "locations"),
    [
        ("author_impersonation", "detected", []),
        (
            "author_impersonation",
            "not_detected",
            [
                {
                    "location_type": "document",
                    "sentence_id": None,
                    "evidence_excerpt": None,
                }
            ],
        ),
        (
            "author_impersonation",
            "detected",
            [
                {
                    "location_type": "sentence",
                    "sentence_id": None,
                    "evidence_excerpt": None,
                }
            ],
        ),
        (
            "author_impersonation",
            "detected",
            [
                {
                    "location_type": "title",
                    "sentence_id": "s-001",
                    "evidence_excerpt": None,
                }
            ],
        ),
        (
            "author_impersonation",
            "detected",
            [
                {
                    "location_type": "document",
                    "sentence_id": None,
                    "evidence_excerpt": "not allowed",
                }
            ],
        ),
        (
            "sensitive_information",
            "detected",
            [
                {
                    "location_type": "sentence",
                    "sentence_id": "s-001",
                    "evidence_excerpt": "identifies the first page",
                }
            ],
        ),
    ],
    ids=[
        "detected-without-location",
        "not-detected-with-location",
        "sentence-without-id",
        "title-with-id",
        "document-with-excerpt",
        "sensitive-with-excerpt",
    ],
)
def test_logically_incomplete_risk_finding_is_audit_incomplete(
    category: str,
    status: str,
    locations: list[dict[str, object]],
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    findings = risk_findings_with(
        category,
        status,
        locations=locations,
    )
    with pytest.raises(AuditServiceError) as exc_info:
        AuditService(
            hy3_service=RecordingDeepAudit(risk_findings=findings)
        ).run_deep_audit(
            bundle,
            records,
            compliance_context(),
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True


def test_risk_excerpt_uses_unicode_whitespace_and_hyphenation_normalization() -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    first_section = sections[0]
    normalized_sentence = first_section.sentences[0].model_copy(
        update={"text": "The inter-\nvention used A\u030Angstro\u0308m units."}
    )
    sections[0] = first_section.model_copy(update={"sentences": [normalized_sentence]})
    normalized_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        normalized_bundle,
        source_blocks_fixture(),
    )
    result = RecordingDeepAudit(
        risk_findings=risk_findings_with(
            "academic_integrity",
            "unclear",
            locations=[
                {
                    "location_type": "sentence",
                    "sentence_id": "s-001",
                    "evidence_excerpt": "intervention used Ångström units",
                }
            ],
        )
    ).deep_audit(
        document=normalized_bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(
            normalized_bundle,
            records,
        ),
    )

    report = AuditService().score(
        normalized_bundle,
        records,
        result,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert report.risk_assessment.level_points == 2


@pytest.mark.parametrize(
    ("context_updates", "risk_status", "expected_points"),
    [
        ({}, None, 4),
        ({"source_disclosure_status": "missing"}, None, 3),
        (
            {
                "source_disclosure_status": "missing",
                "ai_assistance_disclosure_status": "missing",
            },
            None,
            2,
        ),
        ({}, "unclear", 2),
        ({"rights_or_license_confirmed": False}, None, 1),
    ],
)
def test_risk_compliance_mapping_four_through_one(
    context_updates: dict[str, object],
    risk_status: str | None,
    expected_points: int,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    findings = (
        risk_findings_with("academic_integrity", risk_status)
        if risk_status is not None
        else clear_risk_findings()
    )
    result = RecordingDeepAudit(risk_findings=findings).deep_audit(
        document=bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(bundle, records),
    )

    report = AuditService().score(
        bundle,
        records,
        result,
        compliance_context(**context_updates),
    )

    assert report.risk_assessment is not None
    assert report.risk_assessment.level_points == expected_points
    risk_dimension = next(
        item
        for item in report.dimensions
        if item.dimension_id == DimensionId.RISK_COMPLIANCE
    )
    assert risk_dimension.raw_metrics["level_points"] == expected_points


def test_missing_required_generated_label_is_zero_without_hard_failure() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(bundle, records),
    )

    report = AuditService().score(
        bundle,
        records,
        result,
        compliance_context(
            generated_content_label_applicability="applicable",
            generated_content_label_status="missing",
        ),
    )

    assert report.risk_assessment is not None
    assert report.risk_assessment.level_points == 0
    assert report.hard_failures == []
    assert report.core_gate_passed is False
    assert report.decision == Decision.NEEDS_REVISION


@pytest.mark.parametrize(
    ("category", "hard_failure"),
    [
        ("sensitive_information", "SENSITIVE_INFORMATION"),
        ("author_impersonation", "AUTHOR_IMPERSONATION"),
        ("academic_integrity", "ACADEMIC_INTEGRITY"),
    ],
)
def test_detected_risk_categories_generate_fixed_hard_failures(
    category: str,
    hard_failure: str,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    result = RecordingDeepAudit(
        risk_findings=risk_findings_with(
            category,
            "detected",
            locations=[
                {
                    "location_type": "document",
                    "sentence_id": None,
                    "evidence_excerpt": None,
                }
            ],
        )
    ).deep_audit(
        document=bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(bundle, records),
    )

    report = AuditService().score(
        bundle,
        records,
        result,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert report.risk_assessment.level_points == 0
    assert hard_failure in report.hard_failures
    assert report.decision == Decision.UNQUALIFIED


def test_non_auditable_content_still_enters_document_risk_check() -> None:
    bundle = generated_bundle()
    non_auditable_claims = [
        claim.model_copy(
            update={
                "auditability": "non_auditable",
                "candidate_block_ids": [],
                "candidate_quote": None,
            }
        )
        for claim in bundle.claims
    ]
    non_auditable_bundle = bundle.model_copy(
        update={"claims": non_auditable_claims}
    )
    records, _ = AuditService().quick_check(
        non_auditable_bundle,
        source_blocks_fixture(),
    )
    recording = RecordingDeepAudit()

    result, report = AuditService(hy3_service=recording).run_deep_audit(
        non_auditable_bundle,
        records,
        compliance_context(),
    )

    assert recording.calls == [[]]
    assert recording.documents == [non_auditable_bundle.document]
    assert any(
        sentence.sentence_id == "s-005"
        for section in recording.documents[0].sections
        for sentence in section.sentences
    )
    assert result.semantic_judgments == []
    assert report.risk_assessment is not None


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "private.person@example.test",
        "+1 (555) 010-2468",
        "978-3-16-148410-0",
        "10.1234.56789",
        "1234-56789",
    ],
    ids=["email", "phone", "isbn", "doi", "trial-number"],
)
def test_sensitive_free_text_is_redacted_by_public_entry(
    sensitive_value: str,
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Contact {sensitive_value} for the private record."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    findings = risk_findings_with(
        "sensitive_information",
        "detected",
        locations=[
            {
                "location_type": "sentence",
                "sentence_id": "s-005",
                "evidence_excerpt": None,
            }
        ],
        reason=f"The document exposes {sensitive_value}.",
        remediation=f"Remove or generalize {sensitive_value}.",
    )
    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        sensitive_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "detected"
    assert len(returned.locations) == 1
    assert returned.locations[0].location_type.value == "sentence"
    assert returned.locations[0].sentence_id == "s-005"
    assert returned.locations[0].evidence_excerpt is None
    assert returned.reason == "A sensitive-content risk was detected."
    assert returned.remediation == (
        "Remove or generalize the flagged content before release."
    )
    assert saved == returned


@pytest.mark.parametrize(
    ("status", "locations"),
    [
        (
            "detected",
            [
                {
                    "location_type": "document",
                    "sentence_id": None,
                    "evidence_excerpt": None,
                }
            ],
        ),
        ("unclear", []),
    ],
    ids=["document-location", "unclear-without-location"],
)
def test_document_level_sensitive_reason_is_redacted_by_public_entry(
    status: str,
    locations: list[dict[str, object]],
) -> None:
    bundle = generated_bundle()
    sensitive_value = "whole.document@example.test"
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Contact {sensitive_value} for the private record."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    findings = risk_findings_with(
        "sensitive_information",
        status,
        locations=locations,
        reason=f"The full document exposes {sensitive_value}.",
    )
    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        sensitive_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == status
    assert len(returned.locations) == len(locations)
    if locations:
        assert returned.locations[0].location_type.value == "document"
        assert returned.locations[0].sentence_id is None
        assert returned.locations[0].evidence_excerpt is None
    expected_text = {
        "detected": (
            "A sensitive-content risk was detected.",
            "Remove or generalize the flagged content before release.",
        ),
        "unclear": (
            "The sensitive-content risk remains unclear.",
            "Review the generated content before release.",
        ),
    }[status]
    assert (returned.reason, returned.remediation) == expected_text
    assert saved == returned


@pytest.mark.parametrize(
    ("sensitive_value", "field_name"),
    [
        ("REC-Q7M4-91ZX", "reason"),
        ("PX7M42FAKE", "remediation"),
        ("COVID19", "reason"),
        ("SARS-CoV-2", "remediation"),
        ("GPT4Model", "reason"),
        ("BRCA1Variant", "remediation"),
    ],
    ids=[
        "mixed-record-number",
        "passport-style-identifier",
        "covid19",
        "sars-cov-2",
        "gpt4-model",
        "brca1-variant",
    ],
)
def test_alphanumeric_text_is_redacted_and_accepted(
    sensitive_value: str,
    field_name: str,
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Synthetic document term: {sensitive_value}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    explanation = {
        "reason": "A private identifier is present.",
        "remediation": "Remove the private identifier.",
    }
    explanation[field_name] = f"The exposed value is {sensitive_value}."
    findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=explanation["reason"],
        remediation=explanation["remediation"],
    )
    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        sensitive_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


def test_sensitive_phone_reason_is_redacted_by_public_entry() -> None:
    bundle = generated_bundle()
    sensitive_value = "+1 (555) 010-2468"
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Synthetic private telephone: {sensitive_value}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=f"The exposed telephone is {sensitive_value}.",
        remediation="Remove the private telephone.",
    )
    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        sensitive_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


def test_year_range_is_redacted_and_accepted() -> None:
    bundle = generated_bundle()
    sensitive_value = "2020-2024"
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    range_sentence = last_section.sentences[0].model_copy(
        update={"text": f"The study period was {sensitive_value}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [range_sentence]})
    range_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        range_bundle,
        source_blocks_fixture(),
    )
    findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=f"The supplier repeated the study period {sensitive_value}.",
        remediation=f"The supplier recommended reviewing {sensitive_value}.",
    )

    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        range_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


@pytest.mark.parametrize(
    ("sensitive_value", "field_name"),
    [
        ("Avery Quill", "reason"),
        ("42 Fictional Avenue", "remediation"),
    ],
    ids=["synthetic-name", "synthetic-address"],
)
def test_sensitive_name_or_address_is_redacted_and_accepted(
    sensitive_value: str,
    field_name: str,
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Synthetic private subject: {sensitive_value}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    explanation = {
        "reason": "Private personal content is present.",
        "remediation": "Generalize the private personal content.",
    }
    explanation[field_name] = f"The document repeats {sensitive_value}."
    findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=explanation["reason"],
        remediation=explanation["remediation"],
    )
    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        sensitive_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


@pytest.mark.parametrize(
    "risk_phrase",
    [
        "Personal Information",
        "Confidential Information",
        "Sensitive Data",
        "Privacy Risk",
    ],
    ids=[
        "personal-information",
        "confidential-information",
        "sensitive-data",
        "privacy-risk",
    ],
)
def test_generic_sensitive_risk_phrase_is_redacted_and_accepted(
    risk_phrase: str,
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    generic_sentence = last_section.sentences[0].model_copy(
        update={"text": f"The document heading is {risk_phrase}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [generic_sentence]})
    generic_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        generic_bundle,
        source_blocks_fixture(),
    )
    raw_reason = f"The supplier categorized this as {risk_phrase}."
    raw_remediation = f"The supplier recommends reviewing {risk_phrase}."
    findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=raw_reason,
        remediation=raw_remediation,
    )

    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=findings)
    ).run_deep_audit(
        generic_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    result_json = result.model_dump_json()
    report_json = report.model_dump_json()
    assert raw_reason not in result_json
    assert raw_remediation not in result_json
    assert raw_reason not in report_json
    assert raw_remediation not in report_json
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


def test_sensitive_reason_allows_generic_redacted_explanation() -> None:
    bundle = generated_bundle()
    synthetic_values = (
        "private.person@example.test",
        "+1 (555) 010-2468",
        "REC-Q7M4-91ZX",
        "PX7M42FAKE",
        "Avery Quill",
        "42 Fictional Avenue",
    )
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={
            "text": "Synthetic private data: " + "; ".join(synthetic_values) + "."
        }
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    findings = risk_findings_with(
        "sensitive_information",
        "detected",
        locations=[
            {
                "location_type": "document",
                "sentence_id": None,
                "evidence_excerpt": None,
            }
        ],
        reason="Sensitive personal content was detected.",
        remediation="Remove or generalize the identified content before release.",
    )
    result = RecordingDeepAudit(risk_findings=findings).deep_audit(
        document=sensitive_bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(
            sensitive_bundle,
            records,
        ),
    )

    report = AuditService().score(
        sensitive_bundle,
        records,
        result,
        compliance_context(),
    )

    serialized = report.model_dump_json()
    assert all(value not in serialized for value in synthetic_values)
    assert "SENSITIVE_INFORMATION" in report.hard_failures


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "Zorblen",
        "测试甲",
        "73194628",
        "QWERTYZX",
        "虚构市测试区样例路",
    ],
    ids=[
        "single-word-name",
        "chinese-name",
        "eight-digit-record",
        "letters-only-identifier",
        "chinese-address-without-number",
    ],
)
def test_score_redacts_unrecognized_sensitive_text_from_report(
    sensitive_value: str,
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Synthetic confidential value: {sensitive_value}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=f"Supplier reason includes {sensitive_value}.",
        remediation=f"Supplier remediation includes {sensitive_value}.",
    )
    result = RecordingDeepAudit(risk_findings=findings).deep_audit(
        document=sensitive_bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(
            sensitive_bundle,
            records,
        ),
    )

    report = AuditService().score(
        sensitive_bundle,
        records,
        result,
        compliance_context(),
    )

    assert sensitive_value not in report.model_dump_json()
    assert report.risk_assessment is not None
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert saved.status.value == "unclear"
    assert saved.locations == []
    assert saved.reason == "The sensitive-content risk remains unclear."
    assert saved.remediation == "Review the generated content before release."


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "Zorblen",
        "测试甲",
        "73194628",
        "QWERTYZX",
        "虚构市测试区样例路",
    ],
)
def test_run_deep_audit_redacts_sensitive_text_from_result_and_report(
    sensitive_value: str,
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    sensitive_sentence = last_section.sentences[0].model_copy(
        update={"text": f"Synthetic confidential value: {sensitive_value}."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [sensitive_sentence]})
    sensitive_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        sensitive_bundle,
        source_blocks_fixture(),
    )
    raw_findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason=f"Supplier reason includes {sensitive_value}.",
        remediation=f"Supplier remediation includes {sensitive_value}.",
    )

    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=raw_findings)
    ).run_deep_audit(
        sensitive_bundle,
        records,
        compliance_context(),
    )

    assert sensitive_value not in result.model_dump_json()
    assert sensitive_value not in report.model_dump_json()
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."


def test_code_owned_sensitive_explanation_survives_generic_document_phrase() -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    last_section = sections[-1]
    generic_sentence = last_section.sentences[0].model_copy(
        update={"text": "The heading is Sensitive Information."}
    )
    sections[-1] = last_section.model_copy(update={"sentences": [generic_sentence]})
    generic_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        generic_bundle,
        source_blocks_fixture(),
    )
    raw_findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason="A protected-content review is required.",
        remediation="Review the generated content before release.",
    )

    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=raw_findings)
    ).run_deep_audit(
        generic_bundle,
        records,
        compliance_context(),
    )
    rescored = AuditService().score(
        generic_bundle,
        records,
        result,
        compliance_context(),
    )

    assert report == rescored
    assert report.risk_assessment is not None
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert saved.reason == "The sensitive-content risk remains unclear."


def test_safe_sensitive_phrase_matching_document_title_is_redacted_and_accepted(
) -> None:
    bundle = generated_bundle()
    generic_bundle = bundle.model_copy(
        update={
            "document": bundle.document.model_copy(
                update={"title": "Sensitive Information: Sensitive-Content Risk"}
            )
        }
    )
    records, _ = AuditService().quick_check(
        generic_bundle,
        source_blocks_fixture(),
    )
    raw_findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason="Sensitive information and sensitive-content risk require review.",
        remediation="Review generated content before release.",
    )

    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=raw_findings)
    ).run_deep_audit(
        generic_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


def test_safe_sensitive_phrase_matching_section_heading_is_redacted_and_accepted(
) -> None:
    bundle = generated_bundle()
    sections = list(bundle.document.sections)
    sections[0] = sections[0].model_copy(
        update={"heading": "Review Generated Content"}
    )
    generic_bundle = bundle.model_copy(
        update={"document": bundle.document.model_copy(update={"sections": sections})}
    )
    records, _ = AuditService().quick_check(
        generic_bundle,
        source_blocks_fixture(),
    )
    raw_findings = risk_findings_with(
        "sensitive_information",
        "unclear",
        reason="The sensitive-content risk remains unclear.",
        remediation="Review generated content before release.",
    )

    result, report = AuditService(
        hy3_service=RecordingDeepAudit(risk_findings=raw_findings)
    ).run_deep_audit(
        generic_bundle,
        records,
        compliance_context(),
    )

    assert report.risk_assessment is not None
    returned = next(
        finding
        for finding in result.risk_findings
        if finding.category.value == "sensitive_information"
    )
    saved = next(
        finding
        for finding in report.risk_assessment.risk_findings
        if finding.category.value == "sensitive_information"
    )
    assert returned.status.value == "unclear"
    assert returned.locations == []
    assert returned.reason == "The sensitive-content risk remains unclear."
    assert returned.remediation == "Review the generated content before release."
    assert saved == returned


def test_invalid_compliance_context_is_audit_incomplete() -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=AuditService().semantic_pairs(bundle, records),
    )
    invalid_context = ComplianceContext.model_construct(
        rights_or_license_confirmed=True,
        source_disclosure_status=DisclosureStatus.PRESENT,
        ai_assistance_disclosure_status=DisclosureStatus.PRESENT,
        generated_content_label_applicability=(
            GeneratedContentLabelApplicability.APPLICABLE
        ),
        generated_content_label_status=(
            GeneratedContentLabelStatus.NOT_APPLICABLE
        ),
    )

    with pytest.raises(AuditServiceError) as exc_info:
        AuditService().score(
            bundle,
            records,
            result,
            invalid_context,
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"


def test_audit_diagnostic_classifies_compliance_context_validation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    invalid_context = InvalidModelPayload(
        {
            "rights_or_license_confirmed": True,
            "source_disclosure_status": "PYDANTIC_INPUT_SENTINEL",
            "ai_assistance_disclosure_status": "present",
            "generated_content_label_applicability": "not_applicable",
            "generated_content_label_status": "not_applicable",
        }
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                invalid_context,
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    assert_safe_audit_failure_log(
        caplog,
        boundary="compliance_context_validation",
        validation_error_count="1",
        validation_error_type="enum",
        validation_location="source_disclosure_status",
    )


def test_audit_diagnostic_classifies_deep_audit_result_validation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    invalid_result = InvalidModelPayload(
        {
            "semantic_judgments": [],
            "risk_findings": "PYDANTIC_INPUT_SENTINEL",
            "expression_findings": [],
        }
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                invalid_result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    assert_safe_audit_failure_log(
        caplog,
        boundary="deep_audit_result_validation",
        validation_error_count="1",
        validation_error_type="other_validation_error",
        validation_location="risk_findings",
    )


def test_audit_diagnostic_classifies_risk_category_validation_without_mutation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    invalid_result = result.model_copy(
        update={"risk_findings": result.risk_findings[:2]}
    )
    original_findings = invalid_result.model_dump(mode="json")["risk_findings"]

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                invalid_result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert invalid_result.model_dump(mode="json")["risk_findings"] == (
        original_findings
    )
    assert len(invalid_result.risk_findings) == 2
    assert_safe_audit_failure_log(
        caplog,
        boundary="risk_category_validation",
    )


def test_audit_postprocessing_diagnostic_bounds_final_long_location(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    long_location = "LONG_AUDIT_LOCATION_SENTINEL_" * 35
    assert len(long_location) >= 900
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    invalid_context = InvalidModelPayload(
        {
            "rights_or_license_confirmed": True,
            "source_disclosure_status": "PYDANTIC_INPUT_SENTINEL",
            "ai_assistance_disclosure_status": "present",
            "generated_content_label_applicability": "not_applicable",
            "generated_content_label_status": "not_applicable",
        }
    )
    monkeypatch.setattr(
        Hy3Service,
        "_validation_error_diagnostic",
        staticmethod(lambda _error: (1, "value_error", long_location)),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                invalid_context,
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    logs = audit_postprocessing_logs(caplog)
    assert len(logs) == 1
    fields = audit_log_fields(logs[0])
    assert set(fields) == SAFE_DIAGNOSTIC_FIELD_NAMES
    assert fields["operation"] == "audit_postprocessing"
    assert fields["attempt"] == "0"
    assert fields["retry_count"] == "0"
    assert fields["validation_boundary"] == "compliance_context_validation"
    assert fields["error_code"] == "AUDIT_INCOMPLETE"
    assert fields["validation_location"].endswith("<truncated>")
    assert long_location not in logs[0]
    assert len(logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH


def test_audit_diagnostic_classifies_evidence_pair_validation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    invalid_record = records[0].model_copy(
        update={
            "claim_id": "CLAIM_TEXT_SENTINEL",
            "quote": "EVIDENCE_TEXT_SENTINEL",
        }
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().semantic_pairs(bundle, [invalid_record])

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    assert_safe_audit_failure_log(
        caplog,
        boundary="evidence_pair_validation",
    )


def test_audit_diagnostic_classifies_semantic_pair_validation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    invalid = result.model_copy(
        update={"semantic_judgments": result.semantic_judgments[:-1]}
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                invalid,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert_safe_audit_failure_log(
        caplog,
        boundary="semantic_pair_validation",
    )


def test_audit_diagnostic_classifies_risk_location_validation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit(
        risk_findings=risk_findings_with(
            "author_impersonation",
            "detected",
            locations=[],
            reason="RAW_RESPONSE_SENTINEL",
        )
    ).deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert_safe_audit_failure_log(
        caplog,
        boundary="risk_location_validation",
    )


def test_audit_diagnostic_classifies_risk_excerpt_validation(
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit(
        risk_findings=risk_findings_with(
            "academic_integrity",
            "detected",
            locations=[
                {
                    "location_type": "sentence",
                    "sentence_id": "s-001",
                    "evidence_excerpt": "EVIDENCE_TEXT_SENTINEL",
                }
            ],
        )
    ).deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert_safe_audit_failure_log(
        caplog,
        boundary="risk_excerpt_validation",
    )


def test_audit_diagnostic_classifies_risk_assessment_construction(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    risk_assessment_model = audit_module.RiskAssessment

    def invalid_risk_assessment(**_kwargs):
        return risk_assessment_model.model_validate({})

    monkeypatch.setattr(
        audit_module,
        "RiskAssessment",
        invalid_risk_assessment,
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    assert_safe_audit_failure_log(
        caplog,
        boundary="risk_assessment_construction",
        validation_error_count="3",
        validation_error_type="missing",
        validation_location="compliance_context",
    )


def test_audit_diagnostic_classifies_dimension_construction(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    monkeypatch.setattr(
        audit_module,
        "_build_dimensions",
        lambda *_args, **_kwargs: ([], {}),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    assert_safe_audit_failure_log(
        caplog,
        boundary="dimension_construction",
    )


def test_audit_diagnostic_classifies_final_report_construction(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    bundle = generated_bundle()
    records, _ = AuditService().quick_check(bundle, source_blocks_fixture())
    pairs = AuditService().semantic_pairs(bundle, records)
    result = RecordingDeepAudit().deep_audit(
        document=bundle.document,
        claim_evidence_pairs=pairs,
    )
    audit_report_model = audit_module.AuditReport

    def invalid_audit_report(**_kwargs):
        return audit_report_model.model_validate({})

    monkeypatch.setattr(
        audit_module,
        "AuditReport",
        invalid_audit_report,
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(AuditServiceError) as exc_info:
            AuditService().score(
                bundle,
                records,
                result,
                compliance_context(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is False
    assert_safe_audit_failure_log(
        caplog,
        boundary="final_report_construction",
        validation_error_count="6",
        validation_error_type="missing",
        validation_location="audit_status",
    )
