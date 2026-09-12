"""Checks for the offline runner; all probes use synthetic paths and values."""
from pathlib import Path
import subprocess
import sys

import pytest


def test_verifier_environment_drops_inherited_credentials_and_proxies(monkeypatch):
    from tools.desktop_verify import clean_environment
    monkeypatch.setenv("HY3_API_KEY", "synthetic-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic.invalid")
    monkeypatch.setenv("NODE_OPTIONS", "--inspect")
    result = clean_environment()
    assert "HY3_API_KEY" not in result
    assert "HTTPS_PROXY" not in result
    assert "NODE_OPTIONS" not in result
    assert result["PAPERLENS_RUN_MINERU_INTEGRATION"] == "0"


@pytest.mark.parametrize("operation", ["dotenv", "network", "mineru", "old_data", "child_network"])
def test_verifier_guard_refuses_unsafe_operations(tmp_path, operation):
    from tools import desktop_verify
    script = '''
import sys, socket, subprocess
from pathlib import Path
from tools.desktop_verify import install_guard
root = Path(sys.argv[1])
install_guard(root)
try:
    if sys.argv[2] == "dotenv":
        Path(sys.argv[3]).read_text()
    elif sys.argv[2] == "network":
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)
    elif sys.argv[2] == "mineru":
        subprocess.run(["mineru", "-v"])
    elif sys.argv[2] == "child_network":
        child = subprocess.run([sys.executable, "-c",
            "import socket;socket.create_connection(('192.0.2.1',443),timeout=0.1)"],
            capture_output=True, text=True)
        assert child.returncode != 0
        assert "DESKTOP_VERIFY_NETWORK_BLOCKED" in child.stderr
        raise PermissionError("verified child guard")
    else:
        (Path.cwd()/"data"/"synthetic-never-open.db").read_bytes()
except PermissionError:
    print("BLOCKED")
else:
    raise AssertionError("guard did not refuse")
'''
    env = desktop_verify.clean_environment()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path), operation,
                             str(Path(__file__).resolve().parents[2]/".env")],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "BLOCKED" in result.stdout


def test_verifier_invalid_suite_refuses_before_business_import():
    from tools.desktop_verify import main
    with pytest.raises(SystemExit) as raised:
        main(["--suite", "unknown"])
    assert raised.value.code == 2


def test_frontend_verification_refuses_without_isolation(monkeypatch):
    from tools import desktop_verify
    def unavailable(*args):
        raise PermissionError("synthetic guard failure")
    monkeypatch.setattr(desktop_verify, "run_frontend", unavailable)
    assert desktop_verify.main(["--suite", "frontend"]) == 2


@pytest.mark.parametrize("script", [
    "Get-Content .env",
    "[System.IO.Directory]::GetAccessControl('D:\\Hy3'); Get-Content .env",
    "[System.IO.Directory]::GetAccessControl('{synthetic}'); Invoke-WebRequest https://example.invalid",
])
def test_acl_exception_never_allows_arbitrary_powershell(tmp_path, script):
    from tools.desktop_verify import allowed_acl_command
    script = script.replace("{synthetic}", str(tmp_path))
    assert not allowed_acl_command(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], tmp_path,
    )
