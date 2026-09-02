from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from html import escape as escape_html
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError

from backend.app.audit_service import AuditServiceError
from backend.app.document_service import DocumentParseError
from backend.app.hy3_service import Hy3Service, Hy3ServiceError
from backend.app.models import (
    ComplianceContext,
    DeepAuditRequest,
    DeepAuditResponse,
    EditPatch,
    EvidenceSnapshot,
    ErrorResponse,
    GenerationRequest,
    GenerationResponse,
    HealthResponse,
    ParseQualitySnapshot,
    ParseSnapshot,
    ProjectCreateResponse,
    ProjectStage,
    ProjectView,
    RevisionRequest,
    RunMetadata,
    UsageSnapshot,
    VersionSummary,
)
from backend.app.prompts import (
    DEEP_AUDIT_PROMPT_VERSION,
    DEEP_AUDIT_SCHEMA_VERSION,
    GENERATION_PROMPT_VERSION,
    GENERATION_SCHEMA_VERSION,
    REVISION_PROMPT_VERSION,
    REVISION_SCHEMA_VERSION,
)
from backend.app.project_store import ProjectStore, StoreError
from backend.app.settings import Settings


router = APIRouter(prefix="/api")


_ERROR_STATUS = {
    "PDF_INVALID": 400,
    "RIGHTS_NOT_CONFIRMED": 403,
    "PROJECT_NOT_FOUND": 404,
    "PDF_NOT_FOUND": 404,
    "VERSION_NOT_FOUND": 404,
    "PATCH_NOT_FOUND": 404,
    "PROJECT_NOT_READY": 409,
    "EVIDENCE_NOT_READY": 409,
    "TARGET_STALE": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "PARSE_QUALITY_LOW": 422,
    "PATCH_INVALID": 422,
    "IDEMPOTENCY_KEY_INVALID": 422,
    "SCHEMA_INVALID": 502,
    "AUDIT_INCOMPLETE": 502,
    "PARSE_FAILED": 503,
    "HY3_CONFIG_MISSING": 503,
    "HY3_UNAVAILABLE": 503,
}

_IDEMPOTENCY_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MARKDOWN_CONTROL_WHITESPACE = re.compile(r"[\x09-\x0d\x1c-\x1f\x85]+")
_MARKDOWN_INLINE_SPECIAL = re.compile(r"([\\`*_\[\]~|{}#>])")
_MARKDOWN_LEADING_BULLET = re.compile(r"^([ ]{0,3})([-+])(?=[ ]|$|-)")
_MARKDOWN_LEADING_ORDERED = re.compile(r"^([ ]{0,3}\d+)([.)])(?=[ ]|$)")


def _escape_markdown_text(value: str) -> str:
    normalized = _MARKDOWN_CONTROL_WHITESPACE.sub(" ", value)
    escaped = escape_html(normalized, quote=False)
    escaped = _MARKDOWN_INLINE_SPECIAL.sub(r"\\\1", escaped)
    if len(escaped) - len(escaped.lstrip(" ")) >= 4:
        escaped = "&#32;" + escaped[1:]
    escaped = _MARKDOWN_LEADING_BULLET.sub(r"\1\\\2", escaped)
    return _MARKDOWN_LEADING_ORDERED.sub(r"\1\\\2", escaped)


