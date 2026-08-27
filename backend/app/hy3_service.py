from __future__ import annotations

from copy import deepcopy
import json
import logging
from pathlib import Path
import time
from typing import Any, Literal

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from backend.app.models import (
    AtomicClaim,
    ClaimPolicy,
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
    render_generation_retry_prompt,
)
from backend.app.settings import Settings, settings as app_settings


logger = logging.getLogger("uvicorn.error.paperlens.hy3_service")

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
GENERATION_MAX_COMPLETION_TOKENS = 16384
GENERATION_THINKING = "disabled"
DEEP_AUDIT_TEMPERATURE = 0
DEEP_AUDIT_MAX_COMPLETION_TOKENS = 4096
DEEP_AUDIT_THINKING = "disabled"
SAFE_DIAGNOSTIC_MAX_LENGTH = 512
_DIAGNOSTIC_TRUNCATION_MARKER = "<truncated>"
_SAFE_DIAGNOSTIC_INTEGER_MAX = 999_999_999_999_999_999
_SAFE_DIAGNOSTIC_FALLBACK_MESSAGE = (
    "hy3_diagnostic_fallback operation=unknown attempt=null "
    "completion_tokens=null configured_completion_limit=null "
    "completion_limit_reached=false finish_reason=unknown "
    "validation_boundary=none error_code=null retry_count=null "
    "validation_error_count=null "
    "validation_error_type=other_validation_error "
    "validation_location=<truncated>"
)


UsageTuple = tuple[int | None, int | None, int | None]
SemanticPair = tuple[AtomicClaim, EvidenceRecord]
SemanticPairKey = tuple[str, str]
Hy3ValidationBoundary = Literal[
    "provider_unavailable",
    "content_missing",
    "json_invalid",
    "generated_bundle_schema_invalid",
    "claim_policy_invalid",
    "deep_audit_schema_invalid",
    "semantic_pair_invalid",
    "risk_category_coverage_invalid",
    "none",
]
SafeValidationErrorType = Literal[
    "json_invalid",
    "missing",
    "extra_forbidden",
    "enum",
    "literal_error",
    "value_error",
    "other_validation_error",
    "none",
]
SafeFinishReason = Literal[
    "stop",
    "length",
    "content_filter",
    "tool_calls",
    "missing",
    "unknown",
]
_SAFE_FINISH_REASONS = frozenset({
    "stop",
    "length",
    "content_filter",
    "tool_calls",
})
_SAFE_LOG_FINISH_REASONS = _SAFE_FINISH_REASONS | {"missing", "unknown"}
_SAFE_VALIDATION_BOUNDARIES = frozenset({
    "provider_unavailable",
    "content_missing",
    "json_invalid",
    "generated_bundle_schema_invalid",
    "claim_policy_invalid",
    "deep_audit_schema_invalid",
    "semantic_pair_invalid",
    "risk_category_coverage_invalid",
    "none",
})
_SAFE_DIAGNOSTIC_ERROR_CODES = frozenset({
    "NONE",
    "HY3_UNAVAILABLE",
    "SCHEMA_INVALID",
    "AUDIT_INCOMPLETE",
})
_SAFE_VALIDATION_ERROR_MESSAGES = {
    "json_invalid": "JSON text is invalid.",
    "missing": "Required field is missing.",
    "extra_forbidden": "Extra field is not permitted.",
    "enum": "Value is not an allowed enum member.",
    "literal_error": "Value is not an allowed literal.",
    "value_error": "Value failed validation.",
}
_DEFAULT_SAFE_VALIDATION_ERROR_MESSAGE = "Value failed validation."
_SAFE_VALIDATION_ERROR_TYPES = frozenset(
    _SAFE_VALIDATION_ERROR_MESSAGES
)


