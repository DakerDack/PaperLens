import errno
import json
from importlib.metadata import version
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import TypeAdapter

from backend.app.document_service import DocumentParseError, DocumentService
from backend.app.models import GeneratedBundle, SourceBlock
from backend.app.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"


def test_settings_default_to_project_local_mineru(monkeypatch) -> None:
    monkeypatch.delenv("MINERU_COMMAND", raising=False)
    project_root = FIXTURES.parents[2]
    expected = project_root / ".venv-mineru312" / "Scripts" / "mineru.exe"

    defaults = Settings(_env_file=None)
    relative_override = Settings(
        _env_file=None,
        mineru_command=".venv-mineru312/Scripts/mineru.exe",
    )

    assert Path(defaults.mineru_command) == expected
    assert Path(relative_override.mineru_command) == expected


def test_pdfplumber_maps_two_pages_and_reports_quality() -> None:
    result = DocumentService().parse_with_pdfplumber(FIXTURES / "simple_2page.pdf")

    assert [block.page_index for block in result.blocks] == [0, 1]
    assert "PaperLens fixture - page one" in result.blocks[0].text
    assert "PaperLens fixture - page two" in result.blocks[1].text
    assert [block.reading_order for block in result.blocks] == [0, 1]
    assert [block.block_id for block in result.blocks] == ["p01-b001", "p02-b001"]
    assert all(block.parser.value == "pdfplumber" for block in result.blocks)
    assert all(block.parser_version == version("pdfplumber") for block in result.blocks)
    assert all(block.bbox is None for block in result.blocks)

    assert result.quality.page_count == 2
    assert result.quality.block_count == 2
    assert result.quality.empty_page_rate == 0.0
    assert result.quality.abnormal_character_rate == 0.0
    assert result.quality.page_number_completeness_rate == 1.0
    assert result.quality.bbox_availability_rate == 0.0


