import json
from hashlib import sha256
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from openai import OpenAIError
from pydantic import TypeAdapter

from backend.app.hy3_service import (
    DEEP_AUDIT_MAX_COMPLETION_TOKENS,
    GENERATION_MAX_COMPLETION_TOKENS,
    SAFE_DIAGNOSTIC_MAX_LENGTH,
    Hy3Service,
    Hy3ServiceError,
)
from backend.app.models import (
    AtomicClaim,
    EditPatch,
    DeepAuditResult,
    EvidenceRecord,
    GeneratedBundle,
    SectionId,
    SentenceClaimRegenerationResult,
    SourceBlock,
)
from backend.app.prompts import (
    COMMON_SYSTEM_PROMPT,
    DEEP_AUDIT_SYSTEM_PROMPT,
    DEEP_AUDIT_PROMPT_VERSION,
    DEEP_AUDIT_SCHEMA_NAME,
    DEEP_AUDIT_SCHEMA_VERSION,
    GENERATION_PROMPT_VERSION,
    GENERATION_SCHEMA_NAME,
    GENERATION_SCHEMA_VERSION,
    REVISION_PROMPT_VERSION,
    SENTENCE_CLAIMS_PROMPT_VERSION,
    render_deep_audit_prompt,
    render_generation_prompt,
)
from backend.app.project_store import ProjectStore
from backend.app.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"
LOCAL_PATCH_ID = "123e4567-e89b-42d3-a456-426614174010"
OTHER_PATCH_ID = "223e4567-e89b-42d3-a456-426614174011"


def load_source_blocks() -> list[SourceBlock]:
    payload = json.loads((FIXTURES / "source_blocks.json").read_text(encoding="utf-8"))
    return TypeAdapter(list[SourceBlock]).validate_python(payload)


def mock_settings() -> Settings:
    return Settings(_env_file=None, paperlens_model_mode="mock")


def live_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "paperlens_model_mode": "live",
        "hy3_api_key": "unit-test-key",
        "hy3_base_url": "https://tokenhub.tencentmaas.com/v1",
        "hy3_model": "hy3",
        "hy3_timeout_seconds": 30,
        "hy3_max_retries": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeCompletions:
    def __init__(
        self,
        responses: list[str | BaseException | SimpleNamespace],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, SimpleNamespace):
            return response
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=response))
            ],
            usage=SimpleNamespace(
                prompt_tokens=101,
                completion_tokens=53,
                total_tokens=154,
            ),
        )


class FakeClient:
    def __init__(
        self,
        responses: list[str | BaseException | SimpleNamespace],
        *,
        models: list[SimpleNamespace] | None = None,
        models_error: BaseException | None = None,
    ) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)
        self.models = FakeModels(models or [], error=models_error)


class FakeModels:
    def __init__(
        self,
        models: list[SimpleNamespace],
        *,
        error: BaseException | None = None,
    ) -> None:
        self.data = models
        self.error = error
        self.calls = 0

    def list(self) -> SimpleNamespace:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=self.data)


def valid_generation_json() -> str:
    return (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")


def generation_json_with_claims(*, present: bool) -> str:
    payload = json.loads(valid_generation_json())
    if not present:
        payload["claims"] = []
    return json.dumps(payload, ensure_ascii=False)


def generated_bundle() -> GeneratedBundle:
    return GeneratedBundle.model_validate_json(valid_generation_json())


def revision_patch_json(
    *,
    patch_id: str = LOCAL_PATCH_ID,
    base_version: int,
    scope: str,
    target_sentence_ids: list[str],
    before_text: str,
    after_text: str,
) -> str:
    return json.dumps(
        {
            "patch_id": patch_id,
            "base_version": base_version,
            "scope": scope,
            "target_sentence_ids": target_sentence_ids,
            "before_hash": sha256(before_text.encode("utf-8")).hexdigest(),
            "before_text": before_text,
            "after_text": after_text,
            "reason": "Apply the requested bounded revision.",
            "fact_changed": False,
            "evidence_changed": False,
        },
        ensure_ascii=False,
    )


def sentence_claim_regeneration_json(
    claims: list[dict[str, object]],
) -> str:
    return json.dumps({"claims": claims}, ensure_ascii=False)


def regenerated_sentence_claim(
    *,
    claim_id: str = "c-revised-001",
    sentence_id: str = "s-001",
    auditability: str = "auditable",
    candidate_block_ids: list[str] | None = None,
) -> dict[str, object]:
    block_ids = (
        ["p01-b001"]
        if candidate_block_ids is None
        else candidate_block_ids
    )
    return {
        "claim_id": claim_id,
        "sentence_id": sentence_id,
        "text": "The revised sentence preserves the supported result.",
        "claim_type": "result",
        "importance": "critical",
        "qualifiers": [],
        "numeric_entities": [],
        "auditability": auditability,
        "candidate_block_ids": block_ids,
        "candidate_quote": (
            "PaperLens fixture - page one" if block_ids else None
        ),
    }


def verified_claim_evidence_pairs() -> list[tuple[AtomicClaim, EvidenceRecord]]:
    bundle = generated_bundle()
    claims = {claim.claim_id: claim for claim in bundle.claims}
    blocks = {block.block_id: block for block in load_source_blocks()}
    return [
        (
            claims["c-001"],
            EvidenceRecord(
                claim_id="c-001",
                block_id="p01-b001",
                page_index=0,
                quote="PaperLens fixture - page one",
                bbox=blocks["p01-b001"].bbox,
                match_method="model_candidate",
                quote_verified=True,
                rule_flags=[],
            ),
        ),
        (
            claims["c-002"],
            EvidenceRecord(
                claim_id="c-002",
                block_id="p01-b002",
                page_index=0,
                quote="Synthetic test text. No private paper content.",
                bbox=blocks["p01-b002"].bbox,
                match_method="model_candidate",
                quote_verified=True,
                rule_flags=[],
            ),
        ),
        (
            claims["c-003"],
            EvidenceRecord(
                claim_id="c-003",
                block_id="p02-b001",
                page_index=1,
                quote="PaperLens fixture - page two",
                bbox=blocks["p02-b001"].bbox,
                match_method="model_candidate",
                quote_verified=True,
                rule_flags=[],
            ),
        ),
        (
            claims["c-004"],
            EvidenceRecord(
                claim_id="c-004",
                block_id="p02-b002",
                page_index=1,
                quote="Second synthetic page for zero-based mapping.",
                bbox=blocks["p02-b002"].bbox,
                match_method="model_candidate",
                quote_verified=True,
                rule_flags=[],
            ),
        ),
    ]


def valid_deep_audit_json() -> str:
    return (FIXTURES / "deep_audit_valid.json").read_text(encoding="utf-8")


def risk_only_deep_audit_with_extra_finding_field(
    *,
    field_name: str,
    field_value: str,
) -> tuple[str, str]:
    valid_payload = json.loads(valid_deep_audit_json())
    valid_payload["semantic_judgments"] = []
    invalid_payload = json.loads(json.dumps(valid_payload))
    for finding in invalid_payload["risk_findings"]:
        finding[field_name] = field_value
    return (
        json.dumps(invalid_payload, ensure_ascii=False),
        json.dumps(valid_payload, ensure_ascii=False),
    )


def deep_audit_with_risk_category_coverage(variant: str) -> str:
    payload = json.loads(valid_deep_audit_json())
    findings = payload["risk_findings"]
    for index, finding in enumerate(findings):
        finding["reason"] = f"PRIVATE_RISK_REASON_{index}"
        finding["remediation"] = f"PRIVATE_RISK_REMEDIATION_{index}"
    if variant == "missing":
        payload["risk_findings"] = findings[:2]
    elif variant == "duplicate":
        payload["risk_findings"] = [findings[0], findings[1], findings[1]]
    elif variant == "extra":
        payload["risk_findings"] = [*findings, findings[0]]
    else:
        raise AssertionError(f"unknown risk coverage variant: {variant}")
    return json.dumps(payload)


def completion_response(
    content: Any,
    usage: tuple[int | None, int | None, int | None] | None,
    *,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    usage_payload = None
    if usage is not None:
        usage_payload = SimpleNamespace(
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
        )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=usage_payload,
    )


def generation_attempt_logs(caplog: Any) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("hy3_generation_attempt ")
    ]


def deep_audit_attempt_logs(caplog: Any) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("hy3_deep_audit_attempt ")
    ]


def hy3_run_logs(caplog: Any) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("hy3_run ")
    ]


def generation_retry_summary(prompt: str) -> str:
    marker = "\n\n字段错误摘要："
    assert marker in prompt
    return prompt.split(marker, 1)[1]


def attempt_log_fields(message: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in message.split()[1:])


def assert_safe_attempt_log_fields(
    message: str,
    *,
    operation: str,
    boundary: str,
    error_code: str,
) -> None:
    fields = attempt_log_fields(message)
    assert set(fields) == {
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
    assert fields["operation"] == operation
    assert fields["validation_boundary"] == boundary
    assert fields["error_code"] == error_code
    assert SAFE_DIAGNOSTIC_MAX_LENGTH == 512
    assert len(message) <= SAFE_DIAGNOSTIC_MAX_LENGTH


def assert_closed_objects(node: object) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
        for value in node.values():
            assert_closed_objects(value)
    elif isinstance(node, list):
        for value in node:
            assert_closed_objects(value)


def test_live_sentence_revision_sends_only_target_sentence_and_related_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_PATCH_ID,
        raising=False,
    )
    bundle = generated_bundle()
    target = bundle.document.sections[0].sentences[0]
    unrelated_text = bundle.document.sections[1].sentences[0].text
    evidence = verified_claim_evidence_pairs()[0][1]
    unrelated_quote = verified_claim_evidence_pairs()[1][1].quote
    after_text = f"{target.text}（按用户意图调整表达）"
    supplier_output = revision_patch_json(
        base_version=3,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_text=target.text,
        after_text=after_text,
    )
    client = FakeClient([supplier_output])
    service = Hy3Service(settings=live_settings(), client=client)

    patch = service.revise_sentence(
        base_version=3,
        sentence_id=target.sentence_id,
        current_text=target.text,
        evidence_records=[evidence],
        user_instruction="保持事实不变并简化表达。",
    )

    assert isinstance(patch, EditPatch)
    assert patch == EditPatch.model_validate_json(supplier_output)
    assert patch.patch_id == LOCAL_PATCH_ID
    assert patch.after_text == after_text
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    prompt = request["messages"][1]["content"]
    assert target.sentence_id in prompt
    assert target.text in prompt
    assert evidence.quote in prompt
    assert "保持事实不变并简化表达。" in prompt
    assert unrelated_text not in prompt
    assert unrelated_quote not in prompt
    assert "history" not in prompt.casefold()
    assert LOCAL_PATCH_ID in prompt
    response_schema = request["response_format"]["json_schema"]["schema"]
    assert response_schema["properties"]["patch_id"]["const"] == LOCAL_PATCH_ID
    assert "const" not in EditPatch.model_json_schema()["properties"]["patch_id"]
    assert REVISION_PROMPT_VERSION == "revision-v2"


