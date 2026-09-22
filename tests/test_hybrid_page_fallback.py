from pathlib import Path
from support_doc_extractor.engine import ResultMerger, SupportDocPipeline
from support_doc_extractor.models import Block, Document, Page


def _page(page_no: int, text: str) -> Page:
    return Page(page_no=page_no, blocks=[Block(text=text, type="paragraph", page_no=page_no)], text=text)


def test_hybrid_page_failure_preserves_original_page(monkeypatch, tmp_path: Path):
    path = tmp_path / "demo.pdf"
    fallback = Document(file=path, pages=[_page(1, "原始第一页"), _page(2, "原始第二页")], parser="opendataloader_pdf")
    fallback.rebuild_text()
    pipeline = SupportDocPipeline(parser_name="pymupdf")
    monkeypatch.setattr("support_doc_extractor.engine.pdf_page_count", lambda _: 2)

    def fake_hybrid_page(_, page_no: int):
        if page_no == 2:
            raise RuntimeError("OCR service timeout")
        doc = Document(file=path, pages=[_page(1, "OCR第一页")], parser="opendataloader_pdf")
        doc.rebuild_text()
        return doc

    monkeypatch.setattr(pipeline, "_parse_with_hybrid_page", fake_hybrid_page)
    result = pipeline._parse_with_hybrid_batches(path, fallback_document=fallback)
    assert [page.page_no for page in result.pages] == [1, 2]
    assert result.pages[0].text == "OCR第一页"
    assert result.pages[1].text == "原始第二页"
    assert result.meta["ocr_incomplete"] is True
    assert result.meta["fallback_pages"] == [2]
    merged = ResultMerger().merge(result, [])
    assert "ocr_incomplete:fallback_pages=[2]" in merged.warnings
