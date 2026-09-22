from pathlib import Path
from support_doc_extractor.parsers import OpenDataLoaderParser


def test_same_stem_in_different_directories_has_different_cache(tmp_path: Path):
    first = tmp_path / "a" / "环评批复.pdf"
    second = tmp_path / "b" / "环评批复.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    parser = OpenDataLoaderParser(output_root=tmp_path / "cache")
    assert parser._cache_dir(first) != parser._cache_dir(second)
