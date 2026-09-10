from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.audit_service import AuditService
from backend.app.hy3_service import Hy3Service
from backend.app.models import (
    AuditReport,
    ComplianceContext,
    EditPatch,
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
from backend.app.prompts import REVISION_PROMPT_VERSION
from backend.app.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc)
RESTORE_KEY = "123e4567-e89b-42d3-a456-426614174000"
RESTORE_KEY_HASH = sha256(RESTORE_KEY.encode("ascii")).hexdigest()
OTHER_RESTORE_KEY = "223e4567-e89b-42d3-a456-426614174001"
OTHER_RESTORE_KEY_HASH = sha256(OTHER_RESTORE_KEY.encode("ascii")).hexdigest()


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


def revision_metadata(*, model: str = "hy3") -> RunMetadata:
    return RunMetadata(
        message=None,
        retryable=False,
        retryable_stage=None,
        model=model,
        prompt_version=REVISION_PROMPT_VERSION,
        schema_version="edit-patch-v1",
    )


def revision_usage() -> UsageSnapshot:
    return UsageSnapshot(
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
    )


@pytest.mark.parametrize("version", ["current", "revision-v2", "revision-v999"])
def test_scope_context_contract_persistence_current_version_only(tmp_path, version):
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    patch = sentence_patch(bundle, patch_id="scope-version-preview")
    selected = REVISION_PROMPT_VERSION if version == "current" else version
    metadata = revision_metadata().model_copy(update={"prompt_version": selected})
    before = store.row_counts()
    def save():
        return store.save_patch_preview(project_id="project-001", base_version_id=version_id,
            patch=patch, mode="mock", metadata=metadata, usage=revision_usage(),
            started_at=NOW, ended_at=NOW, created_at=NOW)
    if version == "current":
        assert save() == patch
        run = operation_runs(store, "revision")[0]
        assert run["metadata"]["prompt_version"] == REVISION_PROMPT_VERSION
        assert run["usage"] == revision_usage().model_dump(mode="json")
    else:
        with pytest.raises(StoreError) as caught:
            save()
        assert caught.value.error_code == "SCHEMA_INVALID"
        assert store.row_counts() == before


def test_scope_context_contract_persistence_legacy_read_without_rewrite(tmp_path, monkeypatch):
    import backend.app.project_store as module
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    patch = sentence_patch(bundle, patch_id="legacy-scope-preview")
    # Produce an isolated historical record under the historical write contract.
    with monkeypatch.context() as legacy:
        legacy.setattr(module, "REVISION_PROMPT_VERSION", "revision-v2", raising=False)
        store.save_patch_preview(project_id="project-001", base_version_id=version_id,
            patch=patch, mode="mock", metadata=revision_metadata().model_copy(update={"prompt_version": "revision-v2"}),
            usage=revision_usage(), started_at=NOW, ended_at=NOW, created_at=NOW)
    before = store.database_path.read_bytes()
    view = ProjectStore(store.data_dir).get_project_view("project-001", model_mode="mock")
    assert view.pending_patch == patch
    assert operation_runs(store, "revision")[0]["metadata"]["prompt_version"] == "revision-v2"
    assert store.database_path.read_bytes() == before


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


