import json
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Callable, Iterator

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
    EditPatch,
    ErrorResponse,
    EvidenceRecord,
    GeneratedBundle,
    SentenceClaimRegenerationResult,
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
RESTORE_KEY = "123e4567-e89b-42d3-a456-426614174000"
OTHER_RESTORE_KEY = "223e4567-e89b-42d3-a456-426614174001"
LOCAL_REVISION_PATCH_ID = "123e4567-e89b-42d3-a456-426614174010"
OTHER_REVISION_PATCH_ID = "223e4567-e89b-42d3-a456-426614174011"
STABLE_PROJECT_VIEW_FIELDS = (
    "current_version_id",
    "current_version_no",
    "document",
    "claims",
    "evidence_records",
    "audit_report",
    "versions",
)


def assert_stable_snapshot_unchanged(
    actual: dict[str, object],
    expected: dict[str, object],
) -> None:
    assert {
        field: actual[field] for field in STABLE_PROJECT_VIEW_FIELDS
    } == {
        field: expected[field] for field in STABLE_PROJECT_VIEW_FIELDS
    }


def operation_runs(
    store: ProjectStore,
    operation: str,
) -> list[dict[str, object]]:
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM runs WHERE operation = ? ORDER BY rowid",
            (operation,),
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(str(item.pop("metadata_json")))
        item["usage"] = json.loads(str(item.pop("usage_json")))
        result.append(item)
    return result


def database_rows(store: ProjectStore) -> dict[str, list[tuple[object, ...]]]:
    result: dict[str, list[tuple[object, ...]]] = {}
    with sqlite3.connect(store.database_path) as connection:
        for table in (
            "projects",
            "versions",
            "audits",
            "patches",
            "runs",
            "restore_idempotency",
        ):
            result[table] = [
                tuple(row)
                for row in connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'
                ).fetchall()
            ]
    return result


def assert_markdown_export_source(
    client: TestClient,
    store: ProjectStore,
    *,
    model: str,
    mode: str,
) -> str:
    project_before = client.get("/api/projects/project-001").json()
    counts_before = store.row_counts()
    rows_before = database_rows(store)

    response = client.get("/api/projects/project-001/export")

    assert response.status_code == 200
    markdown = response.content.decode("utf-8")
    assert f"- 模型信息：{model}（{mode}）。" in markdown
    assert client.get("/api/projects/project-001").json() == project_before
    assert store.row_counts() == counts_before
    assert database_rows(store) == rows_before
    return markdown


def source_blocks() -> list[SourceBlock]:
    return TypeAdapter(list[SourceBlock]).validate_json(
        (FIXTURES / "source_blocks.json").read_text(encoding="utf-8")
    )


