import pytest

from support_doc_extractor import SupportDocType, extract_document
from support_doc_extractor.document_types import normalize_doc_type


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (SupportDocType.ENVIRONMENT_APPROVAL, "environment_approval"),
        ("环评", "environment_approval"),
        ("环评批复", "environment_approval"),
        ("水保", "soil_water_approval"),
        ("水保批复", "soil_water_approval"),
        ("用地预审", "land_preapproval"),
        ("贷款意向", "loan_intent"),
        ("接入", "grid_access"),
    ],
)
def test_document_type_enum_and_aliases(value, expected):
    assert normalize_doc_type(value) == expected


def test_document_type_display_names():
    assert SupportDocType.ENVIRONMENT_APPROVAL.display_name == "环保批复文件"
    assert SupportDocType.SOIL_WATER_APPROVAL.short_name == "水保"


def test_invalid_document_type_is_rejected():
    with pytest.raises(ValueError, match="不支持的文件类型"):
        normalize_doc_type("其他")


def test_extract_document_rejects_directory(tmp_path):
    with pytest.raises(ValueError, match="必须传入单个文件"):
        extract_document(SupportDocType.ENVIRONMENT_APPROVAL, tmp_path)
