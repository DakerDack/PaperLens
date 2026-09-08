from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.audit_service import AuditService, AuditServiceError
from backend.app.hy3_service import Hy3Service, Hy3ServiceError
from backend.app import hy3_service as hy3_contract
from backend.app.models import (
    CORE_DIMENSION_GATES,
    ComplianceContext,
    DeepAuditResult,
    DeepAuditResultV2,
    DIMENSION_WEIGHTS,
    EditPatch,
    GeneratedBundle,
    SentenceClaimRegenerationResult,
    SourceBlock,
    dimension_level_points,
)
from backend.app.prompts import (
    CLAIM_REGENERATION_PROMPT_VERSION,
    DEEP_AUDIT_PROMPT_VERSION,
    DEEP_AUDIT_SCHEMA_VERSION,
    REVISION_PROMPT_VERSION,
    REVISION_SCHEMA_VERSION,
    SENTENCE_CLAIMS_PROMPT_VERSION,
    SENTENCE_CLAIMS_SCHEMA_VERSION,
)


SUPPORTED_MODES = ("smoke", "calibrate", "final", "stability")
RESULT_VERSION = "paperlens-eval-result-v1"
EVALUATION_METHOD_VERSION = "paperlens-stage7-method-v2"
ResultStatus = Literal["succeeded", "failed", "timeout", "unsupported"]
_REQUIRED_RESULT_FIELDS = (
    "result_version",
    "case_id",
    "run_index",
    "mode",
    "status",
    "error_code",
    "model",
    "prompt_version",
    "schema_version",
    "data_version",
    "code_version",
    "provider_calls",
    "usage",
    "metrics",
    "elapsed_ms",
    "recorded_at",
)
_ALLOWED_RESULT_STATUSES = frozenset(
    ("succeeded", "failed", "timeout", "unsupported")
)

_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_FORBIDDEN_METRIC_KEYS = (
    "api_key",
    "document",
    "paper_text",
    "prompt",
    "quote",
    "raw",
    "reason",
    "remediation",
    "source_text",
)
_MAX_METRICS_JSON_BYTES = 32_768
_MAX_METRIC_STRING_LENGTH = 160
_PLANNING_INPUT_TOKENS_PER_REQUEST = 8_000
_PLANNING_COMPLETION_TOKENS_PER_REQUEST = 4_096
_PLANNING_MAX_RETRIES = 2
_HY3_INPUT_CNY_PER_MILLION_TOKENS = 1.0
_HY3_OUTPUT_CNY_PER_MILLION_TOKENS = 4.0
_EVALUATION_LOCK_TIMEOUT_SECONDS = 3600.0
_EVALUATION_LOCK_POLL_SECONDS = 0.05
FREEZE_VERSION = "paperlens-stage7-freeze-v2"
STAGE7_ACCEPTANCE_TARGETS = {
    "quality_strict_order_min": 4,
    "quality_pairwise_min": 13,
    "severe_error_detection_min": 0.90,
    "key_citation_accuracy_min": 0.90,
    "key_citation_completeness_min": 0.80,
    "stability_mean_score_sd_max": 5.0,
    "stability_dimension_consistency_min": 0.80,
    "attack_detection_min": 13,
    "clean_false_positives_max": 1,
    "revision_error_resolution_min": 0.70,
    "revision_new_severe_errors_max": 0,
    "revision_irrelevant_change_rate_max": 0.05,
}
DEFAULT_FREEZE_PATH = _PROJECT_ROOT / "reports" / "stage7_frozen_config.json"
DEFAULT_RESULT_PATHS = {
    mode: _PROJECT_ROOT / "reports" / f"{mode}_results.jsonl"
    for mode in SUPPORTED_MODES
}


class EvaluationLockError(RuntimeError):
    """The process could not safely acquire the per-output evaluation lock."""


@contextmanager
def _evaluation_run_lock(output_path: Path):
    """Hold a crash-releasable OS lock across one complete evaluation run."""
    lock_path = Path(f"{output_path.resolve()}.lock")
    handle = None
    acquired = False
    try:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            deadline = time.monotonic() + _EVALUATION_LOCK_TIMEOUT_SECONDS
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (BlockingIOError, OSError) as exc:
                    if time.monotonic() >= deadline:
                        raise EvaluationLockError("EVAL_LOCK_UNAVAILABLE") from exc
                    time.sleep(_EVALUATION_LOCK_POLL_SECONDS)
        except EvaluationLockError:
            raise
        except OSError as exc:
            raise EvaluationLockError("EVAL_LOCK_UNAVAILABLE") from exc

        yield
    finally:
        if handle is not None:
            if acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    run_index: int
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.case_id or len(self.case_id) > 128:
            raise ValueError("case_id must contain 1 to 128 characters")
        if self.run_index < 0:
            raise ValueError("run_index must be non-negative")


@dataclass(frozen=True)
class VersionInfo:
    model: str
    prompt_version: str
    schema_version: str
    data_version: str
    code_version: str

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not value or len(value) > 128:
                raise ValueError(f"{name} must contain 1 to 128 characters")


@dataclass(frozen=True)
class RunSummary:
    requested: int
    skipped: int
    appended: int
    recovered_interrupted: int


@dataclass(frozen=True)
class ModePlan:
    mode: str
    total_slots: int
    resume_complete_slots: int
    pending_slots: int
    logical_requests_pending: int
    provider_attempts_upper: int
    input_tokens_upper: int
    completion_tokens_upper: int
    cost_cny_no_retry: float
    cost_cny_upper: float


class EvaluationCaseError(RuntimeError):
    def __init__(
        self,
        *,
        status: ResultStatus,
        error_code: str,
        provider_calls: int | None,
        usage: Mapping[str, int | None] | None = None,
    ) -> None:
        super().__init__(error_code)
        if status == "succeeded":
            raise ValueError("EvaluationCaseError cannot use succeeded status")
        _validate_error_code(error_code)
        _validate_provider_calls(provider_calls)
        self.status = status
        self.error_code = error_code
        self.provider_calls = provider_calls
        self.usage = _normalize_usage(usage)


class EvaluationTimeoutError(EvaluationCaseError):
    def __init__(
        self,
        *,
        provider_calls: int | None,
        usage: Mapping[str, int | None] | None = None,
    ) -> None:
        super().__init__(
            status="timeout",
            error_code="TIMEOUT",
            provider_calls=provider_calls,
            usage=usage,
        )


class UnsupportedCaseError(EvaluationCaseError):
    def __init__(self) -> None:
        super().__init__(
            status="unsupported",
            error_code="UNSUPPORTED_CASE",
            provider_calls=0,
            usage=None,
        )


def pending_path_for(output_path: Path) -> Path:
    return Path(f"{output_path}.pending.jsonl")


def load_mode_cases(mode: str) -> list[EvaluationCase]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported evaluation mode: {mode}")
    if mode == "smoke":
        manifest = _load_smoke_manifest()
        cases = manifest.get("cases")
        if not isinstance(cases, list):
            raise ValueError("smoke manifest cases must be an array")
        return [
            EvaluationCase(
                case_id=_required_string(item, "case_id"),
                run_index=_required_non_negative_integer(item, "run_index"),
                payload=dict(item),
            )
            for item in cases
            if isinstance(item, dict)
        ]

    manifest = _load_live_manifest()
    papers = manifest["papers"]
    if mode == "calibrate":
        return _quality_cases(
            paper for paper in papers if paper["split"] == "development"
        )
    if mode == "final":
        cases = _quality_cases(
            paper for paper in papers if paper["split"] == "holdout"
        )
        for attack in manifest["attacks"]:
            for pair_role in ("clean", "attack"):
                attack_id = attack["attack_id"]
                cases.append(
                    EvaluationCase(
                        case_id=f"attack:{attack_id}:{pair_role}",
                        run_index=0,
                        payload={
                            "case_group": "attack",
                            "attack_id": attack_id,
                            "attack_type": attack["attack_type"],
                            "paper_id": attack["paper_id"],
                            "pair_role": pair_role,
                        },
                    )
                )
        for reference in manifest["revision_case_refs"]:
            paper_id, quality_label = _split_case_reference(reference)
            cases.append(
                EvaluationCase(
                    case_id=f"revision:{reference}",
                    run_index=0,
                    payload={
                        "case_group": "revision",
                        "paper_id": paper_id,
                        "quality_label": quality_label,
                    },
                )
            )
        return cases

    cases = []
    for reference in manifest["stability_case_refs"]:
        for run_index in range(3):
            cases.append(
                EvaluationCase(
                    case_id=f"stability:{reference}",
                    run_index=run_index,
                    payload={
                        "case_group": "stability",
                        "stability_output_id": reference,
                        "source_case_ref": reference,
                    },
                )
            )
    return cases


def build_mode_plan(
    *, mode: str, output_path: Path, diagnostic_plan: dict[str, Any] | None = None,
) -> ModePlan:
    if mode == "smoke":
        raise ValueError("smoke is a zero-cost reference replay and needs no Live plan")
    if diagnostic_plan is not None and mode != "calibrate":
        raise ValueError("invalid context diagnostic")
    cases = (
        context_diagnostic_cases(diagnostic_plan)
        if diagnostic_plan is not None else load_mode_cases(mode)
    )
    planned_keys = {(case.case_id, case.run_index) for case in cases}
    completed_keys = _load_existing_keys(output_path=output_path, mode=mode)
    pending_keys = _load_pending_keys(
        pending_path=pending_path_for(output_path),
        mode=mode,
    )
    _validate_context_resume(output_path, mode, diagnostic_plan)
    resume_keys = completed_keys | pending_keys
    if not resume_keys <= planned_keys:
        raise ValueError("resume data contains keys outside the frozen mode plan")
    remaining = [
        case
        for case in cases
        if (case.case_id, case.run_index) not in resume_keys
    ]
    logical_requests = sum(_logical_request_count(case) for case in remaining)
    provider_attempts_upper = logical_requests * (_PLANNING_MAX_RETRIES + 1)
    input_tokens_upper = (
        provider_attempts_upper * _PLANNING_INPUT_TOKENS_PER_REQUEST
    )
    completion_tokens_upper = (
        provider_attempts_upper * _PLANNING_COMPLETION_TOKENS_PER_REQUEST
    )
    return ModePlan(
        mode=mode,
        total_slots=len(cases),
        resume_complete_slots=len(resume_keys),
        pending_slots=len(remaining),
        logical_requests_pending=logical_requests,
        provider_attempts_upper=provider_attempts_upper,
        input_tokens_upper=input_tokens_upper,
        completion_tokens_upper=completion_tokens_upper,
        cost_cny_no_retry=_estimate_cost_cny(
            input_tokens=(
                logical_requests * _PLANNING_INPUT_TOKENS_PER_REQUEST
            ),
            completion_tokens=(
                logical_requests * _PLANNING_COMPLETION_TOKENS_PER_REQUEST
            ),
        ),
        cost_cny_upper=_estimate_cost_cny(
            input_tokens=input_tokens_upper,
            completion_tokens=completion_tokens_upper,
        ),
    )


CONTEXT_DIAGNOSTIC_VERSION = "paperlens-context-diagnostic-v1"
CONTEXT_DIAGNOSTIC_CASE_IDS = (
    "quality:dev-02:good",
    "quality:dev-02:medium",
    "quality:dev-02:bad",
)
CONTEXT_DIAGNOSTIC_TARGET = ("dev-02-c04", "dev-02-b01")
_CONTEXT_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_CONTEXT_CASE = re.compile(r"quality:([A-Za-z0-9][A-Za-z0-9_.-]{0,63}):(good|medium|bad)\Z")
_CONTEXT_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _sha256_json(value: Any) -> str:
    # Canonical object keys, but preserve array order and every string byte.
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _invalid_context_diagnostic() -> None:
    raise ValueError("invalid context diagnostic")


