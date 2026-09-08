import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app import models
from backend.app.models import (
    AuditReport,
    ClaimsSnapshot,
    ComplianceContext,
    DeepAuditRequest,
    DeepAuditResponse,
    DeepAuditResult,
    DimensionId,
    EditPatch,
    EvidenceRecord,
    EvidenceSnapshot,
    GenerationRequest,
    GenerationResponse,
    GeneratedBundle,
    ParseQualitySnapshot,
    ParseSnapshot,
    ProjectCreateResponse,
    ProjectView,
    RiskAssessment,
    RiskFinding,
    RiskLocation,
    RevisionRequest,
    RunMetadata,
    RunMode,
    RunOperation,
    RunStatus,
    SentenceClaimRegenerationResult,
    SourceBlock,
    UsageSnapshot,
    VersionSummary,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_document_expression_contract_current_schema_requires_findings():
    required = DeepAuditResult.model_json_schema()["required"]
    assert "expression_findings" in required, "EXPRESSION_CHECK_REQUIRED"


def test_document_expression_contract_current_response_cannot_be_legacy():
    payload = {"semantic_judgments": [], "risk_findings": valid_risk_findings_payload()}
    with pytest.raises(ValidationError):
        DeepAuditResult.model_validate(payload)


def test_document_expression_contract_explicit_v2_reader_preserves_absence():
    old = models.DeepAuditResultV2.model_validate(load_json("deep_audit_valid.json"))
    assert "expression_findings" not in old.model_dump()
    current = DeepAuditResult.model_validate(load_json("deep_audit_v3_valid.json"))
    assert len(current.expression_findings) == 2
    payload = current.model_dump(mode="json")
    payload["expression_findings"][0]["reason"] = "FORBIDDEN_EXPRESSION_TEXT"
    with pytest.raises(ValidationError):
        DeepAuditResult.model_validate(payload)


def load_json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def valid_compliance_payload() -> dict[str, object]:
    return {
        "rights_or_license_confirmed": True,
        "source_disclosure_status": "present",
        "ai_assistance_disclosure_status": "present",
        "generated_content_label_applicability": "not_applicable",
        "generated_content_label_status": "not_applicable",
    }


def valid_risk_findings_payload() -> list[dict[str, object]]:
    return [
        {
            "category": category,
            "status": "not_detected",
            "locations": [],
            "reason": (
                "No sensitive-content risk was detected."
                if category == "sensitive_information"
                else "No risk was detected."
            ),
            "remediation": (
                "No sensitive-content remediation is required."
                if category == "sensitive_information"
                else "No change is required."
            ),
        }
        for category in (
            "sensitive_information",
            "author_impersonation",
            "academic_integrity",
        )
    ]


def valid_risk_assessment_payload(*, level_points: int = 4) -> dict[str, object]:
    return {
        "compliance_context": valid_compliance_payload(),
        "risk_findings": valid_risk_findings_payload(),
        "level_points": level_points,
    }


def valid_dimensions_payload(*, risk_level_points: int = 4) -> list[dict[str, object]]:
    dimensions: list[dict[str, object]] = []
    for dimension_id in DimensionId:
        level_points = (
            risk_level_points
            if dimension_id == DimensionId.RISK_COMPLIANCE
            else 4
        )
        dimensions.append({
            "dimension_id": dimension_id.value,
            "raw_metrics": {"level_points": level_points},
            "score": level_points * 25.0,
            "level": (
                "good"
                if level_points >= 4
                else "acceptable"
                if level_points >= 2
                else "poor"
            ),
        })
    return dimensions


def test_source_blocks_fixture_is_valid() -> None:
    blocks = [SourceBlock.model_validate(item) for item in load_json("source_blocks.json")]

    assert [block.page_index for block in blocks] == [0, 0, 1, 1]
    assert [block.reading_order for block in blocks] == [0, 1, 2, 3]
    assert blocks[0].bbox == (0.112, 0.074, 0.415, 0.097)


def test_source_block_rejects_invalid_bbox() -> None:
    payload = load_json("source_blocks.json")[0]
    payload["bbox"] = [0.8, 0.1, 0.2, 0.3]

    with pytest.raises(ValidationError, match="positive width"):
        SourceBlock.model_validate(payload)


def test_models_forbid_extra_fields() -> None:
    payload = load_json("source_blocks.json")[0]
    payload["invented"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceBlock.model_validate(payload)


def test_generated_bundle_fixture_is_valid() -> None:
    bundle = GeneratedBundle.model_validate(load_json("generation_valid.json"))

    assert len(bundle.document.sections) == 5
    assert bundle.claims[0].candidate_block_ids == ["p01-b001"]


def test_generated_bundle_rejects_duplicate_sentence_ids() -> None:
    payload = load_json("generation_valid.json")
    payload["document"]["sections"][1]["sentences"][0]["sentence_id"] = "s-001"

    with pytest.raises(ValidationError, match="sentence_id values must be unique"):
        GeneratedBundle.model_validate(payload)


def _sentence_claim_regeneration_claim(
    *,
    claim_id: str = "c-revised-001",
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "sentence_id": "s-001",
        "text": "The revised sentence states the supported result.",
        "claim_type": "result",
        "importance": "critical",
        "qualifiers": [],
        "numeric_entities": [],
        "auditability": "auditable",
        "candidate_block_ids": ["p01-b001"],
        "candidate_quote": "supported result",
    }


def test_sentence_claim_regeneration_result_accepts_valid_claims() -> None:
    result = SentenceClaimRegenerationResult.model_validate(
        {"claims": [_sentence_claim_regeneration_claim()]}
    )

    assert [claim.claim_id for claim in result.claims] == ["c-revised-001"]
    schema = SentenceClaimRegenerationResult.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["claims"]["minItems"] == 1
    assert schema["$defs"]["AtomicClaim"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"claims": []},
        {"claims": [_sentence_claim_regeneration_claim()], "history": []},
        {
            "claims": [
                {
                    **_sentence_claim_regeneration_claim(),
                    "page_index": 1,
                }
            ]
        },
        {
            "claims": [
                {
                    **_sentence_claim_regeneration_claim(),
                    "candidate_block_ids": [],
                }
            ]
        },
    ],
)
def test_sentence_claim_regeneration_result_rejects_invalid_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SentenceClaimRegenerationResult.model_validate(payload)


