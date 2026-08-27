import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from starlette.datastructures import UploadFile as StarletteUploadFile

from backend.app.audit_service import AuditService, AuditServiceError
from backend.app.api import AppError, health
from backend.app.document_service import (
    DocumentParseError,
    ParseQuality,
    ParseResult,
)
from backend.app.main import create_app
from backend.app.hy3_service import Hy3Service, Hy3ServiceError
from backend.app.models import (
    ComplianceContext,
    DeepAuditResult,
    ErrorResponse,
    EvidenceRecord,
    GeneratedBundle,
    SourceBlock,
)
from backend.app.project_store import ProjectStore
from backend.app.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"
VALID_PDF = b"%PDF-1.4\nPaperLens bounded upload fixture\n%%EOF"
VALID_AUDIT_REQUEST = {
    "source_disclosure_status": "present",
    "ai_assistance_disclosure_status": "present",
    "generated_content_label_applicability": "not_applicable",
    "generated_content_label_status": "not_applicable",
}


def source_blocks() -> list[SourceBlock]:
    return TypeAdapter(list[SourceBlock]).validate_json(
        (FIXTURES / "source_blocks.json").read_text(encoding="utf-8")
    )


def generated_bundle() -> GeneratedBundle:
    return GeneratedBundle.model_validate_json(
        (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")
    )


class RecordingDocumentService:
    def __init__(self, error: DocumentParseError | None = None) -> None:
        self.error = error
        self.paths: list[Path] = []

    def parse(self, pdf_path: Path) -> ParseResult:
        self.paths.append(Path(pdf_path))
        if self.error is not None:
            raise self.error
        blocks = source_blocks()
        return ParseResult(
            blocks=blocks,
            quality=ParseQuality(
                page_count=2,
                block_count=len(blocks),
                empty_page_rate=0.0,
                abnormal_character_rate=0.0,
                page_number_completeness_rate=1.0,
                bbox_availability_rate=1.0,
            ),
        )


@contextmanager
def api_client(
    tmp_path: Path,
    *,
    document_service: RecordingDocumentService | None = None,
    project_id: str = "project-001",
    max_pdf_mb: int = 1,
    model_mode: str = "mock",
    hy3_service: object | None = None,
    audit_service: object | None = None,
    hy3_api_key: str = "",
) -> Iterator[tuple[TestClient, ProjectStore, RecordingDocumentService]]:
    settings = Settings(
        _env_file=None,
        paperlens_env="test",
        paperlens_data_dir=tmp_path / "data",
        paperlens_model_mode=model_mode,
        max_pdf_mb=max_pdf_mb,
        hy3_api_key=hy3_api_key,
    )
    store = ProjectStore(settings.paperlens_data_dir)
    parser = document_service or RecordingDocumentService()
    app = create_app(
        settings_override=settings,
        project_store=store,
        document_service=parser,
        hy3_service=hy3_service,
        audit_service=audit_service,
        project_id_factory=lambda: project_id,
    )
    with TestClient(app) as client:
        yield client, store, parser


def test_health_contract() -> None:
    response = health()

    assert response.model_dump() == {
        "status": "ok",
        "service": "paperlens-api",
        "version": "0.1.0",
    }


def test_openapi_contains_health_and_stage_four_read_upload_paths() -> None:
    schema = create_app().openapi()

    assert set(schema["paths"]) == {
        "/api/health",
        "/api/projects",
        "/api/projects/{project_id}",
        "/api/projects/{project_id}/pdf",
        "/api/projects/{project_id}/generate",
        "/api/projects/{project_id}/audit",
    }


def test_app_error_uses_fixed_error_contract() -> None:
    error = AppError(
        "SCHEMA_INVALID",
        "The model response did not match the schema.",
        retryable=True,
    )

    assert error.status_code == 400
    assert error.payload == ErrorResponse(
        error_code="SCHEMA_INVALID",
        message="The model response did not match the schema.",
        retryable=True,
        details=None,
    )


def test_upload_rejects_unconfirmed_rights_before_read_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_read(
        _upload: StarletteUploadFile,
        _size: int = -1,
    ) -> bytes:
        raise AssertionError("rights rejection must happen before UploadFile.read")

    monkeypatch.setattr(StarletteUploadFile, "read", forbidden_read)
    with api_client(tmp_path) as (client, store, parser):
        response = client.post(
            "/api/projects",
            data={"rights_confirmed": "false"},
            files={"file": ("paper.pdf", VALID_PDF, "application/pdf")},
        )

        assert response.status_code == 403
        assert response.json()["error_code"] == "RIGHTS_NOT_CONFIRMED"
        assert parser.paths == []
        assert store.row_counts() == {
            "projects": 0,
            "versions": 0,
            "audits": 0,
            "patches": 0,
            "runs": 0,
        }
        assert list(settings_path for settings_path in tmp_path.rglob("source.pdf")) == []


def test_upload_rejects_non_pdf_filename_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_read(
        _upload: StarletteUploadFile,
        _size: int = -1,
    ) -> bytes:
        raise AssertionError("extension rejection must happen before reading")

    monkeypatch.setattr(StarletteUploadFile, "read", forbidden_read)
    with api_client(tmp_path) as (client, store, parser):
        response = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": ("paper.txt", VALID_PDF, "text/plain")},
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == "PDF_INVALID"
        assert parser.paths == []
        assert store.row_counts()["projects"] == 0


def test_upload_uses_one_bounded_read_and_rejects_oversize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_sizes: list[int] = []
    original_read = StarletteUploadFile.read

    async def recording_read(
        upload: StarletteUploadFile,
        size: int = -1,
    ) -> bytes:
        requested_sizes.append(size)
        return await original_read(upload, size)

    monkeypatch.setattr(StarletteUploadFile, "read", recording_read)
    limit = 1024 * 1024
    with api_client(tmp_path, max_pdf_mb=1) as (client, store, parser):
        response = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={
                "file": (
                    "paper.pdf",
                    b"%PDF-" + b"x" * limit,
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 413
        assert response.json()["error_code"] == "PDF_INVALID"
        assert response.json()["details"] == {"reason": "file_too_large"}
        assert requested_sizes == [limit + 1]
        assert parser.paths == []
        assert store.row_counts()["projects"] == 0
        assert list(tmp_path.rglob("source.pdf")) == []


def test_upload_rejects_pdf_without_magic_and_creates_no_side_effects(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, parser):
        response = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": ("paper.pdf", b"not a PDF", "application/pdf")},
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == "PDF_INVALID"
        assert parser.paths == []
        assert store.row_counts()["projects"] == 0


def test_upload_read_project_and_pdf_are_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, parser):
        upload = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={
                "file": ("../../client-name.pdf", VALID_PDF, "application/pdf")
            },
        )
        assert upload.status_code == 201
        payload = upload.json()
        assert payload["project_id"] == "project-001"
        assert payload["stage"] == "parsed"
        assert payload["source_block_count"] == 4
        assert parser.paths == [store.project_pdf_path("project-001")]

        stored_pdf = store.get_pdf_path("project-001")
        assert stored_pdf == (
            settings_data_root := (tmp_path / "data" / "projects" / "project-001" / "source.pdf")
        ).resolve()
        assert "client-name" not in str(stored_pdf)
        assert settings_data_root.read_bytes() == VALID_PDF

        first = client.get("/api/projects/project-001")
        second = client.get("/api/projects/project-001")
        assert first.status_code == 200
        assert second.json() == first.json()
        assert first.json()["stage"] == "parsed"
        assert first.json()["model_mode"] == "mock"
        assert first.json()["document"] is None
        assert first.json()["claims"] == []

        pdf = client.get("/api/projects/project-001/pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content == VALID_PDF


def test_parse_failure_is_persisted_safely_without_deleting_pdf(
    tmp_path: Path,
) -> None:
    parser = RecordingDocumentService(
        DocumentParseError(
            "PARSE_FAILED",
            "MinerU could not parse the PDF.",
            retryable=True,
            stderr_summary="provider raw output must not escape",
        )
    )
    with api_client(tmp_path, document_service=parser) as (client, store, _):
        response = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": ("paper.pdf", VALID_PDF, "application/pdf")},
        )

        assert response.status_code == 503
        body = response.json()
        assert body["error_code"] == "PARSE_FAILED"
        assert body["retryable"] is True
        assert body["details"] == {"project_id": "project-001"}
        assert "provider raw" not in json.dumps(body)
        assert store.get_pdf_path("project-001").read_bytes() == VALID_PDF

        view = client.get("/api/projects/project-001")
        assert view.status_code == 200
        assert view.json()["stage"] == "failed"
        assert view.json()["error_code"] == "PARSE_FAILED"
        assert view.json()["retryable_stage"] == "parsed"
        assert view.json()["document"] is None


def test_missing_project_and_pdf_use_stable_error_codes(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, store, _):
        project = client.get("/api/projects/missing")
        pdf = client.get("/api/projects/missing/pdf")
        assert project.status_code == 404
        assert project.json()["error_code"] == "PROJECT_NOT_FOUND"
        assert pdf.status_code == 404
        assert pdf.json()["error_code"] == "PDF_NOT_FOUND"

        uploaded = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": ("paper.pdf", VALID_PDF, "application/pdf")},
        )
        assert uploaded.status_code == 201
        store.get_pdf_path("project-001").unlink()
        missing_file = client.get("/api/projects/project-001/pdf")
        assert missing_file.status_code == 404
        assert missing_file.json()["error_code"] == "PDF_NOT_FOUND"