def generated_bundle() -> GeneratedBundle:
    return GeneratedBundle.model_validate_json(
        (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")
    )


def document_revision_after_text(document: object, mutation: str) -> str:
    payload = document.model_dump(mode="json")
    sections = payload["sections"]
    if mutation == "added":
        sections[0]["sentences"].append(
            {"sentence_id": "s-added", "text": "An unapproved added sentence."}
        )
    elif mutation == "deleted":
        sections[0]["sentences"].pop()
    elif mutation == "replaced":
        sections[0]["sentences"][0]["sentence_id"] = "s-replaced"
    elif mutation == "duplicated":
        sections[1]["sentences"][0]["sentence_id"] = sections[0]["sentences"][0][
            "sentence_id"
        ]
    elif mutation == "moved":
        sections[0]["sentences"][0], sections[1]["sentences"][0] = (
            sections[1]["sentences"][0],
            sections[0]["sentences"][0],
        )
    elif mutation == "reordered":
        sections[0], sections[1] = sections[1], sections[0]
    else:
        raise AssertionError(f"unsupported test mutation: {mutation}")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
    project_id_factory: Callable[[], str] | None = None,
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
        project_id_factory=project_id_factory or (lambda: project_id),
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
        "/api/projects/{project_id}/revisions",
        "/api/projects/{project_id}/revisions/{patch_id}/accept",
        "/api/projects/{project_id}/revisions/{patch_id}/reject",
        "/api/projects/{project_id}/versions/{version_id}/restore",
        "/api/projects/{project_id}/export",
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
            "restore_idempotency": 0,
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


class RecordingRevisionService(RecordingGenerateService):
    def __init__(self) -> None:
        super().__init__()
        self.sentence_revisions: list[dict[str, object]] = []
        self.document_revisions: list[dict[str, object]] = []
        self.claim_regenerations: list[dict[str, object]] = []
        self.sentence_claim_regenerations: list[dict[str, object]] = []

    def revise_sentence(self, **kwargs: object) -> EditPatch:
        self.sentence_revisions.append(kwargs)
        before_text = str(kwargs["current_text"])
        return EditPatch(
            patch_id="patch-api-sentence",
            base_version=int(kwargs["base_version"]),
            scope="sentence",
            target_sentence_ids=[str(kwargs["sentence_id"])],
            before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
            before_text=before_text,
            after_text=f"{before_text}!",
            reason="保持事实不变并简化表达。",
            fact_changed=False,
            evidence_changed=False,
        )

    def revise_document(self, **kwargs: object) -> EditPatch:
        self.document_revisions.append(kwargs)
        document = kwargs["document"]
        before_text = document.model_dump_json()
        revised_payload = document.model_dump(mode="json")
        revised_payload["title"] = "修订后的本科生论文解读"
        revised_payload["sections"][0]["heading"] = "修订后的研究问题"
        revised_payload["sections"][0]["sentences"][0]["text"] += (
            " Revised wording."
        )
        revised = type(document).model_validate(revised_payload)
        return EditPatch(
            patch_id="patch-api-document",
            base_version=int(kwargs["base_version"]),
            scope="document",
            target_sentence_ids=[],
            before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
            before_text=before_text,
            after_text=revised.model_dump_json(),
            reason="统一五区表达。",
            fact_changed=False,
            evidence_changed=False,
        )

    def regenerate_document_claims(self, **kwargs: object) -> GeneratedBundle:
        self.claim_regenerations.append(kwargs)
        document = kwargs["document"]
        claims = [
            claim.model_copy(
                update={
                    "claim_id": f"revised-{claim.claim_id}",
                    "text": f"{claim.text}（重建）",
                }
            )
            for claim in generated_bundle().claims
        ]
        return GeneratedBundle(document=document, claims=claims)

    def regenerate_sentence_claims(
        self,
        **kwargs: object,
    ) -> SentenceClaimRegenerationResult:
        self.sentence_claim_regenerations.append(kwargs)
        original_claims = kwargs["original_claims"]
        target_sentence_id = str(kwargs["target_sentence_id"])
        accepted_after_text = str(kwargs["accepted_after_text"])
        original_claim = original_claims[0]
        return SentenceClaimRegenerationResult(
            claims=[
                original_claim.model_copy(
                    update={
                        "claim_id": "replacement-z",
                        "sentence_id": target_sentence_id,
                        "text": accepted_after_text,
                    }
                ),
                original_claim.model_copy(
                    update={
                        "claim_id": "replacement-a",
                        "sentence_id": target_sentence_id,
                        "text": f"{accepted_after_text} Supporting detail.",
                    }
                ),
            ]
        )


class SensitiveRevisionService(RecordingRevisionService):
    def revise_sentence(self, **kwargs: object) -> EditPatch:
        self.sentence_revisions.append(kwargs)
        before_text = str(kwargs["current_text"])
        return EditPatch(
            patch_id="patch-api-sensitive-sentence",
            base_version=int(kwargs["base_version"]),
            scope="sentence",
            target_sentence_ids=[str(kwargs["sentence_id"])],
            before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
            before_text=before_text,
            after_text=f"{before_text} PATCH_BODY_SENTINEL",
            reason="RAW_SUPPLIER_RESPONSE_SENTINEL",
            fact_changed=False,
            evidence_changed=False,
        )


class FailingRevisionPreviewService(RecordingRevisionService):
    def revise_sentence(self, **kwargs: object) -> EditPatch:
        self.sentence_revisions.append(kwargs)
        raise Hy3ServiceError(
            "HY3_UNAVAILABLE",
            "The revision provider failed safely.",
            retryable=True,
            retries=1,
            usage=(23, 13, 36),
        )


class IdentityChangingDocumentRevisionService(RecordingRevisionService):
    def __init__(self, mutation: str) -> None:
        super().__init__()
        self.mutation = mutation

    def revise_document(self, **kwargs: object) -> EditPatch:
        self.document_revisions.append(kwargs)
        document = kwargs["document"]
        before_text = document.model_dump_json()
        return EditPatch(
            patch_id=f"patch-api-document-identity-{self.mutation}",
            base_version=int(kwargs["base_version"]),
            scope="document",
            target_sentence_ids=[],
            before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
            before_text=before_text,
            after_text=document_revision_after_text(document, self.mutation),
            reason="Attempt to change the sentence identity skeleton.",
            fact_changed=True,
            evidence_changed=True,
        )


class FailingClaimRegenerationService(RecordingRevisionService):
    def regenerate_document_claims(self, **kwargs: object) -> GeneratedBundle:
        self.claim_regenerations.append(kwargs)
        raise Hy3ServiceError(
            "HY3_UNAVAILABLE",
            "Claim regeneration is temporarily unavailable.",
            retryable=True,
        )


class UnsafeSentenceRevisionService(RecordingRevisionService):
    def __init__(
        self,
        *,
        fact_changed: bool,
        evidence_changed: bool,
        after_text: str,
    ) -> None:
        super().__init__()
        self.fact_changed = fact_changed
        self.evidence_changed = evidence_changed
        self.after_text = after_text

    def revise_sentence(self, **kwargs: object) -> EditPatch:
        self.sentence_revisions.append(kwargs)
        before_text = str(kwargs["current_text"])
        return EditPatch(
            patch_id="patch-api-unsafe-sentence",
            base_version=int(kwargs["base_version"]),
            scope="sentence",
            target_sentence_ids=[str(kwargs["sentence_id"])],
            before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
            before_text=before_text,
            after_text=self.after_text,
            reason="Exercise the code-owned sentence acceptance boundary.",
            fact_changed=self.fact_changed,
            evidence_changed=self.evidence_changed,
        )


class FailingSentenceClaimRegenerationService(UnsafeSentenceRevisionService):
    def __init__(self, *, error_code: str, retryable: bool) -> None:
        target = generated_bundle().document.sections[0].sentences[0]
        super().__init__(
            fact_changed=False,
            evidence_changed=False,
            after_text=f"{target.text} Non-trivial revision.",
        )
        self.error_code = error_code
        self.retryable = retryable

    def regenerate_sentence_claims(
        self,
        **kwargs: object,
    ) -> SentenceClaimRegenerationResult:
        self.sentence_claim_regenerations.append(kwargs)
        raise Hy3ServiceError(
            self.error_code,
            "Sentence claim regeneration failed safely.",
            retryable=self.retryable,
        )


class CountingAuditService:
    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.quick_calls = 0
        self.quick_inputs: list[tuple[object, ...]] = []
        self.quick_outputs: list[object] = []

    def quick_check(self, *args: object):
        self.quick_calls += 1
        self.quick_inputs.append(args)
        result = self.delegate.quick_check(*args)
        self.quick_outputs.append(result)
        return result

    def run_deep_audit(self, *args: object):
        return self.delegate.run_deep_audit(*args)


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
            "restore_idempotency": 0,
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
        assert first.json()["pending_patch"] is None
        assert first.json()["current_version_id"] == payload["version_id"]
        assert first.json()["evidence_records"] == payload["evidence_records"]
        assert first.json()["audit_report"] == payload["quick_report"]
        assert store.row_counts() == {
            "projects": 1,
            "versions": 1,
            "audits": 1,
            "patches": 0,
            "runs": 3,
            "restore_idempotency": 0,
        }


def test_sentence_revision_preview_is_bounded_and_does_not_overwrite_current_version(
    tmp_path: Path,
) -> None:
    service = SensitiveRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable_before = client.get("/api/projects/project-001").json()
        target = stable_before["document"]["sections"][0]["sentences"][0]

        response = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable_before["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": (
                    "USER_INSTRUCTION_SENTINEL PROMPT_SENTINEL"
                ),
            },
        )

        assert response.status_code == 200
        assert response.json()["scope"] == "sentence"
        assert response.json()["before_text"] == target["text"]
        assert len(service.sentence_revisions) == 1
        call = service.sentence_revisions[0]
        assert call["sentence_id"] == target["sentence_id"]
        assert call["current_text"] == target["text"]
        assert all(record.quote_verified for record in call["evidence_records"])
        assert "document" not in call
        assert "versions" not in call
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == response.json()
        assert_stable_snapshot_unchanged(pending, stable_before)
        assert store.row_counts()["versions"] == 1
        assert store.row_counts()["patches"] == 1
        revision_runs = operation_runs(store, "revision")
        assert len(revision_runs) == 1
        revision_run = revision_runs[0]
        assert revision_run["version_id"] is None
        assert revision_run["operation"] == "revision"
        assert revision_run["mode"] == "mock"
        assert revision_run["status"] == "succeeded"
        assert revision_run["error_code"] is None
        assert revision_run["metadata"] == {
            "message": None,
            "retryable": False,
            "retryable_stage": None,
            "model": "hy3",
            "prompt_version": "revision-v2",
            "schema_version": "edit-patch-v1",
        }
        assert revision_run["usage"] == {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
        serialized_run = json.dumps(revision_run, ensure_ascii=False)
        for secret in {
            "USER_INSTRUCTION_SENTINEL",
            "PROMPT_SENTINEL",
            "PATCH_BODY_SENTINEL",
            "RAW_SUPPLIER_RESPONSE_SENTINEL",
        }:
            assert secret not in serialized_run


@pytest.mark.parametrize("stable_stage", ["quick_checked", "deep_audited"])
def test_revision_provider_failure_records_one_safe_run_without_stable_writes(
    tmp_path: Path,
    stable_stage: str,
) -> None:
    service = FailingRevisionPreviewService()
    with api_client(
        tmp_path,
        model_mode="live",
        hy3_service=service,
        audit_service=explicit_mock_audit_service(tmp_path),
        hy3_api_key="unit-test-key",
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        if stable_stage == "deep_audited":
            deep_audit = client.post(
                "/api/projects/project-001/audit",
                json=VALID_AUDIT_REQUEST,
            )
            assert deep_audit.status_code == 200
        stable = client.get("/api/projects/project-001").json()
        assert stable["stage"] == stable_stage
        target = stable["document"]["sections"][0]["sentences"][0]
        counts = store.row_counts()
        snapshots = store.validate_all_snapshots()

        response = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": (
                    "USER_INSTRUCTION_SENTINEL PROMPT_SENTINEL"
                ),
            },
        )

        assert response.status_code == 503
        assert response.json()["error_code"] == "HY3_UNAVAILABLE"
        assert response.json()["retryable"] is True
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == {**counts, "runs": counts["runs"] + 1}
        after_snapshots = store.validate_all_snapshots()
        assert after_snapshots == {
            **snapshots,
            "metadata_json": snapshots["metadata_json"] + 1,
            "usage_json": snapshots["usage_json"] + 1,
        }
        revision_runs = operation_runs(store, "revision")
        assert len(revision_runs) == 1
        failed_run = revision_runs[0]
        assert failed_run["version_id"] is None
        assert failed_run["mode"] == "live"
        assert failed_run["status"] == "failed"
        assert failed_run["error_code"] == "HY3_UNAVAILABLE"
        assert failed_run["metadata"] == {
            "message": "The revision provider failed safely.",
            "retryable": True,
            "retryable_stage": stable_stage,
            "model": "hy3",
            "prompt_version": "revision-v2",
            "schema_version": "edit-patch-v1",
        }
        assert failed_run["usage"] == {
            "prompt_tokens": 23,
            "completion_tokens": 13,
            "total_tokens": 36,
        }
        serialized_run = json.dumps(failed_run, ensure_ascii=False)
        for secret in {
            "USER_INSTRUCTION_SENTINEL",
            "PROMPT_SENTINEL",
            "RAW_SUPPLIER_RESPONSE_SENTINEL",
            "unit-test-key",
        }:
            assert secret not in serialized_run


