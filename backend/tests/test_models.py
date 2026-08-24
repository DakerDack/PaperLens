import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.models import (
    AuditReport,
    ComplianceContext,
    DeepAuditResult,
    DimensionId,
    EditPatch,
    EvidenceRecord,
    GeneratedBundle,
    RiskAssessment,
    RiskFinding,
    RiskLocation,
    SourceBlock,
)


FIXTURES = Path(__file__).parent / "fixtures"


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
            "reason": "No risk was detected.",
            "remediation": "No change is required.",
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
    return [
        {
            "dimension_id": dimension_id.value,
            "raw_metrics": {
                "level_points": (
                    risk_level_points
                    if dimension_id == DimensionId.RISK_COMPLIANCE
                    else 4
                )
            },
            "score": (
                risk_level_points * 25.0
                if dimension_id == DimensionId.RISK_COMPLIANCE
                else 100.0
            ),
            "level": "good" if risk_level_points >= 4 else "poor",
        }
        for dimension_id in DimensionId
    ]


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


def test_deep_audit_and_patch_fixtures_are_valid() -> None:
    result = DeepAuditResult.model_validate(load_json("deep_audit_valid.json"))
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
    assert set(schema["required"]) == {"semantic_judgments", "risk_findings"}
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


def test_deep_audit_v2_nested_models_reject_extra_fields() -> None:
    payload = load_json("deep_audit_valid.json")
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