class AppError(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = ErrorResponse(
            error_code=error_code,
            message=message,
            retryable=retryable,
            details=details,
        )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error.payload.model_dump(mode="json"),
        )

    @app.exception_handler(StoreError)
    async def handle_store_error(_request: Request, error: StoreError) -> JSONResponse:
        payload = ErrorResponse(
            error_code=error.error_code,
            message=error.message,
            retryable=error.retryable,
            details=None,
        )
        return JSONResponse(
            status_code=_ERROR_STATUS.get(error.error_code, 502),
            content=payload.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        route = request.scope.get("route")
        if request.method == "POST" and getattr(route, "path", None) == "/api/projects":
            body = getattr(error, "body", None)
            rights_confirmed = (
                body.get("rights_confirmed") if hasattr(body, "get") else None
            )
            if not isinstance(rights_confirmed, str) or rights_confirmed.casefold() != "true":
                payload = ErrorResponse(
                    error_code="RIGHTS_NOT_CONFIRMED",
                    message="Document processing rights or permission are not confirmed.",
                    retryable=False,
                    details=None,
                )
                return JSONResponse(
                    status_code=403,
                    content=payload.model_dump(mode="json"),
                )
            payload = ErrorResponse(
                error_code="PDF_INVALID",
                message="A PDF file is required.",
                retryable=False,
                details=None,
            )
            return JSONResponse(
                status_code=400,
                content=payload.model_dump(mode="json"),
            )

        route_path = getattr(route, "path", None)
        if route_path == "/api/projects/{project_id}/revisions":
            payload = ErrorResponse(
                error_code="PATCH_INVALID",
                message="The revision request is incomplete or invalid.",
                retryable=False,
                details=None,
            )
            return JSONResponse(
                status_code=422,
                content=payload.model_dump(mode="json"),
            )
        message = (
            "The generation request is incomplete or invalid."
            if route_path == "/api/projects/{project_id}/generate"
            else "The deep-audit request is incomplete or invalid."
        )
        payload = ErrorResponse(
            error_code="AUDIT_INCOMPLETE",
            message=message,
            retryable=False,
            details=None,
        )
        return JSONResponse(
            status_code=502,
            content=payload.model_dump(mode="json"),
        )


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="paperlens-api", version="0.1.0")


@router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    status_code=201,
)
async def create_project(
    request: Request,
    file: UploadFile | None = File(default=None),
    rights_confirmed: str | None = Form(default=None),
) -> ProjectCreateResponse:
    if rights_confirmed is None or rights_confirmed.casefold() != "true":
        raise AppError(
            "RIGHTS_NOT_CONFIRMED",
            "Document processing rights or permission are not confirmed.",
            status_code=403,
            retryable=False,
        )
    if file is None:
        raise AppError(
            "PDF_INVALID",
            "A PDF file is required.",
            status_code=400,
            retryable=False,
        )

    try:
        filename = file.filename or ""
        if not filename.casefold().endswith(".pdf"):
            raise AppError(
                "PDF_INVALID",
                "The uploaded file must use a .pdf extension.",
                status_code=400,
                retryable=False,
            )

        settings = _settings(request)
        size_limit = settings.max_pdf_mb * 1024 * 1024
        payload = await file.read(size_limit + 1)
        if len(payload) > size_limit:
            raise AppError(
                "PDF_INVALID",
                "The uploaded PDF exceeds the configured size limit.",
                status_code=413,
                retryable=False,
                details={"reason": "file_too_large"},
            )
        if not payload.startswith(b"%PDF-"):
            raise AppError(
                "PDF_INVALID",
                "The uploaded file does not contain a PDF header.",
                status_code=400,
                retryable=False,
            )

        project_id = str(request.app.state.project_id_factory())
        store = _store(request)
        pdf_path = store.project_pdf_path(project_id)
        temporary_path = pdf_path.parent / "source.upload"
        project_directory_created = False
        try:
            pdf_path.parent.mkdir(parents=True, exist_ok=False)
            project_directory_created = True
            temporary_path.write_bytes(payload)
            temporary_path.replace(pdf_path)
            store.create_project(
                project_id=project_id,
                pdf_sha256=sha256(payload).hexdigest(),
                rights_confirmed=True,
            )
        except (OSError, StoreError) as exc:
            if project_directory_created:
                _cleanup_unpersisted_pdf(temporary_path, pdf_path)
            if isinstance(exc, StoreError):
                raise exc
            raise AppError(
                "PARSE_FAILED",
                "The uploaded PDF could not be saved.",
                status_code=503,
                retryable=True,
            ) from exc

        parse_started = datetime.now(timezone.utc)
        try:
            parsed = request.app.state.document_service.parse(pdf_path)
            snapshot = ParseSnapshot(
                blocks=parsed.blocks,
                quality=ParseQualitySnapshot.model_validate(asdict(parsed.quality)),
            )
            parse_ended = datetime.now(timezone.utc)
            return store.save_parse_success(
                project_id=project_id,
                snapshot=snapshot,
                metadata=_success_metadata(),
                usage=_empty_usage(),
                started_at=parse_started,
                ended_at=parse_ended,
            )
        except DocumentParseError as error:
            parse_ended = datetime.now(timezone.utc)
            store.record_failure(
                project_id=project_id,
                version_id=None,
                operation="parse",
                mode="local",
                error_code=error.error_code,
                metadata=RunMetadata(
                    message=error.message,
                    retryable=error.retryable,
                    retryable_stage=ProjectStage.PARSED,
                    model=None,
                    prompt_version=None,
                    schema_version=None,
                ),
                usage=_empty_usage(),
                started_at=parse_started,
                ended_at=parse_ended,
            )
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS[error.error_code],
                retryable=error.retryable,
                details={"project_id": project_id},
            ) from error
        except StoreError as error:
            parse_ended = datetime.now(timezone.utc)
            store.record_failure(
                project_id=project_id,
                version_id=None,
                operation="parse",
                mode="local",
                error_code=error.error_code,
                metadata=RunMetadata(
                    message=error.message,
                    retryable=error.retryable,
                    retryable_stage=ProjectStage.PARSED,
                    model=None,
                    prompt_version=None,
                    schema_version=None,
                ),
                usage=_empty_usage(),
                started_at=parse_started,
                ended_at=parse_ended,
            )
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS.get(error.error_code, 502),
                retryable=error.retryable,
                details={"project_id": project_id},
            ) from error
    finally:
        await file.close()


