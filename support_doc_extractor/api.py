"""单文件解析业务入口。"""

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
    *,
    parsed_root: str | Path | None = None,
    write_files: bool = True,
) -> dict[str, Any]:
    """解析一个支持性文件并输出结构化结果。\n\n    Args:\n        doc_type: 文件类型枚举；兼容已支持的字符串别名。\n        file_path: 待解析的单个文件路径。\n\n    Returns:\n        包含业务结果、详细结果以及两个 JSON 输出路径的字典。\n\n    Raises:\n        FileNotFoundError: 输入文件不存在。\n        ValueError: 输入路径不是文件或文件类型不支持。\n        RuntimeError: 底层解析失败且无法降级。\n    """
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
        cache_root = Path(parsed_root).expanduser().resolve() if parsed_root is not None else None
        pipeline = SupportDocPipeline(
            parser_name=_default_parser_name(),
            parsed_root=cache_root,
        )
        details = pipeline.infer_with_type(path, normalized_type).to_dict()
        result = to_file_content(details)

        result_path: Path | None = None
        details_path: Path | None = None
        if write_files:
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
        result_path or "-",
        details_path or "-",
        int((time.perf_counter() - started_at) * 1000),
    )

    return {
        "result_path": str(result_path) if result_path is not None else None,
        "details_path": str(details_path) if details_path is not None else None,
        "result": result,
        "details": details,
    }


def _validate_input_file(path: Path) -> None:
    """校验输入路径必须是一个真实文件。\n\n    Args:\n        path: 待校验路径。\n\n    Raises:\n        FileNotFoundError: 路径不存在。\n        ValueError: 路径不是普通文件。\n    """
    if not path.exists():
        logger.error("task_failed file=%s reason=file_not_found", path)
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        logger.error("task_failed file=%s reason=not_single_file", path)
        raise ValueError(f"必须传入单个文件，不能传目录: {path}")


def _output_paths(path: Path) -> tuple[Path, Path]:
    """生成业务结果和详细结果文件路径。\n\n    Args:\n        path: 原始输入文件路径。\n\n    Returns:\n        业务 JSON 路径和详细 JSON 路径。\n    """
    return path.with_suffix(".json"), path.with_name(f"{path.stem}_details.json")


def _write_json(path: Path, payload: Any) -> None:
    """写入 UTF-8 JSON 文件。\n\n    Args:\n        path: 输出文件路径。\n        payload: 可 JSON 序列化的数据。\n    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _default_parser_name() -> str:
    """选择默认解析器。\n\n    Returns:\n        安装 OpenDataLoader 时返回 opendataloader_pdf，否则返回 pymupdf。\n    """
    try:
        import opendataloader_pdf  # noqa: F401
    except ImportError:
        return "pymupdf"
    return "opendataloader_pdf"