def test_mineru_uses_argument_array_and_adapts_current_content_list(
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="mineru, version 3.4.5\n",
                stderr="",
            )

        output_dir = Path(args[args.index("-o") + 1])
        content_dir = output_dir / "simple_2page" / "txt"
        content_dir.mkdir(parents=True)
        (content_dir / "simple_2page_content_list.json").write_text(
            json.dumps(
                [
                    {
                        "type": "text",
                        "text": "PaperLens fixture - page one",
                        "text_level": 1,
                        "bbox": [100, 100, 900, 220],
                        "page_idx": 0,
                    },
                    {
                        "type": "text",
                        "text": "PaperLens fixture - page two",
                        "page_idx": 1,
                    },
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings(
        _env_file=None,
        mineru_command="mineru-test.exe",
        mineru_backend="pipeline",
        mineru_timeout_seconds=30,
    )
    result = DocumentService(
        settings=settings, temp_root=FIXTURES
    ).parse_with_mineru(FIXTURES / "simple_2page.pdf")

    parse_args, parse_kwargs = calls[1]
    assert parse_args[0] == "mineru-test.exe"
    assert parse_args[1:3] == ["-p", str(FIXTURES / "simple_2page.pdf")]
    assert parse_args[3] == "-o"
    assert parse_args[5:] == ["-b", "pipeline", "-m", "txt"]
    parse_environment = parse_kwargs.pop("env")
    assert isinstance(parse_environment, dict)
    assert parse_environment["TEMP"].startswith(str(FIXTURES / "paperlens-mineru-"))
    assert parse_environment["TMP"] == parse_environment["TEMP"]
    assert parse_kwargs == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 30,
        "check": False,
        "shell": False,
    }

    assert [block.page_index for block in result.blocks] == [0, 1]
    assert [block.reading_order for block in result.blocks] == [0, 1]
    assert [block.type.value for block in result.blocks] == ["title", "text"]
    assert result.blocks[0].bbox == (0.1, 0.1, 0.9, 0.22)
    assert result.blocks[1].bbox is None
    assert all(block.parser.value == "mineru" for block in result.blocks)
    assert all(block.parser_version == "3.4.5" for block in result.blocks)
    assert result.quality.page_count == 2
    assert result.quality.block_count == 2
    assert result.quality.bbox_availability_rate == 0.5
    assert not list(FIXTURES.glob("paperlens-mineru-*"))


def test_parse_falls_back_when_mineru_is_not_executable(monkeypatch) -> None:
    def missing_executable(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("mineru-test.exe was not found")

    monkeypatch.setattr(subprocess, "run", missing_executable)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    result = DocumentService(settings=settings, temp_root=FIXTURES).parse(
        FIXTURES / "simple_2page.pdf"
    )

    assert [block.parser.value for block in result.blocks] == [
        "pdfplumber",
        "pdfplumber",
    ]


def test_temporary_directory_creation_failure_returns_parse_failed(monkeypatch) -> None:
    missing_root = FIXTURES / "paperlens-missing-parent-for-test" / "nested"
    assert not missing_root.parent.exists()

    def version_only_run(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert args[-1] == "-v"
        return subprocess.CompletedProcess(
            args,
            returncode=0,
            stdout="mineru, version 3.4.5\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", version_only_run)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=missing_root).parse(
            FIXTURES / "simple_2page.pdf"
        )

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert exc_info.value.retryable is True
    assert not missing_root.exists()


def test_corrupt_pdf_returns_pdf_invalid() -> None:
    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(temp_root=FIXTURES).parse(FIXTURES / "corrupt.pdf")

    assert exc_info.value.error_code == "PDF_INVALID"
    assert exc_info.value.retryable is False


def test_image_only_pdf_returns_parse_quality_low_without_ocr(monkeypatch) -> None:
    def missing_executable(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("mineru-test.exe was not found")

    monkeypatch.setattr(subprocess, "run", missing_executable)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=FIXTURES).parse(
            FIXTURES / "scanned_1page.pdf"
        )

    assert exc_info.value.error_code == "PARSE_QUALITY_LOW"
    assert exc_info.value.retryable is False


def test_mineru_failure_is_sanitized_and_cleans_temp_directory(monkeypatch) -> None:
    pdf_path = FIXTURES / "simple_2page.pdf"

    def failed_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="mineru, version 3.4.5\n", stderr=""
            )
        output_dir = args[args.index("-o") + 1]
        return subprocess.CompletedProcess(
            args,
            returncode=7,
            stdout="",
            stderr=(
                f"token=secret-value PDF={pdf_path} output={output_dir} failed"
            ),
        )

    monkeypatch.setattr(subprocess, "run", failed_run)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=FIXTURES).parse(pdf_path)

    error = exc_info.value
    assert error.error_code == "PARSE_FAILED"
    assert error.retryable is True
    assert error.exit_code == 7
    assert error.stderr_summary is not None
    assert "secret-value" not in error.stderr_summary
    assert str(pdf_path) not in error.stderr_summary
    assert "paperlens-mineru-" not in error.stderr_summary
    assert "<redacted>" in error.stderr_summary
    assert not list(FIXTURES.glob("paperlens-mineru-*"))


def test_mineru_timeout_cleans_temp_directory(monkeypatch) -> None:
    def timeout_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="mineru, version 3.4.5\n", stderr=""
            )
        raise subprocess.TimeoutExpired(
            args,
            timeout=30,
            stderr="authorization: Bearer private-token",
        )

    monkeypatch.setattr(subprocess, "run", timeout_run)
    settings = Settings(
        _env_file=None,
        mineru_command="mineru-test.exe",
        mineru_timeout_seconds=30,
    )

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=FIXTURES).parse(
            FIXTURES / "simple_2page.pdf"
        )

    error = exc_info.value
    assert error.error_code == "PARSE_FAILED"
    assert error.exit_code is None
    assert error.stderr_summary is not None
    assert "private-token" not in error.stderr_summary
    assert "<redacted>" in error.stderr_summary
    assert not list(FIXTURES.glob("paperlens-mineru-*"))


def test_cli_output_is_decoded_as_utf8_instead_of_windows_default() -> None:
    settings = Settings(_env_file=None, mineru_timeout_seconds=30)
    service = DocumentService(settings=settings)
    utf8_output = (
        "import sys;"
        "sys.stdout.buffer.write("
        "b'\\xe8\\xa7\\xa3\\xe6\\x9e\\x90\\xe5\\xae\\x8c\\xe6\\x88\\x90'"
        ")"
    )

    completed = service._run_cli(
        [sys.executable, "-c", utf8_output],
        pdf_path=FIXTURES / "simple_2page.pdf",
        temporary_directory=None,
    )

    assert completed.stdout == "\u89e3\u6790\u5b8c\u6210"


def test_mineru_failure_allows_stderr_none(monkeypatch) -> None:
    def failed_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="mineru, version 3.4.5\n", stderr=None
            )
        return subprocess.CompletedProcess(
            args, returncode=7, stdout=None, stderr=None
        )

    monkeypatch.setattr(subprocess, "run", failed_run)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=FIXTURES).parse(
            FIXTURES / "simple_2page.pdf"
        )

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert exc_info.value.stderr_summary is None
    assert not list(FIXTURES.glob("paperlens-mineru-*"))


