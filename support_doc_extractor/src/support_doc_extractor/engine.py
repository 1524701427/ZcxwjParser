from __future__ import annotations

import inspect
import json
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from support_doc_extractor.models import (
    Block,
    Document,
    ExtractedField,
    ExtractionResult,
    Page,
    Table,
    config_path,
    load_config,
    runtime_root,
)



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
        return document


# ==== OpenDataLoader 解析 ====

class OpenDataLoaderParser(Parser):
    """Adapter for opendataloader_pdf outputs.

    The parser has two responsibilities:
    1. Run opendataloader_pdf when needed.
    2. Convert the produced JSON into the project-level Document object.

    The exact opendataloader options vary by environment, so this class keeps
    execution small and explicit. Existing main.py integration can later be
    reused here without changing business extractors.
    """

    name = "opendataloader_pdf"

    def __init__(self, output_root: Path | None = None, options: dict[str, Any] | None = None) -> None:
        """Initialize parser output cache and opendataloader options."""
        self.output_root = output_root
        self.options = options or {}

    def parse(self, path: Path) -> Document:
        """Resolve or create opendataloader JSON and adapt it to Document."""
        json_path = self._resolve_or_run(path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return document_from_opendataloader_json(path, data, parser=self.name, source_root=json_path.parent)

    def _resolve_or_run(self, path: Path) -> Path:
        """Find existing JSON or run opendataloader for the input file."""
        if self.output_root is None:
            raise RuntimeError("OpenDataLoaderParser requires output_root or a pre-produced JSON adapter call.")
        if self.options.get("refresh"):
            self._cleanup_existing_output(path)
        else:
            existing = self._find_output_json(path)
            if existing is not None:
                return existing
        try:
            import opendataloader_pdf
        except ImportError as exc:
            raise RuntimeError("opendataloader_pdf is not installed.") from exc
        self.output_root.mkdir(parents=True, exist_ok=True)
        options = self._build_convert_options(path, opendataloader_pdf.convert)
        opendataloader_pdf.convert(**options)
        produced = self._find_output_json(path)
        if produced is None:
            raise FileNotFoundError(f"opendataloader output JSON not found for {path}")
        return produced

    def _cleanup_existing_output(self, path: Path) -> None:
        """Remove cached parse output for one document before refreshing."""
        if self.output_root is None:
            return
        targets = [
            self.output_root / path.stem / f"{path.stem}.json",
            self.output_root / f"{path.stem}.json",
            self.output_root / f"{path.stem}_images",
            self.output_root / path.stem,
        ]
        for target in targets:
            try:
                resolved_root = self.output_root.resolve()
                resolved_target = target.resolve()
                if resolved_root not in (resolved_target, *resolved_target.parents):
                    continue
                if target.is_file():
                    target.unlink()
                elif target.is_dir() and target.name in {path.stem, f"{path.stem}_images"}:
                    import shutil

                    shutil.rmtree(target)
            except OSError:
                continue

    def _build_convert_options(self, path: Path, convert_func: Any) -> dict[str, Any]:
        """Build only options supported by the installed converter version."""
        raw = dict(self.options)
        options: dict[str, Any] = {
            "input_path": str(path),
            "output_dir": str(self.output_root),
            "format": raw.pop("format", "json"),
            "reading_order": raw.pop("reading_order", "xycut"),
            "quiet": raw.pop("quiet", True),
        }
        if raw.get("hybrid_backend"):
            options.update(
                {
                    "hybrid": raw.get("hybrid_backend"),
                    "hybrid_mode": raw.get("hybrid_mode", "full"),
                    "hybrid_url": raw.get("hybrid_url"),
                    "hybrid_timeout": raw.get("hybrid_timeout", "0"),
                    "hybrid_fallback": raw.get("hybrid_fallback", True),
                }
            )
        else:
            for key in ("hybrid_backend", "hybrid_mode", "hybrid_url", "hybrid_timeout", "hybrid_fallback", "hybrid_batch_size"):
                raw.pop(key, None)
        options.update(raw)
        supported = set(inspect.signature(convert_func).parameters)
        return {key: value for key, value in options.items() if key in supported and value is not None}

    def _find_output_json(self, path: Path) -> Path | None:
        """Locate the JSON file emitted for an input document."""
        candidates = [
            self.output_root / path.stem / f"{path.stem}.json",
            self.output_root / f"{path.stem}.json",
        ]
        candidates.extend(sorted((self.output_root / path.stem).glob("*.json")) if (self.output_root / path.stem).exists() else [])
        candidates.extend(sorted(self.output_root.glob(f"**/{path.stem}.json")))
        for candidate in candidates:
            if candidate.exists():
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
    """Yield table-like nodes from a nested opendataloader tree."""
    if isinstance(node, dict):
        node_type = str(node.get("type") or node.get("category") or "").lower()
        if "table" in node_type or "cells" in node or "rows" in node:
            yield node
        for value in node.values():
            yield from walk_tables(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_tables(item)


def extract_text(node: dict[str, Any]) -> str:
    """Extract text from common opendataloader node fields."""
    value = node.get("text") or node.get("content") or node.get("value") or ""
    return str(value).strip()


def parse_bbox(value: Any):
    """Parse a four-value bounding box tuple when available."""
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        except (TypeError, ValueError):
            return None
    return None


def table_to_rows(table_node: dict[str, Any]) -> list[list[str]]:
    """Convert table rows or indexed cells into a rectangular row list."""
    rows = table_node.get("rows")
    if isinstance(rows, list):
        return [[cell_text(cell) for cell in row] for row in rows if isinstance(row, list)]
    cells = table_node.get("cells")
    if not isinstance(cells, list):
        return []
    indexed: dict[int, dict[int, str]] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        row = int(cell.get("row") or cell.get("row_index") or 0)
        col = int(cell.get("col") or cell.get("col_index") or 0)
        indexed.setdefault(row, {})[col] = cell_text(cell)
    result: list[list[str]] = []
    for row in sorted(indexed):
        cols = indexed[row]
        result.append([cols.get(col, "") for col in range(max(cols) + 1)])
    return result


def cell_text(cell: Any) -> str:
    """Extract display text from one table cell."""
    if isinstance(cell, dict):
        return str(cell.get("text") or cell.get("content") or cell.get("value") or "").strip()
    return str(cell or "").strip()


# ==== 文档类型处理 ====

class RuleClassifier:
    """Classify support documents using filename and content keywords.

    Args:
        config: Optional parsed ``support_doc_types.yaml`` config. When omitted,
            the default project config is loaded.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config(config_path("support_doc_types.yaml"))
        self.document_types = self.config.get("document_types", {})

    def classify(self, document: Document) -> tuple[str, float, dict[str, Any]]:
        """Classify a parsed document.

        Args:
            document: Unified document model.

        Returns:
            Tuple of document type, confidence, and matching details.
        """
        filename = str(document.file.name)
        text = document.full_text or document.rebuild_text()
        if self._looks_like_land_certificate(text):
            document.doc_type = "land_preapproval"
            document.meta["classification"] = {"confidence": 0.95, "reason": "land_certificate_layout"}
            return "land_preapproval", 0.95, {"reason": "land_certificate_layout"}
        best_type = "unknown_support_doc"
        best_score = 0.0
        details: dict[str, Any] = {}

        for doc_type, spec in self.document_types.items():
            if doc_type == "unknown_support_doc":
                continue
            keywords = spec.get("keywords", {}) or {}
            filename_hits = [kw for kw in keywords.get("filename", []) if kw and kw in filename]
            content_hits = [kw for kw in keywords.get("content", []) if kw and kw in text[:8000]]
            score = len(filename_hits) * 2 + len(content_hits)
            if score > best_score:
                best_type = doc_type
                best_score = float(score)
                details = {"filename_hits": filename_hits, "content_hits": content_hits}

        confidence = min(best_score / 5.0, 1.0) if best_score else 0.0
        if confidence < 0.2:
            best_type = "unknown_support_doc"
        document.doc_type = best_type
        document.meta["classification"] = {"confidence": confidence, **details}
        return best_type, confidence, details

    def fields_for(self, doc_type: str) -> list[str]:
        """Return configured output fields for a document type."""
        spec = self.document_types.get(doc_type) or self.document_types.get("unknown_support_doc") or {}
        return list(spec.get("fields", []))

    def _looks_like_land_certificate(self, text: str) -> bool:
        """Detect the certificate-style land preapproval layout."""
        hints = [
            "\u81ea\u7136\u8d44\u6e90\u548c\u89c4\u5212\u5c40",
            "\u9879\u76ee\u89c4\u5212\u9009\u5740\u8303\u56f4\u56fe",
            "\u9879\u76ee\u5efa\u8bbe\u7528\u5730\u8981\u6c42",
            "\u603b\u7528\u5730\u9762\u79ef",
        ]
        return sum(1 for hint in hints if hint in text) >= 2


# ==== 类型化抽取 ====

class TypedExtractor:
    """Extract fields for layouts that need document-type-specific logic."""

    source = "typed"

    def extract(self, document: Document, fields: list[str]) -> list[ExtractedField]:
        """Dispatch extraction by classified document type."""
        if document.doc_type == "land_preapproval":
            return self._land_preapproval(document, fields)
        return []

    def _land_preapproval(self, document: Document, fields: list[str]) -> list[ExtractedField]:
        """Extract land preapproval fields from reply or certificate layouts."""
        text = document.full_text or document.rebuild_text()
        results: list[ExtractedField] = []
        if self._looks_like_land_certificate(document):
            results.extend(self._land_certificate(document, fields))
        else:
            results.extend(self._land_approval_reply(document, fields))
        return results

    def _looks_like_land_certificate(self, document: Document) -> bool:
        """Return whether the document has the certificate-style table page."""
        text = document.full_text or document.rebuild_text()
        if "\u5efa\u8bbe\u9879\u76ee\u7528\u5730\u9884\u5ba1\u4e0e\u9009\u5740\u610f\u89c1\u4e66" in text:
            return True
        hints = [
            "\u9879\u76ee\u89c4\u5212\u9009\u5740\u8303\u56f4\u56fe",
            "\u9879\u76ee\u5efa\u8bbe\u7528\u5730\u8981\u6c42",
            "\u603b\u7528\u5730\u9762\u79ef",
            "\u81ea\u7136\u8d44\u6e90\u548c\u89c4\u5212\u5c40",
        ]
        return sum(1 for hint in hints if hint in text) >= 2

    def _land_certificate(self, document: Document, fields: list[str]) -> list[ExtractedField]:
        """Extract from the two-column certificate form layout."""
        blocks = sorted(document.pages[0].blocks if document.pages else [], key=lambda b: ((b.bbox or (0, 0, 0, 0))[1], (b.bbox or (0, 0, 0, 0))[0]), reverse=True)
        right_values = [b for b in blocks if b.bbox and b.bbox[0] > 500 and b.text]
        left_values = [b for b in blocks if b.bbox and 180 < b.bbox[0] < 340 and b.text]
        mapping = [
            ("project_name", 0),
            ("project_code", 1),
            ("construction_unit", 2),
            ("project_basis", 3),
            ("project_location", 4),
            ("land_control_area", 5),
            ("construction_scale", 6),
        ]
        results: list[ExtractedField] = []
        if "title" in fields:
            title = "\u5efa\u8bbe\u9879\u76ee\u7528\u5730\u9884\u5ba1\u4e0e\u9009\u5740\u610f\u89c1\u4e66"
            results.append(ExtractedField("title", title, self.source, 0.96, page_no=1, evidence=title))
        for field, index in mapping:
            if field in fields and len(right_values) > index:
                block = right_values[index]
                if field == "construction_scale" and is_area_value(block.text):
                    continue
                results.append(ExtractedField(field, block.text, self.source, 0.94, page_no=block.page_no, evidence=block.text))
        if "approval_agency" in fields and left_values:
            agency = next((b for b in left_values if any(h in b.text for h in ("\u5c40", "\u5385", "\u59d4", "\u4eba\u6c11\u653f\u5e9c"))), None)
            if agency:
                results.append(ExtractedField("approval_agency", agency.text, self.source, 0.94, page_no=agency.page_no, evidence=agency.text))
        if "issue_date" in fields:
            date_block = next((b for b in left_values if re.search(r"\d{4}\s*\u5e74\s*\d{1,2}\s*\u6708\s*\d{1,2}\s*\u65e5", b.text)), None)
            if date_block:
                results.append(ExtractedField("issue_date", date_block.text, self.source, 0.94, page_no=date_block.page_no, evidence=date_block.text))
        if "document_no" in fields:
            doc_no = next((b for b in left_values if re.fullmatch(r"[A-Za-z0-9\-]+", b.text.strip())), None)
            if doc_no:
                value = f"\u7528\u5b57\u7b2c{doc_no.text}\u53f7"
                results.append(ExtractedField("document_no", value, self.source, 0.92, page_no=doc_no.page_no, evidence=doc_no.text))
        return results

    def _land_approval_reply(self, document: Document, fields: list[str]) -> list[ExtractedField]:
        """Extract from ordinary narrative land preapproval replies."""
        text = document.full_text or document.rebuild_text()
        results: list[ExtractedField] = []
        title_match = re.search(r"\u5173\u4e8e.{5,80}?\u5efa\u8bbe\u7528\u5730\u9884\u5ba1\u7684\u6279\u590d", text)
        if "title" in fields and title_match:
            results.append(ExtractedField("title", cleanup(title_match.group(0)), self.source, 0.9, page_no=1, evidence=title_match.group(0)))
        project_match = re.search(r"\u4e00\u3001\s*([^\uff08\(\n\u3002]{4,80}?\u9879\u76ee)[\uff08\(]\u9879\u76ee\u4ee3\u7801[:\uff1a]\s*([A-Za-z0-9\-]+)", text)
        if project_match:
            if "project_name" in fields:
                results.append(ExtractedField("project_name", cleanup(project_match.group(1)), self.source, 0.88, evidence=project_match.group(0)))
            if "project_code" in fields:
                results.append(ExtractedField("project_code", project_match.group(2), self.source, 0.9, evidence=project_match.group(0)))
        land_match = re.search(r"\u9879\u76ee\u7528\u5730\u5e94\u63a7\u5236\u5728\s*([^\u3002]{1,120}(?:\u516c\u9877|\u4ea9|\u5e73\u65b9\u7c73)[^\u3002]*)", text)
        if "land_control_area" in fields and land_match:
            results.append(ExtractedField("land_control_area", cleanup(land_match.group(1)), self.source, 0.88, evidence=land_match.group(0)))
        if "approval_agency" in fields:
            agency_match = re.search(r"([\u4e00-\u9fa5]{2,30}(?:\u81ea\u7136\u8d44\u6e90\u5385|\u81ea\u7136\u8d44\u6e90\u548c\u89c4\u5212\u5c40|\u81ea\u7136\u8d44\u6e90\u5c40))(?:\u6587\u4ef6)?", text[:1500])
            if not agency_match:
                agency_match = re.search(r"([\u4e00-\u9fa5]{2,30}(?:\u81ea\u7136\u8d44\u6e90\u5385|\u81ea\u7136\u8d44\u6e90\u548c\u89c4\u5212\u5c40|\u81ea\u7136\u8d44\u6e90\u5c40))", document.file.name)
            if agency_match:
                results.append(ExtractedField("approval_agency", cleanup(agency_match.group(1)), self.source, 0.9, page_no=1, evidence=agency_match.group(0)))
        if "document_no" in fields:
            doc_no_match = re.search(r"([\u4e00-\u9fa5]{0,6}\u81ea\u7136\u8d44\u9884\u5ba1\s*[\u3014\uff3b\[\uff08(]\s*(?:19|20)\d{2}\s*[\u3015\uff3d\]\uff09)]\s*\d+\s*\u53f7)", text[:2500])
            if doc_no_match:
                results.append(ExtractedField("document_no", cleanup(doc_no_match.group(1)), self.source, 0.9, evidence=doc_no_match.group(0)))
        return results


def cleanup(text: str) -> str:
    """Compact Chinese field text and trim common punctuation."""
    return re.sub(r"\s+", "", text or "").strip("\uff1a: ,\uff0c")


def is_area_value(text: str) -> bool:
    """Return whether text is a land-area value."""
    value = cleanup(text)
    if not value:
        return False
    return bool(re.search(r"(?:\u516c\u9877|\u4ea9)", value))


# ==== 通用字段抽取 ====

ORG_HINTS = (
    "\u5c40",
    "\u5385",
    "\u59d4",
    "\u516c\u53f8",
    "\u4eba\u6c11\u653f\u5e9c",
    "\u81ea\u7136\u8d44\u6e90",
    "\u751f\u6001\u73af\u5883",
    "\u53d1\u5c55\u548c\u6539\u9769",
    "\u4f9b\u7535",
    "\u529e\u516c\u5ba4",
    "\u519b\u4e8b\u79d1",
)

DOCUMENT_NO_PATTERNS = (
    r"[\u4e00-\u9fa5A-Za-z]{1,16}\s*[\u3014\[\uff3b\uff08(]\s*(?:19|20)\d{2}\s*[\u3015\]\uff3d\uff09)]\s*\d+\s*\u53f7",
    r"[\u4e00-\u9fa5A-Za-z]{1,16}\s*(?:19|20)\d{2}\s*\d+\s*\u53f7",
    r"\u7528\u5b57\u7b2c\s*[A-Za-z0-9\-]+\s*\u53f7",
)

DATE_PATTERN = r"(?:19|20)\d{2}\s*\u5e74\s*\d{1,2}\s*\u6708\s*\d{1,2}\s*\u65e5"
ISSUE_DATE_PATTERN = rf"({DATE_PATTERN})(?:\s*\u5370\u53d1)?"
TITLE_ENDINGS = (
    "\u6279\u590d",
    "\u56de\u51fd",
    "\u590d\u51fd",
    "\u51fd",
    "\u901a\u77e5",
    "\u610f\u89c1",
    "\u610f\u89c1\u4e66",
)


class CommonExtractor:
    """Extract fields shared by most support documents.

    The common extractor focuses on the first embedded document when multiple
    files are concatenated into one PDF. It handles document number, title,
    approval agency, and issue date using page order, block coordinates, and
    signature/footer clues.
    """

    source = "common"

    def extract(self, document: Document, fields: list[str]) -> list[ExtractedField]:
        """Extract requested common fields.

        Args:
            document: Parsed document.
            fields: Field names configured for the classified document type.

        Returns:
            Candidate fields before merge and normalization.
        """
        results: list[ExtractedField] = []
        first_page = document.pages[0] if document.pages else None
        first_text = first_page.text if first_page else ""
        first_scope_pages = first_document_scope(document)

        if "document_no" in fields:
            document_no = self._document_no(first_page)
            if document_no:
                results.append(
                    ExtractedField(
                        "document_no",
                        document_no,
                        self.source,
                        0.88,
                        page_no=1,
                        evidence=document_no,
                        meta={"rule": "first_page_redline_area"},
                    )
                )
        if "title" in fields:
            title = self._title(document, first_text)
            if title:
                results.append(ExtractedField("title", title, self.source, 0.86, page_no=1, evidence=title))
        if "approval_agency" in fields:
            agency, agency_page, agency_evidence = self._agency(document, first_scope_pages)
            if agency:
                results.append(
                    ExtractedField(
                        "approval_agency",
                        agency,
                        self.source,
                        0.84,
                        page_no=agency_page,
                        evidence=agency_evidence or agency,
                        meta={"rule": "signature_or_footer_first_document"},
                    )
                )
        if "issue_date" in fields:
            issue_date, date_page, date_evidence = self._issue_date(first_scope_pages)
            if issue_date:
                results.append(
                    ExtractedField(
                        "issue_date",
                        issue_date,
                        self.source,
                        0.86,
                        page_no=date_page,
                        evidence=date_evidence or issue_date,
                        meta={"rule": "signature_or_footer_first_document"},
                    )
                )
        return results

    def _document_no(self, first_page: Page | None) -> str | None:
        """Find the first-page official document number near the redline area."""
        if first_page is None:
            return None
        candidates: list[tuple[float, str]] = []
        for block in sorted_blocks(first_page):
            text = normalize_inline(block.text)
            if not text or "\u53f7" not in text:
                continue
            for pattern in DOCUMENT_NO_PATTERNS:
                match = re.search(pattern, text)
                if not match:
                    continue
                y = block_center_ratio(first_page, block)[1]
                score = 1.0 - abs(y - 0.28)
                candidates.append((score, match.group(0)))
        if not candidates:
            text = "\n".join(first_page.text.splitlines()[:12])
            for pattern in DOCUMENT_NO_PATTERNS:
                match = re.search(pattern, text)
                if match:
                    return cleanup_document_no(match.group(0))
            return None
        return cleanup_document_no(max(candidates, key=lambda item: item[0])[1])

    def _title(self, document: Document, first_text: str) -> str | None:
        """Find and merge title blocks from the first one or two pages."""
        for page in document.pages[:2]:
            for block in page.blocks:
                block_text = compact(block.text)
                has_title_word = "\u5173\u4e8e" in block_text or any(word in block_text for word in TITLE_ENDINGS) or "\u7528\u5730\u9884\u5ba1" in block_text
                title_is_complete = any(block_text.endswith(ending) for ending in TITLE_ENDINGS) or "\u7528\u5730\u9884\u5ba1\u4e0e\u9009\u5740\u610f\u89c1\u4e66" in block_text
                if block.type.lower() in {"title", "heading"} and has_title_word and title_is_complete and len(block.text) >= 6:
                    return cleanup_title(block.text)
        first_page = document.pages[0] if document.pages else None
        title = title_from_first_page_blocks(first_page)
        if title:
            return title
        match = re.search(r"\u5173\u4e8e.{5,160}?(?:\u6279\u590d|\u901a\u77e5|\u56de\u51fd|\u590d\u51fd|\u51fd|\u610f\u89c1|\u5ba1\u67e5\u610f\u89c1)", first_text, re.S)
        if match:
            return cleanup_title(match.group(0))
        lines = [line.strip() for line in re.split(r"[\n\u3002]", first_text) if line.strip()]
        title_words = ("\u6279\u590d", "\u610f\u89c1", "\u901a\u77e5", "\u51fd", "\u9009\u5740\u610f\u89c1\u4e66", "\u7528\u5730\u9884\u5ba1")
        for line in lines[:8]:
            if any(word in line for word in title_words) and len(line) >= 8:
                return cleanup_title(line)
        return None

    def _agency(self, document: Document, pages: list[Page]) -> tuple[str | None, int | None, str | None]:
        """Find approval agency from signature, footer, or first-page heading."""
        for page in reversed(pages):
            date_blocks = blocks_matching(page, DATE_PATTERN)
            for date_block in date_blocks:
                nearby = nearby_blocks(page, date_block, x_tolerance=0.35, y_before=0.16, y_after=0.04)
                for block in reversed(nearby):
                    agency = clean_agency(block.text)
                    if agency:
                        return agency, page.page_no, block.text

        for page in reversed(pages):
            footer_blocks = [block for block in sorted_blocks(page) if block_center_ratio(page, block)[1] >= 0.84]
            for block in footer_blocks:
                agency = clean_agency(block.text)
                if agency:
                    return agency, page.page_no, block.text

        first_text = document.pages[0].text if document.pages else ""
        title_prefix = re.search(r"([\u4e00-\u9fa5]{4,30})(?:\u5173\u4e8e)", first_text[:500])
        if title_prefix:
            return title_prefix.group(1), 1, title_prefix.group(0)

        for line in [line.strip() for line in first_text.splitlines() if line.strip()][:10]:
            agency = clean_agency(line)
            if agency:
                return agency, 1, line
        return None, None, None

    def _issue_date(self, pages: list[Page]) -> tuple[str | None, int | None, str | None]:
        """Find the issue date from signature/footer text in the first document."""
        for page in reversed(pages):
            page_text = page.text or "\n".join(block.text for block in page.blocks if block.text)
            lines = [line.strip() for line in page_text.splitlines() if line.strip()]
            for line in reversed(lines):
                if "\u5370\u53d1" not in line and "\u65e5\u671f" not in line:
                    continue
                dates = re.findall(DATE_PATTERN, line)
                if dates:
                    return dates[-1], page.page_no, line

            date_blocks = blocks_matching(page, DATE_PATTERN)
            footer_date_blocks = [block for block in date_blocks if "\u5370\u53d1" in block.text]
            for block in reversed(footer_date_blocks):
                dates = re.findall(DATE_PATTERN, block.text)
                if dates:
                    return dates[-1], page.page_no, block.text
            if date_blocks:
                block = max(date_blocks, key=lambda item: block_center_ratio(page, item)[1])
                match = re.search(DATE_PATTERN, block.text)
                if match:
                    return match.group(0), page.page_no, block.text

        return None, None, None


def cleanup_title(text: str) -> str:
    """Compact title text across OCR line breaks."""
    return re.sub(r"\s+", "", text or "").strip(" \uff1a:")


def cleanup_document_no(text: str) -> str:
    """Normalize bracket styles in official document numbers."""
    return (
        normalize_inline(text)
        .replace("\uff3b", "\u3014")
        .replace("[", "\u3014")
        .replace("\uff3d", "\u3015")
        .replace("]", "\u3015")
        .replace("\uff08", "\u3014")
        .replace("(", "\u3014")
        .replace("\uff09", "\u3015")
        .replace(")", "\u3015")
    )


def normalize_inline(text: str) -> str:
    """Collapse whitespace while keeping word separation."""
    return re.sub(r"\s+", " ", text or "").strip()


def compact(text: str) -> str:
    """Remove all whitespace for Chinese layout matching."""
    return re.sub(r"\s+", "", text or "")


def sorted_blocks(page: Page | None) -> list[Block]:
    if page is None:
        return []
    return sorted(page.blocks, key=lambda block: block_sort_key(page, block))


def block_sort_key(page: Page, block: Block) -> tuple[float, float]:
    x, y = block_center_ratio(page, block)
    return (y, x)


def block_center_ratio(page: Page, block: Block) -> tuple[float, float]:
    if not block.bbox or not page.width or not page.height:
        return (0.5, 0.5)
    x0, y0, x1, y1 = block.bbox
    return ((x0 + x1) / 2 / max(page.width, 1.0), (y0 + y1) / 2 / max(page.height, 1.0))


def blocks_matching(page: Page, pattern: str) -> list[Block]:
    return [block for block in sorted_blocks(page) if re.search(pattern, normalize_inline(block.text))]


def nearby_blocks(page: Page, anchor: Block, *, x_tolerance: float, y_before: float, y_after: float) -> list[Block]:
    anchor_x, anchor_y = block_center_ratio(page, anchor)
    result = []
    for block in sorted_blocks(page):
        x, y = block_center_ratio(page, block)
        if abs(x - anchor_x) <= x_tolerance and anchor_y - y_before <= y <= anchor_y + y_after:
            result.append(block)
    return result


def clean_agency(text: str) -> str | None:
    """Clean a possible issuing agency line and reject weak candidates."""
    text = normalize_inline(text)
    text = re.sub(DATE_PATTERN, "", text)
    text = text.replace("\u5370\u53d1", "").replace("\u53d1\u8bc1\u673a\u5173", "").replace("\u6279\u51c6\u5355\u4f4d\u540d\u79f0", "")
    text = text.replace("\u6587\u4ef6", "")
    text = re.sub(r"[\uff1a:：\s]+$", "", text).strip()
    match = re.search(r"[\u4e00-\u9fa5]{2,35}(?:\u5c40|\u5385|\u59d4|\u90e8|\u5e9c|\u529e\u516c\u5ba4|\u79d1)", text)
    if match:
        value = match.group(0)
    else:
        value = text
    if any(hint in value for hint in ORG_HINTS) and 4 <= len(value) <= 35:
        return value
    return None


def title_from_first_page_blocks(first_page: Page | None) -> str | None:
    """Build a title from spatially ordered first-page blocks."""
    blocks = [block for block in sorted_blocks(first_page) if block.text.strip()]
    if not blocks:
        return None
    lines = [normalize_inline(block.text) for block in blocks]
    for idx, line in enumerate(lines[:20]):
        line_compact = compact(line)
        if "\u5173\u4e8e" not in line_compact:
            continue
        parts = []
        if idx > 0:
            previous_block = blocks[idx - 1]
            previous = lines[idx - 1]
            previous_agency = clean_agency(previous)
            previous_y = block_center_ratio(first_page, previous_block)[1] if first_page else 0.0
            if previous_agency and "\u6587\u4ef6" not in previous and 0.22 <= previous_y <= 0.72:
                parts.append(previous_agency)
        parts.append(line)
        for extra in lines[idx + 1 : idx + 5]:
            extra_compact = compact(extra)
            if should_stop_title_merge(extra_compact):
                break
            parts.append(extra)
            joined = cleanup_title("".join(parts))
            if any(joined.endswith(ending) for ending in TITLE_ENDINGS):
                return joined
        joined = cleanup_title("".join(parts))
        if any(word in joined for word in TITLE_ENDINGS) and 8 <= len(joined) <= 120:
            return joined

    page_text = "\n".join(lines[:16])
    if "\u5efa\u8bbe\u9879\u76ee" in page_text and "\u7528\u5730\u9884\u5ba1" in page_text:
        title_lines = [line for line in lines[:16] if not is_non_title_line(line)]
        title = cleanup_title("".join(line for line in title_lines if "\u5efa\u8bbe\u9879\u76ee" in line or "\u7528\u5730\u9884\u5ba1" in line or "\u9009\u5740\u610f\u89c1\u4e66" in line))
        if title:
            return title
    return None


def should_stop_title_merge(line: str) -> bool:
    """Return whether a following block should stop title concatenation."""
    if not line:
        return True
    if "\uff1a" in line or ":" in line:
        return True
    if re.search(DOCUMENT_NO_PATTERNS[0], line) or re.search(DOCUMENT_NO_PATTERNS[2], line):
        return True
    return False


def is_non_title_line(line: str) -> bool:
    """Return whether a first-page line is clearly not part of a title."""
    value = compact(line)
    if not value:
        return True
    if "\u4e2d\u534e\u4eba\u6c11\u5171\u548c\u56fd" in value:
        return True
    if "\u7528\u5b57\u7b2c" in value or "\u53d1\u8bc1\u673a\u5173" in value:
        return True
    return False


def first_document_scope(document: Document) -> list[Page]:
    """Return pages likely belonging to the first embedded document.

    A scanned PDF may concatenate multiple support documents. Without a reliable
    container boundary, use pages up to the next obvious first-page marker.
    """
    if not document.pages:
        return []
    scoped = [document.pages[0]]
    for page in document.pages[1:]:
        text = compact(page.text or "\n".join(block.text for block in page.blocks if block.text))
        looks_like_new_doc = (
            "\u5173\u4e8e" in text[:400]
            and any(word in text[:700] for word in TITLE_ENDINGS)
            and any(re.search(pattern, text[:500]) for pattern in DOCUMENT_NO_PATTERNS)
        )
        if looks_like_new_doc:
            break
        scoped.append(page)
    return scoped


# ==== 表格字段抽取 ====

class TableExtractor:
    """Extract field candidates from structured table rows."""

    source = "table"

    def __init__(self, fields_config: dict[str, Any] | None = None) -> None:
        """Initialize table aliases from field configuration."""
        self.fields_config = fields_config or load_config(config_path("fields.yaml"))
        self.fields = self.fields_config.get("fields", {})

    def extract(self, document: Document, fields: list[str]) -> tuple[list[ExtractedField], list[Table]]:
        """Extract matching field values and return the source tables."""
        results: list[ExtractedField] = []
        tables: list[Table] = []
        for page in document.pages:
            for table in page.tables:
                tables.append(table)
                results.extend(self._extract_from_table(table, fields))
        return results, tables

    def _extract_from_table(self, table: Table, fields: list[str]) -> list[ExtractedField]:
        """Match configured aliases against the first cell of each row."""
        results: list[ExtractedField] = []
        for row in table.rows:
            if len(row) < 2:
                continue
            key = normalize_cell(row[0])
            value = " ".join(cell.strip() for cell in row[1:] if cell and cell.strip()).strip()
            if not key or not value:
                continue
            for field in fields:
                aliases = self.fields.get(field, {}).get("aliases", [])
                if any(alias and alias in key for alias in aliases):
                    results.append(
                        ExtractedField(
                            name=field,
                            value=value,
                            source=self.source,
                            confidence=0.9,
                            page_no=table.page_no,
                            evidence=f"{row[0]}: {value}",
                        )
                    )
        return results


def normalize_cell(value: str) -> str:
    """Remove whitespace so table headers can be matched reliably."""
    return "".join(str(value or "").split())


# ==== 规则字段抽取 ====

# Access-approval documents contain several related electrical parameters.
# Keeping their fields together prevents generic regexes from stealing a nearby
# voltage, distance, or capacity from the wrong subsection.
GRID_ACCESS_FIELDS = {
    "access_investment",
    "access_plan",
    "access_station",
    "outgoing_circuits",
    "access_distance",
    "conductor_section",
    "access_interval_desc",
    "main_transformer_capacity",
    "main_transformer_wiring",
    "svg_capacity",
}


PATTERNS: dict[str, list[str]] = {
    "document_no": [
        r"[\u4e00-\u9fa5A-Za-z]{1,12}[\u3014\[\uff08(]\d{4}[\u3015\]\uff09)]\s*\d+\s*\u53f7",
        r"\u7528\u5b57\u7b2c\s*[A-Za-z0-9\-]+\s*\u53f7",
    ],
    "issue_date": [
        r"(?:19|20)\d{2}\s*\u5e74\s*\d{1,2}\s*\u6708\s*\d{1,2}\s*\u65e5",
        r"(?:19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2}",
    ],
    "total_investment": [r"\u603b\u6295\u8d44\s*[:\uff1a]?\s*([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143))"],
    "environmental_investment": [
        r"(?:\u73af\u4fdd\u6295\u8d44|\u73af\u5883\u4fdd\u62a4\u6295\u8d44)[^\u3002\uff1b;\n0-9]{0,8}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
        r"\u5176\u4e2d\s*(?:\u73af\u4fdd\u6295\u8d44|\u73af\u5883\u4fdd\u62a4\u6295\u8d44)[^\u3002\uff1b;\n0-9]{0,8}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
        r"([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))[^\u3002\uff1b;\n]{0,8}(?:\u73af\u4fdd\u6295\u8d44|\u73af\u5883\u4fdd\u62a4\u6295\u8d44)",
    ],
    "soil_water_investment": [
        r"\u6c34\u571f\u4fdd\u6301[^\u3002\uff1b;\n]{0,16}?(?:\u4f30\u7b97|\u6982\u7b97)?(?:\u603b)?\u6295\u8d44[^\u3002\uff1b;\n0-9]{0,8}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
        r"\u5efa\u8bbe\u671f\u6c34\u571f\u4fdd\u6301[^\u3002\uff1b;\n]{0,16}?(?:\u4f30\u7b97|\u6982\u7b97)?(?:\u603b)?\u6295\u8d44[^\u3002\uff1b;\n0-9]{0,8}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
        r"([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))[^\u3002\uff1b;\n]{0,8}\u6c34\u571f\u4fdd\u6301[^\u3002\uff1b;\n]{0,12}?(?:\u4f30\u7b97|\u6982\u7b97)?(?:\u603b)?\u6295\u8d44",
    ],
    "soil_water_compensation_fee": [
        r"\u6c34\u571f\u4fdd\u6301\u8865\u507f\u8d39[^\u3002\uff1b;\n0-9]{0,8}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
        r"([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))[^\u3002\uff1b;\n]{0,8}\u6c34\u571f\u4fdd\u6301\u8865\u507f\u8d39",
    ],
    "capacity": [r"(?:\u88c5\u673a\u5bb9\u91cf|\u603b\u88c5\u673a\u89c4\u6a21|\u5efa\u8bbe\u89c4\u6a21)\s*[:\uff1a]?\s*([0-9,.]+\s*(?:MW|\u5146\u74e6|\u4e07\u5343\u74e6|kW))"],
    "land_area": [r"(?:\u62df\u7528\u5730\u9762\u79ef|\u603b\u7528\u5730\u9762\u79ef|\u7528\u5730\u9762\u79ef)\s*[:\uff1a]?\s*([^\u3002\uff1b;\n]{1,80}(?:\u516c\u9877|\u4ea9|\u5e73\u65b9\u7c73))"],
    "land_control_area": [
        r"(?:\u9879\u76ee\u7528\u5730|\u7528\u5730\u8303\u56f4|\u63a7\u5236\u7528\u5730\u89c4\u6a21)[^\u3002\uff1b;\n]{0,20}?(?:\u5e94\u63a7\u5236\u5728|\u63a7\u5236\u5728|\u4e3a)\s*([0-9,.]+\s*(?:\u516c\u9877|\u4ea9|\u5e73\u65b9\u7c73|m2|\u33a1))",
        r"(?:\u5e94\u63a7\u5236\u5728|\u63a7\u5236\u5728)\s*([0-9,.]+\s*(?:\u516c\u9877|\u4ea9|\u5e73\u65b9\u7c73|m2|\u33a1))",
    ],
    "access_voltage": [r"(\d+\s*kV)"],
    "line_length": [
        r"(?:\u67b6\u7a7a\u7ebf\u8def|\u7535\u7f06\u7ebf\u8def|\u65b0\u5efa[^\uff0c\u3002\uff1b;]{0,20}\u7ebf\u8def|\u7ebf\u8def\u957f\u5ea6|\u8def\u5f84\u957f\u5ea6)\s*(?:\u7ea6|:|\uff1a)?\s*([0-9,.]+(?:\s*[xX\u00d7]\s*[0-9,.]+)?\s*(?:km|\u516c\u91cc|\u5343\u7c73))",
    ],
    "loan_interest_rate": [
        r"\u5229\u7387[^\u3002\uff1b;\n%]{0,40}?([0-9]+(?:\.[0-9]+)?)\s*%",
        r"([0-9]+(?:\.[0-9]+)?)\s*%[^\u3002\uff1b;\n]{0,30}?\u5229\u7387",
    ],
}


class RuleExtractor:
    """Extract field candidates with configured regular expressions."""

    source = "rule"

    def extract(self, document: Document, fields: list[str]) -> list[ExtractedField]:
        """Extract all requested rule-based fields from document text.

        Args:
            document: Parsed document.
            fields: Field names configured for the classified document type.

        Returns:
            Candidate fields before merge and normalization.
        """
        text = document.full_text or document.rebuild_text()
        compact_text = compact_text_for_regex(text)
        results: list[ExtractedField] = []
        for field in fields:
            if field in GRID_ACCESS_FIELDS:
                extracted = extract_grid_access_field(field, text)
                if extracted is not None:
                    results.append(extracted)
                    continue
            for pattern in PATTERNS.get(field, []):
                match = re.search(pattern, compact_text)
                if not match:
                    continue
                value = match.group(1) if match.groups() else match.group(0)
                if field == "loan_interest_rate" and "%" not in value:
                    value = f"{value}%"
                results.append(
                    ExtractedField(
                        name=field,
                        value=value.strip(),
                        source=self.source,
                        confidence=0.85,
                        evidence=match.group(0),
                    )
                )
                break
        return results


def extract_grid_access_field(field: str, raw_text: str) -> ExtractedField | None:
    """Extract one access-approval field using the specialized handlers."""
    text = normalize_text(raw_text)
    handlers = {
        "access_investment": extract_access_investment,
        "access_plan": extract_access_plan,
        "access_station": extract_access_station,
        "outgoing_circuits": extract_outgoing_circuits,
        "access_distance": extract_access_distance,
        "conductor_section": extract_conductor_section,
        "access_interval_desc": extract_access_interval_desc,
        "main_transformer_capacity": extract_main_transformer_capacity,
        "main_transformer_wiring": extract_main_transformer_wiring,
        "svg_capacity": extract_svg_capacity,
    }
    value, evidence, meta = handlers[field](text)
    if value is None:
        return None
    return ExtractedField(field, value, "rule", 0.87, evidence=evidence, meta=meta)


def normalize_text(text: str) -> str:
    """Collapse whitespace so multiline OCR text can be searched as prose."""
    return re.sub(r"\s+", " ", text or "").strip()


def compact_text_for_regex(text: str) -> str:
    """Remove OCR whitespace noise before regular-expression extraction."""
    return re.sub(r"\s+", "", text or "")


def first_match(text: str, patterns: list[str]) -> tuple[str | None, str | None, dict]:
    """Return the first capture, evidence, and pattern metadata."""
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(0), {"pattern": pattern}
    return None, None, {}


def split_sentences(text: str) -> list[str]:
    """Split Chinese prose at sentence and clause boundaries used by rules."""
    return [part.strip() for part in re.split(r"(?<=[\u3002\uff1b;])", text) if part.strip()]


def cleanup_sentence(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def section_window(text: str, heading: str, chars: int) -> str:
    """Return a local text window after a subsection heading."""
    index = text.find(heading)
    return "" if index < 0 else text[index : index + chars]


def extract_access_investment(text: str) -> tuple[str | None, str | None, dict]:
    return first_match(
        text,
        [
            r"(?:\u63a5\u5165\u7cfb\u7edf(?:\u5de5\u7a0b|\u65b9\u6848)?(?:\u603b)?\u6295\u8d44|\u63a5\u5165\u7cfb\u7edf\u65b9\u6848\u6295\u8d44)[^\u3002\uff1b;0-9]{0,12}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
            r"\u65b9\u6848\u6295\u8d44[^\u3002\uff1b;0-9]{0,12}([0-9,.]+\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143))",
        ],
    )


def extract_access_plan(text: str) -> tuple[str | None, str | None, dict]:
    """Extract the approved access-system plan sentence."""
    value, evidence, meta = first_match(
        text,
        [
            r"\u63a5\u5165\u7cfb\u7edf(?:\u4e00\u6b21)?\u65b9\u6848[:\uff1a]\s*([^。\uff1b;]{10,240}\u3002?)",
            r"(?:\u63a5\u5165\u7cfb\u7edf\u4e00\u6b21\u65b9\u6848|\u63a5\u5165\u7cfb\u7edf\u65b9\u6848)[^\u3002]{0,80}?\u63a5\u5165\u7cfb\u7edf\u65b9\u6848[:\uff1a]\s*([^。\uff1b;]{10,240}\u3002?)",
            r"\u540c\u610f[^\u3002]{0,50}?\u63a5\u5165\u7cfb\u7edf\u65b9\u6848[:\uff1a]\s*([^。\uff1b;]{10,240}\u3002?)",
        ],
    )
    return (cleanup_sentence(value), evidence, meta) if value else (None, None, {})


def extract_access_station(text: str) -> tuple[str | None, str | None, dict]:
    plan, _, _ = extract_access_plan(text)
    return first_match(plan or text, [r"\u63a5\u5165([\u4e00-\u9fa5A-Za-z0-9~\uff5e]{2,30}(?:\u53d8|\u5347\u538b\u7ad9))"])


def extract_outgoing_circuits(text: str) -> tuple[str | None, str | None, dict]:
    plan, _, _ = extract_access_plan(text)
    value, evidence, meta = first_match(
        plan or text,
        [
            r"(?:\u4ee5|\u76f4\u63a5\u4ee5)\s*([0-9\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\s*\u56de[^\u3002\uff1b;]{0,40}?\u63a5\u5165",
            r"\u51fa\u7ebf\s*([0-9\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\s*\u56de",
        ],
    )
    return (f"{chinese_digit_to_int(value)}\u56de", evidence, meta) if value else (None, None, {})


def extract_access_distance(text: str) -> tuple[str | None, str | None, dict]:
    return first_match(
        text,
        [
            r"(?:\u67b6\u7a7a\u7ebf\u8def|\u7535\u7f06\u7ebf\u8def|\u65b0\u5efa[^\u3002\uff1b;]{0,20}\u7ebf\u8def)\s*(?:\u7ea6)?\s*([0-9,.]+(?:\s*[xX\u00d7]\s*[0-9,.]+)?\s*(?:km|\u516c\u91cc|\u5343\u7c73))",
        ],
    )


def extract_conductor_section(text: str) -> tuple[str | None, str | None, dict]:
    """Extract conductor/cable section values, preserving adjusted values last."""
    item_map: dict[str, str] = {}
    evidences: list[str] = []
    for sentence in split_sentences(text):
        if "\u622a\u9762" not in sentence:
            continue
        last_end = 0
        sentence_line_type: str | None = None
        for match in re.finditer(r"([0-9]+(?:\s*[xX\u00d7]\s*)?[0-9]*)\s*\u5e73\u65b9\u6beb\u7c73", sentence):
            local = sentence[last_end : match.start()]
            if "\u7535\u7f06" in local:
                line_type = "\u7535\u7f06"
            elif "\u67b6\u7a7a" in local or "\u5bfc\u7ebf" in local:
                line_type = "\u67b6\u7a7a"
            elif sentence_line_type:
                line_type = sentence_line_type
            else:
                line_type = "\u67b6\u7a7a"
            sentence_line_type = line_type
            item_map[line_type] = f"{line_type}: {normalize_section_value(match.group(1))}mm\u00b2"
            last_end = match.end()
        if item_map:
            evidences.append(sentence)
    if not item_map:
        return None, None, {}
    items = list(item_map.values())
    return "; ".join(items), " ".join(evidences[:2]), {"items": items}


def extract_access_interval_desc(text: str) -> tuple[str | None, str | None, dict]:
    for sentence in split_sentences(text):
        if "\u95f4\u9694" in sentence and ("\u6269\u5efa" in sentence or "\u51fa\u7ebf" in sentence):
            match = re.search(r"[\u4e00-\u9fa5A-Za-z0-9~\uff5e]{1,20}\u6269\u5efa[^,\uff0c\u3002\uff1b;]*?\u95f4\u9694\s*[0-9\u4e00-\u9fa5]+\s*\u4e2a", sentence)
            if match:
                return cleanup_sentence(match.group(0)), sentence, {}
            return cleanup_sentence(sentence), sentence, {}
    return None, None, {}


def extract_main_transformer_capacity(text: str) -> tuple[str | None, str | None, dict]:
    section = section_window(text, "\u539f\u5219\u7535\u6c14\u4e3b\u63a5\u7ebf", 500)
    return first_match(
        section or text,
        [
            r"\u4e3b\u53d8\u5bb9\u91cf[^\u3002\uff1b;0-9]{0,10}([0-9]+(?:\s*[xX\u00d7]\s*[0-9]+)?\s*(?:MVA|\u5146\u4f0f\u5b89))",
            r"\u4e3b\u53d8[^\u3002\uff1b;]{0,20}?\s*([0-9]+(?:\s*[xX\u00d7]\s*[0-9]+)?\s*(?:MVA|\u5146\u4f0f\u5b89))",
        ],
    )


def extract_main_transformer_wiring(text: str) -> tuple[str | None, str | None, dict]:
    section = section_window(text, "\u539f\u5219\u7535\u6c14\u4e3b\u63a5\u7ebf", 500)
    value, evidence, meta = first_match(section or text, [r"110\s*\u5343\u4f0f[^\u3002\uff1b;]{0,40}?\u91c7\u7528([\u4e00-\u9fa5]{2,12})(?:\u63a5\u7ebf)?"])
    return (value.replace("\u63a5\u7ebf", ""), evidence, meta) if value else (None, None, {})


def extract_svg_capacity(text: str) -> tuple[str | None, str | None, dict]:
    section = section_window(text, "\u65e0\u529f\u5bb9\u91cf\u914d\u7f6e", 350)
    return first_match(section or text, [r"\u914d\u7f6e(?:\u603b)?\u5bb9\u91cf\u4e0d\u4f4e\u4e8e\s*([0-9,.]+\s*(?:Mvar|\u5146\u4e4f))"])


def normalize_section_value(text: str) -> str:
    """Normalize section expressions such as ``2 x 300`` to ``2x300``."""
    return re.sub(r"\s+", "", text).replace("\u00d7", "x").replace("X", "x")


def chinese_digit_to_int(text: str) -> str:
    """Convert a small Chinese numeral used for circuit counts to digits."""
    text = text.strip()
    if text.isdigit():
        return text
    mapping = {"\u4e00": 1, "\u4e8c": 2, "\u4e09": 3, "\u56db": 4, "\u4e94": 5, "\u516d": 6, "\u4e03": 7, "\u516b": 8, "\u4e5d": 9}
    if text == "\u5341":
        return "10"
    if "\u5341" in text:
        left, _, right = text.partition("\u5341")
        return str((mapping.get(left, 1) * 10) + mapping.get(right, 0))
    return str(mapping.get(text, text))


# ==== 图片块识别 ====



def append_image_block_ocr(
    document: Document,
    parsed_root: Path,
    *,
    max_images: int = 8,
    min_score: float = 0.45,
    max_side: int = 1800,
) -> Document:
    """OCR opendataloader image blocks and append recognized text to pages.

    This is intentionally separate from full-page hybrid OCR. Some stamped fields
    such as issue date and issuing agency live in image blocks even when the PDF
    already has a text layer. Rule and table extractors need those snippets in
    page.text to recover dates, agencies, and stamps.
    """

    image_blocks = [
        block
        for page in document.pages
        for block in page.blocks
        if "image" in block.type.lower() and block.meta.get("source")
    ]
    if not image_blocks:
        return document

    engine = _rapidocr_engine()
    ocr_count = 0
    errors: list[dict[str, Any]] = []

    for block in image_blocks[: max(0, max_images)]:
        source = str(block.meta.get("source") or "")
        source_root = Path(str(block.meta.get("source_root"))) if block.meta.get("source_root") else None
        image_path = _resolve_image_path(parsed_root, source, source_root=source_root)
        if image_path is None:
            continue

        try:
            ocr_text = _ocr_image(engine, image_path, min_score=min_score, max_side=max_side)
        except Exception as exc:  # OCR must not break document parsing.
            errors.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not ocr_text:
            continue

        block.text = ocr_text
        block.type = "image_ocr"
        block.meta["image_ocr"] = True
        block.meta["image_path"] = str(image_path)
        ocr_count += 1

    if ocr_count:
        for page in document.pages:
            page.text = "\n".join(block.text for block in page.blocks if block.text).strip()
        document.rebuild_text()
        document.meta["image_block_ocr_used"] = True
        document.meta["image_block_ocr_count"] = ocr_count
    if errors:
        document.meta["image_block_ocr_errors"] = errors[:20]
    return document


def _rapidocr_engine() -> Any:
    """Create a RapidOCR engine instance lazily."""
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("rapidocr is required for --ocr-image-blocks. Install rapidocr first.") from exc
    return RapidOCR()


def _resolve_image_path(parsed_root: Path, source: str, *, source_root: Path | None = None) -> Path | None:
    """Resolve an image block source path from parser output metadata."""
    raw = Path(source)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    if source_root is not None:
        candidates.append(source_root / raw)
        candidates.append(source_root / raw.name)
    candidates.append(parsed_root / raw)
    candidates.append(parsed_root / raw.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = list(parsed_root.rglob(raw.name))
    return matches[0] if matches else None


def _ocr_image(engine: Any, image_path: Path, *, min_score: float, max_side: int) -> str:
    """Run OCR over resized/red-stamp-removed variants and dedupe lines."""
    working_paths = _prepare_image_variants(image_path, max_side=max_side)
    all_lines: list[str] = []
    try:
        for working_path in working_paths:
            output = engine(str(working_path))
            all_lines.extend(_output_lines(output, min_score=min_score))
    finally:
        for working_path in working_paths:
            if working_path != image_path:
                try:
                    working_path.unlink(missing_ok=True)
                except OSError:
                    pass

    deduped = list(dict.fromkeys(line for line in all_lines if line.strip()))
    return "\n".join(deduped).strip()


def _output_lines(output: Any, *, min_score: float) -> list[str]:
    """Convert RapidOCR output variants into confidence-filtered text lines."""

    txts = getattr(output, "txts", None) or []
    scores = getattr(output, "scores", None) or []
    if not txts and isinstance(output, (list, tuple)):
        txts, scores = _extract_from_legacy_output(output)

    lines: list[str] = []
    for index, text in enumerate(txts):
        text = str(text or "").strip()
        if not text:
            continue
        score = float(scores[index]) if index < len(scores) and scores[index] is not None else 1.0
        if score >= min_score:
            lines.append(text)
    return lines


def _extract_from_legacy_output(output: Any) -> tuple[list[str], list[float]]:
    """Parse legacy RapidOCR tuple/list output."""
    items = output[0] if output else []
    txts: list[str] = []
    scores: list[float] = []
    if not isinstance(items, list):
        return txts, scores
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        txts.append(str(item[1]))
        scores.append(float(item[2]) if len(item) > 2 and item[2] is not None else 1.0)
    return txts, scores


def _resize_if_needed(image_path: Path, *, max_side: int) -> Path:
    """Create a temporary downscaled image when OCR input is too large."""
    if max_side <= 0:
        return image_path
    try:
        from PIL import Image
    except ImportError:
        return image_path

    with Image.open(image_path) as image:
        width, height = image.size
        longest = max(width, height)
        if longest <= max_side:
            return image_path
        scale = max_side / float(longest)
        resized = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        tmp = tempfile.NamedTemporaryFile(suffix=image_path.suffix or ".png", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        resized.save(tmp_path)
        return tmp_path


def _prepare_image_variants(image_path: Path, *, max_side: int) -> list[Path]:
    """Build image variants used to improve OCR on stamped pages."""
    resized = _resize_if_needed(image_path, max_side=max_side)
    variants = [resized]
    red_removed = _remove_red_stamp(resized)
    if red_removed is not None:
        variants.append(red_removed)
    return variants


def _remove_red_stamp(image_path: Path) -> Path | None:
    """Return a temporary image with strong red stamp pixels whitened out."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None

    with Image.open(image_path) as image:
        arr = np.array(image.convert("RGB"))
    red = arr[:, :, 0].astype("int16")
    green = arr[:, :, 1].astype("int16")
    blue = arr[:, :, 2].astype("int16")
    red_mask = (red > 120) & (red > green * 1.25) & (red > blue * 1.25)
    if red_mask.mean() < 0.005:
        return None
    arr[red_mask] = [255, 255, 255]

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    Image.fromarray(arr).save(tmp_path)
    return tmp_path


# ==== 字段标准化 ====

def normalize_field(field: ExtractedField) -> ExtractedField:
    """Attach normalized value metadata for supported field types."""
    text = str(field.value or "").strip()
    if not text:
        return field
    if field.name.endswith("investment") or "fee" in field.name or field.name == "total_investment":
        field.normalized = normalize_money(text)
    elif field.name in {"main_transformer_capacity"}:
        field.normalized = normalize_apparent_power(text)
    elif field.name in {"svg_capacity"}:
        field.normalized = normalize_reactive_power(text)
    elif field.name in {"line_length", "access_distance"}:
        field.normalized = normalize_length(text)
    elif field.name in {"outgoing_circuits"}:
        field.normalized = normalize_count(text, "\u56de")
    elif field.name in {"capacity"}:
        field.normalized = normalize_capacity(text)
    elif field.name in {"land_area", "land_control_area"}:
        field.normalized = normalize_area(text)
    elif field.name in {"issue_date"}:
        field.normalized = normalize_date(text)
    elif field.name in {"document_no"}:
        field.value = normalize_document_no(text)
    elif field.name in {"loan_interest_rate"}:
        field.normalized = normalize_percent(text)
    return field


def normalize_money(text: str) -> dict[str, Any] | None:
    """Normalize money expressions to ten-thousand yuan."""
    match = re.search(r"([0-9,.]+)\s*(\u4e07\u5143|\u4ebf\u5143|\u5143)", text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    unit = match.group(2)
    if unit == "\u4ebf\u5143":
        return {"amount": amount * 10000, "unit": "\u4e07\u5143"}
    if unit == "\u5143":
        return {"amount": amount / 10000, "unit": "\u4e07\u5143"}
    return {"amount": amount, "unit": "\u4e07\u5143"}


def normalize_capacity(text: str) -> dict[str, Any] | None:
    """Normalize installed capacity to MW."""
    match = re.search(r"([0-9,.]+)\s*(MW|\u5146\u74e6|\u4e07\u5343\u74e6|kW)", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = match.group(2).lower()
    if unit == "\u4e07\u5343\u74e6":
        return {"value": value * 10, "unit": "MW"}
    if unit == "kw":
        return {"value": value / 1000, "unit": "MW"}
    return {"value": value, "unit": "MW"}


def normalize_area(text: str) -> dict[str, Any] | None:
    """Normalize land area to hectares."""
    match = re.search(r"([0-9,.]+)\s*(\u516c\u9877|\u4ea9|\u5e73\u65b9\u7c73|m2|\u33a1)", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = match.group(2)
    if unit == "\u4ea9":
        return {"value": value / 15, "unit": "\u516c\u9877"}
    if unit in {"\u5e73\u65b9\u7c73", "m2", "\u33a1"}:
        return {"value": value / 10000, "unit": "\u516c\u9877"}
    return {"value": value, "unit": "\u516c\u9877"}


def normalize_date(text: str) -> str | None:
    """Normalize Chinese or slash-separated dates to ISO format."""
    match = re.search(r"((?:19|20)\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"((?:19|20)\d{2})[./-](\d{1,2})[./-](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def normalize_document_no(text: str) -> str:
    """Normalize bracket variants in official document numbers."""
    return (
        text.replace("\uff3b", "\u3014")
        .replace("[", "\u3014")
        .replace("\uff3d", "\u3015")
        .replace("]", "\u3015")
    )


def normalize_percent(text: str) -> dict[str, Any] | None:
    """Normalize percentage values."""
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if not match:
        return None
    return {"value": float(match.group(1)), "unit": "%"}


def normalize_length(text: str) -> dict[str, Any] | None:
    """Normalize line distance to kilometers."""
    match = re.search(r"([0-9,.]+)(?:\s*[xX\u00d7]\s*([0-9,.]+))?\s*(km|\u516c\u91cc|\u5343\u7c73)", text, re.IGNORECASE)
    if not match:
        return None
    first = float(match.group(1).replace(",", ""))
    second = float(match.group(2).replace(",", "")) if match.group(2) else None
    value = first * second if second is not None else first
    return {"value": value, "unit": "km"}


def normalize_apparent_power(text: str) -> dict[str, Any] | None:
    """Normalize transformer apparent power to MVA."""
    match = re.search(r"([0-9,.]+)(?:\s*[xX\u00d7]\s*([0-9,.]+))?\s*(MVA|\u5146\u4f0f\u5b89)", text, re.IGNORECASE)
    if not match:
        return None
    first = float(match.group(1).replace(",", ""))
    second = float(match.group(2).replace(",", "")) if match.group(2) else None
    value = first * second if second is not None else first
    return {"value": value, "unit": "MVA"}


def normalize_reactive_power(text: str) -> dict[str, Any] | None:
    """Normalize reactive power to Mvar."""
    match = re.search(r"([0-9,.]+)\s*(Mvar|\u5146\u4e4f)", text, re.IGNORECASE)
    if not match:
        return None
    return {"value": float(match.group(1).replace(",", "")), "unit": "Mvar"}


def normalize_count(text: str, unit: str) -> dict[str, Any] | None:
    """Normalize integer counts while preserving the business unit."""
    match = re.search(r"([0-9]+)", text)
    if not match:
        return None
    return {"value": int(match.group(1)), "unit": unit}


# ==== 字段校验 ====

def validate_field(field: ExtractedField, document: Document) -> tuple[bool, str | None]:
    """Validate a candidate and return a warning when evidence is approximate."""
    if field.value is None or str(field.value).strip() == "":
        return False, "empty_value"
    text = document.full_text or document.rebuild_text()
    evidence = field.evidence or str(field.value)
    if evidence and evidence not in text and len(str(field.value)) > 4:
        return True, "value_not_exact_span"
    return True, None


# ==== 候选合并 ====

class ResultMerger:
    """Normalize, validate, and select the best candidate for each field."""

    def __init__(self, extraction_config: dict[str, Any] | None = None) -> None:
        """Initialize source priority weights from extraction config."""
        self.extraction_config = extraction_config or load_config(config_path("extraction.yaml"))
        self.source_priorities = self.extraction_config.get("candidate_priorities", {})

    def merge(
        self,
        document: Document,
        candidates: list[ExtractedField],
        tables: list[Table] | None = None,
    ) -> ExtractionResult:
        """Merge extractor candidates into one JSON-ready result."""
        result = ExtractionResult(file=document.file, doc_type=document.doc_type, tables=tables or [], meta=dict(document.meta))
        for candidate in candidates:
            candidate = normalize_field(candidate)
            valid, warning = validate_field(candidate, document)
            if not valid:
                result.warnings.append(f"{candidate.name}:{warning}")
                continue
            if warning:
                candidate.meta["warning"] = warning
            candidate.confidence = min(1.0, candidate.confidence + self.source_priorities.get(candidate.source, 0.0))
            result.add(candidate)
        return result


# ==== 抽取流程 ====



class SupportDocPipeline:
    """End-to-end parser, classifier, extractor, and merger for support docs.

    Args:
        parser_name: Parser backend name, either ``pymupdf`` or
            ``opendataloader_pdf``.
        parsed_root: Optional directory for parsed JSON/cache files.
        extraction_config: Optional extraction pipeline YAML path.
        auto_ocr: Whether to invoke OCR fallback for low-text/image PDFs.
        min_text_chars: Minimum text threshold before a document is considered
            OCR-needed.
        hybrid_backend: Optional opendataloader hybrid OCR backend.
        hybrid_url: Optional hybrid service URL.
        hybrid_mode: Hybrid OCR mode passed through to opendataloader.
        hybrid_batch_size: Page batch size hint for OCR fallback.
        ocr_page_dpi: DPI used when rendering single-page OCR PDFs.
        ocr_page_max_side: Maximum rendered page image side.
        refresh_parsed: Whether to discard existing parsed cache.
    """

    def __init__(
        self,
        parser_name: str = "pymupdf",
        parsed_root: Path | None = None,
        extraction_config: Path | None = None,
        auto_ocr: bool = True,
        min_text_chars: int = 120,
        hybrid_backend: str | None = None,
        hybrid_url: str | None = None,
        hybrid_mode: str = "full",
        hybrid_batch_size: int = 1,
        ocr_page_dpi: int = 96,
        ocr_page_max_side: int = 1400,
        refresh_parsed: bool = False,
    ) -> None:
        self.parser_name = parser_name
        self.parsed_root = parsed_root or runtime_root() / "parsed"
        self.extraction_config_path = extraction_config or config_path("extraction.yaml")
        self.extraction_config = load_config(self.extraction_config_path)
        self.fields_config = load_config(config_path("fields.yaml"))
        self.auto_ocr = auto_ocr
        self.min_text_chars = min_text_chars
        self.hybrid_backend = hybrid_backend
        self.hybrid_url = hybrid_url
        self.hybrid_mode = hybrid_mode
        self.hybrid_batch_size = max(1, hybrid_batch_size)
        self.ocr_page_dpi = ocr_page_dpi
        self.ocr_page_max_side = ocr_page_max_side
        self.refresh_parsed = refresh_parsed
        self.classifier = RuleClassifier()
        self.common_extractor = CommonExtractor()
        self.typed_extractor = TypedExtractor()
        self.rule_extractor = RuleExtractor()
        self.table_extractor = TableExtractor()
        self.merger = ResultMerger(self.extraction_config)

    def parse(self, path: Path) -> Document:
        """Parse a document and optionally apply OCR fallback.

        Args:
            path: PDF or Word file path.

        Returns:
            Unified document model consumed by extractors.
        """
        if self.parser_name == "opendataloader_pdf":
            parser_config = load_config(config_path("parser.yaml"))
            options = parser_config.get("opendataloader_pdf", {})
            if self.refresh_parsed:
                options = dict(options)
                options["refresh"] = True
            try:
                document = OpenDataLoaderParser(output_root=self.parsed_root, options=options).parse(path)
                document = append_image_block_ocr(document, self.parsed_root)
            except Exception as exc:
                document = PyMuPDFParser().parse(path)
                document.meta["parser_fallback"] = "pymupdf"
                document.meta["parser_error"] = f"{type(exc).__name__}: {exc}"
        else:
            document = PyMuPDFParser().parse(path)
        if self.auto_ocr and needs_ocr(document, min_text_chars=self.min_text_chars):
            document.meta["needs_ocr"] = True
            document.meta["ocr_reason"] = ocr_reason(document, min_text_chars=self.min_text_chars)
            if self.parser_name == "opendataloader_pdf" and self.hybrid_backend:
                try:
                    hybrid_document = self._parse_with_hybrid_batches(path)
                    hybrid_document.meta.update(document.meta)
                    hybrid_document.meta["hybrid_used"] = True
                    if not needs_ocr(hybrid_document, min_text_chars=self.min_text_chars):
                        return hybrid_document
                    hybrid_document.meta["hybrid_low_text"] = True
                    document = hybrid_document
                except Exception as exc:
                    document.meta["hybrid_error"] = f"{type(exc).__name__}: {exc}"
            elif self.parser_name == "opendataloader_pdf":
                document.meta["ocr_error"] = "hybrid_backend_not_configured"
        return document

    def _parse_with_hybrid(self, path: Path) -> Document:
        parser_config = load_config(config_path("parser.yaml"))
        options = dict(parser_config.get("opendataloader_pdf", {}))
        options.update(
            {
                "hybrid_backend": self.hybrid_backend,
                "hybrid_url": self.hybrid_url,
                "hybrid_mode": self.hybrid_mode,
                "hybrid_timeout": "0",
                "hybrid_fallback": True,
                "refresh": self.refresh_parsed,
            }
        )
        hybrid_root = self.parsed_root / "_hybrid"
        return OpenDataLoaderParser(output_root=hybrid_root, options=options).parse(path)

    def _parse_with_hybrid_batches(self, path: Path) -> Document:
        page_count = pdf_page_count(path)
        pages = []
        page_errors = []
        for page_no in range(1, page_count + 1):
            try:
                page_document = self._parse_with_hybrid_page(path, page_no)
            except Exception as exc:
                page_errors.append({"page": page_no, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not page_document.pages:
                continue
            page = page_document.pages[0]
            page.page_no = page_no
            for block in page.blocks:
                block.page_no = page_no
            for table in page.tables:
                table.page_no = page_no
            pages.append(page)
        if not pages:
            if page_errors:
                raise RuntimeError(f"All hybrid OCR pages failed: {page_errors[:3]}")
            return self._parse_with_hybrid(path)
        document = Document(file=path, pages=pages, parser="opendataloader_pdf")
        document.meta["hybrid_batched"] = True
        document.meta["hybrid_batch_size"] = self.hybrid_batch_size
        if page_errors:
            document.meta["page_errors"] = page_errors
        document.rebuild_text()
        return document

    def _parse_with_hybrid_page(self, path: Path, page_no: int) -> Document:
        with tempfile.TemporaryDirectory(prefix="support_hybrid_page_") as temp_dir:
            temp_pdf = Path(temp_dir) / f"{path.stem}_page_{page_no:04d}.pdf"
            render_page_to_pdf(path, page_no, temp_pdf, dpi=self.ocr_page_dpi, max_side=self.ocr_page_max_side)
            return self._parse_single_page_pdf_with_hybrid(temp_pdf, path.stem, page_no)

    def _parse_single_page_pdf_with_hybrid(self, temp_pdf: Path, original_stem: str, page_no: int) -> Document:
        parser_config = load_config(config_path("parser.yaml"))
        options = dict(parser_config.get("opendataloader_pdf", {}))
        options.update(
            {
                "hybrid_backend": self.hybrid_backend,
                "hybrid_url": self.hybrid_url,
                "hybrid_mode": self.hybrid_mode,
                "hybrid_timeout": "0",
                "hybrid_fallback": True,
                "refresh": self.refresh_parsed,
            }
        )
        hybrid_root = self.parsed_root / "_hybrid_pages" / original_stem / f"page_{page_no:04d}"
        return OpenDataLoaderParser(output_root=hybrid_root, options=options).parse(temp_pdf)

    def infer(self, path: Path) -> ExtractionResult:
        """Extract configured fields from a single support document.

        Args:
            path: Input document path.

        Returns:
            Merged and normalized extraction result.
        """
        document = self.parse(path)
        doc_type, _, _ = self.classifier.classify(document)
        return self.infer_document(document, doc_type)

    def infer_with_type(self, path: Path, doc_type: str) -> ExtractionResult:
        """Extract a document with an explicitly supplied document type."""
        document = self.parse(path)
        return self.infer_document(document, doc_type)

    def infer_document(self, document: Document, doc_type: str) -> ExtractionResult:
        """Extract configured fields from an already parsed document."""
        document.doc_type = doc_type
        fields = self.classifier.fields_for(doc_type)

        candidates = []
        tables = []
        for extractor_name in self._enabled_extractor_order():
            selected_fields = self._select_fields(extractor_name, fields)
            if not selected_fields:
                continue
            if extractor_name == "typed":
                candidates.extend(self.typed_extractor.extract(document, selected_fields))
            elif extractor_name == "common":
                candidates.extend(self.common_extractor.extract(document, selected_fields))
            elif extractor_name == "table":
                table_candidates, tables = self.table_extractor.extract(document, selected_fields)
                candidates.extend(table_candidates)
            elif extractor_name == "rule":
                candidates.extend(self.rule_extractor.extract(document, selected_fields))
        return self.merger.merge(document, candidates, tables=tables)

    def _enabled_extractor_order(self) -> list[str]:
        order = self.extraction_config.get("pipeline", {}).get("order", [])
        extractors = self.extraction_config.get("extractors", {})
        return [name for name in order if extractors.get(name, {}).get("enabled", True)]

    def _select_fields(self, extractor_name: str, fields: list[str]) -> list[str]:
        extractor_config = self.extraction_config.get("extractors", {}).get(extractor_name, {})
        if extractor_config.get("field_select", "all") == "all":
            return fields
        tags = set(extractor_config.get("field_tags", [extractor_name]))
        specs = self.fields_config.get("fields", {})
        selected = []
        for field in fields:
            configured = set(specs.get(field, {}).get("extractors", []))
            if tags & configured:
                selected.append(field)
        return selected


DOC_TYPE_ALIASES = {
    "\u8d37\u6b3e\u610f\u5411\u4e66": "loan_intent",
    "\u8d37\u6b3e": "loan_intent",
    "loan_intent": "loan_intent",
    "\u7528\u5730\u9884\u5ba1": "land_preapproval",
    "\u7528\u5730\u9884\u5ba1\u4e0e\u9009\u5740\u610f\u89c1\u4e66": "land_preapproval",
    "land_preapproval": "land_preapproval",
    "\u73af\u8bc4\u6279\u590d": "environment_approval",
    "\u73af\u8bc4\u610f\u89c1": "environment_approval",
    "\u73af\u8bc4\u62a5\u544a": "environment_approval",
    "environment_approval": "environment_approval",
    "\u6c34\u4fdd\u6279\u590d": "soil_water_approval",
    "\u6c34\u4fdd\u610f\u89c1": "soil_water_approval",
    "\u6c34\u4fdd\u62a5\u544a": "soil_water_approval",
    "soil_water_approval": "soil_water_approval",
    "\u63a5\u5165\u6279\u590d": "grid_access",
    "\u63a5\u5165\u610f\u89c1": "grid_access",
    "\u63a5\u5165\u7cfb\u7edf\u6279\u590d": "grid_access",
    "grid_access": "grid_access",
}


def normalize_doc_type(doc_type: str) -> str:
    """Normalize Chinese document type names to configured type codes."""
    key = (doc_type or "").strip()
    normalized = DOC_TYPE_ALIASES.get(key)
    if normalized:
        return normalized
    allowed = "\u3001".join(DOC_TYPE_ALIASES)
    raise ValueError(f"\u4e0d\u652f\u6301\u7684\u6587\u4ef6\u7c7b\u578b: {doc_type}\u3002\u53ef\u7528\u7c7b\u578b: {allowed}")


def output_paths_for(file_path: str | Path) -> tuple[Path, Path]:
    """????????????????"""
    path = Path(file_path).resolve()
    result_path = path.with_suffix(".json")
    details_path = path.with_name(f"{path.stem}_details.json")
    return result_path, details_path


def simple_result(details: dict[str, Any]) -> dict[str, Any]:
    """??????????????? JSON?"""
    fields = {}
    for name, item in details.get("fields", {}).items():
        normalized = item.get("normalized")
        fields[name] = normalized if normalized is not None else item.get("value")
    return {
        "file": details.get("file"),
        "doc_type": details.get("doc_type"),
        "fields": fields,
    }


def write_json(path: Path, payload: Any) -> None:
    """Write one JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_parser_name() -> str:
    """Return the best available parser without requiring caller input."""
    try:
        import opendataloader_pdf  # noqa: F401
    except ImportError:
        return "pymupdf"
    return "opendataloader_pdf"


def extract_document(doc_type: str, file_path: str | Path) -> dict[str, Any]:
    """Single public API: pass document type and file path, then write JSON files."""
    normalized_type = normalize_doc_type(doc_type)
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    pipeline = SupportDocPipeline(parser_name=default_parser_name())
    details = pipeline.infer_with_type(path, normalized_type).to_dict()
    result = simple_result(details)
    result_path, details_path = output_paths_for(path)
    write_json(result_path, result)
    write_json(details_path, details)
    return {
        "result_path": str(result_path),
        "details_path": str(details_path),
        "result": result,
        "details": details,
    }


def needs_ocr(document: Document, min_text_chars: int = 120) -> bool:
    """Return whether parsed content looks too sparse to trust."""
    text = document.full_text or document.rebuild_text()
    if len(text.strip()) < min_text_chars:
        return True
    block_count = sum(len(page.blocks) for page in document.pages)
    if block_count == 0:
        return True
    image_blocks = sum(1 for page in document.pages for block in page.blocks if "image" in block.type.lower())
    if has_large_image_low_text(document):
        return True
    return image_blocks > 0 and image_blocks == block_count


def ocr_reason(document: Document, min_text_chars: int = 120) -> str:
    """Return the main reason a document was routed to OCR fallback."""
    text = document.full_text or document.rebuild_text()
    if len(text.strip()) < min_text_chars:
        return "low_text"
    block_count = sum(len(page.blocks) for page in document.pages)
    if block_count == 0:
        return "no_blocks"
    image_blocks = sum(1 for page in document.pages for block in page.blocks if "image" in block.type.lower())
    if has_large_image_low_text(document):
        return "large_image_low_text"
    if image_blocks > 0 and image_blocks == block_count:
        return "image_only"
    return "unknown"


def has_large_image_low_text(document: Document, min_page_text_chars: int = 500, min_image_ratio: float = 0.6) -> bool:
    """Detect scanned pages represented as one large image plus little text."""
    for page in document.pages:
        page_text = page.text or "\n".join(block.text for block in page.blocks if block.text)
        if len(page_text.strip()) >= min_page_text_chars:
            continue
        page_width = page.width or 0
        page_height = page.height or 0
        page_area = page_width * page_height
        if page_area <= 0:
            continue
        for block in page.blocks:
            if "image" not in block.type.lower() or not block.bbox:
                continue
            x0, y0, x1, y1 = block.bbox
            image_area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            if image_area / page_area >= min_image_ratio:
                return True
    return False


def pdf_page_count(path: Path) -> int:
    """Return the number of pages in a PDF file."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required to count PDF pages for batched hybrid OCR.") from exc
    with fitz.open(path) as doc:
        return doc.page_count


def render_page_to_pdf(source_pdf: Path, page_no: int, output_pdf: Path, dpi: int, max_side: int) -> None:
    """Render one source page into a temporary image-only PDF for OCR."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required to create memory-safe OCR page PDFs.") from exc

    with fitz.open(source_pdf) as src:
        page = src[page_no - 1]
        zoom = dpi / 72.0
        width = page.rect.width * zoom
        height = page.rect.height * zoom
        if max_side > 0 and max(width, height) > max_side:
            zoom *= max_side / max(width, height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
            image_path = Path(tmp_img.name)
        try:
            pix.save(image_path)
            dst = fitz.open()
            dst_page = dst.new_page(width=page.rect.width, height=page.rect.height)
            dst_page.insert_image(page.rect, filename=str(image_path))
            dst.save(output_pdf)
            dst.close()
        finally:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass

