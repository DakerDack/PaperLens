import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openai import OpenAIError
from pydantic import TypeAdapter

from backend.app.hy3_service import Hy3Service, Hy3ServiceError
from backend.app.models import GeneratedBundle, SectionId, SourceBlock
from backend.app.prompts import (
    COMMON_SYSTEM_PROMPT,
    GENERATION_PROMPT_VERSION,
    GENERATION_SCHEMA_NAME,
    GENERATION_SCHEMA_VERSION,
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


def completion_response(
    content: str,
    usage: tuple[int | None, int | None, int | None] | None,
) -> SimpleNamespace:
    usage_payload = None
    if usage is not None:
        usage_payload = SimpleNamespace(
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
        )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage_payload,
    )


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
        paper_metadata_json='{"title":"Synthetic"}',
        source_blocks_json='[{"block_id":"p01-b001","text":"Evidence"}]',
    )

    assert GENERATION_PROMPT_VERSION == "gen-v1"
    assert GENERATION_SCHEMA_VERSION == "generated-bundle-v1"
    assert GENERATION_SCHEMA_NAME == "paperlens_generated_bundle_v1"
    assert "只能依据输入中的 SourceBlock" in COMMON_SYSTEM_PROMPT
    for section_id in SectionId:
        assert section_id.value in prompt
    assert "candidate_block_ids" in prompt
    assert "最多 3 个" in prompt
    assert "候选" in prompt
    assert "不得生成页码、bbox、总分或合格结论" in prompt
    assert 'paper_metadata: {"title":"Synthetic"}' in prompt
    assert 'source_blocks: [{"block_id":"p01-b001","text":"Evidence"}]' in prompt


def test_mock_generation_returns_valid_generated_bundle() -> None:
    bundle = Hy3Service(settings=mock_settings()).generate(
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks(),
    )

    assert isinstance(bundle, GeneratedBundle)
    assert [section.section_id for section in bundle.document.sections] == list(
        SectionId
    )
    assert bundle.claims[0].candidate_block_ids == ["p01-b001"]


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
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks(),
    )

    assert bundle.claims[0].candidate_block_ids == ["p99-b999"]
    assert not hasattr(bundle.claims[0], "quote_verified")


def test_live_generation_uses_openai_chat_and_strict_generated_bundle_schema() -> None:
    client = FakeClient([valid_generation_json()])
    service = Hy3Service(settings=live_settings(), client=client)

    bundle = service.generate(
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:3],
    )

    assert isinstance(bundle, GeneratedBundle)
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    assert request["model"] == "hy3"
    assert request["stream"] is False
    assert request["temperature"] == 0
    assert request["max_completion_tokens"] == 4096
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


def test_live_missing_fields_retries_with_only_field_error_summary() -> None:
    invalid = '{"document":{"title":"MODEL_MISSING_SECRET"}}'
    client = FakeClient([invalid, valid_generation_json()])
    service = Hy3Service(settings=live_settings(), client=client)

    service.generate(
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
        paper_metadata={"title": "PaperLens synthetic fixture"},
        source_blocks=load_source_blocks()[:2],
    )

    assert len(client.completions.calls) == 2
    retry_prompt = client.completions.calls[1]["messages"][1]["content"]
    assert "unexpected" in retry_prompt
    assert "MODEL_EXTRA_SECRET" not in retry_prompt


def test_live_non_json_fails_schema_after_two_retries(caplog) -> None:
    invalid = "MODEL_NON_JSON_SECRET"
    client = FakeClient([invalid, invalid, invalid])
    service = Hy3Service(settings=live_settings(), client=client)
    source_blocks = load_source_blocks()[:2]

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            service.generate(
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
        bundle = Hy3Service(settings=live_settings(), client=client).generate(
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
        with pytest.raises(Hy3ServiceError) as exc_info:
            Hy3Service(settings=live_settings(), client=client).generate(
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
        Hy3Service(settings=live_settings(), client=client).generate(
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
        Hy3Service(settings=live_settings(), client=client).generate(
            paper_metadata={"title": "PaperLens synthetic fixture"},
            source_blocks=source_blocks,
        )

    assert "model=hy3" in caplog.text
    assert "prompt_version=gen-v1" in caplog.text
    assert "schema_version=generated-bundle-v1" in caplog.text
    assert "temperature=0" in caplog.text
    assert "max_completion_tokens=4096" in caplog.text
    assert "prompt_tokens=101" in caplog.text
    assert "completion_tokens=53" in caplog.text
    assert "latency_ms=" in caplog.text
    assert "retries=0" in caplog.text
    assert "error_code=NONE" in caplog.text
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
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

    with caplog.at_level(logging.INFO, logger="backend.app.hy3_service"):
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