def save_revision_preview(
    store: ProjectStore,
    *,
    project_id: str,
    base_version_id: str,
    patch: EditPatch,
    created_at: datetime = NOW,
    mode: str = "mock",
    metadata: RunMetadata | None = None,
    usage: UsageSnapshot | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> EditPatch:
    before_runs = operation_runs(store, "revision")
    saved = store.save_patch_preview(
        project_id=project_id,
        base_version_id=base_version_id,
        patch=patch,
        created_at=created_at,
        mode=mode,
        metadata=metadata or revision_metadata(),
        usage=usage or empty_usage(),
        started_at=started_at or created_at,
        ended_at=ended_at or created_at,
    )
    after_runs = operation_runs(store, "revision")
    assert after_runs[:-1] == before_runs
    assert len(after_runs) == len(before_runs) + 1
    assert after_runs[-1]["operation"] == "revision"
    assert after_runs[-1]["mode"] == mode
    assert after_runs[-1]["status"] == "succeeded"
    assert after_runs[-1]["version_id"] is None
    return saved


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
    *,
    generation_model: str = "hy3",
    generation_mode: str = "mock",
) -> tuple[str, GeneratedBundle, EvidenceSnapshot, AuditReport]:
    bundle = load_bundle()
    version_id, version_no = store.save_generated_version(
        project_id=project_id,
        bundle=bundle,
        reason="initial_generation",
        metadata=success_metadata(model=generation_model),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
        mode=generation_mode,
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


@pytest.mark.parametrize("legacy", [True, False])
def test_document_expression_contract_persisted_reports_round_trip(tmp_path, legacy):
    from backend.app.models import DeepAuditResultV2
    store = parsed_store(tmp_path)
    version, bundle, evidence, _ = save_generated_and_quick_checked(store, tmp_path)
    audit = mock_audit_service(tmp_path)
    context = ComplianceContext(rights_or_license_confirmed=True,
        source_disclosure_status="present", ai_assistance_disclosure_status="present",
        generated_content_label_applicability="not_applicable", generated_content_label_status="not_applicable")
    if legacy:
        result = DeepAuditResultV2.model_validate_json((FIXTURES / "deep_audit_valid.json").read_text(encoding="utf-8"))
        report = audit.score_legacy_v2(bundle, evidence.evidence_records, result, context)
    else:
        _, report = audit.run_deep_audit(bundle, evidence.evidence_records, context)
    store.save_deep_audit(project_id="project-001", version_id=version, report=report,
        metadata=success_metadata(), usage=empty_usage(), started_at=NOW, ended_at=NOW)
    restored = ProjectStore(tmp_path).get_project_view("project-001", model_mode="mock")
    assert bool(restored.audit_report == report), "PERSISTED_REPORT_MISMATCH"
    assert bool(restored.document == bundle.document), "DOCUMENT_CHANGED"
    assert len(restored.audit_report.dimensions) == 8


def save_deep_audited(
    store: ProjectStore,
    tmp_path: Path,
    *,
    version_id: str,
    bundle: GeneratedBundle,
    evidence: EvidenceSnapshot,
) -> AuditReport:
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
    store.save_deep_audit(
        project_id="project-001",
        version_id=version_id,
        report=deep_report,
        metadata=success_metadata(model="hy3"),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    return deep_report


def sentence_patch(
    bundle: GeneratedBundle,
    *,
    patch_id: str,
    suffix: str = "!",
) -> EditPatch:
    target = bundle.document.sections[0].sentences[0]
    return EditPatch(
        patch_id=patch_id,
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text}{suffix}",
        reason="Exercise the pending patch state contract.",
        fact_changed=False,
        evidence_changed=False,
    )


def create_legacy_patch_database(
    tmp_path: Path,
    *,
    statuses: list[str],
) -> tuple[ProjectStore, list[tuple[str, str, str]]]:
    store = ProjectStore(tmp_path)
    store.data_dir.mkdir(parents=True, exist_ok=True)
    bundle = load_bundle()
    parse_snapshot = load_parse_snapshot()
    records, quick_report = mock_audit_service(tmp_path).quick_check(
        bundle,
        parse_snapshot.blocks,
    )
    base_patch = sentence_patch(bundle, patch_id="patch-legacy-base")
    created = NOW.isoformat()
    patch_rows: list[tuple[str, str, str]] = []
    for index, status in enumerate(statuses):
        patch = base_patch.model_copy(
            update={"patch_id": f"patch-legacy-{index}"}
        )
        patch_rows.append((patch.patch_id, status, patch.model_dump_json()))

    with sqlite3.connect(store.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                rights_confirmed INTEGER NOT NULL,
                parse_json TEXT,
                current_version_id TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE versions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_no INTEGER NOT NULL,
                parent_version_id TEXT,
                content_json TEXT NOT NULL,
                claims_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE audits (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE patches (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                base_version_id TEXT NOT NULL,
                status TEXT NOT NULL,
                patch_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, stage, pdf_path, pdf_sha256, rights_confirmed,
                parse_json, current_version_id, error_code,
                created_at, updated_at
            ) VALUES (?, 'quick_checked', ?, ?, 1, ?, ?, NULL, ?, ?)
            """,
            (
                "project-001",
                str(store.project_pdf_path("project-001")),
                "a" * 64,
                parse_snapshot.model_dump_json(),
                "version-legacy-001",
                created,
                created,
            ),
        )
        connection.execute(
            """
            INSERT INTO versions (
                id, project_id, version_no, parent_version_id,
                content_json, claims_json, reason, created_at
            ) VALUES (?, ?, 1, NULL, ?, ?, ?, ?)
            """,
            (
                "version-legacy-001",
                "project-001",
                bundle.document.model_dump_json(),
                json.dumps(
                    {
                        "claims": [
                            claim.model_dump(mode="json")
                            for claim in bundle.claims
                        ]
                    },
                    ensure_ascii=False,
                ),
                "initial_generation",
                created,
            ),
        )
        connection.execute(
            """
            INSERT INTO audits (
                id, project_id, version_id, status,
                evidence_json, report_json, created_at
            ) VALUES (?, ?, ?, 'quick_complete', ?, ?, ?)
            """,
            (
                "audit-legacy-001",
                "project-001",
                "version-legacy-001",
                EvidenceSnapshot(evidence_records=records).model_dump_json(),
                quick_report.model_dump_json(),
                created,
            ),
        )
        connection.executemany(
            """
            INSERT INTO patches (
                id, project_id, base_version_id,
                status, patch_json, created_at
            ) VALUES (?, 'project-001', 'version-legacy-001', ?, ?, ?)
            """,
            [(*row, created) for row in patch_rows],
        )
    return store, patch_rows


def test_initialize_creates_exact_six_table_schema(tmp_path: Path) -> None:
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
        "restore_idempotency": (
            "key_hash",
            "project_id",
            "target_version_id",
            "result_version_id",
            "created_at",
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
        "patches.status": (
            "TEXT NOT NULL CHECK "
            "(status IN ('pending', 'accepted', 'rejected'))"
        ),
        "patches.one_pending_per_project": (
            "UNIQUE INDEX WHERE status = 'pending'"
        ),
        "runs.operation": (
            "TEXT NOT NULL CHECK "
            "(operation IN ('parse', 'generate', 'quick_check', "
            "'deep_audit', 'revision'))"
        ),
    }


def test_patch_status_check_and_one_pending_index_are_enforced_per_project(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    first_version_id, first_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    parsed_store(tmp_path, project_id="project-002")
    second_version_id, second_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
        project_id="project-002",
    )
    first_patch = sentence_patch(first_bundle, patch_id="patch-project-one")
    second_patch = sentence_patch(second_bundle, patch_id="patch-project-two")
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=first_version_id,
        patch=first_patch,
        created_at=NOW,
    )
    save_revision_preview(
        store,
        project_id="project-002",
        base_version_id=second_version_id,
        patch=second_patch,
        created_at=NOW,
    )

    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO patches (
                    id, project_id, base_version_id,
                    status, patch_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "patch-invalid-status",
                    "project-001",
                    first_version_id,
                    "unknown",
                    first_patch.model_copy(
                        update={"patch_id": "patch-invalid-status"}
                    ).model_dump_json(),
                    NOW.isoformat(),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO patches (
                    id, project_id, base_version_id,
                    status, patch_json, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (
                    "patch-second-for-project-one",
                    "project-001",
                    first_version_id,
                    first_patch.model_copy(
                        update={"patch_id": "patch-second-for-project-one"}
                    ).model_dump_json(),
                    NOW.isoformat(),
                ),
            )

    assert store.inspect_patch_status("project-001", first_patch.patch_id) == "pending"
    assert store.inspect_patch_status("project-002", second_patch.patch_id) == "pending"
    assert store.row_counts()["patches"] == 2


def test_initialize_migrates_legal_legacy_patch_rows_losslessly_and_idempotently(
    tmp_path: Path,
) -> None:
    store, legacy_rows = create_legacy_patch_database(
        tmp_path,
        statuses=["accepted", "rejected", "pending"],
    )

    store.initialize()
    store.initialize()

    with sqlite3.connect(store.database_path) as connection:
        migrated_rows = connection.execute(
            "SELECT id, status, patch_json FROM patches ORDER BY rowid"
        ).fetchall()
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patches'"
        ).fetchone()[0]
        index_sql = [
            row[0]
            for row in connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'index' AND tbl_name = 'patches' AND sql IS NOT NULL
                """
            ).fetchall()
        ]

    view = store.get_project_view("project-001", model_mode="mock")
    assert migrated_rows == legacy_rows
    assert "CHECK" in table_sql.upper()
    assert all(status in table_sql for status in ("pending", "accepted", "rejected"))
    assert any(
        "UNIQUE" in sql.upper() and "WHERE status = 'pending'" in sql
        for sql in index_sql
    )
    assert view.stage == ProjectStage.PATCH_PENDING
    assert view.pending_patch.patch_id == "patch-legacy-2"
    assert store.row_counts()["patches"] == 3


@pytest.mark.parametrize(
    "statuses",
    [
        ["unknown"],
        ["pending", "pending"],
    ],
    ids=["illegal-status", "multiple-pending"],
)
def test_initialize_rejects_invalid_legacy_patch_rows_without_changing_original_data(
    tmp_path: Path,
    statuses: list[str],
) -> None:
    store, legacy_rows = create_legacy_patch_database(tmp_path, statuses=statuses)
    with sqlite3.connect(store.database_path) as connection:
        original_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patches'"
        ).fetchone()[0]

    with pytest.raises(StoreError) as invalid:
        store.initialize()

    assert invalid.value.error_code == "SCHEMA_INVALID"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT id, status, patch_json FROM patches ORDER BY rowid"
        ).fetchall() == legacy_rows
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patches'"
        ).fetchone()[0] == original_table_sql


