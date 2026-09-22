from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def load_config(path: Path) -> dict[str, Any]:
    """Load a JSON or YAML configuration file.

    Args:
        path: Configuration file path.

    Returns:
        Parsed configuration as a dictionary.

    Raises:
        RuntimeError: If YAML support is required but PyYAML is missing.
        ValueError: If the file extension is not supported.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load YAML configs. Install pyyaml.") from exc
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data or {}
    raise ValueError(f"Unsupported config type: {path}")


def repo_root() -> Path:
    """Return the extractor project root."""
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Return the support document extractor project root."""
    return repo_root()


def runtime_root() -> Path:
    """Return the directory used for generated parse caches and outputs."""
    return project_root() / "runtime"


def config_path(name: str) -> Path:
    """Return a named file under the project configuration directory."""
    return project_root() / "configs" / name


BBox = tuple[float, float, float, float]


@dataclass(slots=True)
class Block:
    """A positioned text, image, title, or paragraph block on a page."""

    text: str
    type: str = "paragraph"
    page_no: int | None = None
    bbox: BBox | None = None
    confidence: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Table:
    """A table extracted from a page as normalized text rows."""

    rows: list[list[str]]
    page_no: int | None = None
    bbox: BBox | None = None
    title: str | None = None
    confidence: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Page:
    """One parsed document page with blocks, tables, and page-level text."""

    page_no: int
    width: float | None = None
    height: float | None = None
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Document:
    """Unified document model shared by parsers, extractors, and postprocess."""

    file: Path
    pages: list[Page]
    doc_type: str = "unknown_support_doc"
    parser: str | None = None
    full_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def rebuild_text(self) -> str:
        """Rebuild full document text from page text or block text.

        Returns:
            Joined text for all pages, also stored in ``full_text``.
        """
        parts: list[str] = []
        for page in self.pages:
            if page.text:
                parts.append(page.text)
            else:
                parts.extend(block.text for block in page.blocks if block.text)
        self.full_text = "\n".join(parts).strip()
        return self.full_text


@dataclass(slots=True)
class ExtractedField:
    """One candidate field value before or after merge/normalization."""

    name: str
    value: Any
    source: str
    confidence: float = 0.0
    page_no: int | None = None
    evidence: str | None = None
    normalized: Any = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractionResult:
    """Final structured output for one input document."""

    file: Path
    doc_type: str
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    tables: list[Table] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, field_result: ExtractedField) -> None:
        """Keep the highest-confidence candidate for a field name."""
        existing = self.fields.get(field_result.name)
        if existing is None or field_result.confidence >= existing.confidence:
            self.fields[field_result.name] = field_result

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to plain JSON-compatible objects."""
        return {
            "file": str(self.file),
            "doc_type": self.doc_type,
            "fields": {
                key: {
                    "value": value.value,
                    "source": value.source,
                    "confidence": value.confidence,
                    "page": value.page_no,
                    "evidence": value.evidence,
                    "normalized": value.normalized,
                    "meta": value.meta,
                }
                for key, value in self.fields.items()
            },
            "tables": [
                {
                    "page": table.page_no,
                    "title": table.title,
                    "rows": table.rows,
                    "confidence": table.confidence,
                    "meta": table.meta,
                }
                for table in self.tables
            ],
            "warnings": self.warnings,
            "meta": self.meta,
        }
