import json
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
    DeepAuditResult,
    EvidenceRecord,
    GeneratedBundle,
    SectionId,
    SourceBlock,
)
from backend.app.prompts import (
    COMMON_SYSTEM_PROMPT,
    DEEP_AUDIT_PROMPT_VERSION,
    DEEP_AUDIT_SCHEMA_NAME,
    DEEP_AUDIT_SCHEMA_VERSION,
    GENERATION_PROMPT_VERSION,
    GENERATION_SCHEMA_NAME,
    GENERATION_SCHEMA_VERSION,
    render_deep_audit_prompt,
    render_generation_prompt,
)
from backend.app.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"


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


def test_generation_prompt_centralizes_stage_two_contract() -> None:
    prompt = render_generation_prompt(
        claim_policy="required",
        paper_metadata_json='{"title":"Synthetic"}',
        source_blocks_json='[{"block_id":"p01-b001","text":"Evidence"}]',
    )

    assert GENERATION_PROMPT_VERSION == "gen-v3"
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

    assert DEEP_AUDIT_PROMPT_VERSION == "audit-v2"
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
    assert "prompt_version=audit-v2" in caplog.text
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

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = Hy3Service(settings=live_settings(), client=client).deep_audit(
            document=generated_bundle().document,
            claim_evidence_pairs=[],
        )

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
    assert "prompt_version=gen-v3" in caplog.text
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
    assert "prompt_version=gen-v3" in caplog.text
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