def test_initialize_migrates_legacy_runs_operation_check_losslessly_and_idempotently(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        original_rows = connection.execute(
            "SELECT * FROM runs ORDER BY rowid"
        ).fetchall()

    store.initialize()
    with sqlite3.connect(store.database_path) as connection:
        first_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()[0]
        first_rows = connection.execute(
            "SELECT * FROM runs ORDER BY rowid"
        ).fetchall()
    store.initialize()
    with sqlite3.connect(store.database_path) as connection:
        second_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()[0]
        second_rows = connection.execute(
            "SELECT * FROM runs ORDER BY rowid"
        ).fetchall()
        connection.execute(
            """
            INSERT INTO runs (
                id, project_id, version_id, operation, mode, status,
                metadata_json, usage_json, error_code, started_at, ended_at
            ) VALUES (?, ?, NULL, 'revision', 'mock', 'succeeded', ?, ?, NULL, ?, ?)
            """,
            (
                "run-legal-revision",
                "project-001",
                revision_metadata().model_dump_json(),
                revision_usage().model_dump_json(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO runs (
                    id, project_id, version_id, operation, mode, status,
                    metadata_json, usage_json, error_code, started_at, ended_at
                ) VALUES (?, ?, NULL, 'unknown', 'mock', 'succeeded', ?, ?, NULL, ?, ?)
                """,
                (
                    "run-illegal-operation",
                    "project-001",
                    revision_metadata().model_dump_json(),
                    revision_usage().model_dump_json(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )

    assert first_rows == original_rows
    assert second_rows == original_rows
    assert first_table_sql == second_table_sql
    assert all(
        operation in first_table_sql
        for operation in ("parse", "generate", "quick_check", "deep_audit", "revision")
    )
    assert store.inspect_constraints()["runs.operation"] == (
        "TEXT NOT NULL CHECK "
        "(operation IN ('parse', 'generate', 'quick_check', "
        "'deep_audit', 'revision'))"
    )


def test_initialize_rejects_invalid_legacy_run_without_partial_migration(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    invalid_operation = "SECRET_INVALID_RUN_OPERATION"
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("ALTER TABLE runs RENAME TO runs_before_invalid_test")
        connection.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_id TEXT,
                operation TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('local', 'mock', 'live')),
                status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                metadata_json TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                error_code TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (version_id) REFERENCES versions(id)
            )
            """
        )
        connection.execute("INSERT INTO runs SELECT * FROM runs_before_invalid_test")
        connection.execute(
            """
            INSERT INTO runs (
                id, project_id, version_id, operation, mode, status,
                metadata_json, usage_json, error_code, started_at, ended_at
            ) VALUES (?, ?, NULL, ?, 'mock', 'succeeded', ?, ?, NULL, ?, ?)
            """,
            (
                "run-invalid-legacy-operation",
                "project-001",
                invalid_operation,
                revision_metadata().model_dump_json(),
                revision_usage().model_dump_json(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute("DROP TABLE runs_before_invalid_test")
        original_table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()[0]
        original_rows = connection.execute(
            "SELECT * FROM runs ORDER BY rowid"
        ).fetchall()

    with pytest.raises(StoreError) as invalid:
        store.initialize()

    assert invalid.value.error_code == "SCHEMA_INVALID"
    assert invalid_operation not in invalid.value.message
    assert str(store.database_path) not in invalid.value.message
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()[0] == original_table_sql
        assert connection.execute(
            "SELECT * FROM runs ORDER BY rowid"
        ).fetchall() == original_rows


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
        "restore_idempotency": 0,
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


def test_patch_preview_reads_minimum_sentence_context_without_changing_current_version(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, evidence, _ = save_generated_and_quick_checked(store, tmp_path)
    before_view = store.get_project_view("project-001", model_mode="mock")
    target = bundle.document.sections[0].sentences[0]

    version_no, current_text, related_evidence = store.get_sentence_revision_inputs(
        "project-001",
        base_version_id=version_id,
        sentence_id=target.sentence_id,
    )
    patch = EditPatch(
        patch_id="patch-preview-001",
        base_version=version_no,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(current_text.encode("utf-8")).hexdigest(),
        before_text=current_text,
        after_text=f"{current_text}（简化表达）",
        reason="保持事实不变并简化表达。",
        fact_changed=False,
        evidence_changed=False,
    )
    saved = save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    after_view = store.get_project_view("project-001", model_mode="mock")

    expected_claim_ids = {
        claim.claim_id
        for claim in bundle.claims
        if claim.sentence_id == target.sentence_id
    }
    assert {record.claim_id for record in related_evidence} == expected_claim_ids
    assert all(record.quote_verified for record in related_evidence)
    assert saved == patch
    assert after_view.stage == ProjectStage.PATCH_PENDING
    assert after_view.pending_patch == patch
    assert after_view.current_version_id == before_view.current_version_id
    assert after_view.current_version_no == before_view.current_version_no
    assert after_view.document == before_view.document
    assert after_view.claims == before_view.claims
    assert after_view.evidence_records == before_view.evidence_records
    assert after_view.audit_report == before_view.audit_report
    assert after_view.versions == before_view.versions
    assert store.row_counts()["versions"] == 1
    assert store.row_counts()["patches"] == 1
    assert store.validate_all_snapshots()["patch_json"] == 1
    assert evidence.evidence_records


def test_patch_preview_and_succeeded_revision_run_are_one_transaction(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    patch = sentence_patch(bundle, patch_id="patch-with-revision-run")
    before = store.row_counts()

    saved = store.save_patch_preview(
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
        mode="live",
        metadata=revision_metadata(),
        usage=revision_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    runs = operation_runs(store, "revision")
    assert saved == patch
    assert store.row_counts() == {
        **before,
        "patches": before["patches"] + 1,
        "runs": before["runs"] + 1,
    }
    assert len(runs) == 1
    assert runs[0]["version_id"] is None
    assert runs[0]["operation"] == "revision"
    assert runs[0]["mode"] == "live"
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["error_code"] is None
    assert runs[0]["metadata"] == revision_metadata().model_dump(mode="json")
    assert runs[0]["usage"] == revision_usage().model_dump(mode="json")


def test_patch_preview_database_failure_rolls_back_patch_run_and_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    patch = sentence_patch(bundle, patch_id="patch-rollback-revision-run")
    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()

    def fail_revision_run(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected revision run failure")

    monkeypatch.setattr(store, "_insert_run", fail_revision_run)
    with pytest.raises(RuntimeError, match="injected revision run failure"):
        store.save_patch_preview(
            project_id="project-001",
            base_version_id=version_id,
            patch=patch,
            created_at=NOW,
            mode="mock",
            metadata=revision_metadata(),
            usage=revision_usage(),
            started_at=NOW,
            ended_at=NOW,
        )

    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots
    assert operation_runs(store, "revision") == []


@pytest.mark.parametrize(
    "source_stage",
    [ProjectStage.QUICK_CHECKED, ProjectStage.DEEP_AUDITED],
)
def test_patch_preview_enters_pending_and_survives_store_reload(
    tmp_path: Path,
    source_stage: ProjectStage,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, evidence, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    if source_stage == ProjectStage.DEEP_AUDITED:
        save_deep_audited(
            store,
            tmp_path,
            version_id=version_id,
            bundle=bundle,
            evidence=evidence,
        )
    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    patch = sentence_patch(bundle, patch_id=f"patch-reload-{source_stage.value}")

    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
        mode="mock",
        metadata=revision_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    pending = store.get_project_view("project-001", model_mode="mock")
    reloaded_store = ProjectStore(tmp_path)
    reloaded_store.initialize()
    reloaded = reloaded_store.get_project_view("project-001", model_mode="mock")

    assert pending.stage == ProjectStage.PATCH_PENDING
    assert pending.pending_patch == patch
    assert reloaded == pending
    assert pending.current_version_id == stable.current_version_id
    assert pending.current_version_no == stable.current_version_no
    assert pending.document == stable.document
    assert pending.claims == stable.claims
    assert pending.evidence_records == stable.evidence_records
    assert pending.audit_report == stable.audit_report
    assert pending.versions == stable.versions
    assert store.row_counts() == {
        **counts,
        "patches": counts["patches"] + 1,
        "runs": counts["runs"] + 1,
    }


def test_second_pending_patch_is_rejected_without_replacing_first(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    first = sentence_patch(bundle, patch_id="patch-only-pending")
    second = sentence_patch(
        bundle,
        patch_id="patch-forbidden-second-pending",
        suffix="！",
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=first,
        created_at=NOW,
    )
    pending = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()

    with pytest.raises(StoreError) as not_ready:
        store.save_patch_preview(
            project_id="project-001",
            base_version_id=version_id,
            patch=second,
            created_at=NOW,
            mode="mock",
            metadata=revision_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )

    assert not_ready.value.error_code == "PROJECT_NOT_READY"
    assert store.get_project_view("project-001", model_mode="mock") == pending
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots
    assert store.inspect_patch_status("project-001", first.patch_id) == "pending"
    with pytest.raises(StoreError) as missing:
        store.inspect_patch_status("project-001", second.patch_id)
    assert missing.value.error_code == "PATCH_NOT_FOUND"


@pytest.mark.parametrize(
    "source_stage",
    [ProjectStage.QUICK_CHECKED, ProjectStage.DEEP_AUDITED],
)
def test_reject_restores_source_stable_stage_and_clears_pending_patch(
    tmp_path: Path,
    source_stage: ProjectStage,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, evidence, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    if source_stage == ProjectStage.DEEP_AUDITED:
        save_deep_audited(
            store,
            tmp_path,
            version_id=version_id,
            bundle=bundle,
            evidence=evidence,
        )
    stable = store.get_project_view("project-001", model_mode="mock")
    patch = sentence_patch(bundle, patch_id=f"patch-reject-{source_stage.value}")
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
        mode="mock",
        metadata=revision_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    revision_before_reject = operation_runs(store, "revision")
    assert len(revision_before_reject) == 1
    assert revision_before_reject[0]["version_id"] is None
    counts = store.row_counts()

    store.reject_patch("project-001", patch_id=patch.patch_id)
    restored = store.get_project_view("project-001", model_mode="mock")
    store.reject_patch("project-001", patch_id=patch.patch_id)

    assert restored.stage == source_stage
    assert restored.pending_patch is None
    assert restored.current_version_id == stable.current_version_id
    assert restored.document == stable.document
    assert restored.claims == stable.claims
    assert restored.evidence_records == stable.evidence_records
    assert restored.audit_report == stable.audit_report
    assert restored.versions == stable.versions
    assert store.inspect_patch_status("project-001", patch.patch_id) == "rejected"
    assert store.get_project_view("project-001", model_mode="mock") == restored
    assert store.row_counts() == counts
    revision_after_reject = operation_runs(store, "revision")
    assert revision_after_reject == revision_before_reject
    assert revision_after_reject[0]["version_id"] is None


def test_document_revision_inputs_return_only_current_document(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)

    version_no, current_document = store.get_document_revision_inputs(
        "project-001",
        base_version_id=version_id,
    )

    assert version_no == 1
    assert current_document == bundle.document


@pytest.mark.parametrize(
    "mutation",
    ["added", "deleted", "replaced", "duplicated", "moved", "reordered"],
)
def test_document_sentence_identity_preview_rejects_every_skeleton_change_without_writes(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    before_text = bundle.document.model_dump_json()
    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()
    patch = EditPatch(
        patch_id=f"patch-document-identity-preview-{mutation}",
        base_version=1,
        scope="document",
        target_sentence_ids=[],
        before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
        before_text=before_text,
        after_text=document_revision_after_text(bundle.document, mutation),
        reason="Attempt to change the sentence identity skeleton.",
        fact_changed=True,
        evidence_changed=True,
    )

    with pytest.raises(StoreError) as rejected:
        store.save_patch_preview(
            project_id="project-001",
            base_version_id=version_id,
            patch=patch,
            created_at=NOW,
            mode="mock",
            metadata=revision_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )

    assert rejected.value.error_code == "PATCH_INVALID"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots


def test_patch_preview_rejects_stale_missing_sentence_and_out_of_scope_patch(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    target = bundle.document.sections[0].sentences[0]
    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()

    with pytest.raises(StoreError) as stale:
        store.get_document_revision_inputs(
            "project-001",
            base_version_id="version-stale",
        )
    assert stale.value.error_code == "TARGET_STALE"

    with pytest.raises(StoreError) as missing_sentence:
        store.get_sentence_revision_inputs(
            "project-001",
            base_version_id=version_id,
            sentence_id="s-missing",
        )
    assert missing_sentence.value.error_code == "PATCH_INVALID"

    invalid_patch = EditPatch(
        patch_id="patch-preview-invalid",
        base_version=1,
        scope="sentence",
        target_sentence_ids=["s-other"],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text="越界修改。",
        reason="越界测试。",
        fact_changed=True,
        evidence_changed=True,
    )
    with pytest.raises(StoreError) as invalid:
        store.save_patch_preview(
            project_id="project-001",
            base_version_id=version_id,
            patch=invalid_patch,
            created_at=NOW,
            mode="mock",
            metadata=revision_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )
    assert invalid.value.error_code == "PATCH_INVALID"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots


def test_reject_patch_transitions_pending_and_is_idempotent_without_other_writes(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    target = bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-reject-pending",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text}!",
        reason="Create a pending patch for rejection.",
        fact_changed=False,
        evidence_changed=False,
    )
    stable = store.get_project_view("project-001", model_mode="mock")
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    pending = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()

    assert pending.stage == ProjectStage.PATCH_PENDING
    assert pending.pending_patch == patch

    store.reject_patch("project-001", patch_id=patch.patch_id)

    assert store.inspect_patch_status("project-001", patch.patch_id) == "rejected"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots

    store.reject_patch("project-001", patch_id=patch.patch_id)

    assert store.inspect_patch_status("project-001", patch.patch_id) == "rejected"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots


def test_reject_patch_rejects_accepted_and_missing_without_writes(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    target = bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-reject-accepted",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text}!",
        reason="Accept before attempting rejection.",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    _, revised_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    records, report = mock_audit_service(tmp_path).quick_check(revised_bundle, blocks)
    store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=revised_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()

    with pytest.raises(StoreError) as accepted:
        store.reject_patch("project-001", patch_id=patch.patch_id)
    assert accepted.value.error_code == "PATCH_INVALID"

    with pytest.raises(StoreError) as missing:
        store.reject_patch("project-001", patch_id="patch-missing")
    assert missing.value.error_code == "PATCH_NOT_FOUND"

    assert store.inspect_patch_status("project-001", patch.patch_id) == "accepted"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots


def test_accept_sentence_patch_creates_child_version_and_preserves_other_sentences(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, original_bundle, original_evidence, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    target = original_bundle.document.sections[0].sentences[0]
    after_text = f"{target.text}!"
    patch = EditPatch(
        patch_id="patch-accept-sentence",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=after_text,
        reason="保持事实不变并简化表达。",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
        mode="mock",
        metadata=revision_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    revision_before_accept = operation_runs(store, "revision")
    assert len(revision_before_accept) == 1
    assert revision_before_accept[0]["version_id"] is None

    saved_patch, revised_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    records, report = mock_audit_service(tmp_path).quick_check(revised_bundle, blocks)
    new_version_id, new_version_no = store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=revised_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    original_texts = {
        sentence.sentence_id: sentence.text
        for section in original_bundle.document.sections
        for sentence in section.sentences
    }
    revised_texts = {
        sentence.sentence_id: sentence.text
        for section in revised_bundle.document.sections
        for sentence in section.sentences
    }
    assert saved_patch == patch
    assert revised_texts[target.sentence_id] == after_text
    assert {
        sentence_id: text
        for sentence_id, text in revised_texts.items()
        if sentence_id != target.sentence_id
    } == {
        sentence_id: text
        for sentence_id, text in original_texts.items()
        if sentence_id != target.sentence_id
    }
    assert new_version_id != version_id
    assert new_version_no == 2
    view = store.get_project_view("project-001", model_mode="mock")
    assert view.current_version_id == new_version_id
    assert view.stage == ProjectStage.QUICK_CHECKED
    assert view.pending_patch is None
    assert [version.version_id for version in view.versions] == [
        version_id,
        new_version_id,
    ]
    assert view.versions[1].parent_version_id == version_id
    assert view.claims == original_bundle.claims
    assert view.evidence_records == original_evidence.evidence_records
    assert store.row_counts()["versions"] == 2
    assert store.row_counts()["audits"] == 2
    revision_after_accept = operation_runs(store, "revision")
    assert len(revision_after_accept) == 1
    assert revision_after_accept[0]["id"] == revision_before_accept[0]["id"]
    assert revision_after_accept[0]["version_id"] == new_version_id


@pytest.mark.parametrize(
    ("fact_changed", "evidence_changed", "after_text"),
    [
        (True, False, "The revised target states the opposite result."),
        (False, True, "The revised target requires different evidence."),
        (False, False, "The provider falsely labels an opposite fact as style-only."),
    ],
    ids=["fact-changed", "evidence-changed", "false-change-flags"],
)
def test_sentence_patch_regeneration_context_is_computed_from_stable_snapshot(
    tmp_path: Path,
    fact_changed: bool,
    evidence_changed: bool,
    after_text: str,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    target = bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id=f"patch-sentence-reuse-{int(fact_changed)}-{int(evidence_changed)}",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=after_text,
        reason="Exercise the code-owned sentence acceptance boundary.",
        fact_changed=fact_changed,
        evidence_changed=evidence_changed,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )

    saved_patch, provisional_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    (
        original_claims,
        related_evidence,
        allowed_block_ids,
        reserved_claim_ids,
    ) = store.get_sentence_claim_regeneration_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )

    expected_original_claims = [
        claim for claim in bundle.claims
        if claim.sentence_id == target.sentence_id
    ]
    expected_reserved_claims = {
        claim.claim_id for claim in bundle.claims
        if claim.sentence_id != target.sentence_id
    }
    assert saved_patch == patch
    assert store.requires_sentence_claim_regeneration(saved_patch) is True
    assert provisional_bundle.document != bundle.document
    assert provisional_bundle.claims == bundle.claims
    assert original_claims == expected_original_claims
    assert {record.claim_id for record in related_evidence} == {
        claim.claim_id for claim in expected_original_claims
    }
    assert all(record.quote_verified for record in related_evidence)
    assert allowed_block_ids == {"p01-b001"}
    assert reserved_claim_ids == expected_reserved_claims
    assert blocks == load_parse_snapshot().blocks
    assert store.inspect_patch_status("project-001", patch.patch_id) == "pending"


def test_nontrivial_sentence_claims_replace_target_in_provider_order(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, original_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    target = original_bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-sentence-regenerated",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text} Non-trivial revision.",
        reason="Regenerate target claims.",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    saved_patch, provisional_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    original_target_claims, _, _, _ = (
        store.get_sentence_claim_regeneration_inputs(
            "project-001",
            patch_id=patch.patch_id,
        )
    )
    first_target_claim = original_target_claims[0]
    regenerated_claims = [
        first_target_claim.model_copy(
            update={
                "claim_id": "replacement-z",
                "text": "Provider result first.",
            }
        ),
        first_target_claim.model_copy(
            update={
                "claim_id": "replacement-a",
                "text": "Provider result second.",
            }
        ),
    ]

    revised_bundle = store.build_sentence_revision_bundle(
        patch=saved_patch,
        provisional_bundle=provisional_bundle,
        regenerated_claims=regenerated_claims,
    )

    unselected_claims = [
        claim for claim in original_bundle.claims
        if claim.sentence_id != target.sentence_id
    ]
    assert [claim.claim_id for claim in revised_bundle.claims] == [
        "replacement-z",
        "replacement-a",
        *[claim.claim_id for claim in unselected_claims],
    ]
    assert [
        claim for claim in revised_bundle.claims
        if claim.sentence_id != target.sentence_id
    ] == unselected_claims
    records, report = mock_audit_service(tmp_path).quick_check(
        revised_bundle,
        blocks,
    )
    new_version_id, version_no = store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=revised_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    view = store.get_project_view("project-001", model_mode="mock")
    assert version_no == 2
    assert view.current_version_id == new_version_id
    assert view.claims == revised_bundle.claims
    assert view.stage == ProjectStage.QUICK_CHECKED
    assert view.pending_patch is None
    assert store.inspect_patch_status("project-001", patch.patch_id) == "accepted"


def test_sentence_accept_rejects_unselected_claim_mutation_without_writes(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, original_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    stable = store.get_project_view("project-001", model_mode="mock")
    target = original_bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-sentence-mutated-unselected",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text} Non-trivial revision.",
        reason="Reject unrelated claim mutation.",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    pending = store.get_project_view("project-001", model_mode="mock")
    saved_patch, provisional_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    original_target_claims, _, _, _ = (
        store.get_sentence_claim_regeneration_inputs(
            "project-001",
            patch_id=patch.patch_id,
        )
    )
    revised_bundle = store.build_sentence_revision_bundle(
        patch=saved_patch,
        provisional_bundle=provisional_bundle,
        regenerated_claims=[
            original_target_claims[0].model_copy(
                update={"claim_id": "replacement-target"}
            )
        ],
    )
    mutated_claims = list(revised_bundle.claims)
    mutated_claims[-1] = mutated_claims[-1].model_copy(
        update={"text": "Unconfirmed mutation to another sentence claim."}
    )
    mutated_bundle = GeneratedBundle(
        document=revised_bundle.document,
        claims=mutated_claims,
    )
    records, report = mock_audit_service(tmp_path).quick_check(
        mutated_bundle,
        blocks,
    )
    counts = store.row_counts()

    with pytest.raises(StoreError) as rejected:
        store.accept_patch(
            project_id="project-001",
            patch_id=patch.patch_id,
            bundle=mutated_bundle,
            evidence=EvidenceSnapshot(evidence_records=records),
            report=report,
            metadata=success_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )

    assert rejected.value.error_code == "PATCH_INVALID"
    assert pending.stage == ProjectStage.PATCH_PENDING
    assert pending.pending_patch == patch
    assert store.get_project_view("project-001", model_mode="mock") == pending
    assert store.row_counts() == counts
    assert store.inspect_patch_status("project-001", patch.patch_id) == "pending"


def test_sentence_accept_transaction_failure_rolls_back_all_acceptance_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = parsed_store(tmp_path)
    version_id, original_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    target = original_bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-sentence-transaction-failure",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text} Non-trivial revision.",
        reason="Exercise acceptance rollback.",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    saved_patch, provisional_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    original_target_claims, _, _, _ = (
        store.get_sentence_claim_regeneration_inputs(
            "project-001",
            patch_id=patch.patch_id,
        )
    )
    revised_bundle = store.build_sentence_revision_bundle(
        patch=saved_patch,
        provisional_bundle=provisional_bundle,
        regenerated_claims=[
            original_target_claims[0].model_copy(
                update={"claim_id": "replacement-before-rollback"}
            )
        ],
    )
    records, report = mock_audit_service(tmp_path).quick_check(
        revised_bundle,
        blocks,
    )
    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    original_insert_run = store._insert_run

    def fail_run_insert(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected acceptance transaction failure")

    monkeypatch.setattr(store, "_insert_run", fail_run_insert)
    with pytest.raises(
        RuntimeError,
        match="injected acceptance transaction failure",
    ):
        store.accept_patch(
            project_id="project-001",
            patch_id=patch.patch_id,
            bundle=revised_bundle,
            evidence=EvidenceSnapshot(evidence_records=records),
            report=report,
            metadata=success_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )
    monkeypatch.setattr(store, "_insert_run", original_insert_run)

    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.inspect_patch_status("project-001", patch.patch_id) == "pending"
    assert stable.stage == ProjectStage.PATCH_PENDING
    assert stable.pending_patch == patch


def test_accept_patch_rejects_missing_and_stale_targets_without_new_version(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(store, tmp_path)
    target = bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-stale-after-accept",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text}!",
        reason="第一次修订。",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )

    with pytest.raises(StoreError) as missing:
        store.get_patch_acceptance_inputs(
            "project-001",
            patch_id="patch-missing",
        )
    assert missing.value.error_code == "PATCH_NOT_FOUND"

    _, revised_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    records, report = mock_audit_service(tmp_path).quick_check(revised_bundle, blocks)
    store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=revised_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    with pytest.raises(StoreError) as stale:
        store.get_patch_acceptance_inputs(
            "project-001",
            patch_id=patch.patch_id,
        )
    assert stale.value.error_code == "TARGET_STALE"
    assert store.row_counts()["versions"] == 2


def test_accept_document_patch_allows_rebuilt_claims_but_not_a_different_document(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    version_id, original_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    revised_payload = original_bundle.document.model_dump(mode="json")
    revised_payload["title"] = "修订后的本科生论文解读"
    revised_payload["sections"][0]["heading"] = "修订后的研究问题"
    revised_payload["sections"][0]["sentences"][0]["text"] += " Revised wording."
    revised_document = type(original_bundle.document).model_validate(revised_payload)
    before_text = original_bundle.document.model_dump_json()
    patch = EditPatch(
        patch_id="patch-accept-document",
        base_version=1,
        scope="document",
        target_sentence_ids=[],
        before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
        before_text=before_text,
        after_text=revised_document.model_dump_json(),
        reason="统一全文表达。",
        fact_changed=True,
        evidence_changed=True,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    _, patched_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    rebuilt_bundle = GeneratedBundle(
        document=patched_bundle.document,
        claims=[
            claim.model_copy(update={"claim_id": f"rebuilt-{claim.claim_id}"})
            for claim in patched_bundle.claims
        ],
    )
    records, report = mock_audit_service(tmp_path).quick_check(rebuilt_bundle, blocks)

    with pytest.raises(StoreError) as wrong_document:
        store.accept_patch(
            project_id="project-001",
            patch_id=patch.patch_id,
            bundle=rebuilt_bundle.model_copy(
                update={
                    "document": rebuilt_bundle.document.model_copy(
                        update={"title": "未获确认的标题"}
                    )
                }
            ),
            evidence=EvidenceSnapshot(evidence_records=records),
            report=report,
            metadata=success_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )
    assert wrong_document.value.error_code == "PATCH_INVALID"
    assert store.row_counts()["versions"] == 1

    new_version_id, version_no = store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=rebuilt_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )
    view = store.get_project_view("project-001", model_mode="mock")
    assert version_no == 2
    assert view.current_version_id == new_version_id
    assert view.document == revised_document
    assert view.claims == rebuilt_bundle.claims
    assert view.stage == ProjectStage.QUICK_CHECKED
    assert view.pending_patch is None


@pytest.mark.parametrize(
    "mutation",
    ["added", "deleted", "replaced", "duplicated", "moved", "reordered"],
)
def test_document_sentence_identity_final_transaction_rejects_tampered_patch_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    store = parsed_store(tmp_path)
    version_id, original_bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    revised_document = original_bundle.document.model_copy(
        update={"title": "A legal document revision"}
    )
    before_text = original_bundle.document.model_dump_json()
    patch = EditPatch(
        patch_id=f"patch-document-identity-final-{mutation}",
        base_version=1,
        scope="document",
        target_sentence_ids=[],
        before_hash=sha256(before_text.encode("utf-8")).hexdigest(),
        before_text=before_text,
        after_text=revised_document.model_dump_json(),
        reason="A legal preview before final transaction revalidation.",
        fact_changed=True,
        evidence_changed=True,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=version_id,
        patch=patch,
        created_at=NOW,
    )
    _, patched_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    rebuilt_bundle = GeneratedBundle(
        document=patched_bundle.document,
        claims=[
            claim.model_copy(update={"claim_id": f"rebuilt-{claim.claim_id}"})
            for claim in patched_bundle.claims
        ],
    )
    records, report = mock_audit_service(tmp_path).quick_check(rebuilt_bundle, blocks)
    tampered_patch = patch.model_copy(
        update={
            "after_text": document_revision_after_text(
                original_bundle.document,
                mutation,
            )
        }
    )
    original_validated_json = store._validated_json

    def substitute_patch(model_type: object, value: str) -> object:
        if model_type is EditPatch:
            return tampered_patch
        return original_validated_json(model_type, value)

    stable = store.get_project_view("project-001", model_mode="mock")
    counts = store.row_counts()
    monkeypatch.setattr(store, "_validated_json", substitute_patch)

    with pytest.raises(StoreError) as rejected:
        store.accept_patch(
            project_id="project-001",
            patch_id=patch.patch_id,
            bundle=rebuilt_bundle,
            evidence=EvidenceSnapshot(evidence_records=records),
            report=report,
            metadata=success_metadata(),
            usage=empty_usage(),
            started_at=NOW,
            ended_at=NOW,
        )

    assert rejected.value.error_code == "PATCH_INVALID"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == counts
    assert store.inspect_patch_status("project-001", patch.patch_id) == "pending"
    assert stable.stage == ProjectStage.PATCH_PENDING
    assert stable.pending_patch == patch


def test_restore_copies_historical_content_claims_and_audit_into_new_child_version(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    first_version_id, bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
    )
    first_snapshot = store.get_project_view("project-001", model_mode="mock")
    target = bundle.document.sections[0].sentences[0]
    patch = EditPatch(
        patch_id="patch-before-restore",
        base_version=1,
        scope="sentence",
        target_sentence_ids=[target.sentence_id],
        before_hash=sha256(target.text.encode("utf-8")).hexdigest(),
        before_text=target.text,
        after_text=f"{target.text}!",
        reason="生成第二版。",
        fact_changed=False,
        evidence_changed=False,
    )
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=first_version_id,
        patch=patch,
        created_at=NOW,
    )
    _, second_bundle, blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    records, report = mock_audit_service(tmp_path).quick_check(second_bundle, blocks)
    second_version_id, _ = store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=second_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    restored_version = store.restore_version(
        "project-001",
        version_id=first_version_id,
        key_hash=RESTORE_KEY_HASH,
        restored_at=NOW,
    )

    restored = store.get_project_view("project-001", model_mode="mock")
    assert restored_version.version_no == 3
    assert restored_version.version_id not in {first_version_id, second_version_id}
    assert restored.current_version_id == restored_version.version_id
    assert restored.document == first_snapshot.document
    assert restored.claims == first_snapshot.claims
    assert restored.evidence_records == first_snapshot.evidence_records
    assert restored.audit_report == first_snapshot.audit_report
    assert len(restored.versions) == 3
    assert restored.versions[-1].parent_version_id == second_version_id
    assert restored.versions[-1].reason == f"restore:{first_version_id}"
    assert store.row_counts()["versions"] == 3
    assert store.row_counts()["audits"] == 3
    assert store.row_counts()["restore_idempotency"] == 1


def test_restore_missing_version_has_no_side_effects(tmp_path: Path) -> None:
    store = parsed_store(tmp_path)
    save_generated_and_quick_checked(store, tmp_path)
    stable = store.get_project_view("project-001", model_mode="mock")

    with pytest.raises(StoreError) as missing:
        store.restore_version(
            "project-001",
            version_id="version-missing",
            key_hash=RESTORE_KEY_HASH,
            restored_at=NOW,
        )

    assert missing.value.error_code == "VERSION_NOT_FOUND"
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts()["versions"] == 1
    assert store.row_counts()["audits"] == 1


def test_restore_idempotency_table_enforces_hash_foreign_keys_and_unique_result(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    target_version_id, _, _, _ = save_generated_and_quick_checked(store, tmp_path)
    restored = store.restore_version(
        "project-001",
        version_id=target_version_id,
        key_hash=RESTORE_KEY_HASH,
        restored_at=NOW,
    )

    def insert_record(
        key_hash: str,
        project_id: str,
        target_id: str,
        result_id: str,
    ) -> None:
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO restore_idempotency (
                    key_hash, project_id, target_version_id,
                    result_version_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (key_hash, project_id, target_id, result_id, NOW.isoformat()),
            )

    for invalid_hash in ("a" * 63, "A" * 64, "g" * 64):
        with pytest.raises(sqlite3.IntegrityError):
            insert_record(
                invalid_hash,
                "project-001",
                target_version_id,
                target_version_id,
            )

    with pytest.raises(sqlite3.IntegrityError):
        insert_record(
            OTHER_RESTORE_KEY_HASH,
            "project-missing",
            target_version_id,
            target_version_id,
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(
            OTHER_RESTORE_KEY_HASH,
            "project-001",
            "version-missing",
            target_version_id,
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(
            OTHER_RESTORE_KEY_HASH,
            "project-001",
            target_version_id,
            "version-missing",
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(
            OTHER_RESTORE_KEY_HASH,
            "project-001",
            target_version_id,
            restored.version_id,
        )


def test_restore_replays_serial_duplicate_without_new_side_effects(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    target_version_id, _, _, _ = save_generated_and_quick_checked(store, tmp_path)
    before = store.row_counts()

    first = store.restore_version(
        "project-001",
        version_id=target_version_id,
        key_hash=RESTORE_KEY_HASH,
        restored_at=NOW,
    )
    after_first = store.row_counts()
    second = store.restore_version(
        "project-001",
        version_id=target_version_id,
        key_hash=RESTORE_KEY_HASH,
        restored_at=NOW,
    )

    assert second == first
    assert after_first == {
        **before,
        "versions": before["versions"] + 1,
        "audits": before["audits"] + 1,
        "restore_idempotency": before["restore_idempotency"] + 1,
    }
    assert store.row_counts() == after_first
    assert store.get_project_view(
        "project-001",
        model_mode="mock",
    ).current_version_id == first.version_id


def test_restore_replays_concurrent_duplicate_with_one_winning_transaction(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    target_version_id, _, _, _ = save_generated_and_quick_checked(store, tmp_path)
    before = store.row_counts()
    barrier = Barrier(6)

    def restore_once() -> object:
        barrier.wait()
        return store.restore_version(
            "project-001",
            version_id=target_version_id,
            key_hash=RESTORE_KEY_HASH,
            restored_at=NOW,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _index: restore_once(), range(6)))

    assert results == [results[0]] * 6
    assert store.row_counts() == {
        **before,
        "versions": before["versions"] + 1,
        "audits": before["audits"] + 1,
        "restore_idempotency": before["restore_idempotency"] + 1,
    }


def test_restore_rejects_same_key_hash_for_different_target_or_project(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    first_target, _, _, _ = save_generated_and_quick_checked(store, tmp_path)
    other_store = parsed_store(tmp_path, project_id="project-002")
    other_target, _, _, _ = save_generated_and_quick_checked(
        other_store,
        tmp_path,
        project_id="project-002",
    )
    first = store.restore_version(
        "project-001",
        version_id=first_target,
        key_hash=RESTORE_KEY_HASH,
        restored_at=NOW,
    )
    stable_counts = store.row_counts()

    with pytest.raises(StoreError) as target_conflict:
        store.restore_version(
            "project-001",
            version_id=first.version_id,
            key_hash=RESTORE_KEY_HASH,
            restored_at=NOW,
        )
    with pytest.raises(StoreError) as project_conflict:
        other_store.restore_version(
            "project-002",
            version_id=other_target,
            key_hash=RESTORE_KEY_HASH,
            restored_at=NOW,
        )

    for conflict in (target_conflict.value, project_conflict.value):
        assert conflict.error_code == "IDEMPOTENCY_CONFLICT"
        assert conflict.retryable is False
        assert RESTORE_KEY not in conflict.message
        assert RESTORE_KEY_HASH not in conflict.message
    assert store.row_counts() == stable_counts


def test_restore_rolls_back_failed_transaction_and_allows_same_key_retry(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    target_version_id, _, _, _ = save_generated_and_quick_checked(store, tmp_path)
    stable = store.get_project_view("project-001", model_mode="mock")
    stable_counts = store.row_counts()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_restore_idempotency
            BEFORE INSERT ON restore_idempotency
            BEGIN
                SELECT RAISE(ABORT, 'injected restore idempotency failure');
            END
            """
        )

    with pytest.raises(StoreError) as failed:
        store.restore_version(
            "project-001",
            version_id=target_version_id,
            key_hash=RESTORE_KEY_HASH,
            restored_at=NOW,
        )

    assert failed.value.error_code == "SCHEMA_INVALID"
    assert failed.value.message == "The project database operation failed."
    assert "injected" not in failed.value.message
    assert store.get_project_view("project-001", model_mode="mock") == stable
    assert store.row_counts() == stable_counts

    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TRIGGER fail_restore_idempotency")
    restored = store.restore_version(
        "project-001",
        version_id=target_version_id,
        key_hash=RESTORE_KEY_HASH,
        restored_at=NOW,
    )

    assert restored.version_no == 2
    assert store.row_counts()["restore_idempotency"] == 1


@pytest.mark.parametrize("foreign_version", ["target", "result"])
def test_restore_rejects_idempotency_record_with_cross_project_version_ownership(
    tmp_path: Path,
    foreign_version: str,
) -> None:
    store = parsed_store(tmp_path)
    target_version_id, _, _, _ = save_generated_and_quick_checked(store, tmp_path)
    other_store = parsed_store(tmp_path, project_id="project-002")
    other_version_id, _, _, _ = save_generated_and_quick_checked(
        other_store,
        tmp_path,
        project_id="project-002",
    )
    recorded_target = (
        other_version_id if foreign_version == "target" else target_version_id
    )
    recorded_result = (
        other_version_id if foreign_version == "result" else target_version_id
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO restore_idempotency (
                key_hash, project_id, target_version_id,
                result_version_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                RESTORE_KEY_HASH,
                "project-001",
                recorded_target,
                recorded_result,
                NOW.isoformat(),
            ),
        )
    stable_counts = store.row_counts()

    with pytest.raises(StoreError) as corrupt:
        store.restore_version(
            "project-001",
            version_id=recorded_target,
            key_hash=RESTORE_KEY_HASH,
            restored_at=NOW,
        )

    assert corrupt.value.error_code == "SCHEMA_INVALID"
    assert RESTORE_KEY_HASH not in corrupt.value.message
    assert store.row_counts() == stable_counts


def test_export_inputs_return_only_current_stable_snapshot(tmp_path: Path) -> None:
    store = parsed_store(tmp_path)
    version_id, bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
        generation_model="stored-model",
        generation_mode="mock",
    )
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()

    (
        document,
        current_version_id,
        version_no,
        created_at,
        pdf_sha256,
        source_model,
        source_mode,
    ) = store.get_export_inputs("project-001")

    assert document == bundle.document
    assert current_version_id == version_id
    assert version_no == 1
    assert created_at == NOW
    assert pdf_sha256 == "a" * 64
    assert source_model == "stored-model"
    assert source_mode == "mock"
    assert store.row_counts()["versions"] == 1
    assert store.row_counts()["audits"] == 1
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots


def test_export_inputs_trace_revision_and_recursive_restore_sources(
    tmp_path: Path,
) -> None:
    store = parsed_store(tmp_path)
    generated_version_id, bundle, _, _ = save_generated_and_quick_checked(
        store,
        tmp_path,
        generation_model="generation-source",
        generation_mode="mock",
    )
    patch = sentence_patch(bundle, patch_id="patch-export-provenance")
    save_revision_preview(
        store,
        project_id="project-001",
        base_version_id=generated_version_id,
        patch=patch,
        mode="live",
        metadata=revision_metadata(model="revision-source"),
    )
    saved_patch, revised_bundle, source_blocks = store.get_patch_acceptance_inputs(
        "project-001",
        patch_id=patch.patch_id,
    )
    assert saved_patch == patch
    records, quick_report = mock_audit_service(tmp_path).quick_check(
        revised_bundle,
        source_blocks,
    )
    revision_version_id, _ = store.accept_patch(
        project_id="project-001",
        patch_id=patch.patch_id,
        bundle=revised_bundle,
        evidence=EvidenceSnapshot(evidence_records=records),
        report=quick_report,
        metadata=success_metadata(),
        usage=empty_usage(),
        started_at=NOW,
        ended_at=NOW,
    )

    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()
    revision_export = store.get_export_inputs("project-001")
    assert revision_export[-2:] == ("revision-source", "live")
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots

    restored_generation = store.restore_version(
        "project-001",
        version_id=generated_version_id,
        key_hash=sha256(b"restore-generation-source").hexdigest(),
        restored_at=NOW,
    )
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()
    generation_restore_export = store.get_export_inputs("project-001")
    assert generation_restore_export[1] == restored_generation.version_id
    assert generation_restore_export[-2:] == ("generation-source", "mock")
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots

    restored_revision = store.restore_version(
        "project-001",
        version_id=revision_version_id,
        key_hash=sha256(b"restore-revision-source").hexdigest(),
        restored_at=NOW,
    )
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()
    revision_restore_export = store.get_export_inputs("project-001")
    assert revision_restore_export[1] == restored_revision.version_id
    assert revision_restore_export[-2:] == ("revision-source", "live")
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots

    restored_twice = store.restore_version(
        "project-001",
        version_id=restored_revision.version_id,
        key_hash=sha256(b"restore-recursive-source").hexdigest(),
        restored_at=NOW,
    )
    counts = store.row_counts()
    snapshots = store.validate_all_snapshots()
    recursive_restore_export = store.get_export_inputs("project-001")
    assert recursive_restore_export[1] == restored_twice.version_id
    assert recursive_restore_export[-2:] == ("revision-source", "live")
    assert store.row_counts() == counts
    assert store.validate_all_snapshots() == snapshots


def test_export_inputs_reject_project_without_stable_version(tmp_path: Path) -> None:
    store = parsed_store(tmp_path)

    with pytest.raises(StoreError) as not_ready:
        store.get_export_inputs("project-001")

    assert not_ready.value.error_code == "PROJECT_NOT_READY"
    assert store.row_counts()["versions"] == 0