class RecordingGenerateService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], list[SourceBlock]]] = []
        self.claim_policies: list[str | None] = []

    def generate(
        self,
        *,
        claim_policy: str | None = None,
        paper_metadata: dict[str, object],
        source_blocks: list[SourceBlock],
    ) -> GeneratedBundle:
        self.claim_policies.append(claim_policy)
        self.calls.append((paper_metadata, source_blocks))
        return generated_bundle()


class ZeroClaimGenerateService(RecordingGenerateService):
    def generate(
        self,
        *,
        claim_policy: str | None = None,
        paper_metadata: dict[str, object],
        source_blocks: list[SourceBlock],
    ) -> GeneratedBundle:
        self.claim_policies.append(claim_policy)
        self.calls.append((paper_metadata, source_blocks))
        return generated_bundle().model_copy(update={"claims": []})


class RiskOnlyDeepAuditProvider:
    def __init__(self) -> None:
        self.calls: list[list[tuple[object, EvidenceRecord]]] = []

    def deep_audit(
        self,
        *,
        document: object,
        claim_evidence_pairs: list[tuple[object, EvidenceRecord]],
    ) -> DeepAuditResult:
        del document
        self.calls.append(claim_evidence_pairs)
        payload = json.loads(
            (FIXTURES / "deep_audit_valid.json").read_text(encoding="utf-8")
        )
        payload["semantic_judgments"] = []
        return DeepAuditResult.model_validate(payload)


class SequencedLiveCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.responses.pop(0))
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
            ),
        )


class SequencedLiveClient:
    def __init__(self, responses: list[str]) -> None:
        self.completions = SequencedLiveCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


class FailingGenerateService:
    def __init__(self, error: Hy3ServiceError) -> None:
        self.error = error
        self.calls = 0

    def generate(self, **_kwargs: object) -> GeneratedBundle:
        self.calls += 1
        raise self.error