def _validate_context_plan(plan: Any) -> None:
    fields = {
        "version", "diagnostic_id", "target_claim_id", "target_block_id",
        "pair_sha256", "configuration_sha256", "versions_sha256",
        "repetitions", "contexts",
    }
    if not isinstance(plan, dict) or set(plan) != fields:
        _invalid_context_diagnostic()
    if plan["version"] != CONTEXT_DIAGNOSTIC_VERSION or type(plan["repetitions"]) is not int or plan["repetitions"] != 3:
        _invalid_context_diagnostic()
    if (plan["target_claim_id"], plan["target_block_id"]) != CONTEXT_DIAGNOSTIC_TARGET:
        _invalid_context_diagnostic()
    for field in ("diagnostic_id", "target_claim_id", "target_block_id"):
        if not isinstance(plan[field], str) or not _CONTEXT_IDENTIFIER.fullmatch(plan[field]):
            _invalid_context_diagnostic()
    for field in ("pair_sha256", "configuration_sha256", "versions_sha256"):
        if not isinstance(plan[field], str) or not _CONTEXT_HASH.fullmatch(plan[field]):
            _invalid_context_diagnostic()
    contexts = plan["contexts"]
    if not isinstance(contexts, list) or len(contexts) != 3:
        _invalid_context_diagnostic()
    papers, requests = set(), set()
    for context, expected_case_id in zip(contexts, CONTEXT_DIAGNOSTIC_CASE_IDS, strict=True):
        if not isinstance(context, dict) or set(context) != {"case_id", "request_sha256"}:
            _invalid_context_diagnostic()
        case_id, request_hash = context["case_id"], context["request_sha256"]
        match = _CONTEXT_CASE.fullmatch(case_id) if isinstance(case_id, str) else None
        if case_id != expected_case_id or match is None or not isinstance(request_hash, str) or not _CONTEXT_HASH.fullmatch(request_hash):
            _invalid_context_diagnostic()
        papers.add(match[1])
        requests.add(request_hash)
    if len(papers) != 1 or len(requests) != 3:
        _invalid_context_diagnostic()


def context_diagnostic_cases(plan: dict[str, Any]) -> list[EvaluationCase]:
    _validate_context_plan(plan)
    cases = []
    for run_index in range(3):
        for position in range(3):
            case_id = plan["contexts"][(position + run_index) % 3]["case_id"]
            _, paper_id, quality_label = case_id.split(":")
            cases.append(EvaluationCase(case_id=case_id, run_index=run_index, payload={
                "case_group": "quality", "paper_id": paper_id,
                "quality_label": quality_label,
            }))
    return cases


def _context_configuration_sha256(service: Any) -> str:
    settings = service.settings
    return _sha256_json({
        "code_version": _code_version(), "model": settings.hy3_model,
        "endpoint": settings.hy3_base_url, "timeout": settings.hy3_timeout_seconds,
        "max_retries": settings.hy3_max_retries,
    })


def _context_input_hashes(service: Any, document: Any, pairs: list[Any], claim_id: str, block_id: str) -> tuple[str, str]:
    selected = [(c, e) for c, e in pairs if c.claim_id == claim_id and e.block_id == block_id and e.quote_verified]
    if len(selected) != 1:
        _invalid_context_diagnostic()
    claim, evidence = selected[0]
    pair_hash = _sha256_json({"claim": claim.model_dump(mode="json"), "evidence": evidence.model_dump(mode="json")})
    # Use the production serializers. Never persist this transient request body.
    request = {
        "model": service.settings.hy3_model,
        "messages": [
            {"role": "system", "content": hy3_contract.DEEP_AUDIT_SYSTEM_PROMPT},
            {"role": "user", "content": hy3_contract.Hy3Service._build_deep_audit_prompt(document, pairs)},
        ],
        "response_format": hy3_contract.Hy3Service._deep_audit_response_format(),
        "stream": False, "temperature": hy3_contract.DEEP_AUDIT_TEMPERATURE,
        "max_completion_tokens": hy3_contract.DEEP_AUDIT_MAX_COMPLETION_TOKENS,
        "extra_body": {"thinking": {"type": hy3_contract.DEEP_AUDIT_THINKING}},
    }
    return pair_hash, _sha256_json(request)


def build_context_diagnostic_plan(
    *, case_ids: list[str], target_claim_id: str, target_block_id: str,
    diagnostic_id: str, repetitions: int, hy3_service: Any, versions: VersionInfo,
) -> dict[str, Any]:
    if (
        not isinstance(case_ids, list)
        or tuple(case_ids) != CONTEXT_DIAGNOSTIC_CASE_IDS
        or (target_claim_id, target_block_id) != CONTEXT_DIAGNOSTIC_TARGET
        or type(repetitions) is not int
        or repetitions != 3
    ):
        _invalid_context_diagnostic()
    available = {case.case_id: case for case in load_mode_cases("calibrate")}
    if not isinstance(case_ids, list) or len(case_ids) != 3 or any(not isinstance(c, str) or c not in available for c in case_ids) or len(set(case_ids)) != 3:
        _invalid_context_diagnostic()
    selected = [available[c] for c in case_ids]
    if len({c.payload["paper_id"] for c in selected}) != 1 or {c.payload["quality_label"] for c in selected} != {"good", "medium", "bad"}:
        _invalid_context_diagnostic()
    if versions.model != hy3_service.settings.hy3_model or versions.prompt_version != DEEP_AUDIT_PROMPT_VERSION or versions.schema_version != DEEP_AUDIT_SCHEMA_VERSION or versions.code_version != _code_version():
        _invalid_context_diagnostic()
    contexts, pair_hashes = [], set()
    for label in ("good", "medium", "bad"):
        case = next(c for c in selected if c.payload["quality_label"] == label)
        sources, bundle = materialize_live_case(case)
        audit = AuditService()
        records, _ = audit.quick_check(bundle, sources)
        pair_hash, request_hash = _context_input_hashes(
            hy3_service, bundle.document, audit.semantic_pairs(bundle, records),
            target_claim_id, target_block_id,
        )
        pair_hashes.add(pair_hash)
        contexts.append({"case_id": case.case_id, "request_sha256": request_hash})
    if len(pair_hashes) != 1:
        _invalid_context_diagnostic()
    plan = {
        "version": CONTEXT_DIAGNOSTIC_VERSION, "diagnostic_id": diagnostic_id,
        "target_claim_id": target_claim_id, "target_block_id": target_block_id,
        "pair_sha256": pair_hashes.pop(), "contexts": contexts,
        "repetitions": repetitions, "versions_sha256": _sha256_json(versions.__dict__),
        "configuration_sha256": _context_configuration_sha256(hy3_service),
    }
    _validate_context_plan(plan)
    return plan


def _context_diagnostic_metadata(plan: dict[str, Any], case: EvaluationCase) -> dict[str, Any]:
    return {
        "plan": copy.deepcopy(plan), "plan_sha256": _sha256_json(plan),
        # This is the planned digest, never evidence that dispatch was observed.
        "request_sha256": next(c["request_sha256"] for c in plan["contexts"] if c["case_id"] == case.case_id),
        "verified_first_request_sha256": None,
    }


def validate_context_diagnostic_record(record: Mapping[str, Any], *, pending: bool = False) -> dict[str, Any]:
    expected_fields = (
        {"case_id", "run_index", "mode", "context_diagnostic"}
        if pending else set(_REQUIRED_RESULT_FIELDS)
    )
    if set(record) != expected_fields:
        _invalid_context_diagnostic()
    container = record if pending else record.get("metrics")
    if not isinstance(container, dict):
        _invalid_context_diagnostic()
    metadata = container.get("context_diagnostic")
    planned_fields = {"plan", "plan_sha256", "request_sha256"}
    if not isinstance(metadata, dict) or set(metadata) not in (planned_fields, planned_fields | {"verified_first_request_sha256"}):
        _invalid_context_diagnostic()
    plan = metadata["plan"]
    _validate_context_plan(plan)
    if record.get("mode") != "calibrate" or type(record.get("run_index")) is not int:
        _invalid_context_diagnostic()
    cases = context_diagnostic_cases(plan)
    case = next((c for c in cases if (c.case_id, c.run_index) == (record.get("case_id"), record["run_index"])), None)
    if case is None:
        _invalid_context_diagnostic()
    expected = _context_diagnostic_metadata(plan, case)
    if any(metadata[field] != expected[field] for field in planned_fields):
        _invalid_context_diagnostic()
    verified = metadata.get("verified_first_request_sha256")
    if verified is not None and (pending or not isinstance(verified, str) or verified != expected["request_sha256"]):
        _invalid_context_diagnostic()
    if not pending:
        _validate_existing_result(record=record, mode="calibrate", line_number=1)
        if plan["versions_sha256"] != _sha256_json({k: record[k] for k in VersionInfo.__dataclass_fields__}):
            _invalid_context_diagnostic()
        calls = record["provider_calls"]
        if calls is not None and calls > 3:
            _invalid_context_diagnostic()
        if verified is not None and calls == 0:
            _invalid_context_diagnostic()
        if record["error_code"] == "RUN_INTERRUPTED" and (
            verified is not None or calls is not None or any(value is not None for value in record["usage"].values())
        ):
            _invalid_context_diagnostic()
        if record["status"] == "succeeded" and (record["error_code"] != "NONE" or calls not in (1, 2, 3)):
            _invalid_context_diagnostic()
        if record["status"] != "succeeded" and record["error_code"] == "NONE":
            _invalid_context_diagnostic()
    return plan


def _validate_context_resume(output_path: Path, mode: str, plan: dict[str, Any] | None) -> None:
    completed_records = []
    if plan is not None:
        _validate_context_plan(plan)
        if mode != "calibrate":
            _invalid_context_diagnostic()
    for path, pending in ((output_path, False), (pending_path_for(output_path), True)):
        if not path.exists():
            continue
        for _, record in _read_jsonl(path):
            container = record if pending else record.get("metrics", {})
            if plan is None:
                if "context_diagnostic" in container:
                    _invalid_context_diagnostic()
            elif validate_context_diagnostic_record(record, pending=pending) != plan:
                _invalid_context_diagnostic()
            elif not pending:
                completed_records.append(record)
    if plan is not None and completed_records:
        # Share the strict claim/count checks instead of allowing resume and
        # reporting to disagree. This pure in-memory path reads no freeze or
        # report files. Import lazily because build_report also imports this module.
        from eval.build_report import _summarize_context_diagnostic

        _summarize_context_diagnostic(completed_records)


class _ContextDiagnosticInputDrift(ValueError):
    def __init__(self):
        super().__init__("context diagnostic input drift")


class _ContextDiagnosticClient:
    """Guard the actual create kwargs; retries are forwarded unchanged."""

    def __init__(self, get_client: Callable[[], Any], expected_sha256: str):
        self._get_client, self._expected_sha256 = get_client, expected_sha256
        self.chat = self.completions = self
        self.verified_first_request_sha256: str | None = None

    def create(self, **kwargs):
        if self.verified_first_request_sha256 is None:
            try:
                actual_sha256 = _sha256_json(kwargs)
            except (TypeError, ValueError):
                raise _ContextDiagnosticInputDrift() from None
            if actual_sha256 != self._expected_sha256:
                raise _ContextDiagnosticInputDrift()
            # Resolve the existing client only after the local drift check.
            dispatch = self._get_client().chat.completions.create
            self.verified_first_request_sha256 = actual_sha256
        else:
            dispatch = self._get_client().chat.completions.create
        return dispatch(**kwargs)


