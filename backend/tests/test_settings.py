import pytest
import os
from pathlib import Path
import subprocess
import sys
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


@pytest.mark.parametrize("module", ["settings", "main", "document_service", "hy3_service"])
def test_desktop_import_never_opens_dotenv_or_starts_services(tmp_path, module):
    # A synthetic file is present, but even opening it is forbidden in desktop mode.
    (tmp_path / ".env").write_text("HY3_API_KEY=synthetic-dotenv\n", encoding="utf-8")
    script = '''
import sys, os
from pathlib import Path
def guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        name = os.fsdecode(args[0])
        if Path(name).name == ".env":
            raise AssertionError("DOTENV_OPENED")
    if event in {"sqlite3.connect", "subprocess.Popen", "socket.connect", "socket.bind"}:
        raise AssertionError("IMPORT_SIDE_EFFECT")
sys.addaudithook(guard)
import importlib
importlib.import_module("backend.app." + sys.argv[1])
from backend.app.settings import Settings, settings
assert settings.hy3_api_key == ""
assert settings.paperlens_model_mode == "mock"
assert settings.max_pdf_mb == 30
s = Settings(_env_file=Path.cwd()/".env", hy3_api_key="synthetic-explicit")
assert s.hy3_api_key == "synthetic-explicit"
assert not (Path.cwd()/"data").exists()
print("DESKTOP_IMPORT_SAFE")
'''
    environment = {k: v for k, v in os.environ.items() if k.upper() in {
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH",
    }}
    environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]),
                       PAPERLENS_DESKTOP="1", HY3_API_KEY="synthetic-inherited",
                       PAPERLENS_MODEL_MODE="live", MAX_PDF_MB="99")
    result = subprocess.run([sys.executable, "-B", "-c", script, module],
                            cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "DESKTOP_IMPORT_SAFE" in result.stdout


def test_desktop_explicit_settings_keep_validation(monkeypatch):
    monkeypatch.setenv("PAPERLENS_DESKTOP", "1")
    monkeypatch.setenv("HY3_API_KEY", "synthetic-inherited")
    assert Settings(_env_file=None).hy3_api_key == ""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, paperlens_model_mode="invalid")


def test_browser_settings_keep_environment_and_synthetic_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERLENS_DESKTOP", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("HY3_API_KEY=synthetic-file\n", encoding="utf-8")
    monkeypatch.delenv("HY3_API_KEY", raising=False)
    assert Settings(_env_file=dotenv).hy3_api_key == "synthetic-file"
    monkeypatch.setenv("HY3_API_KEY", "synthetic-env")
    assert Settings(_env_file=dotenv).hy3_api_key == "synthetic-env"
    assert Settings(_env_file=dotenv, hy3_api_key="synthetic-init").hy3_api_key == "synthetic-init"