def test_live_document_revision_sends_current_five_sections_without_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_PATCH_ID,
        raising=False,
    )
    document = generated_bundle().document
    before_text = document.model_dump_json()
    revised = document.model_copy(update={"title": "修订后的本科生论文解读"})
    after_text = revised.model_dump_json()
    client = FakeClient(
        [
            revision_patch_json(
                base_version=4,
                scope="document",
                target_sentence_ids=[],
                before_text=before_text,
                after_text=after_text,
            )
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)

    patch = service.revise_document(
        base_version=4,
        document=document,
        user_instruction="统一五区表达并保留全部限制。",
    )

    assert patch.scope.value == "document"
    assert patch.after_text == after_text
    prompt = client.completions.calls[0]["messages"][1]["content"]
    for section in document.sections:
        assert section.section_id.value in prompt
    assert "统一五区表达并保留全部限制。" in prompt
    assert "HISTORICAL_VERSION_SENTINEL" not in prompt
    assert "verified_evidence" not in prompt
    assert "source_blocks" not in prompt
    assert LOCAL_PATCH_ID in prompt


def test_live_revision_retries_schema_errors_with_one_local_patch_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uuid_calls = 0

    def assign_patch_id() -> str:
        nonlocal uuid_calls
        uuid_calls += 1
        return LOCAL_PATCH_ID

    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        assign_patch_id,
        raising=False,
    )
    current_text = "当前目标句。"
    accepted_supplier_output = revision_patch_json(
        base_version=2,
        scope="sentence",
        target_sentence_ids=["s-target"],
        before_text=current_text,
        after_text="第三次返回的合法目标句。",
    )
    wrong_id_output = revision_patch_json(
        patch_id=OTHER_PATCH_ID,
        base_version=2,
        scope="sentence",
        target_sentence_ids=["s-target"],
        before_text=current_text,
        after_text="第二次返回但 ID 错误的目标句。",
    )
    client = FakeClient(
        [
            json.dumps({"patch_id": LOCAL_PATCH_ID}),
            wrong_id_output,
            accepted_supplier_output,
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)

    patch = service.revise_sentence(
        base_version=2,
        sentence_id="s-target",
        current_text=current_text,
        evidence_records=[],
        user_instruction="只修改这一句。",
    )

    assert patch.model_dump(mode="json") == json.loads(accepted_supplier_output)
    assert uuid_calls == 1
    assert len(client.completions.calls) == 3
    for request in client.completions.calls:
        prompt = request["messages"][1]["content"]
        response_schema = request["response_format"]["json_schema"]["schema"]
        assert LOCAL_PATCH_ID in prompt
        assert response_schema["properties"]["patch_id"]["const"] == LOCAL_PATCH_ID


def test_mock_sentence_revision_returns_bounded_preview_without_provider() -> None:
    target = generated_bundle().document.sections[0].sentences[0]
    service = Hy3Service(settings=mock_settings())

    patch = service.revise_sentence(
        base_version=5,
        sentence_id=target.sentence_id,
        current_text=target.text,
        evidence_records=[],
        user_instruction="保持事实不变并简化表达。",
    )

    assert patch.base_version == 5
    assert patch.scope.value == "sentence"
    assert patch.target_sentence_ids == [target.sentence_id]
    assert patch.before_text == target.text
    assert patch.before_hash == sha256(target.text.encode("utf-8")).hexdigest()
    assert patch.after_text != target.text
    assert "\n" not in patch.after_text
    assert patch.fact_changed is False
    assert patch.evidence_changed is False


def test_mock_nontrivial_sentence_revision_is_not_a_punctuation_append() -> None:
    target = generated_bundle().document.sections[0].sentences[0]
    instruction = "USER_INSTRUCTION_MUST_NOT_BE_REPLAYED"
    service = Hy3Service(settings=mock_settings())

    patch = service.revise_sentence(
        base_version=5,
        sentence_id=target.sentence_id,
        current_text=target.text,
        evidence_records=[],
        user_instruction=instruction,
    )

    assert patch.after_text != f"{target.text}!"
    assert patch.after_text != f"{target.text}！"
    assert patch.after_text.strip()
    assert "\n" not in patch.after_text
    assert "\r" not in patch.after_text
    assert instruction not in patch.after_text
    assert "Mock" in patch.after_text or "Mock" in patch.reason
    assert ProjectStore.requires_sentence_claim_regeneration(patch) is True


def test_mock_sentence_claim_regeneration_uses_only_confirmed_context() -> None:
    bundle = generated_bundle()
    target_claim = bundle.claims[0]
    evidence = verified_claim_evidence_pairs()[0][1]
    accepted_after_text = (
        "The accepted target sentence now states the supported result clearly."
    )
    kwargs = {
        "target_sentence_id": target_claim.sentence_id,
        "accepted_after_text": accepted_after_text,
        "original_claims": [target_claim],
        "evidence_records": [evidence],
        "allowed_block_ids": {"p01-b001"},
        "reserved_claim_ids": {"c-unmodified"},
        "user_instruction": "USER_INSTRUCTION_MUST_NOT_BE_REPLAYED",
    }
    service = Hy3Service(settings=mock_settings())

    first = service.regenerate_sentence_claims(**kwargs)
    second = service.regenerate_sentence_claims(**kwargs)

    assert first == second
    assert first.claims
    claim_ids = [claim.claim_id for claim in first.claims]
    assert len(claim_ids) == len(set(claim_ids))
    assert set(claim_ids).isdisjoint(kwargs["reserved_claim_ids"])
    assert all(
        claim.sentence_id == target_claim.sentence_id
        for claim in first.claims
    )
    assert all(claim.text == accepted_after_text for claim in first.claims)
    assert all(
        set(claim.candidate_block_ids) <= kwargs["allowed_block_ids"]
        for claim in first.claims
    )
    assert any(claim.auditability.value == "auditable" for claim in first.claims)
    assert all(
        claim.candidate_quote in {None, evidence.quote}
        for claim in first.claims
    )


def test_mock_sentence_claim_regeneration_drops_stale_semantic_fields() -> None:
    original_claim = generated_bundle().claims[0].model_copy(
        update={
            "text": "The previous threshold was 10 units.",
            "qualifiers": ["under the previous condition"],
            "numeric_entities": ["10"],
        }
    )
    accepted_after_text = "The accepted threshold is 20 units."

    regenerated = Hy3Service(
        settings=mock_settings()
    ).regenerate_sentence_claims(
        target_sentence_id=original_claim.sentence_id,
        accepted_after_text=accepted_after_text,
        original_claims=[original_claim],
        evidence_records=[],
        allowed_block_ids=set(original_claim.candidate_block_ids),
        reserved_claim_ids=set(),
        user_instruction="Replace the previous threshold with the accepted one.",
    )

    assert len(regenerated.claims) == 1
    rebuilt_claim = regenerated.claims[0]
    assert rebuilt_claim.text == accepted_after_text
    assert rebuilt_claim.claim_id != original_claim.claim_id
    assert rebuilt_claim.qualifiers == []
    assert rebuilt_claim.numeric_entities == []


def test_mock_document_claim_regeneration_uses_exact_current_document() -> None:
    original = generated_bundle().document.model_dump(mode="json")
    original["title"] = "Custom accepted document"
    for index, section in enumerate(original["sections"], start=1):
        sentence = section["sentences"][0]
        sentence["sentence_id"] = f"custom-s-{index:03d}"
        sentence["text"] = f"Custom accepted sentence {index}."
    document = type(generated_bundle().document).model_validate(original)
    blocks = load_source_blocks()[:2]

    regenerated = Hy3Service(
        settings=mock_settings()
    ).regenerate_document_claims(
        document=document,
        source_blocks=blocks,
    )

    sentence_text = {
        sentence.sentence_id: sentence.text
        for section in document.sections
        for sentence in section.sentences
    }
    allowed_blocks = {block.block_id for block in blocks}
    assert regenerated.document == document
    assert regenerated.claims
    assert len({claim.claim_id for claim in regenerated.claims}) == len(
        regenerated.claims
    )
    assert all(claim.sentence_id in sentence_text for claim in regenerated.claims)
    assert all(
        claim.text == sentence_text[claim.sentence_id]
        for claim in regenerated.claims
    )
    assert all(
        set(claim.candidate_block_ids) <= allowed_blocks
        for claim in regenerated.claims
    )
    assert all(claim.candidate_quote is None for claim in regenerated.claims)
    assert all(
        {"page_index", "bbox", "quote_verified"}.isdisjoint(
            claim.model_dump(mode="json")
        )
        for claim in regenerated.claims
    )


def test_mock_same_sentence_revision_input_gets_a_new_patch_id_each_time() -> None:
    target = generated_bundle().document.sections[0].sentences[0]
    service = Hy3Service(settings=mock_settings())

    first = service.revise_sentence(
        base_version=5,
        sentence_id=target.sentence_id,
        current_text=target.text,
        evidence_records=[],
        user_instruction="保持事实不变并简化表达。",
    )
    second = service.revise_sentence(
        base_version=5,
        sentence_id=target.sentence_id,
        current_text=target.text,
        evidence_records=[],
        user_instruction="保持事实不变并简化表达。",
    )

    assert first.patch_id != second.patch_id


def test_mock_document_revision_returns_valid_preview_without_provider() -> None:
    document = generated_bundle().document
    before_text = document.model_dump_json()
    service = Hy3Service(settings=mock_settings())

    patch = service.revise_document(
        base_version=6,
        document=document,
        user_instruction="统一五区表达并保留全部限制。",
    )

    revised = type(document).model_validate_json(patch.after_text)
    assert patch.base_version == 6
    assert patch.scope.value == "document"
    assert patch.target_sentence_ids == []
    assert patch.before_text == before_text
    assert patch.before_hash == sha256(before_text.encode("utf-8")).hexdigest()
    assert revised != document
    assert [section.section_id for section in revised.sections] == list(SectionId)
    assert document.model_dump_json() == before_text


@pytest.mark.parametrize(
    ("before_hash", "after_text"),
    [
        ("0" * 64, "合法的单句改写。"),
        (None, "合法的单句改写。\n越界修改另一句。"),
    ],
    ids=["wrong-before-hash", "out-of-scope-newline"],
)
def test_live_sentence_revision_rejects_stale_or_out_of_scope_patch(
    monkeypatch: pytest.MonkeyPatch,
    before_hash: str | None,
    after_text: str,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_PATCH_ID,
        raising=False,
    )
    current_text = "当前目标句。"
    payload = json.loads(
        revision_patch_json(
            base_version=2,
            scope="sentence",
            target_sentence_ids=["s-target"],
            before_text=current_text,
            after_text=after_text,
        )
    )
    if before_hash is not None:
        payload["before_hash"] = before_hash
    client = FakeClient([json.dumps(payload, ensure_ascii=False)])
    service = Hy3Service(settings=live_settings(), client=client)

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.revise_sentence(
            base_version=2,
            sentence_id="s-target",
            current_text=current_text,
            evidence_records=[],
            user_instruction="只修改这一句。",
        )

    assert exc_info.value.error_code == "PATCH_INVALID"
    assert exc_info.value.retryable is False
    assert len(client.completions.calls) == 1


@pytest.mark.parametrize(
    "supplier_patch_id",
    [OTHER_PATCH_ID, None],
    ids=["wrong-id", "missing-id"],
)
def test_live_revision_rejects_missing_or_mismatched_local_patch_id(
    monkeypatch: pytest.MonkeyPatch,
    supplier_patch_id: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_PATCH_ID,
        raising=False,
    )
    current_text = "当前目标句。"
    payload = json.loads(
        revision_patch_json(
            patch_id=OTHER_PATCH_ID,
            base_version=2,
            scope="sentence",
            target_sentence_ids=["s-target"],
            before_text=current_text,
            after_text="按用户意图调整后的目标句。",
        )
    )
    if supplier_patch_id is None:
        payload.pop("patch_id")
    else:
        payload["patch_id"] = supplier_patch_id
    supplier_output = json.dumps(payload, ensure_ascii=False)
    client = FakeClient([supplier_output, supplier_output, supplier_output])
    service = Hy3Service(settings=live_settings(), client=client)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.revise_sentence(
                base_version=2,
                sentence_id="s-target",
                current_text=current_text,
                evidence_records=[],
                user_instruction="USER_INSTRUCTION_SENTINEL",
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False
    assert exc_info.value.retries == 2
    assert exc_info.value.usage == (303, 159, 462)
    assert len(client.completions.calls) == 3
    assert client.completions.responses == []
    for secret in {
        "USER_INSTRUCTION_SENTINEL",
        supplier_output,
        "unit-test-key",
    }:
        assert secret not in str(exc_info.value)
        assert secret not in caplog.text


def test_live_revision_provider_failure_is_not_retried_or_mocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_PATCH_ID,
        raising=False,
    )
    client = FakeClient(
        [
            OpenAIError("RAW_PROVIDER_RESPONSE_SENTINEL"),
            revision_patch_json(
                base_version=2,
                scope="sentence",
                target_sentence_ids=["s-target"],
                before_text="当前目标句。",
                after_text="不应被读取的第二次响应。",
            ),
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_build_mock_revision_patch",
        lambda **_kwargs: pytest.fail("Live failure must not use Mock revision"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.revise_sentence(
            base_version=2,
            sentence_id="s-target",
            current_text="当前目标句。",
            evidence_records=[],
            user_instruction="只修改这一句。",
        )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert exc_info.value.retries == 0
    assert exc_info.value.usage == (None, None, None)
    assert len(client.completions.calls) == 1
    assert len(client.completions.responses) == 1


def test_generation_prompt_centralizes_stage_two_contract() -> None:
    prompt = render_generation_prompt(
        claim_policy="required",
        paper_metadata_json='{"title":"Synthetic"}',
        source_blocks_json='[{"block_id":"p01-b001","text":"Evidence"}]',
    )

    assert GENERATION_PROMPT_VERSION == "gen-v4"
    assert GENERATION_SCHEMA_VERSION == "generated-bundle-v1"
    assert GENERATION_SCHEMA_NAME == "paperlens_generated_bundle_v1"
    assert "只能依据输入中的 SourceBlock" in COMMON_SYSTEM_PROMPT
    for section_id in SectionId:
        assert section_id.value in prompt
    assert "candidate_block_ids" in prompt
    assert "最多 3 个" in prompt
    assert "候选" in prompt
    assert "claim_policy: required" in prompt
    assert "claim_policy=required 时，claims 必须至少包含 1 项" in prompt
    assert "claim_policy=must_be_empty 时，claims 必须严格为空数组" in prompt
    assert "不得生成页码、bbox、总分或合格结论" in prompt
    assert 'paper_metadata: {"title":"Synthetic"}' in prompt
    assert 'source_blocks: [{"block_id":"p01-b001","text":"Evidence"}]' in prompt


def test_generation_and_deep_audit_output_budgets_remain_independent() -> None:
    assert GENERATION_MAX_COMPLETION_TOKENS == 16384
    assert DEEP_AUDIT_MAX_COMPLETION_TOKENS == 4096


def test_deep_audit_prompt_centralizes_v2_document_contract() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json=(
            '{"title":"Synthetic","sections":['
            '{"section_id":"plain_explanation","sentences":['
            '{"sentence_id":"s-na","text":"NON_AUDITABLE_PRIVATE_TEXT"}]}]}'
        ),
        verified_claim_evidence_pairs_json=(
            '[{"claim":{"claim_id":"c-001"},'
            '"evidence":{"block_id":"p01-b001"}}]'
        )
    )

    assert DEEP_AUDIT_PROMPT_VERSION == "audit-v5"
    assert DEEP_AUDIT_SCHEMA_VERSION == "deep-audit-result-v2"
    assert DEEP_AUDIT_SCHEMA_NAME == "paperlens_deep_audit_result_v2"
    assert "逐条判断" in prompt
    assert "insufficient" in prompt
    assert "sensitive_information" in prompt
    assert "author_impersonation" in prompt
    assert "academic_integrity" in prompt
    assert "NON_AUDITABLE_PRIVATE_TEXT" in prompt
    assert "完整生成文档" in prompt
    assert "不得返回风险等级、分数、权重、硬失败" in prompt
    assert "页码" in prompt and "bbox" in prompt
    assert "不得补充给定 evidence 之外的知识或证据" in prompt
    assert '"claim_id":"c-001"' in prompt


def test_deep_audit_prompt_defines_non_hedging_semantic_contract() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json=(
            '[{"claim":{"claim_id":"c-001"},'
            '"evidence":{"block_id":"p01-b001"}}]'
        ),
    )

    assert DEEP_AUDIT_PROMPT_VERSION == "audit-v5"
    assert DEEP_AUDIT_SCHEMA_VERSION == "deep-audit-result-v2"
    assert "relation=supports：仅当 evidence 直接蕴含 claim 的全部实质事实时选择。" in prompt
    assert "relation=contradicts：当数字、方向、因果、比较或结论冲突时选择。" in prompt
    assert "relation=insufficient：仅当给定 evidence 缺少对 claim 的直接支持时选择。" in prompt
    assert "直接可判定的配对不得以 unclear 作为回避或兜底选择。" in prompt
    assert (
        "scope_status=preserved：只有样本、方法、比较对象、条件、数量、适用性、因果强度"
        "和限定语均未被扩大时选择。"
    ) in prompt
    assert (
        "scope_status=expanded：省略或改变比较对象、实验条件、总体范围、因果边界、数值"
        "或效应量而使 claim 更宽时选择。"
    ) in prompt
    assert "relation=supports 与 scope_status=expanded 可以同时成立。" in prompt
    assert (
        "terminology_status=correct：同义改述或不同措辞但语义相同仍为 correct；只有替换、"
        "泛化或改变含义时才选择 misused，不得仅因措辞不同选择 unclear 或 misused。"
    ) in prompt
    assert (
        "当前绑定 evidence 已明确支持该限制性主张及其必要边界时，应选择 relation=supports；"
        "不得额外要求背景或其他 SourceBlock。"
    ) in prompt
    assert "severity=none：配对不存在事实、范围或术语问题。" in prompt
    assert (
        "severity=minor：存在局部精度或限定语损失，但不实质改变研究对象、比较条件、数值"
        "或效应解释、因果强度、方向或主要结论。"
    ) in prompt
    assert (
        "severity=major：错误会实质改变对一个主张的理解，例如重要范围、因果、数值、比较"
        "或方向发生变化，但尚未推翻核心或关键结论。"
    ) in prompt
    assert (
        "severity=critical：仅当问题足以反转、伪造或使核心或关键结论实质错误时选择。"
    ) in prompt
    assert (
        "scope_status=expanded 或 terminology_status=misused 时，不得在无充分理由下选择"
        " severity=none。"
    ) in prompt
    assert (
        "同一未变的 claim/evidence 配对必须给出相同判断，不得受其他 items、顺序或无关"
        "文档内容影响。"
    ) in prompt


def test_deep_audit_prompt_closes_severity_decision_contract() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json=(
            '[{"claim":{"claim_id":"c-001","importance":"critical"},'
            '"evidence":{"block_id":"p01-b001"}}]'
        ),
    )

    assert DEEP_AUDIT_PROMPT_VERSION == "audit-v5"
    assert DEEP_AUDIT_SCHEMA_VERSION == "deep-audit-result-v2"
    assert DEEP_AUDIT_SCHEMA_NAME == "paperlens_deep_audit_result_v2"
    assert (
        "severity=major：错误会实质改变对一个主张的理解，例如重要范围、因果、数值、比较"
        "或方向发生变化，但尚未推翻核心或关键结论。"
    ) in prompt
    assert (
        "severity=critical：仅当问题足以反转、伪造或使核心或关键结论实质错误时选择。"
    ) in prompt
    assert "severity=none：配对不存在事实、范围或术语问题。" in prompt
    assert (
        "severity=minor：存在局部精度或限定语损失，但不实质改变研究对象、比较条件、数值"
        "或效应解释、因果强度、方向或主要结论。"
    ) in prompt
    assert (
        "不得仅因措辞不同、claim 的 importance 标为 critical 或普通范围缺失而选择"
        " severity=critical。"
    ) in prompt
    assert "major 或 critical" not in prompt
    assert (
        "同一未变的 claim/evidence 配对必须给出相同判断，不得受其他 items、顺序或无关"
        "文档内容影响。"
    ) in prompt


def test_deep_audit_prompt_defines_material_omission_contract() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )
    ordered_checks = (
        (
            "先判断 claim 中保留的事实是否被 evidence 支持；再独立检查相关边界或精度是否丢失。",
            "MATERIAL_OMISSION_SUPPORT_CHECK_MISSING",
        ),
        (
            "只核对与当前断言的同一对象、谓词、结果或比较关系直接相关的数值精度和比较基准。",
            "MATERIAL_OMISSION_RELEVANCE_CHECK_MISSING",
        ),
        (
            "核心事实仍被支持，且相关精度或比较边界仅有局部损失、未实质改变主张理解时，"
            "可以使用 relation=supports + scope_status=expanded + severity=minor。",
            "MATERIAL_OMISSION_JOINT_JUDGMENT_MISSING",
        ),
    )
    for rule, category in ordered_checks:
        if rule not in prompt:
            pytest.fail(category)
    positions = [prompt.index(rule) for rule, _ in ordered_checks]
    if positions != sorted(positions):
        pytest.fail("MATERIAL_OMISSION_CHECK_ORDER_INVALID")
    for rule, category in (
        (
            "数值精度遗漏包括相关数量或效应量的精确程度丢失；"
            "比较基准遗漏包括相对对象或参照条件丢失。",
            "MATERIAL_OMISSION_TYPES_MISSING",
        ),
        (
            "数值精度遗漏：证据“装置比基准耗能低 18%”；断言“装置比基准耗能低”"
            "——仅丢失幅度且不改变结论时为 supports/expanded/minor。",
            "MATERIAL_OMISSION_NUMERIC_EXAMPLE_MISSING",
        ),
        (
            "比较基准遗漏：证据“传感器比标准探头更灵敏”；断言“传感器更灵敏”"
            "——仅丢失比较对象且未引入更强结论时为 supports/expanded/minor。",
            "MATERIAL_OMISSION_COMPARATOR_EXAMPLE_MISSING",
        ),
    ):
        if rule not in prompt:
            pytest.fail(category)


