"""支持性文件解析项目的最外层入口。

业务方只需要调用 parse_file，传入一个文件路径和 SupportDocType 文件类型即可完成解析。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from support_doc_extractor import SupportDocType, extract_document


def parse_file(
    file_path: str | Path,
    file_type: SupportDocType,
) -> dict[str, Any]:
    """解析单个支持性文件。

    这是项目最外层唯一推荐的业务入口。调用方只需要提供一个实际文件
    和对应的文件类型枚举，内部会完成 PDF 解析、OCR 兜底、字段抽取、
    Java FileContent 映射以及 JSON 结果落盘。

    Args:
        file_path: 待解析的单个文件路径，不允许传目录。
        file_type: 文件类型枚举，例如 SupportDocType.ENVIRONMENT_APPROVAL。

    Returns:
        解析结果字典，包含业务结果、详细结果以及两个 JSON 输出路径。

    Raises:
        FileNotFoundError: 输入文件不存在。
        ValueError: 输入路径不是文件，或文件类型非法。
        RuntimeError: 底层解析器或 OCR 处理失败且无法降级。
    """
    return extract_document(file_type, file_path)


__all__ = ["SupportDocType", "parse_file"]
