"""支持性文件解析的数据模型与配置工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def load_config(path: Path) -> dict[str, Any]:
    """读取 JSON 或 YAML 配置文件。

    Args:
        path: 配置文件路径。

    Returns:
        解析后的配置字典。

    Raises:
        RuntimeError: 读取 YAML 时缺少 PyYAML。
        ValueError: 配置文件扩展名不受支持。
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("读取 YAML 配置需要安装 pyyaml。") from exc
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data or {}
    raise ValueError(f"不支持的配置文件类型: {path}")


def repo_root() -> Path:
    """获取仓库根目录。

    Returns:
        仓库根目录路径。
    """
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    """获取项目根目录。

    Returns:
        项目根目录路径。
    """
    return repo_root()


def runtime_root() -> Path:
    """获取运行时缓存目录。

    Returns:
        runtime 目录路径。
    """
    return project_root() / "runtime"


def config_path(name: str) -> Path:
    """获取指定配置文件路径。

    Args:
        name: 配置文件名称。

    Returns:
        configs 目录下对应配置文件路径。
    """
    return project_root() / "configs" / name


BBox = tuple[float, float, float, float]


@dataclass(slots=True)
class Block:
    """页面中的文本、图片、标题或段落块。"""

    text: str
    type: str = "paragraph"
    page_no: int | None = None
    bbox: BBox | None = None
    confidence: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Table:
    """页面中识别出的表格。"""

    rows: list[list[str]]
    page_no: int | None = None
    bbox: BBox | None = None
    title: str | None = None
    confidence: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Page:
    """统一页面模型。"""

    page_no: int
    width: float | None = None
    height: float | None = None
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Document:
    """解析器、抽取器和后处理共用的统一文档模型。"""

    file: Path
    pages: list[Page]
    doc_type: str = "unknown_support_doc"
    parser: str | None = None
    full_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def rebuild_text(self) -> str:
        """根据页面文本或块文本重新生成全文。

        Returns:
            拼接后的全文，同时写入 full_text。
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
    """字段抽取候选结果。"""

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
    """单个输入文件的最终结构化结果。"""

    file: Path
    doc_type: str
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    tables: list[Table] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, field_result: ExtractedField) -> None:
        """保留同名字段中置信度最高的候选。

        Args:
            field_result: 待合并字段候选。
        """
        existing = self.fields.get(field_result.name)
        if existing is None or field_result.confidence >= existing.confidence:
            self.fields[field_result.name] = field_result

    def to_dict(self) -> dict[str, Any]:
        """转换为可直接 JSON 序列化的字典。

        Returns:
            字段、表格、告警和元数据组成的普通字典。
        """
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