def test_sentence_claim_regeneration_result_rejects_duplicate_claim_ids() -> None:
    with pytest.raises(ValidationError, match="claim_id values must be unique"):
        SentenceClaimRegenerationResult.model_validate(
            {
                "claims": [
                    _sentence_claim_regeneration_claim(),
                    _sentence_claim_regeneration_claim(),
                ]
            }
        )


def test_generated_bundle_rejects_unknown_sentence_reference() -> None:
    payload = load_json("generation_valid.json")
    payload["claims"][0]["sentence_id"] = "s-404"

    with pytest.raises(ValidationError, match="unknown sentence ids"):
        GeneratedBundle.model_validate(payload)


def test_invalid_schema_fixture_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedBundle.model_validate(load_json("generation_invalid_schema.json"))


def test_unknown_block_is_structurally_valid_until_evidence_check() -> None:
    bundle = GeneratedBundle.model_validate(load_json("generation_invalid_block.json"))

    assert bundle.claims[0].candidate_block_ids == ["p99-b999"]


def test_quick_audit_cannot_report_final_score() -> None:
    payload = {
        "audit_status": "quick_complete",
        "dimensions": [],
        "risk_assessment": None,
        "hard_failures": [],
        "core_gate_passed": None,
        "overall_score": 85,
        "decision": "pending_deep_audit",
    }

    with pytest.raises(ValidationError, match="quick audit cannot contain"):
        AuditReport.model_validate(payload)


def test_sentence_patch_requires_one_target() -> None:
    payload = load_json("patch_sentence_valid.json")
    payload["target_sentence_ids"] = ["s-001", "s-002"]

    with pytest.raises(ValidationError, match="exactly one"):
        EditPatch.model_validate(payload)


