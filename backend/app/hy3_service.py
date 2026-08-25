from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from backend.app.models import (
    AtomicClaim,
    ContentDraft,
    DeepAuditResult,
    EvidenceRecord,
    GeneratedBundle,
    RiskCategory,
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
    render_deep_audit_prompt,
    render_generation_prompt,
)
from backend.app.settings import Settings, settings as app_settings


logger = logging.getLogger(__name__)

MOCK_GENERATION_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "generation_valid.json"
)
MOCK_DEEP_AUDIT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "deep_audit_valid.json"
)
GENERATION_TEMPERATURE = 0
GENERATION_MAX_COMPLETION_TOKENS = 4096
GENERATION_THINKING = "disabled"
DEEP_AUDIT_TEMPERATURE = 0
DEEP_AUDIT_MAX_COMPLETION_TOKENS = 4096
DEEP_AUDIT_THINKING = "disabled"


UsageTuple = tuple[int | None, int | None, int | None]
SemanticPair = tuple[AtomicClaim, EvidenceRecord]
SemanticPairKey = tuple[str, str]


class Hy3ServiceError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool,
        field_error_summary: str | None = None,
        retries: int = 0,
        usage: UsageTuple = (None, None, None),
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.field_error_summary = field_error_summary
        self.retries = retries
        self.usage = usage


