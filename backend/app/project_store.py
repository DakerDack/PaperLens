from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.app.models import (
    AuditReport,
    AuditStatus,
    ClaimsSnapshot,
    ContentDraft,
    DeepAuditResponse,
    EditPatch,
    EvidenceSnapshot,
    GeneratedBundle,
    ParseSnapshot,
    ProjectCreateResponse,
    ProjectStage,
    ProjectView,
    RunMetadata,
    RunMode,
    RunOperation,
    RunStatus,
    UsageSnapshot,
    UtcDatetime,
    VersionSummary,
)


_TABLES = ("projects", "versions", "audits", "patches", "runs")
_UTC_ADAPTER = TypeAdapter(UtcDatetime)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


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
            with self._transaction() as connection:
                connection.executescript(
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
                    );

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
                    );

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
                    );

                    CREATE TABLE IF NOT EXISTS patches (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        base_version_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        patch_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (project_id) REFERENCES projects(id),
                        FOREIGN KEY (base_version_id) REFERENCES versions(id)
                    );

                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        version_id TEXT,
                        operation TEXT NOT NULL CHECK (
                            operation IN (
                                'parse', 'generate', 'quick_check', 'deep_audit'
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
                    );
                    """
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
            if version_id is not None and project["current_version_id"] != version_id:
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

        return ProjectView(
            project_id=project_id,
            stage=project["stage"],
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
        normalized_project = " ".join(project_sql["sql"].split()) if project_sql else ""
        normalized_audit = " ".join(audit_sql["sql"].split()) if audit_sql else ""
        rights = "rights_confirmed INTEGER NOT NULL CHECK ( rights_confirmed IN (0, 1) )"
        evidence = "evidence_json TEXT NOT NULL"
        if rights not in normalized_project or evidence not in normalized_audit:
            raise self._corrupt_snapshot()
        return {
            "projects.rights_confirmed": (
                "INTEGER NOT NULL CHECK (rights_confirmed IN (0, 1))"
            ),
            "audits.evidence_json": "TEXT NOT NULL",
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
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
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