def test_mock_revision_can_preview_same_input_again_after_rejection(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]
        request_payload = {
            "base_version_id": stable["current_version_id"],
            "scope": "sentence",
            "target_sentence_id": target["sentence_id"],
            "user_instruction": "保持事实不变并简化表达。",
        }

        first = client.post(
            "/api/projects/project-001/revisions",
            json=request_payload,
        )
        assert first.status_code == 200
        first_patch_id = first.json()["patch_id"]
        first_pending = client.get("/api/projects/project-001").json()
        assert first_pending["stage"] == "patch_pending"
        assert first_pending["pending_patch"] == first.json()
        assert_stable_snapshot_unchanged(first_pending, stable)
        assert client.post(
            f"/api/projects/project-001/revisions/{first_patch_id}/reject"
        ).status_code == 204
        rejected_view = client.get("/api/projects/project-001").json()
        assert rejected_view["stage"] == stable["stage"]
        assert rejected_view["pending_patch"] is None
        assert_stable_snapshot_unchanged(rejected_view, stable)

        second = client.post(
            "/api/projects/project-001/revisions",
            json=request_payload,
        )

        assert second.status_code == 200
        second_patch_id = second.json()["patch_id"]
        assert second_patch_id != first_patch_id
        assert store.inspect_patch_status(
            "project-001",
            first_patch_id,
        ) == "rejected"
        assert store.inspect_patch_status(
            "project-001",
            second_patch_id,
        ) == "pending"
        assert store.row_counts()["patches"] == 2
        second_pending = client.get("/api/projects/project-001").json()
        assert second_pending["stage"] == "patch_pending"
        assert second_pending["pending_patch"] == second.json()
        assert_stable_snapshot_unchanged(second_pending, stable)
        revision_before_accept = operation_runs(store, "revision")
        assert len(revision_before_accept) == 2
        assert [run["version_id"] for run in revision_before_accept] == [None, None]

        accepted = client.post(
            f"/api/projects/project-001/revisions/{second_patch_id}/accept"
        )

        assert accepted.status_code == 200
        revision_after_accept = operation_runs(store, "revision")
        assert len(revision_after_accept) == 2
        assert revision_after_accept[0]["id"] == revision_before_accept[0]["id"]
        assert revision_after_accept[0]["version_id"] is None
        assert revision_after_accept[1]["id"] == revision_before_accept[1]["id"]
        assert revision_after_accept[1]["version_id"] == accepted.json()["version_id"]


def test_second_preview_while_patch_pending_returns_not_ready_without_side_effects(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]
        request_payload = {
            "base_version_id": stable["current_version_id"],
            "scope": "sentence",
            "target_sentence_id": target["sentence_id"],
            "user_instruction": "Create one pending preview only.",
        }
        first = client.post(
            "/api/projects/project-001/revisions",
            json=request_payload,
        )
        assert first.status_code == 200
        pending = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        snapshots = store.validate_all_snapshots()

        second = client.post(
            "/api/projects/project-001/revisions",
            json={**request_payload, "user_instruction": "Do not replace the first."},
        )

        assert second.status_code == 409
        assert second.json()["error_code"] == "PROJECT_NOT_READY"
        assert second.json()["retryable"] is False
        assert client.get("/api/projects/project-001").json() == pending
        assert store.row_counts() == counts
        assert store.validate_all_snapshots() == snapshots
        assert store.inspect_patch_status(
            "project-001",
            first.json()["patch_id"],
        ) == "pending"
        assert len(service.sentence_revisions) == 1


def test_get_project_recovers_pending_patch_after_store_and_app_reopen(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, _store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "Persist this preview across reload.",
            },
        )
        assert preview.status_code == 200

    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        reloaded = client.get("/api/projects/project-001")

        assert reloaded.status_code == 200
        payload = reloaded.json()
        assert payload["stage"] == "patch_pending"
        assert payload["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(payload, stable)
        assert store.inspect_patch_status(
            "project-001",
            preview.json()["patch_id"],
        ) == "pending"


def test_same_mock_revision_input_across_projects_has_distinct_patch_ids(
    tmp_path: Path,
) -> None:
    project_ids = iter(["project-001", "project-002"])
    with api_client(
        tmp_path,
        project_id_factory=lambda: next(project_ids),
    ) as (client, store, _):
        upload_parsed_project(client)
        upload_parsed_project(client)
        assert generate_project(client, project_id="project-001").status_code == 200
        assert generate_project(client, project_id="project-002").status_code == 200

        previews = []
        for project_id in ("project-001", "project-002"):
            stable = client.get(f"/api/projects/{project_id}").json()
            target = stable["document"]["sections"][0]["sentences"][0]
            previews.append(
                client.post(
                    f"/api/projects/{project_id}/revisions",
                    json={
                        "base_version_id": stable["current_version_id"],
                        "scope": "sentence",
                        "target_sentence_id": target["sentence_id"],
                        "user_instruction": "保持事实不变并简化表达。",
                    },
                )
            )

        assert [response.status_code for response in previews] == [200, 200]
        assert previews[0].json()["patch_id"] != previews[1].json()["patch_id"]
        assert store.row_counts()["patches"] == 2


def test_live_revision_wrong_patch_id_echo_returns_schema_invalid_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.hy3_service.uuid4",
        lambda: LOCAL_REVISION_PATCH_ID,
        raising=False,
    )
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]
        supplier_payload = {
            "patch_id": OTHER_REVISION_PATCH_ID,
            "base_version": 1,
            "scope": "sentence",
            "target_sentence_ids": [target["sentence_id"]],
            "before_hash": sha256(target["text"].encode("utf-8")).hexdigest(),
            "before_text": target["text"],
            "after_text": f"{target['text']} PATCH_BODY_SENTINEL",
            "reason": "RAW_SUPPLIER_RESPONSE_SENTINEL",
            "fact_changed": False,
            "evidence_changed": False,
        }
        supplier_output = json.dumps(supplier_payload, ensure_ascii=False)
        live_client = SequencedLiveClient([supplier_output] * 3)
        live_settings = Settings(
            _env_file=None,
            paperlens_env="test",
            paperlens_data_dir=tmp_path / "live-revision-data",
            paperlens_model_mode="live",
            hy3_api_key="unit-test-key",
        )
        live_service = Hy3Service(settings=live_settings, client=live_client)
        monkeypatch.setattr(
            live_service,
            "_load_mock_response",
            lambda: pytest.fail("Live revision must not load Mock data"),
        )
        client.app.state.hy3_service = live_service
        counts = store.row_counts()
        snapshots = store.validate_all_snapshots()

        response = client.post(
            "/api/projects/project-001/revisions",
            json={
                    "base_version_id": stable["current_version_id"],
                    "scope": "sentence",
                    "target_sentence_id": target["sentence_id"],
                    "user_instruction": (
                        "USER_INSTRUCTION_SENTINEL PROMPT_SENTINEL"
                    ),
            },
        )

        assert response.status_code == 502
        assert response.json()["error_code"] == "SCHEMA_INVALID"
        assert response.json()["retryable"] is False
        assert len(live_client.completions.calls) == 3
        assert [
            call["response_format"]["json_schema"]["schema"]["properties"][
                "patch_id"
            ]["const"]
            for call in live_client.completions.calls
        ] == [LOCAL_REVISION_PATCH_ID] * 3
        assert all(
            LOCAL_REVISION_PATCH_ID in call["messages"][1]["content"]
            for call in live_client.completions.calls
        )
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == {**counts, "runs": counts["runs"] + 1}
        after_snapshots = store.validate_all_snapshots()
        assert after_snapshots == {
            **snapshots,
            "metadata_json": snapshots["metadata_json"] + 1,
            "usage_json": snapshots["usage_json"] + 1,
        }
        revision_runs = operation_runs(store, "revision")
        assert len(revision_runs) == 1
        failed_run = revision_runs[0]
        assert failed_run["version_id"] is None
        assert failed_run["mode"] == "live"
        assert failed_run["status"] == "failed"
        assert failed_run["error_code"] == "SCHEMA_INVALID"
        assert failed_run["metadata"] == {
            "message": (
                "The Hy3 revision response did not echo the assigned "
                "patch identifier."
            ),
            "retryable": False,
            "retryable_stage": "quick_checked",
            "model": "hy3",
            "prompt_version": "revision-v2",
            "schema_version": "edit-patch-v1",
        }
        assert failed_run["usage"] == {
            "prompt_tokens": 33,
            "completion_tokens": 21,
            "total_tokens": 54,
        }
        serialized_run = json.dumps(failed_run, ensure_ascii=False)
        for secret in {
            "USER_INSTRUCTION_SENTINEL",
            "PROMPT_SENTINEL",
            "PATCH_BODY_SENTINEL",
            "RAW_SUPPLIER_RESPONSE_SENTINEL",
            "unit-test-key",
        }:
            assert secret not in serialized_run


