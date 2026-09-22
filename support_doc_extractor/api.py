from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from support_doc_extractor.document_types import SupportDocType, normalize_doc_type
from support_doc_extractor.engine import SupportDocPipeline
from support_doc_extractor.logging_utils import get_logger
from support_doc_extractor.mappers import to_file_content


logger = get_logger("api")


def extract_document(
    doc_type: SupportDocType | str,
    file_path: str | Path,
) -> dict[str, Any]:
    """Parse one support document and return/write structured JSON results."""
    started_at = time.perf_counter()
    normalized_type = normalize_doc_type(doc_type)
    path = Path(file_path).expanduser().resolve()

    logger.info(
        "task_start file=%s requested_type=%s normalized_type=%s",
        path,
        doc_type,
        normalized_type,
    )

    _validate_input_file(path)

    try:
        pipeline = SupportDocPipeline(parser_name=_default_parser_name())
        details = pipeline.infer_with_type(path, normalized_type).to_dict()
        result = to_file_content(details)

        result_path, details_path = _output_paths(path)
        _write_json(result_path, result)
        _write_json(details_path, details)
    except Exception:
        logger.exception("task_failed file=%s doc_type=%s", path, normalized_type)
        raise

    logger.info(
        "task_done file=%s doc_type=%s result=%s details=%s elapsed_ms=%d",
        path,
        normalized_type,
        result_path,
        details_path,
        int((time.perf_counter() - started_at) * 1000),
    )

    return {
        "result_path": str(result_path),
        "details_path": str(details_path),
        "result": result,
        "details": details,
    }


def _validate_input_file(path: Path) -> None:
    """Validate that the caller supplied one existing file."""
    if not path.exists():
        logger.error("task_failed file=%s reason=file_not_found", path)
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        logger.error("task_failed file=%s reason=not_single_file", path)
        raise ValueError(f"必须传入单个文件，不能传目录: {path}")


def _output_paths(path: Path) -> tuple[Path, Path]:
    """Return business and detailed JSON output paths."""
    return path.with_suffix(".json"), path.with_name(f"{path.stem}_details.json")


def _write_json(path: Path, payload: Any) -> None:
    """Write UTF-8 JSON with readable Chinese text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _default_parser_name() -> str:
    """Use OpenDataLoader when installed, otherwise fall back to PyMuPDF."""
    try:
        import opendataloader_pdf  # noqa: F401
    except ImportError:
        return "pymupdf"
    return "opendataloader_pdf"
