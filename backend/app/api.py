from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app.models import ErrorResponse, HealthResponse


router = APIRouter(prefix="/api")


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


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="paperlens-api", version="0.1.0")

