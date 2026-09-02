from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.app.models import (
    AtomicClaim,
    AuditReport,
    AuditStatus,
    Auditability,
    ClaimsSnapshot,
    ContentDraft,
    DeepAuditResponse,
    EditPatch,
    EvidenceRecord,
    EvidenceSnapshot,
    GeneratedBundle,
    ParseSnapshot,
    PatchScope,
    PatchStatus,
    ProjectCreateResponse,
    ProjectStage,
    ProjectView,
    RunMetadata,
    RunMode,
    RunOperation,
    RunStatus,
    SourceBlock,
    UsageSnapshot,
    UtcDatetime,
    VersionSummary,
)


_TABLES = (
    "projects",
    "versions",
    "audits",
    "patches",
    "runs",
    "restore_idempotency",
)
_UTC_ADAPTER = TypeAdapter(UtcDatetime)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PATCH_PENDING_INDEX = "idx_patches_one_pending_per_project"
_PATCH_STATUS_CHECK = re.compile(
    r"\bstatus\s+text\s+not\s+null\s+check\s*\(\s*status\s+in\s*\(\s*"
    r"'pending'\s*,\s*'accepted'\s*,\s*'rejected'\s*\)\s*\)",
    re.IGNORECASE,
)
_RUN_OPERATION_CHECK = re.compile(
    r"\boperation\s+text\s+not\s+null\s+check\s*\(\s*operation\s+in\s*\(\s*"
    r"'parse'\s*,\s*'generate'\s*,\s*'quick_check'\s*,\s*'deep_audit'\s*,\s*"
    r"'revision'\s*\)\s*\)",
    re.IGNORECASE,
)


class StoreError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


class ProjectStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.projects_dir = self.data_dir / "projects"
        self.database_path = self.data_dir / "paperlens.db"

    def initialize(self) -> None:
        try:
            self.projects_dir.mkdir(parents=True, exist_ok=True)
            with self._transaction(immediate=True) as connection:
                patches_exists = self._table_exists(connection, "patches")
                patch_rows: list[tuple[object, ...]] = []
                pending_projects: set[str] = set()
                rebuild_patches = False
                create_pending_index = False
                if patches_exists:
                    patch_rows, pending_projects = self._validated_patch_rows(
                        connection
                    )
                    rebuild_patches = not self._patches_has_status_check(connection)
                    create_pending_index = not self._patches_has_pending_index(
                        connection
                    )

                runs_exists = self._table_exists(connection, "runs")
                run_rows: list[tuple[object, ...]] = []
                rebuild_runs = False
                if runs_exists:
                    run_rows = self._validated_run_rows(connection)
                    rebuild_runs = not self._runs_has_operation_check(connection)

                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        stage TEXT NOT NULL CHECK (
                            stage IN (
                                'created', 'parsed', 'generated',
                                'quick_checked', 'deep_audited',
                                'patch_pending', 'failed'
                            )
                        ),
                        pdf_path TEXT NOT NULL,
                        pdf_sha256 TEXT NOT NULL,
                        rights_confirmed INTEGER NOT NULL CHECK (
                            rights_confirmed IN (0, 1)
                        ),
                        parse_json TEXT,
                        current_version_id TEXT,
                        error_code TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS versions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        version_no INTEGER NOT NULL CHECK (version_no >= 1),
                        parent_version_id TEXT,
                        content_json TEXT NOT NULL,
                        claims_json TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE (project_id, version_no),
                        FOREIGN KEY (project_id) REFERENCES projects(id),
                        FOREIGN KEY (parent_version_id) REFERENCES versions(id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audits (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        version_id TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('quick_complete', 'deep_complete')
                        ),
                        evidence_json TEXT NOT NULL,
                        report_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (project_id) REFERENCES projects(id),
                        FOREIGN KEY (version_id) REFERENCES versions(id)
                    )
                    """
                )
                if rebuild_patches:
                    connection.execute(
                        "ALTER TABLE patches RENAME TO patches_legacy_migration"
                    )
                if not patches_exists or rebuild_patches:
                    self._create_patches_table(connection)
                if rebuild_patches:
                    connection.executemany(
                        """
                        INSERT INTO patches (
                            id, project_id, base_version_id,
                            status, patch_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        patch_rows,
                    )
                    connection.execute("DROP TABLE patches_legacy_migration")
                    create_pending_index = True
                if not patches_exists or create_pending_index:
                    self._create_pending_patch_index(connection)
                if rebuild_runs:
                    connection.execute(
                        "ALTER TABLE runs RENAME TO runs_legacy_migration"
                    )
                if not runs_exists or rebuild_runs:
                    self._create_runs_table(connection)
                if rebuild_runs:
                    connection.executemany(
                        """
                        INSERT INTO runs (
                            id, project_id, version_id, operation, mode, status,
                            metadata_json, usage_json, error_code,
                            started_at, ended_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        run_rows,
                    )
                    connection.execute("DROP TABLE runs_legacy_migration")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS restore_idempotency (
                        key_hash TEXT PRIMARY KEY NOT NULL CHECK (
                            length(key_hash) = 64
                            AND key_hash = lower(key_hash)
                            AND key_hash NOT GLOB '*[^0-9a-f]*'
                        ),
                        project_id TEXT NOT NULL,
                        target_version_id TEXT NOT NULL,
                        result_version_id TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (project_id) REFERENCES projects(id),
                        FOREIGN KEY (target_version_id) REFERENCES versions(id),
                        FOREIGN KEY (result_version_id) REFERENCES versions(id)
                    )
                    """
                )
                if rebuild_patches or create_pending_index:
                    for project_id in pending_projects:
                        connection.execute(
                            "UPDATE projects SET stage = ? WHERE id = ?",
                            (ProjectStage.PATCH_PENDING.value, project_id),
                        )
        except OSError as exc:
            raise StoreError(
                "SCHEMA_INVALID",
                "The PaperLens data directory could not be initialized.",
            ) from exc

    def project_pdf_path(self, project_id: str) -> Path:
        project_directory = (self.projects_dir / project_id).resolve()
        if project_directory.parent != self.projects_dir.resolve():
            raise StoreError(
                "PDF_NOT_FOUND",
                "The project PDF path is unavailable.",
            )
        return project_directory / "source.pdf"

    def create_project(
        self,
        *,
        project_id: str,
        pdf_sha256: str,
        rights_confirmed: bool,
        created_at: datetime | None = None,
    ) -> None:
        if rights_confirmed is not True:
            raise StoreError(
                "RIGHTS_NOT_CONFIRMED",
                "Document processing rights or permission are not confirmed.",
            )
        if not _SHA256.fullmatch(pdf_sha256):
            raise StoreError(
                "SCHEMA_INVALID",
                "The PDF digest is invalid.",
            )
        timestamp = self._timestamp(created_at)
        pdf_path = self.project_pdf_path(project_id)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, stage, pdf_path, pdf_sha256, rights_confirmed,
                    parse_json, current_version_id, error_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    project_id,
                    ProjectStage.CREATED.value,
                    str(pdf_path),
                    pdf_sha256,
                    1,
                    timestamp,
                    timestamp,
                ),
            )

    def save_parse_success(
        self,
        *,
        project_id: str,
        snapshot: ParseSnapshot,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
    ) -> ProjectCreateResponse:
        validated_snapshot = self._validated_model(ParseSnapshot, snapshot)
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        started, ended = self._run_timestamps(started_at, ended_at)
        with self._transaction() as connection:
            row = self._require_transition(
                connection,
                project_id,
                allowed={ProjectStage.CREATED},
                retry_target=ProjectStage.PARSED,
            )
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, parse_json = ?, error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    ProjectStage.PARSED.value,
                    validated_snapshot.model_dump_json(),
                    ended,
                    project_id,
                ),
            )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=None,
                operation=RunOperation.PARSE,
                mode=RunMode.LOCAL,
                status=RunStatus.SUCCEEDED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=None,
                started_at=started,
                ended_at=ended,
            )
        return ProjectCreateResponse(
            project_id=project_id,
            stage=ProjectStage.PARSED,
            parse_quality=validated_snapshot.quality,
            source_block_count=len(validated_snapshot.blocks),
            created_at=row["created_at"],
        )

    def get_parse_snapshot(self, project_id: str) -> ParseSnapshot:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT parse_json FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise StoreError(
                "PROJECT_NOT_FOUND",
                "The requested project does not exist.",
            )
        if row["parse_json"] is None:
            raise StoreError(
                "PROJECT_NOT_READY",
                "The project parse snapshot is not ready.",
            )
        return self._validated_json(ParseSnapshot, row["parse_json"])

    def get_generation_inputs(
        self,
        project_id: str,
    ) -> tuple[ParseSnapshot, GeneratedBundle | None, str | None]:
        with self._read_connection() as connection:
            try:
                project = self._require_transition(
                    connection,
                    project_id,
                    allowed={ProjectStage.GENERATED},
                    retry_target=ProjectStage.QUICK_CHECKED,
                )
            except StoreError as error:
                if error.error_code != "PROJECT_NOT_READY":
                    raise
                project = self._require_transition(
                    connection,
                    project_id,
                    allowed={ProjectStage.PARSED},
                    retry_target=ProjectStage.GENERATED,
                )
                bundle = None
                version_id = None
            else:
                version_id = project["current_version_id"]
                if not isinstance(version_id, str) or not version_id:
                    raise self._corrupt_snapshot()
                version = self._current_version(connection, project_id, version_id)
                if self._latest_audit(connection, project_id, version_id) is not None:
                    raise self._corrupt_snapshot()
                bundle = self._bundle_from_version(version)

            parse_snapshot = self._validated_json(
                ParseSnapshot,
                project["parse_json"],
            )
        return parse_snapshot, bundle, version_id

    def save_generated_version(
        self,
        *,
        project_id: str,
        bundle: GeneratedBundle,
        reason: str,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
        mode: RunMode | Literal["mock", "live"] = RunMode.MOCK,
    ) -> tuple[str, int]:
        validated_bundle = self._validated_model(GeneratedBundle, bundle)
        validated_content = self._validated_model(
            ContentDraft,
            validated_bundle.document,
        )
        validated_claims = self._validated_model(
            ClaimsSnapshot,
            ClaimsSnapshot(claims=validated_bundle.claims),
        )
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        run_mode = self._enum_value(RunMode, mode)
        if run_mode == RunMode.LOCAL:
            raise StoreError("SCHEMA_INVALID", "Generation mode must be Mock or Live.")
        if not reason.strip():
            raise StoreError("SCHEMA_INVALID", "Version reason is required.")
        started, ended = self._run_timestamps(started_at, ended_at)
        version_id = f"version-{uuid4().hex}"
        with self._transaction() as connection:
            self._require_transition(
                connection,
                project_id,
                allowed={ProjectStage.PARSED},
                retry_target=ProjectStage.GENERATED,
            )
            current = connection.execute(
                "SELECT MAX(version_no) AS value FROM versions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            version_no = int(current["value"] or 0) + 1
            connection.execute(
                """
                INSERT INTO versions (
                    id, project_id, version_no, parent_version_id,
                    content_json, claims_json, reason, created_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    project_id,
                    version_no,
                    validated_content.model_dump_json(),
                    validated_claims.model_dump_json(),
                    reason.strip(),
                    ended,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, current_version_id = ?, error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    ProjectStage.GENERATED.value,
                    version_id,
                    ended,
                    project_id,
                ),
            )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=version_id,
                operation=RunOperation.GENERATE,
                mode=run_mode,
                status=RunStatus.SUCCEEDED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=None,
                started_at=started,
                ended_at=ended,
            )
        return version_id, version_no

    def save_quick_check(
        self,
        *,
        project_id: str,
        version_id: str,
        evidence: EvidenceSnapshot,
        report: AuditReport,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
    ) -> None:
        validated_evidence = self._validated_model(EvidenceSnapshot, evidence)
        validated_report = self._validated_model(AuditReport, report)
        if validated_report.audit_status != AuditStatus.QUICK_COMPLETE:
            raise StoreError(
                "SCHEMA_INVALID",
                "Quick check requires a quick-complete audit report.",
            )
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        started, ended = self._run_timestamps(started_at, ended_at)
        with self._transaction() as connection:
            self._require_transition(
                connection,
                project_id,
                allowed={ProjectStage.GENERATED},
                retry_target=ProjectStage.QUICK_CHECKED,
            )
            version = self._current_version(connection, project_id, version_id)
            bundle = self._bundle_from_version(version)
            self._validate_evidence_coverage(bundle, validated_evidence)
            connection.execute(
                """
                INSERT INTO audits (
                    id, project_id, version_id, status,
                    evidence_json, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid4().hex}",
                    project_id,
                    version_id,
                    validated_report.audit_status.value,
                    validated_evidence.model_dump_json(),
                    validated_report.model_dump_json(),
                    ended,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (ProjectStage.QUICK_CHECKED.value, ended, project_id),
            )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=version_id,
                operation=RunOperation.QUICK_CHECK,
                mode=RunMode.LOCAL,
                status=RunStatus.SUCCEEDED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=None,
                started_at=started,
                ended_at=ended,
            )

    def get_deep_audit_inputs(
        self,
        project_id: str,
    ) -> tuple[GeneratedBundle, EvidenceSnapshot, bool, str]:
        with self._read_connection() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise StoreError(
                    "PROJECT_NOT_FOUND",
                    "The requested project does not exist.",
                )
            if project["current_version_id"] is None:
                raise StoreError(
                    "EVIDENCE_NOT_READY",
                    "Verified evidence is not ready for deep audit.",
                )
            self._require_transition(
                connection,
                project_id,
                allowed={ProjectStage.QUICK_CHECKED, ProjectStage.DEEP_AUDITED},
                retry_target=ProjectStage.DEEP_AUDITED,
            )
            version = self._current_version(
                connection,
                project_id,
                project["current_version_id"],
            )
            audit = self._latest_audit(
                connection,
                project_id,
                project["current_version_id"],
            )
        if audit is None:
            raise StoreError(
                "EVIDENCE_NOT_READY",
                "Verified evidence is not ready for deep audit.",
            )
        bundle = self._bundle_from_version(version)
        evidence = self._validated_json(EvidenceSnapshot, audit["evidence_json"])
        self._validated_json(AuditReport, audit["report_json"])
        self._validate_evidence_coverage(bundle, evidence)
        return (
            bundle,
            evidence,
            bool(project["rights_confirmed"]),
            project["current_version_id"],
        )

    def save_deep_audit(
        self,
        *,
        project_id: str,
        version_id: str,
        report: AuditReport,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
        mode: RunMode | Literal["mock", "live"] = RunMode.MOCK,
    ) -> DeepAuditResponse:
        validated_report = self._validated_model(AuditReport, report)
        if validated_report.audit_status != AuditStatus.DEEP_COMPLETE:
            raise StoreError(
                "SCHEMA_INVALID",
                "Deep audit requires a deep-complete audit report.",
            )
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        run_mode = self._enum_value(RunMode, mode)
        if run_mode == RunMode.LOCAL:
            raise StoreError("SCHEMA_INVALID", "Deep-audit mode must be Mock or Live.")
        started, ended = self._run_timestamps(started_at, ended_at)
        with self._transaction() as connection:
            self._require_transition(
                connection,
                project_id,
                allowed={ProjectStage.QUICK_CHECKED, ProjectStage.DEEP_AUDITED},
                retry_target=ProjectStage.DEEP_AUDITED,
            )
            self._current_version(connection, project_id, version_id)
            prior_audit = self._latest_audit(connection, project_id, version_id)
            if prior_audit is None:
                raise StoreError(
                    "EVIDENCE_NOT_READY",
                    "Verified evidence is not ready for deep audit.",
                )
            evidence = self._validated_json(
                EvidenceSnapshot,
                prior_audit["evidence_json"],
            )
            connection.execute(
                """
                INSERT INTO audits (
                    id, project_id, version_id, status,
                    evidence_json, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid4().hex}",
                    project_id,
                    version_id,
                    validated_report.audit_status.value,
                    evidence.model_dump_json(),
                    validated_report.model_dump_json(),
                    ended,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (ProjectStage.DEEP_AUDITED.value, ended, project_id),
            )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=version_id,
                operation=RunOperation.DEEP_AUDIT,
                mode=run_mode,
                status=RunStatus.SUCCEEDED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=None,
                started_at=started,
                ended_at=ended,
            )
        return DeepAuditResponse(
            project_id=project_id,
            version_id=version_id,
            stage=ProjectStage.DEEP_AUDITED,
            audit_report=validated_report,
        )

    def record_failure(
        self,
        *,
        project_id: str,
        version_id: str | None,
        operation: RunOperation | str,
        mode: RunMode | str,
        error_code: str,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
    ) -> None:
        run_operation = self._enum_value(RunOperation, operation)
        run_mode = self._enum_value(RunMode, mode)
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        if (
            not error_code.strip()
            or validated_metadata.message is None
            or validated_metadata.retryable_stage is None
        ):
            raise StoreError(
                "SCHEMA_INVALID",
                "Failed runs require a safe error code, message, and retry stage.",
            )
        started, ended = self._run_timestamps(started_at, ended_at)
        with self._transaction() as connection:
            project = self._project_row(connection, project_id)
            project_stage = ProjectStage(project["stage"])
            if run_operation == RunOperation.REVISION:
                if (
                    version_id is not None
                    or project_stage
                    not in {ProjectStage.QUICK_CHECKED, ProjectStage.DEEP_AUDITED}
                    or validated_metadata.retryable_stage != project_stage
                ):
                    raise StoreError(
                        "PROJECT_NOT_READY",
                        "The project is not ready to record a revision failure.",
                    )
            elif version_id is not None and project["current_version_id"] != version_id:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The requested version is not current.",
                )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=version_id,
                operation=run_operation,
                mode=run_mode,
                status=RunStatus.FAILED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=error_code.strip(),
                started_at=started,
                ended_at=ended,
            )
            if run_operation == RunOperation.REVISION:
                return
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, error_code = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ProjectStage.FAILED.value,
                    error_code.strip(),
                    ended,
                    project_id,
                ),
            )

    def get_sentence_revision_inputs(
        self,
        project_id: str,
        *,
        base_version_id: str,
        sentence_id: str,
    ) -> tuple[int, str, list[EvidenceRecord]]:
        with self._read_connection() as connection:
            version, bundle = self._revision_base(
                connection,
                project_id,
                base_version_id,
            )
            sentence_text = next(
                (
                    sentence.text
                    for section in bundle.document.sections
                    for sentence in section.sentences
                    if sentence.sentence_id == sentence_id
                ),
                None,
            )
            if sentence_text is None:
                raise StoreError(
                    "PATCH_INVALID",
                    "The requested sentence does not exist in the current version.",
                )
            audit = self._latest_audit(connection, project_id, base_version_id)
            evidence = EvidenceSnapshot(evidence_records=[])
            if audit is not None:
                evidence = self._validated_json(
                    EvidenceSnapshot,
                    audit["evidence_json"],
                )
                self._validated_json(AuditReport, audit["report_json"])
                self._validate_evidence_coverage(bundle, evidence)

        target_claim_ids = {
            claim.claim_id
            for claim in bundle.claims
            if claim.sentence_id == sentence_id
        }
        related_evidence = [
            record
            for record in evidence.evidence_records
            if record.claim_id in target_claim_ids and record.quote_verified
        ]
        return int(version["version_no"]), sentence_text, related_evidence

    def get_document_revision_inputs(
        self,
        project_id: str,
        *,
        base_version_id: str,
    ) -> tuple[int, ContentDraft]:
        with self._read_connection() as connection:
            version, bundle = self._revision_base(
                connection,
                project_id,
                base_version_id,
            )
        return int(version["version_no"]), bundle.document

    def save_patch_preview(
        self,
        *,
        project_id: str,
        base_version_id: str,
        patch: EditPatch,
        mode: RunMode | str,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
        created_at: datetime | None = None,
    ) -> EditPatch:
        validated_patch = self._validated_model(EditPatch, patch)
        run_mode = self._enum_value(RunMode, mode)
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        if (
            validated_metadata.message is not None
            or validated_metadata.retryable
            or validated_metadata.retryable_stage is not None
            or not validated_metadata.model
            or validated_metadata.prompt_version != "revision-v2"
            or validated_metadata.schema_version != "edit-patch-v1"
        ):
            raise StoreError(
                "SCHEMA_INVALID",
                "A successful revision run requires safe revision metadata.",
            )
        started, ended = self._run_timestamps(started_at, ended_at)
        created = self._timestamp(created_at)
        with self._transaction(immediate=True) as connection:
            version, bundle = self._revision_base(
                connection,
                project_id,
                base_version_id,
            )
            if connection.execute(
                """
                SELECT 1 FROM patches
                WHERE project_id = ? AND status = 'pending'
                LIMIT 1
                """,
                (project_id,),
            ).fetchone() is not None:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The project already has a pending revision patch.",
                )
            if validated_patch.base_version != int(version["version_no"]):
                raise StoreError(
                    "PATCH_INVALID",
                    "The revision patch version does not match its base version.",
                )
            if validated_patch.scope == PatchScope.SENTENCE:
                sentence_id = validated_patch.target_sentence_ids[0]
                before_text = next(
                    (
                        sentence.text
                        for section in bundle.document.sections
                        for sentence in section.sentences
                        if sentence.sentence_id == sentence_id
                    ),
                    None,
                )
                if before_text is None or any(
                    marker in validated_patch.after_text for marker in ("\n", "\r")
                ):
                    raise StoreError(
                        "PATCH_INVALID",
                        "The sentence revision patch exceeded its target scope.",
                    )
            else:
                before_text = bundle.document.model_dump_json()
                try:
                    revised_document = ContentDraft.model_validate_json(
                        validated_patch.after_text
                    )
                except (TypeError, ValueError, ValidationError) as exc:
                    raise StoreError(
                        "PATCH_INVALID",
                        "The document revision patch is not a valid ContentDraft.",
                    ) from exc
                self._validate_document_revision_identity(
                    bundle.document,
                    revised_document,
                )

            expected_hash = sha256(before_text.encode("utf-8")).hexdigest()
            if (
                validated_patch.before_text != before_text
                or validated_patch.before_hash != expected_hash
                or validated_patch.after_text == before_text
            ):
                raise StoreError(
                    "PATCH_INVALID",
                    "The revision patch does not match the current target.",
                )
            connection.execute(
                """
                INSERT INTO patches (
                    id, project_id, base_version_id, status, patch_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    validated_patch.patch_id,
                    project_id,
                    base_version_id,
                    PatchStatus.PENDING.value,
                    validated_patch.model_dump_json(),
                    created,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, updated_at = ?
                WHERE id = ?
                """,
                (ProjectStage.PATCH_PENDING.value, created, project_id),
            )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=None,
                operation=RunOperation.REVISION,
                mode=run_mode,
                status=RunStatus.SUCCEEDED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=None,
                started_at=started,
                ended_at=ended,
            )
        return validated_patch

    def reject_patch(self, project_id: str, *, patch_id: str) -> None:
        with self._transaction(immediate=True) as connection:
            patch_row = connection.execute(
                """
                SELECT * FROM patches
                WHERE id = ? AND project_id = ?
                """,
                (patch_id, project_id),
            ).fetchone()
            if patch_row is None:
                raise StoreError(
                    "PATCH_NOT_FOUND",
                    "The requested revision patch does not exist.",
                )

            status = self._enum_value(PatchStatus, patch_row["status"])
            if status == PatchStatus.REJECTED:
                return
            if status == PatchStatus.ACCEPTED:
                raise StoreError(
                    "PATCH_INVALID",
                    "An accepted revision patch cannot be rejected.",
                )

            patch = self._validated_json(EditPatch, patch_row["patch_json"])
            project = self._project_row(connection, project_id)
            if (
                ProjectStage(project["stage"]) != ProjectStage.PATCH_PENDING
                or project["current_version_id"] != patch_row["base_version_id"]
                or patch.patch_id != patch_row["id"]
            ):
                raise self._corrupt_snapshot()
            current_version_id = project["current_version_id"]
            if not isinstance(current_version_id, str):
                raise self._corrupt_snapshot()
            current_version = self._current_version(
                connection,
                project_id,
                current_version_id,
            )
            if patch.base_version != int(current_version["version_no"]):
                raise self._corrupt_snapshot()
            audit = self._latest_audit(
                connection,
                project_id,
                current_version_id,
            )
            if audit is None:
                raise self._corrupt_snapshot()
            report = self._validated_json(AuditReport, audit["report_json"])
            if audit["status"] != report.audit_status.value:
                raise self._corrupt_snapshot()
            restored_stage = (
                ProjectStage.DEEP_AUDITED
                if report.audit_status == AuditStatus.DEEP_COMPLETE
                else ProjectStage.QUICK_CHECKED
            )
            updated = connection.execute(
                """
                UPDATE patches
                SET status = ?
                WHERE id = ? AND project_id = ? AND status = ?
                """,
                (
                    PatchStatus.REJECTED.value,
                    patch_id,
                    project_id,
                    PatchStatus.PENDING.value,
                ),
            )
            if updated.rowcount != 1:
                raise self._corrupt_snapshot()
            connection.execute(
                "UPDATE projects SET stage = ? WHERE id = ?",
                (restored_stage.value, project_id),
            )

    def get_patch_acceptance_inputs(
        self,
        project_id: str,
        *,
        patch_id: str,
    ) -> tuple[EditPatch, GeneratedBundle, list[SourceBlock]]:
        with self._read_connection() as connection:
            project = self._project_row(connection, project_id)
            patch_row = connection.execute(
                """
                SELECT * FROM patches
                WHERE id = ? AND project_id = ?
                """,
                (patch_id, project_id),
            ).fetchone()
            if patch_row is None:
                raise StoreError(
                    "PATCH_NOT_FOUND",
                    "The requested revision patch does not exist.",
                )
            patch = self._validated_json(EditPatch, patch_row["patch_json"])
            _version, current_bundle = self._revision_base(
                connection,
                project_id,
                patch_row["base_version_id"],
                pending=True,
            )
            if patch_row["status"] != PatchStatus.PENDING.value:
                raise StoreError(
                    "PATCH_INVALID",
                    "The revision patch is no longer pending.",
                )
            revised_bundle = self._apply_patch_to_bundle(patch, current_bundle)
            parse_snapshot = self._validated_json(
                ParseSnapshot,
                project["parse_json"],
            )
        return patch, revised_bundle, parse_snapshot.blocks

    def get_sentence_claim_regeneration_inputs(
        self,
        project_id: str,
        *,
        patch_id: str,
    ) -> tuple[
        list[AtomicClaim],
        list[EvidenceRecord],
        set[str],
        set[str],
    ]:
        with self._read_connection() as connection:
            project = self._project_row(connection, project_id)
            patch_row = connection.execute(
                """
                SELECT * FROM patches
                WHERE id = ? AND project_id = ?
                """,
                (patch_id, project_id),
            ).fetchone()
            if patch_row is None:
                raise StoreError(
                    "PATCH_NOT_FOUND",
                    "The requested revision patch does not exist.",
                )
            patch = self._validated_json(EditPatch, patch_row["patch_json"])
            _version, current_bundle = self._revision_base(
                connection,
                project_id,
                patch_row["base_version_id"],
                pending=True,
            )
            if patch_row["status"] != PatchStatus.PENDING.value:
                raise StoreError(
                    "PATCH_INVALID",
                    "The revision patch is no longer pending.",
                )
            if patch.scope != PatchScope.SENTENCE:
                raise StoreError(
                    "PATCH_INVALID",
                    "The revision patch is not sentence scoped.",
                )
            parse_snapshot = self._validated_json(
                ParseSnapshot,
                project["parse_json"],
            )
            return self._sentence_claim_regeneration_context(
                connection,
                project_id=project_id,
                version_id=patch_row["base_version_id"],
                bundle=current_bundle,
                sentence_id=patch.target_sentence_ids[0],
                source_blocks=parse_snapshot.blocks,
            )

    @staticmethod
    def requires_sentence_claim_regeneration(patch: EditPatch) -> bool:
        return (
            patch.scope == PatchScope.SENTENCE
            and not ProjectStore._sentence_claim_reuse_is_safe(patch)
        )

    def build_sentence_revision_bundle(
        self,
        *,
        patch: EditPatch,
        provisional_bundle: GeneratedBundle,
        regenerated_claims: list[AtomicClaim],
    ) -> GeneratedBundle:
        validated_patch = self._validated_model(EditPatch, patch)
        validated_bundle = self._validated_model(
            GeneratedBundle,
            provisional_bundle,
        )
        validated_claims = self._validated_model(
            ClaimsSnapshot,
            ClaimsSnapshot(claims=regenerated_claims),
        ).claims
        if (
            validated_patch.scope != PatchScope.SENTENCE
            or not self.requires_sentence_claim_regeneration(validated_patch)
            or not validated_claims
        ):
            raise StoreError(
                "PATCH_INVALID",
                "The sentence revision claim replacement is invalid.",
            )
        sentence_id = validated_patch.target_sentence_ids[0]
        if any(
            claim.sentence_id != sentence_id
            for claim in validated_claims
        ):
            raise StoreError(
                "PATCH_INVALID",
                "The regenerated claims do not match the target sentence.",
            )
        claims = self._replace_sentence_claims(
            validated_bundle.claims,
            sentence_id=sentence_id,
            regenerated_claims=validated_claims,
        )
        try:
            return GeneratedBundle(
                document=validated_bundle.document,
                claims=claims,
            )
        except ValidationError as exc:
            raise StoreError(
                "PATCH_INVALID",
                "The regenerated claims do not form a valid document bundle.",
            ) from exc

    def accept_patch(
        self,
        *,
        project_id: str,
        patch_id: str,
        bundle: GeneratedBundle,
        evidence: EvidenceSnapshot,
        report: AuditReport,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[str, int]:
        validated_bundle = self._validated_model(GeneratedBundle, bundle)
        validated_evidence = self._validated_model(EvidenceSnapshot, evidence)
        validated_report = self._validated_model(AuditReport, report)
        if validated_report.audit_status != AuditStatus.QUICK_COMPLETE:
            raise StoreError(
                "SCHEMA_INVALID",
                "Accepted revisions require a quick-complete audit report.",
            )
        validated_metadata = self._validated_model(RunMetadata, metadata)
        validated_usage = self._validated_model(UsageSnapshot, usage)
        started, ended = self._run_timestamps(started_at, ended_at)
        new_version_id = f"version-{uuid4().hex}"

        with self._transaction(immediate=True) as connection:
            patch_row = connection.execute(
                """
                SELECT * FROM patches
                WHERE id = ? AND project_id = ?
                """,
                (patch_id, project_id),
            ).fetchone()
            if patch_row is None:
                raise StoreError(
                    "PATCH_NOT_FOUND",
                    "The requested revision patch does not exist.",
                )
            patch = self._validated_json(EditPatch, patch_row["patch_json"])
            base_version, current_bundle = self._revision_base(
                connection,
                project_id,
                patch_row["base_version_id"],
                pending=True,
            )
            if patch_row["status"] != PatchStatus.PENDING.value:
                raise StoreError(
                    "PATCH_INVALID",
                    "The revision patch is no longer pending.",
                )
            revision_run = connection.execute(
                """
                SELECT id FROM runs
                WHERE project_id = ?
                  AND operation = ?
                  AND status = ?
                  AND version_id IS NULL
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (
                    project_id,
                    RunOperation.REVISION.value,
                    RunStatus.SUCCEEDED.value,
                ),
            ).fetchone()
            if revision_run is None:
                raise self._corrupt_snapshot()
            expected_bundle = self._apply_patch_to_bundle(patch, current_bundle)
            if patch.scope == PatchScope.SENTENCE:
                if self._sentence_claim_reuse_is_safe(patch):
                    accepted_matches_patch = validated_bundle == expected_bundle
                else:
                    project = self._project_row(connection, project_id)
                    parse_snapshot = self._validated_json(
                        ParseSnapshot,
                        project["parse_json"],
                    )
                    (
                        original_target_claims,
                        related_evidence,
                        allowed_block_ids,
                        reserved_claim_ids,
                    ) = self._sentence_claim_regeneration_context(
                        connection,
                        project_id=project_id,
                        version_id=patch_row["base_version_id"],
                        bundle=current_bundle,
                        sentence_id=patch.target_sentence_ids[0],
                        source_blocks=parse_snapshot.blocks,
                    )
                    sentence_id = patch.target_sentence_ids[0]
                    regenerated_claims = [
                        claim
                        for claim in validated_bundle.claims
                        if claim.sentence_id == sentence_id
                    ]
                    auditable_required = bool(related_evidence) or any(
                        claim.auditability == Auditability.AUDITABLE
                        for claim in original_target_claims
                    )
                    replacement_invalid = (
                        not regenerated_claims
                        or any(
                            claim.claim_id in reserved_claim_ids
                            or not set(claim.candidate_block_ids).issubset(
                                allowed_block_ids
                            )
                            for claim in regenerated_claims
                        )
                        or (
                            auditable_required
                            and not any(
                                claim.auditability == Auditability.AUDITABLE
                                for claim in regenerated_claims
                            )
                        )
                    )
                    if replacement_invalid:
                        raise StoreError(
                            "PATCH_INVALID",
                            "The regenerated sentence claims are invalid.",
                        )
                    expected_rebuilt_bundle = (
                        self.build_sentence_revision_bundle(
                            patch=patch,
                            provisional_bundle=expected_bundle,
                            regenerated_claims=regenerated_claims,
                        )
                    )
                    accepted_matches_patch = (
                        validated_bundle == expected_rebuilt_bundle
                    )
            else:
                accepted_matches_patch = (
                    validated_bundle.document == expected_bundle.document
                )
            if not accepted_matches_patch:
                raise StoreError(
                    "PATCH_INVALID",
                    "The accepted revision does not match the pending patch.",
                )
            self._validate_evidence_coverage(validated_bundle, validated_evidence)
            current = connection.execute(
                "SELECT MAX(version_no) AS value FROM versions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            version_no = int(current["value"] or 0) + 1
            claims = ClaimsSnapshot(claims=validated_bundle.claims)
            connection.execute(
                """
                INSERT INTO versions (
                    id, project_id, version_no, parent_version_id,
                    content_json, claims_json, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_version_id,
                    project_id,
                    version_no,
                    base_version["id"],
                    validated_bundle.document.model_dump_json(),
                    claims.model_dump_json(),
                    f"accepted_patch:{patch.patch_id}",
                    ended,
                ),
            )
            connection.execute(
                """
                INSERT INTO audits (
                    id, project_id, version_id, status,
                    evidence_json, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid4().hex}",
                    project_id,
                    new_version_id,
                    validated_report.audit_status.value,
                    validated_evidence.model_dump_json(),
                    validated_report.model_dump_json(),
                    ended,
                ),
            )
            updated_patch = connection.execute(
                """
                UPDATE patches
                SET status = ?
                WHERE id = ? AND project_id = ? AND status = ?
                """,
                (
                    PatchStatus.ACCEPTED.value,
                    patch_id,
                    project_id,
                    PatchStatus.PENDING.value,
                ),
            )
            if updated_patch.rowcount != 1:
                raise self._corrupt_snapshot()
            updated_revision_run = connection.execute(
                """
                UPDATE runs
                SET version_id = ?
                WHERE id = ?
                  AND project_id = ?
                  AND operation = ?
                  AND status = ?
                  AND version_id IS NULL
                """,
                (
                    new_version_id,
                    revision_run["id"],
                    project_id,
                    RunOperation.REVISION.value,
                    RunStatus.SUCCEEDED.value,
                ),
            )
            if updated_revision_run.rowcount != 1:
                raise self._corrupt_snapshot()
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, current_version_id = ?, error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    ProjectStage.QUICK_CHECKED.value,
                    new_version_id,
                    ended,
                    project_id,
                ),
            )
            self._insert_run(
                connection,
                project_id=project_id,
                version_id=new_version_id,
                operation=RunOperation.QUICK_CHECK,
                mode=RunMode.LOCAL,
                status=RunStatus.SUCCEEDED,
                metadata=validated_metadata,
                usage=validated_usage,
                error_code=None,
                started_at=started,
                ended_at=ended,
            )
        return new_version_id, version_no

    def restore_version(
        self,
        project_id: str,
        *,
        version_id: str,
        key_hash: str,
        restored_at: datetime | None = None,
    ) -> VersionSummary:
        if not _SHA256.fullmatch(key_hash):
            raise StoreError(
                "SCHEMA_INVALID",
                "The restore idempotency digest is invalid.",
            )
        restored = self._timestamp(restored_at)
        with self._transaction(immediate=True) as connection:
            idempotency = connection.execute(
                """
                SELECT * FROM restore_idempotency
                WHERE key_hash = ?
                """,
                (key_hash,),
            ).fetchone()
            if idempotency is not None:
                if (
                    idempotency["project_id"] != project_id
                    or idempotency["target_version_id"] != version_id
                ):
                    raise StoreError(
                        "IDEMPOTENCY_CONFLICT",
                        "The idempotency key was already used for another restore target.",
                    )
                target_owner = connection.execute(
                    "SELECT project_id FROM versions WHERE id = ?",
                    (idempotency["target_version_id"],),
                ).fetchone()
                result = connection.execute(
                    "SELECT * FROM versions WHERE id = ?",
                    (idempotency["result_version_id"],),
                ).fetchone()
                if (
                    target_owner is None
                    or target_owner["project_id"] != project_id
                    or result is None
                    or result["project_id"] != project_id
                ):
                    raise self._corrupt_snapshot()
                return self._version_summary(result)

            project = self._project_row(connection, project_id)
            if ProjectStage(project["stage"]) == ProjectStage.PATCH_PENDING:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The project has a pending revision patch.",
                )
            current_version_id = project["current_version_id"]
            if not isinstance(current_version_id, str) or not current_version_id:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The project does not have a stable version to restore.",
                )
            self._current_version(connection, project_id, current_version_id)
            target = connection.execute(
                """
                SELECT * FROM versions
                WHERE id = ? AND project_id = ?
                """,
                (version_id, project_id),
            ).fetchone()
            if target is None:
                raise StoreError(
                    "VERSION_NOT_FOUND",
                    "The requested historical version does not exist.",
                )
            audit = self._latest_audit(connection, project_id, version_id)
            if audit is None:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The requested historical version is not stable.",
                )
            bundle = self._bundle_from_version(target)
            evidence = self._validated_json(
                EvidenceSnapshot,
                audit["evidence_json"],
            )
            report = self._validated_json(AuditReport, audit["report_json"])
            self._validate_evidence_coverage(bundle, evidence)
            new_version_id = f"version-{uuid4().hex}"
            current = connection.execute(
                "SELECT MAX(version_no) AS value FROM versions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            version_no = int(current["value"] or 0) + 1
            claims = ClaimsSnapshot(claims=bundle.claims)
            connection.execute(
                """
                INSERT INTO versions (
                    id, project_id, version_no, parent_version_id,
                    content_json, claims_json, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_version_id,
                    project_id,
                    version_no,
                    current_version_id,
                    bundle.document.model_dump_json(),
                    claims.model_dump_json(),
                    f"restore:{version_id}",
                    restored,
                ),
            )
            connection.execute(
                """
                INSERT INTO audits (
                    id, project_id, version_id, status,
                    evidence_json, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid4().hex}",
                    project_id,
                    new_version_id,
                    report.audit_status.value,
                    evidence.model_dump_json(),
                    report.model_dump_json(),
                    restored,
                ),
            )
            stage = (
                ProjectStage.DEEP_AUDITED
                if report.audit_status == AuditStatus.DEEP_COMPLETE
                else ProjectStage.QUICK_CHECKED
            )
            connection.execute(
                """
                UPDATE projects
                SET stage = ?, current_version_id = ?, error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (stage.value, new_version_id, restored, project_id),
            )
            connection.execute(
                """
                INSERT INTO restore_idempotency (
                    key_hash, project_id, target_version_id,
                    result_version_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    project_id,
                    version_id,
                    new_version_id,
                    restored,
                ),
            )
            result = connection.execute(
                "SELECT * FROM versions WHERE id = ? AND project_id = ?",
                (new_version_id, project_id),
            ).fetchone()
            if result is None:
                raise self._corrupt_snapshot()
            return self._version_summary(result)

    def get_export_inputs(
        self,
        project_id: str,
    ) -> tuple[ContentDraft, str, int, datetime, str, str, str]:
        with self._read_connection() as connection:
            project = self._project_row(connection, project_id)
            version_id = project["current_version_id"]
            if not isinstance(version_id, str) or not version_id:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The project does not have a stable version to export.",
                )
            version = self._current_version(connection, project_id, version_id)
            bundle = self._bundle_from_version(version)
            audit = self._latest_audit(connection, project_id, version_id)
            if audit is None:
                raise StoreError(
                    "PROJECT_NOT_READY",
                    "The current version has not completed a stable check.",
                )
            evidence = self._validated_json(
                EvidenceSnapshot,
                audit["evidence_json"],
            )
            self._validated_json(AuditReport, audit["report_json"])
            self._validate_evidence_coverage(bundle, evidence)
            try:
                created_at = _UTC_ADAPTER.validate_python(version["created_at"])
            except ValidationError as exc:
                raise self._corrupt_snapshot() from exc
            source_model, source_mode = self._export_source(
                connection,
                project_id=project_id,
                version_id=version_id,
            )
        return (
            bundle.document,
            version_id,
            int(version["version_no"]),
            created_at,
            project["pdf_sha256"],
            source_model,
            source_mode,
        )

    def _export_source(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        version_id: str,
    ) -> tuple[str, str]:
        visited: set[str] = set()
        source_version_id = version_id
        while True:
            if source_version_id in visited:
                raise self._corrupt_snapshot()
            visited.add(source_version_id)
            source_version = connection.execute(
                """
                SELECT id, project_id, parent_version_id, reason
                FROM versions
                WHERE id = ?
                """,
                (source_version_id,),
            ).fetchone()
            if source_version is None or source_version["project_id"] != project_id:
                raise self._corrupt_snapshot()
            reason = source_version["reason"]
            if not isinstance(reason, str):
                raise self._corrupt_snapshot()
            if reason.startswith("restore:"):
                target_version_id = reason.removeprefix("restore:")
                if not target_version_id:
                    raise self._corrupt_snapshot()
                source_version_id = target_version_id
                continue
            operation = (
                RunOperation.GENERATE
                if source_version["parent_version_id"] is None
                else RunOperation.REVISION
            )
            source_runs = connection.execute(
                """
                SELECT mode, metadata_json, error_code
                FROM runs
                WHERE project_id = ?
                  AND version_id = ?
                  AND operation = ?
                  AND status = ?
                ORDER BY rowid
                """,
                (
                    project_id,
                    source_version_id,
                    operation.value,
                    RunStatus.SUCCEEDED.value,
                ),
            ).fetchall()
            if len(source_runs) != 1:
                raise self._corrupt_snapshot()
            source_run = source_runs[0]
            mode = self._enum_value(RunMode, source_run["mode"])
            metadata = self._validated_json(
                RunMetadata,
                source_run["metadata_json"],
            )
            if (
                mode == RunMode.LOCAL
                or source_run["error_code"] is not None
                or metadata.message is not None
                or metadata.retryable
                or metadata.retryable_stage is not None
                or not isinstance(metadata.model, str)
                or not metadata.model.strip()
                or metadata.model != metadata.model.strip()
            ):
                raise self._corrupt_snapshot()
            return metadata.model, mode.value

    def get_project_view(
        self,
        project_id: str,
        *,
        model_mode: Literal["mock", "live"],
    ) -> ProjectView:
        with self._read_connection() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise StoreError(
                    "PROJECT_NOT_FOUND",
                    "The requested project does not exist.",
                )
            version_rows = connection.execute(
                """
                SELECT * FROM versions
                WHERE project_id = ?
                ORDER BY version_no ASC
                """,
                (project_id,),
            ).fetchall()
            latest_audit = None
            if project["current_version_id"] is not None:
                latest_audit = self._latest_audit(
                    connection,
                    project_id,
                    project["current_version_id"],
                )
            failed_run = None
            if project["stage"] == ProjectStage.FAILED.value:
                failed_run = connection.execute(
                    """
                    SELECT * FROM runs
                    WHERE project_id = ? AND status = 'failed'
                    ORDER BY
                        ended_at DESC,
                        CASE operation
                            WHEN 'parse' THEN 1
                            WHEN 'generate' THEN 2
                            WHEN 'quick_check' THEN 3
                            WHEN 'deep_audit' THEN 4
                        END DESC,
                        id DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
            pending_rows = connection.execute(
                """
                SELECT * FROM patches
                WHERE project_id = ? AND status = 'pending'
                ORDER BY created_at, id
                """,
                (project_id,),
            ).fetchall()

        parse_snapshot = None
        if project["parse_json"] is not None:
            parse_snapshot = self._validated_json(
                ParseSnapshot,
                project["parse_json"],
            )

        versions: list[VersionSummary] = []
        bundles: dict[str, GeneratedBundle] = {}
        for row in version_rows:
            bundles[row["id"]] = self._bundle_from_version(row)
            versions.append(
                VersionSummary(
                    version_id=row["id"],
                    version_no=row["version_no"],
                    parent_version_id=row["parent_version_id"],
                    reason=row["reason"],
                    created_at=row["created_at"],
                )
            )

        current_bundle = bundles.get(project["current_version_id"])
        if project["current_version_id"] is not None and current_bundle is None:
            raise self._corrupt_snapshot()

        evidence = EvidenceSnapshot(evidence_records=[])
        report = None
        if latest_audit is not None:
            evidence = self._validated_json(
                EvidenceSnapshot,
                latest_audit["evidence_json"],
            )
            report = self._validated_json(AuditReport, latest_audit["report_json"])
            if current_bundle is None:
                raise self._corrupt_snapshot()
            self._validate_evidence_coverage(current_bundle, evidence)

        error_code = project["error_code"]
        retryable_stage = None
        if project["stage"] == ProjectStage.FAILED.value:
            if error_code is None or failed_run is None:
                raise self._corrupt_snapshot()
            metadata = self._validated_json(
                RunMetadata,
                failed_run["metadata_json"],
            )
            self._validated_json(UsageSnapshot, failed_run["usage_json"])
            retryable_stage = metadata.retryable_stage
        elif error_code is not None:
            raise self._corrupt_snapshot()

        stage = self._enum_value(ProjectStage, project["stage"])
        if len(pending_rows) > 1:
            raise self._corrupt_snapshot()
        pending_patch = None
        if pending_rows:
            pending_row = pending_rows[0]
            try:
                pending_patch = EditPatch.model_validate_json(
                    pending_row["patch_json"]
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise self._corrupt_snapshot() from exc
            current_summary = next(
                (
                    version
                    for version in versions
                    if version.version_id == project["current_version_id"]
                ),
                None,
            )
            if (
                pending_patch.patch_id != pending_row["id"]
                or pending_row["base_version_id"] != project["current_version_id"]
                or current_summary is None
                or pending_patch.base_version != current_summary.version_no
            ):
                raise self._corrupt_snapshot()
        if (stage == ProjectStage.PATCH_PENDING) != (pending_patch is not None):
            raise self._corrupt_snapshot()

        return ProjectView(
            project_id=project_id,
            stage=stage,
            model_mode=model_mode,
            parse_quality=parse_snapshot.quality if parse_snapshot else None,
            source_block_count=len(parse_snapshot.blocks) if parse_snapshot else 0,
            current_version_id=project["current_version_id"],
            current_version_no=(
                next(
                    (
                        version.version_no
                        for version in versions
                        if version.version_id == project["current_version_id"]
                    ),
                    None,
                )
            ),
            document=current_bundle.document if current_bundle else None,
            claims=current_bundle.claims if current_bundle else [],
            evidence_records=evidence.evidence_records,
            audit_report=report,
            versions=versions,
            pending_patch=pending_patch,
            error_code=error_code,
            retryable_stage=retryable_stage,
            created_at=project["created_at"],
            updated_at=project["updated_at"],
        )

    def get_pdf_path(self, project_id: str) -> Path:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT pdf_path FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise StoreError("PDF_NOT_FOUND", "The project PDF was not found.")
        try:
            expected = self.project_pdf_path(project_id).resolve()
            stored = Path(row["pdf_path"]).resolve()
        except (OSError, StoreError) as exc:
            raise StoreError("PDF_NOT_FOUND", "The project PDF was not found.") from exc
        if stored != expected or not stored.is_file():
            raise StoreError("PDF_NOT_FOUND", "The project PDF was not found.")
        return stored

    def inspect_schema(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
            names = [row["name"] for row in rows]
            for name in names:
                if name not in _TABLES:
                    result[name] = ()
                    continue
                columns = connection.execute(
                    f'PRAGMA table_info("{name}")'
                ).fetchall()
                result[name] = tuple(row["name"] for row in columns)
        return result

    def inspect_constraints(self) -> dict[str, str]:
        with self._read_connection() as connection:
            project_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
            ).fetchone()
            audit_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'audits'"
            ).fetchone()
            patch_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patches'"
            ).fetchone()
            run_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
            ).fetchone()
            pending_index = self._patches_has_pending_index(connection)
        normalized_project = " ".join(project_sql["sql"].split()) if project_sql else ""
        normalized_audit = " ".join(audit_sql["sql"].split()) if audit_sql else ""
        rights = "rights_confirmed INTEGER NOT NULL CHECK ( rights_confirmed IN (0, 1) )"
        evidence = "evidence_json TEXT NOT NULL"
        has_patch_check = bool(
            patch_sql is not None
            and _PATCH_STATUS_CHECK.search(str(patch_sql["sql"]))
        )
        has_run_operation_check = bool(
            run_sql is not None
            and _RUN_OPERATION_CHECK.search(str(run_sql["sql"]))
        )
        if (
            rights not in normalized_project
            or evidence not in normalized_audit
            or not has_patch_check
            or not pending_index
            or not has_run_operation_check
        ):
            raise self._corrupt_snapshot()
        return {
            "projects.rights_confirmed": (
                "INTEGER NOT NULL CHECK (rights_confirmed IN (0, 1))"
            ),
            "audits.evidence_json": "TEXT NOT NULL",
            "patches.status": (
                "TEXT NOT NULL CHECK "
                "(status IN ('pending', 'accepted', 'rejected'))"
            ),
            "patches.one_pending_per_project": (
                "UNIQUE INDEX WHERE status = 'pending'"
            ),
            "runs.operation": (
                "TEXT NOT NULL CHECK (operation IN ('parse', 'generate', "
                "'quick_check', 'deep_audit', 'revision'))"
            ),
        }

    def row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._read_connection() as connection:
            for table in _TABLES:
                row = connection.execute(
                    f'SELECT COUNT(*) AS value FROM "{table}"'
                ).fetchone()
                counts[table] = int(row["value"])
        return counts

    def inspect_patch_status(self, project_id: str, patch_id: str) -> str:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT status FROM patches WHERE id = ? AND project_id = ?",
                (patch_id, project_id),
            ).fetchone()
        if row is None:
            raise StoreError(
                "PATCH_NOT_FOUND",
                "The requested revision patch does not exist.",
            )
        return str(row["status"])

    def validate_all_snapshots(self) -> dict[str, int]:
        counts = {
            "parse_json": 0,
            "content_json": 0,
            "claims_json": 0,
            "evidence_json": 0,
            "report_json": 0,
            "metadata_json": 0,
            "usage_json": 0,
            "patch_json": 0,
        }
        with self._read_connection() as connection:
            projects = connection.execute("SELECT parse_json FROM projects").fetchall()
            versions = connection.execute(
                "SELECT content_json, claims_json FROM versions"
            ).fetchall()
            audits = connection.execute(
                "SELECT evidence_json, report_json FROM audits"
            ).fetchall()
            runs = connection.execute(
                "SELECT metadata_json, usage_json FROM runs"
            ).fetchall()
            patches = connection.execute("SELECT patch_json FROM patches").fetchall()

        for row in projects:
            if row["parse_json"] is not None:
                self._validated_json(ParseSnapshot, row["parse_json"])
                counts["parse_json"] += 1
        for row in versions:
            content = self._validated_json(ContentDraft, row["content_json"])
            claims = self._validated_json(ClaimsSnapshot, row["claims_json"])
            self._validated_model(
                GeneratedBundle,
                GeneratedBundle(document=content, claims=claims.claims),
            )
            counts["content_json"] += 1
            counts["claims_json"] += 1
        for row in audits:
            self._validated_json(EvidenceSnapshot, row["evidence_json"])
            self._validated_json(AuditReport, row["report_json"])
            counts["evidence_json"] += 1
            counts["report_json"] += 1
        for row in runs:
            self._validated_json(RunMetadata, row["metadata_json"])
            self._validated_json(UsageSnapshot, row["usage_json"])
            counts["metadata_json"] += 1
            counts["usage_json"] += 1
        for row in patches:
            self._validated_json(EditPatch, row["patch_json"])
            counts["patch_json"] += 1
        return counts

    @contextmanager
    def _transaction(
        self,
        *,
        immediate: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
            else:
                with connection:
                    yield connection
        except sqlite3.Error as exc:
            raise StoreError(
                "SCHEMA_INVALID",
                "The project database operation failed.",
            ) from exc
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        except sqlite3.Error as exc:
            raise StoreError(
                "SCHEMA_INVALID",
                "The project database could not be read.",
            ) from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error as exc:
            raise StoreError(
                "SCHEMA_INVALID",
                "The project database is unavailable.",
            ) from exc

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone() is not None

    @staticmethod
    def _create_patches_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE patches (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                base_version_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'accepted', 'rejected')
                ),
                patch_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (base_version_id) REFERENCES versions(id)
            )
            """
        )

    @staticmethod
    def _create_pending_patch_index(connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE UNIQUE INDEX {_PATCH_PENDING_INDEX}
            ON patches(project_id)
            WHERE status = 'pending'
            """
        )

    @staticmethod
    def _create_runs_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version_id TEXT,
                operation TEXT NOT NULL CHECK (
                    operation IN (
                        'parse', 'generate', 'quick_check', 'deep_audit',
                        'revision'
                    )
                ),
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

    @staticmethod
    def _runs_has_operation_check(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()
        return bool(row is not None and _RUN_OPERATION_CHECK.search(str(row["sql"])))

    def _validated_run_rows(
        self,
        connection: sqlite3.Connection,
    ) -> list[tuple[object, ...]]:
        rows = connection.execute(
            """
            SELECT rowid AS source_rowid, *
            FROM runs
            ORDER BY source_rowid
            """
        ).fetchall()
        validated_rows: list[tuple[object, ...]] = []
        for row in rows:
            operation = self._enum_value(RunOperation, row["operation"])
            mode = self._enum_value(RunMode, row["mode"])
            status = self._enum_value(RunStatus, row["status"])
            metadata = self._validated_json(RunMetadata, row["metadata_json"])
            self._validated_json(UsageSnapshot, row["usage_json"])
            project = connection.execute(
                "SELECT id FROM projects WHERE id = ?",
                (row["project_id"],),
            ).fetchone()
            version = None
            if row["version_id"] is not None:
                version = connection.execute(
                    "SELECT project_id FROM versions WHERE id = ?",
                    (row["version_id"],),
                ).fetchone()
            try:
                started_at = _UTC_ADAPTER.validate_python(row["started_at"])
                ended_at = _UTC_ADAPTER.validate_python(row["ended_at"])
            except ValidationError as exc:
                raise self._corrupt_snapshot() from exc
            if (
                project is None
                or (version is not None and version["project_id"] != row["project_id"])
                or (row["version_id"] is not None and version is None)
                or ended_at < started_at
                or (status == RunStatus.SUCCEEDED and row["error_code"] is not None)
                or (
                    status == RunStatus.FAILED
                    and (
                        not isinstance(row["error_code"], str)
                        or not row["error_code"].strip()
                        or metadata.message is None
                        or metadata.retryable_stage is None
                    )
                )
            ):
                raise self._corrupt_snapshot()
            validated_rows.append(
                (
                    row["id"],
                    row["project_id"],
                    row["version_id"],
                    operation.value,
                    mode.value,
                    status.value,
                    row["metadata_json"],
                    row["usage_json"],
                    row["error_code"],
                    row["started_at"],
                    row["ended_at"],
                )
            )
        return validated_rows

    @staticmethod
    def _patches_has_status_check(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patches'"
        ).fetchone()
        return bool(row is not None and _PATCH_STATUS_CHECK.search(str(row["sql"])))

    @staticmethod
    def _patches_has_pending_index(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (_PATCH_PENDING_INDEX,),
        ).fetchone()
        if row is None:
            return False
        normalized = " ".join(str(row["sql"]).lower().split())
        expected = (
            f"create unique index {_PATCH_PENDING_INDEX} "
            "on patches(project_id) where status = 'pending'"
        )
        if normalized != expected:
            raise ProjectStore._corrupt_snapshot()
        return True

    def _validated_patch_rows(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[list[tuple[object, ...]], set[str]]:
        rows = connection.execute(
            """
            SELECT rowid AS source_rowid, *
            FROM patches
            ORDER BY source_rowid
            """
        ).fetchall()
        validated_rows: list[tuple[object, ...]] = []
        pending_projects: set[str] = set()
        for row in rows:
            status = self._enum_value(PatchStatus, row["status"])
            patch = self._validated_json(EditPatch, row["patch_json"])
            project = connection.execute(
                "SELECT id, current_version_id FROM projects WHERE id = ?",
                (row["project_id"],),
            ).fetchone()
            version = connection.execute(
                """
                SELECT project_id, version_no
                FROM versions
                WHERE id = ?
                """,
                (row["base_version_id"],),
            ).fetchone()
            if (
                project is None
                or version is None
                or version["project_id"] != row["project_id"]
                or patch.patch_id != row["id"]
                or patch.base_version != int(version["version_no"])
            ):
                raise self._corrupt_snapshot()
            if status == PatchStatus.PENDING:
                project_id = str(row["project_id"])
                if (
                    project_id in pending_projects
                    or project["current_version_id"] != row["base_version_id"]
                ):
                    raise self._corrupt_snapshot()
                pending_projects.add(project_id)
            validated_rows.append(
                (
                    row["id"],
                    row["project_id"],
                    row["base_version_id"],
                    status.value,
                    row["patch_json"],
                    row["created_at"],
                )
            )
        return validated_rows, pending_projects

    def _insert_run(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        version_id: str | None,
        operation: RunOperation,
        mode: RunMode,
        status: RunStatus,
        metadata: RunMetadata,
        usage: UsageSnapshot,
        error_code: str | None,
        started_at: str,
        ended_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO runs (
                id, project_id, version_id, operation, mode, status,
                metadata_json, usage_json, error_code, started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"run-{uuid4().hex}",
                project_id,
                version_id,
                operation.value,
                mode.value,
                status.value,
                metadata.model_dump_json(),
                usage.model_dump_json(),
                error_code,
                started_at,
                ended_at,
            ),
        )

    def _require_transition(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        *,
        allowed: set[ProjectStage],
        retry_target: ProjectStage,
    ) -> sqlite3.Row:
        project = self._project_row(connection, project_id)
        stage = ProjectStage(project["stage"])
        if stage in allowed:
            return project
        if stage == ProjectStage.FAILED:
            failed_run = connection.execute(
                """
                SELECT metadata_json FROM runs
                WHERE project_id = ? AND status = 'failed'
                ORDER BY
                    ended_at DESC,
                    CASE operation
                        WHEN 'parse' THEN 1
                        WHEN 'generate' THEN 2
                        WHEN 'quick_check' THEN 3
                        WHEN 'deep_audit' THEN 4
                    END DESC,
                    id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if failed_run is not None:
                metadata = self._validated_json(
                    RunMetadata,
                    failed_run["metadata_json"],
                )
                if metadata.retryable_stage == retry_target:
                    return project
        raise StoreError(
            "PROJECT_NOT_READY",
            "The project is not ready for this operation.",
        )

    @staticmethod
    def _project_row(
        connection: sqlite3.Connection,
        project_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise StoreError(
                "PROJECT_NOT_FOUND",
                "The requested project does not exist.",
            )
        return row

    @staticmethod
    def _current_version(
        connection: sqlite3.Connection,
        project_id: str,
        version_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT v.* FROM versions AS v
            JOIN projects AS p ON p.current_version_id = v.id
            WHERE p.id = ? AND v.id = ? AND v.project_id = p.id
            """,
            (project_id, version_id),
        ).fetchone()
        if row is None:
            raise StoreError(
                "PROJECT_NOT_READY",
                "The current generated version is not ready.",
            )
        return row

    def _revision_base(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        base_version_id: str,
        *,
        pending: bool = False,
    ) -> tuple[sqlite3.Row, GeneratedBundle]:
        project = self._project_row(connection, project_id)
        if project["current_version_id"] is None:
            raise StoreError(
                "PROJECT_NOT_READY",
                "The project does not have a stable version to revise.",
            )
        if project["current_version_id"] != base_version_id:
            raise StoreError(
                "TARGET_STALE",
                "The requested revision base is no longer current.",
            )
        allowed_stages = (
            {ProjectStage.PATCH_PENDING}
            if pending
            else {ProjectStage.QUICK_CHECKED, ProjectStage.DEEP_AUDITED}
        )
        if ProjectStage(project["stage"]) not in allowed_stages:
            raise StoreError(
                "PROJECT_NOT_READY",
                "The project is not ready for revision.",
            )
        version = self._current_version(connection, project_id, base_version_id)
        return version, self._bundle_from_version(version)

    def _apply_patch_to_bundle(
        self,
        patch: EditPatch,
        bundle: GeneratedBundle,
    ) -> GeneratedBundle:
        if patch.base_version < 1:
            raise StoreError("PATCH_INVALID", "The revision patch base is invalid.")
        if patch.scope == PatchScope.SENTENCE:
            sentence_id = patch.target_sentence_ids[0]
            before_text = next(
                (
                    sentence.text
                    for section in bundle.document.sections
                    for sentence in section.sentences
                    if sentence.sentence_id == sentence_id
                ),
                None,
            )
            if before_text is None or any(
                marker in patch.after_text for marker in ("\n", "\r")
            ):
                raise StoreError(
                    "PATCH_INVALID",
                    "The sentence revision patch exceeded its target scope.",
                )
            sections = [
                section.model_copy(
                    update={
                        "sentences": [
                            sentence.model_copy(update={"text": patch.after_text})
                            if sentence.sentence_id == sentence_id
                            else sentence
                            for sentence in section.sentences
                        ]
                    }
                )
                for section in bundle.document.sections
            ]
            document = bundle.document.model_copy(update={"sections": sections})
        else:
            before_text = bundle.document.model_dump_json()
            try:
                document = ContentDraft.model_validate_json(patch.after_text)
            except (TypeError, ValueError, ValidationError) as exc:
                raise StoreError(
                    "PATCH_INVALID",
                    "The document revision patch is not a valid ContentDraft.",
                ) from exc
            self._validate_document_revision_identity(bundle.document, document)

        if (
            patch.before_text != before_text
            or patch.before_hash != sha256(before_text.encode("utf-8")).hexdigest()
            or patch.after_text == before_text
        ):
            raise StoreError(
                "PATCH_INVALID",
                "The revision patch does not match the current target.",
            )
        try:
            return GeneratedBundle(document=document, claims=bundle.claims)
        except ValidationError as exc:
            raise StoreError(
                "PATCH_INVALID",
                "The revision patch produced invalid claim links.",
            ) from exc

    @staticmethod
    def _validate_document_revision_identity(
        current: ContentDraft,
        revised: ContentDraft,
    ) -> None:
        def identity(
            document: ContentDraft,
        ) -> tuple[tuple[str, tuple[str, ...]], ...]:
            return tuple(
                (
                    section.section_id.value,
                    tuple(sentence.sentence_id for sentence in section.sentences),
                )
                for section in document.sections
            )

        if identity(revised) != identity(current):
            raise StoreError(
                "PATCH_INVALID",
                "The document revision changed the sentence identity structure.",
            )

    def _sentence_claim_regeneration_context(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        version_id: str,
        bundle: GeneratedBundle,
        sentence_id: str,
        source_blocks: list[SourceBlock],
    ) -> tuple[
        list[AtomicClaim],
        list[EvidenceRecord],
        set[str],
        set[str],
    ]:
        original_claims = [
            claim
            for claim in bundle.claims
            if claim.sentence_id == sentence_id
        ]
        original_claim_ids = {claim.claim_id for claim in original_claims}
        reserved_claim_ids = {
            claim.claim_id
            for claim in bundle.claims
            if claim.sentence_id != sentence_id
        }
        audit = self._latest_audit(connection, project_id, version_id)
        evidence = EvidenceSnapshot(evidence_records=[])
        if audit is not None:
            evidence = self._validated_json(
                EvidenceSnapshot,
                audit["evidence_json"],
            )
            self._validated_json(AuditReport, audit["report_json"])
            self._validate_evidence_coverage(bundle, evidence)
        related_evidence = [
            record
            for record in evidence.evidence_records
            if record.claim_id in original_claim_ids
            and record.quote_verified
        ]
        source_block_ids = {block.block_id for block in source_blocks}
        candidate_block_ids = {
            block_id
            for claim in original_claims
            for block_id in claim.candidate_block_ids
        }
        verified_block_ids = {
            record.block_id
            for record in related_evidence
            if record.block_id is not None
        }
        allowed_block_ids = (
            candidate_block_ids | verified_block_ids
        ) & source_block_ids
        return (
            original_claims,
            related_evidence,
            allowed_block_ids,
            reserved_claim_ids,
        )

    @staticmethod
    def _replace_sentence_claims(
        claims: list[AtomicClaim],
        *,
        sentence_id: str,
        regenerated_claims: list[AtomicClaim],
    ) -> list[AtomicClaim]:
        merged: list[AtomicClaim] = []
        replacement_inserted = False
        for claim in claims:
            if claim.sentence_id == sentence_id:
                if not replacement_inserted:
                    merged.extend(regenerated_claims)
                    replacement_inserted = True
                continue
            merged.append(claim)
        if not replacement_inserted:
            merged.extend(regenerated_claims)
        return merged

    @staticmethod
    def _sentence_claim_reuse_is_safe(patch: EditPatch) -> bool:
        if not patch.after_text.startswith(patch.before_text):
            return False
        suffix = patch.after_text[len(patch.before_text) :]
        return bool(suffix) and all(character in {"!", "！"} for character in suffix)

    @staticmethod
    def _latest_audit(
        connection: sqlite3.Connection,
        project_id: str,
        version_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM audits
            WHERE project_id = ? AND version_id = ?
            ORDER BY
                created_at DESC,
                CASE status WHEN 'deep_complete' THEN 1 ELSE 0 END DESC,
                id DESC
            LIMIT 1
            """,
            (project_id, version_id),
        ).fetchone()

    def _bundle_from_version(self, row: sqlite3.Row) -> GeneratedBundle:
        content = self._validated_json(ContentDraft, row["content_json"])
        claims = self._validated_json(ClaimsSnapshot, row["claims_json"])
        return self._validated_model(
            GeneratedBundle,
            GeneratedBundle(document=content, claims=claims.claims),
        )

    @staticmethod
    def _version_summary(row: sqlite3.Row) -> VersionSummary:
        return VersionSummary(
            version_id=row["id"],
            version_no=row["version_no"],
            parent_version_id=row["parent_version_id"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _validate_evidence_coverage(
        bundle: GeneratedBundle,
        evidence: EvidenceSnapshot,
    ) -> None:
        claim_ids = {claim.claim_id for claim in bundle.claims}
        evidence_claim_ids = {record.claim_id for record in evidence.evidence_records}
        if evidence_claim_ids != claim_ids:
            raise StoreError(
                "SCHEMA_INVALID",
                "Evidence must cover the current generated claims exactly.",
            )

    @staticmethod
    def _enum_value(enum_type: type, value: object):
        try:
            return enum_type(value)
        except (TypeError, ValueError) as exc:
            raise StoreError(
                "SCHEMA_INVALID",
                "A stored enum value is invalid.",
            ) from exc

    @staticmethod
    def _validated_model(model_type: type, value: object):
        try:
            if not hasattr(value, "model_dump_json"):
                raise TypeError("value is not a Pydantic model")
            return model_type.model_validate_json(value.model_dump_json())
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise ProjectStore._corrupt_snapshot() from exc

    @staticmethod
    def _validated_json(model_type: type, value: object):
        if not isinstance(value, (str, bytes, bytearray)):
            raise ProjectStore._corrupt_snapshot()
        try:
            return model_type.model_validate_json(value)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ProjectStore._corrupt_snapshot() from exc

    @staticmethod
    def _timestamp(value: datetime | None) -> str:
        candidate = value or datetime.now(timezone.utc)
        try:
            validated = _UTC_ADAPTER.validate_python(candidate)
        except ValidationError as exc:
            raise ProjectStore._corrupt_snapshot() from exc
        return validated.isoformat().replace("+00:00", "Z")

    @classmethod
    def _run_timestamps(
        cls,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[str, str]:
        started = cls._timestamp(started_at)
        ended = cls._timestamp(ended_at)
        if ended_at < started_at:
            raise StoreError("SCHEMA_INVALID", "Run timestamps are out of order.")
        return started, ended

    @staticmethod
    def _corrupt_snapshot() -> StoreError:
        return StoreError(
            "SCHEMA_INVALID",
            "A stored PaperLens snapshot is invalid or inconsistent.",
        )
