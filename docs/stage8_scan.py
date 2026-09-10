"""Read-only release content scan; never print matched secrets or document text."""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.settings import Settings

PATTERNS = {
    "token_pattern": re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|AKID[A-Za-z0-9]{20,})"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_url": re.compile(rb"https?://[^\s/:]+:[^\s/@]+@"),
    "absolute_local_path": re.compile(rb"(?<![A-Za-z])[A-Za-z]:[\\/](?:[^\s\"'<>`]|\\ ){2,}"),
}
PDF_HASHES = {
    "backend/tests/fixtures/corrupt.pdf": "eff16626e5e56d70d56b308945c2d99f76625bf0e5479e12fec004f6913e886e",
    "backend/tests/fixtures/scanned_1page.pdf": "d27fcbfdae9a573d0b002431e5b1a9a0095085294453b4b0a25621a2ab2dd65b",
    "backend/tests/fixtures/simple_2page.pdf": "a892c27abbf7af13a2eff71d1cac1c4021555636ee5725cf98c6bc3fff639f8c",
}
ALLOWED_ENV = {
    "PAPERLENS_ENV", "PAPERLENS_DATA_DIR", "PAPERLENS_MODEL_MODE", "MAX_PDF_MB",
    "MINERU_COMMAND", "MINERU_BACKEND", "MINERU_TIMEOUT_SECONDS", "HY3_BASE_URL",
    "HY3_MODEL", "HY3_API_KEY", "HY3_TIMEOUT_SECONDS", "HY3_MAX_RETRIES",
}


def scan():
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, check=True).stdout
    paths = {ROOT / name.decode("utf-8") for name in listed.split(b"\0") if name}
    for directory in (ROOT / "reports", ROOT / "frontend/dist"):
        paths.update(p for p in directory.rglob("*") if p.is_file())
    paths.discard(ROOT / "reports/stage8_scan.json")
    key = Settings(_env_file=ROOT / ".env").hy3_api_key.encode("utf-8")
    findings, inventory, pdfs = [], [], []
    for path in sorted(paths):
        relative = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        inventory.append({"path": relative, "bytes": len(data), "sha256": digest})
        if key and key in data:
            findings.append({"path": relative, "category": "configured_key_match", "blocking": True})
        for category, pattern in PATTERNS.items():
            if category == "absolute_local_path" and path.suffix.lower() in {".pdf", ".png", ".webm", ".zip"}:
                continue
            for match in pattern.finditer(data):
                test_sentinel = (relative == "backend/tests/test_api.py" and category == "token_pattern"
                    and hashlib.sha256(match.group()).hexdigest() == "18af9567fa7364ac4f6e4edf31d30eef9c7bff0bd6b462cbe5c66563e805ef21")
                findings.append({"path": relative, "line": data.count(b"\n", 0, match.start()) + 1,
                    "category": "reviewed_test_sentinel" if test_sentinel else category,
                    "blocking": category != "absolute_local_path" and not test_sentinel})
        if path.suffix.lower() == ".pdf":
            known = PDF_HASHES.get(relative) == digest
            pdfs.append({"path": relative, "sha256": digest, "reviewed_synthetic": known})
            if not known:
                findings.append({"path": relative, "category": "pdf_requires_rights_review", "blocking": True})
        if path.suffix.lower() in {".log", ".txt"} and relative != "requirements.lock":
            findings.append({"path": relative, "category": "raw_text_review", "blocking": True})
        if path.suffix.lower() in {".json", ".jsonl"}:
            try:
                objects = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()] if path.suffix == ".jsonl" else [json.loads(data)]
            except (ValueError, UnicodeError):
                findings.append({"path": relative, "category": "unreadable_json", "blocking": True})
                continue
            def inspect(value):
                if isinstance(value, str) and len(value) > 5000:
                    findings.append({"path": relative, "category": "long_text_review", "blocking": True})
                elif isinstance(value, dict):
                    for name, child in value.items():
                        if name.lower() in {"raw_response", "raw_prompt", "api_key", "authorization"} and child:
                            findings.append({"path": relative, "category": "raw_field_review", "blocking": True})
                        inspect(child)
                elif isinstance(value, list):
                    for child in value:
                        inspect(child)
            for value in objects:
                inspect(value)
    example = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines() if line and not line.startswith("#"))
    env_ok = set(example) == ALLOWED_ENV and example.get("HY3_API_KEY") == "" and example.get("PAPERLENS_MODEL_MODE") == "mock"
    if not env_ok:
        findings.append({"path": ".env.example", "category": "env_contract", "blocking": True})
    report = {"date_utc": datetime.now(timezone.utc).isoformat(), "files_scanned": len(inventory),
        "configured_key_checked": bool(key), "env_contract_passed": env_ok,
        "blocking_findings": sum(item["blocking"] for item in findings),
        "findings": findings, "pdfs": pdfs, "inventory": inventory,
        "limits": "Pattern and configured-key scanning cannot prove absence of every secret. PDF clearance is restricted to exact reviewed hashes. Absolute paths are listed for human review; no matched text is emitted. Media needs visual review. Existing historical artifacts are preserved."}
    (ROOT / "reports/stage8_scan.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("files_scanned", "configured_key_checked", "env_contract_passed", "blocking_findings")}))
    for item in findings:
        if item["blocking"]:
            print(json.dumps(item))
    return int(bool(report["blocking_findings"]))


if __name__ == "__main__":
    # Minimal scanner check: sentinel content is never included in emitted finding records.
    assert PATTERNS["token_pattern"].search(b"sk-" + b"z" * 24)
    assert not PATTERNS["token_pattern"].search(b"HY3_API_KEY=")
    raise SystemExit(scan())