class Hy3Service:
    def __init__(
        self,
        settings: Settings | None = None,
        client: Any | None = None,
    ) -> None:
        self.settings = settings or app_settings
        self._client = client

    def generate(
        self,
        *,
        paper_metadata: dict[str, Any],
        source_blocks: list[SourceBlock],
    ) -> GeneratedBundle:
        started_at = time.perf_counter()
        usage: UsageTuple = (None, None, None)
        retries = 0
        try:
            if self.settings.paperlens_model_mode == "mock":
                bundle = self._validate_generated_bundle(
                    self._load_mock_response()
                )
            else:
                self._require_live_config()
                prompt = self._build_generation_prompt(
                    paper_metadata=paper_metadata,
                    source_blocks=source_blocks,
                )
                bundle, retries, usage = self._generate_live(prompt)
        except Hy3ServiceError as exc:
            self._log_run(
                started_at=started_at,
                retries=exc.retries,
                usage=exc.usage,
                error_code=exc.error_code,
            )
            raise

        self._log_run(
            started_at=started_at,
            retries=retries,
            usage=usage,
            error_code="NONE",
        )
        return bundle

    def deep_audit(
        self,
        *,
        document: ContentDraft,
        claim_evidence_pairs: list[SemanticPair],
    ) -> DeepAuditResult:
        started_at = time.perf_counter()
        usage: UsageTuple = (None, None, None)
        retries = 0
        try:
            expected_pairs = self._validate_deep_audit_input(
                claim_evidence_pairs
            )
            if self.settings.paperlens_model_mode == "mock":
                result = self._validate_deep_audit_result(
                    self._load_mock_deep_audit_response(),
                    expected_pairs,
                )
            else:
                self._require_live_config()
                prompt = self._build_deep_audit_prompt(
                    document,
                    claim_evidence_pairs,
                )
                result, retries, usage = self._deep_audit_live(
                    prompt,
                    expected_pairs,
                )
        except Hy3ServiceError as exc:
            self._log_run(
                started_at=started_at,
                retries=exc.retries,
                usage=exc.usage,
                error_code=exc.error_code,
                prompt_version=DEEP_AUDIT_PROMPT_VERSION,
                schema_version=DEEP_AUDIT_SCHEMA_VERSION,
                temperature=DEEP_AUDIT_TEMPERATURE,
                max_completion_tokens=DEEP_AUDIT_MAX_COMPLETION_TOKENS,
                thinking=DEEP_AUDIT_THINKING,
            )
            raise

        self._log_run(
            started_at=started_at,
            retries=retries,
            usage=usage,
            error_code="NONE",
            prompt_version=DEEP_AUDIT_PROMPT_VERSION,
            schema_version=DEEP_AUDIT_SCHEMA_VERSION,
            temperature=DEEP_AUDIT_TEMPERATURE,
            max_completion_tokens=DEEP_AUDIT_MAX_COMPLETION_TOKENS,
            thinking=DEEP_AUDIT_THINKING,
        )
        return result

    def check_model_online(self) -> bool:
        self._require_live_config()
        try:
            response = self._get_client().models.list()
        except OpenAIError as exc:
            logger.info(
                "hy3_models model=%s status=unknown error_code=HY3_UNAVAILABLE",
                self.settings.hy3_model,
            )
            raise Hy3ServiceError(
                "HY3_UNAVAILABLE",
                "The TokenHub model list could not be retrieved.",
                retryable=True,
            ) from exc

        for model in getattr(response, "data", []):
            model_id = self._model_value(model, "id")
            status = self._model_value(model, "status")
            if model_id == self.settings.hy3_model and status == "online":
                logger.info(
                    "hy3_models model=%s status=online error_code=NONE",
                    self.settings.hy3_model,
                )
                return True

        logger.info(
            "hy3_models model=%s status=not_online error_code=HY3_UNAVAILABLE",
            self.settings.hy3_model,
        )
        raise Hy3ServiceError(
            "HY3_UNAVAILABLE",
            "The configured Hy3 model is not online.",
            retryable=True,
        )

    def _generate_live(
        self,
        original_prompt: str,
    ) -> tuple[GeneratedBundle, int, UsageTuple]:
        field_error_summary: str | None = None
        cumulative_usage: UsageTuple | None = None
        for attempt in range(self.settings.hy3_max_retries + 1):
            prompt = original_prompt
            if field_error_summary is not None:
                prompt += f"\n\n字段错误摘要：{field_error_summary}"

            try:
                response = self._get_client().chat.completions.create(
                    model=self.settings.hy3_model,
                    messages=[
                        {"role": "system", "content": COMMON_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=self._response_format(),
                    stream=False,
                    temperature=GENERATION_TEMPERATURE,
                    max_completion_tokens=GENERATION_MAX_COMPLETION_TOKENS,
                    extra_body={
                        "thinking": {"type": GENERATION_THINKING}
                    },
                )
            except OpenAIError as exc:
                raise Hy3ServiceError(
                    "HY3_UNAVAILABLE",
                    "The Hy3 provider request failed.",
                    retryable=True,
                    retries=attempt,
                    usage=(
                        cumulative_usage
                        if cumulative_usage is not None
                        else (None, None, None)
                    ),
                ) from exc

            cumulative_usage = self._accumulate_usage(
                cumulative_usage,
                self._extract_usage(response),
            )
            raw_response = self._extract_response_content(response)
            try:
                return (
                    self._validate_generated_bundle(raw_response),
                    attempt,
                    cumulative_usage,
                )
            except Hy3ServiceError as exc:
                exc.retries = attempt
                exc.usage = cumulative_usage
                if attempt >= self.settings.hy3_max_retries:
                    raise
                field_error_summary = (
                    exc.field_error_summary or "$ [schema_invalid]"
                )

        raise AssertionError("unreachable schema retry state")

    def _deep_audit_live(
        self,
        original_prompt: str,
        expected_pairs: set[SemanticPairKey],
    ) -> tuple[DeepAuditResult, int, UsageTuple]:
        field_error_summary: str | None = None
        cumulative_usage: UsageTuple | None = None
        for attempt in range(self.settings.hy3_max_retries + 1):
            prompt = original_prompt
            if field_error_summary is not None:
                prompt += f"\n\n字段错误摘要：{field_error_summary}"

            try:
                response = self._get_client().chat.completions.create(
                    model=self.settings.hy3_model,
                    messages=[
                        {
                            "role": "system",
                            "content": DEEP_AUDIT_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format=self._deep_audit_response_format(),
                    stream=False,
                    temperature=DEEP_AUDIT_TEMPERATURE,
                    max_completion_tokens=DEEP_AUDIT_MAX_COMPLETION_TOKENS,
                    extra_body={
                        "thinking": {"type": DEEP_AUDIT_THINKING}
                    },
                )
            except OpenAIError as exc:
                raise Hy3ServiceError(
                    "HY3_UNAVAILABLE",
                    "The Hy3 provider request failed.",
                    retryable=True,
                    retries=attempt,
                    usage=(
                        cumulative_usage
                        if cumulative_usage is not None
                        else (None, None, None)
                    ),
                ) from exc

            cumulative_usage = self._accumulate_usage(
                cumulative_usage,
                self._extract_usage(response),
            )
            raw_response = self._extract_response_content(response)
            try:
                return (
                    self._validate_deep_audit_result(
                        raw_response,
                        expected_pairs,
                    ),
                    attempt,
                    cumulative_usage,
                )
            except Hy3ServiceError as exc:
                exc.retries = attempt
                exc.usage = cumulative_usage
                if attempt >= self.settings.hy3_max_retries:
                    raise
                field_error_summary = (
                    exc.field_error_summary or "$ [schema_invalid]"
                )

        raise AssertionError("unreachable schema retry state")

    def _require_live_config(self) -> None:
        if self.settings.hy3_api_key.strip():
            return
        raise Hy3ServiceError(
            "HY3_CONFIG_MISSING",
            "HY3_API_KEY is required in Live mode.",
            retryable=False,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.settings.hy3_api_key.strip(),
                base_url=self.settings.hy3_base_url,
                timeout=self.settings.hy3_timeout_seconds,
                max_retries=0,
            )
        return self._client

    @staticmethod
    def _build_generation_prompt(
        *,
        paper_metadata: dict[str, Any],
        source_blocks: list[SourceBlock],
    ) -> str:
        paper_metadata_json = json.dumps(
            paper_metadata,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        source_blocks_json = json.dumps(
            [block.model_dump(mode="json") for block in source_blocks],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return render_generation_prompt(
            paper_metadata_json=paper_metadata_json,
            source_blocks_json=source_blocks_json,
        )

    @staticmethod
    def _build_deep_audit_prompt(
        document: ContentDraft,
        claim_evidence_pairs: list[SemanticPair],
    ) -> str:
        items = [
            {
                "claim": claim.model_dump(mode="json"),
                "evidence": evidence.model_dump(mode="json"),
            }
            for claim, evidence in claim_evidence_pairs
        ]
        return render_deep_audit_prompt(
            content_draft_json=document.model_dump_json(),
            verified_claim_evidence_pairs_json=json.dumps(
                items,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    @staticmethod
    def _response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": GENERATION_SCHEMA_NAME,
                "strict": True,
                "schema": GeneratedBundle.model_json_schema(),
            },
        }

    @staticmethod
    def _deep_audit_response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": DEEP_AUDIT_SCHEMA_NAME,
                "strict": True,
                "schema": DeepAuditResult.model_json_schema(),
            },
        }

    @staticmethod
    def _load_mock_response() -> str:
        try:
            return MOCK_GENERATION_FIXTURE.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The configured Mock response could not be read.",
                retryable=False,
            ) from exc

    @staticmethod
    def _load_mock_deep_audit_response() -> str:
        try:
            return MOCK_DEEP_AUDIT_FIXTURE.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The configured Mock deep-audit response could not be read.",
                retryable=False,
            ) from exc

    @staticmethod
    def _validate_generated_bundle(raw_response: Any) -> GeneratedBundle:
        if not isinstance(raw_response, (str, bytes, bytearray)):
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not contain JSON text.",
                retryable=False,
                field_error_summary=(
                    "$ [json_type]: response content must be JSON text"
                ),
            )
        try:
            return GeneratedBundle.model_validate_json(raw_response)
        except ValidationError as exc:
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not match GeneratedBundle.",
                retryable=False,
                field_error_summary=Hy3Service._field_error_summary(exc),
            ) from exc

    @staticmethod
    def _validate_deep_audit_input(
        claim_evidence_pairs: list[SemanticPair],
    ) -> set[SemanticPairKey]:
        expected_pairs: set[SemanticPairKey] = set()
        for claim, evidence in claim_evidence_pairs:
            if (
                evidence.claim_id != claim.claim_id
                or evidence.block_id is None
                or not evidence.quote_verified
            ):
                raise Hy3ServiceError(
                    "SCHEMA_INVALID",
                    "Deep audit requires claim-linked verified evidence.",
                    retryable=False,
                    field_error_summary=(
                        "$ [input_invalid]: claim and verified evidence "
                        "must be linked"
                    ),
                )
            pair = (claim.claim_id, evidence.block_id)
            if pair in expected_pairs:
                raise Hy3ServiceError(
                    "SCHEMA_INVALID",
                    "Deep audit input contains a duplicate claim-evidence pair.",
                    retryable=False,
                    field_error_summary=(
                        "$ [input_duplicate]: duplicate claim-evidence pair"
                    ),
                )
            expected_pairs.add(pair)
        return expected_pairs

    @staticmethod
    def _validate_deep_audit_result(
        raw_response: Any,
        expected_pairs: set[SemanticPairKey],
    ) -> DeepAuditResult:
        if not isinstance(raw_response, (str, bytes, bytearray)):
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not contain JSON text.",
                retryable=False,
                field_error_summary=(
                    "$ [json_type]: response content must be JSON text"
                ),
            )
        try:
            result = DeepAuditResult.model_validate_json(raw_response)
        except ValidationError as exc:
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not match DeepAuditResult v2.",
                retryable=False,
                field_error_summary=Hy3Service._field_error_summary(exc),
            ) from exc

        actual_pairs = [
            (judgment.claim_id, judgment.block_id)
            for judgment in result.semantic_judgments
        ]
        unique_actual_pairs = set(actual_pairs)
        if (
            len(actual_pairs) != len(unique_actual_pairs)
            or unique_actual_pairs != expected_pairs
        ):
            raise Hy3ServiceError(
                "AUDIT_INCOMPLETE",
                "The deep-audit semantic judgments did not match the requested pairs.",
                retryable=True,
                field_error_summary=(
                    "semantic_judgments [semantic_pair_mismatch]: "
                    f"expected_count={len(expected_pairs)} "
                    f"actual_count={len(actual_pairs)} "
                    f"unique_count={len(unique_actual_pairs)}"
                ),
            )

        risk_categories = [
            finding.category for finding in result.risk_findings
        ]
        unique_risk_categories = set(risk_categories)
        expected_risk_categories = set(RiskCategory)
        if (
            len(risk_categories) != len(expected_risk_categories)
            or len(unique_risk_categories) != len(expected_risk_categories)
            or unique_risk_categories != expected_risk_categories
        ):
            raise Hy3ServiceError(
                "AUDIT_INCOMPLETE",
                "The deep-audit risk findings did not cover the required categories.",
                retryable=True,
                field_error_summary=(
                    "risk_findings [risk_category_coverage_invalid]: "
                    f"expected_count={len(expected_risk_categories)} "
                    f"actual_count={len(risk_categories)} "
                    f"unique_count={len(unique_risk_categories)}"
                ),
            )
        return result

    @staticmethod
    def _field_error_summary(error: ValidationError) -> str:
        summaries: list[str] = []
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:8]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "$"
            error_type = str(item.get("type", "validation_error"))
            message = " ".join(str(item.get("msg", "invalid value")).split())
            summaries.append(f"{location} [{error_type}]: {message}")
        summary = "; ".join(summaries) or "$ [schema_invalid]"
        return summary[:800]

    @staticmethod
    def _extract_response_content(response: Any) -> Any:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        return getattr(message, "content", None)

    @staticmethod
    def _extract_usage(response: Any) -> UsageTuple:
        usage = getattr(response, "usage", None)
        if usage is None:
            return (None, None, None)
        return (
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None),
        )

    @staticmethod
    def _accumulate_usage(
        accumulated: UsageTuple | None,
        received: UsageTuple,
    ) -> UsageTuple:
        if accumulated is None:
            return received

        def add_component(
            previous: int | None,
            current: int | None,
        ) -> int | None:
            if previous is None or current is None:
                return None
            return previous + current

        return (
            add_component(accumulated[0], received[0]),
            add_component(accumulated[1], received[1]),
            add_component(accumulated[2], received[2]),
        )

    @staticmethod
    def _model_value(model: Any, field_name: str) -> Any:
        if isinstance(model, dict):
            return model.get(field_name)
        value = getattr(model, field_name, None)
        if value is not None:
            return value
        model_extra = getattr(model, "model_extra", None)
        if isinstance(model_extra, dict):
            return model_extra.get(field_name)
        return None

    def _log_run(
        self,
        *,
        started_at: float,
        retries: int,
        usage: UsageTuple,
        error_code: str,
        prompt_version: str = GENERATION_PROMPT_VERSION,
        schema_version: str = GENERATION_SCHEMA_VERSION,
        temperature: int = GENERATION_TEMPERATURE,
        max_completion_tokens: int = GENERATION_MAX_COMPLETION_TOKENS,
        thinking: str = GENERATION_THINKING,
    ) -> None:
        prompt_tokens, completion_tokens, total_tokens = usage
        latency_ms = max(0, round((time.perf_counter() - started_at) * 1000))
        logger.info(
            "hy3_run model=%s prompt_version=%s schema_version=%s mode=%s "
            "temperature=%s max_completion_tokens=%s thinking=%s "
            "prompt_tokens=%s completion_tokens=%s total_tokens=%s "
            "latency_ms=%s retries=%s error_code=%s",
            self.settings.hy3_model,
            prompt_version,
            schema_version,
            self.settings.paperlens_model_mode,
            temperature,
            max_completion_tokens,
            thinking,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            latency_ms,
            retries,
            error_code,
        )