def build_safe_diagnostic_message(
    message_prefix: str,
    validation_location: str,
) -> str:
    if not isinstance(message_prefix, str):
        return _SAFE_DIAGNOSTIC_FALLBACK_MESSAGE
    if not isinstance(validation_location, str):
        validation_location = "none"
    available_location_length = (
        SAFE_DIAGNOSTIC_MAX_LENGTH - len(message_prefix)
    )
    if available_location_length < len(_DIAGNOSTIC_TRUNCATION_MARKER):
        return _SAFE_DIAGNOSTIC_FALLBACK_MESSAGE
    if len(validation_location) > available_location_length:
        preserved_length = (
            available_location_length
            - len(_DIAGNOSTIC_TRUNCATION_MARKER)
        )
        validation_location = (
            validation_location[:preserved_length]
            + _DIAGNOSTIC_TRUNCATION_MARKER
        )
    message = message_prefix + validation_location
    if len(message) > SAFE_DIAGNOSTIC_MAX_LENGTH:
        return _SAFE_DIAGNOSTIC_FALLBACK_MESSAGE
    return message


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
        validation_boundary: Hy3ValidationBoundary = "none",
        validation_error_count: int = 0,
        validation_error_type: SafeValidationErrorType = "none",
        validation_location: str = "none",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.field_error_summary = field_error_summary
        self.retries = retries
        self.usage = usage
        self.validation_boundary = validation_boundary
        self.validation_error_count = validation_error_count
        self.validation_error_type = validation_error_type
        self.validation_location = validation_location


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
        claim_policy: ClaimPolicy,
        paper_metadata: dict[str, Any],
        source_blocks: list[SourceBlock],
    ) -> GeneratedBundle:
        started_at = time.perf_counter()
        usage: UsageTuple = (None, None, None)
        retries = 0
        try:
            if self.settings.paperlens_model_mode == "mock":
                bundle = self._validate_generated_bundle(
                    self._load_mock_response(),
                    claim_policy,
                )
            else:
                self._require_live_config()
                prompt = self._build_generation_prompt(
                    claim_policy=claim_policy,
                    paper_metadata=paper_metadata,
                    source_blocks=source_blocks,
                )
                bundle, retries, usage = self._generate_live(
                    prompt,
                    claim_policy,
                )
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
        claim_policy: ClaimPolicy,
    ) -> tuple[GeneratedBundle, int, UsageTuple]:
        field_error_summaries: list[tuple[int, str]] = []
        cumulative_usage: UsageTuple | None = None
        for attempt in range(self.settings.hy3_max_retries + 1):
            prompt = original_prompt
            if field_error_summaries:
                prompt += "\n\n" + render_generation_retry_prompt(
                    validation_summaries=field_error_summaries,
                )

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
                self._log_generation_attempt(
                    attempt=attempt,
                    completion_tokens=None,
                    finish_reason="missing",
                    validation_boundary="provider_unavailable",
                    error_code="HY3_UNAVAILABLE",
                )
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

            attempt_usage = self._extract_usage(response)
            cumulative_usage = self._accumulate_usage(
                cumulative_usage,
                attempt_usage,
            )
            raw_response = self._extract_response_content(response)
            try:
                bundle = self._validate_generated_bundle(
                    raw_response,
                    claim_policy,
                )
            except Hy3ServiceError as exc:
                self._log_generation_attempt(
                    attempt=attempt,
                    completion_tokens=self._safe_completion_tokens(
                        attempt_usage[1]
                    ),
                    finish_reason=self._safe_finish_reason(response),
                    validation_boundary=exc.validation_boundary,
                    error_code=exc.error_code,
                    validation_error_count=exc.validation_error_count,
                    validation_error_type=exc.validation_error_type,
                    validation_location=exc.validation_location,
                )
                exc.retries = attempt
                exc.usage = cumulative_usage
                if attempt >= self.settings.hy3_max_retries:
                    raise
                safe_summary = (
                    exc.field_error_summary or "$ [schema_invalid]"
                )[:800]
                field_error_summaries.append((attempt, safe_summary))
                continue

            self._log_generation_attempt(
                attempt=attempt,
                completion_tokens=self._safe_completion_tokens(
                    attempt_usage[1]
                ),
                finish_reason=self._safe_finish_reason(response),
                validation_boundary="none",
                error_code="NONE",
            )
            return bundle, attempt, cumulative_usage

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
                self._log_deep_audit_attempt(
                    attempt=attempt,
                    completion_tokens=None,
                    finish_reason="missing",
                    validation_boundary="provider_unavailable",
                    error_code="HY3_UNAVAILABLE",
                )
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

            attempt_usage = self._extract_usage(response)
            cumulative_usage = self._accumulate_usage(
                cumulative_usage,
                attempt_usage,
            )
            raw_response = self._extract_response_content(response)
            try:
                result = self._validate_deep_audit_result(
                    raw_response,
                    expected_pairs,
                )
            except Hy3ServiceError as exc:
                self._log_deep_audit_attempt(
                    attempt=attempt,
                    completion_tokens=self._safe_completion_tokens(
                        attempt_usage[1]
                    ),
                    finish_reason=self._safe_finish_reason(response),
                    validation_boundary=exc.validation_boundary,
                    error_code=exc.error_code,
                    validation_error_count=exc.validation_error_count,
                    validation_error_type=exc.validation_error_type,
                    validation_location=exc.validation_location,
                )
                exc.retries = attempt
                exc.usage = cumulative_usage
                if attempt >= self.settings.hy3_max_retries:
                    raise
                field_error_summary = (
                    exc.field_error_summary or "$ [schema_invalid]"
                )
                continue

            self._log_deep_audit_attempt(
                attempt=attempt,
                completion_tokens=self._safe_completion_tokens(
                    attempt_usage[1]
                ),
                finish_reason=self._safe_finish_reason(response),
                validation_boundary="none",
                error_code="NONE",
            )
            return result, attempt, cumulative_usage

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
        claim_policy: ClaimPolicy,
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
            claim_policy=claim_policy,
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
        schema = DeepAuditResult.model_json_schema()
        risk_findings_schema = schema["properties"]["risk_findings"]
        risk_finding_schema = schema["$defs"]["RiskFinding"]
        coverage_rules: list[dict[str, Any]] = []
        for category in RiskCategory:
            constrained_finding = deepcopy(risk_finding_schema)
            constrained_finding["properties"]["category"] = {
                "const": category.value,
            }
            coverage_rules.append(
                {
                    "contains": constrained_finding,
                    "minContains": 1,
                    "maxContains": 1,
                }
            )
        risk_findings_schema.update(
            {
                "minItems": len(RiskCategory),
                "maxItems": len(RiskCategory),
                "allOf": coverage_rules,
            }
        )
        return {
            "type": "json_schema",
            "json_schema": {
                "name": DEEP_AUDIT_SCHEMA_NAME,
                "strict": True,
                "schema": schema,
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
    def validate_claim_policy(
        bundle: GeneratedBundle,
        claim_policy: ClaimPolicy,
    ) -> GeneratedBundle:
        actual_count = len(bundle.claims)
        if claim_policy == "required":
            policy_satisfied = actual_count >= 1
            expected_count = "at_least_1"
        else:
            policy_satisfied = actual_count == 0
            expected_count = "0"
        if not policy_satisfied:
            raise Hy3ServiceError(
                "AUDIT_INCOMPLETE",
                "The generated claims did not satisfy the requested claim policy.",
                retryable=True,
                field_error_summary=(
                    "claims [claim_policy_mismatch]: "
                    f"claim_policy={claim_policy} "
                    f"expected_count={expected_count} "
                    f"actual_count={actual_count}"
                ),
                validation_boundary="claim_policy_invalid",
            )
        return bundle

    @staticmethod
    def _validate_generated_bundle(
        raw_response: Any,
        claim_policy: ClaimPolicy,
    ) -> GeneratedBundle:
        if not isinstance(raw_response, (str, bytes, bytearray)):
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not contain JSON text.",
                retryable=False,
                field_error_summary=(
                    "$ [json_type]: response content must be JSON text"
                ),
                validation_boundary=(
                    "content_missing"
                    if raw_response is None
                    else "json_invalid"
                ),
            )
        try:
            bundle = GeneratedBundle.model_validate_json(raw_response)
        except ValidationError as exc:
            (
                validation_error_count,
                validation_error_type,
                validation_location,
            ) = Hy3Service._validation_error_diagnostic(exc)
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not match GeneratedBundle.",
                retryable=False,
                field_error_summary=Hy3Service._field_error_summary(exc),
                validation_boundary=(
                    Hy3Service._generated_bundle_validation_boundary(exc)
                ),
                validation_error_count=validation_error_count,
                validation_error_type=validation_error_type,
                validation_location=validation_location,
            ) from exc

        return Hy3Service.validate_claim_policy(bundle, claim_policy)

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
                validation_boundary=(
                    "content_missing"
                    if raw_response is None
                    else "json_invalid"
                ),
            )
        try:
            result = DeepAuditResult.model_validate_json(raw_response)
        except ValidationError as exc:
            (
                validation_error_count,
                validation_error_type,
                validation_location,
            ) = Hy3Service._validation_error_diagnostic(exc)
            raise Hy3ServiceError(
                "SCHEMA_INVALID",
                "The model response did not match DeepAuditResult v2.",
                retryable=False,
                field_error_summary=Hy3Service._field_error_summary(exc),
                validation_boundary=(
                    Hy3Service._deep_audit_validation_boundary(exc)
                ),
                validation_error_count=validation_error_count,
                validation_error_type=validation_error_type,
                validation_location=validation_location,
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
                validation_boundary="semantic_pair_invalid",
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
                validation_boundary="risk_category_coverage_invalid",
            )
        return result

    @staticmethod
    def _safe_validation_location(
        location: Any,
        error_type: str,
    ) -> str:
        if not isinstance(location, (tuple, list)):
            return "<location>"
        if not location:
            return "$"

        safe_segments: list[str] = []
        last_index = len(location) - 1
        for index, segment in enumerate(location):
            if error_type == "extra_forbidden" and index == last_index:
                safe_segments.append("<extra_field>")
            elif isinstance(segment, str) and segment:
                safe_segments.append(segment)
            elif type(segment) is int and segment >= 0:
                safe_segments.append(str(segment))
            else:
                safe_segments.append("<location_segment>")
        return ".".join(safe_segments)

    @staticmethod
    def _field_error_summary(error: ValidationError) -> str:
        summaries: list[str] = []
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:8]:
            raw_error_type = str(item.get("type", "validation_error"))
            error_type = Hy3Service._safe_validation_error_type(
                raw_error_type
            )
            location = Hy3Service._safe_validation_location(
                item.get("loc", ()),
                raw_error_type,
            )
            message = Hy3Service._safe_validation_error_message(
                raw_error_type,
                location,
            )
            summaries.append(f"{location} [{error_type}]: {message}")
        summary = "; ".join(summaries) or "$ [schema_invalid]"
        return summary[:800]

    @staticmethod
    def _safe_validation_error_message(
        error_type: str,
        location: str,
    ) -> str:
        location_segments = location.split(".")
        if (
            error_type == "extra_forbidden"
            and len(location_segments) == 3
            and location_segments[0] == "risk_findings"
            and location_segments[2] == "<extra_field>"
            and location_segments[1]
            and all(
                "0" <= character <= "9"
                for character in location_segments[1]
            )
            and (
                location_segments[1] == "0"
                or not location_segments[1].startswith("0")
            )
        ):
            return (
                "RiskFinding objects may contain only category, status, "
                "locations, reason, and remediation. RiskLocation objects "
                "may contain only location_type, sentence_id, and "
                "evidence_excerpt; evidence_excerpt must appear only inside "
                "locations."
            )
        if error_type == "value_error" and location == "document":
            return (
                "Document must contain each required section exactly once "
                "and use globally unique sentence identifiers."
            )
        if error_type == "value_error" and location.startswith("claims."):
            return (
                "Auditable claims require 1 to 3 candidate block identifiers, "
                "and candidate quotes must be null or non-empty."
            )
        return _SAFE_VALIDATION_ERROR_MESSAGES.get(
            error_type,
            _DEFAULT_SAFE_VALIDATION_ERROR_MESSAGE,
        )

    @staticmethod
    def _safe_validation_error_type(
        error_type: str,
    ) -> SafeValidationErrorType:
        if error_type in _SAFE_VALIDATION_ERROR_TYPES:
            return error_type  # type: ignore[return-value]
        return "other_validation_error"

    @staticmethod
    def _validation_error_diagnostic(
        error: ValidationError,
    ) -> tuple[int, SafeValidationErrorType, str]:
        items = error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        if not items:
            return (0, "none", "none")
        first = items[0]
        raw_error_type = str(first.get("type", "validation_error"))
        return (
            len(items),
            Hy3Service._safe_validation_error_type(raw_error_type),
            Hy3Service._safe_validation_location(
                first.get("loc", ()),
                raw_error_type,
            ),
        )

    @staticmethod
    def _generated_bundle_validation_boundary(
        error: ValidationError,
    ) -> Hy3ValidationBoundary:
        if any(
            item.get("type") == "json_invalid"
            for item in error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        ):
            return "json_invalid"
        return "generated_bundle_schema_invalid"

    @staticmethod
    def _deep_audit_validation_boundary(
        error: ValidationError,
    ) -> Hy3ValidationBoundary:
        if any(
            item.get("type") == "json_invalid"
            for item in error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        ):
            return "json_invalid"
        return "deep_audit_schema_invalid"

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
            Hy3Service._safe_diagnostic_integer(
                getattr(usage, "prompt_tokens", None)
            ),
            Hy3Service._safe_diagnostic_integer(
                getattr(usage, "completion_tokens", None)
            ),
            Hy3Service._safe_diagnostic_integer(
                getattr(usage, "total_tokens", None)
            ),
        )

    @staticmethod
    def _safe_finish_reason(response: Any) -> SafeFinishReason:
        choices = getattr(response, "choices", None)
        if not choices:
            return "missing"
        finish_reason = getattr(choices[0], "finish_reason", None)
        if finish_reason is None:
            return "missing"
        if finish_reason in _SAFE_FINISH_REASONS:
            return finish_reason
        return "unknown"

    @staticmethod
    def _safe_completion_tokens(value: Any) -> int | None:
        return Hy3Service._safe_diagnostic_integer(value)

    @staticmethod
    def _safe_diagnostic_integer(value: Any) -> int | None:
        if (
            type(value) is not int
            or value < 0
            or value > _SAFE_DIAGNOSTIC_INTEGER_MAX
        ):
            return None
        return value

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
            safe_previous = Hy3Service._safe_diagnostic_integer(previous)
            safe_current = Hy3Service._safe_diagnostic_integer(current)
            if safe_previous is None or safe_current is None:
                return None
            return Hy3Service._safe_diagnostic_integer(
                safe_previous + safe_current
            )

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

    @staticmethod
    def _log_generation_attempt(
        *,
        attempt: int,
        completion_tokens: int | None,
        finish_reason: SafeFinishReason,
        validation_boundary: Hy3ValidationBoundary,
        error_code: str,
        validation_error_count: int = 0,
        validation_error_type: SafeValidationErrorType = "none",
        validation_location: str = "none",
    ) -> None:
        Hy3Service._log_attempt(
            event_name="hy3_generation_attempt",
            operation="generation",
            attempt=attempt,
            completion_tokens=completion_tokens,
            configured_completion_limit=GENERATION_MAX_COMPLETION_TOKENS,
            finish_reason=finish_reason,
            validation_boundary=validation_boundary,
            error_code=error_code,
            validation_error_count=validation_error_count,
            validation_error_type=validation_error_type,
            validation_location=validation_location,
        )

    @staticmethod
    def _log_deep_audit_attempt(
        *,
        attempt: int,
        completion_tokens: int | None,
        finish_reason: SafeFinishReason,
        validation_boundary: Hy3ValidationBoundary,
        error_code: str,
        validation_error_count: int = 0,
        validation_error_type: SafeValidationErrorType = "none",
        validation_location: str = "none",
    ) -> None:
        Hy3Service._log_attempt(
            event_name="hy3_deep_audit_attempt",
            operation="deep_audit",
            attempt=attempt,
            completion_tokens=completion_tokens,
            configured_completion_limit=DEEP_AUDIT_MAX_COMPLETION_TOKENS,
            finish_reason=finish_reason,
            validation_boundary=validation_boundary,
            error_code=error_code,
            validation_error_count=validation_error_count,
            validation_error_type=validation_error_type,
            validation_location=validation_location,
        )

    @staticmethod
    def _log_attempt(
        *,
        event_name: Literal[
            "hy3_generation_attempt",
            "hy3_deep_audit_attempt",
        ],
        operation: Literal["generation", "deep_audit"],
        attempt: int,
        completion_tokens: int | None,
        configured_completion_limit: int,
        finish_reason: SafeFinishReason,
        validation_boundary: Hy3ValidationBoundary,
        error_code: str,
        validation_error_count: int,
        validation_error_type: SafeValidationErrorType,
        validation_location: str,
    ) -> None:
        safe_attempt = Hy3Service._safe_diagnostic_integer(attempt)
        safe_completion_tokens = Hy3Service._safe_diagnostic_integer(
            completion_tokens
        )
        safe_configured_limit = Hy3Service._safe_diagnostic_integer(
            configured_completion_limit
        )
        safe_validation_error_count = Hy3Service._safe_diagnostic_integer(
            validation_error_count
        )
        safe_finish_reason = (
            finish_reason
            if isinstance(finish_reason, str)
            and finish_reason in _SAFE_LOG_FINISH_REASONS
            else "unknown"
        )
        safe_validation_boundary = (
            validation_boundary
            if isinstance(validation_boundary, str)
            and validation_boundary in _SAFE_VALIDATION_BOUNDARIES
            else "none"
        )
        safe_error_code = (
            error_code
            if isinstance(error_code, str)
            and error_code in _SAFE_DIAGNOSTIC_ERROR_CODES
            else "null"
        )
        safe_validation_error_type = (
            validation_error_type
            if isinstance(validation_error_type, str)
            and validation_error_type
            in _SAFE_VALIDATION_ERROR_TYPES | {"none", "other_validation_error"}
            else "other_validation_error"
        )
        safe_validation_location = (
            validation_location
            if isinstance(validation_location, str)
            else "none"
        )
        completion_limit_reached = (
            safe_completion_tokens is not None
            and safe_configured_limit is not None
            and safe_completion_tokens >= safe_configured_limit
        )
        message_prefix = (
            f"{event_name} operation={operation} "
            "attempt="
            f"{safe_attempt if safe_attempt is not None else 'null'} "
            "completion_tokens="
            f"{safe_completion_tokens if safe_completion_tokens is not None else 'null'} "
            "configured_completion_limit="
            f"{safe_configured_limit if safe_configured_limit is not None else 'null'} "
            "completion_limit_reached="
            f"{'true' if completion_limit_reached else 'false'} "
            f"finish_reason={safe_finish_reason} "
            f"validation_boundary={safe_validation_boundary} "
            f"error_code={safe_error_code} retry_count="
            f"{safe_attempt if safe_attempt is not None else 'null'} "
            "validation_error_count="
            f"{safe_validation_error_count if safe_validation_error_count is not None else 'null'} "
            f"validation_error_type={safe_validation_error_type} "
            "validation_location="
        )
        logger.info(
            "%s",
            build_safe_diagnostic_message(
                message_prefix,
                safe_validation_location,
            ),
        )

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
        prompt_tokens, completion_tokens, total_tokens = (
            self._safe_diagnostic_integer(value) for value in usage
        )
        latency_ms = self._safe_diagnostic_integer(
            max(0, round((time.perf_counter() - started_at) * 1000))
        )
        safe_retries = self._safe_diagnostic_integer(retries)
        safe_temperature = self._safe_diagnostic_integer(temperature)
        safe_max_completion_tokens = self._safe_diagnostic_integer(
            max_completion_tokens
        )
        logger.info(
            "hy3_run model=%s prompt_version=%s schema_version=%s mode=%s "
            "temperature=%s max_completion_tokens=%s thinking=%s "
            "prompt_tokens=%s completion_tokens=%s total_tokens=%s "
            "latency_ms=%s retries=%s error_code=%s",
            self.settings.hy3_model,
            prompt_version,
            schema_version,
            self.settings.paperlens_model_mode,
            safe_temperature,
            safe_max_completion_tokens,
            thinking,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            latency_ms,
            safe_retries,
            error_code,
        )
