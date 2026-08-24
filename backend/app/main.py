from collections.abc import Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.audit_service import AuditService
from backend.app.api import register_error_handlers, router
from backend.app.document_service import DocumentService
from backend.app.hy3_service import Hy3Service
from backend.app.project_store import ProjectStore
from backend.app.settings import Settings, settings as app_settings


def create_app(
    *,
    settings_override: Settings | None = None,
    project_store: ProjectStore | None = None,
    document_service: DocumentService | None = None,
    hy3_service: Hy3Service | None = None,
    audit_service: AuditService | None = None,
    project_id_factory: Callable[[], str] | None = None,
) -> FastAPI:
    current_settings = settings_override or app_settings
    current_store = project_store or ProjectStore(current_settings.paperlens_data_dir)
    current_document_service = document_service or DocumentService(current_settings)
    current_hy3_service = hy3_service or Hy3Service(current_settings)
    current_audit_service = audit_service or AuditService(current_hy3_service)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        current_store.initialize()
        yield

    app = FastAPI(
        title="PaperLens API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = current_settings
    app.state.project_store = current_store
    app.state.document_service = current_document_service
    app.state.hy3_service = current_hy3_service
    app.state.audit_service = current_audit_service
    app.state.project_id_factory = project_id_factory or (lambda: uuid4().hex)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
