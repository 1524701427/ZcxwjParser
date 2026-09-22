from io import BytesIO

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_parse_api_returns_file_content(monkeypatch):
    def fake_extract_document(doc_type, file_path, *, parsed_root=None, write_files=True):
        assert doc_type.value == "environment_approval"
        assert parsed_root is not None
        assert write_files is False
        return {
            "result_path": None,
            "details_path": None,
            "result": {
                "fileType": "环保批复文件",
                "approvalUnit": "榆林市生态环境局",
                "dispatchNo": "榆环批复〔2026〕1号",
                "obtainDate": "2026-09-01T00:00:00",
                "environmentalProtectionInvestment": "500万元",
                "recognizeDate": "2026-09-22T17:00:00",
                "remark": None,
            },
            "details": {},
        }

    monkeypatch.setattr(main, "extract_document", fake_extract_document)

    response = client.post(
        "/api/parse",
        data={"file_type": "环评"},
        files={"file": ("demo.pdf", BytesIO(b"%PDF-1.4 demo"), "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fileType"] == "环保批复文件"
    assert body["approvalUnit"] == "榆林市生态环境局"
    assert body["environmentalProtectionInvestment"] == "500万元"


def test_parse_api_rejects_non_pdf():
    response = client.post(
        "/api/parse",
        data={"file_type": "水保"},
        files={"file": ("demo.docx", BytesIO(b"demo"), "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "当前仅支持 PDF 文件。"