def test_non_executable_os_error_does_not_fall_back(monkeypatch) -> None:
    def out_of_memory(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOMEM, "not enough memory")

    monkeypatch.setattr(subprocess, "run", out_of_memory)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=FIXTURES).parse(
            FIXTURES / "simple_2page.pdf"
        )

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert exc_info.value.retryable is True


def test_cleanup_failure_preserves_original_parse_error(monkeypatch, caplog) -> None:
    def failed_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="mineru, version 3.4.5\n", stderr=""
            )
        return subprocess.CompletedProcess(
            args, returncode=9, stdout="", stderr="pipeline failed"
        )

    def cleanup_denied(path: Path) -> None:
        raise PermissionError(f"cannot remove {path}")

    monkeypatch.setattr(subprocess, "run", failed_run)
    monkeypatch.setattr(
        "backend.app.document_service.shutil.rmtree", cleanup_denied
    )
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")
    service = DocumentService(settings=settings, temp_root=FIXTURES)
    monkeypatch.setattr(
        service,
        "_create_temporary_directory",
        lambda: FIXTURES / "paperlens-cleanup-test",
    )

    with pytest.raises(DocumentParseError) as exc_info:
        service.parse(FIXTURES / "simple_2page.pdf")

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert exc_info.value.exit_code == 9
    assert "temporary directory cleanup failed" in caplog.text.lower()


def test_gbk_stderr_bytes_are_tolerated_with_utf8_replacement() -> None:
    summary = DocumentService._sanitize_stderr(
        bytes.fromhex("c4dab4e6b2bbd7e3"),
        sensitive_paths=(FIXTURES / "simple_2page.pdf",),
    )

    assert summary is not None
    assert "\ufffd" in summary


def test_cleanup_failure_after_success_becomes_parse_failed(monkeypatch, caplog) -> None:
    def cleanup_denied(path: Path) -> None:
        raise PermissionError(f"cannot remove {path}")

    monkeypatch.setattr(
        "backend.app.document_service.shutil.rmtree", cleanup_denied
    )

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService._cleanup_temporary_directory(
            FIXTURES / "paperlens-cleanup-success-test",
            preserve_primary_error=False,
        )

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert exc_info.value.retryable is True
    assert "temporary directory cleanup failed" in caplog.text.lower()


def test_mineru_maps_supported_academic_content_without_silent_loss(caplog) -> None:
    raw_content = [
        {
            "type": "list",
            "list_items": ["First reference", "Second reference"],
            "page_idx": 0,
        },
        {
            "type": "code",
            "code_caption": ["Algorithm 1"],
            "code_body": "print('ok')",
            "code_footnote": ["Synthetic code"],
            "page_idx": 0,
        },
        {"type": "ref_text", "text": "Reference entry", "page_idx": 0},
        {
            "type": "table",
            "table_caption": ["Table 1"],
            "table_body": "<table><tr><td>1</td></tr></table>",
            "page_idx": 0,
        },
        {"type": "equation", "text": "$$x=1$$", "page_idx": 0},
        {
            "type": "image",
            "image_caption": ["Figure 1"],
            "content": "Synthetic figure",
            "page_idx": 0,
        },
        {
            "type": "chart",
            "chart_caption": ["Chart 1"],
            "content": "Synthetic chart",
            "page_idx": 0,
        },
        {"type": "page_footnote", "text": "Footnote evidence", "page_idx": 0},
        {"type": "aside_text", "text": "Margin evidence", "page_idx": 0},
        {"type": "footer", "text": "Journal footer", "page_idx": 0},
        {"type": "page_number", "text": "1", "page_idx": 0},
        {"type": "future_type", "text": "Retained unknown text", "page_idx": 0},
    ]

    blocks = DocumentService._adapt_mineru_content(
        raw_content,
        page_count=1,
        parser_version="3.4.5",
    )

    assert [block.type.value for block in blocks] == [
        "text",
        "text",
        "text",
        "table",
        "formula",
        "image_caption",
        "image_caption",
        "text",
        "text",
        "text",
    ]
    assert [block.text for block in blocks] == [
        "First reference\nSecond reference",
        "Algorithm 1\nprint('ok')\nSynthetic code",
        "Reference entry",
        "Table 1\n<table><tr><td>1</td></tr></table>",
        "$$x=1$$",
        "Figure 1\nSynthetic figure",
        "Chart 1\nSynthetic chart",
        "Footnote evidence",
        "Margin evidence",
        "Retained unknown text",
    ]
    assert "footer" in caplog.text
    assert "page_number" in caplog.text
    assert "future_type" in caplog.text