@router.post(
    "/projects/{project_id}/generate",
    response_model=GenerationResponse,
)
async def generate_project(
    project_id: str,
    generation_request: GenerationRequest,
    request: Request,
) -> GenerationResponse:

    settings = _settings(request)
    store = _store(request)
    parse_snapshot, bundle, version_id = store.get_generation_inputs(project_id)
    if bundle is None:
        generation_started = datetime.now(timezone.utc)
        try:
            bundle = request.app.state.hy3_service.generate(
                claim_policy=generation_request.claim_policy,
                paper_metadata={},
                source_blocks=parse_snapshot.blocks,
            )
        except Hy3ServiceError as error:
            generation_ended = datetime.now(timezone.utc)
            store.record_failure(
                project_id=project_id,
                version_id=None,
                operation="generate",
                mode=settings.paperlens_model_mode,
                error_code=error.error_code,
                metadata=RunMetadata(
                    message=error.message,
                    retryable=error.retryable,
                    retryable_stage=ProjectStage.GENERATED,
                    model=settings.hy3_model,
                    prompt_version=GENERATION_PROMPT_VERSION,
                    schema_version=GENERATION_SCHEMA_VERSION,
                ),
                usage=_usage_snapshot(error.usage),
                started_at=generation_started,
                ended_at=generation_ended,
            )
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS[error.error_code],
                retryable=error.retryable,
                details={"project_id": project_id},
            ) from error

        generation_ended = datetime.now(timezone.utc)
        version_id, _version_no = store.save_generated_version(
            project_id=project_id,
            bundle=bundle,
            reason="initial_generation",
            metadata=RunMetadata(
                message=None,
                retryable=False,
                retryable_stage=None,
                model=settings.hy3_model,
                prompt_version=GENERATION_PROMPT_VERSION,
                schema_version=GENERATION_SCHEMA_VERSION,
            ),
            usage=_empty_usage(),
            started_at=generation_started,
            ended_at=generation_ended,
            mode=settings.paperlens_model_mode,
        )
    else:
        try:
            bundle = Hy3Service.validate_claim_policy(
                bundle,
                generation_request.claim_policy,
            )
        except Hy3ServiceError as error:
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS[error.error_code],
                retryable=error.retryable,
                details={"project_id": project_id, "version_id": version_id},
            ) from error

    quick_started = datetime.now(timezone.utc)
    try:
        records, quick_report = request.app.state.audit_service.quick_check(
            bundle,
            parse_snapshot.blocks,
        )
        evidence = EvidenceSnapshot(evidence_records=records)
    except (AuditServiceError, ValidationError) as error:
        quick_ended = datetime.now(timezone.utc)
        if isinstance(error, AuditServiceError):
            error_code = error.error_code
            message = error.message
            retryable = error.retryable
        else:
            error_code = "AUDIT_INCOMPLETE"
            message = "Quick check could not be completed."
            retryable = False
        store.record_failure(
            project_id=project_id,
            version_id=version_id,
            operation="quick_check",
            mode="local",
            error_code=error_code,
            metadata=RunMetadata(
                message=message,
                retryable=retryable,
                retryable_stage=ProjectStage.QUICK_CHECKED,
                model=None,
                prompt_version=None,
                schema_version=None,
            ),
            usage=_empty_usage(),
            started_at=quick_started,
            ended_at=quick_ended,
        )
        raise AppError(
            error_code,
            message,
            status_code=_ERROR_STATUS.get(error_code, 502),
            retryable=retryable,
            details={"project_id": project_id, "version_id": version_id},
        ) from error

    quick_ended = datetime.now(timezone.utc)
    store.save_quick_check(
        project_id=project_id,
        version_id=version_id,
        evidence=evidence,
        report=quick_report,
        metadata=_success_metadata(),
        usage=_empty_usage(),
        started_at=quick_started,
        ended_at=quick_ended,
    )
    return GenerationResponse(
        project_id=project_id,
        version_id=version_id,
        stage=ProjectStage.QUICK_CHECKED,
        model_mode=settings.paperlens_model_mode,
        document=bundle.document,
        claims=bundle.claims,
        evidence_records=evidence.evidence_records,
        quick_report=quick_report,
    )