def test_document_revision_preview_uses_current_document_without_history(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, _store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()

        response = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "document",
                "target_sentence_id": None,
                "user_instruction": "统一五区表达。",
            },
        )

        assert response.status_code == 200
        assert response.json()["scope"] == "document"
        assert len(service.document_revisions) == 1
        call = service.document_revisions[0]
        assert call["document"].model_dump(mode="json") == stable["document"]
        assert "versions" not in call
        assert "evidence_records" not in call


@pytest.mark.parametrize(
    "mutation",
    ["added", "deleted", "replaced", "duplicated", "moved", "reordered"],
)
def test_document_sentence_identity_preview_returns_422_before_regeneration_or_quick_check(
    tmp_path: Path,
    mutation: str,
) -> None:
    service = IdentityChangingDocumentRevisionService(mutation)
    audit = CountingAuditService(explicit_mock_audit_service(tmp_path))
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=audit,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()

        response = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "document",
                "target_sentence_id": None,
                "user_instruction": "Attempt to change sentence identities.",
            },
        )

        assert response.status_code == 422
        assert response.json()["error_code"] == "PATCH_INVALID"
        assert len(service.document_revisions) == 1
        assert service.claim_regenerations == []
        assert audit.quick_calls == 1
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == counts


@pytest.mark.parametrize(
    "mutation",
    ["added", "deleted", "replaced", "duplicated", "moved", "reordered"],
)
def test_document_sentence_identity_accept_returns_422_before_regeneration_or_quick_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    service = RecordingRevisionService()
    audit = CountingAuditService(explicit_mock_audit_service(tmp_path))
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=audit,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "document",
                "target_sentence_id": None,
                "user_instruction": "Create a legal document preview.",
            },
        )
        assert preview.status_code == 200
        saved_patch = EditPatch.model_validate(preview.json())
        tampered_patch = saved_patch.model_copy(
            update={
                "after_text": document_revision_after_text(
                    generated_bundle().document,
                    mutation,
                )
            }
        )
        original_validated_json = store._validated_json

        def substitute_patch(model_type: object, value: str) -> object:
            if model_type is EditPatch:
                return tampered_patch
            return original_validated_json(model_type, value)

        counts = store.row_counts()
        monkeypatch.setattr(store, "_validated_json", substitute_patch)

        response = client.post(
            f"/api/projects/project-001/revisions/{saved_patch.patch_id}/accept"
        )

        assert response.status_code == 422
        assert response.json()["error_code"] == "PATCH_INVALID"
        assert service.claim_regenerations == []
        assert audit.quick_calls == 1
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == saved_patch.model_dump(mode="json")
        assert_stable_snapshot_unchanged(pending, stable)
        assert store.row_counts() == counts
        assert store.inspect_patch_status(
            "project-001",
            saved_patch.patch_id,
        ) == "pending"


@pytest.mark.parametrize(
    ("payload_update", "status_code", "error_code"),
    [
        ({"base_version_id": "version-stale"}, 409, "TARGET_STALE"),
        ({"target_sentence_id": "s-missing"}, 422, "PATCH_INVALID"),
        ({"history": ["version-old"]}, 422, "PATCH_INVALID"),
    ],
)
def test_revision_preview_rejects_stale_missing_sentence_and_extra_history(
    tmp_path: Path,
    payload_update: dict[str, object],
    status_code: int,
    error_code: str,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]
        payload = {
            "base_version_id": stable["current_version_id"],
            "scope": "sentence",
            "target_sentence_id": target["sentence_id"],
            "user_instruction": "只修改目标句。",
            **payload_update,
        }

        response = client.post(
            "/api/projects/project-001/revisions",
            json=payload,
        )

        assert response.status_code == status_code
        assert response.json()["error_code"] == error_code
        assert response.json()["retryable"] is False
        assert service.sentence_revisions == []
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts()["patches"] == 0


def test_accept_sentence_patch_creates_new_quick_checked_version_only_on_confirm(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        before = client.get("/api/projects/project-001").json()
        target = before["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": before["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "保持事实不变并简化表达。",
            },
        )
        assert preview.status_code == 200
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, before)
        revision_before_accept = operation_runs(store, "revision")
        assert len(revision_before_accept) == 1
        assert revision_before_accept[0]["version_id"] is None

        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert accepted.status_code == 200
        payload = accepted.json()
        assert payload["stage"] == "quick_checked"
        assert payload["version_id"] != before["current_version_id"]
        after = client.get("/api/projects/project-001").json()
        assert after["stage"] == "quick_checked"
        assert after["pending_patch"] is None
        assert after["current_version_id"] == payload["version_id"]
        assert len(after["versions"]) == 2
        assert after["versions"][1]["parent_version_id"] == before["current_version_id"]
        before_texts = {
            sentence["sentence_id"]: sentence["text"]
            for section in before["document"]["sections"]
            for sentence in section["sentences"]
        }
        after_texts = {
            sentence["sentence_id"]: sentence["text"]
            for section in after["document"]["sections"]
            for sentence in section["sentences"]
        }
        assert after_texts[target["sentence_id"]] == preview.json()["after_text"]
        assert {
            sentence_id: text
            for sentence_id, text in after_texts.items()
            if sentence_id != target["sentence_id"]
        } == {
            sentence_id: text
            for sentence_id, text in before_texts.items()
            if sentence_id != target["sentence_id"]
        }
        assert store.row_counts()["versions"] == 2
        assert store.row_counts()["patches"] == 1
        revision_after_accept = operation_runs(store, "revision")
        assert len(revision_after_accept) == 1
        assert revision_after_accept[0]["id"] == revision_before_accept[0]["id"]
        assert revision_after_accept[0]["version_id"] == payload["version_id"]