def test_mineru_rejects_invalid_page_index() -> None:
    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService._adapt_mineru_content(
            [{"type": "text", "text": "outside", "page_idx": 2}],
            page_count=2,
            parser_version="3.4.5",
        )

    assert exc_info.value.error_code == "PARSE_FAILED"


@pytest.mark.parametrize("content_list_count", [0, 2])
def test_mineru_requires_exactly_one_legacy_content_list(
    monkeypatch,
    content_list_count: int,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="mineru, version 3.4.5\n", stderr=""
            )
        output_dir = Path(args[args.index("-o") + 1])
        for index in range(content_list_count):
            content_dir = output_dir / f"result-{index}"
            content_dir.mkdir(parents=True)
            (content_dir / f"result-{index}_content_list.json").write_text(
                "[]",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")

    with pytest.raises(DocumentParseError) as exc_info:
        DocumentService(settings=settings, temp_root=FIXTURES).parse_with_mineru(
            FIXTURES / "simple_2page.pdf"
        )

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert not list(FIXTURES.glob("paperlens-mineru-*"))


def test_content_list_enumeration_failure_returns_parse_failed_and_cleans(
    monkeypatch,
) -> None:
    original_rglob = Path.rglob

    def successful_run(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="mineru, version 3.4.5\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    def denied_rglob(path: Path, pattern: str):
        if pattern == "*_content_list.json":
            raise PermissionError("content list enumeration denied")
        return original_rglob(path, pattern)

    monkeypatch.setattr(subprocess, "run", successful_run)
    monkeypatch.setattr(Path, "rglob", denied_rglob)
    settings = Settings(_env_file=None, mineru_command="mineru-test.exe")
    service = DocumentService(settings=settings, temp_root=FIXTURES)

    def unexpected_fallback(pdf_path: Path):
        pytest.fail(f"unexpected pdfplumber fallback for {pdf_path}")

    monkeypatch.setattr(service, "parse_with_pdfplumber", unexpected_fallback)

    with pytest.raises(DocumentParseError) as exc_info:
        service.parse(FIXTURES / "simple_2page.pdf")

    assert exc_info.value.error_code == "PARSE_FAILED"
    assert exc_info.value.retryable is True
    assert not list(FIXTURES.glob("paperlens-mineru-*"))


def test_pdf_source_blocks_and_generation_fixture_share_one_evidence_chain() -> None:
    source_data = json.loads(
        (FIXTURES / "source_blocks.json").read_text(encoding="utf-8")
    )
    source_blocks = TypeAdapter(list[SourceBlock]).validate_python(source_data)
    generated = GeneratedBundle.model_validate_json(
        (FIXTURES / "generation_valid.json").read_text(encoding="utf-8")
    )
    pdf_result = DocumentService().parse_with_pdfplumber(
        FIXTURES / "simple_2page.pdf"
    )
    page_text = {block.page_index: block.text for block in pdf_result.blocks}

    assert [block.block_id for block in source_blocks] == [
        "p01-b001",
        "p01-b002",
        "p02-b001",
        "p02-b002",
    ]
    assert [block.reading_order for block in source_blocks] == [0, 1, 2, 3]
    assert all(block.text in page_text[block.page_index] for block in source_blocks)

    sources_by_id = {block.block_id: block for block in source_blocks}
    for claim in generated.claims:
        assert all(block_id in sources_by_id for block_id in claim.candidate_block_ids)
        if claim.candidate_quote is not None:
            assert any(
                claim.candidate_quote in sources_by_id[block_id].text
                for block_id in claim.candidate_block_ids
            )


def test_real_mineru_cli_matches_checked_in_source_blocks_when_enabled() -> None:
    if os.environ.get("PAPERLENS_RUN_MINERU_INTEGRATION") != "1":
        pytest.skip("set PAPERLENS_RUN_MINERU_INTEGRATION=1 to run real MinerU")

    command = os.environ.get("MINERU_COMMAND")
    assert command is not None and Path(command).is_file()
    settings = Settings(
        _env_file=None,
        mineru_command=command,
        mineru_backend="pipeline",
        mineru_timeout_seconds=300,
    )
    actual = DocumentService(settings=settings, temp_root=FIXTURES).parse_with_mineru(
        FIXTURES / "simple_2page.pdf"
    )
    expected_data = json.loads(
        (FIXTURES / "source_blocks.json").read_text(encoding="utf-8")
    )
    expected = TypeAdapter(list[SourceBlock]).validate_python(expected_data)

    assert [block.model_dump(mode="json") for block in actual.blocks] == [
        block.model_dump(mode="json") for block in expected
    ]