class FailingQuickAudit:
    def __init__(self) -> None:
        self.calls = 0
        self.should_fail = True

    def quick_check(self, *_args: object) -> object:
        self.calls += 1
        if self.should_fail:
            raise AuditServiceError(
                "AUDIT_INCOMPLETE",
                "Quick check could not be completed.",
                retryable=True,
            )
        return AuditService().quick_check(*_args)


def upload_parsed_project(client: TestClient) -> None:
    response = client.post(
        "/api/projects",
        data={"rights_confirmed": "true"},
        files={"file": ("paper.pdf", VALID_PDF, "application/pdf")},
    )
    assert response.status_code == 201


def generate_project(
    client: TestClient,
    *,
    project_id: str = "project-001",
    claim_policy: str = "required",
):
    return client.post(
        f"/api/projects/{project_id}/generate",
        json={"claim_policy": claim_policy},
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"claim_policy": "optional"},
        {"claim_policy": "required", "source_blocks": []},
    ],
)
def test_generate_requires_strict_claim_policy_without_calling_service(
    tmp_path: Path,
    payload: dict[str, object] | None,
) -> None:
    service = RecordingGenerateService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)

        if payload is None:
            response = client.post("/api/projects/project-001/generate")
        else:
            response = client.post(
                "/api/projects/project-001/generate",
                json=payload,
            )

        assert response.status_code == 502
        assert response.json()["error_code"] == "AUDIT_INCOMPLETE"
        assert service.calls == []
        assert store.get_project_view(
            "project-001",
            model_mode="mock",
        ).stage.value == "parsed"


def test_request_validation_messages_are_route_specific_and_safe(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)

        generation = client.post(
            "/api/projects/project-001/generate",
            json={},
        )
        deep_audit = client.post(
            "/api/projects/project-001/audit",
            json={},
        )

        assert generation.status_code == 502
        assert generation.json() == {
            "error_code": "AUDIT_INCOMPLETE",
            "message": "The generation request is incomplete or invalid.",
            "retryable": False,
            "details": None,
        }
        assert deep_audit.status_code == 502
        assert deep_audit.json() == {
            "error_code": "AUDIT_INCOMPLETE",
            "message": "The deep-audit request is incomplete or invalid.",
            "retryable": False,
            "details": None,
        }
        serialized = json.dumps(
            [generation.json(), deep_audit.json()],
            ensure_ascii=False,
        )
        assert "Field required" not in serialized
        assert "validation" not in serialized.casefold()
        assert store.row_counts() == {
            "projects": 1,
            "versions": 0,
            "audits": 0,
            "patches": 0,
            "runs": 1,
        }


def test_generate_uses_only_saved_parse_snapshot_and_persists_quick_check(
    tmp_path: Path,
) -> None:
    service = RecordingGenerateService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)

        response = generate_project(client)

        assert response.status_code == 200
        payload = response.json()
        assert payload["project_id"] == "project-001"
        assert payload["stage"] == "quick_checked"
        assert payload["model_mode"] == "mock"
        assert payload["document"] == generated_bundle().document.model_dump(mode="json")
        assert payload["claims"] == [
            claim.model_dump(mode="json") for claim in generated_bundle().claims
        ]
        assert payload["evidence_records"]
        assert payload["quick_report"]["audit_status"] == "quick_complete"
        assert len(service.calls) == 1
        assert service.claim_policies == ["required"]
        assert service.calls[0][0] == {}
        assert service.calls[0][1] == source_blocks()

        first = client.get("/api/projects/project-001")
        second = client.get("/api/projects/project-001")
        assert first.json() == second.json()
        assert first.json()["stage"] == "quick_checked"
        assert first.json()["current_version_id"] == payload["version_id"]
        assert first.json()["evidence_records"] == payload["evidence_records"]
        assert first.json()["audit_report"] == payload["quick_report"]
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 1,
            "patches": 0,
            "runs": 3,
        }


def test_zero_claim_generation_can_complete_risk_only_api_audit(
    tmp_path: Path,
) -> None:
    generation = ZeroClaimGenerateService()
    risk_provider = RiskOnlyDeepAuditProvider()
    audit_service = AuditService(hy3_service=risk_provider)
    with api_client(
        tmp_path,
        hy3_service=generation,
        audit_service=audit_service,
    ) as (client, store, _):
        upload_parsed_project(client)

        generated = generate_project(
            client,
            claim_policy="must_be_empty",
        )
        assert generated.status_code == 200
        assert generated.json()["stage"] == "quick_checked"
        assert generated.json()["claims"] == []
        assert generated.json()["evidence_records"] == []
        assert generation.claim_policies == ["must_be_empty"]
        version_id = generated.json()["version_id"]

        audited = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )

        assert audited.status_code == 200
        payload = audited.json()
        assert payload["stage"] == "deep_audited"
        assert payload["version_id"] == version_id
        assert payload["audit_report"]["audit_status"] == "deep_complete"
        assert len(payload["audit_report"]["dimensions"]) == 8
        assert risk_provider.calls == [[]]
        project = client.get("/api/projects/project-001")
        assert project.status_code == 200
        assert project.json()["stage"] == "deep_audited"
        assert project.json()["error_code"] is None
        assert project.json()["claims"] == []
        assert project.json()["evidence_records"] == []
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 2,
            "patches": 0,
            "runs": 4,
        }


