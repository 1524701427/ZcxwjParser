"""Public API for support document extraction."""

from support_doc_extractor.api import extract_document
from support_doc_extractor.document_types import SupportDocType
from support_doc_extractor.logging_utils import configure_logging

__all__ = [
    "SupportDocType",
    "extract_document",
    "configure_logging",
]