def test_reject_patch_returns_204_and_is_idempotent_without_other_writes(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    audit = CountingAuditService(explicit_mock_audit_service(tmp_path))
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=audit,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "Create a patch to reject.",
            },
        )
        assert preview.status_code == 200
        patch_id = preview.json()["patch_id"]
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, stable)
        counts = store.row_counts()
        snapshots = store.validate_all_snapshots()
        revision_before_reject = operation_runs(store, "revision")
        assert len(revision_before_reject) == 1
        assert revision_before_reject[0]["version_id"] is None

        rejected = client.post(
            f"/api/projects/project-001/revisions/{patch_id}/reject"
        )
        retried = client.post(
            f"/api/projects/project-001/revisions/{patch_id}/reject"
        )

        assert rejected.status_code == 204
        assert rejected.content == b""
        assert retried.status_code == 204
        assert retried.content == b""
        assert store.inspect_patch_status("project-001", patch_id) == "rejected"
        restored = client.get("/api/projects/project-001").json()
        assert restored["stage"] == stable["stage"]
        assert restored["pending_patch"] is None
        assert_stable_snapshot_unchanged(restored, stable)
        assert store.row_counts() == counts
        assert store.validate_all_snapshots() == snapshots
        revision_after_reject = operation_runs(store, "revision")
        assert revision_after_reject == revision_before_reject
        assert revision_after_reject[0]["version_id"] is None
        assert service.claim_regenerations == []
        assert service.sentence_claim_regenerations == []
        assert audit.quick_calls == 1


def test_reject_patch_returns_stable_errors_for_accepted_and_missing(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        first = client.get("/api/projects/project-001").json()
        target = first["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": first["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "Accept this patch before rejecting it.",
            },
        )
        assert preview.status_code == 200
        patch_id = preview.json()["patch_id"]
        assert client.post(
            f"/api/projects/project-001/revisions/{patch_id}/accept"
        ).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        snapshots = store.validate_all_snapshots()

        accepted = client.post(
            f"/api/projects/project-001/revisions/{patch_id}/reject"
        )
        missing = client.post(
            "/api/projects/project-001/revisions/patch-missing/reject"
        )

        assert accepted.status_code == 422
        assert accepted.json()["error_code"] == "PATCH_INVALID"
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "PATCH_NOT_FOUND"
        assert store.inspect_patch_status("project-001", patch_id) == "accepted"
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == counts
        assert store.validate_all_snapshots() == snapshots


@pytest.mark.parametrize(
    ("fact_changed", "evidence_changed", "after_text"),
    [
        (True, False, "The revised target states the opposite result."),
        (False, True, "The revised target requires different evidence."),
        (False, False, "The provider falsely labels an opposite fact as style-only."),
    ],
    ids=["fact-changed", "evidence-changed", "false-change-flags"],
)
def test_sentence_revision_acceptance_rebuilds_only_target_claims(
    tmp_path: Path,
    fact_changed: bool,
    evidence_changed: bool,
    after_text: str,
) -> None:
    service = UnsafeSentenceRevisionService(
        fact_changed=fact_changed,
        evidence_changed=evidence_changed,
        after_text=after_text,
    )
    audit = CountingAuditService(explicit_mock_audit_service(tmp_path))
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=audit,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        assert client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        ).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        target = stable["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "Change the target sentence.",
            },
        )
        assert preview.status_code == 200

        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert accepted.status_code == 200
        after = client.get("/api/projects/project-001").json()
        assert after["current_version_id"] != stable["current_version_id"]
        assert after["stage"] == "quick_checked"
        assert after["pending_patch"] is None
        assert after["audit_report"]["audit_status"] == "quick_complete"
        assert len(after["versions"]) == 2
        assert audit.quick_calls == 2
        assert len(audit.quick_inputs) == 2
        assert len(audit.quick_outputs) == 2
        assert service.claim_regenerations == []
        assert len(service.sentence_claim_regenerations) == 1
        regeneration = service.sentence_claim_regenerations[0]
        target_claims = [
            claim
            for claim in stable["claims"]
            if claim["sentence_id"] == target["sentence_id"]
        ]
        unselected_claims = [
            claim
            for claim in stable["claims"]
            if claim["sentence_id"] != target["sentence_id"]
        ]
        assert [
            claim.model_dump(mode="json")
            for claim in regeneration["original_claims"]
        ] == target_claims
        assert {
            record.claim_id for record in regeneration["evidence_records"]
        } == {claim["claim_id"] for claim in target_claims}
        assert all(
            record.quote_verified
            for record in regeneration["evidence_records"]
        )
        assert regeneration["allowed_block_ids"] == {"p01-b001"}
        assert regeneration["reserved_claim_ids"] == {
            claim["claim_id"] for claim in unselected_claims
        }
        assert regeneration["accepted_after_text"] == after_text
        assert regeneration["user_instruction"] == (
            "Regenerate claims for the accepted target sentence."
        )
        assert {
            "document",
            "source_blocks",
            "history",
            "versions",
        }.isdisjoint(regeneration)
        assert [claim["claim_id"] for claim in after["claims"]] == [
            "replacement-z",
            "replacement-a",
            *[claim["claim_id"] for claim in unselected_claims],
        ]
        old_target_claim_ids = {claim["claim_id"] for claim in target_claims}
        after_target_claims = [
            claim
            for claim in after["claims"]
            if claim["sentence_id"] == target["sentence_id"]
        ]
        assert old_target_claim_ids.isdisjoint(
            claim["claim_id"] for claim in after_target_claims
        )
        assert [claim["sentence_id"] for claim in after_target_claims] == [
            target["sentence_id"],
            target["sentence_id"],
        ]
        assert [claim["text"] for claim in after_target_claims] == [
            after_text,
            f"{after_text} Supporting detail.",
        ]
        assert [
            claim
            for claim in after["claims"]
            if claim["sentence_id"] != target["sentence_id"]
        ] == unselected_claims
        revision_bundle = audit.quick_inputs[-1][0]
        revision_records, revision_report = audit.quick_outputs[-1]
        assert revision_bundle.document.model_dump(mode="json") == after["document"]
        assert [
            claim.model_dump(mode="json") for claim in revision_bundle.claims
        ] == after["claims"]
        assert [
            record.model_dump(mode="json") for record in revision_records
        ] == after["evidence_records"]
        assert revision_report.model_dump(mode="json") == after["audit_report"]
        after_claim_ids = {claim["claim_id"] for claim in after["claims"]}
        after_evidence_claim_ids = {
            record["claim_id"] for record in after["evidence_records"]
        }
        assert after_evidence_claim_ids <= after_claim_ids
        assert {
            claim["claim_id"] for claim in after_target_claims
        } <= after_evidence_claim_ids
        assert old_target_claim_ids.isdisjoint(after_evidence_claim_ids)
        stable_other_texts = [
            sentence
            for section in stable["document"]["sections"]
            for sentence in section["sentences"]
            if sentence["sentence_id"] != target["sentence_id"]
        ]
        after_other_texts = [
            sentence
            for section in after["document"]["sections"]
            for sentence in section["sentences"]
            if sentence["sentence_id"] != target["sentence_id"]
        ]
        assert after_other_texts == stable_other_texts
        assert store.inspect_patch_status(
            "project-001",
            preview.json()["patch_id"],
        ) == "accepted"
        assert store.row_counts() == {
            **counts,
            "versions": counts["versions"] + 1,
            "audits": counts["audits"] + 1,
            "patches": counts["patches"] + 1,
            "runs": counts["runs"] + 2,
        }


def test_sentence_revision_punctuation_reuse_ignores_provider_change_flags(
    tmp_path: Path,
) -> None:
    target = generated_bundle().document.sections[0].sentences[0]
    service = UnsafeSentenceRevisionService(
        fact_changed=True,
        evidence_changed=True,
        after_text=f"{target.text}!",
    )
    with api_client(tmp_path, hy3_service=service) as (client, _store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        before = client.get("/api/projects/project-001").json()
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": before["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target.sentence_id,
                "user_instruction": "只追加安全标点。",
            },
        )

        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert accepted.status_code == 200
        after = client.get("/api/projects/project-001").json()
        assert after["claims"] == before["claims"]
        assert service.sentence_claim_regenerations == []


