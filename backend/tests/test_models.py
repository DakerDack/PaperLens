import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.models import (
    AuditReport,
    EditPatch,
    EvidenceRecord,
    GeneratedBundle,
    SemanticJudgment,
    SourceBlock,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
    judgments = [
        SemanticJudgment.model_validate(item)
        for item in load_json("deep_audit_valid.json")
    ]
    patch = EditPatch.model_validate(load_json("patch_sentence_valid.json"))

    assert len(judgments) == 2
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