def test_revision_request_accepts_only_scope_specific_target_shape() -> None:
    sentence_request = RevisionRequest.model_validate({
        "base_version_id": "version-001",
        "scope": "sentence",
        "target_sentence_id": "s-001",
        "user_instruction": "保持事实不变并简化表达。",
    })
    document_request = RevisionRequest.model_validate({
        "base_version_id": "version-001",
        "scope": "document",
        "target_sentence_id": None,
        "user_instruction": "统一五区表达。",
    })

    assert sentence_request.target_sentence_id == "s-001"
    assert document_request.target_sentence_id is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "base_version_id": "version-001",
            "scope": "sentence",
            "target_sentence_id": None,
            "user_instruction": "修改目标句。",
        },
        {
            "base_version_id": "version-001",
            "scope": "document",
            "target_sentence_id": "s-001",
            "user_instruction": "修改全文。",
        },
        {
            "base_version_id": "version-001",
            "scope": "sentence",
            "target_sentence_id": "s-001",
            "user_instruction": "   ",
        },
        {
            "base_version_id": "version-001",
            "scope": "sentence",
            "target_sentence_id": "s-001",
            "user_instruction": "修改目标句。",
            "history": ["version-000"],
        },
    ],
)
def test_revision_request_rejects_missing_target_history_and_invalid_scope(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RevisionRequest.model_validate(payload)


def test_deep_audit_and_patch_fixtures_are_valid() -> None:
    result = DeepAuditResult.model_validate(load_json("deep_audit_v3_valid.json"))
    patch = EditPatch.model_validate(load_json("patch_sentence_valid.json"))

    assert [(item.claim_id, item.block_id) for item in result.semantic_judgments] == [
        ("c-001", "p01-b001"),
        ("c-002", "p01-b002"),
        ("c-003", "p02-b001"),
        ("c-004", "p02-b002"),
    ]
    assert [item.category.value for item in result.risk_findings] == [
        "sensitive_information",
        "author_impersonation",
        "academic_integrity",
    ]
    assert patch.target_sentence_ids == ["s-001"]


def test_generated_bundle_json_schema_forbids_extra_object_fields() -> None:
    schema = GeneratedBundle.model_json_schema()

    def assert_closed_objects(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_closed_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_closed_objects(value)

    assert_closed_objects(schema)


def test_evidence_record_rejects_non_normalized_bbox() -> None:
    payload = {
        "claim_id": "c-001",
        "block_id": "p01-b001",
        "page_index": 0,
        "quote": "verified quote",
        "bbox": [10, 20, 30, 40],
        "match_method": "model_candidate",
        "quote_verified": True,
        "rule_flags": [],
    }

    with pytest.raises(ValidationError, match="normalized"):
        EvidenceRecord.model_validate(payload)


def test_deep_audit_v2_schema_closes_every_nested_object() -> None:
    schema = DeepAuditResult.model_json_schema()

    def assert_closed_objects(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_closed_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_closed_objects(value)

    assert schema["type"] == "object"
    assert set(schema["required"]) == {"semantic_judgments", "risk_findings", "expression_findings"}
    assert_closed_objects(schema)


@pytest.mark.parametrize(
    ("applicability", "status", "valid"),
    [
        ("not_applicable", "not_applicable", True),
        ("applicable", "present", True),
        ("applicable", "missing", True),
        ("not_applicable", "present", False),
        ("not_applicable", "missing", False),
        ("applicable", "not_applicable", False),
    ],
)
def test_compliance_context_enforces_label_combination(
    applicability: str,
    status: str,
    valid: bool,
) -> None:
    payload = valid_compliance_payload()
    payload["generated_content_label_applicability"] = applicability
    payload["generated_content_label_status"] = status

    if valid:
        ComplianceContext.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            ComplianceContext.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "location_type": "sentence",
            "sentence_id": "s-001",
            "evidence_excerpt": "risk excerpt",
        },
        {
            "location_type": "title",
            "sentence_id": None,
            "evidence_excerpt": "title excerpt",
        },
        {
            "location_type": "document",
            "sentence_id": None,
            "evidence_excerpt": None,
        },
    ],
)
def test_risk_location_accepts_three_valid_location_types(
    payload: dict[str, object],
) -> None:
    RiskLocation.model_validate(payload)