def test_real_mock_nontrivial_sentence_preview_accept_regenerates_target_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Hy3Service(settings=Settings(_env_file=None, paperlens_model_mode="mock"))
    monkeypatch.setattr(
        service,
        "_get_client",
        lambda: pytest.fail("Mock revision flow must not call the supplier"),
    )
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        target = stable["document"]["sections"][0]["sentences"][0]
        target_claims = [
            claim
            for claim in stable["claims"]
            if claim["sentence_id"] == target["sentence_id"]
        ]
        unrelated_claims = [
            claim
            for claim in stable["claims"]
            if claim["sentence_id"] != target["sentence_id"]
        ]
        allowed_blocks = {
            block_id
            for claim in target_claims
            for block_id in claim["candidate_block_ids"]
        } | {
            record["block_id"]
            for record in stable["evidence_records"]
            if record["claim_id"] in {claim["claim_id"] for claim in target_claims}
            and record["quote_verified"]
            and record["block_id"] is not None
        }

        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "Clarify the supported result in the target sentence.",
            },
        )

        assert preview.status_code == 200
        assert preview.json()["after_text"] not in {
            f"{target['text']}!",
            f"{target['text']}！",
        }
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, stable)

        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert accepted.status_code == 200
        after = client.get("/api/projects/project-001").json()
        assert after["stage"] == "quick_checked"
        assert after["pending_patch"] is None
        assert after["current_version_id"] != stable["current_version_id"]
        assert len(after["versions"]) == len(stable["versions"]) + 1
        assert next(
            sentence["text"]
            for section in after["document"]["sections"]
            for sentence in section["sentences"]
            if sentence["sentence_id"] == target["sentence_id"]
        ) == preview.json()["after_text"]
        new_target_claims = [
            claim
            for claim in after["claims"]
            if claim["sentence_id"] == target["sentence_id"]
        ]
        assert new_target_claims
        assert {claim["claim_id"] for claim in target_claims}.isdisjoint(
            claim["claim_id"] for claim in new_target_claims
        )
        assert all(
            claim["text"] == preview.json()["after_text"]
            for claim in new_target_claims
        )
        assert all(
            set(claim["candidate_block_ids"]) <= allowed_blocks
            for claim in new_target_claims
        )
        assert [
            claim
            for claim in after["claims"]
            if claim["sentence_id"] != target["sentence_id"]
        ] == unrelated_claims
        assert store.inspect_patch_status(
            "project-001", preview.json()["patch_id"]
        ) == "accepted"
        assert store.row_counts() == {
            **counts,
            "versions": counts["versions"] + 1,
            "audits": counts["audits"] + 1,
            "patches": counts["patches"] + 1,
            "runs": counts["runs"] + 2,
        }


def test_real_mock_explicit_punctuation_revision_reuses_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Hy3Service(settings=Settings(_env_file=None, paperlens_model_mode="mock"))
    monkeypatch.setattr(
        service,
        "_get_client",
        lambda: pytest.fail("Mock punctuation flow must not call the supplier"),
    )
    with api_client(tmp_path, hy3_service=service) as (client, _store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        target = stable["document"]["sections"][0]["sentences"][0]

        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "只追加安全标点。",
            },
        )
        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert preview.status_code == 200
        assert preview.json()["after_text"] == f"{target['text']}!"
        assert accepted.status_code == 200
        assert client.get("/api/projects/project-001").json()["claims"] == stable[
            "claims"
        ]


@pytest.mark.parametrize(
    ("error_code", "retryable", "status_code"),
    [
        ("HY3_UNAVAILABLE", True, 503),
        ("SCHEMA_INVALID", False, 502),
    ],
)
def test_sentence_claim_regeneration_failure_has_no_acceptance_side_effects(
    tmp_path: Path,
    error_code: str,
    retryable: bool,
    status_code: int,
) -> None:
    service = FailingSentenceClaimRegenerationService(
        error_code=error_code,
        retryable=retryable,
    )
    audit = CountingAuditService(explicit_mock_audit_service(tmp_path))
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=audit,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        target = stable["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "执行非平凡修订。",
            },
        )

        failed = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert failed.status_code == status_code
        assert failed.json()["error_code"] == error_code
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, stable)
        assert store.inspect_patch_status(
            "project-001",
            preview.json()["patch_id"],
        ) == "pending"
        assert store.row_counts() == {
            **counts,
            "patches": counts["patches"] + 1,
            "runs": counts["runs"] + 1,
        }
        assert len(service.sentence_claim_regenerations) == 1
        assert audit.quick_calls == 1


def test_sentence_revision_quick_check_failure_keeps_old_stable_snapshot(
    tmp_path: Path,
) -> None:
    target = generated_bundle().document.sections[0].sentences[0]
    service = UnsafeSentenceRevisionService(
        fact_changed=False,
        evidence_changed=False,
        after_text=f"{target.text} Non-trivial revision.",
    )
    audit = FailingQuickAudit()
    audit.should_fail = False
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=audit,
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target.sentence_id,
                "user_instruction": "执行非平凡修订。",
            },
        )
        audit.should_fail = True

        failed = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert failed.status_code == 502
        assert failed.json()["error_code"] == "AUDIT_INCOMPLETE"
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, stable)
        assert store.inspect_patch_status(
            "project-001",
            preview.json()["patch_id"],
        ) == "pending"
        assert store.row_counts() == {
            **counts,
            "patches": counts["patches"] + 1,
            "runs": counts["runs"] + 1,
        }
        assert len(service.sentence_claim_regenerations) == 1
        assert audit.calls == 2


def test_accept_patch_returns_stable_missing_and_stale_errors_without_overwrite(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        before = client.get("/api/projects/project-001").json()
        target = before["document"]["sections"][0]["sentences"][0]

        missing = client.post(
            "/api/projects/project-001/revisions/patch-missing/accept"
        )
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "PATCH_NOT_FOUND"
        assert client.get("/api/projects/project-001").json() == before

        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": before["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "简化目标句。",
            },
        )
        assert preview.status_code == 200
        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )
        assert accepted.status_code == 200
        stable = client.get("/api/projects/project-001").json()

        stale = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )
        assert stale.status_code == 409
        assert stale.json()["error_code"] == "TARGET_STALE"
        assert stale.json()["retryable"] is False
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts()["versions"] == 2


def test_accept_document_patch_rebuilds_claims_and_expires_current_deep_audit(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(
        tmp_path,
        hy3_service=service,
        audit_service=explicit_mock_audit_service(tmp_path),
    ) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        audited = client.post(
            "/api/projects/project-001/audit",
            json=VALID_AUDIT_REQUEST,
        )
        assert audited.status_code == 200
        before = client.get("/api/projects/project-001").json()
        assert before["stage"] == "deep_audited"
        assert before["pending_patch"] is None
        assert before["audit_report"]["audit_status"] == "deep_complete"

        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": before["current_version_id"],
                "scope": "document",
                "target_sentence_id": None,
                "user_instruction": "统一全文表达。",
            },
        )
        assert preview.status_code == 200
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, before)

        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert accepted.status_code == 200
        after = client.get("/api/projects/project-001").json()
        assert after["stage"] == "quick_checked"
        assert after["pending_patch"] is None
        assert after["audit_report"]["audit_status"] == "quick_complete"
        assert after["audit_report"]["dimensions"] == []
        assert after["claims"] != before["claims"]
        assert all(claim["claim_id"].startswith("revised-") for claim in after["claims"])
        assert len(service.claim_regenerations) == 1
        regeneration = service.claim_regenerations[0]
        assert regeneration["document"].model_dump(mode="json") == after["document"]
        assert regeneration["source_blocks"] == source_blocks()
        assert "versions" not in regeneration
        assert "history" not in regeneration
        assert len(after["versions"]) == 2
        assert store.row_counts()["audits"] == 3