@router.post(
    "/projects/{project_id}/audit",
    response_model=DeepAuditResponse,
)
def audit_project(
    project_id: str,
    audit_request: DeepAuditRequest,
    request: Request,
) -> DeepAuditResponse:
    settings = _settings(request)
    store = _store(request)
    bundle, evidence, rights_confirmed, version_id = store.get_deep_audit_inputs(
        project_id
    )
    try:
        compliance_context = ComplianceContext(
            rights_or_license_confirmed=rights_confirmed,
            source_disclosure_status=audit_request.source_disclosure_status,
            ai_assistance_disclosure_status=(
                audit_request.ai_assistance_disclosure_status
            ),
            generated_content_label_applicability=(
                audit_request.generated_content_label_applicability
            ),
            generated_content_label_status=(
                audit_request.generated_content_label_status
            ),
        )
    except ValidationError as error:
        raise AppError(
            "AUDIT_INCOMPLETE",
            "The deep-audit request is incomplete or invalid.",
            status_code=502,
            retryable=False,
        ) from error

    audit_started = datetime.now(timezone.utc)
    try:
        _safe_result, report = request.app.state.audit_service.run_deep_audit(
            bundle,
            evidence.evidence_records,
            compliance_context,
        )
    except (Hy3ServiceError, AuditServiceError, ValidationError) as error:
        audit_ended = datetime.now(timezone.utc)
        if isinstance(error, (Hy3ServiceError, AuditServiceError)):
            error_code = error.error_code
            message = error.message
            retryable = error.retryable
        else:
            error_code = "AUDIT_INCOMPLETE"
            message = "The deep audit could not be completed."
            retryable = False
        usage = (
            _usage_snapshot(error.usage)
            if isinstance(error, Hy3ServiceError)
            else _empty_usage()
        )
        store.record_failure(
            project_id=project_id,
            version_id=version_id,
            operation="deep_audit",
            mode=settings.paperlens_model_mode,
            error_code=error_code,
            metadata=RunMetadata(
                message=message,
                retryable=retryable,
                retryable_stage=ProjectStage.DEEP_AUDITED,
                model=settings.hy3_model,
                prompt_version=DEEP_AUDIT_PROMPT_VERSION,
                schema_version=DEEP_AUDIT_SCHEMA_VERSION,
            ),
            usage=usage,
            started_at=audit_started,
            ended_at=audit_ended,
        )
        raise AppError(
            error_code,
            message,
            status_code=_ERROR_STATUS.get(error_code, 502),
            retryable=retryable,
            details={"project_id": project_id, "version_id": version_id},
        ) from error

    audit_ended = datetime.now(timezone.utc)
    return store.save_deep_audit(
        project_id=project_id,
        version_id=version_id,
        report=report,
        metadata=RunMetadata(
            message=None,
            retryable=False,
            retryable_stage=None,
            model=settings.hy3_model,
            prompt_version=DEEP_AUDIT_PROMPT_VERSION,
            schema_version=DEEP_AUDIT_SCHEMA_VERSION,
        ),
        usage=_empty_usage(),
        started_at=audit_started,
        ended_at=audit_ended,
        mode=settings.paperlens_model_mode,
    )