def test_zero_claim_live_provider_retries_invalid_risk_categories_and_completes_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_payload = json.loads(
        (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")
    )
    generation_payload["claims"] = []
    invalid_audit = json.loads(
        (FIXTURES / "deep_audit_valid.json").read_text(encoding="utf-8")
    )
    invalid_audit["semantic_judgments"] = []
    invalid_audit["risk_findings"] = invalid_audit["risk_findings"][:2]
    valid_audit = json.loads(
        (FIXTURES / "deep_audit_valid.json").read_text(encoding="utf-8")
    )
    valid_audit["semantic_judgments"] = []
    live_client = SequencedLiveClient(
        [
            json.dumps(generation_payload),
            json.dumps(invalid_audit),
            json.dumps(valid_audit),
        ]
    )
    live_settings = Settings(
        _env_file=None,
        paperlens_env="test",
        paperlens_data_dir=tmp_path / "live-service-data",
        paperlens_model_mode="live",
        hy3_api_key="unit-test-key",
        hy3_max_retries=2,
    )
    hy3_service = Hy3Service(settings=live_settings, client=live_client)
    monkeypatch.setattr(
        hy3_service,
        "_load_mock_response",
        lambda: pytest.fail("Live generation must not load Mock data"),
    )
    monkeypatch.setattr(
        hy3_service,
        "_load_mock_deep_audit_response",
        lambda: pytest.fail("Live deep audit must not load Mock data"),
    )

    with api_client(
        tmp_path,
        model_mode="live",
        hy3_service=hy3_service,
        audit_service=AuditService(hy3_service=hy3_service),
        hy3_api_key="unit-test-key",
    ) as (client, store, _):
        upload_parsed_project(client)
        generated = generate_project(
            client,
            claim_policy="must_be_empty",
        )

        assert generated.status_code == 200
        assert generated.json()["stage"] == "quick_checked"
        assert generated.json()["model_mode"] == "live"
        assert generated.json()["claims"] == []
        assert generated.json()["evidence_records"] == []
        assert (
            "claim_policy: must_be_empty"
            in live_client.completions.calls[0]["messages"][1]["content"]
        )

        audited = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )

        assert audited.status_code == 200
        body = audited.json()
        assert body["stage"] == "deep_audited"
        assert body["audit_report"]["audit_status"] == "deep_complete"
        assert len(body["audit_report"]["dimensions"]) == 8
        assert len(live_client.completions.calls) == 3
        retry_prompt = live_client.completions.calls[2]["messages"][1]["content"]
        assert "risk_category_coverage_invalid" in retry_prompt

        project = client.get("/api/projects/project-001")
        assert project.status_code == 200
        assert project.json()["stage"] == "deep_audited"
        assert project.json()["model_mode"] == "live"
        assert project.json()["error_code"] is None
        assert project.json()["claims"] == []
        assert project.json()["evidence_records"] == []
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 2,
            "patches": 0,
            "runs": 4,
        }


def test_generate_rejects_client_source_blocks_without_calling_service(
    tmp_path: Path,
) -> None:
    service = RecordingGenerateService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)

        response = client.post(
            "/api/projects/project-001/generate",
            json={
                "claim_policy": "required",
                "source_blocks": [source_blocks()[0].model_dump(mode="json")],
            },
        )

        assert response.status_code == 502
        assert response.json()["error_code"] == "AUDIT_INCOMPLETE"
        assert service.calls == []
        assert store.get_project_view(
            "project-001",
            model_mode="mock",
        ).stage.value == "parsed"
        assert store.row_counts()["versions"] == 0
        assert store.row_counts()["runs"] == 1


@pytest.mark.parametrize(
    ("error_code", "status_code", "retryable"),
    [
        ("HY3_CONFIG_MISSING", 503, False),
        ("HY3_UNAVAILABLE", 503, True),
        ("SCHEMA_INVALID", 502, False),
    ],
)
def test_generate_provider_or_schema_failure_stays_failed_without_mock(
    tmp_path: Path,
    error_code: str,
    status_code: int,
    retryable: bool,
) -> None:
    error = Hy3ServiceError(
        error_code,
        "The generation service failed safely.",
        retryable=retryable,
        field_error_summary="provider raw output SECRET-PROMPT",
        usage=(7, 3, 10),
    )
    service = FailingGenerateService(error)
    with api_client(
        tmp_path,
        model_mode="live",
        hy3_service=service,
    ) as (client, store, _):
        upload_parsed_project(client)

        response = generate_project(client)

        assert response.status_code == status_code
        body = response.json()
        assert body["error_code"] == error_code
        assert body["retryable"] is retryable
        assert body["details"] == {"project_id": "project-001"}
        assert "SECRET-PROMPT" not in json.dumps(body)
        assert service.calls == 1

        view = client.get("/api/projects/project-001").json()
        assert view["stage"] == "failed"
        assert view["model_mode"] == "live"
        assert view["error_code"] == error_code
        assert view["retryable_stage"] == "generated"
        assert view["document"] is None
        assert store.row_counts()["versions"] == 0
        assert store.row_counts()["audits"] == 0
        database_bytes = store.database_path.read_bytes()
        assert b"SECRET-PROMPT" not in database_bytes