def test_document_claim_regeneration_failure_keeps_current_version_unchanged(
    tmp_path: Path,
) -> None:
    service = FailingClaimRegenerationService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": stable["current_version_id"],
                "scope": "document",
                "target_sentence_id": None,
                "user_instruction": "统一全文表达。",
            },
        )
        assert preview.status_code == 200

        failed = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )

        assert failed.status_code == 503
        assert failed.json()["error_code"] == "HY3_UNAVAILABLE"
        pending = client.get("/api/projects/project-001").json()
        assert pending["stage"] == "patch_pending"
        assert pending["pending_patch"] == preview.json()
        assert_stable_snapshot_unchanged(pending, stable)
        assert store.row_counts()["versions"] == 1
        assert store.row_counts()["audits"] == 1


def test_restore_api_copies_historical_snapshot_as_new_current_version(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        first = client.get("/api/projects/project-001").json()
        target = first["document"]["sections"][0]["sentences"][0]
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": first["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "生成第二版。",
            },
        )
        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )
        assert accepted.status_code == 200
        second = client.get("/api/projects/project-001").json()

        response = client.post(
            f"/api/projects/project-001/versions/{first['current_version_id']}/restore",
            headers={"Idempotency-Key": RESTORE_KEY},
        )

        assert response.status_code == 200
        restored_summary = response.json()
        restored = client.get("/api/projects/project-001").json()
        assert restored["current_version_id"] not in {
            first["current_version_id"],
            second["current_version_id"],
        }
        assert restored["document"] == first["document"]
        assert restored["claims"] == first["claims"]
        assert restored["evidence_records"] == first["evidence_records"]
        assert restored["audit_report"] == first["audit_report"]
        assert len(restored["versions"]) == 3
        assert restored["versions"][-1]["parent_version_id"] == second["current_version_id"]
        assert restored_summary == restored["versions"][-1]
        assert store.row_counts()["versions"] == 3
        assert store.row_counts()["restore_idempotency"] == 1


def test_restore_api_missing_version_preserves_current_snapshot(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()

        response = client.post(
            "/api/projects/project-001/versions/version-missing/restore",
            headers={"Idempotency-Key": RESTORE_KEY},
        )

        assert response.status_code == 404
        assert response.json()["error_code"] == "VERSION_NOT_FOUND"
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts()["versions"] == 1


@pytest.mark.parametrize(
    "idempotency_key",
    [
        None,
        "123e4567-e89b-12d3-a456-426614174000",
        "123E4567-E89B-42D3-A456-426614174000",
        "123e4567-e89b-42d3-7456-426614174000",
        " 123e4567-e89b-42d3-a456-426614174000",
        "not-a-uuid",
    ],
)
def test_restore_api_rejects_missing_or_invalid_idempotency_header_without_mutation(
    tmp_path: Path,
    idempotency_key: str | None,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        stable = client.get("/api/projects/project-001").json()
        counts = store.row_counts()
        headers = (
            {"Idempotency-Key": idempotency_key}
            if idempotency_key is not None
            else None
        )

        response = client.post(
            f"/api/projects/project-001/versions/{stable['current_version_id']}/restore",
            headers=headers,
        )

        assert response.status_code == 422
        assert response.json() == {
            "error_code": "IDEMPOTENCY_KEY_INVALID",
            "message": "A valid Idempotency-Key header is required.",
            "retryable": False,
            "details": None,
        }
        assert client.get("/api/projects/project-001").json() == stable
        assert store.row_counts() == counts


def test_restore_api_replays_committed_result_when_first_response_is_discarded(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        target = client.get("/api/projects/project-001").json()["current_version_id"]
        route = f"/api/projects/project-001/versions/{target}/restore"
        headers = {"Idempotency-Key": RESTORE_KEY}
        before = store.row_counts()

        discarded_response = client.post(route, headers=headers)
        assert discarded_response.status_code == 200
        after_first = store.row_counts()
        replay = client.post(route, headers=headers)

        assert replay.status_code == 200
        assert replay.json() == discarded_response.json()
        assert after_first == {
            **before,
            "versions": before["versions"] + 1,
            "audits": before["audits"] + 1,
            "restore_idempotency": before["restore_idempotency"] + 1,
        }
        assert store.row_counts() == after_first
        current = client.get("/api/projects/project-001").json()
        assert current["current_version_id"] == replay.json()["version_id"]
        assert current["versions"][-1] == replay.json()


def test_restore_api_rejects_same_key_for_different_target_without_leaking_key(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        first_target = client.get("/api/projects/project-001").json()[
            "current_version_id"
        ]
        headers = {"Idempotency-Key": RESTORE_KEY}
        first = client.post(
            f"/api/projects/project-001/versions/{first_target}/restore",
            headers=headers,
        )
        assert first.status_code == 200
        stable_counts = store.row_counts()

        conflict = client.post(
            f"/api/projects/project-001/versions/{first.json()['version_id']}/restore",
            headers=headers,
        )

        assert conflict.status_code == 409
        assert conflict.json() == {
            "error_code": "IDEMPOTENCY_CONFLICT",
            "message": "The idempotency key was already used for another restore target.",
            "retryable": False,
            "details": None,
        }
        key_hash = sha256(RESTORE_KEY.encode("ascii")).hexdigest()
        exposed = conflict.text + caplog.text
        assert RESTORE_KEY not in exposed
        assert key_hash not in exposed
        assert RESTORE_KEY.encode("ascii") not in store.database_path.read_bytes()
        assert all(
            RESTORE_KEY not in version["reason"]
            for version in client.get("/api/projects/project-001").json()["versions"]
        )
        assert store.row_counts() == stable_counts


def test_markdown_export_uses_persisted_generation_source_not_current_settings(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"hy3_model": "stored-model", "paperlens_model_mode": "mock"}
        )
        assert generate_project(client).status_code == 200
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"hy3_model": "current-model", "paperlens_model_mode": "live"}
        )

        markdown = assert_markdown_export_source(
            client,
            store,
            model="stored-model",
            mode="mock",
        )

        assert "current-model（live）" not in markdown


def test_markdown_export_escapes_dynamic_content_without_persisted_writes(
    tmp_path: Path,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        current = client.get("/api/projects/project-001").json()
        version_id = current["current_version_id"]
        with sqlite3.connect(store.database_path) as connection:
            content = json.loads(
                connection.execute(
                    "SELECT content_json FROM versions WHERE id = ?",
                    (version_id,),
                ).fetchone()[0]
            )
            content["title"] = "<script>alert(1)</script>\r\n## injected-title"
            content["sections"][0]["heading"] = (
                "[x](javascript:alert(1))\tlinked heading"
            )
            content["sections"][0]["sentences"][0]["text"] = "`inline code`"
            content["sections"][1]["sentences"][0]["text"] = (
                "![image](javascript:alert(1))"
            )
            content["sections"][2]["sentences"][0]["text"] = "- injected list"
            content["sections"][3]["sentences"][0]["text"] = "---"
            content["sections"][4]["sentences"][0]["text"] = (
                "正常 Unicode：研究结果提高 20%，但限制仍然存在。"
            )
            connection.execute(
                "UPDATE versions SET content_json = ? WHERE id = ?",
                (json.dumps(content, ensure_ascii=False), version_id),
            )
            run_row = connection.execute(
                """
                SELECT id, metadata_json FROM runs
                WHERE version_id = ? AND operation = 'generate'
                """,
                (version_id,),
            ).fetchone()
            metadata = json.loads(run_row[1])
            metadata["model"] = (
                "<script>model</script>\n[x](javascript:alert(1))"
            )
            connection.execute(
                "UPDATE runs SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), run_row[0]),
            )
            connection.commit()

        project_before = client.get("/api/projects/project-001").json()
        counts_before = store.row_counts()
        rows_before = database_rows(store)

        response = client.get("/api/projects/project-001/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "attachment" in response.headers["content-disposition"]
        markdown = response.content.decode("utf-8")
        assert (
            "# &lt;script&gt;alert(1)&lt;/script&gt; "
            r"\#\# injected-title"
        ) in markdown
        assert (
            "## "
            r"\[x\](javascript:alert(1)) linked heading"
        ) in markdown
        assert r"\`inline code\`" in markdown
        assert r"!\[image\](javascript:alert(1))" in markdown
        assert "\n\\- injected list\n" in markdown
        assert "\n\\---\n" in markdown
        assert "正常 Unicode：研究结果提高 20%，但限制仍然存在。" in markdown
        assert (
            "- 模型信息：&lt;script&gt;model&lt;/script&gt; "
            r"\[x\](javascript:alert(1))（mock）。"
        ) in markdown
        assert "<script>" not in markdown
        assert "\r" not in markdown
        assert "\t" not in markdown
        assert "\v" not in markdown
        assert "\f" not in markdown
        assert "\n## injected-title" not in markdown
        assert client.get("/api/projects/project-001").json() == project_before
        assert store.row_counts() == counts_before
        assert database_rows(store) == rows_before


def test_markdown_export_matches_current_revision_and_has_no_side_effects(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        first = client.get("/api/projects/project-001").json()
        target = first["document"]["sections"][0]["sentences"][0]
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"hy3_model": "revision-source", "paperlens_model_mode": "live"}
        )
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": first["current_version_id"],
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "用于导出测试的修订。",
            },
        )
        assert preview.status_code == 200
        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )
        assert accepted.status_code == 200
        current = client.get("/api/projects/project-001").json()
        revision_runs = [
            run
            for run in operation_runs(store, "revision")
            if run["version_id"] == current["current_version_id"]
        ]
        quick_runs = [
            run
            for run in operation_runs(store, "quick_check")
            if run["version_id"] == current["current_version_id"]
        ]
        assert len(revision_runs) == 1
        assert revision_runs[0]["metadata"]["model"] == "revision-source"
        assert revision_runs[0]["mode"] == "live"
        assert len(quick_runs) == 1
        assert quick_runs[0]["mode"] == "local"
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"hy3_model": "current-model", "paperlens_model_mode": "mock"}
        )

        markdown = assert_markdown_export_source(
            client,
            store,
            model="revision-source",
            mode="live",
        )

        response = client.get("/api/projects/project-001/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "attachment" in response.headers["content-disposition"]
        assert markdown.startswith(f"# {current['document']['title']}")
        assert preview.json()["after_text"] in markdown
        for section in current["document"]["sections"]:
            for sentence in section["sentences"]:
                assert sentence["text"] in markdown
        assert "来源论文说明" in markdown
        assert "AI 辅助说明" in markdown
        assert "生成时间" in markdown
        assert "模型信息" in markdown
        assert current["current_version_id"] in markdown
        assert list(tmp_path.rglob("*.md")) == []


def test_markdown_export_traces_generate_and_revision_through_recursive_restore(
    tmp_path: Path,
) -> None:
    service = RecordingRevisionService()
    with api_client(tmp_path, hy3_service=service) as (client, store, _):
        upload_parsed_project(client)
        client.app.state.settings = client.app.state.settings.model_copy(
            update={
                "hy3_model": "generation-source",
                "paperlens_model_mode": "mock",
            }
        )
        generated = generate_project(client)
        assert generated.status_code == 200
        generated_version_id = generated.json()["version_id"]
        target = generated.json()["document"]["sections"][0]["sentences"][0]
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"hy3_model": "revision-source", "paperlens_model_mode": "live"}
        )
        preview = client.post(
            "/api/projects/project-001/revisions",
            json={
                "base_version_id": generated_version_id,
                "scope": "sentence",
                "target_sentence_id": target["sentence_id"],
                "user_instruction": "Create a revision source for export tracing.",
            },
        )
        assert preview.status_code == 200
        accepted = client.post(
            f"/api/projects/project-001/revisions/{preview.json()['patch_id']}/accept"
        )
        assert accepted.status_code == 200
        revision_version_id = accepted.json()["version_id"]
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"hy3_model": "current-model", "paperlens_model_mode": "mock"}
        )

        restored_generation = client.post(
            f"/api/projects/project-001/versions/{generated_version_id}/restore",
            headers={"Idempotency-Key": RESTORE_KEY},
        )
        assert restored_generation.status_code == 200
        assert_markdown_export_source(
            client,
            store,
            model="generation-source",
            mode="mock",
        )

        restored_revision = client.post(
            f"/api/projects/project-001/versions/{revision_version_id}/restore",
            headers={"Idempotency-Key": OTHER_RESTORE_KEY},
        )
        assert restored_revision.status_code == 200
        assert_markdown_export_source(
            client,
            store,
            model="revision-source",
            mode="live",
        )

        restored_twice = client.post(
            "/api/projects/project-001/versions/"
            f"{restored_revision.json()['version_id']}/restore",
            headers={
                "Idempotency-Key": "323e4567-e89b-42d3-a456-426614174002"
            },
        )
        assert restored_twice.status_code == 200
        assert_markdown_export_source(
            client,
            store,
            model="revision-source",
            mode="live",
        )