def test_deep_audit_prompt_keeps_omission_negative_controls() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )
    for rule, category in (
        (
            "不得把 evidence 中出现但 claim 未重复的所有数字、条件都判为遗漏。",
            "OMISSION_RELEVANCE_NEGATIVE_CONTROL_MISSING",
        ),
        (
            "不得仅因省略细节就自动判 contradicts、insufficient、major、critical。",
            "OMISSION_NO_AUTOMATIC_ESCALATION_MISSING",
        ),
        (
            "保留完整边界的同义改述、语序变化或合法简写不应误报；"
            "与当前断言无关的背景事实可以省略。",
            "OMISSION_PARAPHRASE_BACKGROUND_CONTROL_MISSING",
        ),
        (
            "真正的数值错误、方向反转、因果改变仍按事实与严重度契约处理，不得统一降为 minor。",
            "OMISSION_TRUE_ERROR_PROTECTION_MISSING",
        ),
        (
            "完整同义改述：证据“与标准探头相比，传感器灵敏度更高”；"
            "断言“传感器比标准探头更灵敏”——supports/preserved/none。",
            "OMISSION_PARAPHRASE_EXAMPLE_MISSING",
        ),
        (
            "无关背景省略：证据“记录仪外壳为绿色。阀门在 8 秒后关闭”；"
            "断言“阀门在 8 秒后关闭”——supports/preserved/none。",
            "OMISSION_BACKGROUND_EXAMPLE_MISSING",
        ),
        (
            "同一未变的 claim/evidence 配对必须给出相同判断，"
            "不得受其他 items、顺序或无关文档内容影响。",
            "OMISSION_PAIR_INDEPENDENCE_MISSING",
        ),
        (
            "检查步骤仅为内部指令，不新增输出字段。",
            "OMISSION_OUTPUT_SHAPE_GUARD_MISSING",
        ),
    ):
        if rule not in prompt:
            pytest.fail(category)


def test_deep_audit_prompt_isolates_claim_scope() -> None:
    claim_text = "结论只适用于密封容器中的样本。"
    items_json = json.dumps(
        [{
            "claim": {"claim_id": "c-local", "text": claim_text},
            "evidence": {
                "block_id": "b-local",
                "quote": "该结论仅适用于密封容器中的样本。",
            },
        }],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    for unrelated_text in ("指示灯为蓝色。", "指示灯为红色。"):
        document = {
            "title": "Synthetic",
            "sections": [{
                "section_id": "limitations",
                "sentences": [
                    {"sentence_id": "s-local", "text": claim_text},
                    {"sentence_id": "s-other", "text": unrelated_text},
                ],
            }],
        }
        prompt = render_deep_audit_prompt(
            content_draft_json=json.dumps(document, ensure_ascii=False),
            verified_claim_evidence_pairs_json=items_json,
        )
        for rule, category in (
            (
                "任务一的局部事实与范围判断只能使用当前 item 的 claim 和 evidence；"
                "完整 document 仅供任务二文档风险检查，不得为当前配对补充边界或借入其他句子的问题。",
                "LOCAL_SCOPE_INPUT_BOUNDARY_MISSING",
            ),
            (
                "不得根据其他句子的问题数量、文档整体质量、其他配对或排列顺序改变当前 "
                "relation、scope_status、terminology_status、severity。",
                "LOCAL_SCOPE_CONTEXT_INDEPENDENCE_MISSING",
            ),
            (f"- items: {items_json}", "LOCAL_SCOPE_PAIR_INPUT_CHANGED"),
        ):
            if rule not in prompt:
                pytest.fail(category)


def test_deep_audit_prompt_preserves_supported_limitation_scope() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )
    for rule, category in (
        (
            "限制性主张须核对其自身限制的对象及必要边界；只有本配对直接支持且无事实、范围或术语"
            "问题时才判 supports/preserved/none，不得因它是限制句就预设通过。",
            "LOCAL_LIMITATION_SUPPORT_GUARD_MISSING",
        ),
        (
            "当前绑定 evidence 已明确支持该限制性主张及其必要边界时，应选择 relation=supports；"
            "不得额外要求背景或其他 SourceBlock。",
            "LOCAL_LIMITATION_ENTAILMENT_GUARD_MISSING",
        ),
        (
            "局部限制对照：证据“该结论仅适用于密封容器中的样本”；断言“结论只适用于密封容器中的样本”"
            "——本配对支持且边界完整时为 supports/preserved/none；"
            "其他句子从“指示灯为蓝色”变为“指示灯为红色”不改变此判断。",
            "LOCAL_LIMITATION_CONTEXT_EXAMPLE_MISSING",
        ),
        (
            "保留完整边界的同义改述、语序变化或合法简写不应误报；与当前断言无关的背景事实可以省略。",
            "LOCAL_SCOPE_BACKGROUND_PROTECTION_MISSING",
        ),
    ):
        if rule not in prompt:
            pytest.fail(category)


def test_deep_audit_prompt_keeps_real_omission_errors() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )
    for rule, category in (
        (
            "判 expanded 前，必须能在当前 claim/evidence 中指出本断言实际丢失或改变的必要条件、"
            "数值精度或比较基准；不能用文档级印象代替局部证据。",
            "LOCAL_SCOPE_OMISSION_EVIDENCE_MISSING",
        ),
        (
            "必要条件遗漏：同一证据下，断言“结论适用于容器中的样本”丢失了密封条件，"
            "应标 scope_status=expanded；relation 与 severity 按本配对的实际影响判定，"
            "不得沿用前例的 preserved/none。",
            "LOCAL_SCOPE_CONDITION_OMISSION_EXAMPLE_MISSING",
        ),
        (
            "数值精度遗漏：证据“装置比基准耗能低 18%”；断言“装置比基准耗能低”"
            "——仅丢失幅度且不改变结论时为 supports/expanded/minor。",
            "LOCAL_SCOPE_NUMERIC_OMISSION_REGRESSION",
        ),
        (
            "比较基准遗漏：证据“传感器比标准探头更灵敏”；断言“传感器更灵敏”"
            "——仅丢失比较对象且未引入更强结论时为 supports/expanded/minor。",
            "LOCAL_SCOPE_COMPARATOR_OMISSION_REGRESSION",
        ),
        (
            "真正的数值错误、方向反转、因果改变仍按事实与严重度契约处理，不得统一降为 minor。",
            "LOCAL_SCOPE_TRUE_ERROR_REGRESSION",
        ),
    ):
        if rule not in prompt:
            pytest.fail(category)


def test_deep_audit_prompt_keeps_quality_labels_out_of_model_input() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )
    model_input = f"{DEEP_AUDIT_SYSTEM_PROMPT}\n{prompt}".casefold()

    for forbidden in (
        "quality_label",
        "known_error_type",
        "known_error_severity",
        "known_error_detected",
        "known_error_",
        "case_id",
        "paper_id",
        "dev-01",
        "dev-03",
        "target_score",
        "expected_answer",
        "evaluation_answer",
    ):
        assert forbidden not in model_input


def test_deep_audit_prompt_makes_risk_only_output_shape_explicit() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )

    assert "items 为空时，semantic_judgments 必须为空数组" in prompt
    assert "risk_findings 必须仍为长度恰好为 3 的数组" in prompt
    ordered_categories = [
        "risk_findings[0].category 必须为 sensitive_information",
        "risk_findings[1].category 必须为 author_impersonation",
        "risk_findings[2].category 必须为 academic_integrity",
    ]
    assert all(rule in prompt for rule in ordered_categories)
    assert [prompt.index(rule) for rule in ordered_categories] == sorted(
        prompt.index(rule) for rule in ordered_categories
    )
    assert "禁止缺失、重复、增加类别或返回空 risk_findings" in prompt


def test_deep_audit_prompt_makes_risk_finding_location_nesting_explicit() -> None:
    prompt = render_deep_audit_prompt(
        content_draft_json='{"title":"Synthetic","sections":[]}',
        verified_claim_evidence_pairs_json="[]",
    )

    assert (
        "RiskFinding 对象只能且必须包含 category、status、locations、reason、remediation"
        in prompt
    )
    assert (
        "RiskLocation 对象只能且必须包含 location_type、sentence_id、evidence_excerpt"
        in prompt
    )
    assert "evidence_excerpt 只能存在于 locations 数组的 RiskLocation 对象中" in prompt
    assert "evidence_excerpt 不得成为 RiskFinding 顶层字段" in prompt
    assert "reason 和 remediation 只属于 RiskFinding，不属于 RiskLocation" in prompt
    assert "禁止返回任何未列出的键" in prompt


def test_mock_deep_audit_returns_valid_deep_audit_result() -> None:
    result = Hy3Service(settings=mock_settings()).deep_audit(
        document=generated_bundle().document,
        claim_evidence_pairs=verified_claim_evidence_pairs()
    )

    assert isinstance(result, DeepAuditResult)
    assert [(item.claim_id, item.block_id) for item in result.semantic_judgments] == [
        ("c-001", "p01-b001"),
        ("c-002", "p01-b002"),
        ("c-003", "p02-b001"),
        ("c-004", "p02-b002"),
    ]
    assert all(item.relation == "supports" for item in result.semantic_judgments)
    assert {item.category.value for item in result.risk_findings} == {
        "sensitive_information",
        "author_impersonation",
        "academic_integrity",
    }