def test_risk_location_excerpt_field_is_required() -> None:
    payload = {
        "location_type": "document",
        "sentence_id": None,
    }

    with pytest.raises(ValidationError, match="evidence_excerpt"):
        RiskLocation.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "location_type": "paragraph",
            "sentence_id": None,
            "evidence_excerpt": None,
        },
        {
            "location_type": "sentence",
            "sentence_id": ["s-001"],
            "evidence_excerpt": None,
        },
        {
            "location_type": "sentence",
            "sentence_id": "s-001",
            "evidence_excerpt": "x" * 161,
        },
        {
            "location_type": "document",
            "sentence_id": None,
            "evidence_excerpt": None,
            "page_index": 1,
        },
    ],
)
def test_risk_location_rejects_structurally_invalid_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RiskLocation.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "location_type": "sentence",
            "sentence_id": None,
            "evidence_excerpt": None,
        },
        {
            "location_type": "title",
            "sentence_id": "s-001",
            "evidence_excerpt": None,
        },
        {
            "location_type": "document",
            "sentence_id": None,
            "evidence_excerpt": "not allowed",
        },
    ],
)
def test_risk_location_logical_combinations_are_deferred_to_audit_service(
    payload: dict[str, object],
) -> None:
    RiskLocation.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "locations"),
    [
        ("detected", []),
        (
            "not_detected",
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
)
def test_risk_finding_logical_states_are_deferred_to_audit_service(
    status: str,
    locations: list[dict[str, object]],
) -> None:
    RiskFinding.model_validate({
        "category": "author_impersonation",
        "status": status,
        "locations": locations,
        "reason": "Bounded reason.",
        "remediation": "Bounded remediation.",
    })


def test_sensitive_information_excerpt_is_deferred_to_audit_service() -> None:
    payload = {
        "category": "sensitive_information",
        "status": "detected",
        "locations": [
            {
                "location_type": "sentence",
                "sentence_id": "s-001",
                "evidence_excerpt": "private@example.test",
            }
        ],
        "reason": "Sensitive information is present.",
        "remediation": "Remove the sensitive value.",
    }

    RiskFinding.model_validate(payload)


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_risk_assessment_rejects_missing_or_duplicate_categories(mode: str) -> None:
    payload = valid_risk_assessment_payload()
    findings = payload["risk_findings"]
    assert isinstance(findings, list)
    if mode == "missing":
        payload["risk_findings"] = findings[:-1]
    else:
        payload["risk_findings"] = [*findings, findings[0]]

    with pytest.raises(ValidationError, match="each required category exactly once"):
        RiskAssessment.model_validate(payload)


def test_risk_assessment_rejects_inconsistent_code_computed_level() -> None:
    payload = valid_risk_assessment_payload(level_points=4)
    context = payload["compliance_context"]
    assert isinstance(context, dict)
    context["rights_or_license_confirmed"] = False

    with pytest.raises(ValidationError, match="code-computed risk level"):
        RiskAssessment.model_validate(payload)


def test_risk_assessment_rejects_provider_sensitive_free_text() -> None:
    payload = valid_risk_assessment_payload()
    findings = payload["risk_findings"]
    assert isinstance(findings, list)
    sensitive = findings[0]
    assert isinstance(sensitive, dict)
    sensitive["reason"] = "Supplier-provided sensitive explanation."

    with pytest.raises(ValidationError, match="code-owned redacted text"):
        RiskAssessment.model_validate(payload)


def test_deep_audit_v2_nested_models_reject_extra_fields() -> None:
    payload = load_json("deep_audit_v3_valid.json")
    payload["risk_findings"][0]["locations"] = [
        {
            "location_type": "document",
            "sentence_id": None,
            "evidence_excerpt": None,
            "invented": True,
        }
    ]
    payload["risk_findings"][0]["status"] = "detected"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeepAuditResult.model_validate(payload)


def test_quick_and_deep_audit_require_matching_risk_assessment_state() -> None:
    quick_payload = {
        "audit_status": "quick_complete",
        "dimensions": [],
        "risk_assessment": valid_risk_assessment_payload(),
        "hard_failures": [],
        "core_gate_passed": None,
        "overall_score": None,
        "decision": "pending_deep_audit",
    }
    with pytest.raises(ValidationError, match="risk assessment"):
        AuditReport.model_validate(quick_payload)

    quick_payload["risk_assessment"] = None
    quick_payload["hard_failures"] = ["FORGED_CITATION:c-001"]
    with pytest.raises(ValidationError, match="hard failures"):
        AuditReport.model_validate(quick_payload)

    deep_payload = {
        "audit_status": "deep_complete",
        "dimensions": valid_dimensions_payload(),
        "risk_assessment": None,
        "hard_failures": [],
        "core_gate_passed": True,
        "overall_score": 100.0,
        "decision": "qualified",
    }
    with pytest.raises(ValidationError, match="risk assessment"):
        AuditReport.model_validate(deep_payload)

    deep_payload["risk_assessment"] = valid_risk_assessment_payload(level_points=2)
    with pytest.raises(ValidationError, match="level_points"):
        AuditReport.model_validate(deep_payload)


def test_audit_report_rejects_detected_risk_without_fixed_hard_failure() -> None:
    findings = valid_risk_findings_payload()
    findings[1] = {
        "category": "author_impersonation",
        "status": "detected",
        "locations": [
            {
                "location_type": "document",
                "sentence_id": None,
                "evidence_excerpt": None,
            }
        ],
        "reason": "A structured risk was detected.",
        "remediation": "Revise the generated content.",
    }
    payload = {
        "audit_status": "deep_complete",
        "dimensions": valid_dimensions_payload(risk_level_points=0),
        "risk_assessment": {
            "compliance_context": valid_compliance_payload(),
            "risk_findings": findings,
            "level_points": 0,
        },
        "hard_failures": [],
        "core_gate_passed": False,
        "overall_score": 95.0,
        "decision": "needs_revision",
    }

    with pytest.raises(ValidationError, match="fixed risk hard failures"):
        AuditReport.model_validate(payload)


def test_audit_report_rejects_spurious_risk_hard_failure() -> None:
    payload = {
        "audit_status": "deep_complete",
        "dimensions": valid_dimensions_payload(),
        "risk_assessment": valid_risk_assessment_payload(),
        "hard_failures": ["SENSITIVE_INFORMATION"],
        "core_gate_passed": False,
        "overall_score": 95.0,
        "decision": "unqualified",
    }

    with pytest.raises(ValidationError, match="fixed risk hard failures"):
        AuditReport.model_validate(payload)


def test_audit_report_rejects_hard_failure_with_non_unqualified_decision() -> None:
    findings = valid_risk_findings_payload()
    findings[1] = {
        "category": "author_impersonation",
        "status": "detected",
        "locations": [
            {
                "location_type": "document",
                "sentence_id": None,
                "evidence_excerpt": None,
            }
        ],
        "reason": "A structured risk was detected.",
        "remediation": "Revise the generated content.",
    }
    payload = {
        "audit_status": "deep_complete",
        "dimensions": valid_dimensions_payload(risk_level_points=0),
        "risk_assessment": {
            "compliance_context": valid_compliance_payload(),
            "risk_findings": findings,
            "level_points": 0,
        },
        "hard_failures": ["AUTHOR_IMPERSONATION"],
        "core_gate_passed": False,
        "overall_score": 95.0,
        "decision": "needs_revision",
    }

    with pytest.raises(ValidationError, match="code-computed decision"):
        AuditReport.model_validate(payload)


@pytest.mark.parametrize(
    ("core_gate_passed", "decision"),
    [
        (True, "needs_revision"),
        (False, "qualified"),
    ],
)
def test_audit_report_rejects_forged_generated_label_gate_or_decision(
    core_gate_passed: bool,
    decision: str,
) -> None:
    context = valid_compliance_payload()
    context["generated_content_label_applicability"] = "applicable"
    context["generated_content_label_status"] = "missing"
    payload = {
        "audit_status": "deep_complete",
        "dimensions": valid_dimensions_payload(risk_level_points=0),
        "risk_assessment": {
            "compliance_context": context,
            "risk_findings": valid_risk_findings_payload(),
            "level_points": 0,
        },
        "hard_failures": [],
        "core_gate_passed": core_gate_passed,
        "overall_score": 95.0,
        "decision": decision,
    }

    error = "code-computed core gate" if core_gate_passed else "code-computed decision"
    with pytest.raises(ValidationError, match=error):
        AuditReport.model_validate(payload)


@pytest.mark.parametrize("forged_field", ["score", "level"])
def test_audit_report_rejects_forged_risk_dimension_display(
    forged_field: str,
) -> None:
    context = valid_compliance_payload()
    context["generated_content_label_applicability"] = "applicable"
    context["generated_content_label_status"] = "missing"
    dimensions = valid_dimensions_payload(risk_level_points=0)
    risk_dimension = next(
        item
        for item in dimensions
        if item["dimension_id"] == DimensionId.RISK_COMPLIANCE.value
    )
    risk_dimension[forged_field] = 100.0 if forged_field == "score" else "good"
    payload = {
        "audit_status": "deep_complete",
        "dimensions": dimensions,
        "risk_assessment": {
            "compliance_context": context,
            "risk_findings": valid_risk_findings_payload(),
            "level_points": 0,
        },
        "hard_failures": [],
        "core_gate_passed": False,
        "overall_score": 95.0,
        "decision": "needs_revision",
    }

    with pytest.raises(ValidationError, match="code-computed dimension"):
        AuditReport.model_validate(payload)


def test_audit_report_rejects_inconsistent_weighted_overall_score() -> None:
    payload = {
        "audit_status": "deep_complete",
        "dimensions": valid_dimensions_payload(),
        "risk_assessment": valid_risk_assessment_payload(),
        "hard_failures": [],
        "core_gate_passed": True,
        "overall_score": 99.0,
        "decision": "qualified",
    }

    with pytest.raises(ValidationError, match="code-computed overall score"):
        AuditReport.model_validate(payload)


def test_audit_report_json_round_trip_preserves_complete_risk_details() -> None:
    findings = valid_risk_findings_payload()
    findings[1] = {
        "category": "author_impersonation",
        "status": "detected",
        "locations": [
            {
                "location_type": "sentence",
                "sentence_id": "s-001",
                "evidence_excerpt": "author claim",
            },
            {
                "location_type": "title",
                "sentence_id": None,
                "evidence_excerpt": "PaperLens title",
            },
        ],
        "reason": "Author impersonation was detected.",
        "remediation": "Use third-person attribution.",
    }
    assessment = RiskAssessment.model_validate(
        {
            "compliance_context": valid_compliance_payload(),
            "risk_findings": findings,
            "level_points": 0,
        }
    )
    report = AuditReport.model_validate(
        {
            "audit_status": "deep_complete",
            "dimensions": valid_dimensions_payload(risk_level_points=0),
            "risk_assessment": assessment,
            "hard_failures": ["AUTHOR_IMPERSONATION"],
            "core_gate_passed": False,
            "overall_score": 95.0,
            "decision": "unqualified",
        }
    )

    restored = AuditReport.model_validate_json(report.model_dump_json())

    assert restored == report
    assert restored.risk_assessment is not None
    assert len(restored.risk_assessment.risk_findings[1].locations) == 2


def valid_parse_quality_payload() -> dict[str, object]:
    return {
        "page_count": 2,
        "block_count": 4,
        "empty_page_rate": 0.0,
        "abnormal_character_rate": 0.0,
        "page_number_completeness_rate": 1.0,
        "bbox_availability_rate": 1.0,
    }


def valid_project_view_payload(
    *,
    stage: str,
    pending_patch: dict[str, object] | None,
) -> dict[str, object]:
    bundle = GeneratedBundle.model_validate(load_json("generation_valid.json"))
    return {
        "project_id": "project-001",
        "stage": stage,
        "model_mode": "mock",
        "parse_quality": valid_parse_quality_payload(),
        "source_block_count": 4,
        "current_version_id": "version-001",
        "current_version_no": 1,
        "document": bundle.document.model_dump(mode="json"),
        "claims": [claim.model_dump(mode="json") for claim in bundle.claims],
        "evidence_records": [],
        "audit_report": None,
        "versions": [
            {
                "version_id": "version-001",
                "version_no": 1,
                "parent_version_id": None,
                "reason": "initial_generation",
                "created_at": "2026-08-24T01:02:03Z",
            }
        ],
        "pending_patch": pending_patch,
        "error_code": None,
        "retryable_stage": None,
        "created_at": "2026-08-24T01:02:03Z",
        "updated_at": "2026-08-24T01:02:03Z",
    }


def test_patch_status_public_enum_has_only_fixed_values() -> None:
    patch_status = getattr(models, "PatchStatus")

    assert [member.value for member in patch_status] == [
        "pending",
        "accepted",
        "rejected",
    ]


@pytest.mark.parametrize("stage", ["quick_checked", "deep_audited"])
def test_project_view_stable_stage_requires_explicit_null_pending_patch(
    stage: str,
) -> None:
    view = ProjectView.model_validate(
        valid_project_view_payload(stage=stage, pending_patch=None)
    )

    assert view.pending_patch is None


def test_project_view_patch_pending_requires_complete_edit_patch() -> None:
    patch_payload = load_json("patch_sentence_valid.json")

    view = ProjectView.model_validate(
        valid_project_view_payload(
            stage="patch_pending",
            pending_patch=patch_payload,
        )
    )

    assert view.pending_patch == EditPatch.model_validate(patch_payload)


@pytest.mark.parametrize(
    ("stage", "pending_patch"),
    [
        ("patch_pending", None),
        ("quick_checked", load_json("patch_sentence_valid.json")),
        ("deep_audited", load_json("patch_sentence_valid.json")),
    ],
)
def test_project_view_rejects_inconsistent_pending_patch_state(
    stage: str,
    pending_patch: dict[str, object] | None,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProjectView.model_validate(
            valid_project_view_payload(
                stage=stage,
                pending_patch=pending_patch,
            )
        )

    assert "extra_forbidden" not in {
        error["type"] for error in exc_info.value.errors()
    }


def test_project_view_pending_patch_is_a_required_public_schema_field() -> None:
    schema = ProjectView.model_json_schema()
    payload = valid_project_view_payload(
        stage="quick_checked",
        pending_patch=None,
    )
    payload.pop("pending_patch")

    assert "pending_patch" in schema["required"]
    with pytest.raises(ValidationError, match="pending_patch"):
        ProjectView.model_validate(payload)


def test_stage_four_snapshot_contracts_round_trip_strictly() -> None:
    bundle = GeneratedBundle.model_validate(load_json("generation_valid.json"))
    blocks = [SourceBlock.model_validate(item) for item in load_json("source_blocks.json")]
    evidence = EvidenceRecord(
        claim_id="c-001",
        block_id="p01-b001",
        page_index=0,
        quote="PaperLens fixture - page one",
        bbox=blocks[0].bbox,
        match_method="model_candidate",
        quote_verified=True,
        rule_flags=[],
    )

    parse_snapshot = ParseSnapshot(
        blocks=blocks,
        quality=ParseQualitySnapshot.model_validate(valid_parse_quality_payload()),
    )
    claims_snapshot = ClaimsSnapshot(claims=bundle.claims)
    evidence_snapshot = EvidenceSnapshot(evidence_records=[evidence])
    usage_snapshot = UsageSnapshot(
        prompt_tokens=10,
        completion_tokens=None,
        total_tokens=15,
    )
    metadata = RunMetadata(
        message="Generation failed safely.",
        retryable=True,
        retryable_stage="generated",
        model="hy3",
        prompt_version="generation-v1",
        schema_version="generated-bundle-v1",
    )

    assert ParseSnapshot.model_validate_json(parse_snapshot.model_dump_json()) == parse_snapshot
    assert ClaimsSnapshot.model_validate_json(claims_snapshot.model_dump_json()) == claims_snapshot
    assert EvidenceSnapshot.model_validate_json(evidence_snapshot.model_dump_json()) == evidence_snapshot
    assert UsageSnapshot.model_validate_json(usage_snapshot.model_dump_json()) == usage_snapshot
    assert RunMetadata.model_validate_json(metadata.model_dump_json()) == metadata
    assert set(RunOperation) == {
        "parse",
        "generate",
        "quick_check",
        "deep_audit",
        "revision",
    }
    assert RunOperation.REVISION.value == "revision"
    assert set(RunMode) == {"local", "mock", "live"}
    assert set(RunStatus) == {"succeeded", "failed"}


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            ParseQualitySnapshot,
            {**valid_parse_quality_payload(), "page_count": -1},
        ),
        (
            ParseQualitySnapshot,
            {**valid_parse_quality_payload(), "empty_page_rate": 1.01},
        ),
        (
            UsageSnapshot,
            {
                "prompt_tokens": -1,
                "completion_tokens": None,
                "total_tokens": None,
            },
        ),
        (
            RunMetadata,
            {
                "message": None,
                "retryable": False,
                "retryable_stage": None,
                "model": None,
                "prompt_version": None,
                "schema_version": None,
                "provider_response": "must not be stored",
            },
        ),
    ],
)
def test_stage_four_snapshot_contracts_reject_invalid_values(
    model: type,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_stage_four_core_api_models_enforce_fixed_contracts() -> None:
    bundle = GeneratedBundle.model_validate(load_json("generation_valid.json"))
    blocks = [SourceBlock.model_validate(item) for item in load_json("source_blocks.json")]
    quality = ParseQualitySnapshot.model_validate(valid_parse_quality_payload())
    evidence = EvidenceRecord(
        claim_id="c-001",
        block_id="p01-b001",
        page_index=0,
        quote="PaperLens fixture - page one",
        bbox=blocks[0].bbox,
        match_method="model_candidate",
        quote_verified=True,
        rule_flags=[],
    )
    quick_report = AuditReport(
        audit_status="quick_complete",
        dimensions=[],
        risk_assessment=None,
        hard_failures=[],
        core_gate_passed=None,
        overall_score=None,
        decision="pending_deep_audit",
    )
    now = datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc)
    version = VersionSummary(
        version_id="version-001",
        version_no=1,
        parent_version_id=None,
        reason="initial_generation",
        created_at=now,
    )

    created = ProjectCreateResponse(
        project_id="project-001",
        stage="parsed",
        parse_quality=quality,
        source_block_count=len(blocks),
        created_at=now,
    )
    view = ProjectView(
        project_id="project-001",
        stage="quick_checked",
        model_mode="mock",
        parse_quality=quality,
        source_block_count=len(blocks),
        current_version_id="version-001",
        current_version_no=1,
        document=bundle.document,
        claims=bundle.claims,
        evidence_records=[evidence],
        audit_report=quick_report,
        versions=[version],
        pending_patch=None,
        error_code=None,
        retryable_stage=None,
        created_at=now,
        updated_at=now,
    )
    generation = GenerationResponse(
        project_id="project-001",
        version_id="version-001",
        stage="quick_checked",
        model_mode="mock",
        document=bundle.document,
        claims=bundle.claims,
        evidence_records=[evidence],
        quick_report=quick_report,
    )
    audit_request = DeepAuditRequest(
        source_disclosure_status="present",
        ai_assistance_disclosure_status="present",
        generated_content_label_applicability="not_applicable",
        generated_content_label_status="not_applicable",
    )

    assert created.stage.value == "parsed"
    assert view.versions == [version]
    assert generation.stage.value == "quick_checked"
    assert audit_request.generated_content_label_status.value == "not_applicable"


