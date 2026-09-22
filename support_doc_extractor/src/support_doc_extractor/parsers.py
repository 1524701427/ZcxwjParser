from __future__ import annotations

import hashlib
import inspect
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from support_doc_extractor.models import Block, Document, Page, Table
from support_doc_extractor.logging_utils import get_logger

logger = get_logger("parsers")

# ==== 解析器基础 ====

class Parser(ABC):
    """Base interface for document parsers."""

    name: str

    @abstractmethod
    def parse(self, path: Path) -> Document:
        """Parse a file into the unified document model."""
        raise NotImplementedError


# ==== PyMuPDF 解析 ====

def clean_text(text: str) -> str:
    """Collapse PDF text whitespace into a single readable line."""
    return re.sub(r"\s+", " ", text or "").strip()


class PyMuPDFParser(Parser):
    """Lightweight fallback parser backed by PyMuPDF text extraction."""

    name = "pymupdf"

    def parse(self, path: Path) -> Document:
        """Parse a PDF with PyMuPDF.

        Args:
            path: PDF file path.

        Returns:
            Unified document containing page-level text blocks.
        """
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is required for fallback parsing. Install pymupdf.") from exc

        logger.debug("pymupdf_parse_start file=%s", path)
        pages: list[Page] = []
        with fitz.open(path) as doc:
            for page_index, page in enumerate(doc, 1):
                text = clean_text(page.get_text("text") or "")
                blocks = [Block(text=text, type="page_text", page_no=page_index)] if text else []
                pages.append(
                    Page(
                        page_no=page_index,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        blocks=blocks,
                        text=text,
                    )
                )
        document = Document(file=path, pages=pages, parser=self.name)
        document.rebuild_text()
        logger.info(
            "pymupdf_parse_done file=%s pages=%d text_chars=%d",
            path,
            len(document.pages),
            len(document.full_text or ""),
        )
        return document


# ==== OpenDataLoader 解析 ====