def test_mock_deep_audit_rejects_semantic_pair_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Hy3Service(settings=mock_settings())
    invalid = json.loads(valid_deep_audit_json())
    invalid["semantic_judgments"][1]["block_id"] = "p99-b999"
    monkeypatch.setattr(
        service,
        "_load_mock_deep_audit_response",
        lambda: json.dumps(invalid),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert "p99-b999" not in (exc_info.value.field_error_summary or "")


def test_mock_deep_audit_uses_same_v2_schema_validation_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Hy3Service(settings=mock_settings())
    invalid = json.loads(valid_deep_audit_json())
    invalid.pop("risk_findings")
    monkeypatch.setattr(
        service,
        "_load_mock_deep_audit_response",
        lambda: json.dumps(invalid),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False


def test_live_deep_audit_uses_one_strict_batch_request() -> None:
    client = FakeClient([valid_deep_audit_json()])
    service = Hy3Service(settings=live_settings(), client=client)

    result = service.deep_audit(
        document=generated_bundle().document,
        claim_evidence_pairs=verified_claim_evidence_pairs()
    )

    assert len(result.semantic_judgments) == 4
    assert len(result.risk_findings) == 3
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    assert request["model"] == "hy3"
    assert request["stream"] is False
    assert request["temperature"] == 0
    assert request["max_completion_tokens"] == 4096
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == DEEP_AUDIT_SCHEMA_NAME
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["type"] == "object"
    assert_closed_objects(response_format["json_schema"]["schema"])
    user_prompt = request["messages"][1]["content"]
    assert "PaperLens fixture - page one" in user_prompt
    assert "PaperLens synthetic fixture explanation" in user_prompt
    assert "In plain terms, this is a short synthetic PDF for parser tests." in user_prompt
    assert "overall_score" not in user_prompt


def test_deep_audit_read_only_observation_counts_retries_and_usage() -> None:
    client = FakeClient(["INVALID_PRIVATE_RESPONSE", valid_deep_audit_json()])
    service = Hy3Service(settings=live_settings(), client=client)

    service.deep_audit(
        document=generated_bundle().document,
        claim_evidence_pairs=verified_claim_evidence_pairs(),
    )

    observation = service.last_run_observation
    assert observation is not None
    assert observation.operation == "deep_audit"
    assert observation.provider_calls == 2
    assert observation.retries == 1
    assert observation.prompt_tokens == 202
    assert observation.completion_tokens == 106
    assert observation.total_tokens == 308
    assert observation.error_code == "NONE"


def test_deep_audit_observation_records_pre_provider_config_failure() -> None:
    service = Hy3Service(
        settings=live_settings(hy3_api_key=""),
        client=FakeClient([valid_deep_audit_json()]),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert exc_info.value.error_code == "HY3_CONFIG_MISSING"
    observation = service.last_run_observation
    assert observation is not None
    assert observation.operation == "deep_audit"
    assert observation.provider_calls == 0
    assert observation.retries == 0
    assert observation.prompt_tokens is None
    assert observation.completion_tokens is None
    assert observation.total_tokens is None
    assert observation.error_code == "HY3_CONFIG_MISSING"


def test_sentence_revision_read_only_observation_has_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_PATCH_ID,
        raising=False,
    )
    bundle = generated_bundle()
    target = bundle.document.sections[0].sentences[0]
    supplier_output = revision_patch_json(
        base_version=3,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_text=target.text,
        after_text=f"{target.text} revised",
    )
    service = Hy3Service(
        settings=live_settings(),
        client=FakeClient([supplier_output]),
    )

    service.revise_sentence(
        base_version=3,
        sentence_id=target.sentence_id,
        current_text=target.text,
        evidence_records=[verified_claim_evidence_pairs()[0][1]],
        user_instruction="Revise only this sentence.",
    )

    observation = service.last_run_observation
    assert observation is not None
    assert observation.operation == "revision"
    assert observation.provider_calls == 1
    assert observation.retries == 0
    assert observation.prompt_tokens == 101
    assert observation.completion_tokens == 53
    assert observation.total_tokens == 154
    assert observation.error_code == "NONE"
    assert set(observation.__dict__) == {
        "operation",
        "provider_calls",
        "retries",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "error_code",
    }


def test_deep_audit_response_format_requires_each_risk_category_exactly_once() -> None:
    response_format = Hy3Service._deep_audit_response_format()
    schema = response_format["json_schema"]["schema"]
    risk_findings = schema["properties"]["risk_findings"]

    assert risk_findings["minItems"] == 3
    assert risk_findings["maxItems"] == 3
    coverage_rules = risk_findings["allOf"]
    assert len(coverage_rules) == 3

    constrained_categories = set()
    for rule in coverage_rules:
        assert rule["minContains"] == 1
        assert rule["maxContains"] == 1
        constrained_finding = rule["contains"]
        assert constrained_finding["type"] == "object"
        assert constrained_finding["additionalProperties"] is False
        assert set(constrained_finding["required"]) == {
            "category",
            "status",
            "reason",
            "locations",
            "remediation",
        }
        constrained_categories.add(
            constrained_finding["properties"]["category"]["const"]
        )

    assert constrained_categories == {
        "sensitive_information",
        "author_impersonation",
        "academic_integrity",
    }


def test_live_deep_audit_schema_error_retries_and_logs_safely(caplog) -> None:
    invalid = "DEEP_AUDIT_PRIVATE_INVALID_RESPONSE"
    client = FakeClient([invalid, valid_deep_audit_json()])
    source_secret = verified_claim_evidence_pairs()[0][1].quote

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = Hy3Service(
            settings=live_settings(),
            client=client,
        ).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert len(result.semantic_judgments) == 4
    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    assert retry_prompt.count("字段错误摘要：") == 1
    assert invalid not in retry_prompt
    assert "prompt_version=audit-v5" in caplog.text
    assert "schema_version=deep-audit-result-v2" in caplog.text
    assert "retries=1" in caplog.text
    assert invalid not in caplog.text
    assert source_secret not in caplog.text
    assert generated_bundle().document.title not in caplog.text
    assert (
        generated_bundle().document.sections[-1].sentences[0].text
        not in caplog.text
    )
    assert "unit-test-key" not in caplog.text


def test_live_deep_audit_extra_field_uses_actionable_safe_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    field_name = "RISK_FINDING_DYNAMIC_FIELD_SENTINEL"
    field_value = "RISK_FINDING_DYNAMIC_VALUE_SENTINEL"
    invalid_raw, valid_raw = risk_only_deep_audit_with_extra_finding_field(
        field_name=field_name,
        field_value=field_value,
    )
    client = FakeClient([invalid_raw, valid_raw])
    service = Hy3Service(settings=live_settings(), client=client)
    validation_inputs: list[Any] = []
    rejected_summaries: list[str] = []
    original_validate = service._validate_deep_audit_result

    def track_validation(
        raw_response: Any,
        expected_pairs: set[tuple[str, str]],
    ) -> DeepAuditResult:
        validation_inputs.append(raw_response)
        try:
            return original_validate(raw_response, expected_pairs)
        except Hy3ServiceError as exc:
            rejected_summaries.append(exc.field_error_summary or "")
            raise

    monkeypatch.setattr(service, "_validate_deep_audit_result", track_validation)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=[],
        )

    expected_message = (
        "RiskFinding objects may contain only category, status, locations, "
        "reason, and remediation. RiskLocation objects may contain only "
        "location_type, sentence_id, and evidence_excerpt; evidence_excerpt "
        "must appear only inside locations."
    )
    assert validation_inputs == [invalid_raw, valid_raw]
    assert len(rejected_summaries) == 1
    safe_summary = rejected_summaries[0]
    assert expected_message in safe_summary
    assert all(
        f"risk_findings.{index}.<extra_field> [extra_forbidden]" in safe_summary
        for index in range(3)
    )
    assert result == DeepAuditResult.model_validate_json(valid_raw)
    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    assert "字段错误摘要：" in retry_prompt
    assert "risk_findings.0.<extra_field>" in retry_prompt
    assert expected_message in retry_prompt
    assert (
        Hy3Service._safe_validation_error_message(
            "extra_forbidden",
            "risk_findings.0.locations.0.<extra_field>",
        )
        == "Extra field is not permitted."
    )
    assert "retries=1" in caplog.text
    for sentinel in (field_name, field_value):
        assert sentinel not in retry_prompt
        assert sentinel not in safe_summary
        assert sentinel not in caplog.text


def test_live_deep_audit_extra_field_exhaustion_stays_failed_without_mock(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    field_name = "RISK_FINDING_EXHAUSTION_FIELD_SENTINEL"
    field_value = "RISK_FINDING_EXHAUSTION_VALUE_SENTINEL"
    invalid_raw, _ = risk_only_deep_audit_with_extra_finding_field(
        field_name=field_name,
        field_value=field_value,
    )
    client = FakeClient([invalid_raw, invalid_raw, invalid_raw])
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_deep_audit_response",
        lambda: pytest.fail("Live schema failure must not load Mock data"),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=[],
            )

    error = exc_info.value
    assert error.error_code == "SCHEMA_INVALID"
    assert error.validation_boundary == "deep_audit_schema_invalid"
    assert error.validation_error_count == 3
    assert error.validation_error_type == "extra_forbidden"
    assert error.validation_location == "risk_findings.0.<extra_field>"
    assert error.retries == 2
    assert len(client.completions.calls) == 3
    assert client.completions.responses == []
    assert all(
        request["max_completion_tokens"] == 4096
        for request in client.completions.calls
    )
    assert "risk_findings.0.<extra_field>" in (error.field_error_summary or "")
    attempts = deep_audit_attempt_logs(caplog)
    assert len(attempts) == 3
    assert all(
        attempt_log_fields(message)["validation_boundary"]
        == "deep_audit_schema_invalid"
        for message in attempts
    )
    assert all(
        attempt_log_fields(message)["validation_error_type"]
        == "extra_forbidden"
        for message in attempts
    )
    assert all(
        attempt_log_fields(message)["validation_location"]
        == "risk_findings.0.<extra_field>"
        for message in attempts
    )
    for request in client.completions.calls[1:]:
        retry_prompt = request["messages"][1]["content"]
        assert "risk_findings.0.<extra_field>" in retry_prompt
    for sentinel in (field_name, field_value):
        assert sentinel not in str(error)
        assert sentinel not in (error.field_error_summary or "")
        assert sentinel not in caplog.text
        assert all(
            sentinel not in request["messages"][1]["content"]
            for request in client.completions.calls
        )


def test_live_deep_audit_retries_schema_valid_semantic_pair_mismatch() -> None:
    invalid = json.loads(valid_deep_audit_json())
    invalid["semantic_judgments"][0]["block_id"] = "p99-b999"
    raw_response = json.dumps(invalid)
    client = FakeClient([raw_response, raw_response, raw_response])

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service(settings=live_settings(), client=client).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert exc_info.value.retries == 2
    assert exc_info.value.usage == (303, 159, 462)
    assert len(client.completions.calls) == 3
    assert "p99-b999" not in (exc_info.value.field_error_summary or "")
    for request in client.completions.calls[1:]:
        retry_prompt = request["messages"][1]["content"]
        assert retry_prompt.count("字段错误摘要：") == 1
        assert "semantic_pair_mismatch" in retry_prompt
        assert "p99-b999" not in retry_prompt


@pytest.mark.parametrize(
    ("variant", "actual_count", "unique_count"),
    [
        ("missing", 2, 2),
        ("duplicate", 3, 2),
        ("extra", 4, 3),
    ],
)
def test_live_deep_audit_retries_invalid_risk_category_coverage(
    variant: str,
    actual_count: int,
    unique_count: int,
    caplog,
) -> None:
    raw_response = deep_audit_with_risk_category_coverage(variant)
    client = FakeClient([raw_response, raw_response, raw_response])
    safe_summary = (
        "risk_findings [risk_category_coverage_invalid]: "
        f"expected_count=3 actual_count={actual_count} "
        f"unique_count={unique_count}"
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=verified_claim_evidence_pairs(),
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert exc_info.value.retries == 2
    assert exc_info.value.usage == (303, 159, 462)
    assert exc_info.value.field_error_summary == safe_summary
    assert len(client.completions.calls) == 3
    for request in client.completions.calls[1:]:
        retry_prompt = request["messages"][1]["content"]
        assert retry_prompt.count("字段错误摘要：") == 1
        assert safe_summary in retry_prompt
    for private_value in (
        "PRIVATE_RISK_REASON_0",
        "PRIVATE_RISK_REASON_1",
        "PRIVATE_RISK_REASON_2",
        "PRIVATE_RISK_REMEDIATION_0",
        "PRIVATE_RISK_REMEDIATION_1",
        "PRIVATE_RISK_REMEDIATION_2",
    ):
        assert private_value not in (exc_info.value.field_error_summary or "")
        assert private_value not in caplog.text


def test_live_risk_only_deep_audit_retries_invalid_categories_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    invalid = json.loads(deep_audit_with_risk_category_coverage("missing"))
    invalid["semantic_judgments"] = []
    valid = json.loads(valid_deep_audit_json())
    valid["semantic_judgments"] = []
    client = FakeClient(
        [
            completion_response(json.dumps(invalid), (11, 7, 18)),
            completion_response(json.dumps(valid), (13, 9, 22)),
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    accepted_results: list[DeepAuditResult] = []
    original_validate = service._validate_deep_audit_result

    def track_valid_result(
        raw_response: Any,
        expected_pairs: set[tuple[str, str]],
    ) -> DeepAuditResult:
        result = original_validate(raw_response, expected_pairs)
        accepted_results.append(result)
        return result

    monkeypatch.setattr(service, "_validate_deep_audit_result", track_valid_result)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=[],
        )

    assert result is accepted_results[-1]
    assert result.semantic_judgments == []
    categories = [finding.category.value for finding in result.risk_findings]
    assert len(categories) == 3
    assert len(set(categories)) == 3
    assert set(categories) == {
        "sensitive_information",
        "author_impersonation",
        "academic_integrity",
    }
    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    assert retry_prompt.count("字段错误摘要：") == 1
    assert (
        "risk_findings [risk_category_coverage_invalid]: "
        "expected_count=3 actual_count=2 unique_count=2"
        in retry_prompt
    )
    assert "PRIVATE_RISK_REASON" not in retry_prompt
    assert "PRIVATE_RISK_REMEDIATION" not in retry_prompt
    assert "prompt_tokens=24" in caplog.text
    assert "completion_tokens=16" in caplog.text
    assert "total_tokens=40" in caplog.text
    assert "retries=1" in caplog.text
    assert "PRIVATE_RISK_REASON" not in caplog.text
    assert "PRIVATE_RISK_REMEDIATION" not in caplog.text


def test_live_deep_audit_empty_pairs_requires_empty_semantic_judgments(
    caplog,
) -> None:
    extra = json.loads(valid_deep_audit_json())
    extra["semantic_judgments"] = [extra["semantic_judgments"][0]]
    risk_only = json.loads(valid_deep_audit_json())
    risk_only["semantic_judgments"] = []
    client = FakeClient(
        [
            completion_response(json.dumps(extra), (11, 7, 18)),
            completion_response(json.dumps(risk_only), (13, 9, 22)),
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = Hy3Service(settings=live_settings(), client=client).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=[],
        )

    assert result.semantic_judgments == []
    assert len(result.risk_findings) == 3
    assert len(client.completions.calls) == 2
    assert "semantic_pair_mismatch" in (
        client.completions.calls[1]["messages"][1]["content"]
    )
    assert "prompt_tokens=24" in caplog.text
    assert "completion_tokens=16" in caplog.text
    assert "total_tokens=40" in caplog.text
    assert "retries=1" in caplog.text


def test_live_deep_audit_provider_failure_stays_failed_without_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([OpenAIError("private provider detail")])
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_deep_audit_response",
        lambda: pytest.fail("Live failure must not load Mock deep-audit data"),
        raising=False,
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs()
        )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert len(client.completions.calls) == 1


def test_deep_audit_rejects_duplicate_input_pair() -> None:
    pairs = verified_claim_evidence_pairs()

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service(settings=mock_settings()).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=[*pairs, pairs[0]]
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False


def test_deep_audit_rejects_unverified_input_evidence() -> None:
    pairs = verified_claim_evidence_pairs()
    claim, evidence = pairs[0]
    unverified = evidence.model_copy(update={"quote_verified": False})

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service(settings=mock_settings()).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=[(claim, unverified)]
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False


def test_live_deep_audit_three_schema_failures_accumulate_usage(caplog) -> None:
    private_values = [
        "DEEP_PRIVATE_INVALID_ONE",
        "DEEP_PRIVATE_INVALID_TWO",
        "DEEP_PRIVATE_INVALID_THREE",
    ]
    client = FakeClient(
        [
            completion_response(private_values[0], (11, 7, 18)),
            completion_response(private_values[1], (13, 9, 22)),
            completion_response(private_values[2], (17, 11, 28)),
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=verified_claim_evidence_pairs()
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False
    assert exc_info.value.retries == 2
    assert exc_info.value.usage == (41, 27, 68)
    assert len(client.completions.calls) == 3
    assert "prompt_tokens=41" in caplog.text
    assert "completion_tokens=27" in caplog.text
    assert "total_tokens=68" in caplog.text
    assert all(value not in caplog.text for value in private_values)
    attempt_logs = deep_audit_attempt_logs(caplog)
    assert len(attempt_logs) == 3
    assert [attempt_log_fields(log)["attempt"] for log in attempt_logs] == [
        "0",
        "1",
        "2",
    ]
    assert all(
        attempt_log_fields(log)["retry_count"]
        == attempt_log_fields(log)["attempt"]
        for log in attempt_logs
    )


def test_live_deep_audit_provider_failure_preserves_prior_usage(caplog) -> None:
    private_values = ["DEEP_PRIVATE_FIRST", "DEEP_PRIVATE_SECOND"]
    client = FakeClient(
        [
            completion_response(private_values[0], (13, 5, 18)),
            completion_response(private_values[1], (17, 7, 24)),
            OpenAIError("private provider detail after schema retries"),
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=verified_claim_evidence_pairs()
            )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert exc_info.value.retries == 2
    assert exc_info.value.usage == (30, 12, 42)
    assert len(client.completions.calls) == 3
    assert "prompt_tokens=30" in caplog.text
    assert "completion_tokens=12" in caplog.text
    assert "total_tokens=42" in caplog.text
    assert "error_code=HY3_UNAVAILABLE" in caplog.text
    assert all(value not in caplog.text for value in private_values)


@pytest.mark.parametrize("variant", ["missing", "extra", "illegal_enum"])
def test_live_deep_audit_v2_schema_errors_retry_twice(
    variant: str,
) -> None:
    invalid = json.loads(valid_deep_audit_json())
    if variant == "missing":
        invalid.pop("risk_findings")
    elif variant == "extra":
        invalid["unexpected"] = "PRIVATE_SCHEMA_VALUE"
    else:
        invalid["risk_findings"][0]["status"] = "invalid_status"
    raw = json.dumps(invalid)
    client = FakeClient([raw, raw, raw])

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service(settings=live_settings(), client=client).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retries == 2
    assert len(client.completions.calls) == 3


def test_live_deep_audit_with_empty_pairs_still_checks_complete_document() -> None:
    payload = json.loads(valid_deep_audit_json())
    payload["semantic_judgments"] = []
    client = FakeClient([json.dumps(payload)])
    document = generated_bundle().document

    result = Hy3Service(settings=live_settings(), client=client).deep_audit(
        document=document,
        claim_evidence_pairs=[],
    )

    assert result.semantic_judgments == []
    assert len(result.risk_findings) == 3
    assert len(client.completions.calls) == 1
    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert document.title in prompt
    assert document.sections[-1].sentences[0].text in prompt
    assert "items: []" in prompt


def test_live_deep_audit_missing_key_does_not_call_provider_or_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([valid_deep_audit_json()])
    service = Hy3Service(
        settings=live_settings(hy3_api_key=""),
        client=client,
    )
    monkeypatch.setattr(
        service,
        "_load_mock_deep_audit_response",
        lambda: pytest.fail("Live config failure must not load Mock data"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert exc_info.value.error_code == "HY3_CONFIG_MISSING"
    assert exc_info.value.retryable is False
    assert client.completions.calls == []


def test_mock_generation_returns_valid_generated_bundle() -> None:
    bundle = Hy3Service(settings=mock_settings()).generate(
        claim_policy="required",
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks(),
    )

    assert isinstance(bundle, GeneratedBundle)
    assert [section.section_id for section in bundle.document.sections] == list(
        SectionId
    )
    assert bundle.claims[0].candidate_block_ids == ["p01-b001"]


@pytest.mark.parametrize(
    ("claim_policy", "claims_present"),
    [("required", True), ("must_be_empty", False)],
)
def test_live_generation_claim_policy_returns_legal_bundle_unchanged(
    claim_policy: str,
    claims_present: bool,
) -> None:
    raw = generation_json_with_claims(present=claims_present)
    client = FakeClient([raw])

    bundle = Hy3Service(settings=live_settings(), client=client).generate(
        claim_policy=claim_policy,
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:2],
    )

    assert bundle == GeneratedBundle.model_validate_json(raw)
    assert bool(bundle.claims) is claims_present
    assert len(client.completions.calls) == 1
    assert (
        f"claim_policy: {claim_policy}"
        in client.completions.calls[0]["messages"][1]["content"]
    )


@pytest.mark.parametrize(
    ("claim_policy", "claims_present"),
    [("required", True), ("must_be_empty", False)],
)
def test_saved_generated_bundle_uses_same_claim_policy_validation_entry(
    claim_policy: str,
    claims_present: bool,
) -> None:
    bundle = GeneratedBundle.model_validate_json(
        generation_json_with_claims(present=claims_present)
    )

    validated = Hy3Service.validate_claim_policy(bundle, claim_policy)

    assert validated is bundle


@pytest.mark.parametrize(
    ("claim_policy", "claims_present", "expected_summary"),
    [
        (
            "required",
            False,
            "claims [claim_policy_mismatch]: claim_policy=required "
            "expected_count=at_least_1 actual_count=0",
        ),
        (
            "must_be_empty",
            True,
            "claims [claim_policy_mismatch]: claim_policy=must_be_empty "
            "expected_count=0 actual_count=5",
        ),
    ],
)
def test_live_generation_claim_policy_mismatch_uses_bounded_retry_and_safe_summary(
    claim_policy: str,
    claims_present: bool,
    expected_summary: str,
    caplog,
) -> None:
    raw = generation_json_with_claims(present=claims_present)
    client = FakeClient([raw, raw, raw])

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).generate(
                claim_policy=claim_policy,
                paper_metadata={"title": "PaperLens synthetic fixture"},
                source_blocks=load_source_blocks()[:2],
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    assert exc_info.value.retryable is True
    assert exc_info.value.retries == 2
    assert exc_info.value.field_error_summary == expected_summary
    assert len(client.completions.calls) == 3
    for request in client.completions.calls[1:]:
        retry_prompt = request["messages"][1]["content"]
        assert expected_summary in retry_prompt
        assert retry_prompt.count("字段错误摘要：") == 1
    private_claim_text = generated_bundle().claims[0].text
    assert private_claim_text not in (exc_info.value.field_error_summary or "")
    assert private_claim_text not in caplog.text


@pytest.mark.parametrize(
    ("claim_policy", "first_has_claims", "second_has_claims"),
    [("required", False, True), ("must_be_empty", True, False)],
)
def test_live_generation_claim_policy_recovers_without_mutating_supplier_output(
    claim_policy: str,
    first_has_claims: bool,
    second_has_claims: bool,
) -> None:
    valid_raw = generation_json_with_claims(present=second_has_claims)
    client = FakeClient(
        [
            generation_json_with_claims(present=first_has_claims),
            valid_raw,
        ]
    )

    bundle = Hy3Service(settings=live_settings(), client=client).generate(
        claim_policy=claim_policy,
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:2],
    )

    assert bundle == GeneratedBundle.model_validate_json(valid_raw)
    assert len(client.completions.calls) == 2


def test_live_generation_required_accumulates_cross_attempt_contract_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    schema_invalid = json.loads(valid_generation_json())
    schema_invalid["unexpected"] = {
        "raw": "RAW_RESPONSE_SENTINEL",
        "input": "PYDANTIC_INPUT_SENTINEL",
    }
    policy_invalid = generation_json_with_claims(present=False)
    valid_raw = valid_generation_json()
    source_blocks = load_source_blocks()[:2]
    source_blocks[0] = source_blocks[0].model_copy(
        update={"text": "SOURCE_BLOCK_SENTINEL"}
    )
    client = FakeClient(
        [json.dumps(schema_invalid), policy_invalid, valid_raw]
    )
    service = Hy3Service(
        settings=live_settings(hy3_api_key="API_KEY_SENTINEL"),
        client=client,
    )
    accepted_bundles: list[GeneratedBundle] = []
    original_validate = service._validate_generated_bundle

    def track_valid_bundle(
        raw_response: Any,
        claim_policy: str,
    ) -> GeneratedBundle:
        bundle = original_validate(raw_response, claim_policy)
        accepted_bundles.append(bundle)
        return bundle

    monkeypatch.setattr(service, "_validate_generated_bundle", track_valid_bundle)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.generate(
            claim_policy="required",
            paper_metadata={"title": "PROMPT_SENTINEL"},
            source_blocks=source_blocks,
        )

    assert result is accepted_bundles[-1]
    assert result == GeneratedBundle.model_validate_json(valid_raw)
    assert len(client.completions.calls) == 3
    prompts = [call["messages"][1]["content"] for call in client.completions.calls]
    assert "字段错误摘要：" not in prompts[0]
    attempt_one_summary = generation_retry_summary(prompts[1])
    attempt_two_summary = generation_retry_summary(prompts[2])
    assert "<extra_field> [extra_forbidden]" in attempt_one_summary
    assert "claim_policy_mismatch" not in attempt_one_summary
    assert "<extra_field> [extra_forbidden]" in attempt_two_summary
    assert (
        "claims [claim_policy_mismatch]: claim_policy=required "
        "expected_count=at_least_1 actual_count=0"
        in attempt_two_summary
    )
    assert "attempt=0" in attempt_one_summary
    assert "attempt=0" in attempt_two_summary
    assert "attempt=1" in attempt_two_summary
    assert attempt_two_summary.index("attempt=0") < attempt_two_summary.index(
        "attempt=1"
    )
    assert "必须同时修复" in attempt_two_summary
    assert "重新违反先前约束" in attempt_two_summary
    assert "retries=2" in caplog.text
    for sentinel in (
        "PROMPT_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "SOURCE_BLOCK_SENTINEL",
        "CLAIM_TEXT_SENTINEL",
        "API_KEY_SENTINEL",
        "PYDANTIC_INPUT_SENTINEL",
    ):
        assert sentinel not in attempt_one_summary
        assert sentinel not in attempt_two_summary
        assert sentinel not in caplog.text


def test_live_generation_must_be_empty_accumulates_cross_attempt_contract_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    schema_invalid = json.loads(valid_generation_json())
    schema_invalid["unexpected"] = "RAW_RESPONSE_SENTINEL"
    policy_invalid = json.loads(valid_generation_json())
    policy_invalid["claims"][0]["text"] = "CLAIM_TEXT_SENTINEL"
    valid_raw = generation_json_with_claims(present=False)
    client = FakeClient(
        [
            json.dumps(schema_invalid),
            json.dumps(policy_invalid),
            valid_raw,
        ]
    )
    service = Hy3Service(
        settings=live_settings(hy3_api_key="API_KEY_SENTINEL"),
        client=client,
    )
    accepted_bundles: list[GeneratedBundle] = []
    original_validate = service._validate_generated_bundle

    def track_valid_bundle(
        raw_response: Any,
        claim_policy: str,
    ) -> GeneratedBundle:
        bundle = original_validate(raw_response, claim_policy)
        accepted_bundles.append(bundle)
        return bundle

    monkeypatch.setattr(service, "_validate_generated_bundle", track_valid_bundle)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.generate(
            claim_policy="must_be_empty",
            paper_metadata={"title": "PROMPT_SENTINEL"},
            source_blocks=load_source_blocks()[:2],
        )

    assert result is accepted_bundles[-1]
    assert result == GeneratedBundle.model_validate_json(valid_raw)
    assert result.claims == []
    assert len(client.completions.calls) == 3
    prompts = [call["messages"][1]["content"] for call in client.completions.calls]
    assert "字段错误摘要：" not in prompts[0]
    attempt_one_summary = generation_retry_summary(prompts[1])
    attempt_two_summary = generation_retry_summary(prompts[2])
    assert "<extra_field> [extra_forbidden]" in attempt_one_summary
    assert "claim_policy_mismatch" not in attempt_one_summary
    assert "<extra_field> [extra_forbidden]" in attempt_two_summary
    assert (
        "claims [claim_policy_mismatch]: claim_policy=must_be_empty "
        "expected_count=0 actual_count=5"
        in attempt_two_summary
    )
    assert "attempt=0" in attempt_one_summary
    assert "attempt=0" in attempt_two_summary
    assert "attempt=1" in attempt_two_summary
    assert attempt_two_summary.index("attempt=0") < attempt_two_summary.index(
        "attempt=1"
    )
    assert "必须同时修复" in attempt_two_summary
    assert "重新违反先前约束" in attempt_two_summary
    assert "retries=2" in caplog.text
    for sentinel in (
        "PROMPT_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "SOURCE_BLOCK_SENTINEL",
        "CLAIM_TEXT_SENTINEL",
        "API_KEY_SENTINEL",
        "PYDANTIC_INPUT_SENTINEL",
    ):
        assert sentinel not in attempt_one_summary
        assert sentinel not in attempt_two_summary
        assert sentinel not in caplog.text


def test_live_generation_cross_attempt_retry_exhaustion_preserves_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_schema_invalid = json.loads(valid_generation_json())
    first_schema_invalid["first_unexpected"] = "FIRST_RAW_SENTINEL"
    last_schema_invalid = json.loads(valid_generation_json())
    last_schema_invalid["last_unexpected"] = "LAST_RAW_SENTINEL"
    client = FakeClient(
        [
            json.dumps(first_schema_invalid),
            generation_json_with_claims(present=False),
            json.dumps(last_schema_invalid),
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Live retry exhaustion must not load Mock data"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.generate(
            claim_policy="required",
            paper_metadata={"title": "Synthetic"},
            source_blocks=load_source_blocks()[:2],
        )

    assert len(client.completions.calls) == 3
    assert client.completions.responses == []
    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.validation_boundary == "generated_bundle_schema_invalid"
    assert exc_info.value.retries == 2
    assert "<extra_field> [extra_forbidden]" in (
        exc_info.value.field_error_summary or ""
    )
    assert "last_unexpected" not in (exc_info.value.field_error_summary or "")
    assert "FIRST_RAW_SENTINEL" not in (exc_info.value.field_error_summary or "")
    assert "LAST_RAW_SENTINEL" not in (exc_info.value.field_error_summary or "")


def test_live_generation_b_only_cross_field_sequence_uses_actionable_safe_retries(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    document_invalid = json.loads(valid_generation_json())
    document_invalid["document"]["sections"][-1]["section_id"] = "limitations"
    document_invalid["claims"][0]["candidate_block_ids"] = []
    document_invalid["claims"][1]["candidate_quote"] = ""

    claim_invalid = json.loads(valid_generation_json())
    base_claim = claim_invalid["claims"][0]
    claim_invalid["claims"] = []
    for index in range(47):
        claim = dict(base_claim)
        claim["claim_id"] = f"c-live-{index:03d}"
        claim_invalid["claims"].append(claim)
    claim_invalid["claims"][45]["candidate_block_ids"] = []
    claim_invalid["claims"][46]["candidate_quote"] = ""

    client = FakeClient(
        [
            completion_response(
                json.dumps(document_invalid),
                (100, 13700, 13800),
                finish_reason="stop",
            ),
            completion_response(
                json.dumps(claim_invalid),
                (100, 10228, 10328),
                finish_reason="stop",
            ),
            completion_response(
                '{"document":',
                (100, 16384, 16484),
                finish_reason="length",
            ),
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Live retry exhaustion must not load Mock data"),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.generate(
                claim_policy="required",
                paper_metadata={"title": "Synthetic"},
                source_blocks=load_source_blocks()[:2],
            )

    assert GENERATION_PROMPT_VERSION == "gen-v4"
    assert len(client.completions.calls) == 3
    assert client.completions.responses == []
    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.validation_boundary == "json_invalid"
    assert exc_info.value.retries == 2

    prompts = [call["messages"][1]["content"] for call in client.completions.calls]
    first_retry = generation_retry_summary(prompts[1])
    last_retry = generation_retry_summary(prompts[2])
    assert "attempt=0" in first_retry
    assert "document [value_error]" in first_retry
    assert "each required section exactly once" in first_retry
    assert "globally unique sentence identifiers" in first_retry
    assert "attempt=0" in last_retry
    assert "attempt=1" in last_retry
    assert last_retry.index("attempt=0") < last_retry.index("attempt=1")
    assert "claims.45 [value_error]" in last_retry
    assert "Auditable claims require 1 to 3 candidate block identifiers" in last_retry
    assert "candidate quotes must be null or non-empty" in last_retry

    attempts = generation_attempt_logs(caplog)
    assert len(attempts) == 3
    fields = [attempt_log_fields(message) for message in attempts]
    assert [item["attempt"] for item in fields] == ["0", "1", "2"]
    assert [item["completion_tokens"] for item in fields] == ["13700", "10228", "16384"]
    assert [item["finish_reason"] for item in fields] == ["stop", "stop", "length"]
    assert [item["completion_limit_reached"] for item in fields] == [
        "false",
        "false",
        "true",
    ]
    assert [item["validation_boundary"] for item in fields] == [
        "generated_bundle_schema_invalid",
        "generated_bundle_schema_invalid",
        "json_invalid",
    ]
    assert [item["error_code"] for item in fields] == [
        "SCHEMA_INVALID",
        "SCHEMA_INVALID",
        "SCHEMA_INVALID",
    ]
    assert [item["validation_error_count"] for item in fields] == ["3", "2", "1"]
    assert [item["validation_error_type"] for item in fields] == [
        "value_error",
        "value_error",
        "json_invalid",
    ]
    assert [item["validation_location"] for item in fields] == [
        "document",
        "claims.45",
        "$",
    ]


def test_generated_bundle_validation_summary_excludes_dynamic_validator_value() -> None:
    sentence_id_sentinel = "UNKNOWN_SENTENCE_ID_SENTINEL"
    invalid = json.loads(valid_generation_json())
    invalid["claims"][0]["sentence_id"] = sentence_id_sentinel

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service._validate_generated_bundle(
            json.dumps(invalid),
            "required",
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert (
        exc_info.value.validation_boundary
        == "generated_bundle_schema_invalid"
    )
    safe_summary = exc_info.value.field_error_summary or ""
    assert sentence_id_sentinel not in safe_summary
    assert "$ [value_error]: Value failed validation." in safe_summary


def test_live_generation_retry_summary_excludes_dynamic_validator_value(
    caplog: Any,
) -> None:
    sentence_id_sentinel = "UNKNOWN_SENTENCE_ID_SENTINEL"
    invalid = json.loads(valid_generation_json())
    invalid["claims"][0]["sentence_id"] = sentence_id_sentinel
    valid_raw = valid_generation_json()
    client = FakeClient([json.dumps(invalid), valid_raw])

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = Hy3Service(
            settings=live_settings(),
            client=client,
        ).generate(
            claim_policy="required",
            paper_metadata={"title": "Synthetic"},
            source_blocks=load_source_blocks()[:2],
        )

    assert result == GeneratedBundle.model_validate_json(valid_raw)
    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    retry_summary = generation_retry_summary(retry_prompt)
    assert sentence_id_sentinel not in retry_summary
    assert sentence_id_sentinel not in caplog.text
    assert "$ [value_error]: Value failed validation." in retry_summary


def test_generated_bundle_top_level_extra_key_uses_safe_location() -> None:
    extra_key_sentinel = "TOP_LEVEL_EXTRA_KEY_SENTINEL"
    invalid = json.loads(valid_generation_json())
    invalid[extra_key_sentinel] = "PRIVATE_EXTRA_VALUE"

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service._validate_generated_bundle(
            json.dumps(invalid),
            "required",
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert (
        exc_info.value.validation_boundary
        == "generated_bundle_schema_invalid"
    )
    safe_summary = exc_info.value.field_error_summary or ""
    assert extra_key_sentinel not in safe_summary
    assert (
        "<extra_field> [extra_forbidden]: Extra field is not permitted."
        in safe_summary
    )


def test_generated_bundle_nested_extra_key_preserves_safe_parent_location() -> None:
    extra_key_sentinel = "NESTED_EXTRA_KEY_SENTINEL"
    invalid = json.loads(valid_generation_json())
    invalid["document"]["sections"][0]["sentences"][0][
        extra_key_sentinel
    ] = "PRIVATE_NESTED_EXTRA_VALUE"

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service._validate_generated_bundle(
            json.dumps(invalid),
            "required",
        )

    safe_summary = exc_info.value.field_error_summary or ""
    assert extra_key_sentinel not in safe_summary
    assert (
        "document.sections.0.sentences.0.<extra_field> "
        "[extra_forbidden]: Extra field is not permitted."
        in safe_summary
    )


def test_live_generation_retry_prompt_excludes_dynamic_extra_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    extra_key_sentinel = "TOP_LEVEL_EXTRA_KEY_SENTINEL"
    invalid = json.loads(valid_generation_json())
    invalid[extra_key_sentinel] = "PRIVATE_EXTRA_VALUE"
    valid_raw = valid_generation_json()
    client = FakeClient([json.dumps(invalid), valid_raw])
    service = Hy3Service(settings=live_settings(), client=client)
    accepted_bundles: list[GeneratedBundle] = []
    original_validate = service._validate_generated_bundle

    def track_valid_bundle(
        raw_response: Any,
        claim_policy: str,
    ) -> GeneratedBundle:
        bundle = original_validate(raw_response, claim_policy)
        accepted_bundles.append(bundle)
        return bundle

    monkeypatch.setattr(service, "_validate_generated_bundle", track_valid_bundle)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.generate(
            claim_policy="required",
            paper_metadata={"title": "Synthetic"},
            source_blocks=load_source_blocks()[:2],
        )

    assert result is accepted_bundles[-1]
    assert result == GeneratedBundle.model_validate_json(valid_raw)
    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    retry_summary = generation_retry_summary(retry_prompt)
    assert extra_key_sentinel not in retry_summary
    assert extra_key_sentinel not in caplog.text
    assert "<extra_field> [extra_forbidden]" in retry_summary


def test_safe_validation_location_does_not_stringify_abnormal_segment() -> None:
    class UnsafeLocationSegment:
        def __str__(self) -> str:
            raise AssertionError("unsafe location segment must not be stringified")

    assert Hy3Service._safe_validation_location(
        ("document", UnsafeLocationSegment()),
        "value_error",
    ) == "document.<location_segment>"
    assert Hy3Service._safe_validation_location(
        UnsafeLocationSegment(),
        "value_error",
    ) == "<location>"


def test_mock_invalid_fixture_uses_generated_bundle_validation_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Hy3Service(settings=mock_settings())
    invalid_response = (FIXTURES / "generation_invalid_schema.json").read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(service, "_load_mock_response", lambda: invalid_response)

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=load_source_blocks(),
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False


def test_mock_unknown_candidate_block_remains_unverified_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Hy3Service(settings=mock_settings())
    candidate_only_response = (
        FIXTURES / "generation_invalid_block.json"
    ).read_text(encoding="utf-8")
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: candidate_only_response,
    )

    bundle = service.generate(
        claim_policy="required",
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks(),
    )

    assert bundle.claims[0].candidate_block_ids == ["p99-b999"]
    assert not hasattr(bundle.claims[0], "quote_verified")


def test_live_generation_uses_openai_chat_and_strict_generated_bundle_schema() -> None:
    client = FakeClient([valid_generation_json()])
    service = Hy3Service(settings=live_settings(), client=client)

    bundle = service.generate(
        claim_policy="required",
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:3],
    )

    assert isinstance(bundle, GeneratedBundle)
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    assert request["model"] == "hy3"
    assert request["stream"] is False
    assert request["temperature"] == 0
    assert request["max_completion_tokens"] == 16384
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["messages"][0] == {
        "role": "system",
        "content": COMMON_SYSTEM_PROMPT,
    }
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == GENERATION_SCHEMA_NAME
    assert response_format["json_schema"]["strict"] is True
    assert_closed_objects(response_format["json_schema"]["schema"])


@pytest.mark.parametrize(
    ("import_order", "log_level", "expected_attempt_logs"),
    [
        ("configure_then_import", "info", 1),
        ("import_then_configure", "info", 1),
        ("configure_then_import", "warning", 0),
        ("import_then_configure", "warning", 0),
    ],
    ids=[
        "info-configure-then-import",
        "info-import-then-configure",
        "warning-configure-then-import",
        "warning-import-then-configure",
    ],
)
def test_generation_diagnostic_respects_uvicorn_logging_order(
    import_order: str,
    log_level: str,
    expected_attempt_logs: int,
    tmp_path: Path,
) -> None:
    probe = """
import sys

from uvicorn import Config


def configure_logging() -> None:
    Config("paperlens.logging_probe:app", log_level=sys.argv[2]).configure_logging()


if sys.argv[1] == "configure_then_import":
    configure_logging()
    from backend.app.hy3_service import Hy3Service
elif sys.argv[1] == "import_then_configure":
    from backend.app.hy3_service import Hy3Service
    configure_logging()
else:
    raise AssertionError("unknown import order")

Hy3Service._log_generation_attempt(
    attempt=2,
    completion_tokens=4096,
    finish_reason="length",
    validation_boundary="json_invalid",
    error_code="SCHEMA_INVALID",
)
"""
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTHONUTF8": "1",
    }
    for name in ("SYSTEMROOT", "TEMP", "TMP", "WINDIR"):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value

    result = subprocess.run(
        [sys.executable, "-c", probe, import_order, log_level],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, (
        f"probe returncode={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    marker = "hy3_generation_attempt operation=generation "
    assert output.count(marker) == expected_attempt_logs
    for sentinel in (
        "PROMPT_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "SOURCE_BLOCK_SENTINEL",
        "CLAIM_TEXT_SENTINEL",
        "API_KEY_SENTINEL",
    ):
        assert sentinel not in output


def test_live_generation_provider_failure_uses_closed_attempt_boundary(
    caplog: Any,
) -> None:
    client = FakeClient([OpenAIError("RAW_RESPONSE_SENTINEL")])

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(
                settings=live_settings(hy3_max_retries=0),
                client=client,
            ).generate(
                claim_policy="required",
                paper_metadata={"title": "PROMPT_SENTINEL"},
                source_blocks=load_source_blocks()[:2],
            )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    logs = generation_attempt_logs(caplog)
    assert len(logs) == 1
    assert_safe_attempt_log_fields(
        logs[0],
        operation="generation",
        boundary="provider_unavailable",
        error_code="HY3_UNAVAILABLE",
    )
    assert "RAW_RESPONSE_SENTINEL" not in caplog.text
    assert "PROMPT_SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    ("variant", "expected_boundary", "expected_error_code"),
    [
        ("content_missing", "content_missing", "SCHEMA_INVALID"),
        ("json_invalid", "json_invalid", "SCHEMA_INVALID"),
        (
            "schema_invalid",
            "deep_audit_schema_invalid",
            "SCHEMA_INVALID",
        ),
        ("semantic_pair", "semantic_pair_invalid", "AUDIT_INCOMPLETE"),
        (
            "risk_category",
            "risk_category_coverage_invalid",
            "AUDIT_INCOMPLETE",
        ),
        ("valid", "none", "NONE"),
    ],
)
def test_live_deep_audit_emits_one_closed_diagnostic_per_attempt(
    variant: str,
    expected_boundary: str,
    expected_error_code: str,
    caplog: Any,
) -> None:
    payload = json.loads(valid_deep_audit_json())
    if variant == "content_missing":
        raw_response: Any = None
    elif variant == "json_invalid":
        raw_response = "RAW_RESPONSE_SENTINEL"
    elif variant == "schema_invalid":
        payload.pop("risk_findings")
        raw_response = json.dumps(payload)
    elif variant == "semantic_pair":
        payload["semantic_judgments"][0]["block_id"] = "CLAIM_TEXT_SENTINEL"
        raw_response = json.dumps(payload)
    elif variant == "risk_category":
        payload["risk_findings"] = payload["risk_findings"][:2]
        raw_response = json.dumps(payload)
    else:
        raw_response = json.dumps(payload)
    client = FakeClient(
        [completion_response(raw_response, (11, 7, 18), finish_reason="stop")]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        if expected_error_code == "NONE":
            result = Hy3Service(
                settings=live_settings(hy3_max_retries=0),
                client=client,
            ).deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=verified_claim_evidence_pairs(),
            )
            assert isinstance(result, DeepAuditResult)
        else:
            with pytest.raises(Hy3ServiceError) as exc_info:
                Hy3Service(
                    settings=live_settings(hy3_max_retries=0),
                    client=client,
                ).deep_audit(
                    document=generated_bundle().document,
                    claim_evidence_pairs=verified_claim_evidence_pairs(),
                )
            assert exc_info.value.error_code == expected_error_code

    logs = deep_audit_attempt_logs(caplog)
    assert len(logs) == 1
    assert_safe_attempt_log_fields(
        logs[0],
        operation="deep_audit",
        boundary=expected_boundary,
        error_code=expected_error_code,
    )
    assert "configured_completion_limit=4096" in logs[0]
    assert "attempt=0" in logs[0]
    assert "retry_count=0" in logs[0]
    fields = attempt_log_fields(logs[0])
    if variant == "json_invalid":
        assert fields["validation_error_count"] == "1"
        assert fields["validation_error_type"] == "json_invalid"
        assert fields["validation_location"] == "$"
    elif variant == "schema_invalid":
        assert fields["validation_error_count"] == "1"
        assert fields["validation_error_type"] == "missing"
        assert fields["validation_location"] == "risk_findings"
    elif variant in {"semantic_pair", "risk_category", "content_missing"}:
        assert fields["validation_error_count"] == "0"
        assert fields["validation_error_type"] == "none"
        assert fields["validation_location"] == "none"
    for sentinel in (
        "RAW_RESPONSE_SENTINEL",
        "CLAIM_TEXT_SENTINEL",
        "PROMPT_SENTINEL",
        "SOURCE_BLOCK_SENTINEL",
        "API_KEY_SENTINEL",
    ):
        assert sentinel not in caplog.text


def test_live_deep_audit_provider_failure_uses_closed_attempt_boundary(
    caplog: Any,
) -> None:
    client = FakeClient([OpenAIError("RAW_RESPONSE_SENTINEL")])

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(
                settings=live_settings(hy3_max_retries=0),
                client=client,
            ).deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=verified_claim_evidence_pairs(),
            )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    logs = deep_audit_attempt_logs(caplog)
    assert len(logs) == 1
    assert_safe_attempt_log_fields(
        logs[0],
        operation="deep_audit",
        boundary="provider_unavailable",
        error_code="HY3_UNAVAILABLE",
    )
    assert "RAW_RESPONSE_SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    ("variant", "expected_type", "expected_location"),
    [
        ("extra", "extra_forbidden", "<extra_field>"),
        ("unknown_type", "other_validation_error", "document.title"),
    ],
)
def test_generation_diagnostic_redacts_validation_type_and_location(
    variant: str,
    expected_type: str,
    expected_location: str,
    caplog: Any,
) -> None:
    payload = json.loads(valid_generation_json())
    if variant == "extra":
        payload["PYDANTIC_RAW_FIELD_SENTINEL"] = "RAW_RESPONSE_SENTINEL"
    else:
        payload["document"]["title"] = ""
    client = FakeClient(
        [
            completion_response(
                json.dumps(payload),
                (11, 7, 18),
                finish_reason="stop",
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError):
            Hy3Service(
                settings=live_settings(hy3_max_retries=0),
                client=client,
            ).generate(
                claim_policy="required",
                paper_metadata={"title": "PROMPT_SENTINEL"},
                source_blocks=load_source_blocks()[:2],
            )

    logs = generation_attempt_logs(caplog)
    assert len(logs) == 1
    fields = attempt_log_fields(logs[0])
    assert fields["validation_error_count"] == "1"
    assert fields["validation_error_type"] == expected_type
    assert fields["validation_location"] == expected_location
    for sentinel in (
        "PYDANTIC_RAW_FIELD_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "PROMPT_SENTINEL",
    ):
        assert sentinel not in caplog.text


def test_generation_diagnostic_bounds_final_rendered_long_location(
    caplog: Any,
) -> None:
    long_location = "LONG_GENERATION_LOCATION_SENTINEL_" * 30
    assert len(long_location) >= 900

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        Hy3Service._log_generation_attempt(
            attempt=2,
            completion_tokens=16384,
            finish_reason="length",
            validation_boundary="json_invalid",
            error_code="SCHEMA_INVALID",
            validation_error_count=1,
            validation_error_type="json_invalid",
            validation_location=long_location,
        )

    logs = generation_attempt_logs(caplog)
    assert len(logs) == 1
    assert_safe_attempt_log_fields(
        logs[0],
        operation="generation",
        boundary="json_invalid",
        error_code="SCHEMA_INVALID",
    )
    fields = attempt_log_fields(logs[0])
    assert fields["attempt"] == "2"
    assert fields["retry_count"] == "2"
    assert fields["validation_location"].endswith("<truncated>")
    assert long_location not in logs[0]
    assert len(logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH


def test_deep_audit_diagnostic_bounds_final_rendered_long_location(
    caplog: Any,
) -> None:
    long_location = "LONG_DEEP_AUDIT_LOCATION_SENTINEL_" * 30
    assert len(long_location) >= 900

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        Hy3Service._log_deep_audit_attempt(
            attempt=2,
            completion_tokens=4096,
            finish_reason="length",
            validation_boundary="deep_audit_schema_invalid",
            error_code="SCHEMA_INVALID",
            validation_error_count=1,
            validation_error_type="other_validation_error",
            validation_location=long_location,
        )

    logs = deep_audit_attempt_logs(caplog)
    assert len(logs) == 1
    assert_safe_attempt_log_fields(
        logs[0],
        operation="deep_audit",
        boundary="deep_audit_schema_invalid",
        error_code="SCHEMA_INVALID",
    )
    fields = attempt_log_fields(logs[0])
    assert fields["attempt"] == "2"
    assert fields["retry_count"] == "2"
    assert fields["validation_location"].endswith("<truncated>")
    assert long_location not in logs[0]
    assert len(logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH


def test_live_generation_huge_usage_does_not_mask_valid_bundle(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    huge_count = 10**800
    huge_count_text = str(huge_count)
    client = FakeClient(
        [
            completion_response(
                valid_generation_json(),
                (11, huge_count, huge_count + 11),
                finish_reason="length",
            )
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    accepted: list[GeneratedBundle] = []
    original_validator = service._validate_generated_bundle

    def track_valid_bundle(
        raw_response: Any,
        claim_policy: str,
    ) -> GeneratedBundle:
        bundle = original_validator(raw_response, claim_policy)  # type: ignore[arg-type]
        accepted.append(bundle)
        return bundle

    monkeypatch.setattr(service, "_validate_generated_bundle", track_valid_bundle)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=load_source_blocks()[:2],
        )

    assert result is accepted[0]
    attempt_logs = generation_attempt_logs(caplog)
    assert len(attempt_logs) == 1
    assert_safe_attempt_log_fields(
        attempt_logs[0],
        operation="generation",
        boundary="none",
        error_code="NONE",
    )
    assert attempt_log_fields(attempt_logs[0])["completion_tokens"] == "null"
    run_logs = hy3_run_logs(caplog)
    assert len(run_logs) == 1
    assert len(run_logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH
    assert huge_count_text not in caplog.text


def test_live_generation_huge_usage_preserves_schema_error(caplog: Any) -> None:
    huge_count = 10**800
    huge_count_text = str(huge_count)
    client = FakeClient(
        [
            completion_response(
                "RAW_JSON_INVALID_SENTINEL",
                (11, huge_count, huge_count + 11),
                finish_reason="length",
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(
                settings=live_settings(hy3_max_retries=0),
                client=client,
            ).generate(
                claim_policy="required",
                paper_metadata={"title": "PaperLens synthetic fixture"},
                source_blocks=load_source_blocks()[:2],
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False
    attempt_logs = generation_attempt_logs(caplog)
    assert len(attempt_logs) == 1
    assert_safe_attempt_log_fields(
        attempt_logs[0],
        operation="generation",
        boundary="json_invalid",
        error_code="SCHEMA_INVALID",
    )
    assert attempt_log_fields(attempt_logs[0])["completion_tokens"] == "null"
    run_logs = hy3_run_logs(caplog)
    assert len(run_logs) == 1
    assert len(run_logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH
    assert huge_count_text not in caplog.text
    assert "RAW_JSON_INVALID_SENTINEL" not in caplog.text


def test_live_deep_audit_huge_usage_does_not_mask_valid_result(
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
) -> None:
    huge_count = 10**800
    huge_count_text = str(huge_count)
    client = FakeClient(
        [
            completion_response(
                valid_deep_audit_json(),
                (17, huge_count, huge_count + 17),
                finish_reason="length",
            )
        ]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    accepted: list[DeepAuditResult] = []
    original_validator = service._validate_deep_audit_result

    def track_valid_result(
        raw_response: Any,
        expected_pairs: set[tuple[str, str]],
    ) -> DeepAuditResult:
        result = original_validator(raw_response, expected_pairs)
        accepted.append(result)
        return result

    monkeypatch.setattr(service, "_validate_deep_audit_result", track_valid_result)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=verified_claim_evidence_pairs(),
        )

    assert result is accepted[0]
    attempt_logs = deep_audit_attempt_logs(caplog)
    assert len(attempt_logs) == 1
    assert_safe_attempt_log_fields(
        attempt_logs[0],
        operation="deep_audit",
        boundary="none",
        error_code="NONE",
    )
    assert attempt_log_fields(attempt_logs[0])["completion_tokens"] == "null"
    run_logs = hy3_run_logs(caplog)
    assert len(run_logs) == 1
    assert len(run_logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH
    assert huge_count_text not in caplog.text


def test_live_deep_audit_huge_usage_preserves_schema_error(caplog: Any) -> None:
    huge_count = 10**800
    huge_count_text = str(huge_count)
    client = FakeClient(
        [
            completion_response(
                "RAW_DEEP_AUDIT_JSON_INVALID_SENTINEL",
                (17, huge_count, huge_count + 17),
                finish_reason="length",
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(
                settings=live_settings(hy3_max_retries=0),
                client=client,
            ).deep_audit(
                document=generated_bundle().document,
                claim_evidence_pairs=verified_claim_evidence_pairs(),
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False
    attempt_logs = deep_audit_attempt_logs(caplog)
    assert len(attempt_logs) == 1
    assert_safe_attempt_log_fields(
        attempt_logs[0],
        operation="deep_audit",
        boundary="json_invalid",
        error_code="SCHEMA_INVALID",
    )
    assert attempt_log_fields(attempt_logs[0])["completion_tokens"] == "null"
    run_logs = hy3_run_logs(caplog)
    assert len(run_logs) == 1
    assert len(run_logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH
    assert huge_count_text not in caplog.text
    assert "RAW_DEEP_AUDIT_JSON_INVALID_SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    ("operation", "configured_limit"),
    [
        ("generation", GENERATION_MAX_COMPLETION_TOKENS),
        ("deep_audit", DEEP_AUDIT_MAX_COMPLETION_TOKENS),
    ],
)
@pytest.mark.parametrize("unsafe_number", [True, -1, 10**800])
def test_attempt_diagnostic_normalizes_unsafe_numbers_without_throwing(
    operation: str,
    configured_limit: int,
    unsafe_number: Any,
    caplog: Any,
) -> None:
    long_location = "DIRECT_NUMERIC_DIAGNOSTIC_SENTINEL_" * 30
    assert len(long_location) >= 900

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        if operation == "generation":
            Hy3Service._log_generation_attempt(
                attempt=unsafe_number,
                completion_tokens=unsafe_number,
                finish_reason="length",
                validation_boundary="json_invalid",
                error_code="SCHEMA_INVALID",
                validation_error_count=unsafe_number,
                validation_error_type="json_invalid",
                validation_location=long_location,
            )
            logs = generation_attempt_logs(caplog)
        else:
            Hy3Service._log_deep_audit_attempt(
                attempt=unsafe_number,
                completion_tokens=unsafe_number,
                finish_reason="length",
                validation_boundary="deep_audit_schema_invalid",
                error_code="SCHEMA_INVALID",
                validation_error_count=unsafe_number,
                validation_error_type="other_validation_error",
                validation_location=long_location,
            )
            logs = deep_audit_attempt_logs(caplog)

    assert len(logs) == 1
    fields = attempt_log_fields(logs[0])
    assert fields["operation"] == operation
    assert fields["attempt"] == "null"
    assert fields["retry_count"] == "null"
    assert fields["completion_tokens"] == "null"
    assert fields["configured_completion_limit"] == str(configured_limit)
    assert fields["validation_error_count"] == "null"
    assert fields["validation_location"].endswith("<truncated>")
    assert long_location not in logs[0]
    assert len(logs[0]) <= SAFE_DIAGNOSTIC_MAX_LENGTH


@pytest.mark.parametrize(
    ("finish_reason", "completion_tokens", "limit_reached"),
    [
        ("stop", 53, "false"),
        ("stop", 4096, "false"),
        ("stop", 16383, "false"),
        ("length", 16384, "true"),
        ("length", 16385, "true"),
    ],
)
def test_live_generation_logs_safe_finish_and_per_attempt_completion_limit(
    finish_reason: str,
    completion_tokens: int,
    limit_reached: str,
    caplog: Any,
) -> None:
    raw = valid_generation_json()
    client = FakeClient(
        [
            completion_response(
                raw,
                (101, completion_tokens, 101 + completion_tokens),
                finish_reason=finish_reason,
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        bundle = Hy3Service(settings=live_settings(), client=client).generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=load_source_blocks()[:2],
        )

    assert bundle == GeneratedBundle.model_validate_json(raw)
    logs = generation_attempt_logs(caplog)
    assert len(logs) == 1
    assert "operation=generation" in logs[0]
    assert "attempt=0" in logs[0]
    assert f"completion_tokens={completion_tokens}" in logs[0]
    assert "configured_completion_limit=16384" in logs[0]
    assert f"completion_limit_reached={limit_reached}" in logs[0]
    assert f"finish_reason={finish_reason}" in logs[0]
    assert "validation_boundary=none" in logs[0]
    assert "error_code=NONE" in logs[0]


@pytest.mark.parametrize(
    ("raw_response", "claim_policy", "expected_boundary", "error_code"),
    [
        (
            "RAW_JSON_INVALID_SENTINEL",
            "required",
            "json_invalid",
            "SCHEMA_INVALID",
        ),
        (
            json.dumps({"document": {"title": "SCHEMA_VALUE_SENTINEL"}}),
            "required",
            "generated_bundle_schema_invalid",
            "SCHEMA_INVALID",
        ),
        (
            generation_json_with_claims(present=False),
            "required",
            "claim_policy_invalid",
            "AUDIT_INCOMPLETE",
        ),
    ],
)
def test_live_generation_logs_validation_boundary_without_changing_public_error(
    raw_response: str,
    claim_policy: str,
    expected_boundary: str,
    error_code: str,
    caplog: Any,
) -> None:
    client = FakeClient(
        [
            completion_response(
                raw_response,
                (11, 7, 18),
                finish_reason="stop",
            )
            for _ in range(3)
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).generate(
                claim_policy=claim_policy,
                paper_metadata={"title": "PaperLens synthetic fixture"},
                source_blocks=load_source_blocks()[:2],
            )

    assert exc_info.value.error_code == error_code
    assert exc_info.value.retries == 2
    assert len(client.completions.calls) == 3
    logs = generation_attempt_logs(caplog)
    assert len(logs) == 3
    assert [f"attempt={attempt}" in log for attempt, log in enumerate(logs)] == [
        True,
        True,
        True,
    ]
    assert all(f"validation_boundary={expected_boundary}" in log for log in logs)
    assert all(f"error_code={error_code}" in log for log in logs)


def test_live_generation_logs_missing_content_boundary_safely(caplog: Any) -> None:
    client = FakeClient(
        [
            completion_response(
                None,
                (11, 0, 11),
                finish_reason=None,
            )
            for _ in range(3)
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).generate(
                claim_policy="required",
                paper_metadata={"title": "PaperLens synthetic fixture"},
                source_blocks=load_source_blocks()[:2],
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retries == 2
    logs = generation_attempt_logs(caplog)
    assert len(logs) == 3
    assert all("finish_reason=missing" in log for log in logs)
    assert all("validation_boundary=content_missing" in log for log in logs)


def test_generation_attempt_logs_contain_only_safe_fixed_metadata(caplog: Any) -> None:
    schema_invalid = json.loads(valid_generation_json())
    schema_invalid["PYDANTIC_RAW_FIELD_SENTINEL"] = "PRIVATE_FIELD_VALUE"
    policy_invalid = json.loads(valid_generation_json())
    policy_invalid["claims"][0]["text"] = "CLAIM_TEXT_SENTINEL"
    source_blocks = load_source_blocks()[:2]
    source_blocks[0] = source_blocks[0].model_copy(
        update={"text": "SOURCE_BLOCK_SENTINEL"}
    )
    client = FakeClient(
        [
            completion_response(
                "RAW_RESPONSE_SENTINEL",
                (None, None, None),
                finish_reason="PRIVATE_FINISH_REASON_SENTINEL",
            ),
            completion_response(
                json.dumps(schema_invalid),
                (13, 7, 20),
                finish_reason="content_filter",
            ),
            completion_response(
                json.dumps(policy_invalid),
                (17, 8, 25),
                finish_reason="tool_calls",
            ),
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(
                settings=live_settings(hy3_api_key="API_KEY_SENTINEL"),
                client=client,
            ).generate(
                claim_policy="must_be_empty",
                paper_metadata={"title": "PROMPT_SENTINEL"},
                source_blocks=source_blocks,
            )

    assert exc_info.value.error_code == "AUDIT_INCOMPLETE"
    logs = generation_attempt_logs(caplog)
    assert len(logs) == 3
    expected_keys = {
        "operation",
        "attempt",
        "completion_tokens",
        "configured_completion_limit",
        "completion_limit_reached",
        "finish_reason",
        "validation_boundary",
        "error_code",
        "retry_count",
        "validation_error_count",
        "validation_error_type",
        "validation_location",
    }
    finish_reasons = {
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "missing",
        "unknown",
    }
    boundaries = {
        "content_missing",
        "json_invalid",
        "generated_bundle_schema_invalid",
        "claim_policy_invalid",
        "provider_unavailable",
        "none",
    }
    for log in logs:
        fields = dict(item.split("=", 1) for item in log.split()[1:])
        assert set(fields) == expected_keys
        assert fields["operation"] == "generation"
        assert fields["attempt"] in {"0", "1", "2"}
        assert fields["retry_count"] == fields["attempt"]
        completion_tokens = fields["completion_tokens"]
        assert completion_tokens == "null" or int(completion_tokens) >= 0
        assert fields["configured_completion_limit"] == "16384"
        assert fields["completion_limit_reached"] in {"true", "false"}
        assert fields["finish_reason"] in finish_reasons
        assert fields["validation_boundary"] in boundaries
        assert fields["error_code"] in {
            "SCHEMA_INVALID",
            "AUDIT_INCOMPLETE",
        }

    assert "finish_reason=unknown" in logs[0]
    assert "completion_tokens=null" in logs[0]
    assert "validation_boundary=json_invalid" in logs[0]
    assert "validation_boundary=generated_bundle_schema_invalid" in logs[1]
    assert "validation_boundary=claim_policy_invalid" in logs[2]
    for sentinel in (
        "RAW_RESPONSE_SENTINEL",
        "PROMPT_SENTINEL",
        "SOURCE_BLOCK_SENTINEL",
        "CLAIM_TEXT_SENTINEL",
        "API_KEY_SENTINEL",
        "PYDANTIC_RAW_FIELD_SENTINEL",
        "PRIVATE_FIELD_VALUE",
        "PRIVATE_FINISH_REASON_SENTINEL",
    ):
        assert sentinel not in caplog.text


def test_live_missing_fields_retries_with_only_field_error_summary() -> None:
    invalid = '{"document":{"title":"MODEL_MISSING_SECRET"}}'
    client = FakeClient([invalid, valid_generation_json()])
    service = Hy3Service(settings=live_settings(), client=client)

    service.generate(
        claim_policy="required",
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:2],
    )

    assert len(client.completions.calls) == 2
    first_prompt = client.completions.calls[0]["messages"][1]["content"]
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    assert retry_prompt.startswith(first_prompt + "\n\n字段错误摘要：")
    assert retry_prompt.count("字段错误摘要：") == 1
    assert "MODEL_MISSING_SECRET" not in retry_prompt


def test_live_extra_field_retries_without_echoing_invalid_value() -> None:
    invalid_payload = json.loads(valid_generation_json())
    invalid_payload["unexpected"] = "MODEL_EXTRA_SECRET"
    client = FakeClient(
        [json.dumps(invalid_payload, ensure_ascii=False), valid_generation_json()]
    )
    service = Hy3Service(settings=live_settings(), client=client)

    service.generate(
        claim_policy="required",
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:2],
    )

    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    assert "<extra_field> [extra_forbidden]" in retry_prompt
    assert "unexpected" not in retry_prompt
    assert "MODEL_EXTRA_SECRET" not in retry_prompt


def test_live_non_json_fails_schema_after_two_retries(caplog) -> None:
    invalid = "MODEL_NON_JSON_SECRET"
    client = FakeClient([invalid, invalid, invalid])
    service = Hy3Service(settings=live_settings(), client=client)
    source_blocks = load_source_blocks()[:2]

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.generate(
                claim_policy="required",
                paper_metadata={"title": "PaperLens synthetic fixture"},
                source_blocks=source_blocks,
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False
    assert exc_info.value.retries == 2
    assert exc_info.value.usage == (303, 159, 462)
    assert len(client.completions.calls) == 3
    assert "prompt_tokens=303" in caplog.text
    assert "completion_tokens=159" in caplog.text
    assert "total_tokens=462" in caplog.text
    assert "prompt_version=gen-v4" in caplog.text
    assert "schema_version=generated-bundle-v1" in caplog.text
    assert "retries=2" in caplog.text
    for request in client.completions.calls[1:]:
        retry_prompt = request["messages"][1]["content"]
        assert retry_prompt.count("字段错误摘要：") == 1
        assert invalid not in retry_prompt
    assert invalid not in caplog.text
    assert "unit-test-key" not in caplog.text
    assert all(block.text not in caplog.text for block in source_blocks)


def test_live_schema_retry_logs_sum_of_invalid_and_valid_response_usage(
    caplog,
) -> None:
    invalid = "MODEL_CUMULATIVE_INVALID_SECRET"
    client = FakeClient(
        [
            completion_response(invalid, (11, 7, 18)),
            completion_response(valid_generation_json(), (23, 17, 40)),
        ]
    )
    source_blocks = load_source_blocks()[:2]

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        bundle = Hy3Service(settings=live_settings(), client=client).generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=source_blocks,
        )

    assert isinstance(bundle, GeneratedBundle)
    assert len(client.completions.calls) == 2
    assert "prompt_tokens=34" in caplog.text
    assert "completion_tokens=24" in caplog.text
    assert "total_tokens=58" in caplog.text
    assert "retries=1" in caplog.text
    assert "error_code=NONE" in caplog.text
    assert invalid not in caplog.text
    assert "unit-test-key" not in caplog.text
    assert all(block.text not in caplog.text for block in source_blocks)


def test_live_provider_failure_after_schema_response_preserves_prior_usage(
    caplog,
) -> None:
    invalid = "MODEL_PRIOR_INVALID_SECRET"
    client = FakeClient(
        [
            completion_response(invalid, (13, 5, 18)),
            OpenAIError("unit-test provider failure"),
        ]
    )
    source_blocks = load_source_blocks()[:2]

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).generate(
                claim_policy="required",
                paper_metadata={"title": "PaperLens synthetic fixture"},
                source_blocks=source_blocks,
            )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retries == 1
    assert exc_info.value.usage == (13, 5, 18)
    assert len(client.completions.calls) == 2
    assert "prompt_tokens=13" in caplog.text
    assert "completion_tokens=5" in caplog.text
    assert "total_tokens=18" in caplog.text
    assert "retries=1" in caplog.text
    assert "error_code=HY3_UNAVAILABLE" in caplog.text
    assert invalid not in caplog.text
    assert "unit-test-key" not in caplog.text
    assert all(block.text not in caplog.text for block in source_blocks)


def test_live_usage_component_stays_unknown_if_any_response_omits_it(
    caplog,
) -> None:
    client = FakeClient(
        [
            completion_response("MODEL_PARTIAL_USAGE_INVALID", (10, None, 10)),
            completion_response(valid_generation_json(), (20, 30, 50)),
        ]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        Hy3Service(settings=live_settings(), client=client).generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=load_source_blocks()[:2],
        )

    assert "prompt_tokens=30" in caplog.text
    assert "completion_tokens=None" in caplog.text
    assert "total_tokens=60" in caplog.text


def test_live_missing_key_fails_without_loading_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([valid_generation_json()])
    service = Hy3Service(
        settings=live_settings(hy3_api_key=""),
        client=client,
    )
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Live failure must not load Mock data"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=load_source_blocks()[:2],
        )

    assert exc_info.value.error_code == "HY3_CONFIG_MISSING"
    assert exc_info.value.retryable is False
    assert client.completions.calls == []


def test_live_provider_failure_stays_failed_without_mock_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([OpenAIError("unit-test provider failure")])
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Live failure must not load Mock data"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=load_source_blocks()[:2],
        )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert exc_info.value.usage == (None, None, None)
    assert len(client.completions.calls) == 1


def test_live_run_log_contains_metadata_but_not_key_or_source_text(caplog) -> None:
    client = FakeClient([valid_generation_json()])
    source_blocks = load_source_blocks()[:2]

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        Hy3Service(settings=live_settings(), client=client).generate(
            claim_policy="required",
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=source_blocks,
        )

    assert "model=hy3" in caplog.text
    assert "prompt_version=gen-v4" in caplog.text
    assert "schema_version=generated-bundle-v1" in caplog.text
    assert "temperature=0" in caplog.text
    assert "max_completion_tokens=16384" in caplog.text
    assert "prompt_tokens=101" in caplog.text
    assert "completion_tokens=53" in caplog.text
    assert "latency_ms=" in caplog.text
    assert "retries=0" in caplog.text
    assert "error_code=NONE" in caplog.text
    assert COMMON_SYSTEM_PROMPT not in caplog.text
    assert "unit-test-key" not in caplog.text
    assert all(block.text not in caplog.text for block in source_blocks)


def test_models_endpoint_requires_configured_online_hy3() -> None:
    client = FakeClient(
        [],
        models=[SimpleNamespace(id="hy3", status="online")],
    )

    assert Hy3Service(
        settings=live_settings(),
        client=client,
    ).check_model_online() is True
    assert client.models.calls == 1


def test_models_endpoint_missing_key_does_not_call_provider_or_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [],
        models=[SimpleNamespace(id="hy3", status="online")],
    )
    service = Hy3Service(
        settings=live_settings(hy3_api_key=""),
        client=client,
    )
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Model probe failure must not load Mock data"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.check_model_online()

    assert exc_info.value.error_code == "HY3_CONFIG_MISSING"
    assert exc_info.value.retryable is False
    assert client.models.calls == 0


def test_models_endpoint_provider_error_is_stable_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    provider_detail = "MODEL_LIST_PROVIDER_PRIVATE_DETAIL"
    client = FakeClient([], models_error=OpenAIError(provider_detail))
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Model probe failure must not load Mock data"),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.check_model_online()

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert client.models.calls == 1
    assert provider_detail not in caplog.text
    assert "unit-test-key" not in caplog.text


def test_models_endpoint_missing_hy3_is_unavailable_without_mock(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    client = FakeClient(
        [],
        models=[SimpleNamespace(id="another-model", status="online")],
    )
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Model probe failure must not load Mock data"),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.check_model_online()

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert client.models.calls == 1
    assert "unit-test-key" not in caplog.text


@pytest.mark.parametrize("status", ["offline", "pre-offline"])
def test_models_endpoint_rejects_non_online_hy3_without_mock(
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    client = FakeClient(
        [],
        models=[SimpleNamespace(id="hy3", status=status)],
    )
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Model probe failure must not load Mock data"),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.check_model_online()

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert client.models.calls == 1
    assert "unit-test-key" not in caplog.text


def test_openai_client_is_built_from_backend_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = FakeClient(
        [],
        models=[SimpleNamespace(id="hy3", status="online")],
    )

    def fake_openai(**kwargs: Any) -> FakeClient:
        captured.update(kwargs)
        return client

    monkeypatch.setattr("backend.app.hy3_service.OpenAI", fake_openai)

    assert Hy3Service(settings=live_settings()).check_model_online() is True
    assert captured == {
        "api_key": "unit-test-key",
        "base_url": "https://tokenhub.tencentmaas.com/v1",
        "timeout": 30,
        "max_retries": 0,
    }


def test_document_claim_regeneration_sends_current_document_and_sources_without_history() -> None:
    bundle = generated_bundle()
    client = FakeClient([valid_generation_json()])

    regenerated = Hy3Service(
        settings=live_settings(),
        client=client,
    ).regenerate_document_claims(
        document=bundle.document,
        source_blocks=load_source_blocks()[:2],
    )

    assert regenerated == bundle
    assert len(client.completions.calls) == 1
    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert bundle.document.model_dump_json() in prompt
    assert "source_blocks" in prompt
    assert "history" not in prompt.casefold()
    assert "versions" not in prompt.casefold()


def test_document_claim_regeneration_rejects_provider_document_rewrite() -> None:
    bundle = generated_bundle()
    rewritten = json.loads(valid_generation_json())
    rewritten["document"]["title"] = "Provider rewrote the accepted document"
    client = FakeClient([json.dumps(rewritten, ensure_ascii=False)])

    with pytest.raises(Hy3ServiceError) as exc_info:
        Hy3Service(
            settings=live_settings(),
            client=client,
        ).regenerate_document_claims(
            document=bundle.document,
            source_blocks=load_source_blocks()[:2],
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False


def test_sentence_claim_regeneration_sends_only_minimal_target_context() -> None:
    bundle = generated_bundle()
    target_claim = bundle.claims[0]
    evidence = verified_claim_evidence_pairs()[0][1]
    response_payload = {
        "claims": [
            regenerated_sentence_claim(
                claim_id="c-revised-002",
                sentence_id=target_claim.sentence_id,
            ),
            regenerated_sentence_claim(
                claim_id="c-revised-001",
                sentence_id=target_claim.sentence_id,
            ),
        ]
    }
    client = FakeClient(
        [json.dumps(response_payload, ensure_ascii=False)]
    )
    service = Hy3Service(settings=live_settings(), client=client)
    revised_text = "The revised target sentence keeps its supported fact."

    result = service.regenerate_sentence_claims(
        target_sentence_id=target_claim.sentence_id,
        accepted_after_text=revised_text,
        original_claims=[target_claim],
        evidence_records=[evidence],
        allowed_block_ids={"p01-b001"},
        reserved_claim_ids={"UNMODIFIED_CLAIM_ID_SENTINEL"},
        user_instruction="USER_INTENT_SENTINEL",
    )

    assert isinstance(result, SentenceClaimRegenerationResult)
    assert result.model_dump(mode="json") == response_payload
    assert [claim.claim_id for claim in result.claims] == [
        "c-revised-002",
        "c-revised-001",
    ]
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    prompt = request["messages"][1]["content"]
    assert target_claim.sentence_id in prompt
    assert revised_text in prompt
    assert target_claim.text in prompt
    assert evidence.quote in prompt
    assert "p01-b001" in prompt
    assert "USER_INTENT_SENTINEL" not in prompt
    assert bundle.document.title not in prompt
    assert bundle.document.sections[1].sentences[0].text not in prompt
    assert "UNMODIFIED_CLAIM_ID_SENTINEL" not in prompt
    assert '"page_index":' not in prompt
    assert '"bbox":' not in prompt
    assert '"quote_verified":' not in prompt
    assert '"match_method":' not in prompt
    assert "history" not in prompt.casefold()
    assert SENTENCE_CLAIMS_PROMPT_VERSION == "sentence-claims-v2"
    response_format = request["response_format"]["json_schema"]
    assert response_format["name"] == "paperlens_sentence_claims_v1"
    assert response_format["strict"] is True
    assert response_format["schema"] == (
        SentenceClaimRegenerationResult.model_json_schema()
    )
    assert request["temperature"] == 0
    assert request["max_completion_tokens"] == 4096
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    observation = service.last_run_observation
    assert observation is not None
    assert observation.operation == "sentence_claims"
    assert observation.provider_calls == 1
    assert observation.retries == 0
    assert observation.prompt_tokens == 101
    assert observation.completion_tokens == 53
    assert observation.total_tokens == 154
    assert observation.error_code == "NONE"


@pytest.mark.parametrize(
    "source_of_auditable_requirement",
    ["original_auditable_claim", "verified_evidence"],
)
def test_sentence_claim_regeneration_requires_auditable_continuity(
    source_of_auditable_requirement: str,
) -> None:
    if source_of_auditable_requirement == "original_auditable_claim":
        original_claim = generated_bundle().claims[0]
        evidence_records: list[EvidenceRecord] = []
    else:
        original_claim = generated_bundle().claims[2]
        evidence_records = [verified_claim_evidence_pairs()[2][1]]
    target_sentence_id = original_claim.sentence_id
    non_auditable = sentence_claim_regeneration_json(
        [
            regenerated_sentence_claim(
                sentence_id=target_sentence_id,
                auditability="non_auditable",
                candidate_block_ids=[],
            )
        ]
    )
    client = FakeClient([non_auditable, non_auditable, non_auditable])
    service = Hy3Service(settings=live_settings(), client=client)
    allowed_block_ids = set(original_claim.candidate_block_ids)
    allowed_block_ids.update(
        record.block_id
        for record in evidence_records
        if record.block_id is not None
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.regenerate_sentence_claims(
            target_sentence_id=target_sentence_id,
            accepted_after_text="A revised sentence requiring auditable coverage.",
            original_claims=[original_claim],
            evidence_records=evidence_records,
            allowed_block_ids=allowed_block_ids,
            reserved_claim_ids=set(),
            user_instruction="Revise the target sentence.",
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retryable is False
    assert exc_info.value.retries == 2
    assert len(client.completions.calls) == 3


def test_sentence_claim_regeneration_rejects_any_wrong_sentence_id() -> None:
    payload = sentence_claim_regeneration_json(
        [
            regenerated_sentence_claim(claim_id="c-valid"),
            regenerated_sentence_claim(
                claim_id="c-wrong-target",
                sentence_id="s-other",
            ),
        ]
    )
    client = FakeClient([payload, payload, payload])
    service = Hy3Service(settings=live_settings(), client=client)

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.regenerate_sentence_claims(
            target_sentence_id="s-001",
            accepted_after_text="The revised target sentence.",
            original_claims=[],
            evidence_records=[],
            allowed_block_ids={"p01-b001"},
            reserved_claim_ids=set(),
            user_instruction="Revise only the target.",
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retries == 2
    assert len(client.completions.calls) == 3


def test_sentence_claim_regeneration_rejects_whole_output_for_forbidden_block() -> None:
    payload = sentence_claim_regeneration_json(
        [
            regenerated_sentence_claim(claim_id="c-valid"),
            regenerated_sentence_claim(
                claim_id="c-forbidden-block",
                candidate_block_ids=["FORBIDDEN_BLOCK_SENTINEL"],
            ),
        ]
    )
    client = FakeClient([payload, payload, payload])
    service = Hy3Service(settings=live_settings(), client=client)

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.regenerate_sentence_claims(
            target_sentence_id="s-001",
            accepted_after_text="The revised target sentence.",
            original_claims=[],
            evidence_records=[],
            allowed_block_ids={"p01-b001"},
            reserved_claim_ids=set(),
            user_instruction="Revise only the target.",
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retries == 2
    assert len(client.completions.calls) == 3


def test_sentence_claim_regeneration_retries_reserved_claim_id_collision() -> None:
    payload = sentence_claim_regeneration_json(
        [regenerated_sentence_claim(claim_id="c-unmodified")]
    )
    client = FakeClient([payload, payload, payload])
    service = Hy3Service(settings=live_settings(), client=client)

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.regenerate_sentence_claims(
            target_sentence_id="s-001",
            accepted_after_text="The revised target sentence.",
            original_claims=[],
            evidence_records=[],
            allowed_block_ids={"p01-b001"},
            reserved_claim_ids={"c-unmodified"},
            user_instruction="Revise only the target.",
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retries == 2
    assert len(client.completions.calls) == 3
    assert all(
        "c-unmodified" not in request["messages"][1]["content"]
        for request in client.completions.calls
    )


def test_sentence_claim_regeneration_retries_invalid_then_returns_unchanged() -> None:
    invalid = sentence_claim_regeneration_json(
        [regenerated_sentence_claim(sentence_id="s-other")]
    )
    valid_payload = {
        "claims": [
            regenerated_sentence_claim(claim_id="c-second"),
            regenerated_sentence_claim(claim_id="c-first"),
        ]
    }
    client = FakeClient(
        [invalid, json.dumps(valid_payload, ensure_ascii=False)]
    )
    service = Hy3Service(settings=live_settings(), client=client)

    result = service.regenerate_sentence_claims(
        target_sentence_id="s-001",
        accepted_after_text="The revised target sentence.",
        original_claims=[],
        evidence_records=[],
        allowed_block_ids={"p01-b001"},
        reserved_claim_ids=set(),
        user_instruction="Revise only the target.",
    )

    assert result.model_dump(mode="json") == valid_payload
    assert [claim.claim_id for claim in result.claims] == [
        "c-second",
        "c-first",
    ]
    assert len(client.completions.calls) == 2


def test_sentence_claim_regeneration_diagnostics_are_bounded_and_safe(
    caplog: Any,
) -> None:
    original_claim = generated_bundle().claims[0].model_copy(
        update={"text": "ORIGINAL_CLAIM_TEXT_SENTINEL"}
    )
    evidence = verified_claim_evidence_pairs()[0][1].model_copy(
        update={"quote": "VERIFIED_EVIDENCE_SENTINEL"}
    )
    raw_response = "RAW_PROVIDER_RESPONSE_SENTINEL"
    client = FakeClient([raw_response, raw_response, raw_response])
    service = Hy3Service(settings=live_settings(), client=client)
    secrets = {
        "TARGET_SENTENCE_TEXT_SENTINEL",
        "ORIGINAL_CLAIM_TEXT_SENTINEL",
        "VERIFIED_EVIDENCE_SENTINEL",
        "USER_INSTRUCTION_SENTINEL",
        "RAW_PROVIDER_RESPONSE_SENTINEL",
        "unit-test-key",
    }

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.regenerate_sentence_claims(
                target_sentence_id="s-001",
                accepted_after_text="TARGET_SENTENCE_TEXT_SENTINEL",
                original_claims=[original_claim],
                evidence_records=[evidence],
                allowed_block_ids={"p01-b001"},
                reserved_claim_ids=set(),
                user_instruction="USER_INSTRUCTION_SENTINEL",
            )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert exc_info.value.retries == 2
    attempt_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("hy3_sentence_claims_attempt ")
    ]
    assert len(attempt_logs) == 3
    for message in attempt_logs:
        assert_safe_attempt_log_fields(
            message,
            operation="sentence_claims",
            boundary="json_invalid",
            error_code="SCHEMA_INVALID",
        )
    assert all(secret not in caplog.text for secret in secrets)
    for request in client.completions.calls[1:]:
        prompt = request["messages"][1]["content"]
        retry_summary = prompt.split("\n\n字段错误摘要：", 1)[1]
        assert all(secret not in retry_summary for secret in secrets)


def test_sentence_claim_regeneration_live_failure_never_loads_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([OpenAIError("RAW_PROVIDER_FAILURE_SENTINEL")])
    service = Hy3Service(settings=live_settings(), client=client)
    monkeypatch.setattr(
        service,
        "_load_mock_response",
        lambda: pytest.fail("Live failure must not load Mock data"),
    )

    with pytest.raises(Hy3ServiceError) as exc_info:
        service.regenerate_sentence_claims(
            target_sentence_id="s-001",
            accepted_after_text="The revised target sentence.",
            original_claims=[],
            evidence_records=[],
            allowed_block_ids={"p01-b001"},
            reserved_claim_ids=set(),
            user_instruction="Revise only the target.",
        )

    assert exc_info.value.error_code == "HY3_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert len(client.completions.calls) == 1