def test_generation_success_and_failure_metadata_use_prompt_v4_without_prompt_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success_metadata: list[object] = []
    with api_client(tmp_path / "success") as (client, store, _):
        original_save_generated_version = store.save_generated_version

        def capture_success_metadata(**kwargs: object):
            success_metadata.append(kwargs["metadata"])
            return original_save_generated_version(**kwargs)

        monkeypatch.setattr(
            store,
            "save_generated_version",
            capture_success_metadata,
        )
        upload_parsed_project(client)

        assert generate_project(client).status_code == 200

        assert len(success_metadata) == 1
        assert success_metadata[0].prompt_version == "gen-v4"
        assert success_metadata[0].schema_version == "generated-bundle-v1"
        assert (
            "你是 PaperLens 的受约束学术内容处理模块。".encode("utf-8")
            not in store.database_path.read_bytes()
        )

    failure_metadata: list[object] = []
    failure = FailingGenerateService(
        Hy3ServiceError(
            "HY3_UNAVAILABLE",
            "The generation service failed safely.",
            retryable=True,
        )
    )
    with api_client(
        tmp_path / "failure",
        model_mode="live",
        hy3_service=failure,
    ) as (client, store, _):
        original_record_failure = store.record_failure

        def capture_failure_metadata(**kwargs: object) -> None:
            if kwargs["operation"] == "generate":
                failure_metadata.append(kwargs["metadata"])
            original_record_failure(**kwargs)

        monkeypatch.setattr(store, "record_failure", capture_failure_metadata)
        upload_parsed_project(client)

        response = generate_project(client)

        assert response.status_code == 503
        assert len(failure_metadata) == 1
        assert failure_metadata[0].prompt_version == "gen-v4"
        assert failure_metadata[0].schema_version == "generated-bundle-v1"
        assert (
            "你是 PaperLens 的受约束学术内容处理模块。".encode("utf-8")
            not in store.database_path.read_bytes()
        )


def test_quick_check_failure_keeps_generated_version_and_no_false_evidence(
    tmp_path: Path,
) -> None:
    generation = RecordingGenerateService()
    quick = FailingQuickAudit()
    with api_client(
        tmp_path,
        hy3_service=generation,
        audit_service=quick,
    ) as (client, store, _):
        upload_parsed_project(client)

        response = generate_project(client)

        assert response.status_code == 502
        assert response.json()["error_code"] == "AUDIT_INCOMPLETE"
        assert quick.calls == 1
        view = client.get("/api/projects/project-001").json()
        assert view["stage"] == "failed"
        assert view["error_code"] == "AUDIT_INCOMPLETE"
        assert view["retryable_stage"] == "quick_checked"
        assert view["current_version_id"] is not None
        assert view["document"] == generated_bundle().document.model_dump(mode="json")
        assert view["claims"]
        assert view["evidence_records"] == []
        assert view["audit_report"] is None
        stable_version_id = view["current_version_id"]
        stable_document = view["document"]
        stable_claims = view["claims"]
        assert len(generation.calls) == 1
        assert store.row_counts()["versions"] == 1
        assert store.row_counts()["audits"] == 0
        assert store.row_counts()["runs"] == 3

        quick.should_fail = False
        recovered = generate_project(client)
        recovered_payload = recovered.json()

        assert (recovered.status_code, recovered_payload.get("error_code")) == (
            200,
            None,
        )
        assert recovered_payload["stage"] == "quick_checked"
        assert recovered_payload["version_id"] == stable_version_id
        assert recovered_payload["document"] == stable_document
        assert recovered_payload["claims"] == stable_claims
        assert len(generation.calls) == 1
        assert quick.calls == 2
        first_read = client.get("/api/projects/project-001").json()
        second_read = client.get("/api/projects/project-001").json()
        assert first_read == second_read
        assert first_read["stage"] == "quick_checked"
        assert first_read["error_code"] is None
        assert first_read["retryable_stage"] is None
        assert first_read["current_version_id"] == stable_version_id
        assert first_read["document"] == stable_document
        assert first_read["claims"] == stable_claims
        assert first_read["evidence_records"] == recovered_payload["evidence_records"]
        assert first_read["audit_report"] == recovered_payload["quick_report"]
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 1,
            "patches": 0,
            "runs": 4,
        }
        snapshots = store.validate_all_snapshots()
        assert snapshots["evidence_json"] == 1
        assert snapshots["report_json"] == 1


@pytest.mark.parametrize(
    ("initial_policy", "retry_policy"),
    [
        ("required", "must_be_empty"),
        ("must_be_empty", "required"),
    ],
)
def test_quick_check_recovery_rejects_cross_policy_saved_bundle_reuse(
    tmp_path: Path,
    initial_policy: str,
    retry_policy: str,
) -> None:
    generation = (
        RecordingGenerateService()
        if initial_policy == "required"
        else ZeroClaimGenerateService()
    )
    quick = FailingQuickAudit()
    with api_client(
        tmp_path,
        hy3_service=generation,
        audit_service=quick,
    ) as (client, store, _):
        upload_parsed_project(client)

        first = generate_project(client, claim_policy=initial_policy)

        assert first.status_code == 502
        assert first.json()["error_code"] == "AUDIT_INCOMPLETE"
        stable = client.get("/api/projects/project-001").json()
        stable_counts = store.row_counts()
        assert stable["stage"] == "failed"
        assert stable["retryable_stage"] == "quick_checked"
        assert bool(stable["claims"]) is (initial_policy == "required")
        assert len(generation.calls) == 1
        assert quick.calls == 1

        rejected = generate_project(client, claim_policy=retry_policy)

        assert rejected.status_code == 502
        assert rejected.json() == {
            "error_code": "AUDIT_INCOMPLETE",
            "message": (
                "The generated claims did not satisfy the requested claim policy."
            ),
            "retryable": True,
            "details": {
                "project_id": "project-001",
                "version_id": stable["current_version_id"],
            },
        }
        assert len(generation.calls) == 1
        assert generation.claim_policies == [initial_policy]
        assert quick.calls == 1
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == stable_counts


