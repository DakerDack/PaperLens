import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    RunMetadata,
    RunMode,
    RunOperation,
    RunStatus,
    SourceBlock,
    UsageSnapshot,
    VersionSummary,
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
    assert set(RunOperation) == {"parse", "generate", "quick_check", "deep_audit"}
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