class _ContextDiagnosticService:
    """Precheck the pair, then guard the production client's dispatch boundary."""

    def __init__(self, service: Any, plan: dict[str, Any], case: EvaluationCase):
        self.service, self.plan, self.case = service, copy.deepcopy(plan), case
        self._dispatch_client: _ContextDiagnosticClient | None = None

    def diagnostic_metadata(self) -> dict[str, Any]:
        metadata = _context_diagnostic_metadata(self.plan, self.case)
        if self._dispatch_client is not None:
            metadata["verified_first_request_sha256"] = self._dispatch_client.verified_first_request_sha256
        return metadata

    @property
    def last_run_observation(self):
        return self.service.last_run_observation

    def deep_audit(self, *, document, claim_evidence_pairs):
        pair_hash, request_hash = _context_input_hashes(
            self.service, document, claim_evidence_pairs,
            self.plan["target_claim_id"], self.plan["target_block_id"],
        )
        metadata = _context_diagnostic_metadata(self.plan, self.case)
        if pair_hash != self.plan["pair_sha256"] or request_hash != metadata["request_sha256"] or _context_configuration_sha256(self.service) != self.plan["configuration_sha256"]:
            raise _ContextDiagnosticInputDrift()
        if isinstance(self.service, hy3_contract.Hy3Service):
            # Isolate instrumentation from the caller's reusable service. The
            # original Hy3Service still builds, validates and retries requests;
            # only its client accessor is decorated on this per-evaluation copy.
            self.service = copy.copy(self.service)
            self._dispatch_client = _ContextDiagnosticClient(
                self.service._get_client, metadata["request_sha256"],
            )
            self.service._get_client = lambda: self._dispatch_client
        return self.service.deep_audit(document=document, claim_evidence_pairs=claim_evidence_pairs)


def materialize_live_case(
    case: EvaluationCase,
) -> tuple[list[SourceBlock], GeneratedBundle]:
    manifest = _load_live_manifest()
    papers = {
        paper["paper_id"]: paper
        for paper in manifest["papers"]
    }
    attacks = {
        attack["attack_id"]: attack
        for attack in manifest["attacks"]
    }
    group = case.payload.get("case_group")
    mutation: Mapping[str, Any] | None = None

    if group in {"quality", "revision"}:
        paper_id = _required_string(case.payload, "paper_id")
        quality_label = _required_string(case.payload, "quality_label")
        if quality_label != "good":
            mutation = papers[paper_id]["quality_mutations"][quality_label]
    elif group == "attack":
        attack_id = _required_string(case.payload, "attack_id")
        attack = attacks[attack_id]
        paper_id = attack["paper_id"]
        if case.payload.get("pair_role") == "attack":
            mutation = attack
        elif case.payload.get("pair_role") != "clean":
            raise ValueError("attack pair_role must be clean or attack")
    elif group == "stability":
        reference = _required_string(case.payload, "source_case_ref")
        if reference.startswith("attack:"):
            attack_id = reference.removeprefix("attack:")
            if attack_id not in attacks:
                raise ValueError("stability reference uses an unknown attack")
            mutation = attacks[attack_id]
            paper_id = mutation["paper_id"]
        else:
            paper_id, quality_label = _split_case_reference(reference)
            if quality_label != "good":
                mutation = papers[paper_id]["quality_mutations"][quality_label]
    else:
        raise ValueError("Live case uses an unsupported case_group")

    if paper_id not in papers:
        raise ValueError("Live case references an unknown paper")
    paper = papers[paper_id]
    source_blocks = [
        SourceBlock.model_validate(
            {
                "block_id": block["block_id"],
                "page_index": 0,
                "type": "text",
                "text": block["text"],
                "bbox": None,
                "reading_order": index,
                "parser": "pdfplumber",
                "parser_version": "plos-api-curated-v1",
            }
        )
        for index, block in enumerate(paper["source_blocks"])
    ]
    bundle_data = _live_bundle_data(paper)
    if mutation is not None:
        _apply_live_mutation(bundle_data, mutation)
    return source_blocks, GeneratedBundle.model_validate(bundle_data)


def evaluate_live_case(
    case: EvaluationCase,
    *,
    hy3_service: Any | None = None,
    diagnostic_plan: dict[str, Any] | None = None,
) -> dict[str, object]:
    service = hy3_service or Hy3Service()
    if diagnostic_plan is not None:
        if case not in context_diagnostic_cases(diagnostic_plan):
            _invalid_context_diagnostic()
        service = _ContextDiagnosticService(service, diagnostic_plan, case)
    audit_service = AuditService(hy3_service=service)
    observations: list[Mapping[str, Any]] = []
    source_blocks, bundle = materialize_live_case(case)
    compliance_context = ComplianceContext(
        rights_or_license_confirmed=True,
        source_disclosure_status="present",
        ai_assistance_disclosure_status="present",
        generated_content_label_applicability="not_applicable",
        generated_content_label_status="not_applicable",
    )

    def audited(
        candidate_bundle: GeneratedBundle,
    ) -> tuple[list[Any], DeepAuditResult, Any]:
        evidence_records, _ = audit_service.quick_check(
            candidate_bundle,
            source_blocks,
        )
        result, report = _call_and_capture(
            lambda: audit_service.run_deep_audit(
                candidate_bundle,
                evidence_records,
                compliance_context,
            ),
            service=service,
            observations=observations,
        )
        return evidence_records, result, report

    try:
        group = case.payload.get("case_group")
        if group != "revision":
            evidence_records, deep_result, report = audited(bundle)
            metrics = _live_report_metrics(
                case=case,
                bundle=bundle,
                source_blocks=source_blocks,
                evidence_records=evidence_records,
                deep_result=deep_result,
                report=report,
            )
            if diagnostic_plan is not None:
                metrics["context_diagnostic"] = service.diagnostic_metadata()
            return _live_evaluation_result(
                observations=observations,
                metrics=metrics,
            )

        paper_id = _required_string(case.payload, "paper_id")
        manifest = _load_live_manifest()
        paper = next(
            paper for paper in manifest["papers"] if paper["paper_id"] == paper_id
        )
        mutation = paper["quality_mutations"]["bad"]
        target_sentence_id = _required_string(mutation, "target_sentence_id")
        evidence_records, before_result, before_report = audited(bundle)
        target_sentence = _find_sentence(bundle, target_sentence_id)
        target_claims = [
            claim for claim in bundle.claims if claim.sentence_id == target_sentence_id
        ]
        target_evidence = [
            record
            for record in evidence_records
            if any(claim.claim_id == record.claim_id for claim in target_claims)
        ]
        patch = _call_and_capture(
            lambda: service.revise_sentence(
                base_version=1,
                sentence_id=target_sentence_id,
                current_text=target_sentence.text,
                evidence_records=target_evidence,
                user_instruction=(
                    "修复已发现的问题，保持有证据支持的事实、范围和限制；"
                    "只修改目标句。"
                ),
            ),
            service=service,
            observations=observations,
        )
        if not isinstance(patch, EditPatch):
            raise ValueError("revision service returned an invalid patch object")
        regenerated = _call_and_capture(
            lambda: service.regenerate_sentence_claims(
                target_sentence_id=target_sentence_id,
                accepted_after_text=patch.after_text,
                original_claims=target_claims,
                evidence_records=target_evidence,
                allowed_block_ids={block.block_id for block in source_blocks},
                reserved_claim_ids={
                    claim.claim_id
                    for claim in bundle.claims
                    if claim not in target_claims
                },
                user_instruction=(
                    "根据已接受的目标句重建其原子 claim，并保留可审计证据。"
                ),
            ),
            service=service,
            observations=observations,
        )
        if not isinstance(regenerated, SentenceClaimRegenerationResult):
            raise ValueError("claim regeneration returned an invalid result object")
        revised_bundle = _replace_revised_sentence_claims(
            bundle=bundle,
            target_sentence_id=target_sentence_id,
            after_text=patch.after_text,
            regenerated=regenerated,
        )
        after_evidence, after_result, after_report = audited(revised_bundle)
        metrics = _revision_metrics(
            case=case,
            before_bundle=bundle,
            after_bundle=revised_bundle,
            target_sentence_id=target_sentence_id,
            mutation=mutation,
            before_result=before_result,
            after_result=after_result,
            before_report=before_report,
            after_report=after_report,
            before_evidence=evidence_records,
            after_evidence=after_evidence,
        )
        return _live_evaluation_result(
            observations=observations,
            metrics=metrics,
        )
    except EvaluationCaseError:
        raise
    except _RevisionMetricsIndeterminate as exc:
        raise _evaluation_case_error(exc, observations) from None
    except (Hy3ServiceError, AuditServiceError) as exc:
        error = _evaluation_case_error(exc, observations)
        if diagnostic_plan is not None:
            error.context_diagnostic = service.diagnostic_metadata()
        raise error from None


def _call_and_capture(
    operation: Callable[[], Any],
    *,
    service: Any,
    observations: list[Mapping[str, Any]],
) -> Any:
    try:
        result = operation()
    except (Hy3ServiceError, AuditServiceError):
        observation = _read_run_observation(service)
        if observation is not None:
            observations.append(observation)
        raise
    observation = _read_run_observation(service)
    if observation is not None:
        observations.append(observation)
    return result