@pytest.mark.parametrize(
    "corruption",
    ["missing_run", "invalid_metadata", "cross_project", "cycle"],
)
def test_markdown_export_rejects_corrupt_provenance_without_side_effects(
    tmp_path: Path,
    corruption: str,
) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)
        assert generate_project(client).status_code == 200
        current = client.get("/api/projects/project-001").json()
        version_id = current["current_version_id"]
        with sqlite3.connect(store.database_path) as connection:
            if corruption == "missing_run":
                connection.execute(
                    "DELETE FROM runs WHERE version_id = ? AND operation = 'generate'",
                    (version_id,),
                )
            elif corruption == "invalid_metadata":
                connection.execute(
                    """
                    UPDATE runs SET metadata_json = ?
                    WHERE version_id = ? AND operation = 'generate'
                    """,
                    ('{"raw":"RAW_RUN_JSON_SENTINEL"}', version_id),
                )
            elif corruption == "cross_project":
                source = connection.execute(
                    "SELECT * FROM versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, stage, pdf_path, pdf_sha256, rights_confirmed,
                        parse_json, current_version_id, error_code,
                        created_at, updated_at
                    ) SELECT ?, stage, pdf_path, pdf_sha256, rights_confirmed,
                             parse_json, ?, error_code, created_at, updated_at
                      FROM projects WHERE id = ?
                    """,
                    ("project-other", "version-other", "project-001"),
                )
                connection.execute(
                    """
                    INSERT INTO versions (
                        id, project_id, version_no, parent_version_id,
                        content_json, claims_json, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "version-other",
                        "project-other",
                        1,
                        None,
                        source[4],
                        source[5],
                        "initial_generation",
                        source[7],
                    ),
                )
                connection.execute(
                    "UPDATE versions SET reason = ? WHERE id = ?",
                    ("restore:version-other", version_id),
                )
            else:
                connection.execute(
                    "UPDATE versions SET reason = ? WHERE id = ?",
                    (f"restore:{version_id}", version_id),
                )
            connection.commit()

        project_before = client.get("/api/projects/project-001").json()
        counts_before = store.row_counts()
        rows_before = database_rows(store)

        response = client.get("/api/projects/project-001/export")

        assert response.status_code == 502
        assert response.json()["error_code"] == "SCHEMA_INVALID"
        assert response.json()["retryable"] is False
        serialized_error = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (
            "RAW_RUN_JSON_SENTINEL",
            "SELECT ",
            str(store.database_path),
            "unit-test-key",
        ):
            assert forbidden not in serialized_error
        assert client.get("/api/projects/project-001").json() == project_before
        assert store.row_counts() == counts_before
        assert database_rows(store) == rows_before


def test_markdown_export_rejects_project_without_stable_version(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, store, _):
        upload_parsed_project(client)

        response = client.get("/api/projects/project-001/export")

        assert response.status_code == 409
        assert response.json()["error_code"] == "PROJECT_NOT_READY"
        assert store.row_counts()["versions"] == 0
        assert list(tmp_path.rglob("*.md")) == []


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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
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
            "restore_idempotency",
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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
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
            "restore_idempotency": 0,
        }