def test_generate_recovers_generated_stage_after_quick_save_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = RecordingGenerateService()
    quick = FailingQuickAudit()
    quick.should_fail = False
    with api_client(
        tmp_path,
        hy3_service=generation,
        audit_service=quick,
    ) as (client, store, _):
        upload_parsed_project(client)
        original_save_quick_check = store.save_quick_check
        save_attempts = 0

        def interrupt_first_quick_save(**kwargs: object) -> None:
            nonlocal save_attempts
            save_attempts += 1
            if save_attempts == 1:
                raise RuntimeError("injected interruption before quick transaction")
            original_save_quick_check(**kwargs)

        monkeypatch.setattr(store, "save_quick_check", interrupt_first_quick_save)

        with pytest.raises(
            RuntimeError,
            match="injected interruption before quick transaction",
        ):
            generate_project(client)

        generated = client.get("/api/projects/project-001").json()
        stable_version_id = generated["current_version_id"]
        stable_document = generated["document"]
        stable_claims = generated["claims"]
        assert generated["stage"] == "generated"
        assert generated["error_code"] is None
        assert generated["retryable_stage"] is None
        assert generated["evidence_records"] == []
        assert generated["audit_report"] is None
        assert len(generation.calls) == 1
        assert quick.calls == 1
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 0,
            "patches": 0,
            "runs": 2,
        }

        recovered = generate_project(client)
        recovered_payload = recovered.json()

        assert (recovered.status_code, recovered_payload.get("error_code")) == (
            200,
            None,
        )
        assert recovered_payload["stage"] == "quick_checked"
        assert recovered_payload["version_id"] == stable_version_id
        assert recovered_payload["document"] == stable_document
        assert recovered_payload["claims"] == stable_claims
        assert len(generation.calls) == 1
        assert quick.calls == 2
        assert save_attempts == 2
        first_read = client.get("/api/projects/project-001").json()
        second_read = client.get("/api/projects/project-001").json()
        assert first_read == second_read
        assert first_read["stage"] == "quick_checked"
        assert first_read["current_version_id"] == stable_version_id
        assert first_read["document"] == stable_document
        assert first_read["claims"] == stable_claims
        assert first_read["evidence_records"] == recovered_payload["evidence_records"]
        assert first_read["audit_report"] == recovered_payload["quick_report"]
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 1,
            "patches": 0,
            "runs": 3,
        }


def test_generated_stage_recovery_rejects_cross_policy_after_save_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = RecordingGenerateService()
    quick = FailingQuickAudit()
    quick.should_fail = False
    with api_client(
        tmp_path,
        hy3_service=generation,
        audit_service=quick,
    ) as (client, store, _):
        upload_parsed_project(client)
        original_save_quick_check = store.save_quick_check
        save_attempts = 0

        def interrupt_first_quick_save(**kwargs: object) -> None:
            nonlocal save_attempts
            save_attempts += 1
            if save_attempts == 1:
                raise RuntimeError("injected interruption before quick transaction")
            original_save_quick_check(**kwargs)

        monkeypatch.setattr(store, "save_quick_check", interrupt_first_quick_save)

        with pytest.raises(
            RuntimeError,
            match="injected interruption before quick transaction",
        ):
            generate_project(client, claim_policy="required")

        stable = client.get("/api/projects/project-001").json()
        stable_counts = store.row_counts()
        assert stable["stage"] == "generated"
        assert stable["claims"]
        assert len(generation.calls) == 1
        assert quick.calls == 1
        assert save_attempts == 1

        rejected = generate_project(client, claim_policy="must_be_empty")

        assert rejected.status_code == 502
        assert rejected.json()["error_code"] == "AUDIT_INCOMPLETE"
        assert rejected.json()["message"] == (
            "The generated claims did not satisfy the requested claim policy."
        )
        assert len(generation.calls) == 1
        assert quick.calls == 1
        assert save_attempts == 1
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == stable_counts


