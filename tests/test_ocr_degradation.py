from pathlib import Path
from support_doc_extractor.engine import append_image_block_ocr
from support_doc_extractor.models import Block, Document, Page


def test_missing_rapidocr_keeps_opendataloader_document(monkeypatch, tmp_path: Path):
    document = Document(
        file=tmp_path / "demo.pdf",
        pages=[Page(page_no=1, blocks=[
            Block(text="已有文本", type="image", page_no=1, meta={"source": "imageFile1.png"})
        ], text="已有文本")],
        parser="opendataloader_pdf",
    )
    document.rebuild_text()

    def raise_missing_runtime():
        raise RuntimeError("rapidocr runtime unavailable")

    monkeypatch.setattr("support_doc_extractor.engine._rapidocr_engine", raise_missing_runtime)
    result = append_image_block_ocr(document, tmp_path)
    assert result is document
    assert result.full_text == "已有文本"
    assert "rapidocr runtime unavailable" in result.meta["image_block_ocr_error"]