@router.post(
    "/projects/{project_id}/revisions",
    response_model=EditPatch,
)
def create_revision_preview(
    project_id: str,
    revision_request: RevisionRequest,
    request: Request,
) -> EditPatch:
    settings = _settings(request)
    store = _store(request)
    hy3_service = request.app.state.hy3_service
    service_settings = getattr(hy3_service, "settings", None)
    revision_mode = getattr(
        service_settings,
        "paperlens_model_mode",
        settings.paperlens_model_mode,
    )
    revision_model = getattr(service_settings, "hy3_model", settings.hy3_model)
    revision_started = datetime.now(timezone.utc)
    stable_stage: ProjectStage | None = None
    try:
        if revision_request.scope.value == "sentence":
            sentence_id = revision_request.target_sentence_id
            if sentence_id is None:
                raise AppError(
                    "PATCH_INVALID",
                    "The sentence revision target is missing.",
                    status_code=422,
                    retryable=False,
                )
            base_version, current_text, evidence = (
                store.get_sentence_revision_inputs(
                    project_id,
                    base_version_id=revision_request.base_version_id,
                    sentence_id=sentence_id,
                )
            )
            stable_stage = store.get_project_view(
                project_id,
                model_mode=settings.paperlens_model_mode,
            ).stage
            patch = hy3_service.revise_sentence(
                base_version=base_version,
                sentence_id=sentence_id,
                current_text=current_text,
                evidence_records=evidence,
                user_instruction=revision_request.user_instruction,
            )
        else:
            base_version, document = store.get_document_revision_inputs(
                project_id,
                base_version_id=revision_request.base_version_id,
            )
            stable_stage = store.get_project_view(
                project_id,
                model_mode=settings.paperlens_model_mode,
            ).stage
            patch = hy3_service.revise_document(
                base_version=base_version,
                document=document,
                user_instruction=revision_request.user_instruction,
            )
    except Hy3ServiceError as error:
        revision_ended = datetime.now(timezone.utc)
        if stable_stage is None:
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS.get(error.error_code, 502),
                retryable=error.retryable,
            ) from error
        store.record_failure(
            project_id=project_id,
            version_id=None,
            operation="revision",
            mode=revision_mode,
            error_code=error.error_code,
            metadata=RunMetadata(
                message=error.message,
                retryable=error.retryable,
                retryable_stage=stable_stage,
                model=revision_model,
                prompt_version=REVISION_PROMPT_VERSION,
                schema_version=REVISION_SCHEMA_VERSION,
            ),
            usage=_usage_snapshot(error.usage),
            started_at=revision_started,
            ended_at=revision_ended,
        )
        raise AppError(
            error.error_code,
            error.message,
            status_code=_ERROR_STATUS.get(error.error_code, 502),
            retryable=error.retryable,
        ) from error

    revision_ended = datetime.now(timezone.utc)
    return store.save_patch_preview(
        project_id=project_id,
        base_version_id=revision_request.base_version_id,
        patch=patch,
        mode=revision_mode,
        metadata=RunMetadata(
            message=None,
            retryable=False,
            retryable_stage=None,
            model=revision_model,
            prompt_version=REVISION_PROMPT_VERSION,
            schema_version=REVISION_SCHEMA_VERSION,
        ),
        usage=_empty_usage(),
        started_at=revision_started,
        ended_at=revision_ended,
    )