class OpenDataLoaderParser(Parser):
    """Adapter for OpenDataLoader outputs with per-source isolated caches."""

    name = "opendataloader_pdf"

    def __init__(self, output_root: Path | None = None, options: dict[str, Any] | None = None) -> None:
        self.output_root = output_root
        self.options = options or {}

    def parse(self, path: Path) -> Document:
        json_path = self._resolve_or_run(path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return document_from_opendataloader_json(path, data, parser=self.name, source_root=json_path.parent)

    def _cache_dir(self, path: Path) -> Path:
        if self.output_root is None:
            raise RuntimeError("OpenDataLoaderParser requires output_root.")
        resolved = path.expanduser().resolve()
        try:
            stat = resolved.stat()
            signature = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            signature = str(resolved)
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        safe_stem = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", path.stem).strip("._") or "document"
        return self.output_root / f"{safe_stem}_{digest}"

    def _resolve_or_run(self, path: Path) -> Path:
        if self.output_root is None:
            raise RuntimeError("OpenDataLoaderParser requires output_root.")
        if self.options.get("refresh"):
            self._cleanup_existing_output(path)
        else:
            existing = self._find_output_json(path)
            if existing is not None:
                logger.debug("parser_cache_hit file=%s cache=%s", path, existing)
                return existing
        try:
            import opendataloader_pdf
        except ImportError as exc:
            raise RuntimeError("opendataloader_pdf is not installed.") from exc
        cache_dir = self._cache_dir(path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("opendataloader_convert_start file=%s cache_dir=%s", path, cache_dir)
        opendataloader_pdf.convert(**self._build_convert_options(path, opendataloader_pdf.convert))
        produced = self._find_output_json(path)
        if produced is None:
            logger.error("opendataloader_output_missing file=%s cache_dir=%s", path, cache_dir)
            raise FileNotFoundError(f"opendataloader output JSON not found for {path}")
        logger.info("opendataloader_convert_done file=%s json=%s", path, produced)
        return produced

    def _cleanup_existing_output(self, path: Path) -> None:
        if self.output_root is None:
            return
        cache_dir = self._cache_dir(path)
        if cache_dir.exists():
            import shutil
            logger.debug("parser_cache_clear file=%s cache_dir=%s", path, cache_dir)
            shutil.rmtree(cache_dir)

    def _build_convert_options(self, path: Path, convert_func: Any) -> dict[str, Any]:
        raw = dict(self.options)
        options: dict[str, Any] = {
            "input_path": str(path),
            "output_dir": str(self._cache_dir(path)),
            "format": raw.pop("format", "json"),
            "reading_order": raw.pop("reading_order", "xycut"),
            "quiet": raw.pop("quiet", True),
        }
        if raw.get("hybrid_backend"):
            options.update({
                "hybrid": raw.get("hybrid_backend"),
                "hybrid_mode": raw.get("hybrid_mode", "full"),
                "hybrid_url": raw.get("hybrid_url"),
                "hybrid_timeout": raw.get("hybrid_timeout", "120"),
                "hybrid_fallback": raw.get("hybrid_fallback", True),
            })
        else:
            for key in ("hybrid_backend","hybrid_mode","hybrid_url","hybrid_timeout","hybrid_fallback","hybrid_batch_size"):
                raw.pop(key, None)
        options.update(raw)
        supported = set(inspect.signature(convert_func).parameters)
        return {key: value for key, value in options.items() if key in supported and value is not None}

    def _find_output_json(self, path: Path) -> Path | None:
        cache_dir = self._cache_dir(path)
        if not cache_dir.exists():
            return None
        candidates = [cache_dir / path.stem / f"{path.stem}.json", cache_dir / f"{path.stem}.json"]
        candidates.extend(sorted(cache_dir.rglob(f"{path.stem}.json")))
        candidates.extend(sorted(cache_dir.rglob("*.json")))
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                return candidate
        return None

def document_from_opendataloader_json(
    file_path: Path,
    data: dict[str, Any],
    parser: str = "opendataloader_pdf",
    source_root: Path | None = None,
) -> Document:
    """Convert an opendataloader JSON tree into the unified document model.

    Args:
        file_path: Original source document.
        data: Parsed opendataloader JSON.
        parser: Parser name to store on the document.
        source_root: Directory used to resolve emitted image files.

    Returns:
        Unified document with pages, blocks, and tables.
    """
    pages: dict[int, Page] = {}

    def get_page(page_no: int) -> Page:
        if page_no not in pages:
            pages[page_no] = Page(page_no=page_no)
        return pages[page_no]

    for element in walk_elements(data):
        text = extract_text(element)
        block_type = str(element.get("type") or element.get("category") or "paragraph")
        if not text and "image" not in block_type.lower():
            continue
        page_no = int(element.get("page_number") or element.get("page number") or element.get("page") or 1)
        bbox = parse_bbox(element.get("bounding box") or element.get("bbox"))
        page = get_page(page_no)
        if bbox:
            page.width = max(page.width or 0, bbox[2])
            page.height = max(page.height or 0, bbox[3])
        page.blocks.append(
            Block(
                text=text,
                type=block_type,
                page_no=page_no,
                bbox=bbox,
                meta={"raw_type": block_type, "source": element.get("source"), "source_root": str(source_root) if source_root else None},
            )
        )

    for table_node in walk_tables(data):
        page_no = int(table_node.get("page_number") or table_node.get("page number") or table_node.get("page") or 1)
        rows = table_to_rows(table_node)
        if rows:
            get_page(page_no).tables.append(Table(rows=rows, page_no=page_no, bbox=parse_bbox(table_node.get("bounding box") or table_node.get("bbox"))))

    ordered_pages = [pages[key] for key in sorted(pages)] or [Page(page_no=1)]
    for page in ordered_pages:
        page.text = "\n".join(block.text for block in page.blocks if block.text)
    document = Document(file=file_path, pages=ordered_pages, parser=parser)
    document.rebuild_text()
    return document


def walk_elements(node: Any):
    """Yield text/image-like nodes from a nested opendataloader tree."""
    if isinstance(node, dict):
        node_type = str(node.get("type") or node.get("category") or "").lower()
        if any(key in node for key in ("text", "content")) or "image" in node_type:
            yield node
        for value in node.values():
            yield from walk_elements(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_elements(item)


def walk_tables(node: Any):
    """Yield only real table nodes, not row/cell containers."""
    if isinstance(node, dict):
        node_type = str(node.get("type") or node.get("category") or "").strip().lower()
        if node_type == "table" or node_type.endswith("_table"):
            yield node
        for value in node.values():
            yield from walk_tables(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_tables(item)


def extract_text(node: dict[str, Any]) -> str:
    value = node.get("text") or node.get("content") or node.get("value") or ""
    return str(value).strip()


def parse_bbox(value: Any):
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        except (TypeError, ValueError):
            return None
    return None


_CELL_CHILD_KEYS = ("kids", "children", "list items", "paragraphs", "blocks")


def cell_text(cell: Any) -> str:
    """Extract display text from nested OpenDataLoader table cells."""
    if not isinstance(cell, dict):
        if isinstance(cell, list):
            return " ".join(part for part in (cell_text(item) for item in cell) if part).strip()
        return str(cell or "").strip()

    child_parts: list[str] = []
    for key in _CELL_CHILD_KEYS:
        child = cell.get(key)
        if child is None:
            continue
        items = child if isinstance(child, list) else [child]
        for item in items:
            value = cell_text(item)
            if value:
                child_parts.append(value)
    if child_parts:
        return " ".join(dict.fromkeys(child_parts)).strip()

    for key in ("text", "content", "value"):
        value = cell.get(key)
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return text
        elif isinstance(value, (dict, list)):
            nested = cell_text(value)
            if nested:
                return nested
    return ""


def table_to_rows(table_node: dict[str, Any]) -> list[list[str]]:
    """Convert OpenDataLoader row/cell variants into normalized text rows."""
    rows = table_node.get("rows")
    if isinstance(rows, list):
        result: list[list[str]] = []
        for row in rows:
            if isinstance(row, dict):
                cells = row.get("cells")
                if not isinstance(cells, list):
                    continue
            elif isinstance(row, list):
                cells = row
            else:
                continue
            values = [cell_text(cell) for cell in cells]
            if any(values):
                result.append(values)
        if result:
            return result

    cells = table_node.get("cells")
    if not isinstance(cells, list):
        return []
    indexed: dict[int, dict[int, str]] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        try:
            row = int(cell.get("row") if cell.get("row") is not None else cell.get("row_index", 0))
            col = int(cell.get("col") if cell.get("col") is not None else cell.get("col_index", 0))
        except (TypeError, ValueError):
            continue
        indexed.setdefault(row, {})[col] = cell_text(cell)
    result: list[list[str]] = []
    for row in sorted(indexed):
        cols = indexed[row]
        if not cols:
            continue
        values = [cols.get(col, "") for col in range(max(cols) + 1)]
        if any(values):
            result.append(values)
    return result