@pytest.mark.parametrize("claim_policy", ["required", "must_be_empty"])
def test_generation_request_accepts_only_explicit_claim_policies(
    claim_policy: str,
) -> None:
    request = GenerationRequest.model_validate({"claim_policy": claim_policy})

    assert request.claim_policy == claim_policy


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"claim_policy": "optional"},
        {"claim_policy": "required", "unexpected": True},
    ],
)
def test_generation_request_rejects_missing_invalid_or_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload", "message"),
    [
        (
            ProjectCreateResponse,
            {
                "project_id": "project-001",
                "stage": "created",
                "parse_quality": valid_parse_quality_payload(),
                "source_block_count": 4,
                "created_at": "2026-08-24T01:02:03Z",
            },
            "stage",
        ),
        (
            VersionSummary,
            {
                "version_id": "version-001",
                "version_no": 1,
                "parent_version_id": None,
                "reason": "initial_generation",
                "created_at": "2026-08-24T01:02:03+08:00",
            },
            "UTC",
        ),
        (
            DeepAuditRequest,
            {
                "source_disclosure_status": "present",
                "ai_assistance_disclosure_status": "present",
                "generated_content_label_applicability": "applicable",
                "generated_content_label_status": "not_applicable",
            },
            "generated content",
        ),
        (
            DeepAuditRequest,
            {
                "source_disclosure_status": "present",
                "ai_assistance_disclosure_status": "present",
                "generated_content_label_applicability": "not_applicable",
                "generated_content_label_status": "not_applicable",
                "rights_or_license_confirmed": True,
            },
            "Extra inputs",
        ),
    ],
)
def test_stage_four_api_models_reject_wrong_stage_time_or_request(
    model: type,
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        model.model_validate(payload)


def test_stage_four_deep_audit_response_requires_deep_audited_stage() -> None:
    payload = {
        "project_id": "project-001",
        "version_id": "version-001",
        "stage": "quick_checked",
        "audit_report": {
            "audit_status": "quick_complete",
            "dimensions": [],
            "risk_assessment": None,
            "hard_failures": [],
            "core_gate_passed": None,
            "overall_score": None,
            "decision": "pending_deep_audit",
        },
    }

    with pytest.raises(ValidationError, match="stage"):
        DeepAuditResponse.model_validate(payload)