@router.post(
    "/projects/{project_id}/revisions/{patch_id}/accept",
    response_model=GenerationResponse,
)
def accept_revision_patch(
    project_id: str,
    patch_id: str,
    request: Request,
) -> GenerationResponse:
    settings = _settings(request)
    store = _store(request)
    patch, bundle, source_blocks = store.get_patch_acceptance_inputs(
        project_id,
        patch_id=patch_id,
    )
    if (
        patch.scope.value == "sentence"
        and store.requires_sentence_claim_regeneration(patch)
    ):
        (
            original_claims,
            related_evidence,
            allowed_block_ids,
            reserved_claim_ids,
        ) = store.get_sentence_claim_regeneration_inputs(
            project_id,
            patch_id=patch_id,
        )
        try:
            regenerated = (
                request.app.state.hy3_service.regenerate_sentence_claims(
                    target_sentence_id=patch.target_sentence_ids[0],
                    accepted_after_text=patch.after_text,
                    original_claims=original_claims,
                    evidence_records=related_evidence,
                    allowed_block_ids=allowed_block_ids,
                    reserved_claim_ids=reserved_claim_ids,
                    user_instruction=(
                        "Regenerate claims for the accepted target sentence."
                    ),
                )
            )
        except Hy3ServiceError as error:
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS.get(error.error_code, 502),
                retryable=error.retryable,
            ) from error
        bundle = store.build_sentence_revision_bundle(
            patch=patch,
            provisional_bundle=bundle,
            regenerated_claims=regenerated.claims,
        )
    elif patch.scope.value == "document":
        try:
            bundle = request.app.state.hy3_service.regenerate_document_claims(
                document=bundle.document,
                source_blocks=source_blocks,
            )
        except Hy3ServiceError as error:
            raise AppError(
                error.error_code,
                error.message,
                status_code=_ERROR_STATUS.get(error.error_code, 502),
                retryable=error.retryable,
            ) from error
    quick_started = datetime.now(timezone.utc)
    try:
        records, quick_report = request.app.state.audit_service.quick_check(
            bundle,
            source_blocks,
        )
        evidence = EvidenceSnapshot(evidence_records=records)
    except (AuditServiceError, ValidationError) as error:
        if isinstance(error, AuditServiceError):
            error_code = error.error_code
            message = error.message
            retryable = error.retryable
        else:
            error_code = "AUDIT_INCOMPLETE"
            message = "Quick check could not be completed."
            retryable = False
        raise AppError(
            error_code,
            message,
            status_code=_ERROR_STATUS.get(error_code, 502),
            retryable=retryable,
        ) from error

    quick_ended = datetime.now(timezone.utc)
    version_id, _version_no = store.accept_patch(
        project_id=project_id,
        patch_id=patch_id,
        bundle=bundle,
        evidence=evidence,
        report=quick_report,
        metadata=_success_metadata(),
        usage=_empty_usage(),
        started_at=quick_started,
        ended_at=quick_ended,
    )
    return GenerationResponse(
        project_id=project_id,
        version_id=version_id,
        stage=ProjectStage.QUICK_CHECKED,
        model_mode=settings.paperlens_model_mode,
        document=bundle.document,
        claims=bundle.claims,
        evidence_records=evidence.evidence_records,
        quick_report=quick_report,
    )


