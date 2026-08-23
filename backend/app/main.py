from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import register_error_handlers, router


def create_app() -> FastAPI:
    app = FastAPI(
        title="PaperLens API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
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