def test_generate_missing_or_wrong_stage_is_stable_and_non_destructive(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        missing = generate_project(client, project_id="missing")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "PROJECT_NOT_FOUND"

        upload_parsed_project(client)
        first = generate_project(client)
        assert first.status_code == 200
        stable = client.get("/api/projects/project-001").json()

        repeated = generate_project(client)
        assert repeated.status_code == 409
        assert repeated.json()["error_code"] == "PROJECT_NOT_READY"
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts()["versions"] == 1
        assert store.row_counts()["audits"] == 1


class RecordingAuditWrapper:
    def __init__(self, delegate: AuditService) -> None:
        self.delegate = delegate
        self.contexts: list[ComplianceContext] = []
        self.evidence: list[list[EvidenceRecord]] = []

    def quick_check(self, *args: object):
        return self.delegate.quick_check(*args)

    def run_deep_audit(
        self,
        bundle: GeneratedBundle,
        evidence_records: list[EvidenceRecord],
        compliance_context: ComplianceContext,
    ):
        self.contexts.append(compliance_context)
        self.evidence.append(evidence_records)
        return self.delegate.run_deep_audit(
            bundle,
            evidence_records,
            compliance_context,
        )


class FailingDeepAudit:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def quick_check(self, *args: object):
        return AuditService().quick_check(*args)

    def run_deep_audit(self, *_args: object):
        self.calls += 1
        raise self.error


def explicit_mock_audit_service(tmp_path: Path) -> AuditService:
    settings = Settings(
        _env_file=None,
        paperlens_env="test",
        paperlens_data_dir=tmp_path / "delegate-data",
        paperlens_model_mode="mock",
    )
    return AuditService(Hy3Service(settings=settings))


def test_deep_audit_rebuilds_rights_and_persists_same_redacted_report(
    tmp_path: Path,
) -> None:
    wrapper = RecordingAuditWrapper(explicit_mock_audit_service(tmp_path))
    with api_client(tmp_path, audit_service=wrapper) as (client, store, _):
        upload_parsed_project(client)
        generated = generate_project(client)
        assert generated.status_code == 200
        quick_view = client.get("/api/projects/project-001").json()

        response = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["stage"] == "deep_audited"
        assert body["version_id"] == generated.json()["version_id"]
        assert body["audit_report"]["audit_status"] == "deep_complete"
        assert len(body["audit_report"]["dimensions"]) == 8
        assert body["audit_report"]["risk_assessment"] is not None
        assert wrapper.contexts[0].rights_or_license_confirmed is True
        assert wrapper.evidence[0] == [
            EvidenceRecord.model_validate(item)
            for item in quick_view["evidence_records"]
        ]

        first = client.get("/api/projects/project-001")
        second = client.get("/api/projects/project-001")
        assert first.json() == second.json()
        assert first.json()["stage"] == "deep_audited"
        assert first.json()["audit_report"] == body["audit_report"]
        assert first.json()["evidence_records"] == quick_view["evidence_records"]
        serialized = json.dumps(body, ensure_ascii=False)
        assert "未发现敏感信息。" not in serialized
        assert "No sensitive-content risk was detected." in serialized
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 2,
            "patches": 0,
            "runs": 4,
        }
        assert store.validate_all_snapshots()["report_json"] == 2
        database_bytes = store.database_path.read_bytes()
        assert "未发现敏感信息。".encode("utf-8") not in database_bytes


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {**VALID_AUDIT_REQUEST, "rights_or_license_confirmed": False},
        {**VALID_AUDIT_REQUEST, "evidence_records": []},
        {
            **VALID_AUDIT_REQUEST,
            "generated_content_label_applicability": "applicable",
            "generated_content_label_status": "not_applicable",
        },
        {},
    ],
)
def test_deep_audit_rejects_client_rights_evidence_or_invalid_contract(
    tmp_path: Path,
    invalid_payload: dict[str, object],
) -> None:
    wrapper = RecordingAuditWrapper(explicit_mock_audit_service(tmp_path))
    with api_client(tmp_path, audit_service=wrapper) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()

        response = client.post(
            "/api/projects/project-001/audit",
            json=invalid_payload,
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error_code"] == "AUDIT_INCOMPLETE"
        assert "input" not in body
        assert wrapper.contexts == []
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts()["audits"] == 1
        assert store.row_counts()["runs"] == 3


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (
            Hy3ServiceError(
                "HY3_UNAVAILABLE",
                "The deep-audit provider failed safely.",
                retryable=True,
                field_error_summary="raw provider SECRET-DEEP-PROMPT",
                usage=(11, 5, 16),
            ),
            503,
        ),
        (
            Hy3ServiceError(
                "SCHEMA_INVALID",
                "The deep-audit response schema was invalid.",
                retryable=False,
                field_error_summary="raw provider SECRET-DEEP-PROMPT",
                usage=(11, 5, 16),
            ),
            502,
        ),
        (
            AuditServiceError(
                "AUDIT_INCOMPLETE",
                "The deep audit could not be completed.",
                retryable=True,
            ),
            502,
        ),
    ],
)
def test_deep_audit_failure_preserves_quick_snapshot_and_safe_error(
    tmp_path: Path,
    error: BaseException,
    status_code: int,
) -> None:
    failing = FailingDeepAudit(error)
    generation = RecordingGenerateService()
    with api_client(
        tmp_path,
        model_mode="live",
        hy3_service=generation,
        audit_service=failing,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()

        response = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )

        assert response.status_code == status_code
        body = response.json()
        assert body["error_code"] == getattr(error, "error_code")
        assert "SECRET-DEEP-PROMPT" not in json.dumps(body)
        failed = client.get("/api/projects/project-001").json()
        assert failed["stage"] == "failed"
        assert failed["current_version_id"] == stable["current_version_id"]
        assert failed["document"] == stable["document"]
        assert failed["claims"] == stable["claims"]
        assert failed["evidence_records"] == stable["evidence_records"]
        assert failed["audit_report"] == stable["audit_report"]
        assert failed["error_code"] == getattr(error, "error_code")
        assert failed["retryable_stage"] == "deep_audited"
        assert store.row_counts()["versions"] == 1
        assert store.row_counts()["audits"] == 1
        assert store.row_counts()["runs"] == 4
        assert b"SECRET-DEEP-PROMPT" not in store.database_path.read_bytes()


def test_deep_audit_missing_or_evidence_not_ready_has_stable_error(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        missing = client.post(
            "/api/projects/missing/audit",
            json=VALID_AUDIT_REQUEST,
        )
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "PROJECT_NOT_FOUND"

        upload_parsed_project(client)
        not_ready = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )
        assert not_ready.status_code == 409
        assert not_ready.json()["error_code"] == "EVIDENCE_NOT_READY"
        assert store.row_counts()["audits"] == 0
        assert store.row_counts()["runs"] == 1


class FailOnSecondDeepAudit:
    def __init__(self, delegate: AuditService) -> None:
        self.delegate = delegate
        self.deep_calls = 0

    def quick_check(self, *args: object):
        return self.delegate.quick_check(*args)

    def run_deep_audit(self, *args: object):
        self.deep_calls += 1
        if self.deep_calls == 2:
            raise Hy3ServiceError(
                "HY3_UNAVAILABLE",
                "The deep-audit provider is temporarily unavailable.",
                retryable=True,
                field_error_summary="raw retry response SECRET-RETRY-RAW",
                usage=(13, 8, 21),
            )
        return self.delegate.run_deep_audit(*args)


def test_failed_reaudit_keeps_last_complete_audit_and_evidence(
    tmp_path: Path,
) -> None:
    switchable = FailOnSecondDeepAudit(explicit_mock_audit_service(tmp_path))
    with api_client(tmp_path, audit_service=switchable) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        completed = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )
        assert completed.status_code == 200
        stable = client.get("/api/projects/project-001").json()

        failed = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )

        assert failed.status_code == 503
        assert failed.json()["error_code"] == "HY3_UNAVAILABLE"
        recovered = client.get("/api/projects/project-001").json()
        assert recovered["stage"] == "failed"
        assert recovered["retryable_stage"] == "deep_audited"
        assert recovered["current_version_id"] == stable["current_version_id"]
        assert recovered["document"] == stable["document"]
        assert recovered["claims"] == stable["claims"]
        assert recovered["evidence_records"] == stable["evidence_records"]
        assert recovered["audit_report"] == stable["audit_report"]
        assert recovered["audit_report"]["audit_status"] == "deep_complete"
        assert store.row_counts()["versions"] == 1
        assert store.row_counts()["audits"] == 2
        assert store.row_counts()["runs"] == 5
        assert b"SECRET-RETRY-RAW" not in store.database_path.read_bytes()


