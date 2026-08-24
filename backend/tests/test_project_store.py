import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.audit_service import AuditService
from backend.app.hy3_service import Hy3Service
from backend.app.models import (
    AuditReport,
    ComplianceContext,
    EvidenceSnapshot,
    GeneratedBundle,
    ParseQualitySnapshot,
    ParseSnapshot,
    ProjectStage,
    RunMetadata,
    SourceBlock,
    UsageSnapshot,
)
from backend.app.project_store import ProjectStore, StoreError
from backend.app.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc)


def load_bundle() -> GeneratedBundle:
    return GeneratedBundle.model_validate_json(
        (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")
    )


def load_parse_snapshot() -> ParseSnapshot:
    blocks = TypeAdapter(list[SourceBlock]).validate_json(
        (FIXTURES / "source_blocks.json").read_text(encoding="utf-8")
    )
    return ParseSnapshot(
        blocks=blocks,
        quality=ParseQualitySnapshot(
            page_count=2,
            block_count=len(blocks),
            empty_page_rate=0.0,
            abnormal_character_rate=0.0,
            page_number_completeness_rate=1.0,
            bbox_availability_rate=1.0,
        ),
    )


def success_metadata(*, model: str | None = None) -> RunMetadata:
    return RunMetadata(
        message=None,
        retryable=False,
        retryable_stage=None,
        model=model,
        prompt_version=None,
        schema_version=None,
    )


def empty_usage() -> UsageSnapshot:
    return UsageSnapshot(
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
    )


def mock_audit_service(tmp_path: Path) -> AuditService:
    settings = Settings(
        _env_file=None,
        paperlens_data_dir=tmp_path,
        paperlens_model_mode="mock",
    )
    return AuditService(hy3_service=Hy3Service(settings=settings))


def parsed_store(tmp_path: Path, project_id: str = "project-001") -> ProjectStore:
    store = ProjectStore(tmp_path)
    store.initialize()
    pdf_path = store.project_pdf_path(project_id)
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4\nfixture")
    store.create_project(
        project_id=project_id,
        pdf_sha256="a" * 64,
        rights_confirmed=True,
        created_at=NOW,
    )
    store.save_parse_success(
        project_id=project_id,
        snapshot=load_parse_snapshot(),
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    return store


def save_generated_and_quick_checked(
    store: ProjectStore,
    tmp_path: Path,
    project_id: str = "project-001",
) -> tuple[str, GeneratedBundle, EvidenceSnapshot, AuditReport]:
    bundle = load_bundle()
    version_id, version_no = store.save_generated_version(
        project_id=project_id,
        bundle=bundle,
        reason="initial_generation",
        metadata=success_metadata(model="hy3"),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    assert version_no == 1
    records, quick_report = mock_audit_service(tmp_path).quick_check(
        bundle,
        load_parse_snapshot().blocks,
    )
    evidence = EvidenceSnapshot(evidence_records=records)
    store.save_quick_check(
        project_id=project_id,
        version_id=version_id,
        evidence=evidence,
        report=quick_report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    return version_id, bundle, evidence, quick_report


def test_initialize_creates_exact_five_table_schema(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    store.initialize()

    assert store.inspect_schema() == {
        "audits": (
            "id",
            "project_id",
            "version_id",
            "status",
            "evidence_json",
            "report_json",
            "created_at",
        ),
        "patches": (
            "id",
            "project_id",
            "base_version_id",
            "status",
            "patch_json",
            "created_at",
        ),
        "projects": (
            "id",
            "stage",
            "pdf_path",
            "pdf_sha256",
            "rights_confirmed",
            "parse_json",
            "current_version_id",
            "error_code",
            "created_at",
            "updated_at",
        ),
        "runs": (
            "id",
            "project_id",
            "version_id",
            "operation",
            "mode",
            "status",
            "metadata_json",
            "usage_json",
            "error_code",
            "started_at",
            "ended_at",
        ),
        "versions": (
            "id",
            "project_id",
            "version_no",
            "parent_version_id",
            "content_json",
            "claims_json",
            "reason",
            "created_at",
        ),
    }
    assert store.inspect_constraints() == {
        "projects.rights_confirmed": "INTEGER NOT NULL CHECK (rights_confirmed IN (0, 1))",
        "audits.evidence_json": "TEXT NOT NULL",
    }


def test_store_round_trips_every_stage_four_snapshot_and_current_view(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, evidence, quick_report = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    context = ComplianceContext(
        rights_or_license_confirmed=True,
        source_disclosure_status="present",
        ai_assistance_disclosure_status="present",
        generated_content_label_applicability="not_applicable",
        generated_content_label_status="not_applicable",
    )
    _, deep_report = mock_audit_service(tmp_path).run_deep_audit(
        bundle,
        evidence.evidence_records,
        context,
    )
    response = store.save_deep_audit(
        project_id="project-001",
        version_id=version_id,
        report=deep_report,
        metadata=success_metadata(model="hy3"),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    view = store.get_project_view("project-001", model_mode="mock")
    restored_bundle, restored_evidence, rights, restored_version_id = (
        store.get_deep_audit_inputs("project-001")
    )

    assert response.audit_report == deep_report
    assert view.stage == ProjectStage.DEEP_AUDITED
    assert view.document == bundle.document
    assert view.claims == bundle.claims
    assert view.evidence_records == evidence.evidence_records
    assert view.audit_report == deep_report
    assert view.versions[0].version_id == version_id
    assert restored_bundle == bundle
    assert restored_evidence == evidence
    assert rights is True
    assert restored_version_id == version_id
    assert quick_report.audit_status.value == "quick_complete"
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


def test_store_revalidates_untrusted_models_before_any_json_write(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    store.initialize()
    store.create_project(
        project_id="project-001",
        pdf_sha256="a" * 64,
        rights_confirmed=True,
        created_at=NOW,
    )
    valid = load_parse_snapshot()
    invalid_quality = ParseQualitySnapshot.model_construct(
        **{**valid.quality.model_dump(), "empty_page_rate": 2.0}
    )
    untrusted = ParseSnapshot.model_construct(
        blocks=valid.blocks,
        quality=invalid_quality,
    )

    with pytest.raises(StoreError) as exc_info:
        store.save_parse_success(
            project_id="project-001",
            snapshot=untrusted,
            metadata=success_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )

    assert exc_info.value.error_code == "SCHEMA_INVALID"
    assert store.row_counts() == {
        "projects": 1,
        "versions": 0,
        "audits": 0,
        "patches": 0,
        "runs": 0,
    }


def test_store_revalidates_json_again_on_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = parsed_store(tmp_path)

    def reject_stored_json(_cls: type, _raw: str) -> ParseSnapshot:
        raise ValidationError.from_exception_data("ParseSnapshot", [])

    monkeypatch.setattr(
        ParseSnapshot,
        "model_validate_json",
        classmethod(reject_stored_json),
    )

    with pytest.raises(StoreError) as exc_info:
        store.get_parse_snapshot("project-001")

    assert exc_info.value.error_code == "SCHEMA_INVALID"


def test_generated_transaction_rolls_back_all_rows_and_project_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = parsed_store(tmp_path)
    original_insert_run = store._insert_run

    def fail_run_insert(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(store, "_insert_run", fail_run_insert)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        store.save_generated_version(
            project_id="project-001",
            bundle=load_bundle(),
            reason="initial_generation",
            metadata=success_metadata(model="hy3"),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )
    monkeypatch.setattr(store, "_insert_run", original_insert_run)

    view = store.get_project_view("project-001", model_mode="mock")
    assert view.stage == ProjectStage.PARSED
    assert view.current_version_id is None
    assert store.row_counts()["versions"] == 0
    assert store.row_counts()["runs"] == 1


def test_failed_quick_and_deep_audit_keep_previous_stable_snapshots(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    bundle = load_bundle()
    version_id, _ = store.save_generated_version(
        project_id="project-001",
        bundle=bundle,
        reason="initial_generation",
        metadata=success_metadata(model="hy3"),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    store.record_failure(
        project_id="project-001",
        version_id=version_id,
        operation="quick_check",
        mode="local",
        error_code="AUDIT_INCOMPLETE",
        metadata=RunMetadata(
            message="Quick check could not be completed.",
            retryable=True,
            retryable_stage="quick_checked",
            model=None,
            prompt_version=None,
            schema_version=None,
        ),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    failed_quick = store.get_project_view("project-001", model_mode="mock")
    assert failed_quick.stage == ProjectStage.FAILED
    assert failed_quick.document == bundle.document
    assert failed_quick.evidence_records == []
    assert failed_quick.error_code == "AUDIT_INCOMPLETE"
    assert failed_quick.retryable_stage == ProjectStage.QUICK_CHECKED

    records, quick_report = mock_audit_service(tmp_path).quick_check(
        bundle,
        load_parse_snapshot().blocks,
    )
    evidence = EvidenceSnapshot(evidence_records=records)
    store.save_quick_check(
        project_id="project-001",
        version_id=version_id,
        evidence=evidence,
        report=quick_report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    store.record_failure(
        project_id="project-001",
        version_id=version_id,
        operation="deep_audit",
        mode="mock",
        error_code="HY3_UNAVAILABLE",
        metadata=RunMetadata(
            message="The audit provider is temporarily unavailable.",
            retryable=True,
            retryable_stage="deep_audited",
            model="hy3",
            prompt_version="audit-v2",
            schema_version="deep-audit-result-v2",
        ),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    failed_deep = store.get_project_view("project-001", model_mode="mock")
    assert failed_deep.stage == ProjectStage.FAILED
    assert failed_deep.document == bundle.document
    assert failed_deep.evidence_records == evidence.evidence_records
    assert failed_deep.audit_report == quick_report
    assert failed_deep.error_code == "HY3_UNAVAILABLE"
    assert failed_deep.retryable_stage == ProjectStage.DEEP_AUDITED


def test_store_rejects_unconfirmed_rights_and_missing_or_unsafe_pdf(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    store.initialize()

    with pytest.raises(StoreError) as rights_error:
        store.create_project(
            project_id="project-001",
            pdf_sha256="a" * 64,
            rights_confirmed=False,
            created_at=NOW,
        )
    assert rights_error.value.error_code == "RIGHTS_NOT_CONFIRMED"
    assert store.row_counts()["projects"] == 0

    with pytest.raises(StoreError) as path_error:
        store.project_pdf_path("../escape")
    assert path_error.value.error_code == "PDF_NOT_FOUND"

    store.create_project(
        project_id="project-001",
        pdf_sha256="a" * 64,
        rights_confirmed=True,
        created_at=NOW,
    )
    with pytest.raises(StoreError) as missing_error:
        store.get_pdf_path("project-001")
    assert missing_error.value.error_code == "PDF_NOT_FOUND"


def test_store_returns_stable_not_found_and_stage_errors(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    store.initialize()

    with pytest.raises(StoreError) as missing:
        store.get_project_view("missing", model_mode="mock")
    assert missing.value.error_code == "PROJECT_NOT_FOUND"

    parsed_store(tmp_path)
    with pytest.raises(StoreError) as evidence:
        store.get_deep_audit_inputs("project-001")
    assert evidence.value.error_code == "EVIDENCE_NOT_READY"
