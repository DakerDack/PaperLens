from backend.app.api import AppError, health
from backend.app.main import create_app
from backend.app.models import ErrorResponse


def test_health_contract() -> None:
    response = health()

    assert response.model_dump() == {
        "status": "ok",
        "service": "paperlens-api",
        "version": "0.1.0",
    }


def test_openapi_contains_only_health_business_path() -> None:
    schema = create_app().openapi()

    assert set(schema["paths"]) == {"/api/health"}


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
