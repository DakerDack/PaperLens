from __future__ import annotations

from dataclasses import dataclass
import errno
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
import unicodedata
from uuid import uuid4

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import PSEOF
from pdfplumber.utils.exceptions import PdfminerException

from backend.app.models import BlockType, ParserName, SourceBlock
from backend.app.settings import Settings, settings as app_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseQuality:
    page_count: int
    block_count: int
    empty_page_rate: float
    abnormal_character_rate: float
    page_number_completeness_rate: float
    bbox_availability_rate: float


@dataclass(frozen=True)
class ParseResult:
    blocks: list[SourceBlock]
    quality: ParseQuality


class DocumentParseError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool,
        exit_code: int | None = None,
        stderr_summary: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.exit_code = exit_code
        self.stderr_summary = stderr_summary


class _MinerUUnavailable(RuntimeError):
    pass


class DocumentService:
    def __init__(
        self,
        settings: Settings | None = None,
        temp_root: Path | None = None,
    ) -> None:
        self.settings = settings or app_settings
        self.temp_root = Path(temp_root) if temp_root is not None else None

    def parse(self, pdf_path: Path) -> ParseResult:
        try:
            return self.parse_with_mineru(pdf_path)
        except _MinerUUnavailable:
            return self.parse_with_pdfplumber(pdf_path)

    def parse_with_mineru(self, pdf_path: Path) -> ParseResult:
        pdf_path = Path(pdf_path)
        page_count = self._read_page_count(pdf_path)
        parser_version = self._read_mineru_version(pdf_path)

        temporary_directory = self._create_temporary_directory()
        primary_error: BaseException | None = None
        try:
            output_dir = temporary_directory / "output"
            args = [
                self.settings.mineru_command,
                "-p",
                str(pdf_path),
                "-o",
                str(output_dir),
                "-b",
                self.settings.mineru_backend,
                "-m",
                "txt",
            ]
            completed = self._run_cli(
                args,
                pdf_path=pdf_path,
                temporary_directory=temporary_directory,
            )
            if completed.returncode != 0:
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU could not parse the PDF.",
                    retryable=True,
                    exit_code=completed.returncode,
                    stderr_summary=self._sanitize_stderr(
                        completed.stderr,
                        sensitive_paths=(pdf_path, temporary_directory),
                    ),
                )

            try:
                content_files = list(output_dir.rglob("*_content_list.json"))
            except OSError as exc:
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU output files could not be enumerated.",
                    retryable=True,
                ) from exc
            if len(content_files) != 1:
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU did not produce exactly one content list.",
                    retryable=True,
                )
            try:
                raw_content = json.loads(content_files[0].read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU produced an unreadable content list.",
                    retryable=True,
                ) from exc

            blocks = self._adapt_mineru_content(
                raw_content,
                page_count=page_count,
                parser_version=parser_version,
            )
            quality = self._build_quality(page_count, blocks)
            if quality.block_count == 0:
                raise DocumentParseError(
                    "PARSE_QUALITY_LOW",
                    "The PDF contains no extractable text and OCR is not enabled.",
                    retryable=False,
                )
            return ParseResult(blocks=blocks, quality=quality)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._cleanup_temporary_directory(
                temporary_directory,
                preserve_primary_error=primary_error is not None,
            )

    def parse_with_pdfplumber(self, pdf_path: Path) -> ParseResult:
        blocks: list[SourceBlock] = []

        try:
            with pdfplumber.open(Path(pdf_path)) as pdf:
                page_count = len(pdf.pages)
                for page_index, page in enumerate(pdf.pages):
                    text = (page.extract_text() or "").strip()
                    if not text:
                        continue
                    blocks.append(
                        SourceBlock(
                            block_id=f"p{page_index + 1:02d}-b001",
                            page_index=page_index,
                            type=BlockType.TEXT,
                            text=text,
                            bbox=None,
                            reading_order=len(blocks),
                            parser=ParserName.PDFPLUMBER,
                            parser_version=version("pdfplumber"),
                        )
                    )
        except (
            FileNotFoundError,
            PermissionError,
            OSError,
            PDFSyntaxError,
            PdfminerException,
            PSEOF,
            ValueError,
        ) as exc:
            raise DocumentParseError(
                "PDF_INVALID",
                "The PDF is invalid or cannot be read.",
                retryable=False,
            ) from exc

        quality = self._build_quality(page_count, blocks)
        if quality.block_count == 0:
            raise DocumentParseError(
                "PARSE_QUALITY_LOW",
                "The PDF contains no extractable text and OCR is not enabled.",
                retryable=False,
            )
        return ParseResult(blocks=blocks, quality=quality)

    def _read_mineru_version(self, pdf_path: Path) -> str:
        completed = self._run_cli(
            [self.settings.mineru_command, "-v"],
            pdf_path=pdf_path,
            temporary_directory=None,
        )
        stdout = completed.stdout if isinstance(completed.stdout, str) else ""
        match = re.search(r"\bversion\s+([^\s,]+)", stdout)
        if completed.returncode != 0 or match is None:
            raise DocumentParseError(
                "PARSE_FAILED",
                "The MinerU CLI version could not be verified.",
                retryable=True,
                exit_code=completed.returncode,
                stderr_summary=self._sanitize_stderr(
                    completed.stderr,
                    sensitive_paths=(pdf_path,),
                ),
            )
        return match.group(1)

    def _run_cli(
        self,
        args: list[str],
        *,
        pdf_path: Path,
        temporary_directory: Path | None,
    ) -> subprocess.CompletedProcess[str]:
        run_kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": self.settings.mineru_timeout_seconds,
            "check": False,
            "shell": False,
        }
        if temporary_directory is not None:
            environment = os.environ.copy()
            environment["TEMP"] = str(temporary_directory)
            environment["TMP"] = str(temporary_directory)
            run_kwargs["env"] = environment
        try:
            return subprocess.run(args, **run_kwargs)
        except (FileNotFoundError, PermissionError) as exc:
            raise _MinerUUnavailable(str(exc)) from exc
        except OSError as exc:
            if exc.errno == errno.ENOEXEC or getattr(exc, "winerror", None) == 193:
                raise _MinerUUnavailable(str(exc)) from exc
            sensitive_paths = (pdf_path,)
            if temporary_directory is not None:
                sensitive_paths += (temporary_directory,)
            raise DocumentParseError(
                "PARSE_FAILED",
                "MinerU could not be started because of a system error.",
                retryable=True,
                stderr_summary=self._sanitize_stderr(
                    str(exc),
                    sensitive_paths=sensitive_paths,
                ),
            ) from exc
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode(encoding="utf-8", errors="replace")
            sensitive_paths = (pdf_path,)
            if temporary_directory is not None:
                sensitive_paths += (temporary_directory,)
            summary = self._sanitize_stderr(
                stderr if isinstance(stderr, str) else "",
                sensitive_paths=sensitive_paths,
            )
            if summary is None:
                summary = (
                    f"MinerU timed out after "
                    f"{self.settings.mineru_timeout_seconds} seconds."
                )
            raise DocumentParseError(
                "PARSE_FAILED",
                "MinerU timed out while parsing the PDF.",
                retryable=True,
                stderr_summary=summary,
            ) from exc

    @staticmethod
    def _sanitize_stderr(
        stderr: str | bytes | None,
        *,
        sensitive_paths: tuple[Path, ...],
    ) -> str | None:
        if isinstance(stderr, bytes):
            stderr = stderr.decode(encoding="utf-8", errors="replace")
        if not isinstance(stderr, str):
            return None
        summary = stderr
        for path in sensitive_paths:
            summary = summary.replace(str(path), "<path>")
        summary = re.sub(
            r"(?i)\b(authorization)\s*[:=]\s*Bearer\s+\S+",
            r"\1: Bearer <redacted>",
            summary,
        )
        summary = re.sub(
            r"(?i)\b(api[_-]?key|token)\s*[:=]\s*\S+",
            r"\1=<redacted>",
            summary,
        )
        summary = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", summary)
        summary = " ".join(summary.split())
        return summary[:500] if summary else None

    @staticmethod
    def _cleanup_temporary_directory(
        temporary_directory: Path,
        *,
        preserve_primary_error: bool,
    ) -> None:
        try:
            shutil.rmtree(temporary_directory)
        except OSError as exc:
            logger.warning(
                "MinerU temporary directory cleanup failed (%s).",
                type(exc).__name__,
            )
            if not preserve_primary_error:
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU parsed the PDF but temporary files could not be cleaned.",
                    retryable=True,
                ) from exc

    def _create_temporary_directory(self) -> Path:
        root = self.temp_root or Path(tempfile.gettempdir())
        temporary_directory = root / f"paperlens-mineru-{uuid4().hex}"
        try:
            temporary_directory.mkdir()
        except OSError as exc:
            raise DocumentParseError(
                "PARSE_FAILED",
                "MinerU temporary directory could not be created.",
                retryable=True,
            ) from exc
        return temporary_directory

    @staticmethod
    def _read_page_count(pdf_path: Path) -> int:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages)
        except (
            FileNotFoundError,
            PermissionError,
            OSError,
            PDFSyntaxError,
            PdfminerException,
            PSEOF,
            ValueError,
        ) as exc:
            raise DocumentParseError(
                "PDF_INVALID",
                "The PDF is invalid or cannot be read.",
                retryable=False,
            ) from exc

    @classmethod
    def _adapt_mineru_content(
        cls,
        raw_content: Any,
        *,
        page_count: int,
        parser_version: str,
    ) -> list[SourceBlock]:
        if not isinstance(raw_content, list):
            raise DocumentParseError(
                "PARSE_FAILED",
                "MinerU content must be a list.",
                retryable=True,
            )

        blocks: list[SourceBlock] = []
        page_block_counts: dict[int, int] = {}
        for item in raw_content:
            if not isinstance(item, dict):
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU content contains an invalid block.",
                    retryable=True,
                )
            page_index = item.get("page_idx")
            if type(page_index) is not int or not 0 <= page_index < page_count:
                raise DocumentParseError(
                    "PARSE_FAILED",
                    "MinerU content contains an invalid page index.",
                    retryable=True,
                )

            block_type, text = cls._mineru_text_and_type(item)
            text = text.strip()
            if not text:
                continue
            page_block_counts[page_index] = page_block_counts.get(page_index, 0) + 1
            blocks.append(
                SourceBlock(
                    block_id=(
                        f"p{page_index + 1:02d}-b{page_block_counts[page_index]:03d}"
                    ),
                    page_index=page_index,
                    type=block_type,
                    text=text,
                    bbox=cls._normalize_mineru_bbox(item.get("bbox")),
                    reading_order=len(blocks),
                    parser=ParserName.MINERU,
                    parser_version=parser_version,
                )
            )
        return blocks

    @staticmethod
    def _mineru_text_and_type(item: dict[str, Any]) -> tuple[BlockType, str]:
        raw_type = item.get("type")
        if raw_type in {"text", "title"}:
            block_type = (
                BlockType.TITLE
                if raw_type == "title" or item.get("text_level") is not None
                else BlockType.TEXT
            )
            return block_type, item.get("text") if isinstance(item.get("text"), str) else ""
        if raw_type in {"list", "ref_text", "index"}:
            return BlockType.TEXT, DocumentService._join_text_fields(
                item, "text", "list_items", "content"
            )
        if raw_type in {"code", "algorithm"}:
            prefix = "code" if raw_type == "code" else "algorithm"
            return BlockType.TEXT, DocumentService._join_text_fields(
                item,
                f"{prefix}_caption",
                f"{prefix}_body",
                f"{prefix}_content",
                f"{prefix}_footnote",
            )
        if raw_type == "equation":
            return BlockType.FORMULA, item.get("text") if isinstance(item.get("text"), str) else ""
        if raw_type == "table":
            return BlockType.TABLE, DocumentService._join_text_fields(
                item, "table_caption", "table_body", "table_footnote"
            )
        if raw_type == "image":
            return BlockType.IMAGE_CAPTION, DocumentService._join_text_fields(
                item, "image_caption", "content", "image_footnote"
            )
        if raw_type == "chart":
            return BlockType.IMAGE_CAPTION, DocumentService._join_text_fields(
                item, "chart_caption", "content", "chart_footnote"
            )
        if raw_type in {"page_footnote", "aside_text", "page_aside_text"}:
            return BlockType.TEXT, DocumentService._join_text_fields(
                item, "text", "content"
            )
        if raw_type in {
            "header",
            "footer",
            "page_number",
            "page_header",
            "page_footer",
        }:
            logger.warning("Ignoring MinerU auxiliary content type %r.", raw_type)
            return BlockType.TEXT, ""

        text = item.get("text") if isinstance(item.get("text"), str) else ""
        logger.warning(
            "MinerU content type %r is not explicitly supported; %s.",
            raw_type,
            "preserving its text field" if text.strip() else "ignoring the empty block",
        )
        return BlockType.TEXT, text

    @staticmethod
    def _join_text_fields(item: dict[str, Any], *field_names: str) -> str:
        parts: list[str] = []
        for field_name in field_names:
            value = item.get(field_name)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, list):
                parts.extend(
                    entry.strip()
                    for entry in value
                    if isinstance(entry, str) and entry.strip()
                )
        return "\n".join(parts)

    @staticmethod
    def _normalize_mineru_bbox(value: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
            return None

        coordinates = tuple(float(item) for item in value)
        if all(0 <= item <= 1 for item in coordinates):
            normalized = coordinates
        elif all(0 <= item <= 1000 for item in coordinates):
            normalized = tuple(item / 1000 for item in coordinates)
        else:
            return None
        x0, y0, x1, y1 = normalized
        if x0 >= x1 or y0 >= y1:
            return None
        return normalized

    @staticmethod
    def _build_quality(page_count: int, blocks: list[SourceBlock]) -> ParseQuality:
        text = "".join(block.text for block in blocks)
        abnormal_count = sum(
            character == "\ufffd"
            or (unicodedata.category(character) == "Cc" and not character.isspace())
            for character in text
        )
        covered_pages = {block.page_index for block in blocks}

        return ParseQuality(
            page_count=page_count,
            block_count=len(blocks),
            empty_page_rate=(page_count - len(covered_pages)) / page_count
            if page_count
            else 1.0,
            abnormal_character_rate=abnormal_count / len(text) if text else 0.0,
            page_number_completeness_rate=len(covered_pages) / page_count
            if page_count
            else 0.0,
            bbox_availability_rate=sum(block.bbox is not None for block in blocks)
            / len(blocks)
            if blocks
            else 0.0,
        )