@router.post(
    "/projects/{project_id}/revisions/{patch_id}/reject",
    status_code=204,
    response_class=Response,
)
def reject_revision_patch(
    project_id: str,
    patch_id: str,
    request: Request,
) -> Response:
    _store(request).reject_patch(project_id, patch_id=patch_id)
    return Response(status_code=204)


@router.post(
    "/projects/{project_id}/versions/{version_id}/restore",
    response_model=VersionSummary,
)
def restore_project_version(
    project_id: str,
    version_id: str,
    request: Request,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> VersionSummary:
    if idempotency_key is None or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise AppError(
            "IDEMPOTENCY_KEY_INVALID",
            "A valid Idempotency-Key header is required.",
            status_code=422,
            retryable=False,
        )
    key_hash = sha256(idempotency_key.encode("ascii")).hexdigest()
    return _store(request).restore_version(
        project_id,
        version_id=version_id,
        key_hash=key_hash,
    )


@router.get("/projects/{project_id}/export", response_class=Response)
def export_project_markdown(project_id: str, request: Request) -> Response:
    (
        document,
        version_id,
        version_no,
        created_at,
        pdf_sha256,
        source_model,
        source_mode,
    ) = _store(request).get_export_inputs(project_id)
    lines = [f"# {_escape_markdown_text(document.title)}", ""]
    for section in document.sections:
        lines.extend([f"## {_escape_markdown_text(section.heading)}", ""])
        for sentence in section.sentences:
            lines.extend([_escape_markdown_text(sentence.text), ""])
    lines.extend(
        [
            "---",
            "",
            "## 生成说明",
            "",
            f"- 来源论文说明：内容基于用户已确认处理权限的上传论文；文件指纹为 `{pdf_sha256}`。",
            "- AI 辅助说明：本文档由 PaperLens 在用户确认修改后辅助生成与检查，仍需人工复核。",
            f"- 生成时间：{created_at.isoformat().replace('+00:00', 'Z')}。",
            f"- 模型信息：{_escape_markdown_text(source_model)}（{source_mode}）。",
            f"- 当前版本：{version_id}（v{version_no}）。",
            "",
        ]
    )
    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="paperlens-{project_id}.md"'
            )
        },
    )


@router.get("/projects/{project_id}", response_model=ProjectView)
def read_project(project_id: str, request: Request) -> ProjectView:
    return _store(request).get_project_view(
        project_id,
        model_mode=_settings(request).paperlens_model_mode,
    )


@router.get("/projects/{project_id}/pdf", response_class=FileResponse)
def read_project_pdf(project_id: str, request: Request) -> FileResponse:
    return FileResponse(
        path=_store(request).get_pdf_path(project_id),
        media_type="application/pdf",
        filename="source.pdf",
    )


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _store(request: Request) -> ProjectStore:
    return request.app.state.project_store


def _success_metadata() -> RunMetadata:
    return RunMetadata(
        message=None,
        retryable=False,
        retryable_stage=None,
        model=None,
        prompt_version=None,
        schema_version=None,
    )


def _empty_usage() -> UsageSnapshot:
    return UsageSnapshot(
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
    )


def _usage_snapshot(
    usage: tuple[int | None, int | None, int | None],
) -> UsageSnapshot:
    return UsageSnapshot(
        prompt_tokens=usage[0],
        completion_tokens=usage[1],
        total_tokens=usage[2],
    )


def _cleanup_unpersisted_pdf(temporary_path: Path, pdf_path: Path) -> None:
    for path in (temporary_path, pdf_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        pdf_path.parent.rmdir()
    except OSError:
        pass
