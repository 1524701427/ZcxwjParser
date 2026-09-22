"""API example: pass document type and file path, then write JSON files."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from support_doc_extractor import SupportDocType, extract_document


def main() -> None:
    """Run extraction with only document type and input file path."""
    doc_type = "\u73af\u8bc4\u6279\u590d"  # 环评批复
    file_path = (
        r"C:\Users\EDY\Desktop\PDF\支持性文件\环评批复"
        r"\1陕西华电靖边韩家沟20万千瓦项目_送出线路环评报告批复(1).pdf"
    )

    if not Path(file_path).exists():
        print("Please change file_path to an existing support document:", file_path)
        return

    output = extract_document(doc_type, file_path)
    print("result:", output["result_path"])
    print("details:", output["details_path"])


if __name__ == "__main__":
    main()