def test_complete_mock_api_and_database_exclude_secrets_prompts_and_raw_output(
    tmp_path: Path,
) -> None:
    secret_key = "sk-stage4-unit-test-secret"
    with api_client(tmp_path, hy3_api_key=secret_key) as (client, store, _):
        upload = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": ("paper.pdf", VALID_PDF, "application/pdf")},
        )
        assert upload.status_code == 201
        project_id = upload.json()["project_id"]
        generation = generate_project(client, project_id=project_id)
        assert generation.status_code == 200
        audit = client.post(
            f"/api/projects/{project_id}/audit",
            json=VALID_AUDIT_REQUEST,
        )
        assert audit.status_code == 200
        project = client.get(f"/api/projects/{project_id}")
        pdf = client.get(f"/api/projects/{project_id}/pdf")
        assert project.status_code == 200
        assert pdf.status_code == 200
        assert pdf.content == VALID_PDF

        api_text = json.dumps(
            [upload.json(), generation.json(), audit.json(), project.json()],
            ensure_ascii=False,
        )
        database_bytes = store.database_path.read_bytes()
        forbidden_text = [
            secret_key,
            "你是 PaperLens 的受约束学术内容处理模块。",
            "你是 PaperLens 的受约束深度审计模块。",
            "证据直接说明了研究问题。",
            "未发现敏感信息。",
            "PaperLens bounded upload fixture",
        ]
        for value in forbidden_text:
            assert value not in api_text
            assert value.encode("utf-8") not in database_bytes
        assert store.inspect_schema().keys() == {
            "projects",
            "versions",
            "audits",
            "patches",
            "runs",
        }
        assert store.validate_all_snapshots() == {
            "parse_json": 1,
            "content_json": 1,
            "claims_json": 1,
            "evidence_json": 2,
            "report_json": 2,
            "metadata_json": 4,
            "usage_json": 4,
            "patch_json": 0,
        }


@pytest.mark.parametrize(
    ("data", "files", "status_code", "error_code"),
    [
        (
            {"rights_confirmed": "true"},
            None,
            400,
            "PDF_INVALID",
        ),
        (
            {},
            {"file": ("paper.pdf", VALID_PDF, "application/pdf")},
            403,
            "RIGHTS_NOT_CONFIRMED",
        ),
        (
            {"rights_confirmed": "yes"},
            {"file": ("paper.pdf", VALID_PDF, "application/pdf")},
            403,
            "RIGHTS_NOT_CONFIRMED",
        ),
    ],
)
def test_upload_missing_or_nonliteral_fields_use_stable_error_contract(
    tmp_path: Path,
    data: dict[str, str],
    files: dict[str, tuple[str, bytes, str]] | None,
    status_code: int,
    error_code: str,
) -> None:
    with api_client(tmp_path) as (client, store, parser):
        response = client.post("/api/projects", data=data, files=files)

        assert response.status_code == status_code
        assert response.json()["error_code"] == error_code
        assert parser.paths == []
        assert store.row_counts() == {
            "projects": 0,
            "versions": 0,
            "audits": 0,
            "patches": 0,
            "runs": 0,
        }
        assert list(tmp_path.rglob("source.pdf")) == []


def test_upload_string_file_field_is_pdf_invalid_without_side_effects(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, parser):
        response = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": (None, "ordinary-text-field")},
        )

        assert response.status_code == 400
        assert response.json() == {
            "error_code": "PDF_INVALID",
            "message": "A PDF file is required.",
            "retryable": False,
            "details": None,
        }
        assert "ordinary-text-field" not in response.text

        rights_response = client.post(
            "/api/projects",
            data={"rights_confirmed": "false"},
            files={"file": (None, "ordinary-text-field")},
        )

        assert rights_response.status_code == 403
        assert rights_response.json()["error_code"] == "RIGHTS_NOT_CONFIRMED"
        assert rights_response.json()["retryable"] is False
        assert "ordinary-text-field" not in rights_response.text
        assert parser.paths == []
        assert store.row_counts() == {
            "projects": 0,
            "versions": 0,
            "audits": 0,
            "patches": 0,
            "runs": 0,
        }
        assert list(tmp_path.rglob("source.pdf")) == []


def test_project_id_collision_does_not_delete_existing_project_pdf(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path, project_id="project-001") as (client, store, _):
        upload_parsed_project(client)
        stable_project = client.get("/api/projects/project-001").json()
        stable_pdf = store.get_pdf_path("project-001").read_bytes()

        collision = client.post(
            "/api/projects",
            data={"rights_confirmed": "true"},
            files={"file": ("second.pdf", VALID_PDF, "application/pdf")},
        )

        assert collision.status_code == 503
        assert collision.json()["error_code"] == "PARSE_FAILED"
        assert client.get("/api/projects/project-001").json() == stable_project
        pdf = client.get("/api/projects/project-001/pdf")
        assert pdf.status_code == 200
        assert pdf.content == stable_pdf
        assert store.get_pdf_path("project-001").read_bytes() == stable_pdf
        assert store.row_counts() == {
            "projects": 1,
            "versions": 0,
            "audits": 0,
            "patches": 0,
            "runs": 1,
        }
