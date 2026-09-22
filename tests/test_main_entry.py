from pathlib import Path

import main
from support_doc_extractor import SupportDocType


def test_root_parse_file_delegates_to_single_file_api(monkeypatch, tmp_path: Path):
    input_file = tmp_path / "demo.pdf"
    input_file.write_bytes(b"pdf")
    expected = {"result": {"fileType": "环保批复文件"}}
    called = {}

    def fake_extract_document(file_type, file_path):
        called["file_type"] = file_type
        called["file_path"] = file_path
        return expected

    monkeypatch.setattr(main, "extract_document", fake_extract_document)

    result = main.parse_file(input_file, SupportDocType.ENVIRONMENT_APPROVAL)

    assert result is expected
    assert called["file_type"] is SupportDocType.ENVIRONMENT_APPROVAL
    assert Path(called["file_path"]) == input_file