def _live_evaluation_result(
    *,
    observations: list[Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> dict[str, object]:
    provider_calls, usage = _aggregate_observations(observations)
    return {
        "provider_calls": provider_calls,
        "usage": usage,
        "metrics": dict(metrics),
    }


def _read_run_observation(service: Any) -> Mapping[str, Any] | None:
    observation = getattr(service, "last_run_observation", None)
    if observation is None:
        return None
    provider_calls = getattr(observation, "provider_calls", None)
    retries = getattr(observation, "retries", None)
    error_code = getattr(observation, "error_code", None)
    if (
        not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
        or provider_calls < 0
        or not isinstance(retries, int)
        or isinstance(retries, bool)
        or retries < 0
        or not isinstance(error_code, str)
        or not _ERROR_CODE_PATTERN.fullmatch(error_code)
    ):
        return None
    values: dict[str, Any] = {
        "provider_calls": provider_calls,
        "retries": retries,
        "prompt_tokens": getattr(observation, "prompt_tokens", None),
        "completion_tokens": getattr(observation, "completion_tokens", None),
        "total_tokens": getattr(observation, "total_tokens", None),
        "error_code": error_code,
    }
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = values[field]
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            return None
    return values


def _aggregate_observations(
    observations: list[Mapping[str, Any]],
) -> tuple[int | None, dict[str, int | None]]:
    if not observations:
        return None, {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
    provider_calls = sum(int(item["provider_calls"]) for item in observations)
    usage: dict[str, int | None] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [item[field] for item in observations]
        usage[field] = (
            sum(int(value) for value in values)
            if all(value is not None for value in values)
            else None
        )
    return provider_calls, usage


def _evaluation_case_error(
    exc: Exception,
    observations: list[Mapping[str, Any]],
) -> EvaluationCaseError:
    error_code = getattr(exc, "error_code", "EVALUATION_FAILED")
    if not isinstance(error_code, str) or not _ERROR_CODE_PATTERN.fullmatch(error_code):
        error_code = "EVALUATION_FAILED"
    provider_calls, usage = _aggregate_observations(observations)
    error = EvaluationCaseError(
        status="timeout" if error_code == "TIMEOUT" else "failed",
        error_code=error_code,
        provider_calls=provider_calls,
        usage=usage,
    )
    error.safe_metrics = {"evaluation_method_version": EVALUATION_METHOD_VERSION,
                          **getattr(exc, "safe_metrics", {})}
    return error


def _find_sentence(bundle: GeneratedBundle, sentence_id: str) -> Any:
    for section in bundle.document.sections:
        for sentence in section.sentences:
            if sentence.sentence_id == sentence_id:
                return sentence
    raise ValueError("revision target sentence is not present")


def _replace_revised_sentence_claims(
    *,
    bundle: GeneratedBundle,
    target_sentence_id: str,
    after_text: str,
    regenerated: SentenceClaimRegenerationResult,
) -> GeneratedBundle:
    data = bundle.model_dump(mode="python")
    target_found = False
    for section in data["document"]["sections"]:
        for sentence in section["sentences"]:
            if sentence["sentence_id"] == target_sentence_id:
                sentence["text"] = after_text
                target_found = True
    if not target_found:
        raise ValueError("revised target sentence is not present")
    data["claims"] = [
        claim
        for claim in data["claims"]
        if claim["sentence_id"] != target_sentence_id
    ] + [claim.model_dump(mode="python") for claim in regenerated.claims]
    return GeneratedBundle.model_validate(data)


def _expression_metrics(result: DeepAuditResult) -> list[dict[str, Any]]:
    return [
        {"category": finding.category.value, "status": finding.status.value,
         "sentence_ids": sorted({location.sentence_id for location in finding.locations})}
        for finding in sorted(result.expression_findings, key=lambda item: item.category.value)
    ]


def validate_expression_metrics(record: Mapping[str, Any]) -> bool:
    """Validate new method provenance; absent historical signals stay absent."""
    metrics = record.get("metrics", {})
    current = record.get("schema_version") == "deep-audit-result-v3" or (
        isinstance(metrics, dict) and "evaluation_method_version" in metrics
    )
    if not current:
        return False
    invalid = ValueError("invalid expression metrics")
    if not isinstance(metrics, dict):
        raise invalid
    if "observed_factual_alert_count" in metrics:
        count = metrics["observed_factual_alert_count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise invalid
    if "observed_factual_issue_codes" in metrics:
        codes = metrics["observed_factual_issue_codes"]
        if not isinstance(codes, list) or any(
            not isinstance(code, str) or not _ERROR_CODE_PATTERN.fullmatch(code)
            for code in codes
        ):
            raise invalid
    if record.get("status") == "succeeded" and metrics.get("evaluation_method_version") != EVALUATION_METHOD_VERSION:
        raise invalid
    if "expression_findings" not in metrics:
        if record.get("status") == "succeeded":
            raise invalid
        return False
    findings = metrics["expression_findings"]
    if not isinstance(findings, list) or len(findings) != 2:
        raise invalid
    categories = set()
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"category", "status", "sentence_ids"}:
            raise invalid
        category, status, ids = finding["category"], finding["status"], finding["sentence_ids"]
        if not isinstance(category, str) or category not in {"redundancy_or_off_topic", "unexplained_terminology"} or category in categories:
            raise invalid
        categories.add(category)
        if not isinstance(status, str) or status not in {"detected", "not_detected", "unclear"} or not isinstance(ids, list):
            raise invalid
        if any(not isinstance(sid, str) or not _CONTEXT_IDENTIFIER.fullmatch(sid) for sid in ids):
            raise invalid
        if len(ids) != len(set(ids)) or (bool(ids) != (status == "detected")):
            raise invalid
        if record.get("status") == "succeeded" and status == "unclear":
            raise invalid
    return record.get("status") == "succeeded"


def _live_report_metrics(
    *,
    case: EvaluationCase,
    bundle: GeneratedBundle,
    source_blocks: list[SourceBlock],
    evidence_records: list[Any],
    deep_result: DeepAuditResult,
    report: Any,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "provider_mode": "live",
        "evaluation_method_version": EVALUATION_METHOD_VERSION,
        "expression_findings": _expression_metrics(deep_result),
        "paper_id": case.payload.get("paper_id"),
        "case_group": case.payload.get("case_group"),
        "dimension_count": len(report.dimensions),
        "dimension_points": {
            dimension.dimension_id.value: dimension_level_points(dimension)
            for dimension in report.dimensions
        },
        "overall_score": report.overall_score,
        "core_gate_passed": report.core_gate_passed,
        "decision": report.decision.value,
        "hard_failure_count": len(report.hard_failures),
        "hard_failure_codes": report.hard_failures,
        "claim_count": len(bundle.claims),
        "verified_evidence_count": sum(
            record.quote_verified for record in evidence_records
        ),
        "rule_issue_count": sum(bool(record.rule_flags) for record in evidence_records),
    }
    key_claim_ids = {
        claim.claim_id
        for claim in bundle.claims
        if getattr(claim.importance, "value", claim.importance) == "critical"
    }
    key_evidence_records = [
        record for record in evidence_records if record.claim_id in key_claim_ids
    ]
    judgments_by_pair = {
        (judgment.claim_id, judgment.block_id): judgment
        for judgment in deep_result.semantic_judgments
    }
    contradictory_claim_ids = {
        judgment.claim_id
        for judgment in deep_result.semantic_judgments
        if getattr(judgment.relation, "value", judgment.relation) == "contradicts"
    }
    deterministic_issue_claim_ids = {
        record.claim_id
        for record in key_evidence_records
        if _has_citation_deterministic_issue(record)
    }
    key_claim_diagnostics = _key_claim_diagnostics(
        bundle=bundle,
        evidence_records=evidence_records,
        deep_result=deep_result,
    )
    accurate_count = sum(
        1
        for record in key_evidence_records
        if (
            record.quote_verified
            and getattr(record.match_method, "value", record.match_method)
            == "model_candidate"
            and (
                judgment := judgments_by_pair.get(
                    (record.claim_id, record.block_id)
                )
            )
            is not None
            and getattr(judgment.relation, "value", judgment.relation)
            == "supports"
            and not _has_citation_deterministic_issue(record)
        )
    )
    sufficiently_covered_claim_ids = {
        record.claim_id
        for record in key_evidence_records
        if (
            record.quote_verified
            and record.claim_id not in deterministic_issue_claim_ids
            and record.claim_id not in contradictory_claim_ids
            and (
                judgment := judgments_by_pair.get(
                    (record.claim_id, record.block_id)
                )
            )
            is not None
            and getattr(judgment.relation, "value", judgment.relation)
            == "supports"
            and not _has_citation_deterministic_issue(record)
        )
    }
    metrics.update(
        {
            "key_claim_count": len(key_claim_ids),
            "key_claim_citation_accuracy_numerator": accurate_count,
            "key_claim_citation_accuracy_denominator": len(key_evidence_records),
            "key_claim_citation_completeness_numerator": len(
                sufficiently_covered_claim_ids
            ),
            "key_claim_citation_completeness_denominator": len(key_claim_ids),
            "key_claim_diagnostics": key_claim_diagnostics,
        }
    )
    if case.payload.get("case_group") == "quality":
        quality_label = _required_string(case.payload, "quality_label")
        metrics["quality_label"] = quality_label
        if quality_label != "good":
            metrics.update(
                _known_error_detection_metrics(
                    case=case,
                    bundle=bundle,
                    evidence_records=evidence_records,
                    deep_result=deep_result,
                )
            )
    if case.payload.get("case_group") == "stability":
        metrics["stability_output_id"] = case.payload.get("stability_output_id")
    if case.payload.get("case_group") == "attack":
        attack_id = _required_string(case.payload, "attack_id")
        attack = next(item for item in _load_live_manifest()["attacks"] if item["attack_id"] == attack_id)
        metrics["attack_id"] = attack_id
        metrics["attack_type"] = attack["attack_type"]
        metrics["pair_role"] = case.payload.get("pair_role")
        metrics["expected_attack"] = case.payload.get("pair_role") == "attack"
        metrics["attack_detected"] = _attack_detected(
            attack=attack,
            bundle=bundle,
            source_blocks=source_blocks,
            evidence_records=evidence_records,
            deep_result=deep_result,
            report=report,
        )
    return metrics


_CITATION_DETERMINISTIC_FLAG_PREFIXES = (
    "NUMBER_MISMATCH:",
    "UNIT_MISMATCH:",
    "NEGATION_MISMATCH",
    "COMPARISON_DIRECTION_MISMATCH",
)

_DIAGNOSTIC_ISSUE_CODE_PREFIXES = (
    ("CANDIDATE_BLOCK_NOT_FOUND:", "CANDIDATE_BLOCK_NOT_FOUND"),
    ("CANDIDATE_QUOTE_MISSING:", "CANDIDATE_QUOTE_MISSING"),
    ("CANDIDATE_QUOTE_NOT_FOUND:", "CANDIDATE_QUOTE_NOT_FOUND"),
    ("NUMBER_MISMATCH:", "NUMBER_MISMATCH"),
    ("UNIT_MISMATCH:", "UNIT_MISMATCH"),
    ("NEGATION_MISMATCH", "NEGATION_MISMATCH"),
    ("COMPARISON_DIRECTION_MISMATCH", "COMPARISON_DIRECTION_MISMATCH"),
    ("INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"),
    ("NON_AUDITABLE", "NON_AUDITABLE"),
)


def _has_citation_deterministic_issue(record: Any) -> bool:
    return any(
        flag.startswith(_CITATION_DETERMINISTIC_FLAG_PREFIXES)
        for flag in record.rule_flags
    )


def _judgment_field(judgment: Any | None, field: str) -> str | None:
    if judgment is None:
        return None
    value = getattr(judgment, field, None)
    value = getattr(value, "value", value)
    return value if isinstance(value, str) else None


def _diagnostic_issue_codes(record: Any) -> list[str]:
    codes: set[str] = set()
    for flag in record.rule_flags:
        for prefix, code in _DIAGNOSTIC_ISSUE_CODE_PREFIXES:
            if flag.startswith(prefix):
                codes.add(code)
                break
    return sorted(codes)


def _key_claim_diagnostics(
    *,
    bundle: GeneratedBundle,
    evidence_records: list[Any],
    deep_result: DeepAuditResult,
) -> list[dict[str, Any]]:
    key_claim_ids = {
        claim.claim_id
        for claim in bundle.claims
        if getattr(claim.importance, "value", claim.importance) == "critical"
    }
    judgments_by_pair = {
        (judgment.claim_id, judgment.block_id): judgment
        for judgment in deep_result.semantic_judgments
    }
    diagnostics_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for record in evidence_records:
        if record.claim_id not in key_claim_ids:
            continue
        pair = (record.claim_id, record.block_id or "")
        existing = diagnostics_by_pair.get(pair)
        issue_codes = _diagnostic_issue_codes(record)
        if existing is not None:
            existing["deterministic_issue_codes"] = sorted(
                set(existing["deterministic_issue_codes"]) | set(issue_codes)
            )
            continue
        judgment = judgments_by_pair.get((record.claim_id, record.block_id))
        diagnostics_by_pair[pair] = {
            "claim_id": record.claim_id,
            "block_id": record.block_id,
            "relation": _judgment_field(judgment, "relation"),
            "scope_status": _judgment_field(judgment, "scope_status"),
            "terminology_status": _judgment_field(
                judgment,
                "terminology_status",
            ),
            "severity": _judgment_field(judgment, "severity"),
            "deterministic_issue_codes": issue_codes,
        }
    return sorted(
        diagnostics_by_pair.values(),
        key=lambda item: (item["claim_id"], item["block_id"] or ""),
    )


def _known_error_detection_metrics(
    *,
    case: EvaluationCase,
    bundle: GeneratedBundle,
    evidence_records: list[Any],
    deep_result: DeepAuditResult,
) -> dict[str, Any]:
    paper_id = _required_string(case.payload, "paper_id")
    quality_label = _required_string(case.payload, "quality_label")
    manifest = _load_live_manifest()
    paper = next(
        item for item in manifest["papers"] if item["paper_id"] == paper_id
    )
    mutation = paper["quality_mutations"].get(quality_label)
    if not isinstance(mutation, dict):
        raise ValueError("known error metadata is unavailable")
    known_error_type = _required_string(mutation, "known_error_type")
    known_error_severity = _required_string(mutation, "severity")
    if known_error_severity not in {"minor", "major", "critical"}:
        raise ValueError("known error severity is invalid")
    target_sentence_id = _required_string(mutation, "target_sentence_id")
    target_claim_ids = sorted(
        {
            claim.claim_id
            for claim in bundle.claims
            if claim.sentence_id == target_sentence_id
        }
    )
    if not target_claim_ids:
        raise ValueError("known error target sentence has no claims")
    target_claim_id_set = set(target_claim_ids)
    deterministic_detected = any(
        record.claim_id in target_claim_id_set
        and bool(_diagnostic_issue_codes(record))
        for record in evidence_records
    )
    semantic_detected = any(
        judgment.claim_id in target_claim_id_set
        and (
            _judgment_field(judgment, "relation")
            in {"contradicts", "insufficient"}
            or _judgment_field(judgment, "scope_status") == "expanded"
            or _judgment_field(judgment, "terminology_status") == "misused"
            or _judgment_field(judgment, "severity")
            in {"minor", "major", "critical"}
        )
        for judgment in deep_result.semantic_judgments
    )
    return {
        "known_error_type": known_error_type,
        "known_error_severity": known_error_severity,
        "known_error_target_sentence_id": target_sentence_id,
        "known_error_target_claim_ids": target_claim_ids,
        "known_error_detected": deterministic_detected or semantic_detected,
    }


def _attack_detected(
    *,
    attack: Mapping[str, Any],
    bundle: GeneratedBundle,
    source_blocks: list[SourceBlock],
    evidence_records: list[Any],
    deep_result: DeepAuditResult,
    report: Any,
) -> bool:
    target_sentence_id = _required_string(attack, "target_sentence_id")
    target_claim_ids = {
        claim.claim_id
        for claim in bundle.claims
        if claim.sentence_id == target_sentence_id
    }
    target_block_ids = {
        record.block_id
        for record in evidence_records
        if record.claim_id in target_claim_ids and record.block_id is not None
    }
    if any(
        record.claim_id in target_claim_ids and record.rule_flags
        for record in evidence_records
    ):
        return True
    for judgment in deep_result.semantic_judgments:
        if judgment.claim_id not in target_claim_ids:
            continue
        if (
            judgment.relation.value != "supports"
            or judgment.scope_status.value != "preserved"
            or judgment.terminology_status.value != "correct"
            or judgment.severity.value in {"major", "critical"}
        ):
            return True
    for judgment in deep_result.semantic_judgments:
        if judgment.claim_id in target_claim_ids:
            continue
        # S7-CONTRACT-01: conservative severe alert, not causal attribution.
        if judgment.severity.value in {"major", "critical"}:
            return True
    if not target_claim_ids:
        expression_category = {
            "length_padding": "redundancy_or_off_topic",
            "terminology_stuffing": "unexplained_terminology",
        }.get(attack.get("attack_type"))
        if expression_category is not None:
            return any(
                finding.category.value == expression_category
                and finding.status.value == "detected"
                and any(location.sentence_id == target_sentence_id for location in finding.locations)
                for finding in deep_result.expression_findings
            )
        dimension_ids = {
            "limitation_deletion": "conclusion_limitations",
            "rubric_prompt_injection": "risk_compliance",
        }
        dimension_id = dimension_ids.get(attack.get("attack_type"))
        if dimension_id is not None:
            points = {
                dimension.dimension_id.value: dimension_level_points(dimension)
                for dimension in report.dimensions
            }
            return points.get(dimension_id, 4) < 4
    return False


class _RevisionMetricsIndeterminate(ValueError):
    def __init__(self) -> None:
        super().__init__("EVALUATION_FAILED")


def _revision_judgment_state(judgment: Any) -> tuple[str, ...]:
    return (
        judgment.relation.value, judgment.scope_status.value,
        judgment.terminology_status.value, judgment.severity.value,
    )


def _revision_evidence_identity(record: Any) -> tuple[Any, ...]:
    return record.block_id, record.page_index, record.quote


def _revision_covered_pairs(
    claims: list[Any], evidence: list[Any], result: DeepAuditResult,
) -> list[tuple[Any, Any, Any]] | None:
    """Require every target claim and observed evidence pair, not a surviving subset."""
    if not claims or any(claim.auditability.value != "auditable" for claim in claims):
        return None
    by_claim = {claim.claim_id: claim for claim in claims}
    records: dict[tuple[str, str], Any] = {}
    for record in evidence:
        if record.claim_id not in by_claim:
            continue
        if not record.quote_verified or record.block_id is None or not record.quote:
            return None
        pair = (record.claim_id, record.block_id)
        previous = records.get(pair)
        if previous is not None and (
            _revision_evidence_identity(previous) != _revision_evidence_identity(record)
            or set(previous.rule_flags) != set(record.rule_flags)
        ):
            return None
        records[pair] = record
    if {pair[0] for pair in records} != set(by_claim):
        return None
    judgments: dict[tuple[str, str], Any] = {}
    for judgment in result.semantic_judgments:
        if judgment.claim_id not in by_claim:
            continue
        pair = (judgment.claim_id, judgment.block_id)
        previous = judgments.get(pair)
        if previous is not None and _revision_judgment_state(previous) != _revision_judgment_state(judgment):
            return None
        judgments[pair] = judgment
    if set(judgments) != set(records):
        return None
    return [(by_claim[pair[0]], record, judgments[pair]) for pair, record in records.items()]


def _revision_pair_supported(record: Any, judgment: Any) -> bool:
    return (
        judgment.relation.value == "supports"
        and judgment.scope_status.value == "preserved"
        and judgment.severity.value not in {"major", "critical"}
        and not _has_citation_deterministic_issue(record)
    )


def _revision_problem_identity(claim: Any, record: Any, judgment: Any) -> tuple[Any, ...]:
    # Deliberately exact: no reason similarity or guessed semantic lineage.
    # IDs, order, retrieval method and major/critical level are not issue identity.
    proposition = (
        claim.sentence_id, claim.text, claim.claim_type.value,
        tuple(sorted(set(claim.qualifiers))), tuple(sorted(set(claim.numeric_entities))),
    )
    issue = (
        _revision_judgment_state(judgment)[:3],
        tuple(sorted({flag for flag in record.rule_flags if flag.startswith(_CITATION_DETERMINISTIC_FLAG_PREFIXES)})),
    )
    return proposition, _revision_evidence_identity(record), issue


def _revision_new_severe_errors(
    before_bundle: GeneratedBundle, after_bundle: GeneratedBundle,
    before_evidence: list[Any], after_evidence: list[Any],
    before_result: DeepAuditResult, after_result: DeepAuditResult,
) -> int:
    after_claims = {claim.claim_id: claim for claim in after_bundle.claims}
    severe_ids = {
        judgment.claim_id for judgment in after_result.semantic_judgments
        if judgment.severity.value in {"major", "critical"}
    }
    if not severe_ids <= after_claims.keys():
        raise _RevisionMetricsIndeterminate()
    new_issues: set[tuple[Any, ...]] = set()
    for sentence_id in {after_claims[claim_id].sentence_id for claim_id in severe_ids}:
        before_pairs = _revision_covered_pairs(
            [claim for claim in before_bundle.claims if claim.sentence_id == sentence_id],
            before_evidence, before_result,
        )
        after_pairs = _revision_covered_pairs(
            [claim for claim in after_bundle.claims if claim.sentence_id == sentence_id],
            after_evidence, after_result,
        )
        if before_pairs is None or after_pairs is None:
            raise _RevisionMetricsIndeterminate()
        old_issues = {
            _revision_problem_identity(claim, record, judgment)
            for claim, record, judgment in before_pairs
            if judgment.severity.value in {"major", "critical"}
        }
        before_inputs = {
            _revision_problem_identity(claim, record, judgment)[:2]
            for claim, record, judgment in before_pairs
        }
        for claim, record, judgment in after_pairs:
            if judgment.severity.value not in {"major", "critical"}:
                continue
            identity = _revision_problem_identity(claim, record, judgment)
            if identity in old_issues:
                continue
            # A reanchored severe proposition/type may still be the old issue;
            # a clean evidence baseline cannot resolve that attribution ambiguity.
            if any(old[0] == identity[0] and old[2] == identity[2] for old in old_issues):
                raise _RevisionMetricsIndeterminate()
            # An unchanged input with a changed judgment is not an introduced error.
            if identity[:2] in before_inputs:
                raise _RevisionMetricsIndeterminate()
            baseline = [(r, j) for _, r, j in before_pairs if _revision_evidence_identity(r) == identity[1]]
            # Confirm new only against a fully covered, clean same-sentence anchor.
            # Changed/split old severe claims and changed evidence remain unknown.
            if not baseline or not all(
                _revision_pair_supported(r, j)
                and j.severity.value == "none" and j.terminology_status.value == "correct"
                for r, j in baseline
            ):
                raise _RevisionMetricsIndeterminate()
            new_issues.add(identity)
    return len(new_issues)


def _revision_metrics(
    *,
    case: EvaluationCase,
    before_bundle: GeneratedBundle,
    after_bundle: GeneratedBundle,
    target_sentence_id: str,
    mutation: Mapping[str, Any],
    before_result: DeepAuditResult,
    after_result: DeepAuditResult,
    before_report: Any,
    after_report: Any,
    before_evidence: list[Any],
    after_evidence: list[Any],
) -> dict[str, Any]:
    before_sentence = _find_sentence(before_bundle, target_sentence_id)
    after_sentence = _find_sentence(after_bundle, target_sentence_id)
    target_claims = [
        claim
        for claim in after_bundle.claims
        if claim.sentence_id == target_sentence_id
    ]
    after_target_pairs = _revision_covered_pairs(
        target_claims, after_evidence, after_result,
    )
    new_severe_errors = _revision_new_severe_errors(
        before_bundle, after_bundle, before_evidence, after_evidence,
        before_result, after_result,
    )
    resolved = (
        before_sentence.text == mutation["replacement_text"]
        and after_sentence.text != before_sentence.text
        and after_target_pairs
        and all(
            _revision_pair_supported(record, judgment)
            for _, record, judgment in after_target_pairs
        )
    )
    metrics = {
        "provider_mode": "live",
        "paper_id": case.payload.get("paper_id"),
        "case_group": "revision",
        "quality_label": case.payload.get("quality_label"),
        "known_issue_count": 1,
        "resolved_issue_count": int(bool(resolved)),
        "error_resolution_rate": 1.0 if resolved else 0.0,
        "new_severe_error_count": new_severe_errors,
        "irrelevant_change": not _only_target_sentence_changed(
            before_bundle.document,
            after_bundle.document,
            target_sentence_id,
        ),
        "pre_overall_score": before_report.overall_score,
        "post_overall_score": after_report.overall_score,
        "pre_verified_evidence_count": sum(
            record.quote_verified for record in before_evidence
        ),
        "post_verified_evidence_count": sum(
            record.quote_verified for record in after_evidence
        ),
    }
    metrics["key_claim_diagnostics"] = _key_claim_diagnostics(
        bundle=after_bundle,
        evidence_records=after_evidence,
        deep_result=after_result,
    )
    metrics["evaluation_method_version"] = EVALUATION_METHOD_VERSION
    metrics["expression_findings"] = _expression_metrics(after_result)
    return metrics


def _only_target_sentence_changed(
    before_document: Any,
    after_document: Any,
    target_sentence_id: str,
) -> bool:
    before = before_document.model_dump(mode="python")
    after = after_document.model_dump(mode="python")
    for document in (before, after):
        for section in document["sections"]:
            section["sentences"] = [
                sentence
                for sentence in section["sentences"]
                if sentence["sentence_id"] != target_sentence_id
            ]
    return before == after


def evaluate_smoke_case(case: EvaluationCase) -> dict[str, object]:
    manifest = _load_smoke_manifest()
    variant_id = _required_string(case.payload, "variant_id")
    variants = manifest.get("variants")
    if not isinstance(variants, dict) or variant_id not in variants:
        raise ValueError("smoke case references an unknown variant")
    variant = variants[variant_id]
    if not isinstance(variant, dict):
        raise ValueError("smoke variant must be an object")
    base = manifest.get("base")
    if not isinstance(base, dict):
        raise ValueError("smoke manifest base must be an object")

    source_data = _load_project_json(_required_string(base, "source_blocks_path"))
    bundle_data = _load_project_json(_required_string(base, "bundle_path"))
    audit_data = _load_project_json(_required_string(base, "deep_audit_path"))
    source_data = _apply_json_operations(
        source_data,
        _required_operations(variant, "source_ops"),
    )
    bundle_data = _apply_json_operations(
        bundle_data,
        _required_operations(variant, "bundle_ops"),
    )
    audit_data = _apply_json_operations(
        audit_data,
        _required_operations(variant, "deep_audit_ops"),
    )

    if not isinstance(source_data, list):
        raise ValueError("smoke source blocks must be an array")
    source_blocks = [SourceBlock.model_validate(item) for item in source_data]
    bundle = GeneratedBundle.model_validate(bundle_data)
    deep_audit_result = DeepAuditResultV2.model_validate(audit_data)
    compliance_context = ComplianceContext(
        rights_or_license_confirmed=True,
        source_disclosure_status="present",
        ai_assistance_disclosure_status="present",
        generated_content_label_applicability="not_applicable",
        generated_content_label_status="not_applicable",
    )
    audit_service = AuditService()
    evidence_records, _ = audit_service.quick_check(bundle, source_blocks)
    report = audit_service.score_legacy_v2(
        bundle,
        evidence_records,
        deep_audit_result,
        compliance_context,
    )
    rule_flags = sorted(
        {
            flag
            for record in evidence_records
            for flag in record.rule_flags
        }
    )
    attack_type = case.payload.get("attack_type")
    attack_detected = _smoke_attack_detected(
        attack_type=attack_type,
        rule_flags=rule_flags,
    )
    dimension_points = {
        dimension.dimension_id.value: dimension_level_points(dimension)
        for dimension in report.dimensions
    }
    metrics: dict[str, object] = {
        "provider_mode": "reference_replay",
        "paper_id": manifest["material"]["paper_id"],
        "case_group": case.payload["case_group"],
        "dimension_count": len(report.dimensions),
        "dimension_points": dimension_points,
        "overall_score": report.overall_score,
        "core_gate_passed": report.core_gate_passed,
        "decision": report.decision.value,
        "hard_failure_count": len(report.hard_failures),
        "hard_failure_codes": report.hard_failures,
        "rule_issue_count": len(rule_flags),
        "attack_detected": attack_detected,
    }
    for field in (
        "quality_label",
        "attack_type",
        "pair_role",
        "stability_output_id",
    ):
        value = case.payload.get(field)
        if value is not None:
            metrics[field] = value
    return {
        "provider_calls": 0,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "metrics": metrics,
    }


def run_cases(
    *,
    mode: str,
    cases: Iterable[EvaluationCase],
    output_path: Path,
    versions: VersionInfo,
    evaluate: Callable[[EvaluationCase], Mapping[str, Any]],
    diagnostic_plan: dict[str, Any] | None = None,
) -> RunSummary:
    with _evaluation_run_lock(output_path):
        return _run_cases_unlocked(
            mode=mode,
            cases=cases,
            output_path=output_path,
            versions=versions,
            evaluate=evaluate,
            diagnostic_plan=diagnostic_plan,
        )


def _run_cases_unlocked(
    *,
    mode: str,
    cases: Iterable[EvaluationCase],
    output_path: Path,
    versions: VersionInfo,
    evaluate: Callable[[EvaluationCase], Mapping[str, Any]],
    diagnostic_plan: dict[str, Any] | None = None,
) -> RunSummary:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported evaluation mode: {mode}")
    materialized_cases = list(cases)
    if diagnostic_plan is not None:
        if mode != "calibrate" or materialized_cases != context_diagnostic_cases(diagnostic_plan) or diagnostic_plan["versions_sha256"] != _sha256_json(versions.__dict__):
            _invalid_context_diagnostic()
    requested_keys = [(case.case_id, case.run_index) for case in materialized_cases]
    if len(requested_keys) != len(set(requested_keys)):
        raise ValueError("duplicate case_id and run_index in evaluation input")

    existing_keys = _load_existing_keys(output_path=output_path, mode=mode)
    pending_keys = _load_pending_keys(
        pending_path=pending_path_for(output_path),
        mode=mode,
    )
    _validate_context_resume(output_path, mode, diagnostic_plan)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    skipped = 0
    appended = 0
    recovered_interrupted = 0
    for case in materialized_cases:
        key = (case.case_id, case.run_index)
        diagnostic_metrics = (
            {"context_diagnostic": _context_diagnostic_metadata(diagnostic_plan, case)}
            if diagnostic_plan is not None else {}
        )
        if key in existing_keys:
            skipped += 1
            continue
        if key in pending_keys:
            record = _result_record(
                case=case,
                mode=mode,
                versions=versions,
                status="failed",
                error_code="RUN_INTERRUPTED",
                provider_calls=None,
                usage=None,
                metrics=diagnostic_metrics,
                elapsed_ms=None,
            )
            _append_jsonl(output_path, record)
            existing_keys.add(key)
            skipped += 1
            appended += 1
            recovered_interrupted += 1
            continue

        _append_jsonl(
            pending_path_for(output_path),
            {
                "case_id": case.case_id,
                "run_index": case.run_index,
                "mode": mode,
                **diagnostic_metrics,
            },
        )
        started_at = time.perf_counter()
        try:
            evaluated = dict(evaluate(case))
            provider_calls = evaluated.pop("provider_calls", 0)
            usage = evaluated.pop("usage", None)
            metrics = evaluated.pop("metrics", {})
            if evaluated:
                raise ValueError("evaluator returned unsupported result fields")
            _validate_provider_calls(provider_calls)
            normalized_usage = _normalize_usage(usage)
            normalized_metrics = _normalize_metrics(metrics)
            status: ResultStatus = "succeeded"
            error_code = "NONE"
        except EvaluationCaseError as exc:
            provider_calls = exc.provider_calls
            normalized_usage = exc.usage
            normalized_metrics = _normalize_metrics(getattr(exc, "safe_metrics", {}))
            status = exc.status
            error_code = exc.error_code
            if diagnostic_plan is not None and hasattr(exc, "context_diagnostic"):
                normalized_metrics["context_diagnostic"] = exc.context_diagnostic
        except _ContextDiagnosticInputDrift:
            provider_calls = 0
            normalized_usage = _normalize_usage(None)
            normalized_metrics = {}
            status = "failed"
            error_code = "EVALUATION_FAILED"
        except Exception:
            provider_calls = None
            normalized_usage = _normalize_usage(None)
            normalized_metrics = {}
            status = "failed"
            error_code = "EVALUATION_FAILED"

        elapsed_ms = max(0, round((time.perf_counter() - started_at) * 1000))
        for field, value in diagnostic_metrics.items():
            normalized_metrics.setdefault(field, value)
        record = _result_record(
            case=case,
            mode=mode,
            versions=versions,
            status=status,
            error_code=error_code,
            provider_calls=provider_calls,
            usage=normalized_usage,
            metrics=normalized_metrics,
            elapsed_ms=elapsed_ms,
        )
        if diagnostic_plan is not None:
            validate_context_diagnostic_record(record)
        _append_jsonl(output_path, record)
        existing_keys.add(key)
        appended += 1

    return RunSummary(
        requested=len(materialized_cases),
        skipped=skipped,
        appended=appended,
        recovered_interrupted=recovered_interrupted,
    )


def _load_existing_keys(*, output_path: Path, mode: str) -> set[tuple[str, int]]:
    if not output_path.exists():
        return set()
    keys: set[tuple[str, int]] = set()
    for line_number, record in _read_jsonl(output_path):
        _validate_existing_result(
            record=record,
            mode=mode,
            line_number=line_number,
        )
        key = _record_key(record=record, line_number=line_number)
        if key in keys:
            raise ValueError(f"duplicate result key at line {line_number}")
        keys.add(key)
    return keys


def _load_pending_keys(*, pending_path: Path, mode: str) -> set[tuple[str, int]]:
    if not pending_path.exists():
        return set()
    keys: set[tuple[str, int]] = set()
    for line_number, record in _read_jsonl(pending_path):
        if record.get("mode") != mode:
            raise ValueError(f"pending mode mismatch at line {line_number}")
        key = _record_key(record=record, line_number=line_number)
        if key in keys:
            raise ValueError(f"duplicate pending key at line {line_number}")
        keys.add(key)
    return keys


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank JSONL record at line {line_number}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL record at line {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record must be an object at line {line_number}")
            records.append((line_number, record))
    return records


def _record_key(*, record: Mapping[str, Any], line_number: int) -> tuple[str, int]:
    case_id = record.get("case_id")
    run_index = record.get("run_index")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"invalid case_id at line {line_number}")
    if not isinstance(run_index, int) or isinstance(run_index, bool) or run_index < 0:
        raise ValueError(f"invalid run_index at line {line_number}")
    return case_id, run_index


def _validate_existing_result(
    *,
    record: Mapping[str, Any],
    mode: str,
    line_number: int,
) -> None:
    if any(field not in record for field in _REQUIRED_RESULT_FIELDS):
        raise ValueError(f"invalid existing result at line {line_number}")
    if record["result_version"] != RESULT_VERSION:
        raise ValueError(f"unsupported result_version at line {line_number}")
    if record["mode"] != mode:
        raise ValueError(f"result mode mismatch at line {line_number}")

    _record_key(record=record, line_number=line_number)
    status = record["status"]
    if not isinstance(status, str) or status not in _ALLOWED_RESULT_STATUSES:
        raise ValueError(f"invalid existing result at line {line_number}")

    for field in (
        "model",
        "prompt_version",
        "schema_version",
        "data_version",
        "code_version",
    ):
        value = record[field]
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError(f"invalid existing result at line {line_number}")

    elapsed_ms = record["elapsed_ms"]
    if (
        elapsed_ms is not None
        and (
            not isinstance(elapsed_ms, int)
            or isinstance(elapsed_ms, bool)
            or elapsed_ms < 0
        )
    ):
        raise ValueError(f"invalid existing result at line {line_number}")
    recorded_at = record["recorded_at"]
    if not isinstance(recorded_at, str) or not recorded_at:
        raise ValueError(f"invalid existing result at line {line_number}")

    try:
        _validate_error_code(record["error_code"])
        _validate_provider_calls(record["provider_calls"])
        usage = record["usage"]
        if not isinstance(usage, Mapping):
            raise ValueError("usage must be an object")
        _normalize_usage(usage)
        _normalize_metrics(record["metrics"])
        validate_expression_metrics(record)
    except Exception:
        raise ValueError(f"invalid existing result at line {line_number}") from None


def _result_record(
    *,
    case: EvaluationCase,
    mode: str,
    versions: VersionInfo,
    status: ResultStatus,
    error_code: str,
    provider_calls: int | None,
    usage: Mapping[str, int | None] | None,
    metrics: Mapping[str, Any],
    elapsed_ms: int | None,
) -> dict[str, Any]:
    _validate_error_code(error_code)
    _validate_provider_calls(provider_calls)
    return {
        "result_version": RESULT_VERSION,
        "case_id": case.case_id,
        "run_index": case.run_index,
        "mode": mode,
        "status": status,
        "error_code": error_code,
        "model": versions.model,
        "prompt_version": versions.prompt_version,
        "schema_version": versions.schema_version,
        "data_version": versions.data_version,
        "code_version": versions.code_version,
        "provider_calls": provider_calls,
        "usage": _normalize_usage(usage),
        "metrics": _normalize_metrics(metrics),
        "elapsed_ms": elapsed_ms,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_error_code(error_code: str) -> None:
    if not _ERROR_CODE_PATTERN.fullmatch(error_code):
        raise ValueError("error_code must be a stable uppercase identifier")


def _validate_provider_calls(provider_calls: int | None) -> None:
    if provider_calls is None:
        return
    if (
        not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
        or provider_calls < 0
    ):
        raise ValueError("provider_calls must be a non-negative integer or null")


def _normalize_usage(
    usage: Mapping[str, int | None] | None,
) -> dict[str, int | None]:
    source = usage or {}
    allowed = ("prompt_tokens", "completion_tokens", "total_tokens")
    if set(source) - set(allowed):
        raise ValueError("usage contains unsupported fields")
    normalized: dict[str, int | None] = {}
    for field in allowed:
        value = source.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"{field} must be a non-negative integer or null")
        normalized[field] = value
    return normalized


def _normalize_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metrics, Mapping):
        raise ValueError("metrics must be an object")
    normalized = json.loads(json.dumps(dict(metrics), ensure_ascii=False))
    _validate_metric_value(normalized, path="metrics", depth=0)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_METRICS_JSON_BYTES:
        raise ValueError("metrics exceed the safe JSON size limit")
    return normalized


def _validate_metric_value(value: Any, *, path: str, depth: int) -> None:
    if depth > 5:
        raise ValueError("metrics nesting exceeds the safe depth")
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("metric keys must be non-empty strings")
            lowered = key.lower()
            if any(forbidden in lowered for forbidden in _FORBIDDEN_METRIC_KEYS):
                raise ValueError(f"unsafe metric field: {path}.{key}")
            _validate_metric_value(
                nested,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError("metric arrays exceed the safe item limit")
        for index, nested in enumerate(value):
            _validate_metric_value(
                nested,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str) and len(value) <= _MAX_METRIC_STRING_LENGTH:
        return
    raise ValueError(f"unsafe metric value at {path}")


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        dict(record),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _quality_cases(papers: Iterable[Mapping[str, Any]]) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for paper in papers:
        paper_id = _required_string(paper, "paper_id")
        for quality_label in ("good", "medium", "bad"):
            cases.append(
                EvaluationCase(
                    case_id=f"quality:{paper_id}:{quality_label}",
                    run_index=0,
                    payload={
                        "case_group": "quality",
                        "paper_id": paper_id,
                        "quality_label": quality_label,
                    },
                )
            )
    return cases


def _live_bundle_data(paper: Mapping[str, Any]) -> dict[str, Any]:
    base_output = paper.get("base_output")
    if not isinstance(base_output, dict):
        raise ValueError("Live paper base_output must be an object")
    source_sections = base_output.get("sections")
    if not isinstance(source_sections, dict):
        raise ValueError("Live paper sections must be an object")
    section_order = (
        ("research_question", "Research question"),
        ("methods", "Methods and data"),
        ("results", "Main result"),
        ("limitations", "Limitations"),
        ("plain_explanation", "Plain explanation"),
    )
    sections: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for section_id, heading in section_order:
        source_sentence = source_sections.get(section_id)
        if not isinstance(source_sentence, dict):
            raise ValueError("Live paper is missing a required section")
        sentence_id = _required_string(source_sentence, "sentence_id")
        sentence_text = _required_string(source_sentence, "text")
        sections.append(
            {
                "section_id": section_id,
                "heading": heading,
                "sentences": [
                    {
                        "sentence_id": sentence_id,
                        "text": sentence_text,
                    }
                ],
            }
        )
        source_claim = source_sentence.get("claim")
        if source_claim is None:
            continue
        if not isinstance(source_claim, dict):
            raise ValueError("Live claim must be an object or null")
        claim = copy.deepcopy(source_claim)
        claim["sentence_id"] = sentence_id
        claim["text"] = sentence_text
        candidate_block_id = claim.pop("candidate_block_id", None)
        claim["candidate_block_ids"] = (
            [candidate_block_id] if candidate_block_id is not None else []
        )
        claims.append(claim)
    return {
        "document": {
            "title": _required_string(base_output, "title"),
            "sections": sections,
        },
        "claims": claims,
    }


def _apply_live_mutation(
    bundle_data: dict[str, Any],
    mutation: Mapping[str, Any],
) -> None:
    target_sentence_id = _required_string(mutation, "target_sentence_id")
    replacement_text = _required_string(mutation, "replacement_text")
    if "qualifiers" in mutation:
        qualifiers = mutation["qualifiers"]
        if not isinstance(qualifiers, list) or any(
            not isinstance(qualifier, str) or not qualifier.strip()
            for qualifier in qualifiers
        ):
            raise ValueError(
                "mutation qualifiers must be an array of non-empty strings"
            )
    target_found = False
    for section in bundle_data["document"]["sections"]:
        for sentence in section["sentences"]:
            if sentence["sentence_id"] == target_sentence_id:
                sentence["text"] = replacement_text
                target_found = True
    if not target_found:
        raise ValueError("Live mutation target sentence was not found")
    for claim in bundle_data["claims"]:
        if claim["sentence_id"] != target_sentence_id:
            continue
        claim["text"] = replacement_text
        if "qualifiers" in mutation:
            claim["qualifiers"] = copy.deepcopy(mutation["qualifiers"])
        if "numeric_entities" in mutation:
            numeric_entities = mutation["numeric_entities"]
            if not isinstance(numeric_entities, list):
                raise ValueError("mutation numeric_entities must be an array")
            claim["numeric_entities"] = copy.deepcopy(numeric_entities)
        if "candidate_block_id_override" in mutation:
            claim["candidate_block_ids"] = [
                _required_string(mutation, "candidate_block_id_override")
            ]
        if "candidate_quote_override" in mutation:
            claim["candidate_quote"] = _required_string(
                mutation,
                "candidate_quote_override",
            )


def _split_case_reference(reference: Any) -> tuple[str, str]:
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ValueError("case reference must contain exactly one separator")
    paper_id, variant = reference.split(":", 1)
    if not paper_id or variant not in {"good", "medium", "bad"}:
        raise ValueError("case reference uses an unsupported quality variant")
    return paper_id, variant


def _logical_request_count(case: EvaluationCase) -> int:
    if case.payload.get("case_group") == "revision":
        return 4
    return 1


def _estimate_cost_cny(*, input_tokens: int, completion_tokens: int) -> float:
    return (
        input_tokens * _HY3_INPUT_CNY_PER_MILLION_TOKENS
        + completion_tokens * _HY3_OUTPUT_CNY_PER_MILLION_TOKENS
    ) / 1_000_000


@lru_cache(maxsize=1)
def _load_live_manifest() -> dict[str, Any]:
    path = _PROJECT_ROOT / "eval" / "live_cases.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Live manifest must be an object")
    if manifest.get("manifest_version") != "paperlens-live-manifest-v1":
        raise ValueError("unsupported Live manifest version")
    if manifest.get("data_version") != "paperlens-plos-abstracts-v1":
        raise ValueError("unsupported Live data version")
    papers = manifest.get("papers")
    attacks = manifest.get("attacks")
    stability_refs = manifest.get("stability_case_refs")
    revision_refs = manifest.get("revision_case_refs")
    if not isinstance(papers, list) or len(papers) != 10:
        raise ValueError("Live manifest must contain ten papers")
    if not isinstance(attacks, list) or len(attacks) != 16:
        raise ValueError("Live manifest must contain sixteen attacks")
    if not isinstance(stability_refs, list) or len(stability_refs) != 12:
        raise ValueError("Live manifest must contain twelve stability references")
    if not isinstance(revision_refs, list) or len(revision_refs) != 5:
        raise ValueError("Live manifest must contain five revision references")

    paper_ids: set[str] = set()
    for paper in papers:
        if not isinstance(paper, dict):
            raise ValueError("Live paper entries must be objects")
        paper_id = _required_string(paper, "paper_id")
        if paper_id in paper_ids:
            raise ValueError("Live manifest contains duplicate paper_id")
        paper_ids.add(paper_id)
        if paper.get("split") not in {"development", "holdout"}:
            raise ValueError("Live paper split is invalid")
        if paper.get("license_name") != "CC BY":
            raise ValueError("Live paper license is not approved")
        if paper.get("contains_private_text") is not False:
            raise ValueError("Live manifest must not contain private text")
        source_blocks = paper.get("source_blocks")
        if not isinstance(source_blocks, list) or not source_blocks:
            raise ValueError("Live paper source blocks are required")
        canonical = json.dumps(
            source_blocks,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != paper.get("material_sha256"):
            raise ValueError("Live paper material hash mismatch")
    if sum(paper["split"] == "development" for paper in papers) != 5:
        raise ValueError("Live development split must contain five papers")
    if sum(paper["split"] == "holdout" for paper in papers) != 5:
        raise ValueError("Live holdout split must contain five papers")
    return manifest


def _code_version() -> str:
    tracked_paths = (
        "eval/build_report.py",
        "eval/live_cases.json",
        "eval/run_eval.py",
        "backend/app/audit_service.py",
        "backend/app/hy3_service.py",
        "backend/app/models.py",
        "backend/app/prompts.py",
        "backend/app/settings.py",
    )
    digest = hashlib.sha256()
    for relative_path in tracked_paths:
        path = _PROJECT_ROOT / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"workspace-content-{digest.hexdigest()}"


def _freeze_payload() -> dict[str, Any]:
    manifest_path = _PROJECT_ROOT / "eval" / "live_cases.json"
    manifest = _load_live_manifest()
    settings = Hy3Service().settings
    return {
        "freeze_version": FREEZE_VERSION,
        "evaluation_method_version": EVALUATION_METHOD_VERSION,
        "manifest_version": manifest["manifest_version"],
        "data_version": manifest["data_version"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "model": settings.hy3_model,
        "provider_config": {
            "base_url_sha256": hashlib.sha256(
                settings.hy3_base_url.encode("utf-8")
            ).hexdigest(),
            "timeout_seconds": settings.hy3_timeout_seconds,
            "max_retries": settings.hy3_max_retries,
        },
        "prompt_versions": {
            "deep_audit": DEEP_AUDIT_PROMPT_VERSION,
            "revision": REVISION_PROMPT_VERSION,
            "sentence_claims": SENTENCE_CLAIMS_PROMPT_VERSION,
        },
        "schema_versions": {
            "deep_audit": DEEP_AUDIT_SCHEMA_VERSION,
            "revision": REVISION_SCHEMA_VERSION,
            "sentence_claims": SENTENCE_CLAIMS_SCHEMA_VERSION,
        },
        "dimension_weights": {
            dimension_id.value: weight
            for dimension_id, weight in DIMENSION_WEIGHTS.items()
        },
        "core_dimension_gates": {
            dimension_id.value: minimum
            for dimension_id, minimum in CORE_DIMENSION_GATES.items()
        },
        "overall_score_threshold": 75,
        "acceptance_targets": copy.deepcopy(STAGE7_ACCEPTANCE_TARGETS),
        "code_version": _code_version(),
    }


def freeze_configuration(*, output_path: Path = DEFAULT_FREEZE_PATH) -> dict[str, Any]:
    payload = _freeze_payload()
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("frozen configuration must be an object")
        immutable_existing = {
            key: value for key, value in existing.items() if key != "created_at"
        }
        if immutable_existing != payload:
            raise ValueError("CONFIG_DRIFT")
        return existing
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def _require_frozen_configuration() -> dict[str, Any]:
    if not DEFAULT_FREEZE_PATH.exists():
        raise ValueError("CONFIG_NOT_FROZEN")
    existing = json.loads(DEFAULT_FREEZE_PATH.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError("CONFIG_DRIFT")
    expected = _freeze_payload()
    immutable_existing = {
        key: value for key, value in existing.items() if key != "created_at"
    }
    if immutable_existing != expected:
        raise ValueError("CONFIG_DRIFT")
    return existing


@lru_cache(maxsize=1)
def _load_smoke_manifest() -> dict[str, Any]:
    path = _PROJECT_ROOT / "eval" / "smoke_cases.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("smoke manifest must be an object")
    if manifest.get("manifest_version") != "paperlens-smoke-manifest-v1":
        raise ValueError("unsupported smoke manifest version")
    data_version = manifest.get("data_version")
    if not isinstance(data_version, str) or not data_version:
        raise ValueError("smoke manifest data_version is required")
    material = manifest.get("material")
    if not isinstance(material, dict):
        raise ValueError("smoke manifest material must be an object")
    if material.get("rights_confirmed") is not True:
        raise ValueError("smoke material rights must be confirmed")
    if material.get("contains_private_text") is not False:
        raise ValueError("smoke material must not contain private text")
    if material.get("source_kind") != "project_authored_synthetic":
        raise ValueError("smoke material must be project-authored synthetic text")
    return manifest


def _load_project_json(relative_path: str) -> Any:
    path = (_PROJECT_ROOT / relative_path).resolve()
    try:
        path.relative_to(_PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("smoke data path leaves the project root") from exc
    return json.loads(path.read_text(encoding="utf-8"))


def _required_string(source: Mapping[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_non_negative_integer(source: Mapping[str, Any], field: str) -> int:
    value = source.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _required_operations(
    source: Mapping[str, Any],
    field: str,
) -> list[dict[str, Any]]:
    value = source.get(field)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be an array of objects")
    return value


def _apply_json_operations(value: Any, operations: list[dict[str, Any]]) -> Any:
    updated = copy.deepcopy(value)
    for operation in operations:
        op = operation.get("op")
        path = operation.get("path")
        if op not in {"replace", "remove"} or not isinstance(path, str):
            raise ValueError("smoke operations only support replace or remove")
        parent, key = _json_pointer_parent(updated, path)
        if isinstance(parent, list):
            try:
                index = int(key)
            except ValueError as exc:
                raise ValueError("array JSON pointer must use an integer") from exc
            if not 0 <= index < len(parent):
                raise ValueError("smoke operation array index is out of range")
            if op == "remove":
                parent.pop(index)
            else:
                if "value" not in operation:
                    raise ValueError("replace operation requires value")
                parent[index] = copy.deepcopy(operation["value"])
        elif isinstance(parent, dict):
            if key not in parent:
                raise ValueError("smoke operation path does not exist")
            if op == "remove":
                del parent[key]
            else:
                if "value" not in operation:
                    raise ValueError("replace operation requires value")
                parent[key] = copy.deepcopy(operation["value"])
        else:
            raise ValueError("smoke operation parent must be an object or array")
    return updated


def _json_pointer_parent(value: Any, path: str) -> tuple[Any, str]:
    if not path.startswith("/") or path == "/":
        raise ValueError("smoke operation path must be a non-root JSON pointer")
    segments = [
        segment.replace("~1", "/").replace("~0", "~")
        for segment in path[1:].split("/")
    ]
    current = value
    for segment in segments[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError) as exc:
                raise ValueError("invalid array JSON pointer") from exc
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            raise ValueError("smoke operation path does not exist")
    return current, segments[-1]


def _smoke_attack_detected(*, attack_type: Any, rule_flags: list[str]) -> bool:
    if attack_type == "numeric_tampering":
        return any(
            flag.startswith(("NUMBER_MISMATCH:", "UNIT_MISMATCH:"))
            for flag in rule_flags
        )
    if attack_type == "fake_citation":
        return any(
            flag.startswith(
                ("CANDIDATE_BLOCK_NOT_FOUND:", "CANDIDATE_QUOTE_NOT_FOUND:")
            )
            for flag in rule_flags
        )
    return False


def _git_code_version() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    version = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", version):
        raise ValueError("git code version is not a full commit hash")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PaperLens stage 7 evaluation.")
    parser.add_argument("--mode", required=True, choices=SUPPORTED_MODES)
    parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="explicitly authorize pending Live provider calls",
    )
    parser.add_argument(
        "--freeze-config",
        action="store_true",
        help="write the immutable stage 7 configuration and do not run cases",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--diagnostic-id")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--target-claim-id")
    parser.add_argument("--target-block-id")
    parser.add_argument("--repeat-count", type=int, choices=(3,))
    args = parser.parse_args()
    diagnostic_requested = any(value is not None for value in (
        args.diagnostic_id, args.case_id, args.target_claim_id,
        args.target_block_id, args.repeat_count,
    ))
    if diagnostic_requested and (
        args.mode != "calibrate" or args.freeze_config or args.output is None
        or not all((args.diagnostic_id, args.case_id, args.target_claim_id, args.target_block_id))
        or args.repeat_count != 3
        or args.output.resolve() in {p.resolve() for p in DEFAULT_RESULT_PATHS.values()}
        or args.output.suffix != ".jsonl"
    ):
        parser.error("invalid context diagnostic")
    output_path = args.output or DEFAULT_RESULT_PATHS[args.mode]
    if args.freeze_config:
        if args.mode == "smoke":
            parser.error("--freeze-config is only valid for a Live mode")
        frozen = freeze_configuration()
        print(
            "CONFIG_FROZEN=True "
            f"VERSION={frozen['freeze_version']} "
            f"CODE_VERSION={frozen['code_version']} "
            f"OUTPUT={DEFAULT_FREEZE_PATH}"
        )
        return 0

    if args.mode == "smoke":
        manifest = _load_smoke_manifest()
        versions = VersionInfo(
            model="hy3-reference-replay",
            prompt_version="audit-v2",
            schema_version="deep-audit-result-v2",
            data_version=manifest["data_version"],
            code_version=_code_version(),
        )
        summary = run_cases(
            mode="smoke",
            cases=load_mode_cases("smoke"),
            output_path=output_path,
            versions=versions,
            evaluate=evaluate_smoke_case,
        )
        print(
            "EVAL_MODE=smoke "
            f"REQUESTED={summary.requested} "
            f"APPENDED={summary.appended} "
            f"SKIPPED={summary.skipped} "
            f"RECOVERED_INTERRUPTED={summary.recovered_interrupted} "
            f"OUTPUT={output_path}"
        )
        return 0

    if args.mode in {"final", "stability"}:
        try:
            _require_frozen_configuration()
        except ValueError as exc:
            print(f"EVAL_REFUSED={exc}")
            return 2

    diagnostic_plan = None
    service = None
    if diagnostic_requested:
        service = Hy3Service()
        versions = VersionInfo(
            model=service.settings.hy3_model, prompt_version=DEEP_AUDIT_PROMPT_VERSION,
            schema_version=DEEP_AUDIT_SCHEMA_VERSION,
            data_version=_load_live_manifest()["data_version"], code_version=_code_version(),
        )
        try:
            diagnostic_plan = build_context_diagnostic_plan(
                case_ids=args.case_id, target_claim_id=args.target_claim_id,
                target_block_id=args.target_block_id, diagnostic_id=args.diagnostic_id,
                repetitions=args.repeat_count, hy3_service=service, versions=versions,
            )
            plan = build_mode_plan(mode=args.mode, output_path=output_path, diagnostic_plan=diagnostic_plan)
        except ValueError:
            print("EVAL_REFUSED=invalid context diagnostic")
            return 2
        print(f"DIAGNOSTIC_ONLY=True PLAN_SHA256={_sha256_json(diagnostic_plan)}")
    else:
        plan = build_mode_plan(mode=args.mode, output_path=output_path)
    print(
        f"EVAL_PLAN={args.mode} "
        f"SLOTS={plan.total_slots} "
        f"RESUME_COMPLETE={plan.resume_complete_slots} "
        f"PENDING={plan.pending_slots} "
        f"LOGICAL_REQUESTS={plan.logical_requests_pending} "
        f"PROVIDER_ATTEMPTS_UPPER={plan.provider_attempts_upper} "
        f"COST_CNY_NO_RETRY={plan.cost_cny_no_retry:.6f} "
        f"COST_CNY_UPPER={plan.cost_cny_upper:.6f}"
    )
    if plan.logical_requests_pending and not args.confirm_cost:
        print("COST_CONFIRMATION_REQUIRED=True")
        return 2
    service = service or Hy3Service()
    if plan.logical_requests_pending and service.settings.paperlens_model_mode != "live":
        print("EVAL_REFUSED=LIVE_MODE_REQUIRED")
        return 2
    manifest = _load_live_manifest()
    versions = VersionInfo(
        model=service.settings.hy3_model,
        prompt_version=DEEP_AUDIT_PROMPT_VERSION,
        schema_version=DEEP_AUDIT_SCHEMA_VERSION,
        data_version=manifest["data_version"],
        code_version=_code_version(),
    )
    summary = run_cases(
        mode=args.mode,
        cases=(context_diagnostic_cases(diagnostic_plan) if diagnostic_plan is not None else load_mode_cases(args.mode)),
        output_path=output_path,
        versions=versions,
        evaluate=lambda case: evaluate_live_case(case, hy3_service=service, diagnostic_plan=diagnostic_plan),
        diagnostic_plan=diagnostic_plan,
    )
    print(
        f"EVAL_MODE={args.mode} "
        f"REQUESTED={summary.requested} "
        f"APPENDED={summary.appended} "
        f"SKIPPED={summary.skipped} "
        f"RECOVERED_INTERRUPTED={summary.recovered_interrupted} "
        f"OUTPUT={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
