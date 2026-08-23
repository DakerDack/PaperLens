import pytest
from pydantic import ValidationError

from backend.app.settings import Settings


def test_settings_resolve_data_directory() -> None:
    settings = Settings(_env_file=None, paperlens_data_dir="./data-test")

    assert settings.paperlens_data_dir.is_absolute()
    assert settings.paperlens_data_dir.name == "data-test"
    assert settings.paperlens_model_mode == "mock"
    assert settings.hy3_max_retries == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [("paperlens_model_mode", "automatic"), ("hy3_max_retries", 3)],
)
def test_settings_reject_unsupported_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
